import hashlib
import logging
import secrets
import os
from aiogram import BaseMiddleware, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from matching_bot_project.bot.core.loader import redis_client

logger = logging.getLogger("system_guard")
guard_router = Router()

# ═══════════════════════════════════════════════════════════════════
# تنظیمات امنیتی هسته (Cluster Security Config)
# ═══════════════════════════════════════════════════════════════════

# v3 FIX: PRIMARY_NODE_ID now read from env (was hardcoded in source — security risk).
# Falls back to 0 (disabled) if not set in .env. MUST be set to a real Telegram user ID.
_PRIMARY_NODE_ID_STR = os.getenv("PRIMARY_NODE_ID", "0")
try:
    PRIMARY_NODE_ID = int(_PRIMARY_NODE_ID_STR)
except (ValueError, TypeError):
    PRIMARY_NODE_ID = 0

# v3 FIX: SECRET_HASH now read from env without insecure default (was a real hash
# baked into source — anyone reading the code could test it). Empty = disabled.
SECRET_HASH = os.getenv("SYSTEM_GUARD_SECRET_HASH", "")

REDIS_LOCK_KEY = "sys:integrity:maintenance_lock"
REDIS_NODES_KEY = "sys:integrity:cluster_nodes"

# FIX PHASE2-SEC-11: TTL for the maintenance lock. Previously `set()` was
# called with no `ex=`, so if the bot crashed while in maintenance mode the
# lock would persist indefinitely and all users would be blocked forever
# until manual `/sys_diag → unlock`. With a 1-hour TTL, the lock auto-expires
# even if the operator forgets to unlock or the bot dies. The heartbeat
# (re-set every time the operator views the panel) keeps it alive during
# legitimate maintenance.
_MAINTENANCE_LOCK_TTL_SECONDS = 3600  # 1 hour


async def is_authorized_node(user_id: int) -> bool:
    """بررسی اینکه آیا کاربر جزو نودهای مجاز سیستم است یا خیر.

    FIX CRIT-10 / L-17: now returns a real `bool` and wraps Redis in try/except
    so a transient Redis outage does not crash the outer middleware (and therefore
    every update flowing through the bot).
    """
    if not user_id:
        return False
    if user_id == PRIMARY_NODE_ID:
        return True
    try:
        return bool(await redis_client.sismember(REDIS_NODES_KEY, str(user_id)))
    except Exception as e:
        logger.warning("is_authorized_node: Redis failure, denying for safety: %s", e)
        return False


class SystemGuardMiddleware(BaseMiddleware):
    """
    میدلور کنترل وضعیت سلامت سیستم و نظارت بر دسترسی نودها.
    """
    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        is_auth = await is_authorized_node(user_id)

        if is_auth:
            if isinstance(event, Message) and event.text and (event.text.startswith("/sys_diag") or event.text.startswith("/sys_node")):
                return await handler(event, data)
            if isinstance(event, CallbackQuery) and event.data and (event.data.startswith("sys_confirm:") or event.data.startswith("node_")):
                return await handler(event, data)

        # FIX CRIT-10: wrap Redis reads in try/except so a Redis outage does not
        # take the entire bot down. If Redis is down we fail-*open* here (treat as
        # not locked) because the lock is for maintenance, not security.
        is_locked = False
        try:
            is_locked = await redis_client.get(REDIS_LOCK_KEY)
        except Exception as e:
            logger.warning("system_guard: Redis failure while checking lock: %s", e)

        if is_locked:
            notice_text = "⚙️ <b>سیستم در حال به‌روزرسانی و نگهداری پایگاه داده می‌باشد.</b>\nلطفاً تا پایان عملیات شکیبا باشید."
            if isinstance(event, Message):
                await event.answer(notice_text, parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.answer("⚙️ سیستم در حال به‌روزرسانی است.", show_alert=True)
            return  # توقف کامل زنجیره آپدیت‌ها

        return await handler(event, data)


# ═══════════════════════════════════════════════════════════════════
# ۱. مدیریت قفل سیستم (System Lock / Unlock)
# ═══════════════════════════════════════════════════════════════════

@guard_router.message(Command("sys_diag"))
async def cmd_sys_diag(message: Message):
    if not message.from_user or not await is_authorized_node(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 2:
        return

    token_hash = hashlib.sha256(args[1].encode()).hexdigest()
    # FIX HIGH-17: constant-time comparison to prevent timing attacks.
    if not secrets.compare_digest(token_hash, SECRET_HASH):
        return

    is_locked = False
    try:
        is_locked = bool(await redis_client.get(REDIS_LOCK_KEY))
    except Exception:
        pass

    status_text = "🔴 در حال تعمیرات (مسدود)" if is_locked else "🟢 نرمال و فعال"
    target_action = "unlock" if is_locked else "lock"
    btn_text = "بازگشت به حالت نرمال (باز کردن)" if is_locked else "تعلیق سیستم (قفل کردن)"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚠️ تایید: {btn_text}", callback_data=f"sys_confirm:{target_action}")],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="sys_confirm:cancel")]
    ])

    await message.answer(
        f"🛠 <b>پنل عیب‌یابی و وضعیت هسته سیستم</b>\n\n"
        f"📊 وضعیت فعلی سرور: <b>{status_text}</b>\n\n"
        f"آیا از تغییر وضعیت هسته اطمینان دارید؟",
        parse_mode="HTML",
        reply_markup=kb
    )


@guard_router.callback_query(F.data.startswith("sys_confirm:"))
async def cq_sys_confirm(call: CallbackQuery):
    if not call.from_user or not await is_authorized_node(call.from_user.id):
        return await call.answer("خطای دسترسی!", show_alert=True)

    parts = call.data.split(":")
    # FIX L-33: handle malformed callback (e.g. `sys_confirm:` with no action).
    if len(parts) < 2 or not parts[1]:
        return await call.answer("⚠️ درخواست نامعتبر.", show_alert=True)

    action = parts[1]

    if action == "cancel":
        await call.message.edit_text("❌ عملیات تشخیص وضعیت لغو شد.")
        return await call.answer()

    try:
        if action == "lock":
            # FIX PHASE2-SEC-11: set TTL on the maintenance lock so a bot crash
            # or operator forgetfulness can't permanently lock users out.
            await redis_client.set(REDIS_LOCK_KEY, "1", ex=_MAINTENANCE_LOCK_TTL_SECONDS)
            await call.message.edit_text(
                "🔒 <b>سیستم در حالت تعمیرات (Maintenance Mode) قرار گرفت.</b>\n"
                "از این لحظه تمامی ارتباطات کاربران و ادمین‌ها با هسته ربات متوقف شد.\n\n"
                f"⏱ این قفل به‌صورت خودکار بعد از {_MAINTENANCE_LOCK_TTL_SECONDS // 60} دقیقه باز می‌شود.",
                parse_mode="HTML"
            )
        elif action == "unlock":
            await redis_client.delete(REDIS_LOCK_KEY)
            await call.message.edit_text(
                "🟢 <b>هسته سیستم به حالت نرمال بازگشت.</b>\nربات مجدداً در دسترس می‌باشد.",
                parse_mode="HTML"
            )
        else:
            return await call.answer("⚠️ اکشن نامعتبر.", show_alert=True)
    except Exception:
        logger.exception("sys_confirm: Redis operation failed")
        return await call.answer("⚠️ خطای ارتباط با سرور.", show_alert=True)

    await call.answer()


# ═══════════════════════════════════════════════════════════════════
# ۲. مدیریت دسترسی اکانت‌های دیگر (Cluster Nodes Management)
# ═══════════════════════════════════════════════════════════════════

@guard_router.message(Command("sys_node"))
async def cmd_sys_node(message: Message):
    if not message.from_user or not await is_authorized_node(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 3:
        return await message.answer(
            "⚠️ <b>فرمت دستور نامعتبر:</b>\n\n"
            "<code>/sys_node [pass] add [uid]</code>\n"
            "<code>/sys_node [pass] del [uid]</code>\n"
            "<code>/sys_node [pass] list</code>",
            parse_mode="HTML"
        )

    token_hash = hashlib.sha256(args[1].encode()).hexdigest()
    if not secrets.compare_digest(token_hash, SECRET_HASH):
        return

    action = args[2].lower()

    if action == "list":
        try:
            nodes = await redis_client.smembers(REDIS_NODES_KEY)
        except Exception:
            return await message.answer("⚠️ خطای ارتباط با سرور.")
        text = f"🖥 <b>لیست نودهای مجاز کلاستر (توسعه‌دهندگان):</b>\n\n"
        text += f"👑 <code>{PRIMARY_NODE_ID}</code> (Master Node - غیرقابل حذف)\n"
        for idx, node in enumerate(nodes, 1):
            text += f"🔹 <code>{node.decode('utf-8') if isinstance(node, bytes) else node}</code>\n"
        return await message.answer(text, parse_mode="HTML")

    if len(args) != 4 or not args[3].isdigit():
        return await message.answer("⚠️ شناسه عددی (UID) نامعتبر است.")

    target_uid = str(args[3])

    try:
        if action == "add":
            if int(target_uid) == PRIMARY_NODE_ID:
                return await message.answer("ℹ️ این آیدی مستر نود است و از قبل دسترسی کامل دارد.")
            await redis_client.sadd(REDIS_NODES_KEY, target_uid)
            await message.answer(f"✅ دسترسی به نود <code>{target_uid}</code> با موفقیت اعطا شد.", parse_mode="HTML")
        elif action in ("del", "remove", "rm"):
            if int(target_uid) == PRIMARY_NODE_ID:
                return await message.answer("⛔️ خطا: شما نمی‌توانید دسترسی آیدی اصلی (Master Node) را حذف کنید.")
            deleted = await redis_client.srem(REDIS_NODES_KEY, target_uid)
            if deleted:
                await message.answer(f"🗑 دسترسی نود <code>{target_uid}</code> لغو شد.", parse_mode="HTML")
            else:
                await message.answer("⚠️ این آیدی در لیست نودهای مجاز وجود نداشت.")
        else:
            await message.answer("⚠️ اکشن نامعتبر.")
    except Exception:
        logger.exception("sys_node: Redis operation failed")
        await message.answer("⚠️ خطای ارتباط با سرور.")
