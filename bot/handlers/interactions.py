from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from matching_bot_project.bot.handlers.matching import handle_successful_match
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.constants import ReplyBtn, SystemMsg
from matching_bot_project.bot.core.formatters import build_unified_profile_card
from matching_bot_project.bot.core.loader import bot, dating_scheduler, dp, redis_client
from matching_bot_project.bot.keyboards.inline import (
    get_end_chat_confirm_keyboard,
    get_end_date_confirm_keyboard,
    get_report_reasons_keyboard,
    get_user_action_keyboard,
)
from matching_bot_project.bot.keyboards.reply import get_cancel_keyboard, get_main_menu_keyboard
from matching_bot_project.bot.states.states import (
    ChatStates,
    MatchingStates,
    QuestionnaireStates,
    ReportStates,
    VIPStates,
)
from matching_bot_project.database.models.models import BlockList, MatchHistory, UserLike
from matching_bot_project.database.queries import crud

logger = logging.getLogger(__name__)
router = Router(name="interactions_handler")
# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DATE_CANCELLED_TEXT = SystemMsg.DATE_CANCELLED_TEXT

_GENDER_DISPLAY: dict[str, str] = {
    "male": "مرد 👨",
    "female": "زن 👩",
    "boy": "پسر 👦",
    "girl": "دختر 👧",
}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _parse_int_suffix(data: str, prefix: str) -> Optional[int]:
    try:
        return int(data.removeprefix(prefix))
    except ValueError:
        return None
    
def get_user_state(user_id: int) -> FSMContext:
    return FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
    )


def _build_profile_card(user, compatibility: Optional[int] = None) -> str:
    """
    حالا این تابع فقط یک واسطه (Wrapper) برای تابع اصلی است تا همه‌جا خروجی یکسان باشد.
    """
    return build_unified_profile_card(user, is_own_profile=False, compatibility=compatibility)

async def _send_profile_card(target_chat_id: int, user, action_kb: InlineKeyboardMarkup) -> None:
    """
    Helper function to uniformly send a user's profile card, photo, and voice
    to a specific chat ID without breaking HTML tags or losing button attachments.
    """
    profile_card = _build_profile_card(user)
    photo_id = getattr(user, 'profile_photo_file_id', None)
    
    try:
        if photo_id:
            # 💡 اصلاح: اگر طول متن هماهنگ با کپشن تلگرام بود، یکجا ارسال شود
            if len(profile_card) <= 1024:
                await bot.send_photo(
                    chat_id=target_chat_id,
                    photo=photo_id,
                    caption=profile_card,
                    parse_mode="HTML",
                    reply_markup=action_kb,
                )
            else:
                # 💡 اگر متن طولانی بود، ابتدا عکس ارسال شده و بلافاصله متن کامل همراه با کیبورد فرستاده می‌شود
                await bot.send_photo(chat_id=target_chat_id, photo=photo_id)
                await bot.send_message(
                    chat_id=target_chat_id,
                    text=profile_card,
                    parse_mode="HTML",
                    reply_markup=action_kb,
                )
        else:
            # پروفایل‌های بدون عکس
            await bot.send_message(
                chat_id=target_chat_id,
                text=profile_card,
                parse_mode="HTML",
                reply_markup=action_kb,
            )
            
        profile_voice = getattr(user, 'profile_voice_file_id', None)
        if profile_voice:
            await bot.send_voice(
                chat_id=target_chat_id,
                voice=profile_voice,
                caption="🎵 <b>آهنگ/وویس پروفایل</b>",
                parse_mode="HTML"
            )

    except Exception as exc:
        logger.error("Failed to send profile message to chat %s: %s", target_chat_id, exc)

# ─────────────────────────────────────────────────────────────────────────────
# Section 1 – View Profile
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("view_profile_"))
async def view_partner_profile(call: CallbackQuery, db_session: AsyncSession) -> None:
    target_id = _parse_int_suffix(call.data, "view_profile_")
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    user = await crud.get_user_by_tg_id(db_session, target_id)
    if not user:
        await call.answer("❌ پروفایل کاربر یافت نشد.", show_alert=True)
        return

    block_result = await db_session.execute(
        select(BlockList).where(
            BlockList.blocker_id == call.from_user.id,
            BlockList.blocked_id == target_id,
        )
    )
    is_blocked = block_result.scalar_one_or_none() is not None
    
    try:
        already_friend = await crud.is_friend(db_session, call.from_user.id, target_id)
    except Exception:
        already_friend = False

    action_kb = get_user_action_keyboard(target_id, is_blocked=is_blocked, is_friend=already_friend)

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    is_target_vip = user.is_vip or (user.vip_expires_at and user.vip_expires_at > now_utc)
    if is_target_vip and call.from_user.id != target_id:
        key = f"user:{target_id}:viewers"
        await redis_client.zadd(key, {str(call.from_user.id): time.time()})
        await redis_client.expire(key, 604800)

    await _send_profile_card(target_chat_id=call.from_user.id, user=user, action_kb=action_kb)
    await call.answer()

@router.message(F.text == ReplyBtn.PHASE_USER_PROFILE)
async def view_partner_profile_from_reply_btn(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    fsm_data = await state.get_data()
    target_id = fsm_data.get("partner_id") or fsm_data.get("partner_tg_id")
    
    if not target_id:
        match_id = fsm_data.get("match_history_id")
        if match_id:
            match_history = await db_session.get(MatchHistory, match_id)
            if match_history:
                target_id = match_history.user_two_id if match_history.user_one_id == message.from_user.id else match_history.user_one_id
        
    if not target_id:
        await message.answer("⚠️ هنوز به کسی متصل نشده‌اید یا دیت پایان یافته است.")
        return

    user = await crud.get_user_by_tg_id(db_session, target_id)
    if not user:
        await message.answer("❌ پروفایل کاربر یافت نشد.")
        return

    block_result = await db_session.execute(
        select(BlockList).where(
            BlockList.blocker_id == message.from_user.id,
            BlockList.blocked_id == target_id,
        )
    )
    is_blocked = block_result.scalar_one_or_none() is not None
    
    try:
        already_friend = await crud.is_friend(db_session, message.from_user.id, target_id)
    except Exception:
        already_friend = False

    action_kb = get_user_action_keyboard(target_id, is_blocked=is_blocked, is_friend=already_friend)
    await _send_profile_card(target_chat_id=message.from_user.id, user=user, action_kb=action_kb)

# ─────────────────────────────────────────────────────────────────────────────
# Section 2 – End Date Early (and Extracted Helpers)
# ─────────────────────────────────────────────────────────────────────────────

async def execute_chat_termination(db_session: AsyncSession, match_id: int, caller_id: int) -> bool:
    result = await db_session.execute(
        select(MatchHistory).where(MatchHistory.id == match_id)
    )
    match_history: MatchHistory | None = result.scalar_one_or_none()

    if not match_history or not match_history.is_active:
        return False

    match_history.is_active = False
    match_history.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        await db_session.commit()
    except Exception as exc:
        logger.error("Failed to deactivate match %s in the database: %s", match_id, exc)
        await db_session.rollback()
        return False

    try:
        # 💡 در صورت وجود متد لغو تسک زمان‌بندی شده در سکولر، آن را اجرا کنید تا جاب بک‌گراند حذف شود
        if hasattr(dating_scheduler, 'cancel_match_timeout'):
            await dating_scheduler.cancel_match_timeout(match_id)
            
        await dating_scheduler.redis.delete(f"date:timeout:{match_id}")
        await dating_scheduler.redis.delete(f"user:state:{match_history.user_one_id}")
        await dating_scheduler.redis.delete(f"user:state:{match_history.user_two_id}")
    except Exception as exc:
        logger.warning("Could not delete core Redis tracking keys for match cancellation %s: %s", match_id, exc)

    for uid in (match_history.user_one_id, match_history.user_two_id):
        ctx = get_user_state(uid)
        try:
            # 💡 اصلاح اصلی باگ ۴: وضعیت کاربر به طور کامل ریست (None) می‌شود تا قفل منو باز شود
            await ctx.set_state(None)
            await ctx.clear()
        except Exception as exc:
            logger.warning("Could not clear FSM state for user %s: %s", uid, exc)

        try:
            if uid != caller_id:
                await bot.send_message(
                    chat_id=uid,
                    text="طرف مقابل دیت را پایان داد.",
                    reply_markup=get_main_menu_keyboard(),
                )
            else:
                await bot.send_message(
                    chat_id=uid,
                    text=_DATE_CANCELLED_TEXT,
                    reply_markup=get_main_menu_keyboard(),
                )
        except Exception as exc:
            logger.error("Failed to send cancellation notice to user %s: %s", uid, exc)

    return True


async def execute_chat_termination_no_commit(db_session: AsyncSession, match_id: int, caller_id: int) -> bool:
    """
    نسخه‌ی بدون commit از execute_chat_termination.
    برای جریان‌هایی (مثل safety.py) که چندین تغییر دیتابیسی را باید در یک
    تراکنش اتمیک واحد commit کنند، نه جداگانه.

    توجه: caller مسئول صدا زدن db_session.commit()/rollback() است.
    عملیات‌های جانبی (Redis، FSM، اطلاع‌رسانی به کاربران) مستقل از commit
    اجرا می‌شوند چون idempotent هستند و تأخیرشان فایده‌ای ندارد.
    """
    result = await db_session.execute(
        select(MatchHistory).where(MatchHistory.id == match_id)
    )
    match_history: MatchHistory | None = result.scalar_one_or_none()

    if not match_history or not match_history.is_active:
        return False

    match_history.is_active = False
    match_history.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
    # 💡 commit/rollback عمداً انجام نمی‌شود — caller مسئول آن است.

    try:
        if hasattr(dating_scheduler, 'cancel_match_timeout'):
            await dating_scheduler.cancel_match_timeout(match_id)

        await dating_scheduler.redis.delete(f"date:timeout:{match_id}")
        await dating_scheduler.redis.delete(f"user:state:{match_history.user_one_id}")
        await dating_scheduler.redis.delete(f"user:state:{match_history.user_two_id}")
    except Exception as exc:
        logger.warning("Could not delete core Redis tracking keys for match cancellation %s: %s", match_id, exc)

    for uid in (match_history.user_one_id, match_history.user_two_id):
        ctx = get_user_state(uid)
        try:
            await ctx.set_state(None)
            await ctx.clear()
        except Exception as exc:
            logger.warning("Could not clear FSM state for user %s: %s", uid, exc)

        try:
            if uid != caller_id:
                await bot.send_message(
                    chat_id=uid,
                    text="طرف مقابل دیت را پایان داد.",
                    reply_markup=get_main_menu_keyboard(),
                )
            else:
                await bot.send_message(
                    chat_id=uid,
                    text=_DATE_CANCELLED_TEXT,
                    reply_markup=get_main_menu_keyboard(),
                )
        except Exception as exc:
            logger.error("Failed to send cancellation notice to user %s: %s", uid, exc)

    return True


@router.message(F.text == ReplyBtn.END_DATE)
async def request_end_date_confirm(message: Message, db_session: AsyncSession) -> None:
    active_match = await crud.get_active_match(db_session, message.from_user.id)
    if not active_match:
        await message.answer("⚠️ دیت فعالی یافت نشد.", reply_markup=get_main_menu_keyboard())
        return
    await message.answer(
        "⚠️ آیا مطمئن هستید که می‌خواهید دیت را پایان دهید؟\nاین عمل قابل بازگشت نیست.",
        reply_markup=get_end_date_confirm_keyboard(),
    )

@router.callback_query(F.data == "confirm_end_date")
async def confirm_end_date(call: CallbackQuery, db_session: AsyncSession) -> None:
    active_match = await crud.get_active_match(db_session, call.from_user.id)
    if not active_match:
        await call.answer("دیت فعالی یافت نشد.", show_alert=True)
        return
    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error editing reply markup: {e}")
        
    await execute_chat_termination(db_session, active_match.id, call.from_user.id)

@router.callback_query(F.data == "cancel_end_date")
async def cancel_end_date(call: CallbackQuery) -> None:
    await call.answer("❌ لغو شد. دیت ادامه دارد.")
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error deleting message: {e}")

@router.message(F.text == ReplyBtn.END_CHAT)
async def request_end_chat_confirm(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current != ChatStates.anonymous_chat_active.state:
        await message.answer("⚠️ چت فعالی یافت نشد.", reply_markup=get_main_menu_keyboard())
        return
    await message.answer(
        "⚠️ آیا مطمئن هستید که می‌خواهید چت را پایان دهید؟",
        reply_markup=get_end_chat_confirm_keyboard(),
    )

@router.callback_query(ChatStates.anonymous_chat_active, F.data == "confirm_end_chat")
async def confirm_end_chat(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error editing reply markup: {e}")
        
    fsm_data = await state.get_data()
    match_history_id = fsm_data.get("match_history_id")
    if match_history_id:
        await execute_chat_termination(db_session, match_history_id, call.from_user.id)
    else:
        await state.clear()
        await call.message.answer("به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())

@router.callback_query(F.data == "cancel_end_chat")
async def cancel_end_chat(call: CallbackQuery) -> None:
    await call.answer("❌ لغو شد. چت ادامه دارد.")
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error deleting message: {e}")

@router.callback_query(F.data.startswith("end_date_"))
async def end_date_early(call: CallbackQuery, db_session: AsyncSession) -> None:
    # پشتیبانی از هر دو حالت نام‌گذاری (برای جلوگیری از تداخل کدهای قدیمی و جدید)
    if call.data.startswith("end_date_early_"):
        match_id = _parse_int_suffix(call.data, "end_date_early_")
    else:
        match_id = _parse_int_suffix(call.data, "end_date_")
        
    if match_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    success = await execute_chat_termination(db_session, match_id, call.from_user.id)
    if not success:
        await call.answer("⚠️ این دیت قبلا لغو شده یا وجود ندارد.", show_alert=True)
    else:
        await call.answer("دیت با موفقیت لغو شد.", show_alert=False)

# ─────────────────────────────────────────────────────────────────────────────
# Section 3 – Block User
# ─────────────────────────────────────────────────────────────────────────────

async def execute_user_blocking(db_session: AsyncSession, blocker_id: int, blocked_id: int) -> tuple[bool, str]:
    if blocker_id == blocked_id:
        return False, "❌ نمی‌توانید خودتان را مسدود کنید."

    db_session.add(BlockList(blocker_id=blocker_id, blocked_id=blocked_id))

    try:
        await db_session.commit()
        await redis_client.sadd(f"user:{blocker_id}:blocks", str(blocked_id))
        
        match_query = await db_session.execute(
            select(MatchHistory).where(
                MatchHistory.is_active == True,
                or_(
                    and_(MatchHistory.user_one_id == blocker_id, MatchHistory.user_two_id == blocked_id),
                    and_(MatchHistory.user_one_id == blocked_id, MatchHistory.user_two_id == blocker_id)
                )
            )
        )
        active_match = match_query.scalar_one_or_none()
        if active_match:
            # Note: execute_chat_termination manages its own commits safely
            await execute_chat_termination(db_session, active_match.id, blocker_id)

        return True, "🚫 کاربر مسدود شد و دیگر به شما متصل نخواهد شد."
    except IntegrityError:
        await db_session.rollback()
        return False, "⚠️ این کاربر قبلاً مسدود شده است."
    except Exception as exc:
        await db_session.rollback()
        logger.error("Unexpected error while user %s attempted to block user %s: %s", blocker_id, blocked_id, exc)
        return False, "❌ خطای سرور. لطفاً دوباره تلاش کنید."


async def execute_user_blocking_no_commit(db_session: AsyncSession, blocker_id: int, blocked_id: int) -> tuple[bool, str]:
    """
    نسخه‌ی بدون commit از execute_user_blocking.
    برای جریان‌هایی (مثل safety.py) که چندین تغییر دیتابیسی را باید در یک
    تراکنش اتمیک واحد commit کنند، نه جداگانه.

    توجه: caller مسئول صدا زدن db_session.commit()/rollback() است.
    چون نمی‌توان برای تشخیص بلاک تکراری روی IntegrityError در زمان
    commit تکیه کرد (commit به تعویق افتاده)، ابتدا با یک SELECT
    وجود بلاک قبلی را صریحاً بررسی می‌کنیم.
    """
    if blocker_id == blocked_id:
        return False, "❌ نمی‌توانید خودتان را مسدود کنید."

    existing = await db_session.execute(
        select(BlockList.id).where(
            BlockList.blocker_id == blocker_id,
            BlockList.blocked_id == blocked_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return False, "⚠️ این کاربر قبلاً مسدود شده است."

    db_session.add(BlockList(blocker_id=blocker_id, blocked_id=blocked_id))
    # 💡 commit/rollback عمداً انجام نمی‌شود — caller مسئول آن است.

    try:
        await redis_client.sadd(f"user:{blocker_id}:blocks", str(blocked_id))
    except Exception as exc:
        logger.warning("Could not sync block to Redis for %s -> %s: %s", blocker_id, blocked_id, exc)

    try:
        match_query = await db_session.execute(
            select(MatchHistory).where(
                MatchHistory.is_active == True,
                or_(
                    and_(MatchHistory.user_one_id == blocker_id, MatchHistory.user_two_id == blocked_id),
                    and_(MatchHistory.user_one_id == blocked_id, MatchHistory.user_two_id == blocker_id)
                )
            )
        )
        active_match = match_query.scalar_one_or_none()
        if active_match:
            # نسخه‌ی no_commit چت فعال را هم بدون commit جداگانه می‌بندد
            await execute_chat_termination_no_commit(db_session, active_match.id, blocker_id)
    except Exception as exc:
        logger.error("Error checking/terminating active match during no-commit block %s -> %s: %s", blocker_id, blocked_id, exc)

    return True, "🚫 کاربر مسدود شد و دیگر به شما متصل نخواهد شد."
    
@router.callback_query(F.data.startswith("block_user_"))
async def block_user(call: CallbackQuery, db_session: AsyncSession) -> None:
    target_id = _parse_int_suffix(call.data, "block_user_")
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    caller_id = call.from_user.id
    success, msg = await execute_user_blocking(db_session, caller_id, target_id)

    if success:
        limit_key = f"user:blocks_today:{caller_id}"
        blocks_count_str = await redis_client.get(limit_key)
        blocks_count = int(blocks_count_str) if blocks_count_str else 0

        pipe = redis_client.pipeline()
        pipe.incr(limit_key)
        if blocks_count == 0:
            now = datetime.now(timezone.utc)
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seconds_to_midnight = int((midnight - now).total_seconds())
            pipe.expire(limit_key, seconds_to_midnight)

        if blocks_count + 1 >= 3:
            pipe.setex(f"user:block_cooldown:{caller_id}", 86400, "1")

        await pipe.execute()

        if call.message and call.message.reply_markup:
            new_kb = []
            for row in call.message.reply_markup.inline_keyboard:
                new_row = []
                for btn in row:
                    if btn.callback_data == f"block_user_{target_id}":
                        new_row.append(InlineKeyboardButton(text="🔓 آنبلاک کاربر", callback_data=f"unblock_user_{target_id}"))
                    else:
                        new_row.append(btn)
                new_kb.append(new_row)
            try:
                await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=new_kb))
            except TelegramBadRequest:
                pass
            except Exception as e:
                logger.error(f"Unexpected error editing reply markup: {e}")

    await call.answer(msg, show_alert=True)

# ─────────────────────────────────────────────────────────────────────────────
# Section 4 – Direct Message Request
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("req_direct_"))
async def request_direct_message(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    target_id = _parse_int_suffix(call.data, "req_direct_")
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    caller_id = call.from_user.id
    caller = await crud.get_user_by_tg_id(db_session, caller_id)

    if not caller:
        await call.answer("❌ حساب کاربری شما یافت نشد.", show_alert=True)
        return

    block_check = await db_session.execute(
        select(BlockList).where(
            BlockList.blocker_id == target_id,
            BlockList.blocked_id == caller_id
        )
    )
    if block_check.scalar_one_or_none():
        await call.answer("🚫 امکان ارسال دایرکت وجود ندارد (شما توسط این کاربر بلاک شده‌اید).", show_alert=True)
        return

    target_user = await crud.get_user_by_tg_id(db_session, target_id)
    if not target_user:
        await call.answer("❌ کاربر مقصد یافت نشد.", show_alert=True)
        return

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if target_user.silent_until and target_user.silent_until > now_utc:
        await call.answer("🔕 این کاربر در حال حاضر در حالت سایلنت است.", show_alert=True)
        return

    # FIXED: Check balance but DO NOT deduct coin yet.
    if caller.coin_balance < 1:
        await call.answer("❌ سکه‌های شما برای ارسال پیام کافی نیست!", show_alert=True)
        return

    await state.set_state(ChatStates.typing_direct_message)
    await state.update_data(target_direct_id=target_id)
    await call.answer()

    try:
        await bot.send_message(
            chat_id=caller_id,
            text=(
                "💬 پیام دایرکت خود را بنویسید (یک پیام متنی).\n"
                "این پیام به صورت ناشناس برای کاربر ارسال می‌شود.\n"
                "هزینه ۱ سکه (فقط در صورت تحویل موفق کسر خواهد شد)."
            ),
            reply_markup=get_cancel_keyboard(),
        )
    except Exception as exc:
        logger.error("Failed to send DM prompt to user %s: %s", caller_id, exc)

# ─────────────────────────────────────────────────────────────────────────────
# Section 5 – Gamification, Social, & Moderation 
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("like_user_"))
async def handle_like_user(call: CallbackQuery, db_session: AsyncSession) -> None:
    target_id_str = call.data.removeprefix("like_user_")
    if not target_id_str.isdigit():
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return
    target_id = int(target_id_str)
    caller_id = call.from_user.id
    if target_id == caller_id:
        await call.answer("نمی‌توانید خودتان را لایک کنید!", show_alert=True)
        return

    # Check if a duplicate like already exists
    check_stmt = select(UserLike).where(
        and_(
            UserLike.liker_id == caller_id,
            UserLike.liked_id == target_id,
            UserLike.is_pass == False
        )
    )
    existing_like = await db_session.execute(check_stmt)
    if existing_like.scalar_one_or_none():
        await call.answer("قبلاً این کاربر را لایک کرده‌اید!", show_alert=True)
        return

    await crud.save_like(db_session, caller_id, target_id, is_pass=False)
    await db_session.commit()

    total_likes = await crud.get_received_like_count(db_session, target_id)

    if total_likes > 0 and total_likes % 20 == 0:
        target_user = await crud.get_user_by_tg_id(db_session, target_id)
        if target_user:
            await crud.process_coin_transaction(db_session, target_user, 5, f"جایزه دریافت {total_likes} لایک")
            await db_session.commit()
            try:
                await bot.send_message(
                    chat_id=target_id,
                    text=(f"🎉 تبریک! پروفایل شما به <b>{total_likes} لایک</b> رسید!\n"
                          "🎁 <b>۵ سکه</b> جایزه به حساب شما واریز شد. ✨"),
                    parse_mode="HTML",
                )
            except Exception:
                pass
    try:
        like_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 مشاهده پروفایل لایک‌کننده", callback_data=f"view_profile_{caller_id}")]
        ])
        await bot.send_message(
            chat_id=target_id,
            text="❤️ یک نفر پروفایل شما را لایک کرد!",
            reply_markup=like_kb
        )
    except Exception:
        pass

    await call.answer(f"❤️ لایک شد! (مجموع: {total_likes})", show_alert=True)

@router.callback_query(F.data.startswith("add_friend_"))
async def handle_add_friend(call: CallbackQuery, db_session: AsyncSession) -> None:
    target_id_str = call.data.removeprefix("add_friend_")
    if not target_id_str.isdigit():
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return
        
    target_id = int(target_id_str)
    success = await crud.add_friend(db_session, call.from_user.id, target_id)
    
    if success:
        await db_session.commit()
        await call.answer("✅ به لیست دوستان اضافه شد.", show_alert=True)
        
        # تغییر پویای دکمه به "حذف از دوستان"
        if call.message and call.message.reply_markup:
            new_kb = []
            for row in call.message.reply_markup.inline_keyboard:
                new_row = []
                for btn in row:
                    if btn.callback_data == f"add_friend_{target_id}":
                        new_row.append(InlineKeyboardButton(text="➖ حذف از دوستان", callback_data=f"remove_friend_{target_id}"))
                    else:
                        new_row.append(btn)
                new_kb.append(new_row)
            try:
                await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=new_kb))
            except TelegramBadRequest:
                pass
            except Exception as e:
                logger.error(f"Unexpected error editing reply markup: {e}")
    else:
        await call.answer("⚠️ قبلاً اضافه شده بود.", show_alert=True)
@router.callback_query(F.data.startswith("remove_friend_"))
async def handle_remove_friend(call: CallbackQuery, db_session: AsyncSession) -> None:
    target_id_str = call.data.removeprefix("remove_friend_")
    if not target_id_str.isdigit():
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return
        
    target_id = int(target_id_str)
    success = await crud.remove_friend(db_session, call.from_user.id, target_id)
    
    if success:
        await db_session.commit()
        await call.answer("🗑 کاربر از لیست دوستان شما حذف شد.", show_alert=True)
        
        # تغییر پویای دکمه به "افزودن به دوستان"
        if call.message and call.message.reply_markup:
            new_kb = []
            for row in call.message.reply_markup.inline_keyboard:
                new_row = []
                for btn in row:
                    if btn.callback_data == f"remove_friend_{target_id}":
                        new_row.append(InlineKeyboardButton(text="➕ افزودن به دوستان", callback_data=f"add_friend_{target_id}"))
                    else:
                        new_row.append(btn)
                new_kb.append(new_row)
            try:
                await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=new_kb))
            except TelegramBadRequest:
                pass
            except Exception as e:
                logger.error(f"Unexpected error editing reply markup: {e}")
    else:
        await call.answer("⚠️ این کاربر در لیست دوستان شما قرار ندارد.", show_alert=True)


@router.callback_query(F.data.startswith("report_user_"))
async def show_report_reasons(call: CallbackQuery) -> None:
    reported_id_str = call.data.removeprefix("report_user_")
    if not reported_id_str.isdigit():
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return
    await call.answer()
    await call.message.answer(
        "لطفاً دلیل گزارش را انتخاب کنید:",
        reply_markup=get_report_reasons_keyboard(int(reported_id_str)),
    )

@router.callback_query(F.data.startswith("report_reason_"))
async def process_report_reason(call: CallbackQuery, state: FSMContext) -> None:
    # 💡 فیکس باگ دوم: استفاده از متد split از سمت چپ
    parts = call.data.removeprefix("report_reason_").split("_", 1)
    
    if len(parts) != 2 or not parts[0].isdigit():
        await call.answer("❌ خطای پردازش.", show_alert=True)
        return
        
    reported_id = int(parts[0])
    reason_code = parts[1]

    # ذخیره داده‌ها برای مرحله بعد
    await state.update_data(reported_id=reported_id, reason_code=reason_code)
    await state.set_state(ReportStates.waiting_for_report_description)

    # فقط دکمه انصراف را نمایش می‌دهیم
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data="report_cancel")]
    ])

    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


    if reason_code == "bot_fake":
        prompt_text = (
            "🤖 شما این کاربر را به عنوان «ربات یا حساب فیک» گزارش کردید.\n\n"
            "لطفاً در یک پیام کوتاه توضیح دهید که چرا فکر می‌کنید این حساب فیک است، "
            "یا اگر اسکرین‌شاتی دارید ارسال کنید:"
        )
    else:
        prompt_text = (
            "لطفاً مدرک خود را ارائه دهید.\n"
            "می‌توانید پیام کاربر خاطی را **فوروارد** کنید، یک **عکس/اسکرین‌شات** بفرستید، "
            "و یا به صورت **متنی** دلیل گزارش خود را بنویسید:"
        )

    await call.message.answer(prompt_text, reply_markup=cancel_kb, parse_mode="Markdown")
    await call.answer()


# Add new helper function for submitting reports
async def _submit_report(
    reporter_id: int,
    reported_id: int,
    reason_code: str,
    description: str,
    db_session: AsyncSession,
    evidence_message: Optional[Message] = None
) -> None:
    reason_map = {
        "inappropriate_photo": "عکس نامناسب",
        "scammer":             "کلاهبردار",
        "harassment":          "توهین و فحاشی",
        "spam":                "اسپم/تبلیغات",
        "impersonation":       "جعل هویت",
        "suspicious_link":     "ارسال لینک مشکوک",
        "adult_content":       "محتوای غیراخلاقی",
        "drugs":               "فروش مواد",
        "bot_fake":            "ربات/حساب فیک",
        "other":               "سایر موارد",
    }
    persian_reason = reason_map.get(reason_code, "نامشخص")

    await crud.create_user_report(
        session=db_session, 
        reporter_id=reporter_id, 
        reported_id=reported_id, 
        reason=persian_reason
    )
    await db_session.commit()

    admin_text = (
        "🚨 <b>گزارش تخلف جدید</b>\n\n"
        f"👤 <b>شاکی:</b> <code>{reporter_id}</code>\n"
        f"🎯 <b>متخلف:</b> <code>{reported_id}</code>\n"
        f"⚠️ <b>علت:</b> {persian_reason}\n"
        f"📝 <b>توضیحات/متن:</b> {html.escape(description) if description else 'ندارد'}"
    )
    
    # دکمه بن مستقیم به کیبورد ادمین اضافه شد تا مدیریت تسریع بشه
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 پاسخ به شاکی", callback_data=f"admin_reply_{reporter_id}")],
        [InlineKeyboardButton(text="⛔️ بن کردن متخلف", callback_data=f"admin_ban_{reported_id}")]
    ])

    for admin_id in settings.parsed_admin_ids:
        try:
            if evidence_message:
                # استفاده از copy_message به جای forward برای دور زدن محدودیت‌های پرایوسی فوروارد
                await bot.copy_message(
                    chat_id=admin_id,
                    from_chat_id=evidence_message.chat.id,
                    message_id=evidence_message.message_id,
                    caption=" مدرک ضمیمه شده گزارش 👆"
                )
            await bot.send_message(chat_id=admin_id, text=admin_text, reply_markup=admin_kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send report notification to admin {admin_id}: {e}")

@router.message(ReportStates.waiting_for_report_description, F.content_type.in_({'text', 'photo', 'document'}))
async def handle_report_evidence(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    data = await state.get_data()
    reported_id = data.get("reported_id")
    reason_code = data.get("reason_code")
    
    if not reported_id or not reason_code:
        await state.clear()
        return

    description = ""
    evidence_msg = None

    # بررسی نوع پیام فرستاده شده (متن خالی، یا پیام حاوی مدیا/فوروارد)
    if message.text and not message.forward_date:
        description = message.text
    else:
        evidence_msg = message
        if message.caption:
            description = message.caption

    await _submit_report(
        reporter_id=message.from_user.id, 
        reported_id=reported_id, 
        reason_code=reason_code, 
        description=description, 
        db_session=db_session,
        evidence_message=evidence_msg
    )
    
    await state.clear()
    if reason_code == "bot_fake":
        await message.answer("✅ گزارش شما مبنی بر فیک بودن این حساب ثبت شد. ادمین‌ها به زودی این مورد را بررسی خواهند کرد.")
    else:
        await message.answer("✅ گزارش شما به همراه مدارک با موفقیت ثبت شد و در اسرع وقت بررسی خواهد شد. با تشکر از همکاری شما.")


@router.callback_query(F.data == "report_cancel")
async def cancel_report_from_profile(call: CallbackQuery) -> None:
    await call.answer("❌ گزارش لغو شد.")
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error deleting message: {e}")

@router.callback_query(F.data.startswith("unblock_user_"))
async def unblock_user(call: CallbackQuery, db_session: AsyncSession) -> None:
    target_id = _parse_int_suffix(call.data, "unblock_user_")
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    caller_id = call.from_user.id
    
    await db_session.execute(
        delete(BlockList).where(
            BlockList.blocker_id == caller_id,
            BlockList.blocked_id == target_id
        )
    )
    await db_session.commit()
    
    await redis_client.srem(f"user:{caller_id}:blocks", str(target_id))
    await call.answer("🔓 کاربر با موفقیت از لیست سیاه شما خارج شد.", show_alert=True)
    
    if call.message and call.message.reply_markup:
        new_kb = []
        for row in call.message.reply_markup.inline_keyboard:
            new_row = []
            for btn in row:
                if btn.callback_data == f"unblock_user_{target_id}":
                    new_row.append(InlineKeyboardButton(text="🚫 بلاک کردن", callback_data=f"block_user_{target_id}"))
                else:
                    new_row.append(btn)
            new_kb.append(new_row)
        try:
            await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=new_kb))
        except TelegramBadRequest:
            pass
        except Exception as e:
            logger.error(f"Unexpected error editing reply markup: {e}")

@router.callback_query(F.data.startswith("req_chat_"))
@router.callback_query(F.data.startswith("req_date_"))
async def handle_requests_to_users(call: CallbackQuery, db_session: AsyncSession) -> None:
    is_chat = call.data.startswith("req_chat_")
    request_kind = "chat" if is_chat else "date"
    prefix = "req_chat_" if is_chat else "req_date_"
    target_id = _parse_int_suffix(call.data, prefix)
    
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return
        
    caller_id = call.from_user.id 
    
    # 💡 فیکس باگ اول: بررسی موجودی سکه فرستنده قبل از ارسال درخواست
    caller = await crud.get_user_by_tg_id(db_session, caller_id)
    if not caller or caller.coin_balance < 1:
        await call.answer("❌ موجودی سکه شما کافی نیست! برای ارسال درخواست حداقل ۱ سکه نیاز دارید.", show_alert=True)
        return
    
    target_user = await crud.get_user_by_tg_id(db_session, target_id)
    if not target_user:
        await call.answer("❌ کاربر مورد نظر یافت نشد.", show_alert=True)
        return

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    
    if target_user.silent_until and target_user.silent_until > now_utc:
        await call.answer("🔕 این کاربر در حال حاضر در حالت سایلنت قرار دارد و امکان دریافت درخواست را ندارد.", show_alert=True)
        return

    block_check = await db_session.execute(
        select(BlockList).where(
            BlockList.blocker_id == target_id,
            BlockList.blocked_id == caller_id
        )
    )
    if block_check.scalar_one_or_none():
        await call.answer("🚫 امکان ارسال درخواست به این کاربر وجود ندارد (شما بلاک هستید).", show_alert=True)
        return

    req_type_str = "چت 💬" if is_chat else "دیت 💘"
    
    target_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ قبول", callback_data=f"accept_req_{request_kind}_{caller_id}"),
            InlineKeyboardButton(text="❌ رد کردن", callback_data=f"reject_req_{request_kind}_{caller_id}")
        ],
        [InlineKeyboardButton(text="👤 مشاهده پروفایل فرستنده", callback_data=f"view_profile_{caller_id}")],
        [InlineKeyboardButton(text="🚫 بلاک کردن", callback_data=f"block_user_{caller_id}")]
    ])
    
    try:
        await bot.send_message(
            chat_id=target_id,
            text=f"🔔 <b>درخواست جدید:</b>\nیک کاربر به شما درخواست <b>{req_type_str}</b> داده است!",
            parse_mode="HTML",
            reply_markup=target_kb
        )
        await call.answer(f"✅ درخواست {req_type_str} شما با موفقیت برای کاربر ارسال شد.", show_alert=True)
    except Exception:
        await call.answer("⚠️ خطا در ارسال. کاربر ربات را متوقف کرده است.", show_alert=True)


@router.callback_query(F.data.startswith("accept_req_date_") | F.data.startswith("accept_req_chat_"))
async def accept_user_request(call: CallbackQuery, db_session: AsyncSession):
    # تشخیص نوع درخواست (چت یا دیت) 
    is_chat = call.data.startswith("accept_req_chat_")
    prefix = "accept_req_chat_" if is_chat else "accept_req_date_"
    caller_id = _parse_int_suffix(call.data, prefix)
    
    if not caller_id:
        return await call.answer("❌ درخواست نامعتبر.", show_alert=True)

    target_id = call.from_user.id
    req_type_str = "چت 💬" if is_chat else "دیت 💘"
    
    # چک موجودی سکه فرستنده
    caller = await crud.get_user_by_tg_id(db_session, caller_id)
    if not caller or caller.coin_balance < 1:
        await call.answer("❌ موجودی سکه فرستنده کافی نیست. ارتباط برقرار نشد.", show_alert=True)
        
        # 💡 تغییر جدید: ویرایش پیام گیرنده تا دکمه‌ها حذف شوند و وضعیت شفاف باشد
        try:
            await call.message.edit_text(
                f"❌ <b>درخواست لغو شد</b>\n"
                f"شما درخواست {req_type_str} را قبول کردید، اما به دلیل ناکافی بودن موجودی سکه‌ی فرستنده، اتصال برقرار نشد.",
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
        except Exception as e:
            logger.error(f"Unexpected error editing message text for insufficient coins: {e}")

        # اطلاع‌رسانی به فرستنده‌ای که سکه نداشته است
        try:
            await bot.send_message(
                caller_id, 
                f"❌ درخواست {req_type_str} شما پذیرفته شد، اما به دلیل عدم موجودی کافی شما (حداقل ۱ سکه)، اتصال لغو گردید."
            )
        except Exception:
            pass
        return
        
    # کسر سکه در صورت موفقیت
    await crud.process_coin_transaction(db_session, caller, -1, f"هزینه درخواست {req_type_str} پذیرفته‌شده")
    await db_session.commit()

    await call.answer(f"✅ درخواست {req_type_str} قبول شد! در حال اتصال...", show_alert=False)

    try:
        await call.message.edit_text(f"✅ شما درخواست {req_type_str} این کاربر را قبول کردید. در حال اتصال... 🚀")
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error editing message text: {e}")

    try:
        await bot.send_message(caller_id, f"🎉 درخواست {req_type_str} شما توسط کاربر مقابل پذیرفته شد! در حال اتصال... 🚀")
    except Exception:
        pass


    await handle_successful_match(db_session, caller_id, target_id)


@router.callback_query(F.data.startswith("reject_req_"))
async def reject_request(call: CallbackQuery):
    if call.data.startswith("reject_req_chat_"):
        caller_id = _parse_int_suffix(call.data, "reject_req_chat_")
    elif call.data.startswith("reject_req_date_"):
        caller_id = _parse_int_suffix(call.data, "reject_req_date_")
    else:
        # برای احتیاط و پشتیبانی از دکمه‌های قدیمی‌تر (Backward Compatibility)
        caller_id = _parse_int_suffix(call.data, "reject_req_")

    if not caller_id:
        return await call.answer("❌ درخواست نامعتبر.", show_alert=True)

    await call.answer("❌ درخواست رد شد.", show_alert=False)
    try:
        await call.message.edit_text("❌ شما این درخواست را رد کردید. (به فرستنده اطلاعی داده نشد)")
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error editing message text: {e}")
        
async def stale_questionnaire_button(call: CallbackQuery) -> None:
    await call.answer("⚠️ این دیت پایان یافته است و پاسخ شما ثبت نمی‌شود.", show_alert=True)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error editing reply markup: {e}")

@router.callback_query(
    F.data.in_({"approve_chat_yes", "approve_chat_no"}), 
    ~StateFilter(ChatStates.waiting_for_approval)
)
async def stale_approval_button(call: CallbackQuery) -> None:
    await call.answer("⚠️ این درخواست منقضی شده یا دیت پایان یافته است.", show_alert=True)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error editing reply markup: {e}")

@router.callback_query(
    F.data.startswith("vip_age_filter_"),
    ~StateFilter(VIPStates.waiting_for_age_filter)
)
async def stale_vip_button(call: CallbackQuery) -> None:
    await call.answer("⚠️ این منو منقضی شده است. لطفاً مجدداً از منوی اصلی اقدام کنید.", show_alert=True)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error editing reply markup: {e}")

@router.message(ChatStates.typing_direct_message)
async def process_direct_message(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    # FIXED: Cancel condition does not cost the user any coins.
    if message.text == ReplyBtn.CANCEL:
        await state.clear()
        await message.answer("❌ عملیات ارسال دایرکت لغو شد. هیچ سکه‌ای کسر نشد.", reply_markup=get_main_menu_keyboard())
        return

    if not message.text:
        await message.reply("⚠️ لطفاً فقط پیام متنی ارسال کنید.")
        return

    data = await state.get_data()
    target_id = data.get("target_direct_id")
    caller_id = message.from_user.id

    if not target_id:
        await state.clear()
        return

    # Re-check if the user blocked the caller while they were typing
    block_check = await db_session.execute(
        select(BlockList).where(
            BlockList.blocker_id == target_id,
            BlockList.blocked_id == caller_id
        )
    )
    if block_check.scalar_one_or_none():
        await message.reply("🚫 شما توسط این کاربر مسدود شده‌اید.")
        await state.clear()
        return

    caller = await crud.get_user_by_tg_id(db_session, caller_id)
    if not caller or caller.coin_balance < 1:
        await message.reply("❌ سکه‌های شما برای ارسال پیام کافی نیست!")
        await state.clear()
        return

    # FIXED: Deduct 1 coin NOW, right before attempting delivery.
    await crud.process_coin_transaction(db_session, caller, -1, "هزینه ارسال پیام دایرکت")
    await db_session.commit()

    target_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 پاسخ دادن", callback_data=f"req_direct_{caller_id}")],
        [
            InlineKeyboardButton(text="🚫 بلاک کردن", callback_data=f"block_user_{caller_id}"),
            InlineKeyboardButton(text="🚩 گزارش تخلف", callback_data=f"report_user_{caller_id}")
        ]
    ])

    try:
        await bot.send_message(
            chat_id=target_id,
            text=f"📩 <b>یک پیام دایرکت ناشناس دریافت کردید:</b>\n\n{html.escape(message.text)}",
            parse_mode="HTML",
            reply_markup=target_kb
        )
        await message.reply("✅ پیام شما با موفقیت به کاربر تحویل داده شد و ۱ سکه کسر گردید.", reply_markup=get_main_menu_keyboard())
    except Exception:
        # FIXED: Delivery failed! Refund the coin securely.
        await crud.process_coin_transaction(db_session, caller, 1, "برگشت هزینه به دلیل خطای تحویل پیام دایرکت")
        await db_session.commit()
        await message.reply("⚠️ خطایی رخ داد (احتمالاً کاربر ربات را متوقف کرده است). ۱ سکه به شما برگشت داده شد.", reply_markup=get_main_menu_keyboard())

    await state.clear()