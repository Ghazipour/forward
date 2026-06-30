#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import asyncio
from datetime import datetime
from typing import List, Optional

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

USE_PROXY = os.getenv("USE_PROXY", "false").lower() == "true"
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:1080")
DELETE_AFTER_FORWARD = os.getenv("DELETE_AFTER_FORWARD", "true").lower() == "true"
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "2"))
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT", "30"))

# ============================================================
# 📝 راه‌اندازی لاگ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 📦 ایمپورت کتابخانه‌ها
# ============================================================

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    from telegram.request import HTTPXRequest
except ImportError as e:
    logger.error(f"❌ کتابخانه نصب نیست: {e}")
    sys.exit(1)

# ============================================================
# 🛠 توابع کمکی
# ============================================================

def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USERS

def get_filters():
    try:
        return filters.ChatType.PRIVATE & ~filters.COMMAND
    except AttributeError:
        try:
            return filters.PRIVATE & ~filters.COMMAND
        except AttributeError:
            return filters.ALL & ~filters.COMMAND

def build_application():
    request_kwargs = {
        "connect_timeout": CONNECTION_TIMEOUT,
        "read_timeout": CONNECTION_TIMEOUT,
        "write_timeout": CONNECTION_TIMEOUT,
        "pool_timeout": CONNECTION_TIMEOUT,
    }
    if USE_PROXY and PROXY_URL:
        request_kwargs["proxy_url"] = PROXY_URL
        logger.info(f"✅ پروکسی فعال: {PROXY_URL}")
    else:
        logger.info("ℹ️ بدون پروکسی")

    request = HTTPXRequest(**request_kwargs)
    return Application.builder().token(BOT_TOKEN).request(request).build()

async def safe_forward(message, chat_id: int) -> bool:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await message.forward(chat_id=chat_id)
            logger.info(f"✅ فوروارد به {chat_id} موفق (تلاش {attempt})")
            return True
        except Exception as e:
            logger.warning(f"⚠️ خطا (تلاش {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
    return False

# ============================================================
# 📌 هندلرها
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return
    await update.message.reply_text(
        "🤖 **بات فورواردر شخصی**\n\n"
        "✅ فقط کاربران مجاز می‌تونن پیام بدن.\n"
        f"📢 تعداد گروه‌ها: {len(TARGET_GROUPS)}\n"
        "برای دریافت آیدی از /getid استفاده کن.",
        parse_mode="Markdown"
    )

async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"🆔 **آیدی شما:** `{user_id}`\n"
        f"🆔 **آیدی این چت:** `{chat_id}`",
        parse_mode="Markdown"
    )

async def forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type

    if not is_allowed(user_id):
        logger.info(f"🚫 کاربر غیرمجاز {user_id}")
        return

    if chat_type != "private":
        logger.info(f"ℹ️ کاربر {user_id} از گروه پیام داد (نادیده گرفته شد)")
        return

    if not update.effective_message:
        return

    if not TARGET_GROUPS:
        await update.message.reply_text("❌ هیچ گروه مقصدی تنظیم نشده!")
        return

    success_count = 0
    for group_id in TARGET_GROUPS:
        if await safe_forward(update.effective_message, group_id):
            success_count += 1

    if success_count == len(TARGET_GROUPS):
        await update.message.reply_text(f"✅ پیام به {success_count} گروه فوروارد شد.")
    elif success_count > 0:
        await update.message.reply_text(f"⚠️ {success_count} از {len(TARGET_GROUPS)} گروه موفق شد.")
    else:
        await update.message.reply_text("❌ فوروارد ناموفق بود.")

    if DELETE_AFTER_FORWARD:
        try:
            await update.message.delete()
            logger.info("🗑️ پیام اصلی حذف شد")
        except Exception as e:
            logger.warning(f"⚠️ خطا در حذف: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"❌ خطا: {context.error}")

# ============================================================
# ▶️ تابع اصلی
# ============================================================

def main():
    logger.info("🚀 راه‌اندازی بات فورواردر")
    logger.info(f"👥 کاربران مجاز: {ALLOWED_USERS}")
    logger.info(f"📢 گروه‌های مقصد: {TARGET_GROUPS}")
    logger.info(f"🗑️ حذف پیام: {'فعال' if DELETE_AFTER_FORWARD else 'غیرفعال'}")

    app = build_application()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("getid", getid))
    app.add_handler(MessageHandler(get_filters(), forward_handler))
    app.add_error_handler(error_handler)

    logger.info("✅ بات آماده‌ی دریافت پیام است...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
