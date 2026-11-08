import os
import sqlite3
import logging
import secrets
import asyncio
import base64
import json
import urllib.request

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
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = 7572110906
DB = os.getenv("DB_PATH", "bot_data.db")
GITHUB_REPO = os.getenv("GITHUB_REPO", "kanhuroul001-ai/Paid_cm_bot")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "stars-payment")


def sync_website_catalogue_sync():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Render environment mein GITHUB_TOKEN set nahi hai.")

    with connect() as con:
        rows = con.execute("SELECT id,title FROM videos ORDER BY id DESC").fetchall()
    payload = json.dumps([{"id": row["id"], "title": row["title"]} for row in rows], ensure_ascii=False)
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/videos.json?ref={GITHUB_BRANCH}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ClipsminesBot",
    }
    request = urllib.request.Request(api_url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        current = json.loads(response.read().decode("utf-8"))
    body = {
        "message": "Sync sample video catalogue from Telegram admin",
        "content": base64.b64encode(payload.encode("utf-8")).decode("ascii"),
        "sha": current["sha"],
        "branch": GITHUB_BRANCH,
    }
    request = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/videos.json",
        data=json.dumps(body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


async def sync_website_catalogue(update):
    try:
        await asyncio.to_thread(sync_website_catalogue_sync)
        await update.message.reply_text("🌐 Website catalogue successfully updated!")
        return True
    except Exception as exc:
        logger.exception("Website catalogue sync failed")
        await update.message.reply_text(
            "⚠️ Telegram catalogue update ho gaya, lekin website sync nahi hua. "
            f"Admin setup check karo. Details: {str(exc)[:250]}"
        )
        return False


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
                price INTEGER NOT NULL DEFAULT 0,
                file_id TEXT NOT NULL
            )
        """)


def is_admin(update):
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to Clipsmines! 🎬\n\n"
        "Sample videos dekhne ke liye /videos bhejo."
    )


async def videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with connect() as con:
        rows = con.execute(
            "SELECT id,title FROM videos ORDER BY id DESC"
        ).fetchall()

    if not rows:
        await update.message.reply_text("Abhi koi sample video available nahi hai.")
        return

    buttons = [
        [InlineKeyboardButton(
            f"▶️ Sample: {row['title']}",
            callback_data=f"sample:{row['id']}"
        )]
        for row in rows
    ]
    await update.message.reply_text(
        "🎬 Clipsmines sample catalogue — video dekhne ke liye select karo:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_sample(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        video_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.message.reply_text("Invalid sample selection.")
        return

    with connect() as con:
        video = con.execute(
            "SELECT title,file_id FROM videos WHERE id=?",
            (video_id,)
        ).fetchone()

    if not video:
        await query.message.reply_text("Sample video available nahi hai.")
        return

    await context.bot.send_video(
        chat_id=query.from_user.id,
        video=video["file_id"],
        caption=f"🎬 Sample: {video['title']}"
    )


async def admin_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    caption = update.message.caption or ""
    parts = caption.split(maxsplit=1)
    if len(parts) < 2 or parts[0] != "/addvideo":
        await update.message.reply_text(
            "Sample upload format (video caption):\n/addvideo Video Name"
        )
        return

    title = parts[1].strip()
    if not title:
        await update.message.reply_text("Video ka naam enter karo.")
        return

    with connect() as con:
        cur = con.execute(
            "INSERT INTO videos (title,file_id) VALUES (?,?)",
            (title, update.message.video.file_id)
        )
        video_id = cur.lastrowid

    await update.message.reply_text(
        f"✅ Sample video added!\nID: {video_id}\nName: {title}\n"
        "Customers ise /videos mein dekh sakte hain."
    )
    await sync_website_catalogue(update)


async def listvideos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    with connect() as con:
        rows = con.execute(
            "SELECT id,title FROM videos ORDER BY id DESC"
        ).fetchall()
    if not rows:
        await update.message.reply_text("Abhi koi sample video upload nahi hai.")
        return

    lines = ["🎬 CLIPSMINES SAMPLE VIDEOS", ""]
    for row in rows:
        lines.append(f"ID: {row['id']} | {row['title']}")
    await update.message.reply_text("\n".join(lines))


async def deletevideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Format: /deletevideo VIDEO_ID")
        return
    video_id = int(context.args[0])
    with connect() as con:
        video = con.execute(
            "SELECT id,title FROM videos WHERE id=?", (video_id,)
        ).fetchone()
        if not video:
            await update.message.reply_text("Video ID nahi mila.")
            return
        con.execute("DELETE FROM videos WHERE id=?", (video_id,))
    await update.message.reply_text(
        f"🗑️ Sample delete ho gaya.\nID: {video_id}\nName: {video['title']}"
    )
    await sync_website_catalogue(update)


async def syncwebsite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await sync_website_catalogue(update)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    with connect() as con:
        video_count = con.execute(
            "SELECT COUNT(*) AS n FROM videos"
        ).fetchone()["n"]
    await update.message.reply_text(
        f"📊 CLIPSMINES STATS\n\n🎬 Sample videos: {video_count}"
    )


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        "👑 CLIPSMINES ADMIN COMMANDS\n\n"
        "Upload a sample video: video ke caption mein\n"
        "/addvideo Video Name\n\n"
        "All sample videos:\n/listvideos\n\n"
        "Delete a sample video:\n/deletevideo VIDEO_ID\n\n"
        "Bot stats:\n/stats\n\n"
        "/syncwebsite - Website catalogue manually sync\n\n"
        "/videos - Customer sample catalogue"
    )


def main():
    setup()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("videos", videos))
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("listvideos", listvideos))
    app.add_handler(CommandHandler("deletevideo", deletevideo))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("syncwebsite", syncwebsite))
    app.add_handler(CallbackQueryHandler(show_sample, pattern=r"^sample:"))
    app.add_handler(
        MessageHandler(
            filters.VIDEO & filters.User(user_id=ADMIN_ID),
            admin_upload
        )
    )

    external_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not external_url.startswith("https://"):
        raise RuntimeError(
            "RENDER_EXTERNAL_URL is missing. Deploy this app as a Render Web Service."
        )

    webhook_path = os.environ.get("WEBHOOK_PATH") or secrets.token_urlsafe(24)
    webhook_secret = os.environ.get("WEBHOOK_SECRET") or secrets.token_urlsafe(32)
    port = int(os.environ.get("PORT", "10000"))
    webhook_url = f"{external_url}/{webhook_path}"

    logger.info("Starting Clipsmines sample catalogue bot on port %s", port)
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
