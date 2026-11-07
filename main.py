import os
import sqlite3
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = 7572110906
DB = "bot_data.db"

def db():
    return sqlite3.connect(DB)

def setup():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, price INTEGER, file_id TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, video_id INTEGER,
            status TEXT
        )""")

def get_setting(key):
    with db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None

def set_setting(key, value):
    with db() as con:
        con.execute(
            "INSERT OR REPLACE INTO settings VALUES (?,?)",
            (key, value)
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! 🎬\n/videos - Available videos\n"
        "Choose a video and follow payment instructions."
    )

async def videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as con:
        rows = con.execute(
            "SELECT id,title,price FROM videos"
        ).fetchall()

    if not rows:
        await update.message.reply_text("Abhi koi video available nahi hai.")
        return

    buttons = [
        [InlineKeyboardButton(
            f"{title} - ₹{price}", callback_data=f"buy:{vid}"
        )]
        for vid, title, price in rows
    ]
    await update.message.reply_text(
        "Apna video select karo:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    video_id = int(q.data.split(":")[1])

    qr = get_setting("qr")
    if not qr:
        await q.message.reply_text(
            "Payment QR abhi set nahi hua hai. Thodi der baad try karo."
        )
        return

    with db() as con:
        video = con.execute(
            "SELECT title,price FROM videos WHERE id=?",
            (video_id,)
        ).fetchone()

        if not video:
            await q.message.reply_text("Video nahi mila.")
            return

        cur = con.execute(
            "INSERT INTO orders (user_id,video_id,status) VALUES (?,?,?)",
            (q.from_user.id, video_id, "waiting")
        )
        order_id = cur.lastrowid

    await context.bot.send_photo(
        chat_id=q.from_user.id,
        photo=qr,
        caption=(
            f"🎬 {video[0]}\n"
            f"💰 Amount: ₹{video[1]}\n"
            f"Order ID: #{order_id}\n\n"
            "Paytm QR scan karke exact payment karo. "
            "Uske baad payment screenshot yahan bhejo."
        )
    )

async def payment_proof(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id
    photo = update.message.photo[-1].file_id

    with db() as con:
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

        order_id, video_id = order
        con.execute(
            "UPDATE orders SET status='submitted' WHERE id=?",
            (order_id,)
        )
        video = con.execute(
            "SELECT title,price FROM videos WHERE id=?",
            (video_id,)
        ).fetchone()

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{order_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject:{order_id}")
    ]])

    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=photo,
        caption=(
            f"💳 PAYMENT REVIEW\nOrder: #{order_id}\n"
            f"Customer ID: {user_id}\n"
            f"Video: {video[0]}\nAmount: ₹{video[1]}\n"
            "Paytm app mein payment verify karo."
        ),
        reply_markup=keyboard
    )
    await update.message.reply_text(
        "Screenshot admin ko bhej diya hai. Verification ke baad video milega."
    )

async def admin_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    q = update.callback_query

    if q.from_user.id != ADMIN_ID:
        await q.answer("Not authorized.", show_alert=True)
        return

    await q.answer()
    action, order_id = q.data.split(":")
    order_id = int(order_id)

    with db() as con:
        order = con.execute(
            """SELECT user_id,video_id,status FROM orders
            WHERE id=?""", (order_id,)
        ).fetchone()

        if not order or order[2] != "submitted":
            await q.message.reply_text("Order already processed or missing.")
            return

        user_id, video_id, status = order

        if action == "approve":
            video = con.execute(
                "SELECT title,file_id FROM videos WHERE id=?",
                (video_id,)
            ).fetchone()
            con.execute(
                "UPDATE orders SET status='approved' WHERE id=?",
                (order_id,)
            )
        else:
            con.execute(
                "UPDATE orders SET status='rejected' WHERE id=?",
                (order_id,)
            )
            video = None

    if action == "approve":
        await context.bot.send_video(
            chat_id=user_id,
            video=video[1],
            caption=f"✅ Payment approved!\nYour video: {video[0]}"
        )
        await q.message.reply_text(f"Order #{order_id} approved and delivered.")
    else:
        await context.bot.send_message(
            chat_id=user_id,
            text="Payment verify nahi ho paya. Admin se contact karo."
        )
        await q.message.reply_text(f"Order #{order_id} rejected.")

async def admin_upload(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if update.effective_user.id != ADMIN_ID:
        return

    caption = update.message.caption or ""

    if caption.startswith("/setqr"):
        set_setting("qr", update.message.photo[-1].file_id)
        await update.message.reply_text("✅ Paytm QR updated!")
        return

    if caption.startswith("/addvideo"):
        parts = caption.split(maxsplit=2)

        if len(parts) < 3 or not parts[1].isdigit():
            await update.message.reply_text(
                "Format: video caption mein /addvideo 10 Video Name likho."
            )
            return

        price = int(parts[1])
        title = parts[2]

        if price <= 0:
            await update.message.reply_text("Price valid nahi hai.")
            return

        with db() as con:
            con.execute(
                "INSERT INTO videos (title,price,file_id) VALUES (?,?,?)",
                (title, price, update.message.video.file_id)
            )

        await update.message.reply_text(
            f"✅ Video added!\nTitle: {title}\nPrice: ₹{price}"
        )

async def admin_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if update.effective_user.id == ADMIN_ID:
        caption = update.message.caption or ""
        if caption.startswith("/setqr"):
            set_setting("qr", update.message.photo[-1].file_id)
            await update.message.reply_text("✅ Paytm QR updated!")
            return

    await payment_proof(update, context)

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "ADMIN COMMANDS\n\n"
        "Video upload: video ke caption mein\n"
        "/addvideo 10 Video Name\n\n"
        "QR update: QR photo ke caption mein\n"
        "/setqr\n\n"
        "/videos - Customer video list"
    )

def main():
    setup()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("videos", videos))
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CallbackQueryHandler(buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(
        admin_action, pattern=r"^(approve|reject):"
    ))
    app.add_handler(MessageHandler(
        filters.VIDEO & filters.User(ADMIN_ID), admin_upload
    ))
    app.add_handler(MessageHandler(
        filters.PHOTO, admin_photo
    ))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
