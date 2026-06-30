#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
نسخه‌ی ۱۴ - نهایی و پایدار
با رفع کامل خطاهای parse_mode، message.bot و Conflict
"""

import os
import sys
import gc
import logging
import asyncio
import time
import random
import traceback
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set, Tuple
from functools import lru_cache, wraps
from collections import defaultdict, deque
from contextlib import asynccontextmanager

# ============================================================
# 📋 تنظیمات از متغیرهای محیطی
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("❌ BOT_TOKEN تنظیم نشده!")
    sys.exit(1)

ALLOWED_USERS_STR = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = [int(x.strip()) for x in ALLOWED_USERS_STR.split(",") if x.strip()] if ALLOWED_USERS_STR else []

TARGET_GROUPS_STR = os.getenv("TARGET_GROUPS", "")
TARGET_GROUPS = [int(x.strip()) for x in TARGET_GROUPS_STR.split(",") if x.strip()] if TARGET_GROUPS_STR else []

OWNER_ID = ALLOWED_USERS[0] if ALLOWED_USERS else None

USE_PROXY = os.getenv("USE_PROXY", "false").lower() == "true"
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:1080")
DELETE_AFTER_FORWARD = os.getenv("DELETE_AFTER_FORWARD", "true").lower() == "true"
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "1"))
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT", "15"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "5"))  # 🔥 کاهش داده شده برای جلوگیری از Conflict
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "200"))
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "100"))

# ============================================================
# 📝 راه‌اندازی لاگ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

error_logger = logging.getLogger("errors")
error_logger.setLevel(logging.ERROR)

# ============================================================
# 📦 ایمپورت کتابخانه‌ها
# ============================================================

try:
    from telegram import Update, Message, InputMediaPhoto, InputMediaVideo, User, error
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    from telegram.request import HTTPXRequest
except ImportError as e:
    logger.error(f"❌ کتابخانه نصب نیست: {e}")
    sys.exit(1)

# ============================================================
# 📊 کلاس مدیریت تاریخچه
# ============================================================

class UserHistory:
    def __init__(self, max_size: int = 100):
        self.max_size = max_size
        self.history = defaultdict(lambda: deque(maxlen=max_size))
        self.user_info = {}
        self.message_count = defaultdict(int)
        self.type_counts = defaultdict(lambda: defaultdict(int))
        self.last_active = {}
        self.first_seen = {}
        self._lock = asyncio.Lock()
        self._total_messages = 0
    
    async def add_message(self, user_id: int, message_text: str, message_type: str, 
                         timestamp: datetime, user_obj: User = None):
        try:
            async with self._lock:
                self.history[user_id].append({
                    "text": message_text[:200],
                    "type": message_type,
                    "time": timestamp.strftime("%H:%M:%S"),
                    "date": timestamp.strftime("%Y-%m-%d")
                })
                self.message_count[user_id] += 1
                self.type_counts[user_id][message_type] += 1
                self.last_active[user_id] = timestamp
                self._total_messages += 1
                
                if user_id not in self.first_seen:
                    self.first_seen[user_id] = timestamp
                
                if user_obj and (user_id not in self.user_info or 
                               self.user_info[user_id].get('username') is None):
                    self.user_info[user_id] = {
                        "username": user_obj.username,
                        "first_name": user_obj.first_name,
                        "last_name": user_obj.last_name,
                        "is_bot": user_obj.is_bot,
                        "language_code": user_obj.language_code
                    }
                
                if self._total_messages % 100 == 0:
                    gc.collect()
        except Exception as e:
            error_logger.error(f"خطا در add_message: {e}\n{traceback.format_exc()}")
    
    def get_user_details(self, user_id: int) -> Optional[Dict]:
        try:
            if user_id not in self.first_seen:
                return None
            total = self.message_count.get(user_id, 0)
            last = self.last_active.get(user_id)
            first = self.first_seen.get(user_id)
            info = self.user_info.get(user_id, {})
            type_breakdown = dict(self.type_counts.get(user_id, {}))
            avg_per_day = 0
            if first and last and total > 0:
                days = (last - first).days + 1
                avg_per_day = total / days if days > 0 else total
            return {
                "user_id": user_id,
                "username": info.get("username", "ندارد"),
                "first_name": info.get("first_name", "نامشخص"),
                "last_name": info.get("last_name", ""),
                "is_bot": info.get("is_bot", False),
                "language_code": info.get("language_code", "نامشخص"),
                "first_seen": first,
                "last_seen": last,
                "total_messages": total,
                "type_breakdown": type_breakdown,
                "avg_per_day": round(avg_per_day, 1),
                "is_owner": user_id == OWNER_ID,
                "is_allowed": user_id in ALLOWED_USERS
            }
        except Exception as e:
            error_logger.error(f"خطا در get_user_details: {e}")
            return None
    
    def get_history(self, user_id: int, limit: int = 15) -> List[Dict]:
        try:
            return list(self.history[user_id])[-limit:]
        except Exception:
            return []
    
    def get_all_users(self) -> List[int]:
        try:
            return list(self.history.keys())
        except Exception:
            return []
    
    def get_stats(self) -> Dict:
        try:
            total_messages = self._total_messages
            total_users = len(self.history)
            active_today = sum(1 for uid, last in self.last_active.items() 
                              if last.date() == datetime.now().date())
            most_active = None
            most_count = 0
            for uid, count in self.message_count.items():
                if count > most_count:
                    most_count = count
                    most_active = uid
            return {
                "total_users": total_users,
                "total_messages": total_messages,
                "active_today": active_today,
                "most_active_user": most_active,
                "most_active_count": most_count,
                "total_types": sum(len(v) for v in self.type_counts.values())
            }
        except Exception:
            return {"total_users": 0, "total_messages": 0, "active_today": 0, 
                    "most_active_user": None, "most_active_count": 0, "total_types": 0}

user_history = UserHistory(max_size=MAX_HISTORY)

# ============================================================
# 🛠 کلاس‌های پیشرفته
# ============================================================

class TokenBucket:
    def __init__(self, rate: int, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self.lock = asyncio.Lock()
    
    async def acquire(self, tokens: int = 1) -> bool:
        try:
            async with self.lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_refill = now
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True
                return False
        except Exception:
            return False

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = defaultdict(int)
        self.last_failure_time = defaultdict(float)
        self.state = defaultdict(lambda: "CLOSED")
        self._lock = asyncio.Lock()
    
    async def record_failure(self, group_id: int):
        try:
            async with self._lock:
                self.failures[group_id] += 1
                self.last_failure_time[group_id] = time.monotonic()
                if self.failures[group_id] >= self.failure_threshold:
                    self.state[group_id] = "OPEN"
                    logger.warning(f"⛔ Circuit Breaker OPEN for {group_id}")
        except Exception:
            pass
    
    async def record_success(self, group_id: int):
        try:
            async with self._lock:
                self.failures[group_id] = 0
                self.state[group_id] = "CLOSED"
        except Exception:
            pass
    
    async def is_allowed(self, group_id: int) -> bool:
        try:
            async with self._lock:
                if self.state[group_id] == "CLOSED":
                    return True
                if self.state[group_id] == "OPEN":
                    if time.monotonic() - self.last_failure_time[group_id] > self.timeout:
                        self.state[group_id] = "HALF_OPEN"
                        return True
                    return False
                if self.state[group_id] == "HALF_OPEN":
                    return True
                return True
        except Exception:
            return True

class RequestQueue:
    def __init__(self, max_concurrent: int = 5, max_queue: int = 200):
        self.max_concurrent = max_concurrent
        self.max_queue = max_queue
        self.queue = asyncio.Queue(maxsize=max_queue)
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._workers = set()
        self._running = True
    
    async def start_workers(self, worker_count: int = 3):
        for _ in range(worker_count):
            task = asyncio.create_task(self._worker_loop())
            self._workers.add(task)
    
    async def _worker_loop(self):
        while self._running:
            try:
                task = await self.queue.get()
                try:
                    async with self.semaphore:
                        await task()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    error_logger.error(f"⚠️ Worker task error: {e}")
                finally:
                    self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                pass
    
    async def add_task(self, coro):
        if self.queue.qsize() >= self.max_queue:
            raise asyncio.QueueFull("صف پر است!")
        await self.queue.put(coro)
    
    def stop(self):
        self._running = False
        for task in self._workers:
            try:
                task.cancel()
            except:
                pass

# ============================================================
# نمونه‌های سراسری
# ============================================================

rate_limiter = TokenBucket(rate=10, capacity=20)
circuit_breaker = CircuitBreaker(failure_threshold=2, timeout=30)
request_queue = RequestQueue(max_concurrent=MAX_CONCURRENT, max_queue=MAX_QUEUE_SIZE)

# ============================================================
# 🛠 توابع کمکی
# ============================================================

def is_allowed(user_id: int) -> bool:
    try:
        return user_id in ALLOWED_USERS
    except:
        return False

def is_owner(user_id: int) -> bool:
    try:
        return user_id == OWNER_ID
    except:
        return False

def get_filters():
    try:
        return filters.ChatType.PRIVATE & ~filters.COMMAND
    except AttributeError:
        try:
            return filters.PRIVATE & ~filters.COMMAND
        except AttributeError:
            return filters.ALL

def build_application():
    try:
        request_kwargs = {
            "connect_timeout": CONNECTION_TIMEOUT,
            "read_timeout": CONNECTION_TIMEOUT,
            "write_timeout": CONNECTION_TIMEOUT,
            "pool_timeout": CONNECTION_TIMEOUT,
        }
        if USE_PROXY and PROXY_URL:
            request_kwargs["proxy_url"] = PROXY_URL
            logger.info(f"✅ Proxy enabled: {PROXY_URL}")
        request = HTTPXRequest(**request_kwargs)
        return Application.builder().token(BOT_TOKEN).request(request).build()
    except Exception as e:
        error_logger.error(f"❌ خطا در build_application: {e}")
        sys.exit(1)

def get_message_type(message: Message) -> Optional[str]:
    try:
        if message.text:
            return "text"
        if message.photo:
            return "photo"
        if message.video:
            return "video"
        if message.animation:
            return "animation"
        if message.sticker:
            return "sticker"
        if message.voice:
            return "voice"
        if message.audio:
            return "audio"
        if message.document:
            return "document"
        if message.video_note:
            return "video_note"
        if message.location:
            return "location"
        if message.contact:
            return "contact"
        if message.dice:
            return "dice"
        if message.poll:
            return "poll"
        return None
    except Exception:
        return None

def get_file_id(message: Message) -> Optional[str]:
    try:
        if message.photo:
            return message.photo[-1].file_id
        for attr in ['video', 'animation', 'sticker', 'voice', 'audio', 'document', 'video_note']:
            obj = getattr(message, attr, None)
            if obj:
                return obj.file_id
        return None
    except Exception:
        return None

def is_retryable_error(e: Exception) -> bool:
    try:
        if isinstance(e, error.TelegramError):
            error_str = str(e).lower()
            permanent_errors = [
                "chat not found", "bot was blocked", "bot is not a member",
                "user not found", "message not found",
                "bad request: chat not found", "group not found"
            ]
            for perm_error in permanent_errors:
                if perm_error in error_str:
                    return False
        return True
    except:
        return True

def split_message(text: str, max_length: int = 4000) -> List[str]:
    try:
        if not text:
            return []
        if len(text) <= max_length:
            return [text]
        parts = []
        while text:
            if len(text) <= max_length:
                parts.append(text)
                break
            split_at = text.rfind('\n', 0, max_length)
            if split_at == -1:
                split_at = text.rfind(' ', 0, max_length)
            if split_at == -1:
                split_at = max_length
            parts.append(text[:split_at])
            text = text[split_at:].strip()
        return parts
    except Exception:
        return [text[:max_length]] if text else []

# ============================================================
# ارسال آلبوم
# ============================================================

class AlbumBuffer:
    def __init__(self, delay: float = 0.7):
        self.delay = delay
        self.buffer = {}
        self.timers = {}
        self.lock = asyncio.Lock()
    
    async def add_message(self, message: Message, bot) -> bool:
        try:
            mgid = message.media_group_id
            if not mgid or not bot:
                return False
            async with self.lock:
                if mgid not in self.buffer:
                    self.buffer[mgid] = []
                    self.timers[mgid] = asyncio.create_task(self._flush(mgid, bot))
                self.buffer[mgid].append(message)
                return True
        except Exception as e:
            error_logger.error(f"خطا در album_buffer.add_message: {e}")
            return False
    
    async def _flush(self, mgid: str, bot):
        try:
            await asyncio.sleep(self.delay)
            async with self.lock:
                messages = self.buffer.pop(mgid, [])
                self.timers.pop(mgid, None)
            if not messages:
                return
            
            for gid in TARGET_GROUPS:
                try:
                    await send_album_group(messages, gid, bot)
                except Exception as e:
                    error_logger.error(f"خطا در ارسال آلبوم به {gid}: {e}")
            
            if DELETE_AFTER_FORWARD:
                for msg in messages:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
            
            try:
                await messages[0].reply_text("📸 آلبوم ارسال شد.")
            except Exception:
                pass
        except Exception as e:
            error_logger.error(f"خطا در album_buffer._flush: {e}")
            # پاکسازی بافر در صورت خطا
            async with self.lock:
                self.buffer.pop(mgid, None)
                self.timers.pop(mgid, None)

album_buffer = AlbumBuffer(delay=0.7)

async def send_album_group(messages: List[Message], chat_id: int, bot) -> bool:
    try:
        if not messages or not bot:
            return False
        
        # حداکثر ۱۰ رسانه در یک آلبوم
        if len(messages) > 10:
            # ارسال به صورت جداگانه
            success = True
            for msg in messages:
                # استفاده از send_single_message برای هر کدام
                if not await send_single_message(msg, chat_id, bot):
                    success = False
            return success
        
        media_group = []
        for idx, msg in enumerate(messages):
            caption = msg.caption if idx == 0 else None  # فقط برای اولین
            caption_entities = msg.caption_entities if idx == 0 else None
            
            if msg.photo:
                media_group.append(InputMediaPhoto(
                    msg.photo[-1].file_id,
                    caption=caption,
                    caption_entities=caption_entities
                ))
            elif msg.video:
                media_group.append(InputMediaVideo(
                    msg.video.file_id,
                    caption=caption,
                    caption_entities=caption_entities
                ))
            else:
                # اگر نوع دیگر بود، به صورت جداگانه ارسال کن
                if not await send_single_message(msg, chat_id, bot):
                    return False
                continue
        
        if media_group:
            await bot.send_media_group(chat_id=chat_id, media=media_group)
            return True
        
        return False
    except Exception as e:
        error_logger.error(f"خطا در send_album_group به {chat_id}: {e}")
        # در صورت خطا، به صورت جداگانه ارسال کن (Fallback)
        success = True
        for msg in messages:
            if not await send_single_message(msg, chat_id, bot):
                success = False
        return success

# ============================================================
# ارسال پیام (بدون استفاده از message.bot)
# ============================================================

async def send_single_message(message: Message, chat_id: int, bot) -> bool:
    try:
        if not await rate_limiter.acquire():
            await asyncio.sleep(0.05)
            return await send_single_message(message, chat_id, bot)
        
        if not await circuit_breaker.is_allowed(chat_id):
            return False
        
        msg_type = get_message_type(message)
        if not msg_type:
            return False
        
        if message.media_group_id:
            return False
        
        # ========== متن طولانی ==========
        if msg_type == "text":
            text = message.text
            if len(text) > 4096:
                # تقسیم به قطعات
                for i in range(0, len(text), 4096):
                    part = text[i:i+4096]
                    await bot.send_message(
                        chat_id=chat_id,
                        text=part,
                        entities=message.entities,
                        disable_web_page_preview=True
                    )
                await circuit_breaker.record_success(chat_id)
                return True
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    entities=message.entities,
                    disable_web_page_preview=True
                )
                await circuit_breaker.record_success(chat_id)
                return True
        
        # ========== نظرسنجی ==========
        if msg_type == "poll":
            if not message.poll.options:
                return False
            # فقط پارامترهای اجباری
            poll_params = {
                "chat_id": chat_id,
                "question": message.poll.question,
                "options": [opt.text for opt in message.poll.options],
                "is_anonymous": message.poll.is_anonymous,
                "type": message.poll.type,
                "allows_multiple_answers": message.poll.allows_multiple_answers,
            }
            # اضافه کردن پارامترهای اختیاری در صورت وجود
            if message.poll.correct_option_id is not None:
                poll_params["correct_option_id"] = message.poll.correct_option_id
            if message.poll.explanation:
                poll_params["explanation"] = message.poll.explanation
                poll_params["explanation_parse_mode"] = message.poll.explanation_parse_mode
            await bot.send_poll(**poll_params)
            await circuit_breaker.record_success(chat_id)
            return True
        
        # ========== مکان ==========
        if msg_type == "location":
            await bot.send_location(
                chat_id=chat_id,
                latitude=message.location.latitude,
                longitude=message.location.longitude
            )
            await circuit_breaker.record_success(chat_id)
            return True
        
        # ========== مخاطب ==========
        if msg_type == "contact":
            await bot.send_contact(
                chat_id=chat_id,
                phone_number=message.contact.phone_number,
                first_name=message.contact.first_name,
                last_name=message.contact.last_name
            )
            await circuit_breaker.record_success(chat_id)
            return True
        
        # ========== تاس ==========
        if msg_type == "dice":
            await bot.send_dice(
                chat_id=chat_id,
                emoji=message.dice.emoji
            )
            await circuit_breaker.record_success(chat_id)
            return True
        
        # ========== انواع با file_id ==========
        file_id = get_file_id(message)
        if not file_id:
            return False
        
        common_params = {
            "chat_id": chat_id,
            "caption": message.caption,
            "caption_entities": message.caption_entities,
        }
        
        method_map = {
            "photo": {"method": "send_photo", "param": "photo"},
            "video": {"method": "send_video", "param": "video", "extra": {"supports_streaming": True}},
            "animation": {"method": "send_animation", "param": "animation"},
            "sticker": {"method": "send_sticker", "param": "sticker"},   # استیکر
            "voice": {"method": "send_voice", "param": "voice"},
            "audio": {"method": "send_audio", "param": "audio"},
            "document": {"method": "send_document", "param": "document"},
            "video_note": {"method": "send_video_note", "param": "video_note"},
        }
        
        config = method_map.get(msg_type)
        if not config:
            return False
        
        method_name = config["method"]
        method = getattr(bot, method_name)
        params = {config["param"]: file_id, **common_params}
        if "extra" in config:
            params.update(config["extra"])
        
        # حذف caption برای استیکر و video_note (بعضی از انواع پشتیبانی نمی‌کنند)
        if msg_type in ["sticker", "video_note"]:
            params.pop("caption", None)
            params.pop("caption_entities", None)
        
        await method(**params)
        await circuit_breaker.record_success(chat_id)
        return True
        
    except Exception as e:
        if is_retryable_error(e):
            logger.warning(f"⚠️ Retryable error to {chat_id}: {e}")
        else:
            error_logger.error(f"❌ Permanent error to {chat_id}: {e}")
            await circuit_breaker.record_failure(chat_id)
        return False

async def send_with_retry(message: Message, chat_id: int, bot) -> bool:
    base_delay = RETRY_DELAY
    for attempt in range(MAX_RETRIES + 1):
        try:
            # اگر آلبوم است از تابع مخصوص استفاده کن
            if message.media_group_id:
                # اما ما آلبوم را در forward_handler مدیریت می‌کنیم، اینجا فقط تکی می‌رسد
                pass
            success = await send_single_message(message, chat_id, bot)
            if success:
                return True
        except Exception as e:
            error_logger.error(f"خطا در send_with_retry (تلاش {attempt+1}): {e}")
        if attempt < MAX_RETRIES:
            jitter = random.uniform(0, 0.5)
            delay = (base_delay * (2 ** attempt)) + jitter
            await asyncio.sleep(delay)
    return False

# ============================================================
# 📌 کامندها
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_allowed(user_id):
            return
        await update.message.reply_text(
            "🤖 **بات فورواردر ناشناس v14**\n\n"
            "✅ **پایدار و نهایی**\n"
            "⚡ پردازش همزمان\n"
            "📸 پشتیبانی از آلبوم\n"
            "🛡️ مدیریت خطای کامل\n\n"
            "📋 برای راهنما: /help",
            parse_mode="Markdown"
        )
    except Exception as e:
        error_logger.error(f"خطا در start: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_allowed(user_id):
            return
        help_text = (
            "📖 **راهنمای کامل**\n\n"
            "**📤 ارسال پیام:**\n"
            "هر پیامی به صورت ناشناس ارسال می‌شود.\n\n"
            "**📸 آلبوم:**\n"
            "چند عکس/ویدئو همزمان.\n\n"
            "**📋 کامندهای عمومی:**\n"
            "/start - خوش‌آمدگویی\n"
            "/help - راهنما\n"
            "/getid - دریافت آیدی\n"
            "/ping - تست سلامت\n"
            "/health - بررسی عمیق سلامت\n"
            "/status - وضعیت عمومی\n"
            "/mystats - آمار شخصی\n"
            "/about - اطلاعات بات\n\n"
        )
        if is_owner(user_id):
            help_text += (
                "**🔐 کامندهای مدیریتی (فقط مدیر):**\n"
                "/userinfo [USER_ID] - اطلاعات کامل کاربر\n"
                "/history [USER_ID] - تاریخچه پیام‌ها\n"
                "/users - لیست کاربران فعال\n"
                "/stats - آمار کلی\n\n"
            )
        help_text += (
            f"👥 **کاربران مجاز:** {len(ALLOWED_USERS)}\n"
            f"📢 **گروه‌های مقصد:** {len(TARGET_GROUPS)}\n"
            f"🗑️ **حذف پیام:** {'فعال' if DELETE_AFTER_FORWARD else 'غیرفعال'}"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
    except Exception as e:
        error_logger.error(f"خطا در help_command: {e}")

async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_allowed(user_id):
            return
        await update.message.reply_text(
            f"🆔 **آیدی شما:** `{update.effective_user.id}`\n"
            f"🆔 **آیدی این چت:** `{update.effective_chat.id}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        error_logger.error(f"خطا در getid: {e}")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_allowed(user_id):
            return
        start = datetime.now()
        await update.message.reply_text("🏓 پنگ...")
        latency = (datetime.now() - start).total_seconds() * 1000
        await update.message.reply_text(
            f"✅ **بات سالم است!**\n"
            f"⏱️ زمان پاسخ: `{latency:.0f}ms`\n"
            f"📅 زمان سرور: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode="Markdown"
        )
    except Exception as e:
        error_logger.error(f"خطا در ping: {e}")

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_owner(user_id):
            return
        
        queue_size = request_queue.queue.qsize()
        total_users = len(user_history.get_all_users())
        stats = user_history.get_stats()
        total_messages = stats.get('total_messages', 0)
        
        health_text = (
            "🩺 **بررسی عمیق سلامت بات**\n\n"
            f"✅ **وضعیت کلی:** {'فعال' if request_queue._running else 'متوقف'}\n"
            f"📊 **صف درخواست‌ها:** {queue_size} (حداکثر {MAX_QUEUE_SIZE})\n"
            f"👥 **کاربران فعال:** {total_users}\n"
            f"📨 **مجموع پیام‌ها:** {total_messages}\n"
            f"⚡ **همزمانی:** {MAX_CONCURRENT}\n"
            f"🔄 **تلاش مجدد:** {MAX_RETRIES}\n"
            f"🗑️ **حذف پیام:** {'فعال' if DELETE_AFTER_FORWARD else 'غیرفعال'}\n"
            f"📅 **زمان اجرا:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await update.message.reply_text(health_text, parse_mode="Markdown")
    except Exception as e:
        error_logger.error(f"خطا در health: {e}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_allowed(user_id):
            return
        status_text = (
            "📊 **وضعیت عمومی بات**\n\n"
            f"✅ وضعیت: **فعال**\n"
            f"👥 کاربران مجاز: {len(ALLOWED_USERS)}\n"
            f"📢 گروه‌های مقصد: {len(TARGET_GROUPS)}\n"
            f"🗑️ حذف پیام: {'فعال' if DELETE_AFTER_FORWARD else 'غیرفعال'}\n"
            f"⚡ همزمانی: {MAX_CONCURRENT}\n"
            f"🔄 تلاش مجدد: {MAX_RETRIES}\n"
            f"📅 زمان: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await update.message.reply_text(status_text, parse_mode="Markdown")
    except Exception as e:
        error_logger.error(f"خطا در status: {e}")

async def mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_allowed(user_id):
            return
        details = user_history.get_user_details(user_id)
        if not details:
            await update.message.reply_text(
                "📊 **آمار شخصی شما**\n\n"
                "هنوز هیچ پیامی ارسال نکرده‌اید."
            )
            return
        msg = f"📊 **آمار شخصی شما**\n\n"
        msg += f"📨 **مجموع پیام‌ها:** {details['total_messages']}\n"
        msg += f"📈 **میانگین روزانه:** {details['avg_per_day']} پیام\n"
        if details['first_seen']:
            msg += f"📅 **اولین فعالیت:** {details['first_seen'].strftime('%Y-%m-%d')}\n"
        if details['last_seen']:
            msg += f"🕒 **آخرین فعالیت:** {details['last_seen'].strftime('%Y-%m-%d %H:%M')}\n"
        if details['type_breakdown']:
            msg += f"\n📋 **تفکیک بر اساس نوع:**\n"
            emoji_map = {
                "text": "📝", "photo": "🖼️", "video": "🎥", "animation": "🎬",
                "sticker": "🏷️", "voice": "🎤", "audio": "🎵", "document": "📄",
                "video_note": "🔄", "location": "📍", "contact": "📇", "dice": "🎲",
                "poll": "📊"
            }
            for msg_type, count in sorted(details['type_breakdown'].items(), key=lambda x: x[1], reverse=True):
                emoji = emoji_map.get(msg_type, "📨")
                msg += f"   {emoji} {msg_type}: {count}\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        error_logger.error(f"خطا در mystats: {e}")

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_allowed(user_id):
            return
        await update.message.reply_text(
            "ℹ️ **درباره بات**\n\n"
            "🤖 **نام:** بات فورواردر ناشناس\n"
            "📌 **نسخه:** 14.0 (نهایی و پایدار)\n"
            "⚡ **ویژگی‌ها:**\n"
            "   • مدیریت کامل خطا\n"
            "   • بازیابی خودکار\n"
            "   • لاگ‌نویسی دقیق\n"
            "   • پشتیبانی از آلبوم\n"
            f"👑 **مدیر بات:** `{OWNER_ID}`\n"
            f"📢 **تعداد گروه‌ها:** {len(TARGET_GROUPS)}",
            parse_mode="Markdown"
        )
    except Exception as e:
        error_logger.error(f"خطا در about: {e}")

# ============================================================
# 📌 کامندهای مدیریتی
# ============================================================

async def userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_owner(user_id):
            await update.message.reply_text("⛔ این کامند فقط برای مدیر بات است.")
            return
        
        args = context.args
        target_user_id = user_id
        
        if args:
            try:
                target_user_id = int(args[0])
            except ValueError:
                await update.message.reply_text(
                    "❌ **خطا در ورودی**\n\nآیدی باید عددی باشد.",
                    parse_mode="Markdown"
                )
                return
        
        details = user_history.get_user_details(target_user_id)
        if not details:
            await update.message.reply_text(
                f"❌ **کاربر یافت نشد**\n\nهیچ اطلاعاتی از کاربر `{target_user_id}` یافت نشد.",
                parse_mode="Markdown"
            )
            return
        
        msg = f"👤 **اطلاعات کاربر**\n\n"
        msg += f"🆔 **آیدی:** `{details['user_id']}`\n"
        name = details['first_name']
        if details['last_name']:
            name += f" {details['last_name']}"
        msg += f"📛 **نام:** {name}\n"
        if details['username'] and details['username'] != "ندارد":
            msg += f"🔗 **یوزرنیم:** @{details['username']}\n"
        msg += f"🤖 **ربات:** {'بله' if details['is_bot'] else 'خیر'}\n"
        msg += f"🌐 **زبان:** {details['language_code']}\n"
        role = "کاربر عادی"
        if details['is_owner']:
            role = "👑 **مدیر بات**"
        elif details['is_allowed']:
            role = "✅ کاربر مجاز"
        else:
            role = "⛔ کاربر غیرمجاز"
        msg += f"👤 **نقش:** {role}\n"
        if details['first_seen']:
            msg += f"📅 **اولین فعالیت:** {details['first_seen'].strftime('%Y-%m-%d %H:%M:%S')}\n"
        if details['last_seen']:
            msg += f"🕒 **آخرین فعالیت:** {details['last_seen'].strftime('%Y-%m-%d %H:%M:%S')}\n"
        msg += f"\n📊 **آمار پیام‌ها**\n"
        msg += f"📨 **مجموع:** {details['total_messages']} پیام\n"
        msg += f"📈 **میانگین روزانه:** {details['avg_per_day']} پیام\n"
        if details['type_breakdown']:
            msg += f"\n📋 **تفکیک بر اساس نوع:**\n"
            emoji_map = {
                "text": "📝", "photo": "🖼️", "video": "🎥", "animation": "🎬",
                "sticker": "🏷️", "voice": "🎤", "audio": "🎵", "document": "📄",
                "video_note": "🔄", "location": "📍", "contact": "📇", "dice": "🎲",
                "poll": "📊"
            }
            for msg_type, count in sorted(details['type_breakdown'].items(), key=lambda x: x[1], reverse=True):
                emoji = emoji_map.get(msg_type, "📨")
                msg += f"   {emoji} {msg_type}: {count}\n"
        
        for part in split_message(msg):
            await update.message.reply_text(part, parse_mode="Markdown")
    except Exception as e:
        error_logger.error(f"خطا در userinfo: {e}")

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_owner(user_id):
            await update.message.reply_text("⛔ این کامند فقط برای مدیر بات است.")
            return
        
        args = context.args
        if not args:
            await update.message.reply_text(
                "📖 **راهنمای /history**\n\nبرای مشاهده تاریخچه یک کاربر، آیدی او را وارد کنید:\n`/history 123456789`",
                parse_mode="Markdown"
            )
            return
        
        try:
            target_user_id = int(args[0])
        except ValueError:
            await update.message.reply_text("❌ آیدی باید عددی باشد.")
            return
        
        history_data = user_history.get_history(target_user_id, limit=15)
        if not history_data:
            await update.message.reply_text(
                f"❌ **تاریخچه یافت نشد**\n\nهیچ پیامی از کاربر `{target_user_id}` یافت نشد.",
                parse_mode="Markdown"
            )
            return
        
        details = user_history.get_user_details(target_user_id)
        name = "نامشخص"
        if details:
            name = details['first_name']
            if details['username'] and details['username'] != "ندارد":
                name = f"@{details['username']}"
        
        msg = f"📋 **تاریخچه {name}** (`{target_user_id}`)\n\n"
        for i, entry in enumerate(history_data, 1):
            msg += f"{i}. [{entry['date']} {entry['time']}] **{entry['type']}**\n"
            if entry['text']:
                msg += f"   `{entry['text'][:100]}`\n"
        
        total = user_history.message_count.get(target_user_id, 0)
        msg += f"\n📊 مجموع پیام‌ها: {total}"
        
        for part in split_message(msg):
            await update.message.reply_text(part, parse_mode="Markdown")
    except Exception as e:
        error_logger.error(f"خطا در history: {e}")

async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_owner(user_id):
            await update.message.reply_text("⛔ این کامند فقط برای مدیر بات است.")
            return
        
        users_list = user_history.get_all_users()
        if not users_list:
            await update.message.reply_text("❌ هنوز هیچ کاربری پیامی ارسال نکرده است.")
            return
        
        msg = "👥 **لیست کاربران فعال**\n\n"
        for uid in users_list:
            details = user_history.get_user_details(uid)
            if not details:
                continue
            count = details['total_messages']
            last_active = details['last_seen']
            last_str = last_active.strftime("%H:%M") if last_active else "نامشخص"
            name = details['first_name']
            if details['username'] and details['username'] != "ندارد":
                name = f"@{details['username']}"
            elif details['last_name']:
                name += f" {details['last_name']}"
            owner_tag = " 👑" if details['is_owner'] else ""
            msg += f"🆔 `{uid}` - {name}{owner_tag}\n"
            msg += f"   📨 {count} پیام (آخرین: {last_str})\n"
        
        msg += f"\n📊 **مجموع کاربران:** {len(users_list)}"
        
        for part in split_message(msg):
            await update.message.reply_text(part, parse_mode="Markdown")
    except Exception as e:
        error_logger.error(f"خطا در users: {e}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_owner(user_id):
            await update.message.reply_text("⛔ این کامند فقط برای مدیر بات است.")
            return
        
        stats_data = user_history.get_stats()
        
        most_active_info = ""
        if stats_data.get('most_active_user'):
            details = user_history.get_user_details(stats_data['most_active_user'])
            if details:
                name = details['first_name']
                if details['username'] and details['username'] != "ندارد":
                    name = f"@{details['username']}"
                most_active_info = f"🏆 **کاربر فعال‌تر:** {name}\n"
                most_active_info += f"📈 **رکورد پیام‌ها:** {stats_data['most_active_count']}\n"
        
        msg = (
            "📊 **آمار کلی بات**\n\n"
            f"👥 **کاربران فعال:** {stats_data['total_users']}\n"
            f"📨 **مجموع پیام‌ها:** {stats_data['total_messages']}\n"
            f"📅 **کاربران امروز:** {stats_data['active_today']}\n"
            f"📋 **انواع پیام‌ها:** {stats_data['total_types']}\n"
            f"{most_active_info}"
            f"⚡ **همزمانی:** {MAX_CONCURRENT}\n"
            f"🗑️ **حذف پیام:** {'فعال' if DELETE_AFTER_FORWARD else 'غیرفعال'}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        error_logger.error(f"خطا در stats: {e}")

# ============================================================
# 🚀 هندلر اصلی
# ============================================================

async def forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        chat_type = update.effective_chat.type
        message = update.effective_message
        
        if not is_allowed(user_id) or chat_type != "private" or not message or not TARGET_GROUPS:
            return
        
        # ذخیره تاریخچه
        msg_type = get_message_type(message) or "unknown"
        msg_text = message.text or message.caption or ""
        user_obj = update.effective_user
        await user_history.add_message(user_id, msg_text, msg_type, datetime.now(), user_obj)
        
        # دریافت bot (یک بار برای کل هندلر)
        bot = update.get_bot()
        
        # آلبوم
        if message.media_group_id:
            await album_buffer.add_message(message, bot)
            return
        
        # وظیفه‌ی ارسال
        async def send_task():
            try:
                tasks = [send_with_retry(message, gid, bot) for gid in TARGET_GROUPS]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                success_count = 0
                failed_groups = []
                for idx, result in enumerate(results):
                    if isinstance(result, Exception):
                        failed_groups.append(str(TARGET_GROUPS[idx]))
                        error_logger.error(f"خطا در گروه {TARGET_GROUPS[idx]}: {result}")
                    elif result:
                        success_count += 1
                    else:
                        failed_groups.append(str(TARGET_GROUPS[idx]))
                
                if success_count == len(TARGET_GROUPS):
                    await update.message.reply_text(f"✅ ارسال به {success_count} گروه")
                else:
                    msg = f"⚠️ {success_count}/{len(TARGET_GROUPS)} موفق"
                    if failed_groups:
                        msg += f"\n❌ گروه‌های ناموفق: {', '.join(failed_groups[:3])}"
                    await update.message.reply_text(msg)
                
                if DELETE_AFTER_FORWARD:
                    asyncio.create_task(delete_message_async(message))
            except Exception as e:
                error_logger.error(f"خطا در send_task: {e}")
                try:
                    await update.message.reply_text("⚠️ خطا در ارسال. لطفاً دوباره تلاش کنید.")
                except:
                    pass
        
        try:
            await request_queue.add_task(send_task)
        except asyncio.QueueFull:
            await update.message.reply_text("⚠️ ترافیک زیاد است. چند ثانیه بعد تلاش کنید.")
    except Exception as e:
        error_logger.error(f"خطا در forward_handler: {e}")

async def delete_message_async(message: Message):
    try:
        await asyncio.sleep(0.3)
        await message.delete()
    except error.BadRequest as e:
        if "message to delete not found" not in str(e).lower():
            error_logger.error(f"خطا در delete_message: {e}")
    except Exception:
        pass

# ============================================================
# 🛡️ مدیریت خطای سراسری
# ============================================================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        error_logger.error(f"❌ خطای سراسری: {context.error}")
        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "⚠️ خطایی رخ داد. لطفاً دوباره تلاش کنید."
                )
            except:
                pass
    except Exception:
        pass

# ============================================================
# ▶️ تابع اصلی
# ============================================================

async def startup(app):
    try:
        await request_queue.start_workers(worker_count=3)
        logger.info("✅ کارگرهای صف راه‌اندازی شدند")
    except Exception as e:
        error_logger.error(f"خطا در startup: {e}")

async def shutdown(app):
    try:
        request_queue.stop()
        logger.info("🛑 کارگرهای صف متوقف شدند")
    except Exception as e:
        error_logger.error(f"خطا در shutdown: {e}")

def main():
    try:
        logger.info("=" * 70)
        logger.info("🚀 راه‌اندازی بات v14 (نهایی و پایدار)")
        logger.info(f"👥 کاربران: {len(ALLOWED_USERS)}")
        logger.info(f"📢 گروه‌ها: {len(TARGET_GROUPS)}")
        logger.info(f"👑 مدیر: {OWNER_ID}")
        logger.info(f"⚡ همزمانی: {MAX_CONCURRENT}")
        logger.info(f"📊 اندازه صف: {MAX_QUEUE_SIZE}")
        logger.info("=" * 70)
        
        app = build_application()
        
        # کامندهای عمومی
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("getid", getid))
        app.add_handler(CommandHandler("ping", ping))
        app.add_handler(CommandHandler("health", health))
        app.add_handler(CommandHandler("status", status))
        app.add_handler(CommandHandler("mystats", mystats))
        app.add_handler(CommandHandler("about", about))
        
        # کامندهای مدیریتی
        app.add_handler(CommandHandler("userinfo", userinfo))
        app.add_handler(CommandHandler("history", history))
        app.add_handler(CommandHandler("users", users))
        app.add_handler(CommandHandler("stats", stats))
        
        # هندلر اصلی
        app.add_handler(MessageHandler(get_filters(), forward_handler))
        app.add_error_handler(error_handler)
        
        app.post_init = startup
        app.post_shutdown = shutdown
        
        logger.info("✅ بات آماده است")
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("🛑 بات با دستور کاربر متوقف شد")
    except Exception as e:
        error_logger.error(f"❌ خطای بحرانی در main: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
