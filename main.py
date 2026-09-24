import os
import asyncio
import yt_dlp

from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.environ["BOT_TOKEN"]

app = FastAPI()

telegram_app = Application.builder().token(BOT_TOKEN).build()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hello!\n\nSend me a public video URL and I will try to download it."
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("Please send a valid video URL.")
        return

    await update.message.reply_text("⏳ Checking the video...")

    try:
        filename = "video.%(ext)s"

        ydl_opts = {
            "outtmpl": filename,
            "format": "best[ext=mp4]/best",
            "noplaylist": True,
            "quiet": True,
        }

        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        file_path = await asyncio.to_thread(download)

        await update.message.reply_video(
            video=open(file_path, "rb"),
            caption="✅ Download complete"
        )

    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        await update.message.reply_text(
            "❌ I couldn't download this video.\n"
            "The website may not be supported or may block automated downloads."
        )


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url)
)


@app.get("/")
async def home():
    return {"status": "Telegram bot is running"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.update_queue.put(update)
    return {"ok": True}


@app.on_event("startup")
async def startup():
    await telegram_app.initialize()
    await telegram_app.start()

    render_url = os.environ["RENDER_EXTERNAL_URL"]
    webhook_url = render_url + "/webhook"

    await telegram_app.bot.set_webhook(webhook_url)

    print("Bot started")
    print("Webhook:", webhook_url)


@app.on_event("shutdown")
async def shutdown():
    await telegram_app.bot.delete_webhook()
    await telegram_app.stop()
    await telegram_app.shutdown()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "10000"))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
  )
