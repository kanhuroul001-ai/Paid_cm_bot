import os
import sqlite3
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = 7572110906
DB = os.getenv("DB_PATH", "bot_data.db")


def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def setup():
    folder = os.path.dirname(DB)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                price INTEGER NOT NULL,
                file_id TEXT NOT NULL
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                video_id INTEGER NOT NULL,
                status TEXT NOT NULL
            )
        """)


def get_setting(key):
    with connect() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,)
        ).fetchone()
        return row["value"] if row else None


def set_setting(key, value):
    with connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO settings VALUES (?,?)",
            (key, value)
        )


def is_admin(update):
    return update.effective_user.id == ADMIN_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! 🎬\n\n"
        "Available videos dekhne ke liye /videos bhejo."
    )


async def videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with connect() as con:
        rows = con.execute(
            "SELECT id,title,price FROM videos ORDER BY id DESC"
        ).fetchall()

    if not rows:
        await update.message.reply_text("Abhi koi video available nahi hai.")
        return

    buttons = [
        [InlineKeyboardButton(
            f"🎬 {row['title']} - ₹{row['price']}",
            callback_data=f"buy:{row['id']}"
        )]
        for row in rows
    ]

    await update.message.reply_text(
        "Apna video select karo:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    video_id = int(q.data.split(":")[1])
    user_id = q.from_user.id
    qr = get_setting("qr")

    if not qr:
        await q.message.reply_text("Payment QR abhi set nahi hua hai.")
        return

    with connect() as con:
        video = con.execute(
            "SELECT title,price FROM videos WHERE id=?",
            (video_id,)
        ).fetchone()

        if not video:
            await q.message.reply_text("Video available nahi hai.")
            return

        active = con.execute(
            """SELECT id FROM orders
            WHERE user_id=? AND status IN
            ('waiting','submitted','delivering')
            LIMIT 1""",
            (user_id,)
        ).fetchone()

        if active:
            await q.message.reply_text(
                f"Tumhara order #{active['id']} abhi process mein hai. "
                "Pehle uska result aane do."
            )
            return

        cur = con.execute(
            """INSERT INTO orders
            (user_id,video_id,status) VALUES (?,?,?)""",
            (user_id, video_id, "waiting")
        )
        order_id = cur.lastrowid

    await context.bot.send_photo(
        chat_id=user_id,
        photo=qr,
        caption=(
            f"🎬 {video['title']}\n"
            f"💰 Amount: ₹{video['price']}\n"
            f"🧾 Order ID: #{order_id}\n\n"
            "QR scan karke exact amount pay karo. "
            "Payment ke baad screenshot isi chat mein bhejo."
        )
    )


async def payment_proof(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    if user_id == ADMIN_ID:
        return

    if not update.message.photo:
        return

    with connect() as con:
        order = con.execute(
            """SELECT id,video_id FROM orders
            WHERE user_id=? AND status='waiting'
            ORDER BY id DESC LIMIT 1""",
            (user_id,)
        ).fetchone()

        if not order:
            await update.message.reply_text(
                "Pehle /videos se video select karo."
            )
            return

        order_id = order["id"]
        video_id = order["video_id"]

        video = con.execute(
            "SELECT title,price FROM videos WHERE id=?",
            (video_id,)
        ).fetchone()

        con.execute(
            "UPDATE orders SET status='submitted' WHERE id=?",
            (order_id,)
        )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Approve",
            callback_data=f"approve:{order_id}"
        ),
        InlineKeyboardButton(
            "❌ Reject",
            callback_data=f"reject:{order_id}"
        )
    ]])

    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=update.message.photo[-1].file_id,
            caption=(
                f"💳 PAYMENT REVIEW\n"
                f"Order: #{order_id}\n"
                f"Customer ID: {user_id}\n"
                f"Video: {video['title']}\n"
                f"Amount: ₹{video['price']}\n\n"
                "Paytm transaction history mein payment verify karo."
            ),
            reply_markup=keyboard
        )

        await update.message.reply_text(
            "✅ Screenshot admin ko bhej diya gaya hai. "
            "Verification ke baad video milega."
        )

    except Exception:
        with connect() as con:
            con.execute(
                "UPDATE orders SET status='waiting' WHERE id=?",
                (order_id,)
            )
        await update.message.reply_text(
            "Screenshot bhejne mein problem hui. Dobara try karo."
        )


async def admin_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    q = update.callback_query

    if q.from_user.id != ADMIN_ID:
        await q.answer("Not authorized!", show_alert=True)
        return

    await q.answer()

    action, order_text = q.data.split(":")
    order_id = int(order_text)

    with connect() as con:
        order = con.execute(
            """SELECT user_id,video_id,status
            FROM orders WHERE id=?""",
            (order_id,)
        ).fetchone()

        if not order or order["status"] != "submitted":
            await q.message.reply_text(
                "Order already processed or not available."
            )
            return

        user_id = order["user_id"]
        video_id = order["video_id"]

        if action == "reject":
            con.execute(
                "UPDATE orders SET status='rejected' WHERE id=?",
                (order_id,)
            )
            video = None
        else:
            video = con.execute(
                "SELECT title,file_id FROM videos WHERE id=?",
                (video_id,)
            ).fetchone()

            if not video:
                await q.message.reply_text("Video not found.")
                return

            con.execute(
                "UPDATE orders SET status='delivering' WHERE id=?",
                (order_id,)
            )

    if action == "reject":
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="Payment approve nahi hua. Admin se contact karo."
            )
        except Exception:
            pass

        await q.message.reply_text(f"Order #{order_id} rejected.")
        return

    try:
        await context.bot.send_video(
            chat_id=user_id,
            video=video["file_id"],
            caption=f"✅ Payment approved!\n🎬 {video['title']}"
        )

        with connect() as con:
            con.execute(
                "UPDATE orders SET status='approved' WHERE id=?",
                (order_id,)
            )

        await q.message.reply_text(
            f"✅ Order #{order_id} approved and delivered."
        )

    except Exception as error:
        logging.exception("Video delivery failed: %s", error)

        with connect() as con:
            con.execute(
                "UPDATE orders SET status='submitted' WHERE id=?",
                (order_id,)
            )

        await q.message.reply_text(
            f"Delivery failed for order #{order_id}. "
            "Approve button dobara try kar sakte ho."
        )


async def admin_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        return

    caption = update.message.caption or ""
    parts = caption.split(maxsplit=2)

    if len(parts) < 3 or parts[0] != "/addvideo":
        await update.message.reply_text(
            "Format:\n/addvideo 10 Video Name"
        )
        return

    try:
        price = int(parts[1])
        title = parts[2].strip()

        if price <= 0 or not title:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "Sahi format: /addvideo 10 Video Name"
        )
        return

    with connect() as con:
        cur = con.execute(
            """INSERT INTO videos (title,price,file_id)
            VALUES (?,?,?)""",
            (title, price, update.message.video.file_id)
        )
        video_id = cur.lastrowid

    await update.message.reply_text(
        f"✅ Video added!\n"
        f"ID: {video_id}\n"
        f"Name: {title}\n"
        f"Price: ₹{price}"
    )


async def admin_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if is_admin(update):
        caption = update.message.caption or ""

        if caption.strip() == "/setqr":
            set_setting(
                "qr",
                update.message.photo[-1].file_id
            )
            await update.message.reply_text("✅ Paytm QR updated!")
        else:
            await update.message.reply_text(
                "QR update karne ke liye photo caption mein /setqr likho."
            )
        return

    await payment_proof(update, context)


async def setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Format: /setprice VIDEO_ID NEW_PRICE\n"
            "Example: /setprice 1 15"
        )
        return

    try:
        video_id = int(context.args[0])
        price = int(context.args[1])
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Valid ID aur price enter karo.")
        return

    with connect() as con:
        cur = con.execute(
            "UPDATE videos SET price=? WHERE id=?",
            (price, video_id)
        )

    if cur.rowcount:
        await update.message.reply_text(
            f"✅ Video #{video_id} ka price ₹{price} ho gaya."
        )
    else:
        await update.message.reply_text("Video ID nahi mila.")


async def showqr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    qr = get_setting("qr")

    if not qr:
        await update.message.reply_text("Abhi QR set nahi hai.")
        return

    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=qr,
        caption="Current Paytm QR"
    )


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    await update.message.reply_text(
        "👑 ADMIN COMMANDS\n\n"
        "Video upload: video caption mein\n"
        "/addvideo 10 Video Name\n\n"
        "Price change:\n"
        "/setprice 1 15\n\n"
        "QR update: photo caption mein /setqr\n"
        "/showqr - Current QR\n"
        "/videos - Video list"
    )


def main():
    setup()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("videos", videos))
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("setprice", setprice))
    app.add_handler(CommandHandler("showqr", showqr))

    app.add_handler(
        CallbackQueryHandler(buy, pattern=r"^buy:")
    )
    app.add_handler(
        CallbackQueryHandler(
            admin_action,
            pattern=r"^(approve|reject):"
        )
    )

    app.add_handler(
        MessageHandler(
            filters.VIDEO & filters.User(user_id=ADMIN_ID),
            admin_upload
        )
    )

    app.add_handler(
        MessageHandler(filters.PHOTO, admin_photo)
    )

    print("Paid video bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
