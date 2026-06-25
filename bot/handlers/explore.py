# bot/handlers/explore.py

"""
SEARCH / NEARBY  (callbacks: search_* | nearby_*)
─────────────────────────────────────────────────
Parse the suffix tokens to derive gender, online, and location filters,
build a block-aware SQLAlchemy query, return one random matching User,
and present an action keyboard.  Clicking "جستجوی مجدد" re-fires the
exact same callback so the same logic runs and returns a different user
(thanks to the ORDER BY RAND() strategy).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.models.models import BlockList, User
from matching_bot_project.database.queries import crud

logger = logging.getLogger(__name__)
router = Router(name="explore_handler")


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# MySQL uses RAND(); swap to func.random() if migrating to PostgreSQL/SQLite.
_RANDOM_FUNC = func.rand()

_NO_RESULT_TEXT = "کاربری با این مشخصات یافت نشد."

_RESULT_HEADER_TEXT = "🔍 یک کاربر یافت شد!"


# ─────────────────────────────────────────────────────────────────────────────
# Filter data container
# ─────────────────────────────────────────────────────────────────────────────

class ProfileIncompleteError(Exception):
    """خطایی برای زمانی که فیلد مورد نیاز در پروفایل خود کاربر خالی است"""
    def __init__(self, missing_field_fa: str):
        self.missing_field_fa = missing_field_fa
        super().__init__(f"User profile is missing: {missing_field_fa}")

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
        raise ProfileIncompleteError("استان")

    if filters.same_city and not caller.city:
        raise ProfileIncompleteError("شهر")

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
        # FIXED: Using func.lower() for case-insensitive gender comparison
        stmt = stmt.where(func.lower(User.gender) == filters.gender.lower())

    if filters.online_only:
        stmt = stmt.where(User.is_online.is_(True))

    if filters.same_province:
        stmt = stmt.where(User.province == caller.province)

# ================== کدهای جایگزین (انتهای تابع) ==================
    if filters.same_city:
        stmt = stmt.where(
            User.province == caller.province,
            User.city == caller.city,
        )

    # ثابت _RANDOM_FUNC با مرتب‌سازی بر اساس آخرین بازدید جایگزین شد
    stmt = stmt.order_by(User.last_active.desc()).limit(1)

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
    except ProfileIncompleteError as e:
        # مدیریت خطای نقص اطلاعات پروفایل کاربر فرستنده
        await call.answer(
            f"⚠️ برای این جستجو، ابتدا باید «{e.missing_field_fa}» خود را در پروفایلت ثبت کنی!", 
            show_alert=True
        )
        return
    except Exception as exc:
        logger.error("Search query failed for user %s (callback=%s): %s", caller_tg_id, call.data, exc)
        await call.answer("❌ خطای سرور در جستجو. لطفاً دوباره تلاش کنید.", show_alert=True)
        return

    if not found_user:
        await call.answer(_NO_RESULT_TEXT, show_alert=True)
        return

    await call.answer()

    try:
        await call.message.edit_text(
            text=_RESULT_HEADER_TEXT,
            reply_markup=_build_result_keyboard(found_user.tg_id, call.data),
        )
    except Exception:
        # ساختار Fallback در صورتی که امکان ادیت پیام وجود نداشته باشد
        await call.message.answer(
            text=_RESULT_HEADER_TEXT,
            reply_markup=_build_result_keyboard(found_user.tg_id, call.data),
        )