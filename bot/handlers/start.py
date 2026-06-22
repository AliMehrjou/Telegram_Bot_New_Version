"""
bot/handlers/start.py — Production-Ready Onboarding & Main Menu Handler
=========================================================================
Telegram Anonymous Dating Bot | aiogram 3.x + FastAPI + SQLAlchemy 2.0 Async

Covers:
  - /start command with FSM anti-hijack guard and referral deep-link processing
  - Step-by-step onboarding: Gender → Age → Province → City
  - All 8 main-menu reply-button actions
  - Anonymous support messaging pipeline

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REQUIRED CHANGES IN OTHER FILES BEFORE THIS MODULE IS FULLY FUNCTIONAL:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. bot/states/states.py — add `waiting_for_province` to OnboardingStates:

    class OnboardingStates(StatesGroup):
        waiting_for_gender   = State()
        waiting_for_age      = State()
        waiting_for_province = State()   # ← ADD THIS
        waiting_for_city     = State()

2. database/models/models.py — add new columns to the User model:

    province:              Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    coin_balance:          Mapped[int]           = mapped_column(Integer, default=3,  nullable=False)
    total_earned_coins:    Mapped[int]           = mapped_column(Integer, default=3,  nullable=False)
    total_spent_coins:     Mapped[int]           = mapped_column(Integer, default=0,  nullable=False)

3. database/queries/crud.py — update `complete_user_registration` signature to:

    async def complete_user_registration(
        session: AsyncSession,
        tg_id: int,
        gender: str,
        age: int,
        province: str,   # ← ADD THIS
        city: str,
    ) -> bool:
        ...
        user.province = province
        ...
        # Give 5 coins to the new user
        user.coin_balance += 5
        # Give 5 coins to the referrer
        if user.referrer_id:
            referrer = await get_user_by_tg_id(session, user.referrer_id)
            if referrer:
                referrer.coin_balance += 5
                referrer.total_earned_coins += 5

    Also implement:
    async def get_user_friends(session: AsyncSession, tg_id: int) -> list[User]:
        ...  # Query Friendship join table once you build that feature

4. bot/keyboards/inline.py — add the three new keyboard factory functions:

    def get_nearby_options_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👧 دخترها",        callback_data="nearby_female")],
            [InlineKeyboardButton(text="👦 پسرها",         callback_data="nearby_male")],
            [InlineKeyboardButton(text="👫 هردو جنسیت",   callback_data="nearby_both")],
        ])

    def get_search_options_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 کاربران آنلاین پسر",    callback_data="search_online_male")],
            [InlineKeyboardButton(text="🟢 کاربران آنلاین دختر",   callback_data="search_online_female")],
            [InlineKeyboardButton(text="🗺️ هم‌استانی‌ها",           callback_data="search_same_province")],
            [InlineKeyboardButton(text="📍 هم‌شهری‌ها",             callback_data="search_same_city")],
            [InlineKeyboardButton(text="💬 کاربران بدون چت و دیت", callback_data="search_no_chat")],
        ])

    def get_coins_menu_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📜 تاریخچه تراکنش‌ها",     callback_data="coins_history")],
            [InlineKeyboardButton(text="💎 خرید سکه",               callback_data="coins_purchase")],
        ])
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import html
import json
import logging
import os
from pathlib import Path
from typing import Optional

from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import bot, redis_client
from matching_bot_project.bot.keyboards.inline import (
    get_coins_menu_keyboard,
    get_gender_keyboard,
    get_matching_type_keyboard,
    get_nearby_options_keyboard,
    get_search_options_keyboard,
    get_terms_keyboard,                    # ← NEW
)
from matching_bot_project.bot.keyboards.reply import (
    get_cancel_keyboard,
    get_main_menu_keyboard,
)
from matching_bot_project.bot.states.states import (
    ChatStates,
    MatchingStates,
    OnboardingStates,
    QuestionnaireStates,
)
from matching_bot_project.database.queries import crud
from matching_bot_project.services import matching_engine

logger = logging.getLogger(__name__)
router = Router(name="start_handler")


# ─── Local FSM States ──────────────────────────────────────────────────────────
# Move SupportStates to bot/states/states.py once stabilised.

class SupportStates(StatesGroup):
    """FSM states for the anonymous support messaging pipeline."""

    waiting_for_support_message = State()


# ─── Module-level constants ────────────────────────────────────────────────────

#: States in which /start must be blocked to prevent pipeline corruption.
_ACTIVE_PIPELINE_STATES: frozenset[str] = frozenset(
    filter(
        None,
        [
            ChatStates.anonymous_chat_active.state,
            MatchingStates.waiting_in_queue.state,
            QuestionnaireStates.answering_questions.state,
            QuestionnaireStates.waiting_for_partner_answer.state,
        ],
    )
)

GENDER_LABELS: dict[str, str] = {
    "Male": "آقا 🙋‍♂️",
    "Female": "خانم 🙋‍♀️",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  /start  — Entry point
# ═══════════════════════════════════════════════════════════════════════════════
def get_gender_reply_keyboard() -> ReplyKeyboardMarkup:
    """ساخت کیبورد متنی معمولی برای انتخاب جنسیت موقع ثبت‌نام"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="آقا 🙋‍♂️"), KeyboardButton(text="خانم 🙋‍♀️")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="جنسیت خود را انتخاب کنید..."
    )


@router.message(CommandStart())
async def handle_start_command(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    """
    Handles the /start command.

    Flow:
      1. Check FSM state: Remove from queue if waiting, or block if in active chat/questionnaire.
      2. Clear any stale FSM state.
      3. Send returning registered users straight to the main menu.
      4. Parse optional referral deep link (/start ref_<TGID>).
      5. Create a new DB record for first-time users (handles race conditions).
      6. Start the onboarding FSM sequence.
    """
    tg_id = message.from_user.id
    current_state = await state.get_state()

    # ── Guard: Manage queue escapes or reject /start mid-pipeline ─────────────
    if current_state == MatchingStates.waiting_in_queue.state:
        # Gracefully remove from queue instead of trapping the user
        await matching_engine.remove_from_queue(tg_id)
        await state.clear()
    elif current_state in _ACTIVE_PIPELINE_STATES:
        await message.answer(
            "⚠️ شما در میانه یک فرآیند فعال (پرسشنامه یا چت ناشناس) هستید.\n"
            "لطفاً ابتدا فرآیند جاری را پایان دهید و سپس مجدداً /start را ارسال کنید."
        )
        return

    await state.clear()

    user = await crud.get_user_by_tg_id(db_session, tg_id)

    # ── Returning, fully-registered user ──────────────────────────────────────
    if user and user.completed_registration:
        safe_name = html.escape(user.first_name or "کاربر")
        await message.answer(
            f"👋 خوش آمدید مجدد، <b>{safe_name}</b>!\n"
            "آماده شروع دیت عاطفی جدید هستید؟ از منوی زیر استفاده کنید 👇",
            reply_markup=get_main_menu_keyboard(),
        )
        return

# ── Parse referral deep link (/start ref_<TGID>) ──────────────────────────
    referrer_id: Optional[int] = None
    
    # 1. Check direct command args
    if command.args and command.args.startswith("ref_"):
        try:
            ref_id_candidate = int(command.args.split("_", 1)[1])
            if ref_id_candidate != tg_id:
                referrer = await crud.get_user_by_tg_id(db_session, ref_id_candidate)
                if referrer:
                    referrer_id = ref_id_candidate
        except Exception:
            pass

    # 2. If no referrer in args, check Redis for pending referrals from Force Join
    if not referrer_id:
        pending_ref = await redis_client.get(f"pending_ref:{tg_id}")
        if pending_ref:
            try:
                ref_id_candidate = int(pending_ref.decode('utf-8') if isinstance(pending_ref, bytes) else pending_ref)
                if ref_id_candidate != tg_id:
                    referrer = await crud.get_user_by_tg_id(db_session, ref_id_candidate)
                    if referrer:
                        referrer_id = ref_id_candidate
            except Exception:
                pass
            
            await redis_client.delete(f"pending_ref:{tg_id}")
    # ── Create new user record in MySQL ───────────────────────────────────────
    if not user:
        try:
            user = await crud.create_user(
                session=db_session,
                tg_id=tg_id,
                first_name=message.from_user.first_name or "کاربر",
                username=message.from_user.username,
                referrer_id=referrer_id,
            )
            await db_session.commit()
        except IntegrityError:
            # Another coroutine inserted this user between our SELECT and INSERT.
            await db_session.rollback()
            user = await crud.get_user_by_tg_id(db_session, tg_id)
        except Exception:
            logger.exception("Unexpected error creating user %d in DB", tg_id)
            await message.answer(
                "⚠️ خطای سرور هنگام ثبت حساب. لطفاً چند لحظه صبر کرده و مجدداً تلاش کنید."
            )
            return

    # ── Begin onboarding — step 1: gender selection ───────────────────────────

    gender_reply_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="آقا 🙋‍♂️"), KeyboardButton(text="خانم 🙋‍♀️")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="جنسیت خود را انتخاب کنید..."
    )

# ── Begin onboarding — step 0: terms acceptance ───────────────────────
    await message.answer(
        f"👋 <b>{html.escape(message.from_user.first_name or 'کاربر')}</b> عزیز "
        "به ربات دیت ناشناس خوش اومدی.\n\n"
        "جهت استفاده از ربات باید همواره از قوانین ربات پیروی کنید و "
        "هرگونه عدم رعایت و قانون‌شکنی مساوی با مسدود شدن اکانت شما و "
        "ثبت تخلف قانونی خواهد شد، پس لطفاً قوانین را رعایت بفرمایید "
        "تا به مشکل نخورید. 🙏",
        reply_markup=get_terms_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(OnboardingStates.waiting_for_terms_acceptance)

# ── Terms: show rules text ─────────────────────────────────────────────────
@router.callback_query(
    OnboardingStates.waiting_for_terms_acceptance,
    F.data == "terms_show",
)
async def show_terms_for_acceptance(call: CallbackQuery) -> None:
    try:
        json_path = Path("json_files/rules.json")
        if not json_path.exists():
            json_path = Path("/app/json_files/rules.json")
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rules_text = "\n".join(data.get("rules_text", []))
    except Exception:
        rules_text = "⚠️ خطا در بارگذاری قوانین."
    await call.answer()
    await call.message.answer(rules_text, parse_mode="HTML")


# ── Terms: user accepted → proceed to gender step ─────────────────────────

@router.callback_query(
    OnboardingStates.waiting_for_terms_acceptance,
    F.data == "terms_accept",
)
async def accept_terms(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("✅ قوانین پذیرفته شد!")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    gender_reply_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="آقا 🙋‍♂️"), KeyboardButton(text="خانم 🙋‍♀️")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="جنسیت خود را انتخاب کنید...",
    )
    await call.message.answer(
        "✅ ممنون! حالا جنسیت خود را انتخاب کنید 👇",
        reply_markup=gender_reply_kb,
        parse_mode="HTML",
    )
    await state.set_state(OnboardingStates.waiting_for_gender)
    
# ═══════════════════════════════════════════════════════════════════════════════
#  Onboarding FSM — Step 1: Gender
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(
    OnboardingStates.waiting_for_gender,
    F.data.in_({"gender_male", "gender_female"}),
)

@router.message(OnboardingStates.waiting_for_gender, F.text.in_({"آقا 🙋‍♂️", "خانم 🙋‍♀️"}))
async def register_gender(message: Message, state: FSMContext) -> None:
    """دریافت جنسیت از دکمه متنی و هدایت به مرحله بعد (سن)"""
    raw_text = message.text
    
    # نگاشت دقیق متن دکمه به فیلد دیتابیس
    gender = "Male" if "آقا" in raw_text else "Female"
    gender_label = "آقا 🙋‍♂️" if gender == "Male" else "خانم 🙋‍♀️"

    await state.update_data(gender=gender)
    await state.set_state(OnboardingStates.waiting_for_age)
    
    await message.answer(
        f"✅ جنسیت شما ثبت شد: <b>{gender_label}</b>\n\n"
        "سن خود را به صورت عددی وارد کنید (مثال: ۲۵) 👇",
        reply_markup=get_cancel_keyboard(), # نمایش دکمه انصراف معمولی
        parse_mode="HTML"
    )

@router.message(OnboardingStates.waiting_for_gender)
async def reject_unknown_gender_message(message: Message) -> None:
    """جلوگیری از ارسال متن‌های متفرقه به جز دکمه‌های اصلی جنسیت"""
    # تعریف دوباره کیبورد جهت نمایش مجدد در صورت خطای کاربر
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
    gender_reply_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="آقا 🙋‍♂️"), KeyboardButton(text="خانم 🙋‍♀️")]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await message.answer(
        "⚠️ لطفاً جنسیت خود را فقط از طریق یکی از دکمه‌های زیر انتخاب کنید 👇",
        reply_markup=gender_reply_kb
    )


@router.callback_query(OnboardingStates.waiting_for_gender)
async def reject_unknown_gender_callback(call: CallbackQuery) -> None:
    """Silently rejects unexpected callbacks while gender state is active."""
    await call.answer(
        "⚠️ لطفاً از دکمه‌های ارائه‌شده استفاده کنید.",
        show_alert=True,
    )


# ===============================================================================
#  Onboarding FSM — Step 2: Age
# ===============================================================================

@router.message(OnboardingStates.waiting_for_age)
async def register_age(message: Message, state: FSMContext) -> None:
    """دریافت سن و نمایش کیبورد متنی استان‌ها از فایل JSON"""
    if message.text == "❌ انصراف و منوی اصلی":
        await state.clear()
        await message.answer("فرآیند ثبت‌نام لغو شد. برای شروع مجدد /start را ارسال کنید.", reply_markup=ReplyKeyboardRemove())
        return

    raw_input = (message.text or "").strip()
    try:
        age = int(raw_input)
        if not (18 <= age <= 75):
            raise ValueError()
    except ValueError:
        await message.reply("⚠️ سن باید یک عدد صحیح بین ۱۸ تا ۷۵ باشد.\nلطفاً مجدداً وارد کنید (مثال: ۲۵):")
        return

    await state.update_data(age=age)
    await state.set_state(OnboardingStates.waiting_for_province)
    
    # 🛠️ لود کردن داینامیک کیبورد استان‌ها از فایل ادیت پروفایل
    from matching_bot_project.bot.handlers.profile_edit import get_provinces_reply_keyboard
    
    await message.answer(
        "✅ سن شما ثبت شد.\n\n"
        "اکنون <b>استان</b> محل سکونت خود را از کیبورد متنی زیر انتخاب کنید 👇",
        reply_markup=get_provinces_reply_keyboard(),
        parse_mode="HTML"
    )


# ===============================================================================
#  Onboarding FSM — Step 3: Province
# ===============================================================================

@router.message(OnboardingStates.waiting_for_province)
async def register_province(message: Message, state: FSMContext) -> None:
    """دریافت متنی استان از کیبورد و نمایش کیبورد متنی شهرهای همان استان"""
    if message.text == "❌ انصراف و منوی اصلی" or message.text == "🔙 برگشت به منوی اصلی":
        await state.clear()
        await message.answer("فرآیند ثبت‌نام لغو شد. برای شروع مجدد /start را ارسال کنید.", reply_markup=ReplyKeyboardRemove())
        return

    province_raw = (message.text or "").strip()
    
    # لود کردن دیکشنری استان‌ها جهت اعتبارسنجی ورودی کاربر
    from matching_bot_project.bot.handlers.profile_edit import IRAN_DATA, get_cities_reply_keyboard

    if province_raw not in IRAN_DATA:
        await message.answer("⚠️ لطفاً استان خود را فقط و فقط از روی کیبورد متنی زیر انتخاب کنید:")
        return

    await state.update_data(province=province_raw)
    await state.set_state(OnboardingStates.waiting_for_city)
    
    await message.answer(
        f"✅ استان <b>{province_raw}</b> انتخاب شد.\n\n"
        f"حالا <b>شهر</b> محل سکونت خود را از کیبورد زیر انتخاب کنید یا نام آن را بنویسید 👇",
        reply_markup=get_cities_reply_keyboard(province_raw),
        parse_mode="HTML"
    )


# ===============================================================================
#  Onboarding FSM — Step 4: City → Complete registration
# ===============================================================================

@router.message(OnboardingStates.waiting_for_city)
async def register_city(
    message: Message,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    """دریافت شهر، اعتبارسنجی و اتمام فرآیند ثبت‌نام اولیه"""
    if message.text == "❌ انصراف و منوی اصلی" or message.text == "🔙 برگشت به منوی اصلی":
        await state.clear()
        await message.answer("فرآیند ثبت‌نام لغو شد. برای شروع مجدد /start را ارسال کنید.", reply_markup=ReplyKeyboardRemove())
        return

    city_raw = (message.text or "").strip()
    if not city_raw or len(city_raw) > 30:
        await message.reply("⚠️ نام شهر نامعتبر است. لطفاً یک نام معتبر وارد کنید:")
        return

    # حذف جایگزینی با آندscore برای اینکه نام شهرها مثل فایل جی‌سان تمیز ذخیره بشن
    city = city_raw
    data = await state.get_data()
    gender: Optional[str] = data.get("gender")
    age: Optional[int] = data.get("age")
    province: Optional[str] = data.get("province")
    tg_id = message.from_user.id

    if not all([gender, age is not None, province]):
        logger.error("Incomplete onboarding FSM data for user %d — stored data: %s", tg_id, data)
        await state.clear()
        await message.answer("⚠️ اطلاعات نشست شما ناقص یا منقضی شده است.\nلطفاً مجدداً از /start شروع کنید.", reply_markup=ReplyKeyboardRemove())
        return

    try:
        success: bool = await crud.complete_user_registration(
            session=db_session,
            tg_id=tg_id,
            gender=gender,
            age=age,
            province=province,
            city=city,
        )
    except Exception as e:
        logger.exception("complete_user_registration raised unexpectedly for user %d", tg_id)
        await db_session.rollback()
        await message.answer("⚠️ خطای سرور در ذخیره اطلاعات. لطفاً مجدداً تلاش کنید.")
        return

    if not success:
        await message.answer("⚠️ مشکلی در ثبت اطلاعات به وجود آمد. لطفا /start را مجددا ارسال کنید.")
        return

    await db_session.commit()
    await state.clear()
    await message.answer(
        "🥳 <b>ثبت‌نام شما با موفقیت تکمیل شد!</b>\n"
        "🎁 <b>۵ سکه</b> به عنوان پاداش تکمیل پروفایل به حساب شما واریز شد.\n\n"
        "حالا می‌توانید وارد مچ‌یابی شده و دیت جدیدی را آغاز کنید.\n"
        "از منوی اصلی زیر استفاده کنید 👇",
        reply_markup=get_main_menu_keyboard(),
    )

# ═══════════════════════════════════════════════════════════════════════════════
#  Main Menu — "⚡️ شروع دیت ناشناس"
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == "⚡️ شروع دیت ناشناس")
async def start_anonymous_dating(
    message: Message, db_session: AsyncSession
) -> None:
    """
    Validates that the user is registered and not already in a match,
    then presents the Free vs. VIP matching type selection keyboard.
    The subsequent match_random / match_vip callbacks are handled in matching.py.
    """
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not user or not user.completed_registration:
        await message.answer(
            "⚠️ ابتدا باید ثبت‌نام را تکمیل کنید.\n"
            "دستور /start را ارسال کنید."
        )
        return

    try:
        active_match = await crud.get_active_match(db_session, tg_id)
    except Exception:
        logger.exception("get_active_match failed for user %d", tg_id)
        await message.answer("⚠️ خطای سرور. لطفاً مجدداً تلاش کنید.")
        return

    if active_match:
        await message.answer(
            "⚠️ شما در حال حاضر در یک دیت فعال هستید!\n"
            "لطفاً ابتدا آن را پایان دهید."
        )
        return

    await message.answer(
        "🎯 <b>نوع مچ‌یابی مورد نظر خود را انتخاب کنید:</b>\n\n"
        "🎲 <b>مچ تصادفی:</b> جفت‌یابی رایگان در سطح کشوری با جنس مخالف.\n\n"
        "👑 <b>مچ پیشرفته (VIP):</b> جفت‌یابی فیلتردار هم‌شهری (نیاز به سکه).",
        reply_markup=get_matching_type_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Menu — "📍 نزدیک من"
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == "📍 نزدیک من")
async def show_nearby_people(message: Message, db_session: AsyncSession) -> None:
    """Presents gender-filter options for nearby-user discovery."""
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if not user or not user.completed_registration:
        await message.answer("⚠️ ابتدا ثبت‌نام خود را تکمیل کنید. /start")
        return

    await message.answer(
        "لطفاً نوع افراد نزدیک مورد نظر خود را انتخاب کنید:",
        reply_markup=get_nearby_options_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Menu — 🔍 جستجوی کاربران
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == "🔍 جستجوی کاربران")
async def show_user_search(message: Message, db_session: AsyncSession) -> None:
    """Presents advanced user-search category options."""
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if not user or not user.completed_registration:
        await message.answer("⚠️ ابتدا ثبت‌نام خود را تکمیل کنید. /start")
        return

    await message.answer(
        "لطفاً از لیست زیر گزینه مورد نظر خود را انتخاب کنید:",
        reply_markup=get_search_options_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Menu — 👥 دوستان من
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == "👥 دوستان من")
async def show_friends_list(message: Message, db_session: AsyncSession) -> None:
    """
    Fetches and renders the user's friends list as clickable inline buttons.
    """
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user or not user.completed_registration:
        await message.answer("⚠️ ابتدا ثبت‌نام خود را تکمیل کنید. /start")
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
            "👥 <b>لیست دوستان شما:</b>\n\n"
            "هنوز دوستی ندارید.\n"
            "پس از پایان دیت‌های موفق می‌توانید کاربران را به لیست دوستان خود اضافه کنید.",
            parse_mode="HTML"
        )
        return

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    # ساخت دکمه شیشه‌ای مجزا برای هر دوست
    keyboard = []
    for friend in friends:
        safe_friend_name = friend.first_name or "کاربر"
        label = f"{safe_friend_name} ({friend.age} سال)"
        # با زدن این دکمه، دقیقا همون پنلی باز میشه که عکسشو فرستادی
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"view_profile_{friend.tg_id}")])
        
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await message.answer(
        "👥 <b>لیست دوستان شما:</b>\nبرای مشاهده پروفایل و مدیریت هر شخص، روی نام او کلیک کنید 👇",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Menu — 🪙 سکه‌های من
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == "🪙 سکه‌های من")
async def show_coin_wallet(message: Message, db_session: AsyncSession) -> None:
    """
    Renders the user's full wallet summary.
    Uses settings.BOT_USERNAME to fetch the bot username statically, avoiding API rate limits.
    """
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user or not user.completed_registration:
        await message.answer("⚠️ ابتدا ثبت‌نام خود را تکمیل کنید. /start")
        return

    # Safe fallbacks for columns that may not exist before migration
    coin_balance: int = getattr(user, "coin_balance", user.vip_quota)
    coins_spent: int = getattr(user, "total_spent_coins", 0)
    total_earned: int = getattr(user, "total_earned_coins", coin_balance + coins_spent)

    # Retrieve bot username securely from environment/settings config
    # rather than blocking with an extra network call (bot.get_me())
    invite_link = f"https://t.me/{settings.BOT_USERNAME}?start=ref_{tg_id}"

    wallet_text = (
        "🪙 <b>کیف پول سکه شما:</b>\n\n"
        f"💰 موجودی فعلی: <b>{coin_balance}</b> سکه\n"
        f"📈 مجموع درآمد: <b>{total_earned}</b> سکه\n"
        f"📉 مجموع مصرف‌شده: <b>{coins_spent}</b> سکه\n\n"
        "──────────────────────────────\n"
        "🔗 <b>لینک دعوت اختصاصی شما:</b>\n"
        f"<code>{invite_link}</code>\n\n"
        "به ازای هر دوستی که با لینک شما ثبت‌نام کامل کند، <b>۵ سکه</b> دریافت می‌کنید!"
    )

    # get_coins_menu_keyboard may not exist yet — handle gracefully
    try:
        markup = get_coins_menu_keyboard()
    except Exception:
        logger.info("get_coins_menu_keyboard not available; sending without extra markup.")
        markup = None

    await message.answer(wallet_text, reply_markup=markup)

# ═══════════════════════════════════════════════════════════════════════════════
#  Main Menu — 📜 قوانین
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == "📜 قوانین")
async def show_rules(message: Message):
    """خواند داینامیک قوانین ربات از فایل JSON موجود در json_files"""
    try:
        # تنظیم مسیر فایل قوانین
        json_path = Path("json_files/rules.json")

        if not json_path.exists():
            # مسیر بک‌آپ برای محیط داخل کانتینر داکر
            json_path = Path("/app/json_files/rules.json")

        if not json_path.exists():
            return await message.answer("⚠️ فایل قوانین و مقررات ربات یافت نشد!")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        rules_data = data.get("rules_text", "متنی یافت نشد.")
        
        
        if isinstance(rules_data, list):
            rules_text = "\n".join(rules_data)
        else:
            rules_text = rules_data

        await message.answer(rules_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error reading rules.json: {e}", exc_info=True)
        await message.answer("❌ خطایی در بازخوانی قوانین ربات رخ داد.")


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Menu — 📞 پشتیبانی  (two-step handler)
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == "📞 پشتیبانی")
async def start_support_chat(message: Message, state: FSMContext) -> None:
    """Prompts the user to type an anonymous support message."""
    await message.answer(
        "📞 <b>ارتباط با تیم پشتیبانی:</b>\n\n"
        "پیام خود را تایپ کنید. پیام شما به صورت <b>کاملاً ناشناس</b> "
        "برای تیم پشتیبانی ارسال می‌شود.\n\n"
        "برای لغو از دکمه «❌ انصراف» استفاده کنید 👇",
        reply_markup=get_cancel_keyboard(),
    )
    await state.set_state(SupportStates.waiting_for_support_message)


@router.message(SupportStates.waiting_for_support_message)
async def receive_support_message(message: Message, state: FSMContext) -> None:
    """
    Forwards the user's support message anonymously to all configured admin IDs
    with attached inline actions for quick reply or ban.
    """
    if message.text == "❌ انصراف و منوی اصلی":
        await state.clear()
        await message.answer(
            "بازگشت به منوی اصلی.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    if not message.text:
        await message.reply(
            "⚠️ لطفاً پیام خود را به صورت متنی ارسال کنید.\n"
            "برای لغو از دکمه «❌ انصراف» استفاده کنید."
        )
        return

    tg_id = message.from_user.id
    safe_user_msg = html.escape(message.text)
    
    admin_notification = (
        "📩 <b>پیام پشتیبانی ناشناس جدید:</b>\n\n"
        f"{safe_user_msg}\n\n"
        "──────────────────────────────\n"
        f"👤 شناسه کاربر: <code>{tg_id}</code>"
    )

    # 🛠️ ساخت دکمه‌های شیشه‌ای عملیات سریع برای ادمین
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 پاسخ به کاربر", callback_data=f"admin_reply_{tg_id}")],
        [InlineKeyboardButton(text="⛔️ بن کردن کاربر", callback_data=f"admin_ban_{tg_id}")]
    ])

    delivered_count = 0
    for admin_id in settings.parsed_admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id, 
                text=admin_notification, 
                reply_markup=admin_kb,
                parse_mode="HTML"
            )
            delivered_count += 1
        except Exception:
            logger.warning(
                "Failed to deliver support message to admin %d", admin_id
            )

    if delivered_count > 0:
        await message.answer(
            "✅ پیام شما با موفقیت به تیم پشتیبانی ارسال شد.\n"
            "در اسرع وقت پاسخ داده خواهد شد.",
            reply_markup=get_main_menu_keyboard(),
        )
    else:
        await message.answer(
            "⚠️ در ارسال پیام به پشتیبانی خطایی رخ داد.\n"
            "لطفاً مستقیماً از طریق پشتیبانی تماس بگیرید.",
            reply_markup=get_main_menu_keyboard(),
        )

    await state.clear()

@router.callback_query(F.data == "check_membership")
async def process_check_membership_callback(
    call: CallbackQuery, 
    state: FSMContext, 
    db_session: AsyncSession
) -> None:
    """هندلر دکمه شیشه‌ای بررسی عضویت مجدد که توسط میدل‌ور فورس‌جوین پاس داده می‌شود"""
    
    await call.answer("عضویت شما تایید شد! خیلی خوش اومدی 🌹", show_alert=True)
    
    try:
        await call.message.delete()
    except Exception:
        pass 
        
    tg_id = call.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    
    # ── کاربر قبلا ثبت‌نام کرده است ──
    if user and user.completed_registration:
        await call.message.answer(
            "✅ عضویت تایید شد.\nآماده شروع دیت جدید هستید؟ 👇",
            reply_markup=get_main_menu_keyboard()
        )
        return

    # ── کاربر جدید است (به خاطر میدل‌ور، دیتابیس او ساخته نشده است) ──
    if not user:
        # بررسی رفرال از ردیس
        referrer_id: Optional[int] = None
        pending_ref = await redis_client.get(f"pending_ref:{tg_id}")
        if pending_ref:
            try:
                ref_id_candidate = int(pending_ref.decode('utf-8') if isinstance(pending_ref, bytes) else pending_ref)
                if ref_id_candidate != tg_id:
                    referrer = await crud.get_user_by_tg_id(db_session, ref_id_candidate)
                    if referrer:
                        referrer_id = ref_id_candidate
            except Exception:
                pass
            await redis_client.delete(f"pending_ref:{tg_id}")

        # ساخت رکورد کاربر در دیتابیس
        try:
            user = await crud.create_user(
                session=db_session,
                tg_id=tg_id,
                first_name=call.from_user.first_name or "کاربر",
                username=call.from_user.username,
                referrer_id=referrer_id,
            )
            await db_session.commit()
        except IntegrityError:
            await db_session.rollback()
        except Exception as exc:
            logger.error("Error creating user after force join: %s", exc)
            await call.message.answer("⚠️ خطای سرور. لطفاً مجدداً /start را ارسال کنید.")
            return

    # ── هدایت کاربر جدید به مرحله تایید قوانین ──
    await call.message.answer(
        f"👋 <b>{html.escape(call.from_user.first_name or 'کاربر')}</b> عزیز "
        "به ربات دیت ناشناس خوش اومدی.\n\n"
        "جهت استفاده از ربات باید همواره از قوانین ربات پیروی کنید و "
        "هرگونه عدم رعایت و قانون‌شکنی مساوی با مسدود شدن اکانت شما و "
        "ثبت تخلف قانونی خواهد شد، پس لطفاً قوانین را رعایت بفرمایید "
        "تا به مشکل نخورید. 🙏",
        reply_markup=get_terms_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(OnboardingStates.waiting_for_terms_acceptance)