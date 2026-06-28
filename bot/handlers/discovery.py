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

     نسخه آپدیت‌شده (جستجوی پیشرفته):
       • نتایج دیگر صرفاً فیلتر نیستند بلکه بر اساس ترکیب علایق مشترک،
         فعالیت اخیر، نزدیکی جغرافیایی/استانی و اعتبار پروفایل رتبه‌بندی
         می‌شوند (به‌جای ORDER BY last_active صرف).
       • فهرست «کاربران دیده‌شده» از FSM state به یک Redis Set با TTL منتقل
         شده تا (الف) با ری‌استارت ربات از بین نرود، (ب) سبک‌تر باشد، و
         (ج) بشه به‌سادگی با دکمه «شروع مجدد» ریست شود.
       • هر نتیجه یک نشانگر کیفیت تطابق (🔥 عالی / ✨ خوب / 🙂 معمولی) دارد
         تا کاربر سریع متوجه شود چرا این پروفایل نشان داده شده.
──────────────────────────────────────────────────────────────────────────────
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram.exceptions import TelegramBadRequest
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
    calculate_distance_km,
)

from matching_bot_project.bot.core.constants import ReplyBtn

logger = logging.getLogger(__name__)
router = Router(name="discovery_handler")

DAILY_LIKE_LIMIT = 30
_MAX_RESULTS      = 5

# مجموعه "کاربران دیده‌شده" در ویزارد فیلتر، به‌جای FSM state، در Redis نگه‌داری
# می‌شود تا با ری‌استارت ربات پاک نشود و سبک‌تر باشد.
_VIEWED_SET_PREFIX = "discovery:viewed"
_VIEWED_SET_TTL     = 60 * 60 * 6  # ۶ ساعت — بعد از این مدت جستجوی قبلی "تازه" می‌شود


def _viewed_set_key(tg_id: int) -> str:
    return f"{_VIEWED_SET_PREFIX}:{tg_id}"


async def _get_viewed_ids(tg_id: int) -> list[int]:
    raw = await redis_client.smembers(_viewed_set_key(tg_id))
    return [int(x) for x in raw] if raw else []


async def _add_viewed_id(tg_id: int, candidate_id: int) -> None:
    key = _viewed_set_key(tg_id)
    await redis_client.sadd(key, candidate_id)
    await redis_client.expire(key, _VIEWED_SET_TTL)


async def _clear_viewed_ids(tg_id: int) -> None:
    await redis_client.delete(_viewed_set_key(tg_id))


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

@router.callback_query(F.data == "disc_cancel")
async def cancel_discovery_wizard(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("جستجو لغو شد.")
    await state.clear()
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        
    await call.message.answer("❌ جستجوی کاربران لغو شد. به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())


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


def _match_quality_label(
    caller,
    candidate,
    interest_filter: set[str],
) -> str:
    """
    یک نشانگر کیفیت تطابق ساده برای نمایش بالای کارت نتیجه می‌سازد.
    این یک هیوریستیک نمایشی سبک است (نه دقیقاً همان فرمول امتیازدهی دیتابیس)
    تا کاربر سریع بفهمد چرا این پروفایل بالا آمده.
    """
    cand_interests = (
        {i.strip() for i in candidate.interests.split(",") if i.strip()}
        if getattr(candidate, "interests", None) else set()
    )
    caller_interests = (
        {i.strip() for i in caller.interests.split(",") if i.strip()}
        if caller and caller.interests else set()
    )
    reference = interest_filter or caller_interests
    shared = reference & cand_interests if reference else set()

    same_city = bool(
        caller and caller.city and candidate.city and caller.city == candidate.city
    )
    same_province = bool(
        caller and caller.province and candidate.province and caller.province == candidate.province
    )

    distance_km = None
    if (
        caller and caller.location_lat is not None and caller.location_lng is not None
        and candidate.location_lat is not None and candidate.location_lng is not None
    ):
        distance_km = calculate_distance_km(
            caller.location_lat, caller.location_lng,
            candidate.location_lat, candidate.location_lng,
        )

    closeness = (distance_km is not None and distance_km <= 20) or same_city

    if len(shared) >= 2 and closeness:
        return "🔥 تطابق عالی"
    if len(shared) >= 2 or (len(shared) >= 1 and closeness):
        return "✨ تطابق خوب"
    if shared or same_province:
        return "🙂 تطابق نسبی"
    return "🔎 نتیجه جدید"


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
    
    msg = await message.answer("✅ استان ثبت شد. در حال بارگذاری مرحله بعد...", reply_markup=ReplyKeyboardRemove())
    try:
        await msg.delete() 
    except Exception:
        pass

    markup = get_discovery_interests_keyboard([])
    inline_kb = list(markup.inline_keyboard)
    inline_kb.append([InlineKeyboardButton(text="❌ انصراف", callback_data="disc_cancel")])

    await message.answer(
        "🔍 <b>مرحله ۲/۳ — علایق</b>\n\n"
        "علایق مورد نظر خود را انتخاب کنید (می‌توانید چند مورد انتخاب کنید).\n"
        "برای رد کردن این مرحله مستقیماً «تأیید و جستجو» را بزنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_kb),
        parse_mode="HTML",
    )

@router.callback_query(DiscoveryStates.choosing_interests, F.data == "disc_int_confirm")
async def confirm_interests(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(DiscoveryStates.choosing_age_range)
    
    
    markup = get_discovery_age_keyboard()
    inline_kb = list(markup.inline_keyboard)
    inline_kb.append([InlineKeyboardButton(text="❌ انصراف", callback_data="disc_cancel")])
    
    await call.message.edit_text(
        "🔍 <b>مرحله ۳/۳ — بازه سنی</b>\n\nبازه سنی مورد نظر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_kb),
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
    markup = get_discovery_interests_keyboard(selected)
    inline_kb = list(markup.inline_keyboard)
    inline_kb.append([InlineKeyboardButton(text="❌ انصراف", callback_data="disc_cancel")])
    
    await call.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_kb)
    )
    await call.answer()



async def show_filtered_candidate(call_or_message, state: FSMContext, db_session: AsyncSession):
    """تابع اصلی برای کشیدن کاندیداهای رتبه‌بندی‌شده از دیتابیس و نمایش بهترین گزینه بعدی"""
    caller_tg_id = call_or_message.from_user.id

    data = await state.get_data()
    province = data.get("province")
    interests = data.get("selected_interests") or []
    min_age = data.get("min_age", 0)
    max_age = data.get("max_age", 99)

    # 💡 لیست دیده‌شده‌ها از Redis Set خوانده می‌شود (نه از FSM state)
    viewed_ids = await _get_viewed_ids(caller_tg_id)

    caller = await get_user_by_tg_id(db_session, caller_tg_id)

    # گرفتن کاندیداهای از قبل رتبه‌بندی‌شده (علایق + فعالیت + فاصله + اعتبار)
    candidates = await get_filtered_discovery_candidates(
        session=db_session,
        caller_tg_id=caller_tg_id,
        province=province,
        interests=interests if interests else None,
        min_age=min_age,
        max_age=max_age,
        exclude_ids=viewed_ids,
        limit=_MAX_RESULTS,
    )

    candidate = candidates[0] if candidates else None

    if not candidate:
        text = "😔 کاربری با این مشخصات یافت نشد یا تمام نتایج را مشاهده کردید.\nفیلترها را تغییر دهید و دوباره جستجو کنید."
        kb = _restart_keyboard()
        await bot.send_message(
            chat_id=caller_tg_id,
            text=text,
            reply_markup=kb
        )
        return

    # ذخیره آیدی کاربر در Redis تا دفعه بعد دوباره نمایش داده نشود
    await _add_viewed_id(caller_tg_id, candidate.tg_id)

    badge = _match_quality_label(caller, candidate, set(interests))
    profile_text = f"{badge}\n\n" + _build_profile_card(candidate)
    base_kb = get_user_action_keyboard(candidate.tg_id)

    # 💡 اضافه کردن دکمه کاربر بعدی به پروفایل
    inline_kb = list(base_kb.inline_keyboard)
    inline_kb.append([
        InlineKeyboardButton(text="⏭ کاربر بعدی", callback_data="disc_next_result"),
        InlineKeyboardButton(text="❌ پایان جستجو", callback_data="disc_cancel")
    ])
    action_kb = InlineKeyboardMarkup(inline_keyboard=inline_kb)

    try:
        await bot.send_message(
            chat_id=caller_tg_id,
            text=profile_text,
            reply_markup=action_kb,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.error("Failed to send discovery candidate %s: %s", candidate.tg_id, exc)


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

    # 💡 ریست کردن لیست کاربران دیده‌شده (Redis Set) برای جلوگیری از نمایش تکراری
    await state.update_data(min_age=min_age, max_age=max_age)
    await _clear_viewed_ids(call.from_user.id)
    await state.set_state(DiscoveryStates.showing_results)
    
    await call.answer("⏳ در حال جستجو...")
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
        
    await show_filtered_candidate(call, state, db_session)

@router.callback_query(DiscoveryStates.showing_results, F.data == "disc_next_result")
async def disc_next_result(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    """هندلر دکمه کاربر بعدی"""
    await call.answer()
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    await show_filtered_candidate(call, state, db_session)

@router.callback_query(F.data == "disc_restart")
async def disc_restart(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await _clear_viewed_ids(call.from_user.id)
    await state.set_state(DiscoveryStates.choosing_province)
    
    # 💡 اصلاح باگ ۴: مدیریت استثنا برای متد حذف پیام تلگرام
    # اگر پیام قدیمی‌تر از ۴۸ ساعت باشد، تلگرام اجازه حذف نمی‌دهد و ربات بدون این بلوک کرش می‌کرد
    try:
        await call.message.delete()
    except TelegramBadRequest:
        logger.warning(
            "Could not delete discovery message for user %d (message likely older than 48 hours).", 
            call.from_user.id
        )
    except Exception as exc:
        logger.error("Unexpected error deleting message in disc_restart: %s", exc)
    
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

