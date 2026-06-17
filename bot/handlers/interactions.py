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
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.loader import bot, dp
from matching_bot_project.bot.keyboards.reply import get_cancel_keyboard, get_main_menu_keyboard
from matching_bot_project.bot.states.states import (
    ChatStates,
    MatchingStates,      # noqa: F401 – imported per project spec; used by other modules
    QuestionnaireStates,  # noqa: F401 – imported per project spec; used by other modules
)
from matching_bot_project.database.models.models import BlockList, MatchHistory
from matching_bot_project.database.queries import crud

logger = logging.getLogger(__name__)
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

    # ── Send ONLY to the caller ───────────────────────────────────────────────
    # We will send it as a message instead of an alert card to preserve formatting and HTML.
    try:
        await bot.send_message(
            chat_id=call.from_user.id,
            text=profile_card,
            parse_mode="HTML"
        )
        await call.answer()
    except Exception as exc:
        logger.error(
            "Failed to send profile message to user %s: %s", call.from_user.id, exc
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 – End Date Early
# ─────────────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("end_date_early_"))
async def end_date_early(
    call: CallbackQuery,
    db_session: AsyncSession,
) -> None:
    """
    Terminate a match that is either in its 5-second countdown or mid-questionnaire.

    Either participant may trigger this.  The handler is idempotent: a second
    tap (from the other user or a double-tap) receives an alert rather than
    producing a double-cancel.

    Steps
    ─────
    1. Fetch MatchHistory; return early if already inactive.
    2. Deactivate and timestamp the record, commit.
    3. Clear FSM state for both users.
    4. Notify both users and return them to the main menu.
    """
    match_id = _parse_int_suffix(call.data, "end_date_early_")
    if match_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    # ── 1. Fetch match record ────────────────────────────────────────────────
    result = await db_session.execute(
        select(MatchHistory).where(MatchHistory.id == match_id)
    )
    match_history: MatchHistory | None = result.scalar_one_or_none()

    if not match_history:
        await call.answer("❌ دیت موردنظر یافت نشد.", show_alert=True)
        return

    # ── Guard: already cancelled ─────────────────────────────────────────────
    if not match_history.is_active:
        await call.answer("⚠️ این دیت قبلا لغو شده است.", show_alert=True)
        return

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
        await call.answer("❌ خطای سرور. لطفاً دوباره تلاش کنید.", show_alert=True)
        return

    await call.answer()

    # ── 3. Stop timeout tracking from scheduler ──────────────────────────────
    from matching_bot_project.bot.core.loader import dating_scheduler

    redis_key = f"date:timeout:{match_id}"
    try:
        await dating_scheduler.redis.delete(redis_key)
        await dating_scheduler.redis.delete(f"match:questions:{match_id}")
        await dating_scheduler.redis.delete(f"match:current_q_index:{match_id}")
        # Clean up users from matching queue / state
        await dating_scheduler.redis.delete(f"user:state:{match_history.user_one_id}")
        await dating_scheduler.redis.delete(f"user:state:{match_history.user_two_id}")
    except Exception as exc:
        logger.warning(f"Could not delete Redis keys for match cancellation {match_id}: {exc}")

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
            if uid != call.from_user.id:
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


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 – Block User
# ─────────────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("block_user_"))
async def block_user(
    call: CallbackQuery,
    db_session: AsyncSession,
) -> None:
    """
    Add the target user to the caller's block list.

    The matching engine is expected to consult the BlockList table to exclude
    blocked pairs from future matches.

    An ``IntegrityError`` is caught gracefully when the row already exists
    (e.g. user double-taps or taps after navigating back to the same card).
    """
    target_id = _parse_int_suffix(call.data, "block_user_")
    if target_id is None:
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    caller_id = call.from_user.id

    # Self-block guard
    if caller_id == target_id:
        await call.answer("❌ نمی‌توانید خودتان را مسدود کنید.", show_alert=True)
        return

    from matching_bot_project.bot.core.loader import redis_client

    db_session.add(BlockList(blocker_id=caller_id, blocked_id=target_id))

    try:
        await db_session.commit()
        # Add to Redis Sets for atomic evaluation in matching engine
        await redis_client.sadd(f"user:{caller_id}:blocks", str(target_id))
    except IntegrityError:
        # The unique constraint on (blocker_id, blocked_id) was violated —
        # the user is already blocked; no further action needed.
        await db_session.rollback()
        await call.answer("⚠️ این کاربر قبلاً مسدود شده است.", show_alert=True)
        return
    except Exception as exc:
        await db_session.rollback()
        logger.error(
            "Unexpected error while user %s attempted to block user %s: %s",
            caller_id,
            target_id,
            exc,
        )
        await call.answer("❌ خطای سرور. لطفاً دوباره تلاش کنید.", show_alert=True)
        return

    await call.answer(
        "🚫 کاربر مسدود شد و دیگر به شما متصل نخواهد شد.",
        show_alert=True,
    )


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