import os
import re
import html
import asyncio
import tempfile
from urllib.parse import urljoin, urlparse

from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from urllib.request import Request as URLRequest
from urllib.request import urlopen


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

app = FastAPI()

telegram_app = (
    Application.builder()
    .token(BOT_TOKEN)
    .build()
)


# ============================================================
# HTTP HEADERS
# ============================================================

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


# ============================================================
# VALIDATE VIDSTAGE URL
# ============================================================

def is_vidstage_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        return (
            host == "vidstage.in"
            or host.endswith(".vidstage.in")
        )
    except Exception:
        return False


# ============================================================
# DOWNLOAD WEBPAGE
# ============================================================

def get_webpage(url: str) -> tuple[str, str]:

    request = URLRequest(
        url,
        headers=BROWSER_HEADERS,
        method="GET",
    )

    with urlopen(request, timeout=30) as response:

        final_url = response.geturl()

        content = response.read()

        charset = response.headers.get_content_charset()

        if charset:
            encoding = charset
        else:
            encoding = "utf-8"

        page = content.decode(
            encoding,
            errors="ignore",
        )

        return page, final_url


# ============================================================
# EXTRACT POSSIBLE MEDIA URLS
# ============================================================

def extract_media_urls(page: str, page_url: str):

    page = html.unescape(page)

    candidates = []

    # --------------------------------------------------------
    # <video src="...">
    # --------------------------------------------------------

    patterns = [

        r'<video[^>]+src=["\']([^"\']+)["\']',

        r'<source[^>]+src=["\']([^"\']+)["\']',

        r'<source[^>]+src\s*=\s*["\']([^"\']+)["\']',

        r'<video[^>]+data-src=["\']([^"\']+)["\']',

        r'data-video-url=["\']([^"\']+)["\']',

        r'data-src=["\']([^"\']+\.mp4[^"\']*)["\']',

        r'content=["\']([^"\']+\.mp4[^"\']*)["\']',

        r'"file"\s*:\s*"([^"]+\.mp4[^"]*)"', 

        r'"src"\s*:\s*"([^"]+\.mp4[^"]*)"', 

        r'"url"\s*:\s*"([^"]+\.mp4[^"]*)"', 

        r"'file'\s*:\s*'([^']+\.mp4[^']*)'",

        r"'src'\s*:\s*'([^']+\.mp4[^']*)'",

    ]

    for pattern in patterns:

        matches = re.findall(
            pattern,
            page,
            flags=re.IGNORECASE,
        )

        for match in matches:

            candidate = match

            # Decode escaped JSON URLs
            candidate = candidate.replace(
                "\\/",
                "/"
            )

            candidate = candidate.replace(
                "\\u0026",
                "&"
            )

            candidate = html.unescape(candidate)

            absolute = urljoin(
                page_url,
                candidate
            )

            if absolute not in candidates:
                candidates.append(absolute)

    # --------------------------------------------------------
    # Find any direct MP4-looking URL
    # --------------------------------------------------------

    generic_mp4 = re.findall(
        r'https?://[^"\'\s<>\\]+?\.mp4(?:\?[^"\'\s<>\\]*)?',
        page,
        flags=re.IGNORECASE,
    )

    for candidate in generic_mp4:

        candidate = candidate.replace(
            "\\/",
            "/"
        )

        candidate = html.unescape(candidate)

        if candidate not in candidates:
            candidates.append(candidate)

    return candidates


# ============================================================
# CHECK WHETHER URL IS A REAL VIDEO
# ============================================================

def download_video(
    video_url: str,
    output_path: str,
):

    headers = dict(BROWSER_HEADERS)

    headers["Referer"] = (
        "https://vidstage.in/"
    )

    headers["Accept"] = (
        "video/mp4,video/*;q=0.9,*/*;q=0.8"
    )

    request = URLRequest(
        video_url,
        headers=headers,
        method="GET",
    )

    with urlopen(
        request,
        timeout=60,
    ) as response:

        content_type = (
            response.headers.get(
                "Content-Type",
                ""
            ).lower()
        )

        total_size = response.headers.get(
            "Content-Length"
        )

        if total_size:
            try:
                total_size = int(total_size)
            except ValueError:
                total_size = None

        # ----------------------------------------------------
        # Download in chunks
        # ----------------------------------------------------

        downloaded = 0

        with open(
            output_path,
            "wb"
        ) as output:

            while True:

                chunk = response.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                output.write(chunk)

                downloaded += len(chunk)

                # Prevent extremely large downloads
                # on the free Render instance.
                if downloaded > 49 * 1024 * 1024:
                    raise RuntimeError(
                        "Video is larger than 49 MB."
                    )

        # ----------------------------------------------------
        # Basic validation
        # ----------------------------------------------------

        if downloaded < 1000:
            raise RuntimeError(
                "Downloaded file is too small."
            )

        # MP4 normally begins with an ftyp box.
        with open(
            output_path,
            "rb"
        ) as f:

            header = f.read(32)

        if (
            b"ftyp" not in header
            and "video/mp4" not in content_type
        ):
            raise RuntimeError(
                "The URL did not return an MP4 video."
            )

        return downloaded


# ============================================================
# FIND VIDSTAGE VIDEO
# ============================================================

def find_vidstage_video(
    page_url: str,
):

    page, final_url = get_webpage(
        page_url
    )

    # First attempt:
    # Look directly in Vidstage HTML.
    candidates = extract_media_urls(
        page,
        final_url
    )

    print(
        "VIDSTAGE CANDIDATES:",
        candidates
    )

    # --------------------------------------------------------
    # Try each candidate
    # --------------------------------------------------------

    for candidate in candidates:

        try:

            print(
                "Trying video URL:",
                candidate
            )

            return candidate

        except Exception as e:

            print(
                "Candidate failed:",
                e
            )

    raise RuntimeError(
        "No direct MP4 video URL was found "
        "inside the Vidstage page."
    )


# ============================================================
# START COMMAND
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "👋 Send me a Vidstage video link and "
        "I will try to download the MP4."
    )


# ============================================================
# HANDLE URL
# ============================================================

async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    url = update.message.text.strip()

    # --------------------------------------------------------
    # Validate URL
    # --------------------------------------------------------

    if not url.startswith(
        ("http://", "https://")
    ):

        await update.message.reply_text(
            "❌ Please send a valid Vidstage URL."
        )

        return

    if not is_vidstage_url(url):

        await update.message.reply_text(
            "❌ Please send a Vidstage.in video URL."
        )

        return

    status_message = await update.message.reply_text(
        "🔎 Opening the Vidstage video..."
    )

    temp_path = None

    try:

        # ----------------------------------------------------
        # Find actual video URL
        # ----------------------------------------------------

        video_url = await asyncio.to_thread(
            find_vidstage_video,
            url
        )

        print(
            "FOUND VIDEO URL:",
            video_url
        )

        await status_message.edit_text(
            "⬇️ Video found. Downloading MP4..."
        )

        # ----------------------------------------------------
        # Temporary file
        # ----------------------------------------------------

        temp_file = tempfile.NamedTemporaryFile(
            suffix=".mp4",
            delete=False
        )

        temp_path = temp_file.name

        temp_file.close()

        # ----------------------------------------------------
        # Download video
        # ----------------------------------------------------

        size = await asyncio.to_thread(
            download_video,
            video_url,
            temp_path
        )

        print(
            "DOWNLOAD COMPLETE:",
            size,
            "bytes"
        )

        await status_message.edit_text(
            "📤 Uploading MP4 to Telegram..."
        )

        # ----------------------------------------------------
        # Send video
        # ----------------------------------------------------

        with open(
            temp_path,
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                caption="✅ Download complete"
            )

        await status_message.delete()

    except Exception as e:

        print(
            "DOWNLOAD ERROR:",
            repr(e)
        )

        try:

            await status_message.edit_text(
                "❌ I couldn't download this video.\n\n"
                "The Vidstage page may not expose a direct "
                "MP4 URL to automated requests."
            )

        except Exception:
            pass

    finally:

        # ----------------------------------------------------
        # Delete temporary file
        # ----------------------------------------------------

        if temp_path:

            try:

                if os.path.exists(temp_path):
                    os.remove(temp_path)

            except Exception as e:

                print(
                    "TEMP FILE DELETE ERROR:",
                    e
                )


# ============================================================
# TELEGRAM HANDLERS
# ============================================================

telegram_app.add_handler(
    CommandHandler(
        "start",
        start
    )
)

telegram_app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_url
    )
)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/")
async def home():

    return {
        "status": "Telegram bot is running"
    }


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.post("/webhook")
async def webhook(
    request: Request
):

    data = await request.json()

    update = Update.de_json(
        data,
        telegram_app.bot
    )

    await telegram_app.process_update(
        update
    )

    return {
        "ok": True
    }


# ============================================================
# STARTUP
# ============================================================
@app.on_event("startup")
async def startup():

    await telegram_app.initialize()

    await telegram_app.start()

    render_url = os.environ.get("RENDER_EXTERNAL_URL")

    if render_url:
        webhook_url = f"{render_url}/webhook"

        await telegram_app.bot.set_webhook(
            url=webhook_url
        )

        print(
            f"Telegram webhook set to: {webhook_url}"
        )

    print(
        "Telegram bot started successfully."
    )
    


# ============================================================
# SHUTDOWN
# ============================================================

@app.on_event("shutdown")
async def shutdown():

    await telegram_app.stop()

    await telegram_app.shutdown()

    print(
        "Telegram bot stopped."
    )
    
