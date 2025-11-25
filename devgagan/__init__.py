# ---------------------------------------------------
# File Name: __init__.py
# Description: A Telegram bot bootstrap (Pyrogram + Telethon safe startup)
# Author: Gagan
# GitHub: https://github.com/devgaganin/
# Telegram: https://t.me/team_spy_pro
# YouTube: https://youtube.com/@dev_gagan
# Created: 2025-01-11
# Last Modified: 2025-11-25 (patched)
# Version: 2.0.5
# License: MIT License
# ---------------------------------------------------

import asyncio
import logging
import time
import sys
from pyrogram import Client
from pyrogram.enums import ParseMode
from config import API_ID, API_HASH, BOT_TOKEN, STRING, MONGO_DB, DEFAULT_SESSION
from telethon import TelegramClient
from telethon.errors.rpcerrorlist import FloodWaitError
from motor.motor_asyncio import AsyncIOMotorClient

# Create and set a fresh event loop (original code did this)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

logging.basicConfig(
    format="[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s",
    level=logging.INFO,
)

botStartTime = time.time()

# Pyrogram bot client (kept as before)
app = Client(
    "pyrobot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=50,
    parse_mode=ParseMode.MARKDOWN
)

# We'll create Telethon clients but start them safely inside restrict_bot()
sex = None
telethon_client = None

# Pro client (pyrogram) when STRING is provided
if STRING:
    pro = Client("ggbot", api_id=API_ID, api_hash=API_HASH, session_string=STRING)
else:
    pro = None

# Optional userrbot (pyrogram) if DEFAULT_SESSION is provided
if DEFAULT_SESSION:
    userrbot = Client("userrbot", api_id=API_ID, api_hash=API_HASH, session_string=DEFAULT_SESSION)
else:
    userrbot = None

# MongoDB setup (async motor)
tclient = AsyncIOMotorClient(MONGO_DB)
tdb = tclient["telegram_bot"]  # database
token = tdb["tokens"]  # tokens collection

async def create_ttl_index():
    """Ensure the TTL index exists for the `tokens` collection."""
    try:
        await token.create_index("expires_at", expireAfterSeconds=0)
        print("MongoDB TTL index ensured.")
    except Exception as e:
        print(f"[DB] Failed to create TTL index: {e}")

# Run the TTL index creation when the bot starts
async def setup_database():
    await create_ttl_index()

async def safe_start_telethon(session_name: str, bot_token: str):
    """
    Create and try to start a Telethon TelegramClient safely.
    Returns the started client or None on failure.
    """
    client = TelegramClient(session_name, API_ID, API_HASH)
    try:
        # Await the async start; this may raise FloodWaitError
        await client.start(bot_token=bot_token)
        print(f"[INFO] Telethon client '{session_name}' started successfully.")
        return client
    except FloodWaitError as fw:
        wait_secs = getattr(fw, "seconds", None) or getattr(fw, "wait", None) or "unknown"
        print(f"[FLOODWAIT] Telethon start for '{session_name}' blocked by Telegram. Need to wait: {wait_secs} seconds.")
        # Return None to indicate client not started — do not crash
        try:
            # Disconnect/cleanup if partially initialized
            await client.disconnect()
        except Exception:
            pass
        return None
    except Exception as e:
        print(f"[WARN] Failed to start Telethon client '{session_name}': {type(e).__name__}: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return None

async def restrict_bot():
    """
    Main startup: DB setup, start pyrogram app and optional clients,
    then start Telethon clients safely.
    """
    global sex, telethon_client, pro, userrbot

    # Setup DB indices
    await setup_database()

    # Start Pyrogram bot (app) and fetch bot info
    try:
        await app.start()
        getme = await app.get_me()
        BOT_ID = getme.id
        BOT_USERNAME = getme.username
        BOT_NAME = f"{getme.first_name} {getme.last_name}" if getattr(getme, "last_name", None) else getme.first_name
        print(f"[INFO] Pyrogram bot started: @{BOT_USERNAME} ({BOT_ID})")
    except Exception as e:
        print(f"[FATAL] Failed to start Pyrogram bot: {type(e).__name__}: {e}")
        # If Pyrogram fails to start, still try to start other parts if appropriate
        # Optionally sys.exit(1) if you want to fail hard:
        # sys.exit(1)

    # Start pro (pyrogram) if provided
    if pro:
        try:
            await pro.start()
            print("[INFO] Pro Pyrogram client started.")
        except Exception as e:
            print(f"[WARN] Could not start pro client: {e}")

    # Start userrbot (pyrogram) if provided
    if userrbot:
        try:
            await userrbot.start()
            print("[INFO] Userrbot (pyrogram) started.")
        except Exception as e:
            print(f"[WARN] Could not start userrbot: {e}")

    # Start Telethon clients safely (these may hit FloodWait)
    # sex client (session name 'sexrepo')
    try:
        sex = await safe_start_telethon("sexrepo", BOT_TOKEN)
    except Exception as e:
        print(f"[WARN] Unexpected error when starting sex Telethon client: {e}")
        sex = None

    # telethon_client (session name 'telethon_session')
    try:
        telethon_client = await safe_start_telethon("telethon_session", BOT_TOKEN)
    except Exception as e:
        print(f"[WARN] Unexpected error when starting telethon_client: {e}")
        telethon_client = None

    # Final status
    print("Startup summary:")
    print(f"  • Pyrogram bot: {'✅' if getme else '❌'}")
    print(f"  • Pro client: {'✅' if pro else '❌'}")
    print(f"  • Userrbot: {'✅' if userrbot else '❌'}")
    print(f"  • Telethon sex: {'✅' if sex else '❌'}")
    print(f"  • Telethon telethon_client: {'✅' if telethon_client else '❌'}")

# Run the startup procedure
try:
    loop.run_until_complete(restrict_bot())
except Exception as e:
    print(f"[FATAL] Error during startup: {e}")
    # Do not re-raise to avoid orchestrator crash loop; exit only if necessary.
    # sys.exit(1)
