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

    Attributes
    ──────────
    gender        : "male" | "female" | None (= any gender)
    online_only   : restrict results to users whose is_online flag is True
    same_province : restrict results to users in the caller's province
    same_city     : restrict results to users in the caller's city
                    (set implicitly for every ``nearby_`` callback)
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

    Supported token vocabulary (space-separated from the prefix):
    ─────────────────────────────────────────────────────────────
    ``female``   → gender=female
    ``male``     → gender=male   (checked *after* female to avoid substring hit)
    ``online``   → online_only=True
    ``province`` → same_province=True
    ``city``     → same_city=True

    ``nearby_`` prefix always implies ``same_city=True`` regardless of tokens.

    Examples
    ────────
    nearby_female          → SearchFilters(gender="female", same_city=True)
    search_online_male     → SearchFilters(gender="male",   online_only=True)
    search_same_province   → SearchFilters(same_province=True)
    search_same_province_female → SearchFilters(gender="female", same_province=True)
    """
    is_nearby = callback_data.startswith("nearby_")

    if is_nearby:
        suffix = callback_data.removeprefix("nearby_")
    else:
        suffix = callback_data.removeprefix("search_")

    # Split on underscore to obtain individual tokens.
    tokens: set[str] = set(suffix.split("_")) if suffix else set()

    # "female" must be evaluated before "male" since "female" contains "male".
    gender: str | None = None
    if "female" in tokens:
        gender = "female"
    elif "male" in tokens:
        gender = "male"

    return SearchFilters(
        gender=gender,
        online_only="online" in tokens,
        same_province="province" in tokens,
        # "city" token OR nearby_ prefix both activate city-level filtering.
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

    Buttons
    ───────
    [مشاهده پروفایل کاربر]          → view_profile_{found_user_tg_id}
    [ارسال پیام دایرکت (۱ سکه)]    → req_direct_{found_user_tg_id}
    [جستجوی مجدد 🔄]               → {rerun_callback}   (exact original callback)

    The "search again" button intentionally reuses the verbatim callback string
    so the same handler fires again and returns a different random user.
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

    builder.adjust(1)  # Stack buttons vertically; avoids wrapping on narrow screens.
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

    Block exclusion logic
    ──────────────────────
    - Exclude users the caller has added to their block list
      (BlockList rows where ``blocker_id == caller_tg_id``).
    - Exclude users who have blocked the caller
      (BlockList rows where ``blocked_id == caller_tg_id``).

    Randomness strategy
    ────────────────────
    ORDER BY RAND() LIMIT 1 is used so that clicking "جستجوی مجدد" always
    produces a different result without any client-side pagination state.
    This is acceptable for the expected dataset size; swap to a keyset-based
    approach if the User table exceeds ~100k rows.

    Location guard
    ────────────────
    For province/city filters, if the caller's own field is NULL the query
    would match every NULL user — clearly wrong.  We catch this early and
    return None, letting the handler deliver "کاربری یافت نشد" instead.
    """
    # ── Pre-flight: guard against NULL location when location filter is active ──
    if filters.same_province and not caller.province:
        logger.warning(
            "User %s triggered a province filter but has no province set.",
            caller_tg_id,
        )
        return None

    if filters.same_city and not caller.city:
        logger.warning(
            "User %s triggered a city filter but has no city set.",
            caller_tg_id,
        )
        return None

    # ── Subqueries for bilateral block exclusion ─────────────────────────────
    # Rows where the caller is the blocker → tg_ids the caller blocked.
    blocked_by_caller_sq = (
        select(BlockList.blocked_id)
        .where(BlockList.blocker_id == caller_tg_id)
        .correlate(False)
        .scalar_subquery()
    )

    # Rows where the caller is the blocked party → tg_ids that blocked them.
    blockers_of_caller_sq = (
        select(BlockList.blocker_id)
        .where(BlockList.blocked_id == caller_tg_id)
        .correlate(False)
        .scalar_subquery()
    )

    # ── Base query: exclude self, incomplete profiles, and blocked parties ────
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

    # ── Apply optional filters ────────────────────────────────────────────────
    if filters.gender:
        stmt = stmt.where(User.gender == filters.gender)

    if filters.online_only:
        stmt = stmt.where(User.is_online.is_(True))

    if filters.same_province:
        stmt = stmt.where(User.province == caller.province)

    if filters.same_city:
        # City filter implies province filter as well (prevents cross-province
        # city name collisions like two cities named "مرکز" in different provinces).
        stmt = stmt.where(
            User.province == caller.province,
            User.city == caller.city,
        )

    # ── Randomise and return exactly one row ─────────────────────────────────
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
    """
    Unified handler for all explore-type search callbacks.

    Flow
    ────
    1. Fetch the caller's User record (needed for province/city values).
    2. Parse the callback suffix into a :class:`SearchFilters` instance.
    3. Execute the block-aware, filtered query.
    4. If no match: answer with an alert and return.
    5. If match: send the result header + action keyboard to the caller.

    The keyboard's "جستجوی مجدد" button carries ``call.data`` verbatim, so
    re-clicking it goes through this same handler and returns a fresh random
    user without any additional state management.
    """
    caller_tg_id = call.from_user.id

    # ── 1. Fetch caller profile ───────────────────────────────────────────────
    try:
        caller = await crud.get_user_by_tg_id(db_session, caller_tg_id)
    except Exception as exc:
        logger.error(
            "DB error while fetching caller %s during search: %s", caller_tg_id, exc
        )
        await call.answer("❌ خطای سرور. لطفاً دوباره تلاش کنید.", show_alert=True)
        return

    if not caller:
        await call.answer("❌ حساب کاربری شما یافت نشد.", show_alert=True)
        return

    # ── 2. Parse filters from callback suffix ────────────────────────────────
    filters = _parse_filters(call.data)

    # ── 3. Execute query ──────────────────────────────────────────────────────
    try:
        found_user = await _query_user(db_session, caller_tg_id, caller, filters)
    except Exception as exc:
        logger.error(
            "Search query failed for user %s (callback=%s): %s",
            caller_tg_id,
            call.data,
            exc,
        )
        await call.answer("❌ خطای سرور در جستجو. لطفاً دوباره تلاش کنید.", show_alert=True)
        return

    # ── 4. No match ───────────────────────────────────────────────────────────
    if not found_user:
        await call.answer(_NO_RESULT_TEXT, show_alert=True)
        return

    await call.answer()

    # ── 5. Send result with action keyboard ──────────────────────────────────
    # ``call.data`` is forwarded verbatim as the "search again" payload.
    try:
        await call.message.answer(
            text=_RESULT_HEADER_TEXT,
            reply_markup=_build_result_keyboard(found_user.tg_id, call.data),
        )
    except Exception as exc:
        logger.error(
            "Failed to send search result to user %s: %s", caller_tg_id, exc
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 – Anonymous Direct Message Forwarding
# ─────────────────────────────────────────────────────────────────────────────


@router.message(StateFilter(ChatStates.typing_direct_message))
async def forward_direct_message(
    message: Message, 
    state: FSMContext, 
    db_session: AsyncSession
) -> None:
    """
    Accept and forward a text DM while the sender is in the
    ``ChatStates.typing_direct_message`` FSM state.

    Flow
    ────
    1. Check for cancellation buttons; refund coin and abort if detected.
    2. Reject non-text content (photos, videos, stickers, etc.) and prompt
       for plain text.  The state is NOT cleared so the user can try again.
    3. Retrieve ``target_direct_id`` from the FSM data bag.  If missing
       (corrupted state), clear and abort gracefully.
    4. Forward the HTML-escaped message body to the target anonymously.
    5. Acknowledge the sender, clear their FSM state, and return them to the
       main menu.

    Error isolation
    ───────────────
    A failure to deliver to the target (e.g. the target has blocked the bot)
    is logged and surfaced to the sender rather than silently swallowed, so
    the sender is not left in limbo believing their message was delivered.
    """
    sender_id = message.from_user.id

    # ── 1. Cancellation guard ─────────────────────────────────────────────────
    if message.text in ["❌ انصراف", "❌ انصراف و منوی اصلی"]:
        caller = await crud.get_user_by_tg_id(db_session, sender_id)
        if caller:
            caller.coin_balance += 1
            caller.total_spent_coins -= 1
            await db_session.commit()
            
        await state.clear()
        await message.answer(
            "❌ عملیات لغو شد. ۱ سکه به حساب شما بازگردانده شد.",
            reply_markup=get_main_menu_keyboard()
        )
        return

    # ── 2. Media guard ────────────────────────────────────────────────────────
    # Any update that carries a non-text payload must be rejected.
    # We do NOT clear FSM state here; the sender is allowed to retry.
    if not message.text:
        await message.answer(_MEDIA_REJECTED_TEXT, parse_mode="HTML")
        return

    # ── 3. Retrieve target ID from FSM data ───────────────────────────────────
    fsm_data: dict = await state.get_data()
    target_id: int | None = fsm_data.get("target_direct_id")

    if not target_id:
        logger.error(
            "User %s is in typing_direct_message state but FSM data "
            "contains no target_direct_id. Clearing state and aborting.",
            sender_id,
        )
        await state.clear()
        await message.answer(
            "❌ خطای سیستم: هدف پیام مشخص نیست. لطفاً دوباره از منوی اصلی اقدام کنید.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError
    # ── 4. Deliver anonymous DM to the target ────────────────────────────────
    # html.escape() prevents the sender from injecting HTML tags into the
    # message rendered on the target's side (e.g. <b>bold</b> spoofing).
    anonymous_text = _DM_ANONYMOUS_TEMPLATE.format(body=html.escape(message.text))

    delivery_ok = True
    try:
        await bot.send_message(
            chat_id=target_id,
            text=anonymous_text,
            parse_mode="HTML",
        )
    except TelegramForbiddenError:
        delivery_ok = False
        logger.warning(f"Target user {target_id} has blocked the bot. DM from {sender_id} failed.")
    except TelegramAPIError as exc:
        delivery_ok = False
        logger.error(f"Telegram API Error delivering DM from {sender_id} to {target_id}: {exc}")
    except Exception as exc:
        delivery_ok = False
        logger.error(
            "Failed to deliver DM from user %s to user %s: %s",
            sender_id,
            target_id,
            exc,
        )

    # ── 5. Notify sender, clear state, return to main menu ───────────────────
    # Clear the FSM state unconditionally: whether delivery succeeded or failed,
    # the sender should not remain in the DM input state.
    await state.clear()

    if delivery_ok:
        confirmation_text = _DM_SENT_ACK
    else:
        confirmation_text = (
            "⚠️ پیام شما به دلیل خطای سیستم یا مسدود بودن ربات توسط کاربر مقصد ارسال نشد.\n"
            "سکه شما بازگردانده نخواهد شد. لطفاً بعداً تلاش کنید."
        )

    try:
        await message.answer(
            text=confirmation_text,
            reply_markup=get_main_menu_keyboard(),
        )
    except Exception as exc:
        logger.error(
            "Failed to send DM delivery confirmation to sender %s: %s", sender_id, exc
        )