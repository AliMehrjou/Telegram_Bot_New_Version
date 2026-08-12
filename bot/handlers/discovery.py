"""
bot/handlers/discovery.py
──────────────────────────────────────────────────────────────────────────────
Two independent discovery flows share this router:

  1) SWIPE FLOW       entry: "💘 کشف کاربران"
  2) FILTER WIZARD     entry: "🔍 جستجوی کاربران"
──────────────────────────────────────────────────────────────────────────────
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError, TelegramForbiddenError
from aiogram import Router, F
from aiogram.filters import StateFilter
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
from matching_bot_project.bot.core.constants import ReplyBtn
from matching_bot_project.bot.handlers.profile_edit import IRAN_DATA
from matching_bot_project.bot.core.formatters import build_unified_profile_card
from matching_bot_project.bot.keyboards.inline import (
    get_discovery_age_keyboard,
    get_discovery_interests_keyboard,
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
    consume_vip_quota_or_coin

)
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)
router = Router(name="discovery_handler")

DAILY_LIKE_LIMIT = 30
_MAX_RESULTS      = 5

def get_back_to_discovery_keyboard() -> InlineKeyboardMarkup:
    """یک کیبورد ساده شامل دکمه بازگشت به منوی رادار (کشف)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت به منوی کشف", callback_data="open_discovery_menu")]
    ])

async def safe_delete_message(message: Message):
    """
    حذف امن پیام. اگر پیام قدیمی‌تر از 48 ساعت باشد و تلگرام اجازه حذف ندهد، 
    حداقل کیبورد شیشه‌ای آن را پاک می‌کند تا کاربر دچار خطای استیت نشود.
    """
    try:
        await message.delete()
    except TelegramBadRequest:
        try:
            await message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Unexpected error in safe_delete_message: {e}")

import math

USERS_PER_PAGE = 5

def _discovery_list_keyboard(current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    """کیبورد اختصاصی برای ناوبری در لیست کشف کاربران"""
    rows = []
    nav_row = []
    
    if current_page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ صفحه قبل", callback_data=f"disc_page_{current_page - 1}"))
    if current_page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="صفحه بعد ▶️", callback_data=f"disc_page_{current_page + 1}"))
    
    if nav_row:
        rows.append(nav_row)
        
    rows.append([
        InlineKeyboardButton(text="🔄 تغییر فیلترها", callback_data="disc_restart"),
        InlineKeyboardButton(text="🔙 منوی کشف", callback_data="open_discovery_menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _distance_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب شعاع فاصله برای جستجوی افراد نزدیک"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 تا ۵۰ کیلومتر (نزدیک‌ترین‌ها)", callback_data="dist_0_50")],
        [InlineKeyboardButton(text="🚗 تا ۱۰۰ کیلومتر (هم‌شهری/شهرهای مجاور)", callback_data="dist_50_100")],
        [InlineKeyboardButton(text="✈️ تا ۲۰۰ کیلومتر (استان و فواصل دورتر)", callback_data="dist_100_200")],
        [InlineKeyboardButton(text="🌍 فرقی نمی‌کنه (هرجا بود)", callback_data="dist_any")],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="disc_cancel")]
    ])


@router.callback_query(F.data == "disc_cancel")
async def cancel_discovery_wizard(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("جستجو لغو شد.")
    await state.clear()
    await safe_delete_message(call.message) # 👈 استفاده از تابع امن
    await call.message.answer("❌ جستجوی کاربران لغو شد. به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())

async def show_discovery_list_page(call_or_message, state: FSMContext, db_session: AsyncSession, page: int = 0):
    """تابع یکپارچه برای تولید لیست صفحه‌بندی شده از کاربران پیدا شده"""
    caller_tg_id = call_or_message.from_user.id
    caller = await get_user_by_tg_id(db_session, caller_tg_id)
    if not caller:
        return

    data = await state.get_data()
    province = data.get("province")
    interests = data.get("selected_interests") or []
    min_age = data.get("min_age", 0)
    max_age = data.get("max_age", 99)
    distance_filter = data.get("distance_filter")
    discovery_filter = data.get("discovery_filter")

    # تشخیص خودکار جنسیت مخالف
    target_gender = None
    if caller and caller.gender:
        _OPPOSITE_GENDER = {"male": "female", "female": "male", "boy": "girl", "girl": "boy"}
        target_gender = _OPPOSITE_GENDER.get(caller.gender.lower(), None)

    # بررسی کسر سکه برای جستجوی پیشرفته (فقط یک بار در هر جستجو)
    is_advanced_search = bool(province or interests or min_age > 0 or max_age < 99)
    if is_advanced_search:
        has_paid = data.get("advanced_search_paid", False)
        if not has_paid:
            has_funds = await consume_vip_quota_or_coin(db_session, caller_tg_id, cost=1, description="هزینه جستجوی پیشرفته لیستی")
            if not has_funds:
                error_text = "❌ موجودی سکه یا سهمیه VIP شما برای جستجوی پیشرفته کافی نیست! لطفاً حساب خود را شارژ کنید."
                kb = _restart_keyboard()
                if isinstance(call_or_message, CallbackQuery):
                    await call_or_message.message.answer(error_text, reply_markup=kb)
                else:
                    from matching_bot_project.bot.core.loader import bot
                    await bot.send_message(chat_id=caller_tg_id, text=error_text, reply_markup=kb)
                return
            await state.update_data(advanced_search_paid=True)

    # واکشی کاندیداها (استخر بزرگتر برای صفحه‌بندی بدون حذف بازدیدشده‌ها)
    candidates = await get_filtered_discovery_candidates(
        session=db_session,
        caller_tg_id=caller_tg_id,
        province=province,
        interests=interests if interests else None,
        min_age=min_age,
        max_age=max_age,
        distance_filter=distance_filter,
        gender_filter=target_gender,
        exclude_ids=[],  # ❌ در حالت لیستی، بازدیدشده‌ها را اینجا حذف نمی‌کنیم تا لیست همیشه پر بماند
        limit=50,        # گرفتن ۵۰ نفر برتر برای ۵ الی ۱۰ صفحه
        pool_size=100,
    )

    if not candidates:
        text = (
            "🙈 <b>ای وای! کسی با این مشخصات پیدا نشد!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "شاید یه کم زیاد سخت‌گیری کردی! یا دور و برت فعلاً کسی در دسترس نیست. 😉\n\n"
            "💡 <b>یه پیشنهاد دوستانه:</b>\n"
            "اگه فیلترهات رو یه کوچولو بازتر کنی، شانس پیدا کردن یه آدم باحال خیلی بیشتر میشه!\n"
        )
        kb = _restart_keyboard()
        if isinstance(call_or_message, CallbackQuery):
            try:
                await call_or_message.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except TelegramBadRequest:
                try:
                    await call_or_message.message.delete()
                except Exception:
                    pass
                await call_or_message.message.answer(text, reply_markup=kb, parse_mode="HTML")
        else:
            await call_or_message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    # منطق صفحه‌بندی (Pagination)
    total_pages = math.ceil(len(candidates) / USERS_PER_PAGE)
    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0

    start_idx = page * USERS_PER_PAGE
    end_idx = start_idx + USERS_PER_PAGE
    page_candidates = candidates[start_idx:end_idx]

    # ساخت متن لیست
    lines = [
        f"🔍 <b>نتایج جستجوی شما (صفحه {page + 1} از {total_pages}):</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
    ]

    for idx, cand in enumerate(page_candidates, start=start_idx + 1):
        badge = _match_quality_label(caller, cand, set(interests))
        # تضمین وجود شناسه
        pub_id = cand.public_id if cand.public_id else f"user_{cand.tg_id}"
        lines.append(f"{idx}. 👤 شناسه: <code>/{pub_id}</code> | تطابق: {badge}")

    lines.append("\n👇 <i>برای مشاهده پروفایل کامل، روی شناسه هر شخص کلیک کنید.</i>")
    final_text = "\n".join(lines)
    
    action_kb = _discovery_list_keyboard(page, total_pages)

    # ارسال یا ویرایش پیام
    if isinstance(call_or_message, CallbackQuery):
        try:
            await call_or_message.message.edit_text(final_text, reply_markup=action_kb, parse_mode="HTML")
        except TelegramBadRequest:
            try:
                await call_or_message.message.delete()
            except Exception:
                pass
            await call_or_message.message.answer(final_text, reply_markup=action_kb, parse_mode="HTML")
    else:
        from matching_bot_project.bot.core.loader import bot
        await bot.send_message(
            chat_id=caller_tg_id,
            text=final_text,
            reply_markup=action_kb,
            parse_mode="HTML",
        )

@router.callback_query(DiscoveryStates.showing_results, F.data.startswith("disc_page_"))
async def handle_discovery_pagination(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """هندلر دکمه‌های صفحه قبل / صفحه بعد در لیست کشف کاربران"""
    page = int(call.data.removeprefix("disc_page_"))
    await call.answer()
    # جلوگیری از تداخل استیت زامبی در صورت وجود دیت فعال
    from matching_bot_project.database.queries import crud
    if await crud.get_active_match(db_session, call.from_user.id):
        return await call.answer("⚠️ شما در یک چت/دیت فعال هستید!", show_alert=True)
        
    await show_discovery_list_page(call, state, db_session, page=page)


@router.message(F.text == ReplyBtn.DISCOVER)
async def start_discovery(message: Message, state: FSMContext, db_session: AsyncSession):
    # برای ورود به کاوش عمومی، فیلترهای ذخیره شده را پاک می‌کنیم
    await state.update_data(distance_filter=None)
    await show_discovery_main_menu(message, db_session)

from aiogram.exceptions import TelegramBadRequest

def _province_inline_keyboard() -> InlineKeyboardMarkup:
    provinces = ["🌍 همه استان‌ها"] + list(IRAN_DATA.keys())
    inline_kb = []
    
    # چیدن دکمه‌ها به صورت ۲ ستونه
    for i in range(0, len(provinces), 2):
        row = [InlineKeyboardButton(text=provinces[i], callback_data=f"prov_{provinces[i]}")]
        if i + 1 < len(provinces):
            row.append(InlineKeyboardButton(text=provinces[i + 1], callback_data=f"prov_{provinces[i + 1]}"))
        inline_kb.append(row)
    
    inline_kb.append([InlineKeyboardButton(text="❌ انصراف", callback_data="disc_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=inline_kb)


def _restart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 تغییر فیلتر و جستجوی مجدد", callback_data="disc_restart")],
        [
            InlineKeyboardButton(text="🔙 منوی کشف", callback_data="open_discovery_menu"),
            InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="disc_main_menu")
        ],
    ])


def _match_quality_label(
    caller,
    candidate,
    interest_filter: set[str],
) -> str:
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
async def cancel_wizard(message: Message, state: FSMContext, db_session: AsyncSession) -> None: # 👈 db_session اضافه شد
    # 🛡️ گارد زامبی: اگر کاربر در چت است، استیتش را پاک نکن
    from matching_bot_project.database.queries import crud
    if await crud.get_active_match(db_session, message.from_user.id):
        return await message.answer("⚠️ شما در یک دیت فعال هستید! لطفاً برای خروج از دکمه لغو دیت/چت استفاده کنید.")

    current = await state.get_state()
    if current and current.startswith("DiscoveryStates:"):
        await state.clear()
        await message.answer("به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())


@router.message(F.text == ReplyBtn.SEARCH_USERS)
async def start_wizard(message: Message, state: FSMContext, db_session: AsyncSession) -> None: # 👈 db_session اضافه شد
    from matching_bot_project.database.queries import crud
    if await crud.get_active_match(db_session, message.from_user.id):
        return await message.answer("⚠️ شما در یک چت/دیت فعال هستید و نمی‌توانید جستجوی جدیدی آغاز کنید.")

    await state.clear()
    await state.set_state(DiscoveryStates.choosing_province)
    await message.answer(
        "🌍 <b>مرحله اول: اهل کجا باشه؟ (۱ از ۳)</b>\n\n"
        "دوست داری پارتنرت هم‌شهری خودت باشه یا برات فرقی نمی‌کنه؟\n"
        "یکی از گزینه‌های زیر رو انتخاب کن تا بریم مرحله بعدی! 👇",
        reply_markup=_province_inline_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(DiscoveryStates.choosing_province, F.data.startswith("prov_"))
async def receive_province(call: CallbackQuery, state: FSMContext) -> None:
    text = call.data.removeprefix("prov_")

    if text == "🌍 همه استان‌ها":
        province = None
    elif text in IRAN_DATA:
        province = text
    else:
        await call.answer("⚠️ لطفاً استان را انتخاب کنید.", show_alert=True)
        return
    
    await state.update_data(province=province, selected_interests=[])
    await state.set_state(DiscoveryStates.choosing_interests)
    
    markup = get_discovery_interests_keyboard([])
    inline_kb = list(markup.inline_keyboard)
    inline_kb.append([InlineKeyboardButton(text="❌ انصراف", callback_data="disc_cancel")])

    # ویرایش همان پیام به جای ارسال پیام جدید
    await call.message.edit_text(
        "🎨 <b>مرحله دوم: علایق مشترک (۲ از ۳)</b>\n\n"
        "دنبال کسی می‌گردی که چه تفریحاتی داشته باشه؟ چند تا از علاقه‌مندی‌های مهمت رو انتخاب کن تا آدمای شبیه‌تر رو بهت پیشنهاد بدیم.\n\n"
        "<i>(اگه برات مهم نیست، می‌تونی بدون انتخاب گزینه‌ای، دکمه «تأیید و جستجو» رو بزنی)</i> 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_kb),
        parse_mode="HTML",
    )
    await call.answer()

@router.callback_query(DiscoveryStates.choosing_interests, F.data == "disc_int_confirm")
async def confirm_interests(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(DiscoveryStates.choosing_age_range)
    
    markup = get_discovery_age_keyboard()
    inline_kb = list(markup.inline_keyboard)
    inline_kb.append([InlineKeyboardButton(text="❌ انصراف", callback_data="disc_cancel")])
    
    await call.message.edit_text(
        "🎂 <b>مرحله آخر: تو چه سن و سالی باشه؟ (۳ از ۳)</b>\n\n"
        "خب رسیدیم به مرحله آخر! دوست داری پارتنرت تو چه بازه سنی باشه؟ انتخاب کن تا جادوی مچینگ رو شروع کنیم! ✨👇",
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

@router.callback_query(DiscoveryStates.choosing_distance, F.data.startswith("dist_"))
async def receive_distance(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """دریافت شعاع کیلومتر و شروع جستجو"""
    dist_val = call.data.replace("dist_", "")
    
    # 🧹 فاز ۱: پاک کردن فیلترهای مزاحم از State برای جستجوی خالص مسافتی
    # این کار باعث میشه شروط مربوط به استان، سن و... با فیلتر مسافت تداخل پیدا نکنن
    await state.update_data(
        distance_filter=dist_val,
        province=None,
        selected_interests=[],
        min_age=0,
        max_age=99,
        discovery_filter=None
    )
    
    await state.set_state(DiscoveryStates.showing_results)
    await call.answer("⏳ در حال یافتن افراد نزدیک...")
    await safe_delete_message(call.message) # 👈 استفاده از تابع امن
    await show_discovery_list_page(call, state, db_session, page=0)

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

    await state.update_data(min_age=min_age, max_age=max_age)
    await state.set_state(DiscoveryStates.showing_results)
    
    await call.answer("⏳ در حال جستجو...")
    await safe_delete_message(call.message) # 👈 استفاده از تابع امن
    await show_discovery_list_page(call, state, db_session, page=0)



# ---------------------------------------------------------
# ۳. بازنویسی کامل هندلر disc_restart
# ---------------------------------------------------------
@router.callback_query(F.data == "disc_restart")
async def disc_restart(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    await call.answer()
    
    # استخراج دیتای مسیر قبلی قبل از پاکسازی
    data = await state.get_data()
    distance_filter = data.get("distance_filter")
    discovery_filter = data.get("discovery_filter")
    
    await state.clear()
    await safe_delete_message(call.message)
    
    # 🔄 هدایت هوشمند کاربر به مسیر درست
    if discovery_filter:
        # مسیر ۱: فیلترهای مستقیم (هم‌شهری، علایق مشترک و...)
        await state.update_data(discovery_filter=discovery_filter)
        await state.set_state(DiscoveryStates.showing_results)
        await show_discovery_list_page(call, state, db_session, page=0)
        
    elif distance_filter:
        # مسیر ۲: جستجوی افراد نزدیک (مبتنی بر GPS)
        await state.set_state(DiscoveryStates.choosing_distance)
        await call.message.answer(
            "📍 <b>جستجوی افراد نزدیک (مبتنی بر GPS)</b>\n\n"
            "دوست داری پارتنرت حداکثر چقدر ازت فاصله داشته باشه؟\n"
            "یکی از فواصل زیر رو انتخاب کن تا بگردم 👇",
            reply_markup=_distance_keyboard(),
            parse_mode="HTML"
        )
    else:
        # مسیر ۳: جستجوی پیشرفته / ویزارد ۳ مرحله‌ای (استان، سن، علایق)
        await state.set_state(DiscoveryStates.choosing_province)
        await call.message.answer(
            "🌍 <b>مرحله اول: اهل کجا باشه؟ (۱ از ۳)</b>\n\n"
            "دوست داری پارتنرت هم‌شهری خودت باشه یا برات فرقی نمی‌کنه؟\n"
            "یکی از گزینه‌های زیر رو انتخاب کن تا بریم مرحله بعدی! 👇",
            reply_markup=_province_inline_keyboard(),
            parse_mode="HTML",
        )

@router.callback_query(F.data == "disc_main_menu")
async def disc_main_menu(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await safe_delete_message(call.message) # 👈 استفاده از تابع امن
    await call.message.answer("به منوی اصلی بازگشتید.", reply_markup=get_main_menu_keyboard())


# ═══════════════════════════════════════════════════════════════════════════
# v3 NEW: Discovery main menu (replaces 'search users' button in main menu)
# ═══════════════════════════════════════════════════════════════════════════

from matching_bot_project.bot.keyboards.inline import get_discovery_main_menu_keyboard
from matching_bot_project.bot.core.constants import ReplyBtn, Messages


async def show_discovery_main_menu(message: Message, db_session) -> None:
    """v3 NEW: 'کشف کاربران' main menu — combines old 'search users' features."""
    text = (
        "🧭 <b>آدمای جدید پیدا کن!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>✨ <b>به بخش جستجوی پیشرفته خیلی خوش اومدی!</b>\n\n"
        "اینجا می‌تونی دقیقاً همون کسی که تو ذهنته رو پیدا کنی. ما بر اساس شهر، سن و علاقه‌مندی‌های مشترک، بهترین‌ها رو برات سوا می‌کنیم! 🎯</blockquote>\n\n"
        "👇 <i>یکی از گزینه‌های زیر رو بزن تا بگردیم ببینیم کی برات مناسب‌تره:</i>"
    )
    
    await message.answer(
        text=text,
        reply_markup=get_discovery_main_menu_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "disc_nearby")
async def disc_nearby(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """جستجوی افراد نزدیک — نیازمند دریافت لوکیشن از دیتابیس و نمایش کیبورد فواصل"""
    from matching_bot_project.database.queries.crud import get_user_by_tg_id
    
    user = await get_user_by_tg_id(db_session, call.from_user.id)
    
    # 🚀 بررسی مستقیم از روی مدل دیتابیس بدون نیاز به ماژول اضافی
    if not user or user.location_lat is None or user.location_lng is None:
        await call.answer("📍 برای استفاده از این بخش باید لوکیشن (GPS) خود را ثبت کنید!", show_alert=True)
        await call.message.answer(
            "⚠️ <b>موقعیت مکانی شما ثبت نشده است!</b>\n\n"
            "برای پیدا کردن افراد نزدیک، لطفاً از منوی اصلی به بخش <b>«👤 پروفایل من»</b> رفته و با کلیک روی <b>«ویرایش پروفایل»</b>، موقعیت مکانی خود را ثبت کنید.",
            parse_mode="HTML"
        )
        return
        
    await state.set_state(DiscoveryStates.choosing_distance)
    await call.message.edit_text(
        "📍 <b>جستجوی افراد نزدیک (مبتنی بر GPS)</b>\n\n"
        "دوست داری پارتنرت حداکثر چقدر ازت فاصله داشته باشه؟\n"
        "یکی از فواصل زیر رو انتخاب کن تا بگردم 👇",
        reply_markup=_distance_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data == "disc_liked_me")
async def disc_liked_me(call: CallbackQuery, db_read_session: AsyncSession):
    from matching_bot_project.database.queries.crud import get_user_by_tg_id, get_users_who_liked_me
    from datetime import datetime, timezone
    
    user = await get_user_by_tg_id(db_read_session, call.from_user.id)
    from datetime import datetime, timezone
    if not user:
        await call.answer("ابتدا ثبت‌نام کنید.", show_alert=True)
        return

    # FIX PHASE5-HIGH-73: check VIP status.
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    is_vip = user.is_vip or (user.vip_expires_at and user.vip_expires_at > now_utc)

    liked_me = await get_users_who_liked_me(db_read_session, call.from_user.id, limit=20)
    if not liked_me:
        
        await call.message.edit_text(
            "هنوز کسی پروفایل شما را لایک نکرده است. 😔", 
            reply_markup=get_back_to_discovery_keyboard()
        )
        await call.answer()
        return

    if is_vip:
        lines = []
        for u in liked_me:
            # تغییر به فرمت قابل کلیک
            lines.append(f"👤 شناسه: <code>/{u.public_id}</code> — {u.first_name}")
        await call.message.edit_text(
            "💖 <b>افرادی که شما را لایک کرده‌اند:</b>\n\n" + "\n".join(lines),
            reply_markup=get_back_to_discovery_keyboard() # 🛡️ افزودن بازگشت
        )
    else:
        preview_count = min(1, len(liked_me))
        total = len(liked_me)
        preview_lines = []
        for u in liked_me[:preview_count]:
            anon_name = (u.first_name[:2] + "***") if u.first_name and len(u.first_name) >= 2 else "?"
            # تغییر به فرمت قابل کلیک
            preview_lines.append(f"👤 شناسه: <code>/{u.public_id}</code> — {anon_name}")
            
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        upsell_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 برای دیدن همه، VIP شوید", callback_data="vip_show_plans")],
            [InlineKeyboardButton(text="🔙 بازگشت به منوی کشف", callback_data="open_discovery_menu")] # 🛡️ افزودن بازگشت
        ])
        
        await call.message.edit_text(
            f"💖 <b>{total} نفر شما را لایک کرده‌اند.</b>\n\n"
            + "\n".join(preview_lines)
            + f"\n\n🔒 برای دیدن لیست کامل، اشتراک VIP تهیه کنید.",
            reply_markup=upsell_kb,
        )
    await call.answer()


@router.callback_query(F.data == "disc_same_interests")
async def disc_same_interests(call: CallbackQuery, state: FSMContext, db_read_session: AsyncSession):
    """جستجوی هم‌سلیقه‌ها و نمایش در قالب لیست"""
    await state.update_data(discovery_filter="same_interests")
    await state.set_state(DiscoveryStates.showing_results)
    await show_discovery_list_page(call, state, db_read_session, page=0)
    await call.answer()

@router.callback_query(F.data == "disc_same_city")
async def disc_same_city(call: CallbackQuery, state: FSMContext, db_read_session: AsyncSession):
    from matching_bot_project.database.queries.crud import get_user_by_tg_id
    user = await get_user_by_tg_id(db_read_session, call.from_user.id)
    
    if not user or not user.city:
        await call.answer("📍 برای پیدا کردن هم‌شهری، باید ابتدا شهر محل سکونت خود را در پروفایل ثبت کنید!", show_alert=True)
        return

    await state.update_data(discovery_filter="same_city")
    await state.set_state(DiscoveryStates.showing_results)
    await show_discovery_list_page(call, state, db_read_session, page=0)
    await call.answer()

@router.callback_query(F.data == "disc_same_province")
async def disc_same_province(call: CallbackQuery, state: FSMContext, db_read_session: AsyncSession):
    from matching_bot_project.database.queries.crud import get_user_by_tg_id
    user = await get_user_by_tg_id(db_read_session, call.from_user.id)
    
    if not user or not user.province:
        await call.answer("📍 برای پیدا کردن افراد هم‌استان، باید ابتدا استان محل سکونت خود را در پروفایل ثبت کنید!", show_alert=True)
        return

    await state.update_data(discovery_filter="same_province")
    await state.set_state(DiscoveryStates.showing_results)
    await show_discovery_list_page(call, state, db_read_session, page=0)
    await call.answer()

@router.callback_query(F.data == "disc_no_chat")
async def disc_no_chat(call: CallbackQuery, state: FSMContext, db_read_session: AsyncSession):
    await state.update_data(discovery_filter="no_chat")
    await state.set_state(DiscoveryStates.showing_results)
    await show_discovery_list_page(call, state, db_read_session, page=0)
    await call.answer()

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

@router.callback_query(F.data == "disc_blocked")
async def disc_blocked(call: CallbackQuery, db_session):
    """Show blocked users list."""
    from matching_bot_project.database.queries.crud import get_blocked_users
    
    blocked_users, total = await get_blocked_users(db_session, call.from_user.id, limit=50)
    
    if not blocked_users:
        await call.message.edit_text(
            "📭 شما هیچ کاربری را بلاک نکرده‌اید.", 
            reply_markup=get_back_to_discovery_keyboard()
        )
        await call.answer()
        return
        
    kb = []
    for u in blocked_users:
        # نمایش اکانت با public_id و اضافه کردن دکمه آنبلاک مستقل
        kb.append([
            InlineKeyboardButton(text=f"👤 {u.public_id}", callback_data=f"view_profile_{u.tg_id}"),
            InlineKeyboardButton(text="🔓 آنبلاک", callback_data=f"unblock_from_disc_{u.tg_id}")
        ])
        
    kb.append([InlineKeyboardButton(text="🔙 بازگشت به منوی کشف", callback_data="open_discovery_menu")])
    
    await call.message.edit_text(
        f"🚫 <b>کاربران بلاک‌شده ({total} نفر):</b>\n\n"
        "<i>(برای حفظ حریم خصوصی، تنها شناسه عمومی افراد نمایش داده می‌شود. برای رفع مسدودی روی دکمه «آنبلاک» کلیک کنید.)</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("unblock_from_disc_"))
async def unblock_from_disc(call: CallbackQuery, db_session: AsyncSession) -> None:
    target_id_str = call.data.replace("unblock_from_disc_", "")
    if not target_id_str.isdigit():
        return await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        
    target_id = int(target_id_str)
    caller_id = call.from_user.id
    
    from matching_bot_project.database.models.models import BlockList
    from sqlalchemy import delete
    
    # پاک کردن رکورد مسدودی از دیتابیس
    await db_session.execute(
        delete(BlockList).where(
            BlockList.blocker_id == caller_id,
            BlockList.blocked_id == target_id,
        )
    )
    await db_session.commit()
    
    # حذف از حافظه موقت (ردیس)
    try:
        await redis_client.srem(f"user:{caller_id}:blocks", str(target_id))
    except Exception:
        pass
        
    await call.answer("🔓 کاربر با موفقیت از لیست سیاه خارج شد.", show_alert=True)
    
    # رفرش کردن سریع لیست بلاک‌ها روی همون پیام
    await disc_blocked(call, db_session)


@router.callback_query(F.data == "disc_friends")
async def disc_friends(call: CallbackQuery, db_session: AsyncSession):
    """Show friends list with interactive inline buttons."""
    from matching_bot_project.database.queries.crud import get_friends
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    friends = await get_friends(db_session, call.from_user.id)
    
    if not friends:
        await call.message.edit_text(
            "📭 شما هنوز دوستی اضافه نکرده‌اید.", 
            reply_markup=get_back_to_discovery_keyboard()
        )
        await call.answer()
        return
        
    # 🔄 فاز ۴: ساخت کیبورد شیشه‌ای تعاملی برای تک‌تک دوستان
    keyboard = []
    for friend in friends:
        # نمایش فقط public_id برای حفظ حریم خصوصی
        label = f"👤 {friend.public_id}"
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"view_profile_{friend.tg_id}")])
        
    # اضافه کردن دکمه بازگشت به انتهای لیست
    keyboard.append([InlineKeyboardButton(text="🔙 بازگشت به منوی کشف", callback_data="open_discovery_menu")])
    
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await call.message.edit_text(
        "👥 <b>لیست دوستان شما:</b>\n\n"
        "<i>(برای دیدن پروفایل و مدیریت هرکدام، روی آیدی مربوطه کلیک کنید)</i> 👇",
        reply_markup=reply_markup,
        parse_mode="HTML" 
    )
    await call.answer()

@router.message(F.text == ReplyBtn.MY_FRIENDS)
async def show_friends_list(message: Message, db_session: AsyncSession) -> None:
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user or not user.completed_registration:
        await message.answer(_registration_required_text(), parse_mode="HTML")
        return

    try:
        friends = await crud.get_user_friends(db_session, tg_id)
    except AttributeError:
        logger.info("crud.get_user_friends not implemented; returning empty list.")
        friends = []
    except Exception:
        logger.exception("Error fetching friends list for user %d", tg_id)
        friends = []

    if not friends:
        await message.answer(
            f"{PEmoji.PEOPLE} <b>لیست دوستان تو</b>\n\n"
            f"<blockquote>هنوز دوستی نداری {PEmoji.SPARKLES}\n"
            "بعد از دیت‌های موفق می‌تونی افراد رو به لیست دوستات اضافه کنی.</blockquote>",
            parse_mode="HTML"
        )
        return

    keyboard = []
    for friend in friends:
        # تغییر لیبل دکمه برای نمایش فقط public_id به جای اسم و سن
        label = f"👤 {friend.public_id}"
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"view_profile_{friend.tg_id}")])
        
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await message.answer(
        f"{PEmoji.PEOPLE} <b>لیست دوستان تو</b>\n"
        f"برای دیدن پروفایل و مدیریت هرکدوم، روی آیدی‌شون بزن {PEmoji.POINT_DOWN}",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "open_discovery_menu")
async def open_discovery_menu_callback(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    from matching_bot_project.database.queries import crud
    if await crud.get_active_match(db_session, call.from_user.id):
        return await call.answer("⚠️ شما در حال حاضر در یک دیت/چت فعال هستید!", show_alert=True)

    await call.answer()
    await state.clear()
    await safe_delete_message(call.message) # 👈 استفاده از تابع امن
    await show_discovery_main_menu(call.message, db_session)
