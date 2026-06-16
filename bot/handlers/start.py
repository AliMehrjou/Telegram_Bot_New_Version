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
import logging
from typing import Optional

from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.fsm.context import FSMContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import bot, redis_client  # noqa: F401
from matching_bot_project.bot.keyboards.inline import (
    get_coins_menu_keyboard,
    get_gender_keyboard,
    get_matching_type_keyboard,
    get_nearby_options_keyboard,
    get_search_options_keyboard,
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

_BOT_RULES_TEXT = (
    "📜 <b>قوانین و مقررات استفاده از ربات:</b>\n\n"
    "۱. استفاده از کلمات رکیک، توهین‌آمیز یا نژادپرستانه ممنوع است.\n"
    "۲. ارسال محتوای غیراخلاقی یا مستهجن منجر به بن دائمی می‌شود.\n"
    "۳. افشای اطلاعات شخصی دیگران (شماره، آدرس، تصویر) بدون رضایت ممنوع است.\n"
    "۴. هرگونه تبلیغات تجاری یا اسپم در چت‌ها ممنوع می‌باشد.\n"
    "۵. کاربران زیر ۱۸ سال اجازه استفاده ندارند.\n"
    "۶. استفاده از ربات برای اهداف غیرقانونی باعث گزارش به مراجع قضایی می‌شود.\n"
    "۷. تیم پشتیبانی حق بررسی و مسدودسازی حساب‌های متخلف را دارد.\n\n"
    "✅ با ادامه استفاده از ربات، تمامی قوانین بالا را پذیرفته‌اید."
)


# ═══════════════════════════════════════════════════════════════════════════════
#  /start  — Entry point
# ═══════════════════════════════════════════════════════════════════════════════

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
      1. Block execution if the user is in a live match / questionnaire / chat.
      2. Clear any stale FSM state.
      3. Send returning registered users straight to the main menu.
      4. Parse optional referral deep link (/start ref_<TGID>).
      5. Create a new DB record for first-time users (handles race conditions).
      6. Start the onboarding FSM sequence.
    """
    tg_id = message.from_user.id

    # ── Guard: reject /start mid-pipeline ─────────────────────────────────────
    current_state = await state.get_state()
    if current_state in _ACTIVE_PIPELINE_STATES:
        await message.answer(
            "⚠️ شما در میانه یک فرآیند فعال (مچینگ، پرسشنامه یا چت ناشناس) هستید.\n"
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
    if command.args and command.args.startswith("ref_"):
        try:
            ref_id_candidate = int(command.args.split("_", 1)[1])
            if ref_id_candidate == tg_id:
                logger.debug("User %d tried to self-refer — ignored.", tg_id)
            else:
                referrer = await crud.get_user_by_tg_id(db_session, ref_id_candidate)
                if referrer:
                    referrer_id = ref_id_candidate
                else:
                    logger.warning(
                        "Referral link used with unknown referrer_id=%d by user=%d",
                        ref_id_candidate,
                        tg_id,
                    )
        except (ValueError, IndexError):
            logger.debug("Malformed referral args '%s' from user %d", command.args, tg_id)

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
    await message.answer(
        "🎉 <b>به ربات دیتینگ ناشناس خوش آمدید!</b>\n\n"
        "برای شروع و دریافت <b>۵ سکه هدیه اولیه</b>، لطفاً اطلاعات هویتی خود را ثبت کنید.\n\n"
        "ابتدا جنسیت خود را انتخاب کنید 👇",
        reply_markup=get_gender_keyboard(),
    )
    await state.set_state(OnboardingStates.waiting_for_gender)


# ═══════════════════════════════════════════════════════════════════════════════
#  Onboarding FSM — Step 1: Gender
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(
    OnboardingStates.waiting_for_gender,
    F.data.in_({"gender_male", "gender_female"}),
)
async def register_gender(call: CallbackQuery, state: FSMContext) -> None:
    """Captures the gender inline-button selection and advances to age input."""
    _gender_map: dict[str, tuple[str, str]] = {
        "gender_male": ("Male", "آقا 🙋‍♂️"),
        "gender_female": ("Female", "خانم 🙋‍♀️"),
    }
    gender, gender_label = _gender_map[call.data]

    await state.update_data(gender=gender)
    await call.message.edit_text(
        f"✅ جنسیت شما ثبت شد: <b>{gender_label}</b>\n\n"
        "سن خود را به صورت عددی وارد کنید (مثال: ۲۵) 👇"
    )
    await state.set_state(OnboardingStates.waiting_for_age)
    await call.answer()


@router.callback_query(OnboardingStates.waiting_for_gender)
async def reject_unknown_gender_callback(call: CallbackQuery) -> None:
    """Silently rejects unexpected callbacks while gender state is active."""
    await call.answer(
        "⚠️ لطفاً از دکمه‌های ارائه‌شده استفاده کنید.",
        show_alert=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Onboarding FSM — Step 2: Age
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(OnboardingStates.waiting_for_age)
async def register_age(message: Message, state: FSMContext) -> None:
    """
    Validates that the user's age is an integer in the range [18, 75].
    Advances to province input on success.
    """
    if message.text == "❌ انصراف و منوی اصلی":
        await state.clear()
        await message.answer(
            "فرآیند ثبت‌نام لغو شد. برای شروع مجدد /start را ارسال کنید.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    raw_input = (message.text or "").strip()
    try:
        age = int(raw_input)
        if not (18 <= age <= 75):
            raise ValueError("age out of allowed range")
    except ValueError:
        await message.reply(
            "⚠️ سن باید یک عدد صحیح بین ۱۸ تا ۷۵ باشد.\n"
            "لطفاً مجدداً وارد کنید (مثال: ۲۵):"
        )
        return

    await state.update_data(age=age)
    await message.answer(
        "✅ سن شما ثبت شد.\n\n"
        "اکنون نام <b>استان</b> محل سکونت خود را تایپ کنید (مثال: اصفهان) 👇",
        reply_markup=get_cancel_keyboard(),
    )
    await state.set_state(OnboardingStates.waiting_for_province)


# ═══════════════════════════════════════════════════════════════════════════════
#  Onboarding FSM — Step 3: Province
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(OnboardingStates.waiting_for_province)
async def register_province(message: Message, state: FSMContext) -> None:
    """
    Validates and normalises the province string (max 30 chars,
    spaces replaced with underscores). Advances to city input.
    """
    if message.text == "❌ انصراف و منوی اصلی":
        await state.clear()
        await message.answer(
            "فرآیند ثبت‌نام لغو شد. برای شروع مجدد /start را ارسال کنید.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    province_raw = (message.text or "").strip()
    if not province_raw or len(province_raw) > 30:
        await message.reply(
            "⚠️ نام استان نامعتبر است یا بیش از ۳۰ کاراکتر دارد.\n"
            "لطفاً یک نام معتبر وارد کنید:"
        )
        return

    province = province_raw.replace(" ", "_")
    await state.update_data(province=province)
    await message.answer(
        "✅ استان ثبت شد.\n\n"
        "حالا نام <b>شهر</b> محل سکونت خود را وارد کنید (مثال: اردستان) 👇"
    )
    await state.set_state(OnboardingStates.waiting_for_city)


# ═══════════════════════════════════════════════════════════════════════════════
#  Onboarding FSM — Step 4: City → Complete registration
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(OnboardingStates.waiting_for_city)
async def register_city(
    message: Message,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    """
    Validates and normalises city input, then calls
    crud.complete_user_registration which:
      - Saves gender / age / province / city to the User row.
      - Awards 5 coins to this user.
      - Awards 5 coins to the referrer (if one exists).
    Presents the main menu on success.
    """
    if message.text == "❌ انصراف و منوی اصلی":
        await state.clear()
        await message.answer(
            "فرآیند ثبت‌نام لغو شد. برای شروع مجدد /start را ارسال کنید.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    city_raw = (message.text or "").strip()
    if not city_raw or len(city_raw) > 30:
        await message.reply(
            "⚠️ نام شهر نامعتبر است یا بیش از ۳۰ کاراکتر دارد.\n"
            "لطفاً یک نام معتبر وارد کنید:"
        )
        return

    city = city_raw.replace(" ", "_")
    data = await state.get_data()
    gender: Optional[str] = data.get("gender")
    age: Optional[int] = data.get("age")
    province: Optional[str] = data.get("province")
    tg_id = message.from_user.id

    # Guard against corrupted / expired FSM session (e.g. Redis flush)
    if not all([gender, age is not None, province]):
        logger.error(
            "Incomplete onboarding FSM data for user %d — stored data: %s",
            tg_id,
            data,
        )
        await state.clear()
        await message.answer(
            "⚠️ اطلاعات نشست شما ناقص یا منقضی شده است.\n"
            "لطفاً مجدداً از /start شروع کنید.",
            reply_markup=ReplyKeyboardRemove(),
        )
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
    except TypeError:
        # Raised if the current crud.complete_user_registration doesn't yet
        # accept 'province'. Update the function signature per the header notes.
        logger.error(
            "crud.complete_user_registration signature mismatch — "
            "please add 'province' parameter. See file header for details."
        )
        await message.answer(
            "⚠️ خطای پیکربندی سرور. لطفاً با پشتیبانی تماس بگیرید."
        )
        return
    except Exception:
        logger.exception("complete_user_registration raised unexpectedly for user %d", tg_id)
        await db_session.rollback()
        await message.answer(
            "⚠️ خطای سرور در ذخیره اطلاعات. لطفاً مجدداً تلاش کنید."
        )
        return

    if not success:
        await message.answer(
            "⚠️ مشکلی در ثبت اطلاعات به وجود آمد. لطفا /start را مجددا ارسال کنید."
        )
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
#  Main Menu — 🎯 شروع دیت ناشناس
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == "🎯 شروع دیت ناشناس")
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
#  Main Menu — 👤 پروفایل من
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == "👤 پروفایل من")
async def view_user_profile(message: Message, db_session: AsyncSession) -> None:
    """
    Renders the authenticated user's profile card in HTML.
    Falls back gracefully when new model columns (province, coins) are absent.
    """
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not user or not user.completed_registration:
        await message.answer(
            "⚠️ شما هنوز ثبت‌نام نکرده‌اید!\n"
            "لطفاً دستور /start را ارسال کنید."
        )
        return

    gender_label = GENDER_LABELS.get(user.gender or "", "نامشخص")
    vip_badge = "👑 عضو VIP" if user.is_vip else "🏷️ عضو عادی"

    safe_name = html.escape(user.first_name or "کاربر")
    safe_city = html.escape((user.city or "").replace("_", " "))
    safe_province = html.escape(
        (getattr(user, "province", None) or "").replace("_", " ")
    ) or "—"

    # Use new `coin_balance` field; fall back to legacy `vip_quota` so the profile
    # doesn't break before the migration is applied.
    coin_balance: int = getattr(user, "coin_balance", user.vip_quota)

    profile_card = (
        "👤 <b>پروفایل کاربری شما:</b>\n\n"
        f"🆔 شناسه تلگرام: <code>{tg_id}</code>\n"
        f"🏷️ نام: <b>{safe_name}</b>\n"
        f"🙋 جنسیت: <b>{gender_label}</b>\n"
        f"🎂 سن: <b>{user.age}</b> سال\n"
        f"🗺️ استان: <b>{safe_province}</b>\n"
        f"📍 شهر: <b>{safe_city}</b>\n"
        f"⚡ وضعیت اشتراک: <b>{vip_badge}</b>\n"
        f"🪙 موجودی سکه: <b>{coin_balance}</b> سکه\n"
    )
    await message.answer(profile_card)


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Menu — 📍 افراد نزدیک من
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == "📍 افراد نزدیک من")
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
    Fetches and renders the user's friends list.
    Degrades gracefully if crud.get_user_friends is not yet implemented.
    """
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user or not user.completed_registration:
        await message.answer("⚠️ ابتدا ثبت‌نام خود را تکمیل کنید. /start")
        return

    friends = []
    try:
        friends = await crud.get_user_friends(db_session, tg_id)
    except AttributeError:
        # crud.get_user_friends not yet implemented — suppress silently.
        logger.info("crud.get_user_friends not implemented; returning empty list.")
    except Exception:
        logger.exception("Error fetching friends list for user %d", tg_id)

    if not friends:
        await message.answer(
            "👥 <b>لیست دوستان شما:</b>\n\n"
            "هنوز دوستی ندارید.\n"
            "پس از پایان دیت‌های موفق می‌توانید کاربران را به لیست دوستان خود اضافه کنید."
        )
        return

    lines = ["👥 <b>لیست دوستان شما:</b>\n"]
    for index, friend in enumerate(friends, start=1):
        safe_friend_name = html.escape(friend.first_name or "کاربر")
        gender_label = GENDER_LABELS.get(friend.gender or "", "?")
        lines.append(
            f"{index}. {safe_friend_name} — {gender_label} | {friend.age} سال"
        )

    await message.answer("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Menu — 🪙 سکه‌های من
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == "🪙 سکه‌های من")
async def show_coin_wallet(message: Message, db_session: AsyncSession) -> None:
    """
    Renders the user's full wallet summary:
      • Current balance
      • Total earned
      • Earned via referrals
      • Total spent
      • Unique referral invite link

    Falls back safely when new model columns haven't been migrated yet.
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

    try:
        bot_me = await bot.get_me()
        invite_link = f"https://t.me/{bot_me.username}?start=ref_{tg_id}"
    except Exception:
        logger.exception("Failed to fetch bot username for invite link; using placeholder.")
        invite_link = f"https://t.me/YOUR_BOT_USERNAME?start=ref_{tg_id}"

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
async def show_rules(message: Message) -> None:
    """Sends the bot's rules and usage policy."""
    await message.answer(_BOT_RULES_TEXT)


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
    Forwards the user's support message anonymously to all configured admin IDs.
    Clears FSM state and returns the user to the main menu regardless of outcome.
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

    safe_user_msg = html.escape(message.text)
    admin_notification = (
        "📩 <b>پیام پشتیبانی ناشناس جدید:</b>\n\n"
        f"{safe_user_msg}\n\n"
        "──────────────────────────────\n"
        "<i>برای پاسخ، از پنل مدیریت ربات استفاده کنید.</i>"
    )

    delivered_count = 0
    for admin_id in settings.parsed_admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=admin_notification)
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
            "لطفاً مستقیماً از طریق @your_support_id تماس بگیرید.",
            reply_markup=get_main_menu_keyboard(),
        )

    await state.clear()