"""
bot/handlers/interactions.py

Handles every inline-keyboard callback that surfaces during or after a match:

  1. view_profile_{user_id}      – Show a caller-only profile card
  2. end_date_early_{match_id}   – Terminate a match at any stage
  3. block_user_{user_id}        – Add a user to the block list
  4. req_direct_{user_id}        – Initiate an anonymous DM request (costs 1 coin)
"""
from __future__ import annotations

import html
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.loader import bot, dp, redis_client
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
)
from matching_bot_project.database.models.models import BlockList, MatchHistory
from matching_bot_project.database.queries import crud
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, delete

from aiogram.filters import StateFilter

router = Router(name="interactions_handler")
# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DATE_CANCELLED_TEXT = (
    "🛑 دیت توسط یکی از طرفین لغو شد و به منوی اصلی بازگشتید."
)

# Maps DB-stored gender values to a display string
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
    """
    Resolve an FSMContext for *any* Telegram user by their ID.

    aiogram 3.x FSMContext is request-scoped.  This helper constructs one
    manually so we can read or write another user's FSM state (e.g. clearing
    a match partner's context after the date is cancelled).
    """
    return FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
    )


def _build_profile_card(user, compatibility: Optional[int] = None) -> str:
    """
    Render a clean, HTML-safe profile card from a User ORM object.

    Every dynamic field is passed through ``html.escape`` to prevent injection
    of HTML tags stored in user-supplied strings (names, city names, etc.).
    """
    name = html.escape(str(user.first_name or "نامشخص"))
    gender_raw = str(user.gender or "").lower()
    gender = html.escape(_GENDER_DISPLAY.get(gender_raw, html.escape(str(user.gender or "نامشخص"))))
    age = html.escape(str(user.age or "نامشخص"))
    province = html.escape(str(user.province or "نامشخص").replace("_", " "))
    city = html.escape(str(user.city or "نامشخص").replace("_", " "))

    bio = html.escape(str(user.bio or "تنظیم نشده"))
    interests = html.escape(str(user.interests or "تنظیم نشده"))

    card = (
        "╔═════════════════════════╗\n"
        "║       👤 <b>پروفایل کاربر</b>       ║\n"
        "╠═════════════════════════╣\n"
        f"║ 📝 نام: <b>{name}</b>\n"
        f"║ ⚧ جنسیت: <b>{gender}</b>\n"
        f"║ 🎂 سن: <b>{age}</b> سال\n"
        f"║ 🗺 استان: <b>{province}</b>\n"
        f"║ 🏙 شهر: <b>{city}</b>\n"
        "╠═════════════════════════╣\n"
        f"║ 📝 بیوگرافی:\n"
        f"║ <i>{bio}</i>\n"
        "║\n"
        f"║ 🎯 علایق:\n"
        f"║ <i>{interests}</i>\n"
        "╚═════════════════════════╝"
    )

    if compatibility is not None:
        card += f"\n\n💞 میزان تفاهم: <b>{compatibility}%</b>"

    return card


def _parse_int_suffix(callback_data: str, prefix: str) -> int | None:
    """
    Extract the integer ID that follows a known prefix in callback_data.

    Returns ``None`` when the suffix is missing or not a valid integer, so
    callers can return an early alert without raising unhandled exceptions.
    """
    try:
        return int(callback_data.removeprefix(prefix))
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 – View Profile
# ─────────────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("view_profile_"))
async def view_partner_profile(
    call: CallbackQuery,
    db_session: AsyncSession,
) -> None:
    """
    Display the requested user's profile card ONLY to the caller.

    Employer requirement:
    "When I click view profile, it should only be shown to me,
     not the other person."

    This is enforced by targeting `call.from_user.id` explicitly and never
    sending anything to the partner.
    """
    target_id = _parse_int_suffix(call.data, "view_profile_")
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    user = await crud.get_user_by_tg_id(db_session, target_id)
    if not user:
        await call.answer("❌ پروفایل کاربر یافت نشد.", show_alert=True)
        return

    profile_card = _build_profile_card(user)
    # Check block status for action keyboard
    block_result = await db_session.execute(
        select(BlockList).where(
            BlockList.blocker_id == call.from_user.id,
            BlockList.blocked_id == target_id,
        )
    )
    is_blocked = block_result.scalar_one_or_none() is not None
    action_kb  = get_user_action_keyboard(target_id, is_blocked=is_blocked)

    # ── Log View for VIP Target ───────────────────────────────────────────────
    is_target_vip = user.is_vip or (user.vip_expires_at and user.vip_expires_at > datetime.utcnow())
    if is_target_vip and call.from_user.id != target_id:
        from matching_bot_project.bot.core.loader import redis_client
        from datetime import timedelta
        import time
        key = f"user:{target_id}:viewers"
        # Log view with Unix timestamp as score
        await redis_client.zadd(key, {str(call.from_user.id): time.time()})
        # TTL of 7 days (604800 seconds)
        await redis_client.expire(key, 604800)

    # ── Send ONLY to the caller ───────────────────────────────────────────────
    # We will send it as a message instead of an alert card to preserve formatting and HTML.
    try:
        await bot.send_message(
            chat_id=call.from_user.id,
            text=profile_card,
            parse_mode="HTML",
            reply_markup=action_kb,         # ← NEW
        )
        await call.answer()

    except Exception as exc:
        logger.error(
            "Failed to send profile message to user %s: %s", call.from_user.id, exc
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 – End Date Early (and Extracted Helpers)
# ─────────────────────────────────────────────────────────────────────────────


async def execute_chat_termination(db_session: AsyncSession, match_id: int, caller_id: int) -> bool:
    """
    Core logic to safely terminate an active match, update the database,
    clean up Redis keys, clear FSM states, and notify both users.
    Returns True if successfully terminated, False if not found or already inactive.
    """
    # ── 1. Fetch match record ────────────────────────────────────────────────
    result = await db_session.execute(
        select(MatchHistory).where(MatchHistory.id == match_id)
    )
    match_history: MatchHistory | None = result.scalar_one_or_none()

    if not match_history:
        return False

    # ── Guard: already cancelled ─────────────────────────────────────────────
    if not match_history.is_active:
        return False

    # ── 2. Deactivate the match ──────────────────────────────────────────────
    match_history.is_active = False
    match_history.ended_at = datetime.utcnow()

    try:
        await db_session.commit()
    except Exception as exc:
        logger.error(
            "Failed to deactivate match %s in the database: %s", match_id, exc
        )
        await db_session.rollback()
        return False

    # ── 3. Stop timeout tracking from scheduler ──────────────────────────────
    from matching_bot_project.bot.core.loader import dating_scheduler

    try:
        await dating_scheduler.redis.delete(f"date:timeout:{match_id}")
        # Clean up users from matching queue / state
        await dating_scheduler.redis.delete(f"user:state:{match_history.user_one_id}")
        await dating_scheduler.redis.delete(f"user:state:{match_history.user_two_id}")
    except Exception as exc:
        logger.warning("Could not delete core Redis tracking keys for match cancellation %s: %s", match_id, exc)

    # ── 4 & 5. Clear state and notify both participants ──────────────────────
    for uid in (match_history.user_one_id, match_history.user_two_id):
        # Clear FSM regardless of which state the user is currently in
        ctx = get_user_state(uid)
        try:
            await ctx.clear()
        except Exception as exc:
            logger.warning(
                "Could not clear FSM state for user %s after match cancellation: %s",
                uid,
                exc,
            )

        # Notify and return to main menu
        try:
            if uid != caller_id:
                # Target partner
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
            logger.error(
                "Failed to send cancellation notice to user %s: %s", uid, exc
            )

    return True

# ── Reply button: "🛑 اتمام دیت" → show confirmation ─────────────────────
@router.message(F.text == "🛑 اتمام دیت")
async def request_end_date_confirm(
    message: Message, db_session: AsyncSession
) -> None:
    active_match = await crud.get_active_match(db_session, message.from_user.id)
    if not active_match:
        await message.answer(
            "⚠️ دیت فعالی یافت نشد.", reply_markup=get_main_menu_keyboard()
        )
        return
    await message.answer(
        "⚠️ آیا مطمئن هستید که می‌خواهید دیت را پایان دهید؟\n"
        "این عمل قابل بازگشت نیست.",
        reply_markup=get_end_date_confirm_keyboard(),
    )

@router.callback_query(F.data == "confirm_end_date")
async def confirm_end_date(
    call: CallbackQuery, db_session: AsyncSession
) -> None:
    active_match = await crud.get_active_match(db_session, call.from_user.id)
    if not active_match:
        await call.answer("دیت فعالی یافت نشد.", show_alert=True)
        return
    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await execute_chat_termination(db_session, active_match.id, call.from_user.id)

@router.callback_query(F.data == "cancel_end_date")
async def cancel_end_date(call: CallbackQuery) -> None:
    await call.answer("❌ لغو شد. دیت ادامه دارد.")
    try:
        await call.message.delete()
    except Exception:
        pass

# ── Reply button: "🛑 اتمام چت" → show confirmation ──────────────────────
@router.message(F.text == "🛑 اتمام چت")
async def request_end_chat_confirm(
    message: Message, state: FSMContext
) -> None:
    current = await state.get_state()
    if current != ChatStates.anonymous_chat_active.state:
        await message.answer(
            "⚠️ چت فعالی یافت نشد.", reply_markup=get_main_menu_keyboard()
        )
        return
    await message.answer(
        "⚠️ آیا مطمئن هستید که می‌خواهید چت را پایان دهید؟",
        reply_markup=get_end_chat_confirm_keyboard(),
    )

@router.callback_query(ChatStates.anonymous_chat_active, F.data == "confirm_end_chat")
async def confirm_end_chat(
    call: CallbackQuery, state: FSMContext, db_session: AsyncSession
) -> None:
    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    fsm_data         = await state.get_data()
    match_history_id = fsm_data.get("match_history_id")
    if match_history_id:
        await execute_chat_termination(db_session, match_history_id, call.from_user.id)
    else:
        await state.clear()
        await call.message.answer(
            "به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard()
        )

@router.callback_query(F.data == "cancel_end_chat")
async def cancel_end_chat(call: CallbackQuery) -> None:
    await call.answer("❌ لغو شد. چت ادامه دارد.")
    try:
        await call.message.delete()
    except Exception:
        pass

@router.callback_query(F.data.startswith("end_date_early_"))
async def end_date_early(
    call: CallbackQuery,
    db_session: AsyncSession,
) -> None:
    """Callback wrapper for terminate date early logic."""
    match_id = _parse_int_suffix(call.data, "end_date_early_")
    if match_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    success = await execute_chat_termination(db_session, match_id, call.from_user.id)
    if not success:
        await call.answer("⚠️ این دیت قبلا لغو شده یا وجود ندارد.", show_alert=True)
    else:
        await call.answer()


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 – Block User (and Extracted Helpers)
# ─────────────────────────────────────────────────────────────────────────────

async def execute_user_blocking(db_session: AsyncSession, blocker_id: int, blocked_id: int) -> tuple[bool, str]:
    """
    Core logic to block a user, updating MySQL BlockList and Redis block sets.
    Terminates any active match between the two users.
    Returns (Success: bool, Message: str).
    """
    if blocker_id == blocked_id:
        return False, "❌ نمی‌توانید خودتان را مسدود کنید."

    from sqlalchemy import select, or_, and_
    from matching_bot_project.bot.core.loader import redis_client

    db_session.add(BlockList(blocker_id=blocker_id, blocked_id=blocked_id))

    try:
        await db_session.commit()
        # Add to Redis Sets for atomic evaluation in matching engine
        await redis_client.sadd(f"user:{blocker_id}:blocks", str(blocked_id))
        
        # Check for active match and terminate if exists
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

        return True, "🚫 کاربر مسدود شد و دیگر به شما متصل نخواهد شد."
    except IntegrityError:
        # The unique constraint on (blocker_id, blocked_id) was violated —
        # the user is already blocked; no further action needed.
        await db_session.rollback()
        return False, "⚠️ این کاربر قبلاً مسدود شده است."
    except Exception as exc:
        await db_session.rollback()
        logger.error(
            "Unexpected error while user %s attempted to block user %s: %s",
            blocker_id,
            blocked_id,
            exc,
        )
        return False, "❌ خطای سرور. لطفاً دوباره تلاش کنید."
    
    
@router.callback_query(F.data.startswith("block_user_"))
async def block_user(
    call: CallbackQuery,
    db_session: AsyncSession,
) -> None:
    """Callback wrapper for block user logic, with rate limiting."""
    target_id = _parse_int_suffix(call.data, "block_user_")
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    caller_id = call.from_user.id
    from matching_bot_project.bot.core.loader import redis_client

    # Execute core block
    success, msg = await execute_user_blocking(db_session, caller_id, target_id)

    if success:
        # Track manual blocks for cooldown
        from datetime import datetime, timedelta
        limit_key = f"user:blocks_today:{caller_id}"

        blocks_count_str = await redis_client.get(limit_key)
        blocks_count = int(blocks_count_str) if blocks_count_str else 0

        pipe = redis_client.pipeline()
        pipe.incr(limit_key)
        if blocks_count == 0:
            now = datetime.utcnow()
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seconds_to_midnight = int((midnight - now).total_seconds())
            pipe.expire(limit_key, seconds_to_midnight)

        # Set cooldown if reached limit
        if blocks_count + 1 >= 3:
            pipe.setex(f"user:block_cooldown:{caller_id}", 86400, "1")  # 24h cooldown

        await pipe.execute()

    await call.answer(msg, show_alert=True)


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 – Direct Message Request (costs 1 coin)
# ─────────────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("req_direct_"))
async def request_direct_message(
    call: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    """
    Initiate an anonymous direct message request to another user.

    Flow
    ─────
    1. Parse and validate the target ID.
    2. Fetch the caller's DB record; verify coin balance ≥ 1.
    3. Deduct 1 coin atomically and commit.
    4. Transition the caller's FSM to ChatStates.typing_direct_message.
    5. Persist the target's ID in the caller's FSM data.
    6. Prompt the caller to type their message.

    The coin is deducted *before* the FSM transition so that a crash between
    the two steps does not leave the user in the input state without having
    paid (the safer direction of failure).
    """
    target_id = _parse_int_suffix(call.data, "req_direct_")
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    caller_id = call.from_user.id
    caller = await crud.get_user_by_tg_id(db_session, caller_id)

    if not caller:
        await call.answer("❌ حساب کاربری شما یافت نشد.", show_alert=True)
        return

    # ── Coin balance check ───────────────────────────────────────────────────
    if caller.coin_balance < 1:
        await call.answer(
            "❌ سکه‌های شما کافی نیست! برای دریافت سکه از منوی اصلی اقدام کنید.",
            show_alert=True,
        )
        return

    # ── Deduct coin and commit ───────────────────────────────────────────────
    caller.coin_balance -= 1
    caller.total_spent_coins += 1
    try:
        await db_session.commit()
        await db_session.refresh(caller)
    except Exception as exc:
        await db_session.rollback()
        logger.error(
            "Failed to deduct coin from user %s for DM request to %s: %s",
            caller_id,
            target_id,
            exc,
        )
        await call.answer("❌ خطای سرور. لطفاً دوباره تلاش کنید.", show_alert=True)
        return

    # ── FSM transition ───────────────────────────────────────────────────────
    await state.set_state(ChatStates.typing_direct_message)
    await state.update_data(target_direct_id=target_id)

    await call.answer()

    # ── Prompt the caller ────────────────────────────────────────────────────
    try:
        await bot.send_message(
            chat_id=caller_id,
            text=(
                "💬 پیام دایرکت خود را بنویسید (یک پیام متنی)."
                " این پیام به صورت ناشناس برای کاربر ارسال می‌شود.\n"
                "هزینه: ۱ سکه کسر شد."
            ),
            reply_markup=get_cancel_keyboard(),
        )
    except Exception as exc:
        logger.error(
            "Failed to send DM prompt to user %s: %s", caller_id, exc
        )

# ─────────────────────────────────────────────────────────────────────────────
# Section 5 – Gamification, Social, & Moderation 
# ─────────────────────────────────────────────────────────────────────────────

# ── Like with gamification ──
@router.callback_query(F.data.startswith("like_user_"))
async def handle_like_user(
    call: CallbackQuery, db_session: AsyncSession
) -> None:
    target_id_str = call.data.removeprefix("like_user_")
    if not target_id_str.isdigit():
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return
    target_id = int(target_id_str)
    caller_id = call.from_user.id
    if target_id == caller_id:
        await call.answer("نمی‌توانید خودتان را لایک کنید!", show_alert=True)
        return

    await crud.save_like(db_session, caller_id, target_id, is_pass=False)
    await db_session.commit()

    total_likes = await crud.get_received_like_count(db_session, target_id)

    # Milestone reward: every 20 likes received → 5 free coins
    if total_likes > 0 and total_likes % 20 == 0:
        target_user = await crud.get_user_by_tg_id(db_session, target_id)
        if target_user:
            await crud.process_coin_transaction(
                db_session, target_user, 5, f"جایزه دریافت {total_likes} لایک"
            )
            await db_session.commit()
            try:
                await bot.send_message(
                    chat_id=target_id,
                    text=(
                        f"🎉 تبریک! پروفایل شما به <b>{total_likes} لایک</b> رسید!\n"
                        "🎁 <b>۵ سکه</b> جایزه به حساب شما واریز شد. ✨"
                    ),
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

# ── Add friend ──
@router.callback_query(F.data.startswith("add_friend_"))
async def handle_add_friend(
    call: CallbackQuery, db_session: AsyncSession
) -> None:
    target_id_str = call.data.removeprefix("add_friend_")
    if not target_id_str.isdigit():
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return
    success = await crud.add_friend(
        db_session, call.from_user.id, int(target_id_str)
    )
    if success:
        await db_session.commit()
    await call.answer(
        "✅ به لیست دوستان اضافه شد." if success else "⚠️ قبلاً اضافه شده بود.",
        show_alert=True,
    )

# ── Report flow ──
@router.callback_query(F.data.startswith("report_user_"))
async def show_report_reasons(
    call: CallbackQuery
) -> None:
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
async def process_report_reason(
    call: CallbackQuery, db_session: AsyncSession
) -> None:
    # Format: report_reason_{reported_tg_id}_{reason_code}
    parts = call.data.removeprefix("report_reason_").rsplit("_", 1)
    if len(parts) != 2 or not parts[0].isdigit():
        await call.answer("❌ خطای پردازش.", show_alert=True)
        return
    reported_id = int(parts[0])
    reason_code = parts[1]

    reason_map = {
        "inappropriate_photo": "عکس نامناسب",
        "scammer":             "کلاهبردار",
        "harassment":          "توهین و فحاشی",
        "spam":                "اسپم/تبلیغات",
        "impersonation":       "جعل هویت",
        "suspicious_link":     "ارسال لینک مشکوک",
        "adult_content":       "محتوای غیراخلاقی",
        "drugs":               "فروش مواد",
        "bot_fake":            "ربات/فیک",
        "other":               "سایر موارد",
    }
    persian_reason = reason_map.get(reason_code, "نامشخص")
    reporter_id    = call.from_user.id

    await crud.create_user_report(
        session=db_session,
        reporter_id=reporter_id,
        reported_id=reported_id,
        reason=persian_reason,
    )
    await db_session.commit()

    # Notify admins
    from matching_bot_project.bot.core.config import settings
    admin_text = (
        "🚨 <b>گزارش تخلف جدید:</b>\n"
        f"گزارش‌دهنده: <code>{reporter_id}</code>\n"
        f"گزارش‌شده:   <code>{reported_id}</code>\n"
        f"دلیل: {persian_reason}"
    )
    for admin_id in settings.parsed_admin_ids:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="HTML")
        except Exception:
            pass

    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.answer("✅ گزارش شما ثبت شد. با تشکر.", show_alert=True)

@router.callback_query(F.data == "report_cancel")
async def cancel_report_from_profile(call: CallbackQuery) -> None:
    await call.answer("❌ گزارش لغو شد.")
    try:
        await call.message.delete()
    except Exception:
        pass

# ── هندلر ارسال پیام دایرکت ───────────────────────────────────────────────
@router.message(ChatStates.typing_direct_message)
async def process_direct_message(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    if message.text == "❌ انصراف و منوی اصلی":
        await state.clear()
        await message.answer("عملیات ارسال دایرکت لغو شد.", reply_markup=get_main_menu_keyboard())
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

    # چک کردن وضعیت بلاک (آیا گیرنده، فرستنده را بلاک کرده است؟)
    block_check = await db_session.execute(
        select(BlockList).where(
            BlockList.blocker_id == target_id,
            BlockList.blocked_id == caller_id
        )
    )
    is_blocked = block_check.scalar_one_or_none() is not None

    if is_blocked:
        await message.reply("🚫 شما توسط این کاربر مسدود شده‌اید و امکان ارسال پیام را ندارید.")
        await state.clear()
        return

    # 🎯 ساخت کیبورد شیشه‌ای کامل برای زیر پیام دایرکت گیرنده
    target_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 پاسخ دادن", callback_data=f"req_direct_{caller_id}")],
        [
            InlineKeyboardButton(text="👤 مشاهده پروفایل", callback_data=f"view_profile_{caller_id}"),
            InlineKeyboardButton(text="🚫 بلاک کردن", callback_data=f"block_user_{caller_id}")
        ],
        [InlineKeyboardButton(text="🚩 گزارش تخلف", callback_data=f"report_user_{caller_id}")]
    ])

    try:
        # ارسال پیام به گیرنده با دکمه‌های جدید (متن قدیمی حذف شد)
        await bot.send_message(
            chat_id=target_id,
            text=f"📩 <b>یک پیام دایرکت ناشناس دریافت کردید:</b>\n\n{html.escape(message.text)}",
            parse_mode="HTML",
            reply_markup=target_kb
        )
        await message.reply("✅ پیام شما با موفقیت به کاربر تحویل داده شد.", reply_markup=get_main_menu_keyboard())
    except Exception:
        await message.reply("⚠️ خطایی رخ داد. احتمالاً کاربر ربات را متوقف کرده است.", reply_markup=get_main_menu_keyboard())

    await state.clear()

# ── هندلر آنبلاک کردن ───────────────────────────────────────────────
@router.callback_query(F.data.startswith("unblock_user_"))
async def unblock_user(
    call: CallbackQuery,
    db_session: AsyncSession,
) -> None:
    target_id = _parse_int_suffix(call.data, "unblock_user_")
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    caller_id = call.from_user.id
    from matching_bot_project.bot.core.loader import redis_client

    # حذف ریکورد بلاک از دیتابیس
    await db_session.execute(
        delete(BlockList).where(
            BlockList.blocker_id == caller_id,
            BlockList.blocked_id == target_id
        )
    )
    await db_session.commit()
    
    # حذف از کش ردیس
    await redis_client.srem(f"user:{caller_id}:blocks", str(target_id))
    await call.answer("🔓 کاربر با موفقیت از لیست سیاه شما خارج شد.", show_alert=True)
    
    # آپدیت کردن کیبورد شیشه‌ای به حالت نرمال (نمایش دکمه بلاک)
    action_kb = get_user_action_keyboard(target_id, is_blocked=False)
    try:
        await call.message.edit_reply_markup(reply_markup=action_kb)
    except Exception:
        pass

# ── هندلر درخواست چت و دیت ───────────────────────────────────────────────
@router.callback_query(F.data.startswith("req_chat_"))
@router.callback_query(F.data.startswith("req_date_"))
async def handle_requests_to_users(call: CallbackQuery, db_session: AsyncSession) -> None:
    is_chat = call.data.startswith("req_chat_")
    prefix = "req_chat_" if is_chat else "req_date_"
    target_id = _parse_int_suffix(call.data, prefix)
    
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return
        
    caller_id = call.from_user.id
    
    # چک کردن وضعیت بلاک
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
