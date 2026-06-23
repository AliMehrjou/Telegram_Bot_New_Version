"""
bot/handlers/explore.py

Two independent feature areas:

  1. SEARCH / NEARBY  (callbacks: search_* | nearby_*)
     ─────────────────────────────────────────────────
     Parse the suffix tokens to derive gender, online, and location filters,
     build a block-aware SQLAlchemy query, return one random matching User,
     and present an action keyboard.  Clicking "جستجوی مجدد" re-fires the
     exact same callback so the same logic runs and returns a different user
     (thanks to the ORDER BY RAND() strategy).

  2. ANONYMOUS DM FORWARDING  (state: ChatStates.typing_direct_message)
     ────────────────────────────────────────────────────────────────────
     Validate the message is text, retrieve the target ID from FSM data,
     forward the HTML-escaped content anonymously, clear state, and return
     the sender to the main menu.
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.loader import bot
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
from matching_bot_project.bot.states.states import ChatStates
from matching_bot_project.database.models.models import BlockList, User
from matching_bot_project.database.queries import crud
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError

logger = logging.getLogger(__name__)
router = Router(name="explore_handler")


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# MySQL uses RAND(); swap to func.random() if migrating to PostgreSQL/SQLite.
_RANDOM_FUNC = func.rand()

_NO_RESULT_TEXT = "کاربری با این مشخصات یافت نشد."

_RESULT_HEADER_TEXT = "🔍 یک کاربر یافت شد!"

_DM_ANONYMOUS_TEMPLATE = (
    "📩 <b>یک پیام دایرکت ناشناس دریافت کردید:</b>\n\n"
    "{body}"
    "\n\n(برای پاسخ دادن، باید پروفایل او را پیدا کنید)"
)

_DM_SENT_ACK = "✅ پیام شما با موفقیت و به صورت ناشناس ارسال شد."

_MEDIA_REJECTED_TEXT = (
    "⚠️ لطفاً فقط یک پیام <b>متنی</b> ارسال کنید.\n"
    "فایل، عکس، ویدیو و استیکر قابل قبول نیستند."
)


# ─────────────────────────────────────────────────────────────────────────────
# Filter data container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SearchFilters:
    """
    All filter dimensions that can be derived from a single callback token.
    """
    gender: str | None = field(default=None)
    online_only: bool = field(default=False)
    same_province: bool = field(default=False)
    same_city: bool = field(default=False)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: callback → filters
# ─────────────────────────────────────────────────────────────────────────────

def _parse_filters(callback_data: str) -> SearchFilters:
    """
    Derive a :class:`SearchFilters` instance from raw ``callback_data``.
    """
    is_nearby = callback_data.startswith("nearby_")

    if is_nearby:
        suffix = callback_data.removeprefix("nearby_")
    else:
        suffix = callback_data.removeprefix("search_")

    tokens: set[str] = set(suffix.split("_")) if suffix else set()

    gender: str | None = None
    if "female" in tokens:
        gender = "female"
    elif "male" in tokens:
        gender = "male"

    return SearchFilters(
        gender=gender,
        online_only="online" in tokens,
        same_province="province" in tokens,
        same_city=is_nearby or "city" in tokens,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build result action keyboard
# ─────────────────────────────────────────────────────────────────────────────

def _build_result_keyboard(
    found_user_tg_id: int,
    rerun_callback: str,
) -> InlineKeyboardMarkup:
    """
    Build the three-button action keyboard that appears below each search result.
    """
    builder = InlineKeyboardBuilder()

    builder.button(
        text="👤 مشاهده پروفایل کاربر",
        callback_data=f"view_profile_{found_user_tg_id}",
    )
    builder.button(
        text="💬 ارسال پیام دایرکت (۱ سکه)",
        callback_data=f"req_direct_{found_user_tg_id}",
    )
    builder.button(
        text="🔄 جستجوی مجدد",
        callback_data=rerun_callback,
    )

    builder.adjust(1)
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build and execute the filtered SQLAlchemy query
# ─────────────────────────────────────────────────────────────────────────────

async def _query_user(
    db_session: AsyncSession,
    caller_tg_id: int,
    caller: User,
    filters: SearchFilters,
) -> User | None:
    """
    Find one random User that satisfies all active filters while respecting
    block relationships in both directions.
    """
    if filters.same_province and not caller.province:
        return None

    if filters.same_city and not caller.city:
        return None

    blocked_by_caller_sq = (
        select(BlockList.blocked_id)
        .where(BlockList.blocker_id == caller_tg_id)
        .correlate(False)
        .scalar_subquery()
    )

    blockers_of_caller_sq = (
        select(BlockList.blocker_id)
        .where(BlockList.blocked_id == caller_tg_id)
        .correlate(False)
        .scalar_subquery()
    )

    stmt = (
        select(User)
        .where(
            User.tg_id != caller_tg_id,
            User.completed_registration.is_(True),
            getattr(User, "invisible_mode", False) == False,
            User.tg_id.not_in(blocked_by_caller_sq),
            User.tg_id.not_in(blockers_of_caller_sq),
        )
    )

    if filters.gender:
        stmt = stmt.where(User.gender == filters.gender)

    if filters.online_only:
        stmt = stmt.where(User.is_online.is_(True))

    if filters.same_province:
        stmt = stmt.where(User.province == caller.province)

    if filters.same_city:
        stmt = stmt.where(
            User.province == caller.province,
            User.city == caller.city,
        )

    stmt = stmt.order_by(_RANDOM_FUNC).limit(1)

    result = await db_session.execute(stmt)
    return result.scalar_one_or_none()


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 – Search & Nearby handler
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(
    F.data.startswith("search_") | F.data.startswith("nearby_")
)
async def execute_search(
    call: CallbackQuery,
    db_session: AsyncSession,
) -> None:
    caller_tg_id = call.from_user.id

    try:
        caller = await crud.get_user_by_tg_id(db_session, caller_tg_id)
    except Exception as exc:
        logger.error("DB error while fetching caller %s during search: %s", caller_tg_id, exc)
        await call.answer("❌ خطای سرور. لطفاً دوباره تلاش کنید.", show_alert=True)
        return

    if not caller:
        await call.answer("❌ حساب کاربری شما یافت نشد.", show_alert=True)
        return

    filters = _parse_filters(call.data)

    try:
        found_user = await _query_user(db_session, caller_tg_id, caller, filters)
    except Exception as exc:
        logger.error("Search query failed for user %s (callback=%s): %s", caller_tg_id, call.data, exc)
        await call.answer("❌ خطای سرور در جستجو. لطفاً دوباره تلاش کنید.", show_alert=True)
        return

    if not found_user:
        await call.answer(_NO_RESULT_TEXT, show_alert=True)
        return

    await call.answer()

    # FIX: Using edit_text instead of answer to prevent chat cluttering on "Search Again"
    try:
        await call.message.edit_text(
            text=_RESULT_HEADER_TEXT,
            reply_markup=_build_result_keyboard(found_user.tg_id, call.data),
        )
    except Exception:
        # Fallback if message cannot be edited (e.g., if it's the first time from a different menu)
        await call.message.answer(
            text=_RESULT_HEADER_TEXT,
            reply_markup=_build_result_keyboard(found_user.tg_id, call.data),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 – Anonymous DM Forwarding (ADDED)
# ─────────────────────────────────────────────────────────────────────────────

@router.message(StateFilter(ChatStates.typing_direct_message))
async def process_anonymous_dm(message: Message, state: FSMContext) -> None:
    """
    Handles the actual text input from the user when they are in the
    process of sending a direct message via the explore action keyboard.
    """
    if not message.text:
        await message.answer(_MEDIA_REJECTED_TEXT)
        return

    data = await state.get_data()
    target_id = data.get("target_user_id")

    if not target_id:
        await state.clear()
        await message.answer(
            "❌ خطای پردازش. لطفاً دوباره امتحان کنید.",
            reply_markup=get_main_menu_keyboard()
        )
        return

    safe_text = html.escape(message.text)
    final_message = _DM_ANONYMOUS_TEMPLATE.format(body=safe_text)

    try:
        await bot.send_message(
            chat_id=target_id,
            text=final_message,
            parse_mode="HTML"
        )
        await message.answer(
            text=_DM_SENT_ACK,
            reply_markup=get_main_menu_keyboard()
        )
    except (TelegramForbiddenError, TelegramAPIError) as exc:
        logger.warning("Could not forward DM to %s: %s", target_id, exc)
        await message.answer(
            "❌ کاربر مورد نظر ربات را مسدود کرده یا در دسترس نیست.",
            reply_markup=get_main_menu_keyboard()
        )
    finally:
        await state.clear()