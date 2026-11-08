import os
import sqlite3
import logging
import secrets

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = 7572110906
DB = os.getenv("DB_PATH", "bot_data.db")
DEFAULT_STARS_PRICE = 25
CURRENCY = "XTR"


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
                price INTEGER NOT NULL DEFAULT 25,
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

        # Safe migrations for databases created by the previous Paytm version.
        video_columns = {
            row["name"] for row in
            con.execute("PRAGMA table_info(videos)").fetchall()
        }
        if "stars_price" not in video_columns:
            con.execute(
                "ALTER TABLE videos ADD COLUMN stars_price INTEGER NOT NULL DEFAULT 25"
            )
        # User selected a uniform price of 25 Stars for all existing videos.
        con.execute("UPDATE videos SET stars_price=?", (DEFAULT_STARS_PRICE,))

        order_columns = {
            row["name"] for row in
            con.execute("PRAGMA table_info(orders)").fetchall()
        }
        migrations = {
            "payload": "TEXT",
            "stars_amount": "INTEGER",
            "currency": "TEXT",
            "telegram_payment_charge_id": "TEXT",
        }
        for column, definition in migrations.items():
            if column not in order_columns:
                con.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")


def is_admin(update):
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to Clipsmines! 🎬\n\n"
        "Videos dekhne ke liye /videos bhejo.\n"
        "Har video ki price: ⭐ 25 Telegram Stars."
    )


async def videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with connect() as con:
        rows = con.execute(
            "SELECT id,title,stars_price FROM videos ORDER BY id DESC"
        ).fetchall()

    if not rows:
        await update.message.reply_text("Abhi koi video available nahi hai.")
        return

    buttons = [
        [InlineKeyboardButton(
            f"🎬 {row['title']} — ⭐ {row['stars_price']}",
            callback_data=f"buy:{row['id']}"
        )]
        for row in rows
    ]
    await update.message.reply_text(
        "Apna video select karo. Payment Telegram Stars se hoga:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        video_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.message.reply_text("Invalid video selection.")
        return

    user_id = query.from_user.id
    with connect() as con:
        video = con.execute(
            "SELECT id,title,stars_price FROM videos WHERE id=?",
            (video_id,)
        ).fetchone()
        if not video:
            await query.message.reply_text("Video available nahi hai.")
            return

        active = con.execute(
            """SELECT id FROM orders
               WHERE user_id=? AND status IN
               ('awaiting_payment','delivering','delivery_pending')
               LIMIT 1""",
            (user_id,)
        ).fetchone()
        if active:
            await query.message.reply_text(
                f"Tumhara order #{active['id']} abhi pending hai. "
                "Agar invoice pay nahi karna, /cancelorder bhejo."
            )
            return

        stars = int(video["stars_price"] or DEFAULT_STARS_PRICE)
        if stars < 1:
            await query.message.reply_text("Is video ki price abhi valid nahi hai.")
            return

        cur = con.execute(
            """INSERT INTO orders
               (user_id,video_id,status,stars_amount,currency)
               VALUES (?,?,?,?,?)""",
            (user_id, video_id, "awaiting_payment", stars, CURRENCY)
        )
        order_id = cur.lastrowid
        payload = f"clipsmines:{order_id}:{user_id}:{secrets.token_urlsafe(12)}"
        con.execute("UPDATE orders SET payload=? WHERE id=?", (payload, order_id))

    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title=video["title"][:32],
            description=f"Clipsmines video access: {video['title']}"[:255],
            payload=payload,
            provider_token="",
            currency=CURRENCY,
            prices=[LabeledPrice(label=video["title"][:32], amount=stars)],
            start_parameter=f"video_{video_id}",
        )
    except Exception:
        logger.exception("Could not send Stars invoice for order %s", order_id)
        with connect() as con:
            con.execute(
                "UPDATE orders SET status='invoice_failed' WHERE id=? AND status='awaiting_payment'",
                (order_id,)
            )
        await query.message.reply_text(
            "Stars invoice bhejne mein problem hui. Thodi der baad dobara try karo."
        )


async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with connect() as con:
        cur = con.execute(
            """UPDATE orders SET status='cancelled'
               WHERE user_id=? AND status='awaiting_payment'""",
            (user_id,)
        )
    if cur.rowcount:
        await update.message.reply_text("Pending unpaid order cancel ho gaya. /videos se dobara select karo.")
    else:
        await update.message.reply_text("Koi pending unpaid order nahi mila.")


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    try:
        with connect() as con:
            order = con.execute(
                """SELECT user_id,video_id,status,payload,stars_amount,currency
                   FROM orders WHERE payload=?""",
                (query.invoice_payload,)
            ).fetchone()

        valid = (
            order is not None
            and order["user_id"] == query.from_user.id
            and order["status"] == "awaiting_payment"
            and order["currency"] == CURRENCY
            and order["stars_amount"] == query.total_amount
            and query.currency == CURRENCY
        )
        if not valid:
            await query.answer(
                ok=False,
                error_message="Order details match nahi karte. /videos se naya order banao."
            )
            return

        await query.answer(ok=True)
    except Exception:
        logger.exception("Pre-checkout validation failed")
        await query.answer(
            ok=False,
            error_message="Payment verify nahi ho paya. Dobara try karo."
        )


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    payment = message.successful_payment
    user_id = update.effective_user.id

    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        order = con.execute(
            """SELECT id,user_id,video_id,status,payload,stars_amount,currency
               FROM orders WHERE payload=?""",
            (payment.invoice_payload,)
        ).fetchone()

        if (
            not order
            or order["user_id"] != user_id
            or order["currency"] != CURRENCY
            or order["stars_amount"] != payment.total_amount
            or payment.currency != CURRENCY
        ):
            con.rollback()
            await message.reply_text(
                "Payment details verify nahi ho paaye. Admin se contact karo."
            )
            return

        if order["status"] in ("delivered", "delivering", "delivery_pending"):
            con.rollback()
            await message.reply_text(
                f"Payment receive ho chuka hai. Order #{order['id']} ka status check ho raha hai."
            )
            return

        if order["status"] != "awaiting_payment":
            con.rollback()
            await message.reply_text("Yeh order ab active nahi hai. Admin se contact karo.")
            return

        con.execute(
            """UPDATE orders
               SET status='delivering',telegram_payment_charge_id=?
               WHERE id=? AND status='awaiting_payment'""",
            (payment.telegram_payment_charge_id, order["id"])
        )
        video = con.execute(
            "SELECT title,file_id FROM videos WHERE id=?",
            (order["video_id"],)
        ).fetchone()
        con.commit()

    if not video:
        with connect() as con:
            con.execute(
                "UPDATE orders SET status='delivery_pending' WHERE id=?",
                (order["id"],)
            )
        await message.reply_text(
            "Payment receive ho gaya, lekin video record nahi mila. Admin ko inform kar diya gaya hai."
        )
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⚠️ Paid order #{order['id']} — video record missing. User: {user_id}"
        )
        return

    try:
        await context.bot.send_video(
            chat_id=user_id,
            video=video["file_id"],
            caption=f"✅ Payment successful!\n🎬 {video['title']}\nEnjoy your video!"
        )
        with connect() as con:
            con.execute(
                "UPDATE orders SET status='delivered' WHERE id=? AND status='delivering'",
                (order["id"],)
            )
        await message.reply_text("⭐ 25 Stars payment successful. Video deliver kar diya gaya!")
    except Exception:
        logger.exception("Video delivery failed for paid order %s", order["id"])
        with connect() as con:
            con.execute(
                "UPDATE orders SET status='delivery_pending' WHERE id=?",
                (order["id"],)
            )
        await message.reply_text(
            "Payment successful hai, lekin video bhejne mein issue aaya. Admin ko notify kar diya hai."
        )
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"⚠️ PAID VIDEO DELIVERY FAILED\n"
                f"Order: #{order['id']}\nUser: {user_id}\n"
                f"Use /retrydelivery {order['id']} after checking the video."
            )
        )


async def retry_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Format: /retrydelivery ORDER_ID")
        return

    order_id = int(context.args[0])
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        order = con.execute(
            """SELECT user_id,video_id,status FROM orders WHERE id=?""",
            (order_id,)
        ).fetchone()
        if not order or order["status"] != "delivery_pending":
            con.rollback()
            await update.message.reply_text("Paid pending-delivery order nahi mila.")
            return
        video = con.execute(
            "SELECT title,file_id FROM videos WHERE id=?",
            (order["video_id"],)
        ).fetchone()
        if not video:
            con.rollback()
            await update.message.reply_text("Video record nahi mila.")
            return
        con.execute("UPDATE orders SET status='delivering' WHERE id=?", (order_id,))
        con.commit()

    try:
        await context.bot.send_video(
            chat_id=order["user_id"],
            video=video["file_id"],
            caption=f"✅ Your paid video: {video['title']}"
        )
        with connect() as con:
            con.execute("UPDATE orders SET status='delivered' WHERE id=?", (order_id,))
        await update.message.reply_text(f"Order #{order_id} delivered.")
    except Exception:
        logger.exception("Retry delivery failed for order %s", order_id)
        with connect() as con:
            con.execute("UPDATE orders SET status='delivery_pending' WHERE id=?", (order_id,))
        await update.message.reply_text("Delivery abhi bhi fail hui. Hosting/video file check karo.")


async def admin_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    caption = update.message.caption or ""
    parts = caption.split(maxsplit=2)
    if len(parts) < 3 or parts[0] != "/addvideo":
        await update.message.reply_text(
            "Format (video caption):\n/addvideo 25 Video Name"
        )
        return

    try:
        stars = int(parts[1])
        title = parts[2].strip()
        if stars < 1 or not title:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Sahi format: /addvideo 25 Video Name")
        return

    with connect() as con:
        cur = con.execute(
            """INSERT INTO videos (title,price,file_id,stars_price)
               VALUES (?,?,?,?)""",
            (title, stars, update.message.video.file_id, stars)
        )
        video_id = cur.lastrowid

    await update.message.reply_text(
        f"✅ Video added!\nID: {video_id}\nName: {title}\nPrice: ⭐ {stars} Stars"
    )


async def setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(context.args) != 2:
        await update.message.reply_text(
            "Format: /setprice VIDEO_ID NEW_STARS\nExample: /setprice 1 25"
        )
        return
    try:
        video_id = int(context.args[0])
        stars = int(context.args[1])
        if stars < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Valid video ID aur Stars amount enter karo.")
        return

    with connect() as con:
        cur = con.execute(
            "UPDATE videos SET stars_price=? WHERE id=?",
            (stars, video_id)
        )
    if cur.rowcount:
        await update.message.reply_text(
            f"✅ Video #{video_id} ka price ⭐ {stars} Stars ho gaya."
        )
    else:
        await update.message.reply_text("Video ID nahi mila.")


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        "👑 CLIPSMINES ADMIN COMMANDS\n\n"
        "Video upload: video ke caption mein\n"
        "/addvideo 25 Video Name\n\n"
        "Price change (Stars):\n"
        "/setprice 1 25\n\n"
        "Failed paid delivery retry:\n"
        "/retrydelivery ORDER_ID\n\n"
        "/videos - Video list"
    )


def main():
    setup()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("videos", videos))
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("setprice", setprice))
    app.add_handler(CommandHandler("cancelorder", cancel_order))
    app.add_handler(CommandHandler("retrydelivery", retry_delivery))

    app.add_handler(CallbackQueryHandler(buy, pattern=r"^buy:"))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment)
    )
    app.add_handler(
        MessageHandler(
            filters.VIDEO & filters.User(user_id=ADMIN_ID),
            admin_upload
        )
    )

    # Render Free Web Services support HTTP webhooks, not a persistent polling worker.
    external_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not external_url.startswith("https://"):
        raise RuntimeError(
            "RENDER_EXTERNAL_URL is missing. Deploy this app as a Render Web Service."
        )

    webhook_path = os.environ.get("WEBHOOK_PATH") or secrets.token_urlsafe(24)
    webhook_secret = os.environ.get("WEBHOOK_SECRET") or secrets.token_urlsafe(32)
    port = int(os.environ.get("PORT", "10000"))
    webhook_url = f"{external_url}/{webhook_path}"

    logger.info("Starting Clipsmines Stars bot with Telegram webhook on port %s", port)
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=webhook_path,
        webhook_url=webhook_url,
        secret_token=webhook_secret,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
