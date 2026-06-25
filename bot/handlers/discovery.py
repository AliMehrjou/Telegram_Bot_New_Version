"""
bot/handlers/discovery.py
──────────────────────────────────────────────────────────────────────────────
Two independent discovery flows share this router:

  1) SWIPE FLOW       entry: "💘 کشف کاربران"
     Card-by-card like/pass discovery with daily like limits (Redis) and
     instant mutual-match handoff to matching.handle_successful_match.
     State: DiscoveryStates.navigating

  2) FILTER WIZARD     entry: "🔍 جستجوی کاربران"
     3-step filtered search: province → interests → age range → results.
     States: DiscoveryStates.choosing_province / choosing_interests /
             choosing_age_range / showing_results
──────────────────────────────────────────────────────────────────────────────
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.loader import bot, redis_client
from matching_bot_project.bot.handlers.interactions import _build_profile_card
from matching_bot_project.bot.handlers.profile_edit import IRAN_DATA
from matching_bot_project.bot.keyboards.inline import (
    get_discovery_age_keyboard,
    get_discovery_interests_keyboard,
    get_user_action_keyboard,
)
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
from matching_bot_project.bot.states.states import DiscoveryStates
from matching_bot_project.database.queries.crud import (
    get_user_by_tg_id,
    get_discovery_candidate,
    get_filtered_discovery_candidates,
    save_like,
    check_mutual_like,
)

from matching_bot_project.bot.core.constants import ReplyBtn

logger = logging.getLogger(__name__)
router = Router(name="discovery_handler")

DAILY_LIKE_LIMIT = 30
_MAX_RESULTS      = 5


# ════════════════════════════════════════════════════════════════════════════
# 1) SWIPE FLOW — entry: "💘 کشف کاربران"
# ════════════════════════════════════════════════════════════════════════════

def get_discovery_keyboard(target_id: int) -> InlineKeyboardMarkup:
    """Builds the inline keyboard for a discovery candidate."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❤️ لایک", callback_data=f"like_{target_id}"),
                InlineKeyboardButton(text="👎 پاس", callback_data=f"pass_{target_id}")
            ],
            [
                InlineKeyboardButton(text="👤 پروفایل کامل", callback_data=f"view_profile_{target_id}")
            ]
        ]
    )


async def send_next_candidate(message_or_call, db_session: AsyncSession, state: FSMContext, user_tg_id: int):
    """Fetches and sends the next discovery candidate with distance calculation."""
    user = await get_user_by_tg_id(db_session, user_tg_id)
    if not user or not user.gender:
        text = "⚠️ لطفاً ابتدا ثبت‌نام خود را از طریق منوی اصلی کامل کنید."
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.edit_text(text)
        else:
            await message_or_call.answer(text)
        return

    candidate = await get_discovery_candidate(db_session, user.tg_id, user.gender)

    if not candidate:
        text = "✨ در حال حاضر کاربر جدیدی متناسب با معیارهای شما پیدا نشد! بعداً دوباره سر بزن یا پروفایلت رو کامل‌تر کن. 🧭"
        await state.clear()
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.edit_text(text)
        else:
            await message_or_call.answer(text)
        return

    # --- محاسبه فاصله (فیچر ۶) ---
    distance_km = None
    if user.location_lat is not None and user.location_lng is not None and \
       candidate.location_lat is not None and candidate.location_lng is not None:
        from matching_bot_project.database.queries.crud import calculate_distance_km
        distance_km = calculate_distance_km(
            user.location_lat, user.location_lng, 
            candidate.location_lat, candidate.location_lng
        )

    # ارسال distance_km به تابع فرمت‌دهی (نسخه آپدیت شده در formatters.py)
    profile_card = build_unified_profile_card(candidate, distance_km=distance_km)
    keyboard = get_discovery_keyboard(candidate.tg_id)

    await state.set_state(DiscoveryStates.navigating)

    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.edit_text(profile_card, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message_or_call.answer(profile_card, reply_markup=keyboard, parse_mode="HTML")


@router.message(F.text == ReplyBtn.DISCOVER)
async def start_discovery(message: Message, state: FSMContext, db_session: AsyncSession):
    """Triggers the swipe discovery flow."""
    tg_id = message.from_user.id

    limit_key = f"user:{tg_id}:likes_today"
    likes_count_str = await redis_client.get(limit_key)
    likes_count = int(likes_count_str) if likes_count_str else 0

    if likes_count >= DAILY_LIKE_LIMIT:
        await message.answer("🚫 سهمیه لایک روزانه شما (۳۰ از ۳۰) به پایان رسیده است. فردا دوباره تلاش کنید! ⏰")
        return

    await send_next_candidate(message, db_session, state, tg_id)


@router.callback_query(DiscoveryStates.navigating, F.data.startswith("pass_"))
async def handle_pass(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """Handles passing a profile."""
    target_id_str = call.data.removeprefix("pass_")
    if not target_id_str.isdigit():
        await call.answer("خطا در پردازش درخواست.")
        return

    target_id = int(target_id_str)
    caller_id = call.from_user.id

    await save_like(db_session, caller_id, target_id, is_pass=True)
    await db_session.commit()

    await call.answer()
    await send_next_candidate(call, db_session, state, caller_id)


@router.callback_query(DiscoveryStates.navigating, F.data.startswith("like_"))
async def handle_like(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """Handles liking a profile, checking limits, and processing mutual likes."""
    target_id_str = call.data.removeprefix("like_")
    if not target_id_str.isdigit():
        await call.answer("خطا در پردازش درخواست.")
        return

    target_id = int(target_id_str)
    caller_id = call.from_user.id

    limit_key = f"user:{caller_id}:likes_today"
    
    # FIX: Use incr first to avoid race conditions
    likes_count = await redis_client.incr(limit_key)
    
    # If it's the first like of the day, set expiration to midnight
    if likes_count == 1:
        now_tehran = datetime.now(ZoneInfo("Asia/Tehran"))
        midnight_tehran = (now_tehran + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_to_midnight = int((midnight_tehran - now_tehran).total_seconds())
        await redis_client.expire(limit_key, seconds_to_midnight)
    
    if likes_count > DAILY_LIKE_LIMIT:
        await call.answer("🚫 سهمیه لایک روزانه شما به پایان رسیده است.", show_alert=True)
        await state.clear()
        await call.message.edit_text("🚫 سهمیه لایک روزانه شما (۳۰ از ۳۰) به پایان رسیده است. فردا دوباره تلاش کنید! ⏰")
        return

    # Save the like
    await save_like(db_session, caller_id, target_id, is_pass=False)
    await db_session.commit()

    # Check for mutual like
    is_mutual = await check_mutual_like(db_session, caller_id, target_id)

    if is_mutual:
        # Mutual Like! Trigger Instant Match
        await state.clear()

        # Clear target's discovery state if they are in it to prevent collision
        from matching_bot_project.bot.handlers.matching import get_user_state
        partner_ctx = get_user_state(target_id)
        current_partner_state = await partner_ctx.get_state()
        if current_partner_state == DiscoveryStates.navigating.state:
            await partner_ctx.clear()

        caller = await get_user_by_tg_id(db_session, caller_id)
        target = await get_user_by_tg_id(db_session, target_id)

        caller_name = caller.first_name if caller else "کاربر"
        target_name = target.first_name if target else "کاربر"

        from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
        try:
            await call.message.edit_text(f"💘 شما و {target_name} همدیگرو لایک کردید! در حال اتصال... 🚀")
        except (TelegramAPIError, TelegramForbiddenError):
            pass

        try:
            await bot.send_message(target_id, f"💘 شما و {caller_name} همدیگرو لایک کردید! در حال اتصال... 🚀")
        except (TelegramAPIError, TelegramForbiddenError):
            pass

        # Call handle_successful_match locally
        from matching_bot_project.bot.handlers.matching import handle_successful_match
        await handle_successful_match(db_session, caller_id, target_id)

    else:
        # Not mutual yet, load next profile
        await call.answer()
        await send_next_candidate(call, db_session, state, caller_id)


# ════════════════════════════════════════════════════════════════════════════
# 2) FILTER WIZARD — entry: "🔍 جستجوی کاربران"
# ════════════════════════════════════════════════════════════════════════════

def _province_keyboard() -> ReplyKeyboardMarkup:
    provinces = ["🌍 همه استان‌ها"] + list(IRAN_DATA.keys())
    buttons   = []
    for i in range(0, len(provinces), 2):
        row = [KeyboardButton(text=provinces[i])]
        if i + 1 < len(provinces):
            row.append(KeyboardButton(text=provinces[i + 1]))
        buttons.append(row)
    
    
    buttons.append([KeyboardButton(text=ReplyBtn.BACK_TO_MENU)])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)

def _restart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 جستجوی مجدد", callback_data="disc_restart")],
        [InlineKeyboardButton(text="🏠 منوی اصلی",   callback_data="disc_main_menu")],
    ])


@router.message(F.text == ReplyBtn.BACK_TO_MENU)
async def cancel_wizard(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current and current.startswith("DiscoveryStates:") and current != DiscoveryStates.navigating.state:
        await state.clear()
        await message.answer("به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())


@router.message(F.text == ReplyBtn.SEARCH_USERS)
async def start_wizard(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(DiscoveryStates.choosing_province)
    await message.answer(
        "🔍 <b>جستجوی کاربران — مرحله ۱/۳</b>\n\nاستان مورد نظر خود را انتخاب کنید:",
        reply_markup=_province_keyboard(),
        parse_mode="HTML",
    )


@router.message(DiscoveryStates.choosing_province)
async def receive_province(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()

    # اصلاح شد: استفاده از متغیر constants
    if text == ReplyBtn.BACK_TO_MENU:
        await state.clear()
        await message.answer("به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())
        return

    if text == "🌍 همه استان‌ها":
        province = None
    elif text in IRAN_DATA:
        province = text
    else:
        await message.answer("⚠️ لطفاً استان را از کیبورد انتخاب کنید.")
        return
    
    await state.update_data(province=province, selected_interests=[])
    await state.set_state(DiscoveryStates.choosing_interests)
    
    # FIX: Remove ReplyKeyboard before sending the InlineKeyboard
    msg = await message.answer("✅ استان ثبت شد. در حال بارگذاری مرحله بعد...", reply_markup=ReplyKeyboardRemove())
    await msg.delete() # Optional: delete the temporary message so the chat stays clean

    await message.answer(
        "🔍 <b>مرحله ۲/۳ — علایق</b>\n\n"
        "علایق مورد نظر خود را انتخاب کنید (می‌توانید چند مورد انتخاب کنید).\n"
        "برای رد کردن این مرحله مستقیماً «تأیید و جستجو» را بزنید.",
        reply_markup=get_discovery_interests_keyboard([]),
        parse_mode="HTML",
    )


@router.callback_query(DiscoveryStates.choosing_interests, F.data.startswith("disc_int_"))
async def toggle_discovery_interest(call: CallbackQuery, state: FSMContext) -> None:
    key  = call.data.removeprefix("disc_int_")
    data = await state.get_data()
    selected: list[str] = data.get("selected_interests", [])

    if key in selected:
        selected.remove(key)
    else:
        selected.append(key)

    await state.update_data(selected_interests=selected)
    await call.message.edit_reply_markup(
        reply_markup=get_discovery_interests_keyboard(selected)
    )
    await call.answer()


@router.callback_query(DiscoveryStates.choosing_interests, F.data == "disc_int_confirm")
async def confirm_interests(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(DiscoveryStates.choosing_age_range)
    
    # FIX: Edit the existing message instead of sending a new one to prevent orphaned buttons
    await call.message.edit_text(
        "🔍 <b>مرحله ۳/۳ — بازه سنی</b>\n\nبازه سنی مورد نظر را انتخاب کنید:",
        reply_markup=get_discovery_age_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(DiscoveryStates.choosing_age_range, F.data.startswith("disc_age_"))
async def receive_age_range(
    call: CallbackQuery, state: FSMContext, db_session: AsyncSession
) -> None:
    age_data = call.data.removeprefix("disc_age_")
    
    
    if age_data == "all":
        min_age, max_age = 0, 99
    else:
        parts = age_data.split("_")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            await call.answer("❌ خطای پردازش.", show_alert=True)
            return
        min_age, max_age = int(parts[0]), int(parts[1])

    await call.answer()

    data       = await state.get_data()
    province   = data.get("province")
    interests  = data.get("selected_interests") or []

    await state.set_state(DiscoveryStates.showing_results)

    candidates = await get_filtered_discovery_candidates(
        session=db_session,
        caller_tg_id=call.from_user.id,
        province=province,
        interests=interests if interests else None,
        min_age=min_age,
        max_age=max_age,
        limit=_MAX_RESULTS,
    )

    if not candidates:
        # FIX: Also edit text here to clear the age selection buttons
        await call.message.edit_text(
            "😔 متأسفانه کاربری با این مشخصات یافت نشد.\n"
            "فیلترها را تغییر دهید و دوباره جستجو کنید.",
            reply_markup=_restart_keyboard(),
        )
        return

    # Clear the age selection keyboard gracefully before showing results
    await call.message.edit_text(
        f"✅ <b>{len(candidates)} کاربر یافت شد:</b>",
        parse_mode="HTML",
    )

    for candidate in candidates:
        profile_text = _build_profile_card(candidate)
        action_kb    = get_user_action_keyboard(candidate.tg_id)
        try:
            await call.message.answer(
                profile_text,
                reply_markup=action_kb,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error("Failed to send discovery candidate %s: %s", candidate.tg_id, exc)

    await call.message.answer(
        "جستجوی جدید یا بازگشت به منو:",
        reply_markup=_restart_keyboard(),
    )


@router.callback_query(F.data == "disc_restart")
async def disc_restart(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await state.set_state(DiscoveryStates.choosing_province)
    
    # When restarting, ensure we send a fresh message to get the reply keyboard back
    await call.message.delete()
    await call.message.answer(
        "🔍 <b>جستجوی کاربران — مرحله ۱/۳</b>\n\nاستان مورد نظر خود را انتخاب کنید:",
        reply_markup=_province_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "disc_main_menu")
async def disc_main_menu(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await call.message.delete()
    await call.message.answer("به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())