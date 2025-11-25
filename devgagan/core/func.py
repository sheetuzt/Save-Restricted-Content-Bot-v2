# ---------------------------------------------------
# File Name: func.py
# Description: Telethon version of helper utilities (converted from Pyrogram)
# Author: Gagan (converted)
# Converted to Telethon by Assistant
# Created: 2025-11-25
# ---------------------------------------------------

import math
import time
import re
import os
import asyncio
import subprocess
from datetime import datetime as dt
from typing import Optional

import cv2

# Correct Telethon RPC error imports (fixed)
from telethon import Button, functions, types
from telethon.errors.rpcerrorlist import (
    FloodWait,
    UserAlreadyParticipant,
    InviteHashInvalid,
    InviteHashExpired,
    UserNotParticipant,
)

from config import CHANNEL_ID, OWNER_ID
from devgagan.core.mongo.plans_db import premium_users

# ---------- Utilities ----------

async def chk_user(message, user_id: int):
    """
    Return 0 for premium/owner, 1 for free users (same behavior as original).
    """
    try:
        user = await premium_users()
        if user_id in user or user_id in OWNER_ID:
            return 0
        else:
            return 1
    except Exception as e:
        # Safe default: treat as free user on error
        print(f"[chk_user] {e}")
        return 1

async def gen_link(client, chat_id):
    """
    Try to export an invite link using Telethon client.
    client: Telethon client (your gf or another session)
    chat_id: numeric or username
    """
    try:
        # Telethon high-level may have export_chat_invite_link on client wrappers; try that first
        if hasattr(client, "export_chat_invite_link"):
            try:
                return await client.export_chat_invite_link(chat_id)
            except Exception:
                pass

        # Try using raw API (best-effort)
        try:
            res = await client(functions.messages.ExportChatInviteRequest(peer=chat_id))
            if hasattr(res, "link"):
                return res.link
        except Exception:
            pass

        # Fallback to constructing a public t.me link when chat_id is username or numeric channel
        if isinstance(chat_id, (str,)) and not str(chat_id).startswith("-100"):
            return f"https://t.me/{chat_id}"
        # fallback unknown
        return None
    except Exception as e:
        print(f"[gen_link] {e}")
        return None

async def subscribe(client, message):
    """
    Ensure user subscribed to CHANNEL_ID. If not, send an invite/link prompt.
    client: Telethon client (to export invite)
    message: Telethon Message object (caller)
    """
    update_channel = CHANNEL_ID
    try:
        url = await gen_link(client, update_channel)
    except Exception:
        url = None

    if not update_channel:
        return None

    try:
        # Try to see if the user is participant
        try:
            # Telethon get_participant/get_permissions usage may vary; try get_participant
            # get_participant accepts (channel, user) where user may be id or username
            participant = await client.get_participant(update_channel, message.from_id.user_id if getattr(message, 'from_id', None) else message.sender_id)
            # If returned without exception, treat as participant
            return 0
        except UserNotParticipant:
            # not participant
            pass
        except Exception:
            # Some wrappers raise different exceptions — fallback to trying to fetch chat member via functions
            try:
                await client(functions.channels.GetParticipantRequest(channel=update_channel, participant=message.sender_id))
                return 0
            except Exception:
                pass

        # If here, assume user not participant
        caption = "Join our channel to use the bot"
        # reply with a photo + button if we have a link
        buttons = [[Button.url("Join Now...", url)]] if url else None
        try:
            # Telethon Message object supports reply
            await message.reply(file="https://envs.sh/F6T.jpg", message=caption, buttons=buttons)
        except Exception:
            try:
                # fallback to send_file
                await client.send_file(message.chat_id or message.sender_id, "https://envs.sh/F6T.jpg", caption=caption, buttons=buttons)
            except Exception:
                # final fallback: send plain message
                await client.send_message(message.chat_id or message.sender_id, caption)
        return 1

    except Exception as e:
        print(f"[subscribe] {e}")
        try:
            await message.reply("Something Went Wrong. Contact us @Pre_contact_bot...")
        except:
            pass
        return 1

# --------- Time helpers ----------
async def get_seconds(time_string: str) -> int:
    """
    Parse strings like '5min', '2hour', '10s' etc. and return seconds.
    """
    def extract_value_and_unit(ts):
        value = ""
        unit = ""
        index = 0
        while index < len(ts) and ts[index].isdigit():
            value += ts[index]
            index += 1
        unit = ts[index:].lstrip()
        if value:
            value = int(value)
        return value, unit

    value, unit = extract_value_and_unit(time_string)
    if unit == 's':
        return value
    elif unit == 'min':
        return value * 60
    elif unit == 'hour':
        return value * 3600
    elif unit == 'day':
        return value * 86400
    elif unit == 'month':
        return value * 86400 * 30
    elif unit == 'year':
        return value * 86400 * 365
    else:
        return 0

# ---------- Progress helpers ----------
PROGRESS_BAR = """\n
│ **__Completed:__** {1}/{2}
│ **__Bytes:__** {0}%
│ **__Speed:__** {3}/s
│ **__ETA:__** {4}
╰─────────────────────╯
"""

async def progress_bar(current, total, ud_type, message, start):
    """
    Similar behavior to original: periodically edit 'message' with progress.
    message: Telethon Message (has .edit method)
    """
    try:
        now = time.time()
        diff = now - start
        # update only intermittently to avoid flooding
        if diff <= 0:
            diff = 0.1
        if round(diff % 10.00) == 0 or current == total:
            percentage = (current * 100) / total if total else 0
            speed = current / diff if diff else 0
            elapsed_time = round(diff) * 1000
            time_to_completion = round((total - current) / speed) * 1000 if speed else 0
            estimated_total_time = elapsed_time + time_to_completion
            elapsed_time_str = TimeFormatter(milliseconds=elapsed_time)
            estimated_total_time_str = TimeFormatter(milliseconds=estimated_total_time)
            progress = "{0}{1}".format(
                ''.join(["♦" for i in range(math.floor(percentage / 10))]),
                ''.join(["◇" for i in range(10 - math.floor(percentage / 10))]))
            tmp = progress + PROGRESS_BAR.format(
                round(percentage, 2),
                humanbytes(current),
                humanbytes(total),
                humanbytes(speed),
                estimated_total_time_str if estimated_total_time_str != '' else "0 s"
            )
            try:
                # Telethon Message.edit(text=...) or .edit may be supported
                if hasattr(message, "edit"):
                    await message.edit(f"{ud_type}\n│ {tmp}")
                elif hasattr(message, "edit_text"):
                    await message.edit_text(f"{ud_type}\n│ {tmp}")
            except Exception:
                pass
    except Exception as e:
        print(f"[progress_bar] {e}")

def humanbytes(size):
    if not size:
        return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN[n] + 'B'

def TimeFormatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = ((str(days) + "d, ") if days else "") + \
        ((str(hours) + "h, ") if hours else "") + \
        ((str(minutes) + "m, ") if minutes else "") + \
        ((str(seconds) + "s, ") if seconds else "") + \
        ((str(milliseconds) + "ms, ") if milliseconds else "")
    return tmp[:-2]

def convert(seconds: int) -> str:
    seconds = seconds % (24 * 3600)
    hour = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    return "%d:%02d:%02d" % (hour, minutes, seconds)

# ---------- Join helpers ----------
async def userbot_join(client, invite_link: str) -> str:
    """
    Best-effort join using Telethon client and the invite link.
    For channel username (t.me/username), use JoinChannelRequest
    For joinchat links, use ImportChatInviteRequest with hash.
    """
    try:
        # if it's a t.me/username or username string
        if invite_link.startswith("https://t.me/") or invite_link.startswith("http://t.me/"):
            target = invite_link.split("/")[-1]
            # If it's a joinchat token starting with '+', use ImportChatInviteRequest
            if target.startswith('+') or target.startswith('joinchat'):
                token = target.replace('+', '')
                try:
                    await client(functions.messages.ImportChatInviteRequest(token))
                    return "Successfully joined the Channel"
                except UserAlreadyParticipant:
                    return "User is already a participant."
                except (InviteHashInvalid, InviteHashExpired):
                    return "Could not join. Maybe your link is expired or Invalid."
                except FloodWait:
                    return "Too many requests, try again later."
                except Exception as e:
                    print(f"[userbot_join.import] {e}")
                    return "Could not join, try joining manually."
            else:
                # username/channel - use JoinChannelRequest
                try:
                    await client(functions.channels.JoinChannelRequest(channel=target))
                    return "Successfully joined the Channel"
                except UserAlreadyParticipant:
                    return "User is already a participant."
                except FloodWait:
                    return "Too many requests, try again later."
                except Exception as e:
                    print(f"[userbot_join.join] {e}")
                    return "Could not join, try joining manually."
        else:
            # If provided a chat id or peer, attempt join via JoinChannelRequest
            try:
                await client(functions.channels.JoinChannelRequest(channel=invite_link))
                return "Successfully joined the Channel"
            except Exception as e:
                print(f"[userbot_join.fallback] {e}")
                return "Could not join, try joining manually."
    except Exception as e:
        print(f"[userbot_join] {e}")
        return "Could not join, try joining manually."

# ---------- URL helper ----------
def get_link(string: str):
    """
    Extract first URL from string (same regex as original).
    """
    regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
    url = re.findall(regex, string)
    try:
        link = [x[0] for x in url][0]
        if link:
            return link
        else:
            return False
    except Exception:
        return False

# ---------- Video metadata & screenshot ----------
def video_metadata(file: str):
    """
    Use cv2 to extract width, height, duration (frames/fps). Returns defaults on failure.
    """
    default_values = {'width': 1, 'height': 1, 'duration': 1}
    try:
        vcap = cv2.VideoCapture(file)
        if not vcap.isOpened():
            return default_values
        width = round(vcap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = round(vcap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = vcap.get(cv2.CAP_PROP_FPS)
        frame_count = vcap.get(cv2.CAP_PROP_FRAME_COUNT)
        vcap.release()
        if not fps or fps <= 0:
            return default_values
        duration = round(frame_count / fps)
        if duration <= 0:
            return default_values
        return {'width': width, 'height': height, 'duration': duration}
    except Exception as e:
        print(f"Error in video_metadata: {e}")
        return default_values

def hhmmss(seconds):
    return time.strftime('%H:%M:%S', time.gmtime(seconds))

async def screenshot(video: str, duration: float, sender: int):
    """
    Create a thumbnail (screenshot) using ffmpeg at midpoint of video.
    Returns path to thumbnail or None.
    """
    try:
        if os.path.exists(f'{sender}.jpg'):
            return f'{sender}.jpg'
        # compute timestamp in format HH:MM:SS
        midpoint = int(duration / 2) if duration else 0
        time_stamp = hhmmss(midpoint)
        out = dt.now().isoformat("_", "seconds") + ".jpg"
        cmd = [
            "ffmpeg",
            "-ss", f"{time_stamp}",
            "-i", f"{video}",
            "-frames:v", "1",
            f"{out}",
            "-y"
        ]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if os.path.isfile(out):
            return out
        return None
    except Exception as e:
        print(f"[screenshot] {e}")
        return None

# ---------- Progress callback (alt) ----------
last_update_time = time.time()

async def progress_callback(current, total, progress_message):
    """
    Simpler periodic progress editor for Telethon messages.
    progress_message: Telethon Message object (with .edit or .edit_text)
    """
    try:
        percent = (current / total) * 100 if total else 0
        global last_update_time
        current_time = time.time()
        if current_time - last_update_time >= 10 or (percent % 10 == 0 and percent != 0):
            completed_blocks = int(percent // 10)
            remaining_blocks = 10 - completed_blocks
            progress_bar = "♦" * completed_blocks + "◇" * remaining_blocks
            current_mb = current / (1024 * 1024) if current else 0
            total_mb = total / (1024 * 1024) if total else 0
            text = (
                "╭──────────────────╮\n"
                "│        **__Uploading...__**       \n"
                "├──────────\n"
                f"│ {progress_bar}\n\n"
                f"│ **__Progress:__** {percent:.2f}%\n"
                f"│ **__Uploaded:__** {current_mb:.2f} MB / {total_mb:.2f} MB\n"
                "╰──────────────────╯\n\n"
                "**__Powered by unknown man__**"
            )
            try:
                if hasattr(progress_message, "edit"):
                    await progress_message.edit(text)
                elif hasattr(progress_message, "edit_text"):
                    await progress_message.edit_text(text)
            except Exception:
                pass
            last_update_time = current_time
    except Exception as e:
        print(f"[progress_callback] {e}")

async def prog_bar(current, total, ud_type, message, start):
    """
    Alternate progress function that edits message text (keeps compatibility with prior naming).
    """
    try:
        now = time.time()
        diff = now - start
        if diff <= 0:
            diff = 0.1
        if round(diff % 10.00) == 0 or current == total:
            percentage = (current * 100) / total if total else 0
            speed = current / diff if diff else 0
            elapsed_time = round(diff) * 1000
            time_to_completion = round((total - current) / speed) * 1000 if speed else 0
            estimated_total_time = elapsed_time + time_to_completion
            elapsed_time_str = TimeFormatter(milliseconds=elapsed_time)
            estimated_total_time_str = TimeFormatter(milliseconds=estimated_total_time)
            progress = "{0}{1}".format(
                ''.join(["♦" for i in range(math.floor(percentage / 10))]),
                ''.join(["◇" for i in range(10 - math.floor(percentage / 10))])
            )
            tmp = progress + PROGRESS_BAR.format(
                round(percentage, 2),
                humanbytes(current),
                humanbytes(total),
                humanbytes(speed),
                estimated_total_time_str if estimated_total_time_str != '' else "0 s"
            )
            try:
                if hasattr(message, "edit"):
                    await message.edit(text=f"{ud_type}\n│ {tmp}")
                elif hasattr(message, "edit_text"):
                    await message.edit_text(text=f"{ud_type}\n│ {tmp}")
            except Exception:
                pass
    except Exception as e:
        print(f"[prog_bar] {e}")
