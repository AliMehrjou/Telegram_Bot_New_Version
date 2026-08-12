from __future__ import annotations

import html
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import or_, and_
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command

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
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
from matching_bot_project.bot.core.loader import bot
from matching_bot_project.bot.handlers.matching import handle_successful_match
from matching_bot_project.bot.handlers.anonymous_chat import activate_anonymous_chat_session
from matching_bot_project.bot.handlers.start import auto_heal_ghost_state  # ⭐ Import smart healer
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.constants import ReplyBtn, Messages as SystemMsg
from matching_bot_project.bot.core.formatters import build_unified_profile_card, chunk_html_text, get_pagination_row
from matching_bot_project.bot.core.loader import bot, dating_scheduler, dp, redis_client
from matching_bot_project.bot.handlers.vip import _is_vip_active
from matching_bot_project.bot.keyboards.inline import (
    get_end_chat_confirm_keyboard,
    get_end_date_confirm_keyboard,
    get_report_reasons_keyboard,
    get_user_action_keyboard,
)
from matching_bot_project.bot.keyboards.reply import get_cancel_keyboard, get_main_menu_keyboard, get_chat_phase_keyboard
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

def get_user_state(user_id: int) -> FSMContext:
    """Helper to get FSMContext for any user by ID (used for cross-user zombie healing)."""
    return FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
    )

async def _preserve_current_state(state: FSMContext) -> None:
    """ذخیره استیت فعلی کاربر قبل از ورود به یک فرآیند موقت (مثل ریپورت یا دایرکت)."""
    current_state = await state.get_state()
    await state.update_data(__prev_state__=current_state)

async def _restore_previous_state(state: FSMContext, db_session: AsyncSession, tg_id: int) -> str | None:
    """بازیابی استیت قبلی کاربر پس از ریپورت، با گارد امنیتی ضد زامبی"""
    data = await state.get_data()
    previous_state = data.pop("__prev_state__", None)
    
    data.pop("target_direct_id", None)
    data.pop("reported_id", None)
    data.pop("reason_code", None)
    
    # 🛡️ گارد امنیتی ضد زامبی
    is_pipeline = previous_state and any(p in previous_state.lower() for p in ["chat", "matching", "questionnaire"])
    if is_pipeline:
        active_match = await crud.get_active_match(db_session, tg_id)
        if not active_match:
            await state.clear()
            return None

    await state.set_data(data)
    if previous_state:
        await state.set_state(previous_state)
    return previous_state

def _parse_int_suffix(data: str, prefix: str) -> Optional[int]:
    try:
        return int(data.removeprefix(prefix))
    except ValueError:
        return None

def _build_profile_card(user, compatibility: Optional[int] = None) -> str:
    return build_unified_profile_card(user, is_own_profile=False, compatibility=compatibility)

async def _send_profile_card(target_chat_id: int, user, action_kb: InlineKeyboardMarkup) -> None:
    profile_card = _build_profile_card(user)
    pages = chunk_html_text(profile_card, max_length=950)
    photo_id = getattr(user, 'profile_photo_file_id', None)
    
    inline_rows = list(action_kb.inline_keyboard) if action_kb else []
    if len(pages) > 1:
        nav_row = get_pagination_row(target_id=user.tg_id, current_page=0, total_pages=len(pages), is_own=False)
        inline_rows.insert(0, nav_row)
        
    final_kb = InlineKeyboardMarkup(inline_keyboard=inline_rows)
    
    try:
        if photo_id:
            await bot.send_photo(chat_id=target_chat_id, photo=photo_id, caption=pages[0], parse_mode="HTML", reply_markup=final_kb)
        else:
            await bot.send_message(chat_id=target_chat_id, text=pages[0], parse_mode="HTML", reply_markup=final_kb)
            
        profile_voice = getattr(user, 'profile_voice_file_id', None)
        if profile_voice:
            await bot.send_voice(chat_id=target_chat_id, voice=profile_voice, caption="🎵 <b>آهنگ/وویس پروفایل</b>", parse_mode="HTML")
    except Exception as exc:
        logger.error("Failed to send profile message to chat %s: %s", target_chat_id, exc)
    
# ─────────────────────────────────────────────────────────────────────────────
# Section 1 – View Profile
# ─────────────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.regexp(r"^view_profile_\d+$"))
async def view_partner_profile(call: CallbackQuery, db_session: AsyncSession) -> None:
    # ...
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

    caller_active_match = await crud.get_active_match(db_session, call.from_user.id)
    action_kb = get_user_action_keyboard(target_id, is_blocked=is_blocked, is_friend=already_friend, in_active_match=(caller_active_match is not None))

    is_target_vip = _is_vip_active(user)

    if is_target_vip and call.from_user.id != target_id:
        key = f"user:{target_id}:viewers"
        await redis_client.zadd(key, {str(call.from_user.id): time.time()})
        await redis_client.expire(key, 604800)

    await _send_profile_card(target_chat_id=call.from_user.id, user=user, action_kb=action_kb)
    await call.answer()

@router.callback_query(F.data.startswith("view_profile_pub_"))
async def view_partner_profile_by_pub_id(call: CallbackQuery, db_session: AsyncSession) -> None:
    pub_id = call.data.removeprefix("view_profile_pub_")
    
    user = await crud.get_user_by_public_id(db_session, pub_id)
    if not user:
        await call.answer("❌ پروفایل کاربر یافت نشد.", show_alert=True)
        return

    target_id = user.tg_id
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

    in_active_match = await crud.is_active_match_partner(db_session, call.from_user.id, target_id)
    action_kb = get_user_action_keyboard(target_id, is_blocked=is_blocked, is_friend=already_friend, in_active_match=in_active_match)

    is_target_vip = _is_vip_active(user)

    if is_target_vip and call.from_user.id != target_id:
        key = f"user:{target_id}:viewers"
        await redis_client.zadd(key, {str(call.from_user.id): time.time()})
        await redis_client.expire(key, 604800)

    await _send_profile_card(target_chat_id=call.from_user.id, user=user, action_kb=action_kb)
    await call.answer()

@router.message(F.text == ReplyBtn.PHASE_USER_PROFILE)
async def view_partner_profile_from_reply_btn(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    """
    🛡️ این هندلر عمداً به FSM دست نمی‌زنه (فقط از دیتابیس می‌خونه) تا هیچ‌وقت باعث قطع/یک‌طرفه‌شدن چت فعال نشه.
    """
    active_match = await crud.get_active_match(db_session, message.from_user.id)
    if not active_match:
        await message.answer("⚠️ شما در حال حاضر در دیت یا چت فعالی نیستید.")
        return

    partner_id = active_match.user_two_id if active_match.user_one_id == message.from_user.id else active_match.user_one_id

    user = await crud.get_user_by_tg_id(db_session, partner_id)
    if not user:
        await message.answer("❌ اطلاعات پارتنر یافت نشد.")
        return

    block_result = await db_session.execute(
        select(BlockList).where(
            BlockList.blocker_id == message.from_user.id,
            BlockList.blocked_id == user.tg_id,
        )
    )
    is_blocked = block_result.scalar_one_or_none() is not None
    
    try:
        already_friend = await crud.is_friend(db_session, message.from_user.id, user.tg_id)
    except Exception:
        already_friend = False

    action_kb = get_user_action_keyboard(
        user.tg_id, 
        is_blocked=is_blocked, 
        is_friend=already_friend, 
        in_active_match=True
    )
    
    await _send_profile_card(target_chat_id=message.from_user.id, user=user, action_kb=action_kb)

# ─────────────────────────────────────────────────────────────────────────────
# Section 2 – End Date Early (and Extracted Helpers)
# ─────────────────────────────────────────────────────────────────────────────

async def execute_chat_termination(
    db_session: AsyncSession,
    match_id: int,
    caller_id: int,
) -> bool:
    """Race-condition-free match termination (manual, via UI button click)."""
    return await dating_scheduler.terminate_match_unified(
        match_id,
        caller_id=caller_id,
        caller_text=_DATE_CANCELLED_TEXT,
        partner_text="طرف مقابل دیت را پایان داد.",
        manual_parse_mode=None,
        session=db_session,
        commit=True,
    )

async def execute_chat_termination_no_commit(
    db_session: AsyncSession,
    match_id: int,
    caller_id: int,
) -> bool:
    """Same as execute_chat_termination but does NOT commit the DB session."""
    return await dating_scheduler.terminate_match_unified(
        match_id,
        caller_id=caller_id,
        caller_text=_DATE_CANCELLED_TEXT,
        partner_text="طرف مقابل دیت را پایان داد.",
        manual_parse_mode=None,
        session=db_session,
        commit=False,
    )

@router.message(F.text == ReplyBtn.END_DATE)
async def request_end_date_confirm(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    active_match = await crud.get_active_match(db_session, message.from_user.id)
    if not active_match:
        # 🐛 فیکس: اگر مچ در دیتابیس مرده، استیت هر دو طرف رو قطعی پاک کن!
        fsm_data = await state.get_data()
        match_id = fsm_data.get("match_history_id")
        if match_id:
            from matching_bot_project.database.models.models import MatchHistory
            match_row = await db_session.get(MatchHistory, match_id)
            if match_row:
                partner_id = match_row.user_two_id if match_row.user_one_id == message.from_user.id else match_row.user_one_id
                partner_ctx = get_user_state(partner_id)
                await partner_ctx.set_state(None)
                await partner_ctx.clear()
        
        await state.clear()
        await message.answer("⚠️ دیت فعالی یافت نشد یا قبلاً پایان یافته است.", reply_markup=get_main_menu_keyboard())
        return

    await message.answer(
        "⚠️ آیا مطمئن هستید که می‌خواهید دیت را پایان دهید؟\nاین عمل قابل بازگشت نیست.",
        reply_markup=get_end_date_confirm_keyboard(),
    )

@router.callback_query(F.data == "confirm_end_date")
async def confirm_end_date(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    active_match = await crud.get_active_match(db_session, call.from_user.id)
    
    fsm_data = await state.get_data()
    match_id = fsm_data.get("match_history_id")
    partner_id = None
    
    if match_id:
        from matching_bot_project.database.models.models import MatchHistory
        match_row = await db_session.get(MatchHistory, match_id)
        if match_row:
            partner_id = match_row.user_two_id if match_row.user_one_id == call.from_user.id else match_row.user_one_id

    if not active_match:
        if partner_id:
            partner_ctx = get_user_state(partner_id)
            await partner_ctx.set_state(None)
            await partner_ctx.clear()
            
        await state.clear()
        await call.answer("دیت فعالی یافت نشد.", show_alert=True)
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await call.message.answer("این دیت قبلاً پایان یافته است. به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())
        return
        
    is_locked = await redis_client.exists(f"anti_skip_lock:{active_match.id}")
    if is_locked:
        await call.answer("چند لحظه صبر کن 🙂", show_alert=False)
        return

    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
        
    success = await execute_chat_termination(db_session, active_match.id, call.from_user.id)
    
    if success:
        await state.clear()
        
        # استخراج تگ‌های دو طرف از دیتابیس
        caller = await crud.get_user_by_tg_id(db_session, call.from_user.id)
        caller_tag = f"<code>{caller.public_id}</code>" if caller and caller.public_id else "ناشناس"

        partner_tag = "ناشناس"
        if partner_id:
            partner = await crud.get_user_by_tg_id(db_session, partner_id)
            if partner and partner.public_id:
                partner_tag = f"<code>{partner.public_id}</code>"
        
        # اطلاع‌رسانی به پارتنر
        if partner_id:
            partner_ctx = get_user_state(partner_id)
            await partner_ctx.set_state(None)
            await partner_ctx.clear()
            
            try:
                partner_text = SystemMsg.CHAT_ENDED_BY_PARTNER.format(user_tag=caller_tag, msg_token=active_match.id or "")
                await bot.send_message(
                    chat_id=partner_id,
                    text=partner_text,
                    reply_markup=get_main_menu_keyboard(),
                    parse_mode="HTML"
                )
            except Exception:
                pass
        
        # پیام پایان برای خود کاربر
        caller_text = SystemMsg.CHAT_ENDED_BY_YOU.format(user_tag=partner_tag, msg_token=active_match.id or "")
        await call.message.answer(caller_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
        
        # 🌟 بررسی پیام‌های خوانده‌نشده برای هر دو کاربر
        await crud.notify_missed_messages(db_session, call.from_user.id)
        if partner_id:
            await crud.notify_missed_messages(db_session, partner_id)
            
    else:
        await state.clear()
        if partner_id:
            partner_ctx = get_user_state(partner_id)
            await partner_ctx.set_state(None)
            await partner_ctx.clear()
        await call.message.answer("این دیت قبلاً پایان یافته است. به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())



@router.callback_query(F.data == "cancel_end_date")
async def cancel_end_date(call: CallbackQuery) -> None:
    await call.answer("❌ لغو شد. دیت ادامه دارد.")
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass

@router.message(F.text == ReplyBtn.END_CHAT)
async def request_end_chat_confirm(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    active_match = await crud.get_active_match(db_session, message.from_user.id)
    if not active_match:
        # 🐛 فیکس: جلوگیری از زامبی شدن در پایان چت‌های بسته شده
        fsm_data = await state.get_data()
        match_id = fsm_data.get("match_history_id")
        if match_id:
            from matching_bot_project.database.models.models import MatchHistory
            match_row = await db_session.get(MatchHistory, match_id)
            if match_row:
                partner_id = match_row.user_two_id if match_row.user_one_id == message.from_user.id else match_row.user_one_id
                partner_ctx = get_user_state(partner_id)
                await partner_ctx.set_state(None)
                await partner_ctx.clear()
                
        await state.clear()
        await message.answer("⚠️ چت فعالی یافت نشد یا قبلاً پایان یافته است.", reply_markup=get_main_menu_keyboard())
        return

    await message.answer(
        "⚠️ آیا مطمئن هستید که می‌خواهید چت را پایان دهید؟",
        reply_markup=get_end_chat_confirm_keyboard(),
    )

@router.callback_query(ChatStates.anonymous_chat_active, F.data == "confirm_end_chat")
async def confirm_end_chat(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    active_match = await crud.get_active_match(db_session, call.from_user.id)
    
    fsm_data = await state.get_data()
    match_id = fsm_data.get("match_history_id")
    partner_id = None
    
    if match_id:
        from matching_bot_project.database.models.models import MatchHistory
        match_row = await db_session.get(MatchHistory, match_id)
        if match_row:
            partner_id = match_row.user_two_id if match_row.user_one_id == call.from_user.id else match_row.user_one_id

    if not active_match:
        if partner_id:
            partner_ctx = get_user_state(partner_id)
            await partner_ctx.set_state(None)
            await partner_ctx.clear()
            
        await state.clear()
        await call.answer("چت فعالی یافت نشد.", show_alert=True)
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await call.message.answer("به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())
        return

    match_history_id = active_match.id
    
    is_locked = await redis_client.exists(f"anti_skip_lock:{match_history_id}")
    if is_locked:
        await call.answer("چند لحظه صبر کن 🙂", show_alert=False)
        return

    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
        
    success = await execute_chat_termination(db_session, match_history_id, call.from_user.id)
    
    if success:
        await state.clear()
        
        # استخراج تگ‌های دو طرف از دیتابیس
        caller = await crud.get_user_by_tg_id(db_session, call.from_user.id)
        caller_tag = f"<code>{caller.public_id}</code>" if caller and caller.public_id else "ناشناس"

        partner_tag = "ناشناس"
        if partner_id:
            partner = await crud.get_user_by_tg_id(db_session, partner_id)
            if partner and partner.public_id:
                partner_tag = f"<code>{partner.public_id}</code>"
        
        if partner_id:
            partner_ctx = get_user_state(partner_id)
            await partner_ctx.set_state(None)
            await partner_ctx.clear()
            
            try:
                partner_text = SystemMsg.CHAT_ENDED_BY_PARTNER.format(user_tag=caller_tag, msg_token=match_history_id or "")
                await bot.send_message(
                    chat_id=partner_id,
                    text=partner_text,
                    reply_markup=get_main_menu_keyboard(),
                    parse_mode="HTML"
                )
            except Exception:
                pass
        
        caller_text = SystemMsg.CHAT_ENDED_BY_YOU.format(user_tag=partner_tag, msg_token=match_history_id or "")
        await call.message.answer(caller_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
        
        # 🌟 بررسی پیام‌های خوانده‌نشده برای هر دو کاربر
        await crud.notify_missed_messages(db_session, call.from_user.id)
        if partner_id:
            await crud.notify_missed_messages(db_session, partner_id)
            
    else:
        await state.clear()
        if partner_id:
            partner_ctx = get_user_state(partner_id)
            await partner_ctx.set_state(None)
            await partner_ctx.clear()
        await call.message.answer("این چت قبلاً پایان یافته است. به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())

    

@router.callback_query(F.data == "cancel_end_chat")
async def cancel_end_chat(call: CallbackQuery) -> None:
    await call.answer("❌ لغو شد. چت ادامه دارد.")
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("end_date_"))
async def end_date_early(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    if call.data.startswith("end_date_early_"):
        match_id = _parse_int_suffix(call.data, "end_date_early_")
    else:
        match_id = _parse_int_suffix(call.data, "end_date_")
        
    if match_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    # --- بررسی قفل ۵ ثانیه‌ای ضد-اسکیپ ---
    is_locked = await redis_client.exists(f"anti_skip_lock:{match_id}")
    if is_locked:
        await call.answer("چند لحظه صبر کن 🙂", show_alert=True)
        return
    # -----------------------------------

    from matching_bot_project.database.models.models import MatchHistory
    match_row = await db_session.get(MatchHistory, match_id)
    partner_id = None
    if match_row:
        partner_id = match_row.user_two_id if match_row.user_one_id == call.from_user.id else match_row.user_one_id

    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    success = await execute_chat_termination(db_session, match_id, call.from_user.id)
    
    if success:
        await state.clear()
        
        # استخراج تگ‌های دو طرف از دیتابیس
        caller = await crud.get_user_by_tg_id(db_session, call.from_user.id)
        caller_tag = f"<code>{caller.public_id}</code>" if caller and caller.public_id else "ناشناس"

        partner_tag = "ناشناس"
        if partner_id:
            partner = await crud.get_user_by_tg_id(db_session, partner_id)
            if partner and partner.public_id:
                partner_tag = f"<code>{partner.public_id}</code>"
        
        # --- پاکسازی استیت پارتنر و ارسال مستقیم پیام خروج ---
        if partner_id:
            partner_ctx = get_user_state(partner_id)
            await partner_ctx.set_state(None)
            await partner_ctx.clear()
            
            try:
                partner_text = SystemMsg.CHAT_ENDED_BY_PARTNER.format(user_tag=caller_tag, msg_token=match_id or "")
                await bot.send_message(
                    chat_id=partner_id,
                    text=partner_text,
                    reply_markup=get_main_menu_keyboard(),
                    parse_mode="HTML"
                )
            except Exception:
                pass
        
        caller_text = SystemMsg.CHAT_ENDED_BY_YOU.format(user_tag=partner_tag, msg_token=match_id or "")
        await call.answer("دیت لغو شد.", show_alert=False)
        await call.message.answer(caller_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
        
        # 🌟 بررسی پیام‌های خوانده‌نشده برای هر دو کاربر
        await crud.notify_missed_messages(db_session, call.from_user.id)
        if partner_id:
            await crud.notify_missed_messages(db_session, partner_id)
            
    else:
        await state.clear()
        if partner_id:
            partner_ctx = get_user_state(partner_id)
            await partner_ctx.set_state(None)
            await partner_ctx.clear()
        await call.message.answer(
            "⚠️ این دیت قبلا لغو شده یا وجود ندارد.", 
            reply_markup=get_main_menu_keyboard()
        )



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
            await execute_chat_termination(db_session, active_match.id, blocker_id)
            
            # --- پاکسازی استیت و ردیس برای هر دو نفر ---
            partner_ctx = get_user_state(blocked_id)
            await partner_ctx.set_state(None)
            await partner_ctx.clear()
            
            blocker_ctx = get_user_state(blocker_id)
            await blocker_ctx.set_state(None)
            await blocker_ctx.clear()

            await redis_client.delete(f"user:state:{blocked_id}")
            await redis_client.delete(f"user:state:{blocker_id}")
            
            # 🔔 اطلاع‌رسانی به پارتنر بلاک‌شده
            try:

                await bot.send_message(
                    chat_id=blocked_id,
                    text="🚫 <b>چت پایان یافت!</b>\nطرف مقابل گفتگو را مسدود کرد. به منوی اصلی بازگشتید.",
                    parse_mode="HTML",
                    reply_markup=get_main_menu_keyboard()
                )
            except Exception:
                pass
            # -------------------------------------------

        return True, "🚫 کاربر مسدود شد و دیگر به شما متصل نخواهد شد."
    except IntegrityError:
        await db_session.rollback()
        return False, "⚠️ این کاربر قبلاً مسدود شده است."
    except Exception as exc:
        await db_session.rollback()
        logger.error("Unexpected error while user %s attempted to block user %s: %s", blocker_id, blocked_id, exc)
        return False, "❌ خطای سرور. لطفاً دوباره تلاش کنید."  

async def execute_user_blocking_no_commit(db_session: AsyncSession, blocker_id: int, blocked_id: int) -> tuple[bool, str]:
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
            await execute_chat_termination_no_commit(db_session, active_match.id, blocker_id)
            
            # --- پاکسازی استیت و ردیس برای هر دو نفر (no_commit) ---
            partner_ctx = get_user_state(blocked_id)
            await partner_ctx.set_state(None)
            await partner_ctx.clear()
            
            blocker_ctx = get_user_state(blocker_id)
            await blocker_ctx.set_state(None)
            await blocker_ctx.clear()

            await redis_client.delete(f"user:state:{blocked_id}")
            await redis_client.delete(f"user:state:{blocker_id}")
            
            # 🔔 اطلاع‌رسانی به پارتنر بلاک‌شده
            try:

                await bot.send_message(
                    chat_id=blocked_id,
                    text="🚫 <b>چت پایان یافت!</b>\nطرف مقابل گفتگو را مسدود کرد. به منوی اصلی بازگشتید.",
                    parse_mode="HTML",
                    reply_markup=get_main_menu_keyboard()
                )
            except Exception:
                pass
            # --------------------------------------------------------
            
    except Exception as exc:
        logger.error("Error checking/terminating active match during no-commit block %s -> %s: %s", blocker_id, blocked_id, exc)

    return True, "🚫 کاربر مسدود شد و دیگر به شما متصل نخواهد شد."


@router.callback_query(F.data.startswith("block_user_"))
async def block_user(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    target_id = _parse_int_suffix(call.data, "block_user_")
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    # ✅ رفع باگ: fromuser به from_user تغییر یافت
    caller_id = call.from_user.id  
    
    # 🌟 بررسی اینکه آیا مسدودکننده با این شخص در چت/دیت فعال است؟
    match_query = await db_session.execute(
        select(MatchHistory).where(
            MatchHistory.is_active == True,
            or_(
                and_(MatchHistory.user_one_id == caller_id, MatchHistory.user_two_id == target_id),
                and_(MatchHistory.user_one_id == target_id, MatchHistory.user_two_id == caller_id)
            )
        )
    )
    active_match = match_query.scalar_one_or_none()

    success, msg = await execute_user_blocking(db_session, caller_id, target_id)

    if success:
        # 🚀 پاکسازی استیت فقط در صورتی که کاربر فعلیِ دیت را بلاک کرده باشد
        if active_match:
            await state.clear()
        
        limit_key = f"user:got_blocked_today:{target_id}"
        blocks_count = await redis_client.incr(limit_key)
        
        if blocks_count == 1:
            now = datetime.now(timezone.utc)
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seconds_to_midnight = int((midnight - now).total_seconds())
            await redis_client.expire(limit_key, seconds_to_midnight)

        if blocks_count >= 3:
            await redis_client.setex(f"user:block_cooldown:{target_id}", 86400, "1")

        # اگر کاربر پارتنر دیتش را بلاک کرده بود
        if active_match:
            from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
            try:
                await call.message.delete()
            except Exception:
                pass
            await call.message.answer(
                "🚫 کاربر مسدود شد و دیت/چت پایان یافت. به منوی اصلی بازگشتید.",
                reply_markup=get_main_menu_keyboard()
            )
            await call.answer()
            return

        # آپدیت دکمه (تبدیل به آنبلاک) در پروفایل‌های عمومی
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
            except Exception:
                pass

    await call.answer(msg, show_alert=True)


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

    # بررسی لایک تکراری
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

    # ثبت لایک جدید در دیتابیس
    await crud.save_like(db_session, caller_id, target_id, is_pass=False)
    await db_session.commit()

    # محاسبه مجموع لایک‌های دریافت‌شده کاربر هدف
    total_likes = await crud.get_received_like_count(db_session, target_id)

    # سیستم پاداش: هر ۲۰ لایک ۵ سکه جایزه
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
                
    # دریافت اطلاعات کاربری که لایک کرده برای استخراج تگ/شناسه
    caller = await crud.get_user_by_tg_id(db_session, caller_id)
    user_tag = f"<code>{caller.public_id}</code>" if caller and caller.public_id else "ناشناس"

    # ارسال نوتیفیکیشن لایک به کاربر هدف
    try:
        like_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 مشاهده پروفایل لایک‌کننده", callback_data=f"view_profile_{caller_id}")]
        ])
        await bot.send_message(
            chat_id=target_id,
            text=SystemMsg.LIKE_RECEIVED.format(user_tag=user_tag),
            reply_markup=like_kb,
            parse_mode="HTML"
        )
    except Exception:
        pass

    # نمایش الرت موفقیت‌آمیز بودن لایک برای فرستنده
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
    else:
        await call.answer("⚠️ این کاربر در لیست دوستان شما قرار ندارد.", show_alert=True)

@router.callback_query(F.data.startswith("report_user_"))
async def show_report_reasons(call: CallbackQuery, db_session: AsyncSession) -> None:
    reported_id_str = call.data.removeprefix("report_user_")
    if not reported_id_str.isdigit():
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return
        
    reported_id = int(reported_id_str)

    # 🛡️ جلوگیری از ریپورت پروفایل خارجی در حین چت/دیت فعال
    active_match = await crud.get_active_match(db_session, call.from_user.id)
    if active_match:
        # اگر کسی که داره ریپورت میشه، همون پارتنر داخل دیت نیست، جلوش رو بگیر
        partner_id = active_match.user_two_id if active_match.user_one_id == call.from_user.id else active_match.user_one_id
        if partner_id != reported_id:
            return await call.answer(
                "⚠️ شما در حال حاضر در یک چت/دیت فعال هستید و نمی‌توانید پروفایل شخص دیگری را گزارش کنید.\n"
                "برای گزارش پارتنرِ فعلی خود، لطفاً از دکمه «گزارش» داخل خود چت استفاده کنید.", 
                show_alert=True
            )

    await call.answer()
    await call.message.answer(
        "لطفاً دلیل گزارش را انتخاب کنید:",
        reply_markup=get_report_reasons_keyboard(reported_id),
    )


@router.callback_query(F.data.startswith("report_reason_"))
async def process_report_reason(call: CallbackQuery, state: FSMContext) -> None:
    parts = call.data.removeprefix("report_reason_").split("_", 1)
    
    if len(parts) != 2 or not parts[0].isdigit():
        await call.answer("❌ خطای پردازش.", show_alert=True)
        return
        
    reported_id = int(parts[0])
    reason_code = parts[1]

    await _preserve_current_state(state)
    await state.update_data(reported_id=reported_id, reason_code=reason_code)
    await state.set_state(ReportStates.waiting_for_report_description)

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
    
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 پاسخ به شاکی", callback_data=f"admin_reply_{reporter_id}")],
        [InlineKeyboardButton(text="⛔️ بن کردن متخلف", callback_data=f"admin_ban_{reported_id}")]
    ])

    for admin_id in settings.parsed_admin_ids:
        try:
            if evidence_message:
                await bot.copy_message(
                    chat_id=admin_id,
                    from_chat_id=evidence_message.chat.id,
                    message_id=evidence_message.message_id,
                    caption=" مدرک ضمیمه شده گزارش 👆"
                )
            await bot.send_message(chat_id=admin_id, text=admin_text, reply_markup=admin_kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send report notification to admin {admin_id}: {e}")

# استفاده از فیلترهای صریح و استاندارد aiogram به جای content_type
@router.message(ReportStates.waiting_for_report_description, F.text | F.photo | F.document)
async def handle_report_evidence(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    data = await state.get_data()
    reported_id = data.get("reported_id")
    reason_code = data.get("reason_code")
    
    if not reported_id or not reason_code:
        await _restore_previous_state(state, db_session, message.from_user.id)
        return await message.answer("⚠️ اطلاعات گزارش منقضی شده است. لطفاً دوباره تلاش کنید.")

    # استخراج هوشمند متن یا کپشن عکس
    description = message.text or message.caption or "بدون متن (فقط تصویر/فایل)"
    evidence_msg = message if (message.photo or message.document or message.forward_date) else None

    # ثبت گزارش در دیتابیس و ارسال به ادمین‌ها
    await _submit_report(
        reporter_id=message.from_user.id, 
        reported_id=reported_id, 
        reason_code=reason_code, 
        description=description, 
        db_session=db_session,
        evidence_message=evidence_msg
    )
    
    # بررسی اینکه آیا کاربر با شخص گزارش‌شده در دیت/چت فعال قرار دارد
    reporter_id = message.from_user.id
    match_query = await db_session.execute(
        select(MatchHistory).where(
            MatchHistory.is_active == True,
            or_(
                and_(MatchHistory.user_one_id == reporter_id, MatchHistory.user_two_id == reported_id),
                and_(MatchHistory.user_one_id == reported_id, MatchHistory.user_two_id == reporter_id)
            )
        )
    )
    active_match = match_query.scalar_one_or_none()

    if active_match:
        # بستن خودکار چت/دیت به دلیل ثبت گزارش
        await execute_chat_termination(db_session, active_match.id, reporter_id)
        await state.clear()
        
        partner_ctx = get_user_state(reported_id)
        await partner_ctx.set_state(None)
        await partner_ctx.clear()
        
        # 🔔 اطلاع‌رسانی به پارتنر ریپورت‌شده
        try:
            from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
            from matching_bot_project.bot.core.loader import bot
            await bot.send_message(
                chat_id=reported_id,
                text="⚠️ <b>چت پایان یافت!</b>\nپارتنر شما به دلیل ثبت گزارش، گفتگو را قطع کرد. به منوی اصلی بازگشتید.",
                parse_mode="HTML",
                reply_markup=get_main_menu_keyboard()
            )
        except Exception:
            pass

        if reason_code == "bot_fake":
            await message.answer(
                "✅ گزارش شما مبنی بر فیک بودن این حساب ثبت شد و گفتگو به صورت خودکار پایان یافت. ادمین‌ها به زودی بررسی خواهند کرد.", 
                reply_markup=get_main_menu_keyboard()
            )
        else:
            await message.answer(
                "✅ گزارش شما ثبت شد و گفتگو/دیت به صورت خودکار پایان یافت. ادمین‌ها در اسرع وقت این مورد را بررسی خواهند کرد.", 
                reply_markup=get_main_menu_keyboard()
            )
        return

    # 🛡️ آپدیت ورودی‌های ریستور (اگر کاربر از داخل پروفایل و خارج از دیت گزارش داده بود)
    prev_state = await _restore_previous_state(state, db_session, message.from_user.id)
    
    # تنظیم کیبورد مناسب بر اساس استیت قبلی
    from matching_bot_project.bot.keyboards.reply import get_chat_phase_keyboard, get_main_menu_keyboard
    if prev_state == ChatStates.anonymous_chat_active.state:
        kb = get_chat_phase_keyboard()
    else:
        kb = get_main_menu_keyboard()
    
    if reason_code == "bot_fake":
        await message.answer("✅ گزارش شما مبنی بر فیک بودن این حساب ثبت شد. ادمین‌ها به زودی این مورد را بررسی خواهند کرد.", reply_markup=kb)
    else:
        await message.answer("✅ گزارش شما به همراه مدارک با موفقیت ثبت شد و در اسرع وقت بررسی خواهد شد. با تشکر از همکاری شما.", reply_markup=kb)


@router.callback_query(F.data == "report_cancel")
async def cancel_report_from_profile(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    await call.answer("❌ گزارش لغو شد.")
    
    # 🛡️ آپدیت ورودی‌های ریستور
    prev_state = await _restore_previous_state(state, db_session, call.from_user.id)
    
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass

    if prev_state == ChatStates.anonymous_chat_active.state:
        await call.message.answer("❌ گزارش لغو شد. شما به چت ناشناس برگشتید. 🟢", reply_markup=get_chat_phase_keyboard())
    else:
        await call.message.answer("❌ گزارش لغو شد.", reply_markup=get_main_menu_keyboard())


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

# ─────────────────────────────────────────────────────────────────────────────
# Section 3B – Blocked Users List
# ─────────────────────────────────────────────────────────────────────────────

_BLOCKED_PER_PAGE = 5

def _build_blocked_list_text(blocked_users: list, total: int, page: int, total_pages: int) -> str:
    if not blocked_users:
        return "🚫 شما در حال حاضر هیچ کاربری را مسدود نکرده‌اید."

    lines = [f"🚫 <b>کاربران بلاک‌شده شما ({total} نفر)</b>\n"]
    for u in blocked_users:
        # فقط نمایش آیدی عمومی برای حفظ حریم خصوصی
        lines.append(f"• <code>{u.public_id}</code>")

    if total_pages > 1:
        lines.append(f"\nصفحه {page + 1} از {total_pages}")

    return "\n".join(lines)

def _build_blocked_list_keyboard(blocked_users: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for u in blocked_users:
        rows.append([
            InlineKeyboardButton(text=f"👤 {u.public_id}", callback_data=f"view_profile_{u.tg_id}"),
            InlineKeyboardButton(text="🔓 آنبلاک", callback_data=f"unblock_from_list_{u.tg_id}_{page}"),
        ])

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"blocked_page_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"blocked_page_{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_blocked_list(db_session: AsyncSession, tg_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
    offset = page * _BLOCKED_PER_PAGE
    blocked_users, total = await crud.get_blocked_users(db_session, tg_id, limit=_BLOCKED_PER_PAGE, offset=offset)
    total_pages = max(1, math.ceil(total / _BLOCKED_PER_PAGE))
    text = _build_blocked_list_text(blocked_users, total, page, total_pages)
    kb = _build_blocked_list_keyboard(blocked_users, page, total_pages)
    return text, kb

@router.message(F.text == ReplyBtn.BLOCKED_USERS)
async def show_blocked_users(message: Message, db_session: AsyncSession) -> None:
    text, kb = await _render_blocked_list(db_session, message.from_user.id, page=0)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("blocked_page_"))
async def paginate_blocked_users(call: CallbackQuery, db_session: AsyncSession) -> None:
    page = _parse_int_suffix(call.data, "blocked_page_")
    if page is None or page < 0:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    text, kb = await _render_blocked_list(db_session, call.from_user.id, page=page)
    await call.answer()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("unblock_from_list_"))
async def unblock_user_from_list(call: CallbackQuery, db_session: AsyncSession) -> None:
    payload = call.data.removeprefix("unblock_from_list_")
    try:
        target_str, page_str = payload.rsplit("_", 1)
        target_id = int(target_str)
        page = int(page_str)
    except ValueError:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    caller_id = call.from_user.id

    await db_session.execute(
        delete(BlockList).where(
            BlockList.blocker_id == caller_id,
            BlockList.blocked_id == target_id,
        )
    )
    await db_session.commit()

    try:
        await redis_client.srem(f"user:{caller_id}:blocks", str(target_id))
    except Exception as exc:
        logger.warning("Could not sync unblock to Redis for %s -> %s: %s", caller_id, target_id, exc)

    await call.answer("🔓 کاربر با موفقیت از لیست سیاه شما خارج شد.", show_alert=False)

    _, total_after = await crud.get_blocked_users(db_session, caller_id, limit=1, offset=page * _BLOCKED_PER_PAGE)
    total_pages_after = max(1, math.ceil(total_after / _BLOCKED_PER_PAGE))
    effective_page = min(page, total_pages_after - 1)

    text, kb = await _render_blocked_list(db_session, caller_id, page=effective_page)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("req_chat_") | F.data.startswith("req_date_"))
async def handle_requests_to_users(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    is_chat = call.data.startswith("req_chat_")
    request_kind = "chat" if is_chat else "date"
    prefix = "req_chat_" if is_chat else "req_date_"
    target_id = _parse_int_suffix(call.data, prefix)
    
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    caller_id = call.from_user.id 
    
    # 🛡️ Smart Heal: Prevent false "You are already in an active process" blocks
    await auto_heal_ghost_state(caller_id, state, db_session)
    
    caller = await crud.get_user_by_tg_id(db_session, caller_id)
    is_vip = _is_vip_active(caller)

    # 💎 اصلاحات کارفرما: بررسی VIP
    if not is_vip and (not caller or caller.coin_balance < 1):
        await call.answer("❌ موجودی سکه‌ت کافی نیست رفیق! برای این کار حداقل ۱ سکه نیاز داری.", show_alert=True)
        return

    # --- بررسی سایلنت بودن فرستنده ---
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if caller.silent_until and caller.silent_until > now_utc:
        await call.answer("🔕 خودت تو حالت سایلنتی! اول سایلنت گوشیت (رباتت) رو خاموش کن تا بتونی به بقیه پیام بدی.", show_alert=True)
        return
    # ------------------------------------------------

    if await crud.is_active_match_partner(db_session, caller_id, target_id):
        await call.answer("⚠️ ای بابا! تو که الان با همین کاربر تو چت هستی! 😉", show_alert=True)
        return

    if await crud.get_active_match(db_session, target_id):
        # 👇 کاربر مشغوله -> اضافه‌شدن به لیست انتظار
        waitlist_key = f"user:{target_id}:waitlist"
        await redis_client.sadd(waitlist_key, caller_id)
        await redis_client.expire(waitlist_key, 86400) # انقضا بعد از ۲۴ ساعت
        
        await call.answer("⏳ این شخص الان داره با یکی دیگه چت می‌کنه. رفتیم تو کمین! 👀 به محض اینکه آزاد بشه فوری بهت خبر می‌دم.", show_alert=True)
        return
    
    if await crud.get_active_match(db_session, caller_id):
        await call.answer("⚠️ یه لحظه صبر کن! تو الان تو یه دیت دیگه‌ای. اول اونو تموم کن بعد بیا سراغ یکی دیگه. 😄", show_alert=True)
        return
    
    target_user = await crud.get_user_by_tg_id(db_session, target_id)
    if not target_user:
        await call.answer("❌ این کاربر پیدا نشد، شاید اکانتشو پاک کرده باشه.", show_alert=True)
        return

    if target_user.silent_until and target_user.silent_until > now_utc:
        await call.answer("🔕 این بنده خدا تو حالت سایلنته و پیامی دریافت نمی‌کنه. بذار برای یه وقت دیگه.", show_alert=True)
        return

    from sqlalchemy import or_, and_
    
    
    block_check = await db_session.execute(
        select(BlockList).where(
            or_(
                and_(BlockList.blocker_id == target_id, BlockList.blocked_id == caller_id),
                and_(BlockList.blocker_id == caller_id, BlockList.blocked_id == target_id)
            )
        )
    )
    block_row = block_check.scalar_one_or_none()
    if block_row:
        if block_row.blocker_id == caller_id:
            await call.answer("🚫 شما این شخص را بلاک کرده‌اید! نمی‌توانید به او درخواست بدهید.", show_alert=True)
        else:
            await call.answer("🚫 اوه! این شخص تو رو بلاک کرده، نمی‌تونی بهش درخواست بدی.", show_alert=True)
        return

    req_type_str = "چت 💬" if is_chat else "دیت 💘"
    
    target_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ پایه‌ام", callback_data=f"accept_req_{request_kind}_{caller_id}"),
            InlineKeyboardButton(text="❌ نه مرسی", callback_data=f"reject_req_{request_kind}_{caller_id}")
        ],
        [InlineKeyboardButton(text="👤 دیدن پروفایلش", callback_data=f"view_profile_{caller_id}")],
        [InlineKeyboardButton(text="🚫 بلاک کردن", callback_data=f"block_user_{caller_id}")]
    ])
    
    try:
        await bot.send_message(
            chat_id=target_id,
            text=f"🔔 <b>درخواست جدید:</b>\nیه نفر باهات درخواست <b>{req_type_str}</b> داره! بدو ببین کیه 👇",
            parse_mode="HTML",
            reply_markup=target_kb
        )
        await call.answer(f"✅ درخواست {req_type_str} براش فرستاده شد. حالا باید منتظر بمونی تا جواب بده.", show_alert=True)
    except Exception:
        await call.answer("⚠️ متأسفانه ربات رو استاپ کرده و درخواستی بهش نمی‌رسه.", show_alert=True)

@router.callback_query(F.data.startswith("accept_req_date_") | F.data.startswith("accept_req_chat_"))
async def accept_user_request(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    is_chat = call.data.startswith("accept_req_chat_")
    req_type_str = "چت 💬" if is_chat else "دیت 💘"
    prefix = "accept_req_chat_" if is_chat else "accept_req_date_"
    caller_id = _parse_int_suffix(call.data, prefix)
    
    if caller_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return
        
    target_id = call.from_user.id

    caller = await crud.get_user_by_tg_id(db_session, caller_id)
    is_caller_vip = _is_vip_active(caller)
    if not is_caller_vip and (not caller or caller.coin_balance < 1):
        await call.answer("❌ موجودی سکه فرستنده کافی نیست. ارتباط برقرار نشد.", show_alert=True)
        try:
            await call.message.edit_text(
                f"❌ <b>درخواست لغو شد</b>\n"
                f"شما درخواست {req_type_str} را قبول کردید، اما به دلیل ناکافی بودن موجودی سکه‌ی فرستنده، اتصال برقرار نشد.",
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
            
        try:
            await bot.send_message(
                caller_id, 
                f"❌ درخواست {req_type_str} شما پذیرفته شد، اما به دلیل عدم موجودی کافی شما (حداقل ۱ سکه)، اتصال لغو گردید."
            )
        except Exception:
            pass
        return

    if await crud.get_active_match(db_session, caller_id):
        await call.answer("❌ فرستنده هم‌اکنون در یک چت/دیت دیگر مشغول است.", show_alert=True)
        try:
            await call.message.edit_text("❌ این درخواست دیگر معتبر نیست؛ فرستنده در حال حاضر مشغول است.")
        except Exception:
            pass
        return

    if await crud.get_active_match(db_session, target_id):
        await call.answer("❌ شما هم‌اکنون در یک چت/دیت دیگر مشغول هستید.", show_alert=True)
        return

    if not is_caller_vip:
        await crud.process_coin_transaction(db_session, caller, -1, f"هزینه درخواست {req_type_str} پذیرفته‌شده")
        await db_session.commit()

    await call.answer(f"✅ درخواست {req_type_str} قبول شد! در حال اتصال...", show_alert=False)

    try:
        await call.message.edit_text(f"✅ شما درخواست {req_type_str} این کاربر را قبول کردید. در حال اتصال... 🚀")
    except TelegramBadRequest:
        pass

    try:
        await bot.send_message(caller_id, f"🎉 درخواست {req_type_str} شما توسط کاربر مقابل پذیرفته شد! در حال اتصال... 🚀")
    except Exception:
        pass

    if is_chat:
        try:
            await activate_anonymous_chat_session(db_session, caller_id, target_id)
        except Exception as exc:
            logger.error(
                "Failed to activate direct anonymous chat session %s <-> %s: %s",
                caller_id, target_id, exc,
            )
            for uid in (caller_id, target_id):
                try:
                    await bot.send_message(
                        uid,
                        "⚠️ خطایی در برقراری چت ناشناس رخ داد. لطفاً دوباره تلاش کنید.",
                    )
                except Exception:
                    pass
    else:
        ok = await handle_successful_match(db_session, caller_id, target_id)
        if not ok:
            await call.answer("⚠️ نمی‌توان دیت را شروع کرد؛ ممکن است قبلاً شروع شده باشد. لطفاً دوباره تلاش کنید.", show_alert=True)
            return

@router.callback_query(F.data.startswith("reject_req_"))
async def reject_request(call: CallbackQuery):
    if call.data.startswith("reject_req_chat_"):
        caller_id = _parse_int_suffix(call.data, "reject_req_chat_")
    elif call.data.startswith("reject_req_date_"):
        caller_id = _parse_int_suffix(call.data, "reject_req_date_")
    else:
        caller_id = _parse_int_suffix(call.data, "reject_req_")

    if not caller_id:
        return await call.answer("❌ درخواست نامعتبر.", show_alert=True)

    await call.answer("❌ درخواست رد شد.", show_alert=False)
    try:
        await call.message.edit_text("❌ شما این درخواست را رد کردید. (به فرستنده اطلاعی داده نشد)")
    except TelegramBadRequest:
        pass
        
# FIX HIGH-34: register the stale-questionnaire handler so old `ans_*` buttons
# do not leave the user with an infinite spinner.
# در فایل bot/handlers/interactions.py

from aiogram.filters import StateFilter
from matching_bot_project.bot.states.states import QuestionnaireStates

# در فایل interactions.py

@router.callback_query(
    F.data.startswith("ans_"),
    ~StateFilter(QuestionnaireStates.answering_questions, QuestionnaireStates.waiting_for_partner_answer)
)
async def stale_questionnaire_button(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    current_state = await state.get_state()
    
    # ۱. اگر کاربر واقعاً وسط ارسال گیفت یا انتقال سکه است
    if current_state and any(x in current_state for x in ["Gift", "Transfer"]):
        await call.answer("⚠️ شما در حال انجام یک عملیات دیگر هستید. لطفاً اول دکمه «❌ انصراف» آن را بزنید.", show_alert=True)
        return
        
    active_match = await crud.get_active_match(db_session, call.from_user.id)
    if active_match:
        # ۲. اگر دیت فعاله ولی استیت به هم ریخته (تداخل بک‌گراند)، استیت رو درجا تعمیر می‌کنیم
        if not active_match.questionnaire_completed:
            await state.set_state(QuestionnaireStates.answering_questions)
            await call.answer("🔄 وضعیت شما همگام‌سازی شد. لطفاً دوباره روی گزینه کلیک کنید.", show_alert=True)
        else:
            await call.answer("⚠️ مرحله پرسشنامه به پایان رسیده است.", show_alert=True)
        return
        
    await call.answer("⚠️ این دیت پایان یافته است و پاسخ شما ثبت نمی‌شود.", show_alert=True)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

@router.callback_query(
    F.data.in_({"approve_chat_yes", "approve_chat_no"}), 
    ~StateFilter(ChatStates.waiting_for_approval)
)
async def stale_approval_button(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    active_match = await crud.get_active_match(db_session, call.from_user.id)
    if active_match:
        await call.answer("⚠️ شما در حال انجام یک عملیات دیگر هستید. لطفاً اول آن را لغو کنید.", show_alert=True)
        return
        
    await call.answer("⚠️ این درخواست منقضی شده یا دیت پایان یافته است.", show_alert=True)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


@router.callback_query(
    F.data.startswith("vip_age_filter_"),
    ~StateFilter(VIPStates.waiting_for_age_filter)
)
async def stale_vip_button(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    current_state = await state.get_state()
    # اگر کاربر وارد صف مچینگ شده یا تو دیته، اخطار درست بده
    if current_state and any(phase in current_state.lower() for phase in ["queue", "chat", "matching", "questionnaire"]):
        await call.answer("⚠️ شما هم‌اکنون در یک فرآیند مچینگ یا دیت فعال هستید.", show_alert=True)
        return
        
    await call.answer("⚠️ این منو منقضی شده است. لطفاً مجدداً از منوی اصلی اقدام کنید.", show_alert=True)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


@router.message(Command("onw"))
async def enable_online_warning_cmd(message: Message):
    await process_online_warning(message.from_user.id, message)

# ── با دکمه شیشه‌ای (اگه خواستی ته پیام پایان چت بذاری) ──
@router.callback_query(F.data == "enable_onw_alert")
async def enable_online_warning_call(call: CallbackQuery):
    await process_online_warning(call.from_user.id, call.message)
    await call.answer("رفتی تو کمین! 👀", show_alert=False)

async def process_online_warning(tg_id: int, message_obj):
    # گرفتن آیدی پارتنر قبلی از ردیس
    last_partner_key = f"user:{tg_id}:last_match_partner"
    last_partner_id = await redis_client.get(last_partner_key)
    
    if not last_partner_id:
        await message_obj.answer("😅 ای بابا! تو که هنوز پارتنر قبلی نداشتی! اول یه دیت/چت رو تموم کن بعد بیا اینو فعال کن.")
        return
        
    last_partner_id = int(last_partner_id.decode() if isinstance(last_partner_id, bytes) else last_partner_id)
    
    # اضافه کردن کاربر به لیست تماشاچی‌های (watchers) پارتنر هدف
    watchers_key = f"user:{last_partner_id}:online_watchers"
    await redis_client.sadd(watchers_key, tg_id)
    await redis_client.expire(watchers_key, 259200) # بعد از ۳ روز منقضی میشه که دیتابیس پر نشه
    
    await message_obj.answer("🔔 حله! رفتیم تو کمین. 👀\nبه محض اینکه پارتنر قبلیت آنلاین بشه، یه پیام فوری برات می‌فرستم تا سریع بری بهش پیام بدی.")


@router.callback_query(F.data == "end_active_chat")
async def request_end_chat_inline(call: CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    if current != ChatStates.anonymous_chat_active.state:
        await call.answer("⚠️ چت فعالی یافت نشد.", show_alert=True)
        return
        
    await call.message.answer(
        "⚠️ آیا مطمئن هستید که می‌خواهید چت را پایان دهید؟",
        reply_markup=get_end_chat_confirm_keyboard(),
    )
    await call.answer()