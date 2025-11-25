# ---------------------------------------------------
# File Name: get_func.py
# Description: SmartTelegramBot rewritten to use Telethon (SpyLib/gf) only
#              Pyrogram references removed. Uploads and story/save use Telethon.
# Author: Gagan (adapted)
# Modified by: Assistant (Telethon-only conversion)
# Date: 2025-11-25
# ---------------------------------------------------

import asyncio
import os
import re
import time
import gc
import subprocess
from typing import Dict, Set, Optional, Any, Tuple
from pathlib import Path
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from collections import defaultdict

import aiofiles
import pymongo
from telethon import events, Button
from telethon.tl.types import DocumentAttributeVideo
from telethon.errors import RPCError, FloodWait, ChatAdminRequired

# Your project imports (ensure devgagan.sex is Telethon client)
from devgagan import sex as gf        # Telethon userbot (main client now)
from devgagan import pro if 'pro' in globals() else None  # optional pro client if configured
from devgagantools import fast_upload  # fast_upload used for Telethon uploads (if available)
from devgagan.core.func import progress_bar, video_metadata, screenshot
from devgagan.core.mongo import db as odb
from config import MONGO_DB as MONGODB_CONNECTION_STRING, LOG_GROUP, OWNER_ID

# ----------------- Config / Dataclasses -----------------
@dataclass
class BotConfig:
    DB_NAME: str = "smart_users"
    COLLECTION_NAME: str = "super_user"
    VIDEO_EXTS: Set[str] = field(default_factory=lambda: {
        'mp4', 'mov', 'avi', 'mkv', 'flv', 'wmv', 'webm', 'mpg', 'mpeg',
        '3gp', 'ts', 'm4v', 'f4v', 'vob'
    })
    DOC_EXTS: Set[str] = field(default_factory=lambda: {'pdf', 'docx', 'txt', 'epub', 'docs'})
    IMG_EXTS: Set[str] = field(default_factory=lambda: {'jpg', 'jpeg', 'png', 'webp'})
    AUDIO_EXTS: Set[str] = field(default_factory=lambda: {'mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg'})
    SIZE_LIMIT: int = 2 * 1024**3  # 2GB
    PART_SIZE: int = int(1.9 * 1024**3)  # 1.9GB splitting
    SETTINGS_PIC: str = "settings.jpg"

@dataclass
class UserProgress:
    previous_done: int = 0
    previous_time: float = field(default_factory=time.time)

# ----------------- Database Manager -----------------
class DatabaseManager:
    def __init__(self, connection_string: str, db_name: str, collection_name: str):
        self.client = pymongo.MongoClient(connection_string)
        self.collection = self.client[db_name][collection_name]
        self._cache = {}

    def get_user_data(self, user_id: int, key: str, default=None) -> Any:
        cache_key = f"{user_id}:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            doc = self.collection.find_one({"_id": user_id})
            value = doc.get(key, default) if doc else default
            self._cache[cache_key] = value
            return value
        except Exception as e:
            print(f"[DB read] {e}")
            return default

    def save_user_data(self, user_id: int, key: str, value: Any) -> bool:
        cache_key = f"{user_id}:{key}"
        try:
            self.collection.update_one({"_id": user_id}, {"$set": {key: value}}, upsert=True)
            self._cache[cache_key] = value
            return True
        except Exception as e:
            print(f"[DB save] {e}")
            return False

    def clear_user_cache(self, user_id: int):
        keys_to_remove = [k for k in self._cache.keys() if k.startswith(f"{user_id}:")]
        for k in keys_to_remove:
            del self._cache[k]

    def get_protected_channels(self) -> Set[int]:
        try:
            return {doc["channel_id"] for doc in self.collection.find({"channel_id": {"$exists": True}})}
        except Exception:
            return set()

    def lock_channel(self, channel_id: int) -> bool:
        try:
            self.collection.insert_one({"channel_id": channel_id})
            return True
        except Exception:
            return False

    def reset_user_data(self, user_id: int) -> bool:
        try:
            self.collection.update_one({"_id": user_id}, {"$unset": {
                "delete_words": "", "replacement_words": "",
                "watermark_text": "", "duration_limit": "",
                "custom_caption": "", "rename_tag": ""
            }})
            self.clear_user_cache(user_id)
            return True
        except Exception as e:
            print(f"[DB reset] {e}")
            return False

# ----------------- Media Processor -----------------
class MediaProcessor:
    def __init__(self, config: BotConfig):
        self.config = config

    def get_file_type(self, filename: str) -> str:
        ext = Path(filename).suffix.lower().lstrip('.')
        if ext in self.config.VIDEO_EXTS:
            return 'video'
        if ext in self.config.IMG_EXTS:
            return 'photo'
        if ext in self.config.AUDIO_EXTS:
            return 'audio'
        if ext in self.config.DOC_EXTS:
            return 'document'
        return 'document'

    @staticmethod
    def get_media_info(msg) -> Tuple[Optional[str], Optional[int], str]:
        # Telethon message object attributes differ; check common ones
        if getattr(msg, 'document', None):
            name = getattr(msg.document, 'attributes', None)
            # Telethon document.filename may exist:
            try:
                fname = msg.document.file_name
            except Exception:
                fname = "document"
            size = getattr(msg.document, 'size', None)
            return fname or "document", size, "document"
        if getattr(msg, 'video', None):
            fname = getattr(msg.video, 'file_name', None) or "video.mp4"
            size = getattr(msg.video, 'size', None)
            return fname, size, "video"
        if getattr(msg, 'photo', None):
            return "photo.jpg", getattr(msg.photo, 'sizes', [None]) and 1, "photo"
        if getattr(msg, 'audio', None):
            fname = getattr(msg.audio, 'file_name', None) or "audio.mp3"
            size = getattr(msg.audio, 'size', None)
            return fname, size, "audio"
        if getattr(msg, 'voice', None):
            return "voice.ogg", getattr(msg.voice, 'size', 1), "voice"
        return "unknown", 1, "document"

# ----------------- Progress Manager -----------------
class ProgressManager:
    def __init__(self):
        self.user_progress: Dict[int, UserProgress] = defaultdict(UserProgress)

    def calculate_progress(self, done: int, total: int, user_id: int, uploader: str = "SpyLib") -> str:
        user_data = self.user_progress[user_id]
        percent = (done / total) * 100 if total else 0
        progress_bar_txt = "♦" * int(percent // 10) + "◇" * (10 - int(percent // 10))
        done_mb, total_mb = done / (1024**2), total / (1024**2) if total else 0

        speed = max(0, done - user_data.previous_done)
        elapsed_time = max(0.1, time.time() - user_data.previous_time)
        speed_mbps = (speed * 8) / (1024**2 * elapsed_time) if elapsed_time > 0 else 0
        eta_seconds = ((total - done) / speed) if speed > 0 else 0
        eta_min = eta_seconds / 60 if eta_seconds else 0

        user_data.previous_done = done
        user_data.previous_time = time.time()

        return (
            f"╭──────────────────╮\n"
            f"│     **__{uploader} ⚡ Uploader__**\n"
            f"├──────────\n"
            f"│ {progress_bar_txt}\n\n"
            f"│ **__Progress:__** {percent:.2f}%\n"
            f"│ **__Done:__** {done_mb:.2f} MB / {total_mb:.2f} MB\n"
            f"│ **__Speed:__** {speed_mbps:.2f} Mbps\n"
            f"│ **__ETA:__** {eta_min:.2f} min\n"
            f"╰──────────────────╯\n\n"
            f"**__Powered by unknown man__**"
        )

# ----------------- Caption Formatter -----------------
class CaptionFormatter:
    @staticmethod
    async def markdown_to_html(caption: str) -> str:
        if not caption:
            return ""
        replacements = [
            (r"^> (.*)", r"<blockquote>\1</blockquote>"),
            (r"```(.*?)```", r"<pre>\1</pre>"),
            (r"`(.*?)`", r"<code>\1</code>"),
            (r"\*\*(.*?)\*\*", r"<b>\1</b>"),
            (r"\*(.*?)\*", r"<b>\1</b>"),
            (r"__(.*?)__", r"<i>\1</i>"),
            (r"_(.*?)_", r"<i>\1</i>"),
            (r"~~(.*?)~~", r"<s>\1</s>"),
            (r"\|\|(.*?)\|\|", r"<details>\1</details>"),
            (r"\[(.*?)\]\((.*?)\)", r'<a href="\2">\1</a>')
        ]
        result = caption
        for pattern, replacement in replacements:
            result = re.sub(pattern, replacement, result, flags=re.MULTILINE | re.DOTALL)
        return result.strip()

# ----------------- File Operations -----------------
class FileOperations:
    def __init__(self, config: BotConfig, db: DatabaseManager):
        self.config = config
        self.db = db

    @asynccontextmanager
    async def safe_file_operation(self, file_path: str):
        try:
            yield file_path
        finally:
            await self._cleanup_file(file_path)

    async def _cleanup_file(self, file_path: str):
        if file_path and os.path.exists(file_path):
            try:
                await asyncio.to_thread(os.remove, file_path)
            except Exception as e:
                print(f"[cleanup] {e}")

    async def process_filename(self, file_path: str, user_id: int) -> str:
        delete_words = set(self.db.get_user_data(user_id, "delete_words", []))
        replacements = self.db.get_user_data(user_id, "replacement_words", {})
        rename_tag = self.db.get_user_data(user_id, "rename_tag", "unknown man")

        path = Path(file_path)
        name = path.stem
        extension = path.suffix.lstrip('.')

        for word in delete_words:
            name = name.replace(word, "")

        for word, replacement in replacements.items():
            name = name.replace(word, replacement)

        if extension.lower() in self.config.VIDEO_EXTS and extension.lower() not in ['mp4']:
            extension = 'mp4'

        new_name = f"{name.strip()} {rename_tag}.{extension}"
        new_path = path.parent / new_name
        await asyncio.to_thread(os.rename, file_path, new_path)
        return str(new_path)

    async def split_large_file(self, file_path: str, app_client, sender: int, target_chat_id: int, caption: str, topic_id: Optional[int] = None):
        if not os.path.exists(file_path):
            await app_client.send_message(sender, "❌ File not found!")
            return

        file_size = os.path.getsize(file_path)
        start_msg = await app_client.send_message(sender, f"ℹ️ File size: {file_size / (1024**2):.2f} MB\n🔄 Splitting and uploading...")

        part_number = 0
        base_path = Path(file_path)

        try:
            async with aiofiles.open(file_path, mode="rb") as f:
                while True:
                    chunk = await f.read(self.config.PART_SIZE)
                    if not chunk:
                        break
                    part_file = f"{base_path.stem}.part{str(part_number).zfill(3)}{base_path.suffix}"
                    async with aiofiles.open(part_file, mode="wb") as part_f:
                        await part_f.write(chunk)

                    part_caption = f"{caption}\n\n**Part: {part_number + 1}**" if caption else f"**Part: {part_number + 1}**"
                    edit_msg = await app_client.send_message(target_chat_id, f"⬆️ Uploading part {part_number + 1}...")
                    try:
                        # Use Telethon send_file for parts
                        sent = await app_client.send_file(
                            target_chat_id,
                            part_file,
                            caption=part_caption
                        )
                        # copy to log group
                        try:
                            await app_client.send_file(LOG_GROUP, part_file, caption=part_caption)
                        except:
                            pass
                        await app_client.delete_messages(edit_msg.peer_id, [edit_msg.id])
                    finally:
                        if os.path.exists(part_file):
                            os.remove(part_file)
                    part_number += 1
        finally:
            await app_client.delete_messages(start_msg.peer_id, [start_msg.id]) if start_msg else None
            if os.path.exists(file_path):
                os.remove(file_path)

# ----------------- Telethon helper wrappers -----------------
async def tele_send_message(chat_id, text, **kwargs):
    return await gf.send_message(chat_id, text, **kwargs)

async def tele_send_file(chat_id, file, caption=None, thumb=None, attributes=None, reply_to=None, parse_mode=None):
    """
    send files via Telethon gf client.
    file can be path or previously uploaded object returned by fast_upload.
    """
    params = {}
    if caption:
        params['caption'] = caption
    if reply_to:
        params['reply_to'] = reply_to
    if thumb:
        params['thumb'] = thumb
    if attributes:
        params['attributes'] = attributes
    # gf.send_file accepts file path or uploaded object; this wrapper keeps it simple
    return await gf.send_file(chat_id, file, **params)

# ----------------- Main Bot Class -----------------
class SmartTelegramBot:
    def __init__(self):
        self.config = BotConfig()
        self.db = DatabaseManager(MONGODB_CONNECTION_STRING, self.config.DB_NAME, self.config.COLLECTION_NAME)
        self.media_processor = MediaProcessor(self.config)
        self.progress_manager = ProgressManager()
        self.file_ops = FileOperations(self.config, self.db)
        self.caption_formatter = CaptionFormatter()

        self.user_sessions: Dict[int, str] = {}
        self.pending_photos: Set[int] = set()
        self.user_chat_ids: Dict[int, int] = {}
        self.user_rename_prefs: Dict[str, str] = {}
        self.user_caption_prefs: Dict[str, str] = {}

        # Telethon clients
        self.userbot = gf           # primary Telethon client for actions
        self.pro_client = None
        try:
            # If pro client available in devgagan package (as in original), use it
            from devgagan import pro
            self.pro_client = pro
        except Exception:
            self.pro_client = None

        print(f"Pro client available: {'Yes' if self.pro_client else 'No'}")

    def get_thumbnail_path(self, user_id: int) -> Optional[str]:
        thumb_path = f'{user_id}.jpg'
        return thumb_path if os.path.exists(thumb_path) else None

    def parse_target_chat(self, target: str) -> Tuple[int, Optional[int]]:
        if '/' in target:
            parts = target.split('/')
            return int(parts[0]), int(parts[1])
        return int(target), None

    async def process_user_caption(self, original_caption: str, user_id: int) -> str:
        custom_caption = self.user_caption_prefs.get(str(user_id), "") or self.db.get_user_data(user_id, "custom_caption", "")
        delete_words = set(self.db.get_user_data(user_id, "delete_words", []))
        replacements = self.db.get_user_data(user_id, "replacement_words", {})

        processed = original_caption or ""
        for word in delete_words:
            processed = processed.replace(word, "")
        for word, replacement in replacements.items():
            processed = processed.replace(word, replacement)
        if custom_caption:
            processed = f"{processed}\n\n{custom_caption}".strip()
        return processed if processed else None

    async def upload_with_telethon(self, file_path: str, user_id: int, target_chat_id: int, caption: str, topic_id: Optional[int] = None, edit_msg=None):
        """
        Upload using Telethon (gf). Uses fast_upload where available for better performance.
        """
        try:
            if edit_msg:
                # delete intermediate edit message if exists
                try:
                    await gf.delete_messages(edit_msg.peer_id, [edit_msg.id])
                except:
                    pass

            progress_message = await gf.send_message(user_id, "**__SpyLib ⚡ Uploading...__**")
            html_caption = await self.caption_formatter.markdown_to_html(caption or "")

            # Use fast_upload if available
            uploaded = None
            try:
                uploaded = await fast_upload(gf, file_path, reply=progress_message, name=None, progress_bar_function=lambda d, t: self.progress_manager.calculate_progress(d, t, user_id, "SpyLib"), user_id=user_id)
            except Exception:
                uploaded = file_path

            # prepare attributes for video
            attributes = None
            file_type = self.media_processor.get_file_type(file_path)
            if file_type == 'video':
                try:
                    meta = video_metadata(file_path)
                    attributes = [DocumentAttributeVideo(duration=meta.get('duration', 0), w=meta.get('width', 0), h=meta.get('height', 0), supports_streaming=True)]
                except Exception:
                    attributes = None

            # send file to target chat
            await gf.send_file(target_chat_id, uploaded, caption=html_caption, attributes=attributes, reply_to=topic_id, parse_mode='html')

            # copy to log group
            try:
                await gf.send_file(LOG_GROUP, uploaded, caption=html_caption, attributes=attributes, parse_mode='html')
            except Exception:
                pass

            try:
                await progress_message.delete()
            except:
                pass

        except Exception as e:
            # log error to LOG_GROUP
            try:
                await gf.send_message(LOG_GROUP, f"**SpyLib Upload Failed:** {str(e)}")
            except:
                print(f"[upload error] {e}")
            raise

    async def handle_large_file_upload(self, file_path: str, sender: int, edit_msg, caption: str):
        """
        Handle >2GB uploads using pro client (Telethon user session capable of >2GB).
        """
        if not self.pro_client:
            # notify sender that pro not configured
            try:
                await gf.send_message(sender, '**❌ 4GB upload not available - Pro client not configured**')
            except:
                pass
            return

        try:
            await gf.send_message(sender, '**✅ 4GB upload starting...**')
            # pro client should have send_file; keep similar signature
            file_type = self.media_processor.get_file_type(file_path)
            attributes = None
            if file_type == 'video':
                meta = video_metadata(file_path)
                attributes = [DocumentAttributeVideo(duration=meta.get('duration', 0), w=meta.get('width', 0), h=meta.get('height', 0), supports_streaming=True)]

            result = await self.pro_client.send_file(LOG_GROUP, file_path, caption=caption, thumb=self.get_thumbnail_path(sender), attributes=attributes)
            # copy to user's target
            target_chat_str = self.user_chat_ids.get(sender, str(sender))
            target_chat_id, _ = self.parse_target_chat(target_chat_str)
            await self.pro_client.send_file(target_chat_id, result.media if hasattr(result, 'media') else file_path, caption=caption)
        except Exception as e:
            try:
                await gf.send_message(LOG_GROUP, f"**4GB Upload Error:** {str(e)}")
            except:
                print(f"[4GB error] {e}")
        finally:
            try:
                if edit_msg:
                    await gf.delete_messages(edit_msg.peer_id, [edit_msg.id])
            except:
                pass

    async def handle_message_download(self, userbot, sender: int, edit_id: int, msg_link: str, offset: int, message):
        """
        Main message handling using Telethon everywhere.
        userbot is expected to be a Telethon client (gf) — you can pass another session if needed.
        """
        edit_msg = None
        file_path = None
        thumb_path = None

        try:
            msg_link = msg_link.split("?single")[0]
            protected_channels = self.db.get_protected_channels()

            chat_id, msg_id = await self._parse_message_link(msg_link, offset, protected_channels, sender, edit_id)
            if not chat_id:
                return

            target_chat_str = self.user_chat_ids.get(message.chat.id, str(message.chat.id))
            target_chat_id, topic_id = self.parse_target_chat(target_chat_str)

            # fetch message via userbot (Telethon)
            msg = await userbot.get_messages(chat_id, ids=msg_id)
            if not msg or getattr(msg, 'service', False) or getattr(msg, 'empty', False):
                # delete the edit message id if exists on user's chat if necessary
                try:
                    await gf.delete_messages(sender, [edit_id])
                except:
                    pass
                return

            # special messages (web preview / text)
            if await self._handle_special_messages(msg, target_chat_id, topic_id, edit_id, sender):
                return

            if not getattr(msg, 'media', None):
                return

            filename, file_size, media_type = self.media_processor.get_media_info(msg)

            # direct media (sticker, voice, video_note) can be forwarded/sent directly
            if await self._handle_direct_media(msg, target_chat_id, topic_id, edit_id, media_type):
                return

            # start download
            # send an "editing" message to user (using gf)
            try:
                edit_msg = await gf.send_message(sender, "**📥 Downloading...**")
            except:
                edit_msg = None

            file_path = await userbot.download_media(msg, file=filename, progress_callback=lambda d, t: None)

            # process caption & filename
            caption = await self.process_user_caption(getattr(msg, 'message', '') or "", sender)
            file_path = await self.file_ops.process_filename(file_path, sender)

            # photo special case
            if media_type == "photo":
                sent = await gf.send_file(target_chat_id, file_path, caption=caption, force_document=False)
                try:
                    await gf.send_file(LOG_GROUP, file_path, caption=caption)
                except:
                    pass
                if edit_msg:
                    try:
                        await edit_msg.delete()
                    except:
                        pass
                return

            # check size
            upload_method = self.db.get_user_data(sender, "upload_method", "SpyLib")  # keep upload_method stored: SpyLib (Telethon) or other
            if file_size and file_size > self.config.SIZE_LIMIT:
                free_check = 0
                if 'chk_user' in globals():
                    try:
                        free_check = await chk_user(chat_id, sender)
                    except:
                        free_check = 0

                if free_check == 1 or not self.pro_client:
                    # split & upload parts
                    if edit_msg:
                        try:
                            await edit_msg.delete()
                        except:
                            pass
                    await self.file_ops.split_large_file(file_path, gf, sender, target_chat_id, caption, topic_id)
                    return
                else:
                    # pro-client 4GB uploader
                    await self.handle_large_file_upload(file_path, sender, edit_msg, caption)
                    return

            # Normal upload using Telethon
            await self.upload_with_telethon(file_path, sender, target_chat_id, caption or "", topic_id, edit_msg)

        except (ChatAdminRequired, RPCError, FloodWait) as e:
            try:
                await gf.send_message(sender, "❌ Access denied or other error. Have you joined the channel or is bot admin?")
            except:
                pass
        except Exception as e:
            print(f"[handle_message_download] {e}")
            try:
                await gf.send_message(LOG_GROUP, f"**Error:** {str(e)}")
            except:
                pass
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except:
                    pass
            gc.collect()

    async def _parse_message_link(self, msg_link: str, offset: int, protected_channels: Set[int], sender: int, edit_id: int) -> Tuple[Optional[int], Optional[int]]:
        try:
            if 't.me/c/' in msg_link or 't.me/b/' in msg_link:
                parts = msg_link.split("/")
                if 't.me/b/' in msg_link:
                    chat_id = parts[-2]
                    msg_id = int(parts[-1]) + offset
                else:
                    chat_id = int('-100' + parts[parts.index('c') + 1])
                    msg_id = int(parts[-1]) + offset

                if chat_id in protected_channels:
                    try:
                        await gf.send_message(sender, "❌ This channel is protected by unknown Gunman.")
                    except:
                        pass
                    return None, None

                return chat_id, msg_id

            elif '/s/' in msg_link:
                # story link
                try:
                    await gf.send_message(sender, "📖 Story Link Detected...")
                except:
                    pass
                parts = msg_link.split("/")
                chat = f"-100{parts[3]}" if parts[3].isdigit() else parts[3]
                msg_id = int(parts[-1])
                # download story via userbot
                await self._download_user_stories(gf, chat, msg_id, sender, edit_id)
                return None, None

            else:
                # public link
                try:
                    await gf.send_message(sender, "🔗 Public link detected...")
                except:
                    pass
                chat = msg_link.split("t.me/")[1].split("/")[0]
                msg_id = int(msg_link.split("/")[-1])
                await self._copy_public_message(gf, gf, sender, chat, msg_id, edit_id)
                return None, None
        except Exception as e:
            print(f"[parse_link] {e}")
            return None, None

    async def _handle_special_messages(self, msg, target_chat_id: int, topic_id: Optional[int], edit_id: int, sender: int) -> bool:
        # WEB_PAGE_PREVIEW handling: Telethon message types differ; fallback on text presence
        if getattr(msg, 'web_page', None):
            text = getattr(msg, 'message', '')
            sent = await gf.send_message(target_chat_id, text)
            try:
                await gf.send_message(LOG_GROUP, text)
            except:
                pass
            try:
                await gf.delete_messages(sender, [edit_id])
            except:
                pass
            return True

        if getattr(msg, 'message', None):
            text = msg.message
            sent = await gf.send_message(target_chat_id, text)
            try:
                await gf.send_message(LOG_GROUP, text)
            except:
                pass
            try:
                await gf.delete_messages(sender, [edit_id])
            except:
                pass
            return True
        return False

    async def _handle_direct_media(self, msg, target_chat_id: int, topic_id: Optional[int], edit_id: int, media_type: str) -> bool:
        try:
            if media_type == "sticker":
                if getattr(msg, 'sticker', None):
                    res = await gf.send_file(target_chat_id, msg.sticker, reply_to=topic_id)
                    try:
                        await gf.send_file(LOG_GROUP, msg.sticker)
                    except:
                        pass
                    await gf.delete_messages(msg.peer_id, [edit_id])
                    return True
            elif media_type == "voice":
                if getattr(msg, 'voice', None):
                    res = await gf.send_file(target_chat_id, msg.voice, reply_to=topic_id)
                    try:
                        await gf.send_file(LOG_GROUP, msg.voice)
                    except:
                        pass
                    await gf.delete_messages(msg.peer_id, [edit_id])
                    return True
            elif media_type == "video_note":
                if getattr(msg, 'video_note', None):
                    res = await gf.send_file(target_chat_id, msg.video_note, reply_to=topic_id)
                    try:
                        await gf.send_file(LOG_GROUP, msg.video_note)
                    except:
                        pass
                    await gf.delete_messages(msg.peer_id, [edit_id])
                    return True
        except Exception as e:
            print(f"[direct_media] {e}")
            return False
        return False

    async def _download_user_stories(self, userbot, chat_id: str, msg_id: int, sender: int, edit_id: int):
        try:
            edit_msg = await gf.send_message(sender, "📖 Downloading Story...")
            story = await userbot.get_stories(chat_id, msg_id)
            if not story or not getattr(story, 'media', None):
                await gf.send_message(sender, "❌ No story available or no media.")
                return
            file_path = await userbot.download_media(story)
            await gf.send_message(sender, "📤 Uploading Story...")
            if getattr(story, 'media', None) == 'video':
                await gf.send_file(sender, file_path)
            elif getattr(story, 'media', None) == 'document':
                await gf.send_file(sender, file_path)
            elif getattr(story, 'media', None) == 'photo':
                await gf.send_file(sender, file_path)
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            await gf.send_message(sender, "✅ Story processed successfully.")
        except RPCError as e:
            await gf.send_message(sender, f"❌ Error: {e}")

    async def _copy_public_message(self, app_client, userbot, sender: int, chat_id: str, message_id: int, edit_id: int):
        target_chat_str = self.user_chat_ids.get(sender, str(sender))
        target_chat_id, topic_id = self.parse_target_chat(target_chat_str)
        file_path = None
        try:
            # try to get message via app_client (gf)
            msg = await app_client.get_messages(chat_id, ids=message_id)
            custom_caption = self.user_caption_prefs.get(str(sender), "")
            final_caption = await self._format_caption_with_custom(getattr(msg, 'message', '') or '', sender, custom_caption)

            if getattr(msg, 'media', None) and not getattr(msg, 'document', None) and not getattr(msg, 'video', None):
                if getattr(msg, 'photo', None):
                    res = await app_client.send_file(target_chat_id, msg.photo, caption=final_caption, reply_to=topic_id)
                elif getattr(msg, 'video', None):
                    res = await app_client.send_file(target_chat_id, msg.video, caption=final_caption, reply_to=topic_id)
                elif getattr(msg, 'document', None):
                    res = await app_client.send_file(target_chat_id, msg.document, caption=final_caption, reply_to=topic_id)
                try:
                    await app_client.send_file(LOG_GROUP, file_path or msg.media, caption=final_caption)
                except:
                    pass
                try:
                    await gf.delete_messages(sender, [edit_id])
                except:
                    pass
                return

            if getattr(msg, 'message', None):
                try:
                    await app_client.forward_messages(target_chat_id, msg, from_peer=chat_id)
                except:
                    # fallback to send message text
                    await app_client.send_message(target_chat_id, msg.message)
                try:
                    await app_client.send_message(LOG_GROUP, msg.message)
                except:
                    pass
                try:
                    await gf.delete_messages(sender, [edit_id])
                except:
                    pass
                return

            # if direct copy failed, use userbot (already userbot is Telethon)
            edit_msg = await gf.send_message(sender, "🔄 Trying alternative method...")
            try:
                await userbot(  # try join chat if required
                    # userbot.join_chat equivalent; Telethon usage might differ
                    # this is best-effort; in most cases public chat need not be joined
                    lambda: None
                )
            except:
                pass

            msg = await userbot.get_messages(chat_id, ids=message_id)
            if not msg or getattr(msg, 'service', False) or getattr(msg, 'empty', False):
                await edit_msg.edit("❌ Message not found or inaccessible")
                return

            if getattr(msg, 'message', None):
                await app_client.send_message(target_chat_id, msg.message)
                try:
                    await edit_msg.delete()
                except:
                    pass
                return

            # download and re-upload
            final_caption = await self._format_caption_with_custom(getattr(msg, 'message', '') if getattr(msg, 'message', None) else "", sender, custom_caption)
            file_path = await userbot.download_media(msg)
            file_path = await self.file_ops.process_filename(file_path, sender)
            filename, file_size, media_type = self.media_processor.get_media_info(msg)

            if media_type == "photo":
                await app_client.send_file(target_chat_id, file_path, caption=final_caption, reply_to=topic_id)
            elif file_size and file_size > self.config.SIZE_LIMIT:
                free_check = 0
                if 'chk_user' in globals():
                    free_check = await chk_user(chat_id, sender)
                if free_check == 1 or not self.pro_client:
                    await edit_msg.delete()
                    await self.file_ops.split_large_file(file_path, app_client, sender, target_chat_id, final_caption, topic_id)
                    return
                else:
                    await self.handle_large_file_upload(file_path, sender, edit_msg, final_caption)
                    return
            else:
                await self.upload_with_telethon(file_path, sender, target_chat_id, final_caption, topic_id, edit_msg)
        except Exception as e:
            print(f"[public_copy] {e}")
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass

    async def _format_caption_with_custom(self, original_caption: str, sender: int, custom_caption: str) -> str:
        delete_words = set(self.db.get_user_data(sender, "delete_words", []))
        replacements = self.db.get_user_data(sender, "replacement_words", {})
        processed = original_caption or ""
        for word in delete_words:
            processed = processed.replace(word, '  ')
        for word, replace_word in replacements.items():
            processed = processed.replace(word, replace_word)
        if custom_caption:
            return f"{processed}\n\n__**{custom_caption}**__" if processed else f"__**{custom_caption}**__"
        return processed

    async def send_settings_panel(self, chat_id: int, user_id: int):
        buttons = [
            [Button.inline("Set Chat ID", b'setchat'), Button.inline("Set Rename Tag", b'setrename')],
            [Button.inline("Caption", b'setcaption'), Button.inline("Replace Words", b'setreplacement')],
            [Button.inline("Remove Words", b'delete'), Button.inline("Reset All", b'reset')],
            [Button.inline("Session Login", b'addsession'), Button.inline("Logout", b'logout')],
            [Button.inline("Set Thumbnail", b'setthumb'), Button.inline("Remove Thumbnail", b'remthumb')],
            [Button.inline("PDF Watermark", b'pdfwt'), Button.inline("Video Watermark", b'watermark')],
            [Button.inline("Upload Method", b'uploadmethod')],
            [Button.url("Report Issues", "https://t.me/rajputserver")]
        ]
        message = (
            "🛠 **Advanced Settings Panel**\n\n"
            "Customize your bot experience with these options:\n"
            "• Configure upload methods\n"
            "• Set custom captions and rename tags\n"
            "• Manage word filters and replacements\n"
            "• Handle thumbnails and watermarks\n\n"
            "Select an option to get started!"
        )
        try:
            await gf.send_file(chat_id, file=self.config.SETTINGS_PIC, caption=message, buttons=buttons)
        except Exception:
            await gf.send_message(chat_id, message)

# create global instance
telegram_bot = SmartTelegramBot()

# ----------------- Telethon event handlers (settings/callbacks) -----------------
@gf.on(events.NewMessage(incoming=True, pattern='/settings'))
async def settings_command_handler(event):
    await telegram_bot.send_settings_panel(event.chat_id, event.sender_id)

@gf.on(events.CallbackQuery)
async def callback_query_handler(event):
    user_id = event.sender_id
    data = event.data
    # Upload method selection
    if data == b'uploadmethod':
        current_method = telegram_bot.db.get_user_data(user_id, "upload_method", "SpyLib")
        pyro_check = " ✅" if current_method == "Pyrogram" else ""
        tele_check = " ✅" if current_method == "Telethon" or current_method == "SpyLib" else ""
        buttons = [
            [Button.inline(f"SpyLib v1 ⚡{tele_check}", b'telethon')]
        ]
        await event.edit(
            "📤 **Choose Upload Method:**\n\n"
            "**SpyLib v1 ⚡:** Advanced features (Telethon)\n",
            buttons=buttons
        )

    elif data == b'telethon':
        telegram_bot.db.save_user_data(user_id, "upload_method", "Telethon")
        await event.edit("✅ Upload method set to **SpyLib v1 ⚡**\n\nThanks for helping test this advanced library!")

    # Session management and other settings
    elif data == b'logout':
        removed = await odb.remove_session(user_id) if hasattr(odb, 'remove_session') else None
        message = "✅ Logged out successfully!" if removed else "❌ You are not logged in."
        await event.respond(message)

    elif data == b'addsession':
        telegram_bot.user_sessions[user_id] = 'addsession'
        await event.respond("🔑 **Session Login**\n\nSend your Telethon session string:")

    elif data == b'setchat':
        telegram_bot.user_sessions[user_id] = 'setchat'
        await event.respond("💬 **Set Target Chat**\n\nSend the chat ID where files should be sent:")

    elif data == b'setrename':
        telegram_bot.user_sessions[user_id] = 'setrename'
        await event.respond("🏷 **Set Rename Tag**\n\nSend the tag to append to filenames:")

    elif data == b'setcaption':
        telegram_bot.user_sessions[user_id] = 'setcaption'
        await event.respond("📝 **Set Custom Caption**\n\nSend the caption to add to all files:")

    elif data == b'setreplacement':
        telegram_bot.user_sessions[user_id] = 'setreplacement'
        await event.respond(
            "🔄 **Word Replacement**\n\nSend replacement rules in format:\n"
            "`'OLD_WORD' 'NEW_WORD'`\n\nExample: `'sample' 'example'`"
        )

    elif data == b'delete':
        telegram_bot.user_sessions[user_id] = 'deleteword'
        await event.respond(
            "🗑 **Delete Words**\n\n"
            "Send words separated by spaces to remove them from captions/filenames:"
        )

    elif data == b'setthumb':
        telegram_bot.pending_photos.add(user_id)
        await event.respond("🖼 **Set Thumbnail**\n\nSend a photo to use as thumbnail for videos:")

    elif data == b'remthumb':
        thumb_path = f'{user_id}.jpg'
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
            await event.respond('✅ Thumbnail removed successfully!')
        else:
            await event.respond("❌ No thumbnail found to remove.")

    elif data == b'pdfwt':
        await event.respond("🚧 **PDF Watermark**\n\nThis feature is under development...")

    elif data == b'watermark':
        await event.respond("🚧 **Video Watermark**\n\nThis feature is under development...")

    elif data == b'reset':
        try:
            success = telegram_bot.db.reset_user_data(user_id)
            telegram_bot.user_chat_ids.pop(user_id, None)
            telegram_bot.user_rename_prefs.pop(str(user_id), None)
            telegram_bot.user_caption_prefs.pop(str(user_id), None)
            thumb_path = f"{user_id}.jpg"
            if os.path.exists(thumb_path):
                os.remove(thumb_path)
            if success:
                await event.respond("✅ All settings reset successfully!\n\nUse /logout to remove session.")
            else:
                await event.respond("❌ Error occurred while resetting settings.")
        except Exception as e:
            await event.respond(f"❌ Reset failed: {e}")

@gf.on(events.NewMessage(func=lambda e: e.sender_id in telegram_bot.pending_photos))
async def thumbnail_handler(event):
    user_id = event.sender_id
    if event.photo:
        temp_path = await event.download_media()
        thumb_path = f'{user_id}.jpg'
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
        os.rename(temp_path, f'./{user_id}.jpg')
        await event.respond('✅ Thumbnail saved successfully!')
    else:
        await event.respond('❌ Please send a photo. Try again.')
    telegram_bot.pending_photos.discard(user_id)

@gf.on(events.NewMessage)
async def user_input_handler(event):
    user_id = event.sender_id
    if user_id in telegram_bot.user_sessions:
        session_type = telegram_bot.user_sessions[user_id]
        if session_type == 'setchat':
            try:
                chat_id = int(event.raw_text.strip())
                telegram_bot.user_chat_ids[user_id] = chat_id
                telegram_bot.db.save_user_data(user_id, "target_chat_id", chat_id)
                await event.respond(f"✅ Target chat set to: `{chat_id}`")
            except Exception:
                await event.respond("❌ Invalid chat ID format!")
        elif session_type == 'setrename':
            rename_tag = event.raw_text.strip()
            telegram_bot.user_rename_prefs[str(user_id)] = rename_tag
            telegram_bot.db.save_user_data(user_id, "rename_tag", rename_tag)
            await event.respond(f"✅ Rename tag set to: **{rename_tag}**")
        elif session_type == 'setcaption':
            custom_caption = event.raw_text.strip()
            telegram_bot.user_caption_prefs[str(user_id)] = custom_caption
            telegram_bot.db.save_user_data(user_id, "custom_caption", custom_caption)
            await event.respond(f"✅ Custom caption set to:\n\n**{custom_caption}**")
        elif session_type == 'setreplacement':
            match = re.match(r"'(.+)' '(.+)'", event.raw_text)
            if not match:
                await event.respond("❌ **Invalid format!**\n\nUse: `'OLD_WORD' 'NEW_WORD'`")
            else:
                old_word, new_word = match.groups()
                delete_words = set(telegram_bot.db.get_user_data(user_id, "delete_words", []))
                if old_word in delete_words:
                    await event.respond(f"❌ '{old_word}' is in delete list and cannot be replaced.")
                else:
                    replacements = telegram_bot.db.get_user_data(user_id, "replacement_words", {})
                    replacements[old_word] = new_word
                    telegram_bot.db.save_user_data(user_id, "replacement_words", replacements)
                    await event.respond(f"✅ Replacement saved:\n**'{old_word}' → '{new_word}'**")
        elif session_type == 'addsession':
            session_string = event.raw_text.strip()
            # store session via odb if available
            if hasattr(odb, 'set_session'):
                await odb.set_session(user_id, session_string)
            else:
                # store locally if odb not available
                telegram_bot.db.save_user_data(user_id, "session_string", session_string)
            await event.respond("✅ Session string added successfully!")
        elif session_type == 'deleteword':
            words_to_delete = event.message.text.split()
            delete_words = set(telegram_bot.db.get_user_data(user_id, "delete_words", []))
            delete_words.update(words_to_delete)
            telegram_bot.db.save_user_data(user_id, "delete_words", list(delete_words))
            await event.respond(f"✅ Words added to delete list:\n**{', '.join(words_to_delete)}**")
        # Clear session
        del telegram_bot.user_sessions[user_id]

# ----------------- Module-level get_msg wrapper -----------------
async def get_msg(userbot, sender, edit_id, msg_link, i, message):
    await telegram_bot.handle_message_download(userbot, sender, edit_id, msg_link, i, message)

print("✅ Smart Telegram Bot (Telethon-only) initialized successfully!")
print(f"   • Database: {'✅' if telegram_bot.db else '❌'}")
print(f"   • Pro Client (4GB): {'✅' if telegram_bot.pro_client else '❌'}")
print(f"   • Userbot (gf): {'✅' if gf else '❌'}")
