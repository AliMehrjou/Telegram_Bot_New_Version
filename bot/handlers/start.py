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
from aiogram.exceptions import TelegramBadRequest
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
    get_terms_keyboard,
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

# Moved these imports to the top level
from matching_bot_project.bot.handlers.profile_edit import IRAN_DATA, get_cities_reply_keyboard, get_provinces_reply_keyboard

# --- NEW CONSTANTS IMPORT ---
from matching_bot_project.bot.core.constants import ReplyBtn

logger = logging.getLogger(__name__)
router = Router(name="start_handler")

# ─── Local FSM States ──────────────────────────────────────────────────────────

class SupportStates(StatesGroup):
    waiting_for_support_message = State()

# ─── Module-level constants ────────────────────────────────────────────────────

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
   
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=GENDER_LABELS["Male"]), KeyboardButton(text=GENDER_LABELS["Female"])]
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
    tg_id = message.from_user.id
    current_state = await state.get_state()

    if current_state == MatchingStates.waiting_in_queue.state:
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

    if user and user.completed_registration:
        safe_name = html.escape(user.first_name or "کاربر")
        await message.answer(
            f"👋 خوش آمدید مجدد، <b>{safe_name}</b>!\n"
            "آماده شروع دیت عاطفی جدید هستید؟ از منوی زیر استفاده کنید 👇",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    referrer_id: Optional[int] = None
    
    if command.args and command.args.startswith("ref_"):
        try:
            ref_id_candidate = int(command.args.split("_", 1)[1])
            if ref_id_candidate != tg_id:
                referrer = await crud.get_user_by_tg_id(db_session, ref_id_candidate)
                if referrer:
                    referrer_id = referrer.id
        except Exception:
            pass

    if not referrer_id:
        pending_ref = await redis_client.get(f"pending_ref:{tg_id}")
        if pending_ref:
            try:
                ref_id_candidate = int(pending_ref.decode('utf-8') if isinstance(pending_ref, bytes) else pending_ref)
                if ref_id_candidate != tg_id:
                    referrer = await crud.get_user_by_tg_id(db_session, ref_id_candidate)
                    if referrer:
                        referrer_id = referrer.id
            except Exception:
                pass
            await redis_client.delete(f"pending_ref:{tg_id}")
    
    if user and not user.completed_registration and referrer_id:
        user.referrer_id = referrer_id
        await db_session.commit()
        
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
            await db_session.rollback()
            user = await crud.get_user_by_tg_id(db_session, tg_id)
        except Exception:
            logger.exception("Unexpected error creating user %d in DB", tg_id)
            await message.answer(
                "⚠️ خطای سرور هنگام ثبت حساب. لطفاً چند لحظه صبر کرده و مجدداً تلاش کنید."
            )
            return

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

@router.callback_query(OnboardingStates.waiting_for_terms_acceptance, F.data == "terms_show")
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

@router.callback_query(OnboardingStates.waiting_for_terms_acceptance, F.data == "terms_accept")
async def accept_terms(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("✅ قوانین پذیرفته شد!")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error editing reply markup: {e}")

    gender_reply_kb = get_gender_reply_keyboard()
    await call.message.answer(
        "✅ ممنون! حالا جنسیت خود را انتخاب کنید 👇",
        reply_markup=gender_reply_kb,
        parse_mode="HTML",
    )
    await state.set_state(OnboardingStates.waiting_for_gender)
    
# ═══════════════════════════════════════════════════════════════════════════════
#  Onboarding FSM — Step 1: Gender
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(OnboardingStates.waiting_for_gender, F.text.in_(set(GENDER_LABELS.values())))
async def register_gender(message: Message, state: FSMContext) -> None:
    raw_text = message.text
    # اصلاح شد: بررسی تطابق با متغیرها
    gender = "Male" if raw_text == GENDER_LABELS["Male"] else "Female"
    gender_label = GENDER_LABELS[gender]

    await state.update_data(gender=gender)
    await state.set_state(OnboardingStates.waiting_for_age)
    
    await message.answer(
        f"✅ جنسیت شما ثبت شد: <b>{gender_label}</b>\n\n"
        "سن خود را به صورت عددی وارد کنید (مثال: ۲۵) 👇",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )


@router.message(OnboardingStates.waiting_for_gender)
async def reject_unknown_gender_message(message: Message) -> None:
    gender_reply_kb = get_gender_reply_keyboard()
    await message.answer(
        "⚠️ لطفاً جنسیت خود را فقط از طریق یکی از دکمه‌های زیر انتخاب کنید 👇",
        reply_markup=gender_reply_kb
    )

@router.callback_query(OnboardingStates.waiting_for_gender)
async def reject_unknown_gender_callback(call: CallbackQuery) -> None:
    await call.answer("⚠️ لطفاً از دکمه‌های ارائه‌شده استفاده کنید.", show_alert=True)

# ===============================================================================
#  Onboarding FSM — Step 2: Age
# ===============================================================================

@router.message(OnboardingStates.waiting_for_age)
async def register_age(message: Message, state: FSMContext) -> None:
    # اصلاح شد: تغییر از CANCEL_TO_MAIN_MENU به CANCEL
    if message.text == ReplyBtn.CANCEL:
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
    # اصلاح شد: تغییر نام متغیرها به CANCEL و BACK_TO_MENU
    if message.text in {ReplyBtn.CANCEL, ReplyBtn.BACK_TO_MENU}:
        await state.clear()
        await message.answer("فرآیند ثبت‌نام لغو شد. برای شروع مجدد /start را ارسال کنید.", reply_markup=ReplyKeyboardRemove())
        return

    province_raw = (message.text or "").strip()
    
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
async def register_city(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    
    if message.text in {ReplyBtn.CANCEL, ReplyBtn.BACK_TO_MENU}:
        await state.clear()
        await message.answer("فرآیند ثبت‌نام لغو شد. برای شروع مجدد /start را ارسال کنید.", reply_markup=ReplyKeyboardRemove())
        return

    city_raw = (message.text or "").strip()
    if not city_raw or len(city_raw) > 30:
        await message.reply("⚠️ نام شهر نامعتبر است. لطفاً یک نام معتبر وارد کنید:")
        return

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
        result: dict = await crud.complete_user_registration(
            session=db_session,
            tg_id=tg_id,
            gender=gender,
            age=age,
            province=province,
            city=city,
        )
        success = result.get("success", False)
        referrer_tg_id = result.get("referrer_tg_id")
    except Exception as e:
        logger.exception("complete_user_registration raised unexpectedly for user %d", tg_id)
        await db_session.rollback()
        await message.answer("⚠️ خطای سرور در ذخیره اطلاعات. لطفاً مجدداً تلاش کنید.")
        return

    if not success:
        await message.answer("⚠️ مشکلی در ثبت اطلاعات به وجود آمد. لطفا /start را مجددا ارسال کنید.")
        return

    await db_session.commit()
    
    # Send notification to the referrer if exists
    if referrer_tg_id:
        try:
            await bot.send_message(
                chat_id=referrer_tg_id,
                text=(
                    "🎉 <b>تبریک!</b>\n"
                    "یک نفر با لینک دعوت شما ثبت‌نام را تکمیل کرد و "
                    "🪙 <b>۵ سکه</b> به حساب شما واریز شد!"
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Could not notify referrer %s: %s", referrer_tg_id, exc)

    await state.clear()
    
    # Send dynamic success message to the newly registered user
    if referrer_tg_id:
        msg_text = (
            "🥳 <b>ثبت‌نام شما با موفقیت تکمیل شد!</b>\n"
            "🎁 <b>۵ سکه</b> به عنوان پاداش تکمیل پروفایل به حساب شما واریز شد.\n"
            "🎁 <b>۵ سکه اضافه</b> نیز به عنوان پاداش ورود از طریق لینک دعوت دریافت کردید!\n\n"
            "حالا می‌توانید وارد مچ‌یابی شده و دیت جدیدی را آغاز کنید.\n"
            "از منوی اصلی زیر استفاده کنید 👇"
        )
    else:
        msg_text = (
            "🥳 <b>ثبت‌نام شما با موفقیت تکمیل شد!</b>\n"
            "🎁 <b>۵ سکه</b> به عنوان پاداش تکمیل پروفایل به حساب شما واریز شد.\n\n"
            "حالا می‌توانید وارد مچ‌یابی شده و دیت جدیدی را آغاز کنید.\n"
            "از منوی اصلی زیر استفاده کنید 👇"
        )
        
    await message.answer(msg_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════════════════
#  Main Menu
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == ReplyBtn.START_DATE)
async def start_anonymous_dating(message: Message, db_session: AsyncSession) -> None:
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not user or not user.completed_registration:
        await message.answer("⚠️ ابتدا باید ثبت‌نام را تکمیل کنید.\nدستور /start را ارسال کنید.")
        return

    try:
        active_match = await crud.get_active_match(db_session, tg_id)
    except Exception:
        logger.exception("get_active_match failed for user %d", tg_id)
        await message.answer("⚠️ خطای سرور. لطفاً مجدداً تلاش کنید.")
        return

    if active_match:
        await message.answer("⚠️ شما در حال حاضر در یک دیت فعال هستید!\nلطفاً ابتدا آن را پایان دهید.")
        return

    await message.answer(
        "🎯 <b>نوع مچ‌یابی مورد نظر خود را انتخاب کنید:</b>\n\n"
        "🎲 <b>مچ تصادفی:</b> جفت‌یابی رایگان در سطح کشوری با جنس مخالف.\n\n"
        "👑 <b>مچ پیشرفته (VIP):</b> جفت‌یابی فیلتردار هم‌شهری (نیاز به سکه).",
        reply_markup=get_matching_type_keyboard(),
    )

@router.message(F.text == ReplyBtn.NEARBY)
async def show_nearby_people(message: Message, db_session: AsyncSession) -> None:
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if not user or not user.completed_registration:
        await message.answer("⚠️ ابتدا ثبت‌نام خود را تکمیل کنید. /start")
        return

    await message.answer(
        "لطفاً نوع افراد نزدیک مورد نظر خود را انتخاب کنید:",
        reply_markup=get_nearby_options_keyboard(),
    )

@router.message(F.text == ReplyBtn.SEARCH_USERS)
async def show_user_search(message: Message, db_session: AsyncSession) -> None:
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if not user or not user.completed_registration:
        await message.answer("⚠️ ابتدا ثبت‌نام خود را تکمیل کنید. /start")
        return

    await message.answer(
        "لطفاً از لیست زیر گزینه مورد نظر خود را انتخاب کنید:",
        reply_markup=get_search_options_keyboard(),
    )

@router.message(F.text == ReplyBtn.MY_FRIENDS)
async def show_friends_list(message: Message, db_session: AsyncSession) -> None:
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

    keyboard = []
    for friend in friends:
        safe_friend_name = friend.first_name or "کاربر"
        label = f"{safe_friend_name} ({friend.age} سال)"
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"view_profile_{friend.tg_id}")])
        
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await message.answer(
        "👥 <b>لیست دوستان شما:</b>\nبرای مشاهده پروفایل و مدیریت هر شخص، روی نام او کلیک کنید 👇",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

@router.message(F.text == ReplyBtn.MY_COINS)
async def show_coin_wallet(message: Message, db_session: AsyncSession) -> None:
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user or not user.completed_registration:
        await message.answer("⚠️ ابتدا ثبت‌نام خود را تکمیل کنید. /start")
        return

    coin_balance: int = getattr(user, "coin_balance", getattr(user, "vip_quota", 0))
    coins_spent: int = getattr(user, "total_spent_coins", 0)
    total_earned: int = getattr(user, "total_earned_coins", coin_balance + coins_spent)

    bot_name = str(settings.BOT_USERNAME).replace("@", "")
    invite_link = f"https://t.me/{bot_name}?start=ref_{tg_id}"

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

    try:
        markup = get_coins_menu_keyboard()
    except Exception:
        logger.info("get_coins_menu_keyboard not available; sending without extra markup.")
        markup = None

    await message.answer(wallet_text, reply_markup=markup)

@router.message(F.text == ReplyBtn.RULES)
async def show_rules(message: Message):
    try:
        json_path = Path("json_files/rules.json")
        if not json_path.exists():
            json_path = Path("/app/json_files/rules.json")

        if not json_path.exists():
            return await message.answer("⚠️ فایل قوانین و مقررات ربات یافت نشد!")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        rules_data = data.get("rules_text", "متنی یافت نشد.")
        rules_text = "\n".join(rules_data) if isinstance(rules_data, list) else rules_data

        await message.answer(rules_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error reading rules.json: {e}", exc_info=True)
        await message.answer("❌ خطایی در بازخوانی قوانین ربات رخ داد.")

@router.message(F.text == ReplyBtn.SUPPORT)
async def start_support_chat(message: Message, state: FSMContext) -> None:
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
    if message.text == ReplyBtn.CANCEL:
        await state.clear()
        await message.answer("بازگشت به منوی اصلی.", reply_markup=get_main_menu_keyboard())
        return

    if not message.text:
        await message.reply("⚠️ لطفاً پیام خود را به صورت متنی ارسال کنید.\nبرای لغو از دکمه «❌ انصراف» استفاده کنید.")
        return

    tg_id = message.from_user.id
    safe_user_msg = html.escape(message.text)
    
    admin_notification = (
        "📩 <b>پیام پشتیبانی ناشناس جدید:</b>\n\n"
        f"{safe_user_msg}\n\n"
        "──────────────────────────────\n"
        f"👤 شناسه کاربر: <code>{tg_id}</code>"
    )

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
            logger.warning("Failed to deliver support message to admin %d", admin_id)

    if delivered_count > 0:
        await message.answer("✅ پیام شما با موفقیت به تیم پشتیبانی ارسال شد.\nدر اسرع وقت پاسخ داده خواهد شد.", reply_markup=get_main_menu_keyboard())
    else:
        await message.answer("⚠️ در ارسال پیام به پشتیبانی خطایی رخ داد.\nلطفاً مستقیماً از طریق پشتیبانی تماس بگیرید.", reply_markup=get_main_menu_keyboard())

    await state.clear()

@router.callback_query(F.data == "check_membership")
async def process_check_membership_callback(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    await call.answer("عضویت شما تایید شد! خیلی خوش اومدی 🌹", show_alert=True)
    
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error deleting message: {e}")
        
    tg_id = call.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    
    if user and user.completed_registration:
        await call.message.answer("✅ عضویت تایید شد.\nآماده شروع دیت جدید هستید؟ 👇", reply_markup=get_main_menu_keyboard())
        return

    if not user:
        referrer_id: Optional[int] = None
        pending_ref = await redis_client.get(f"pending_ref:{tg_id}")
        if pending_ref:
            try:
                ref_id_candidate = int(pending_ref.decode('utf-8') if isinstance(pending_ref, bytes) else pending_ref)
                if ref_id_candidate != tg_id:
                    referrer = await crud.get_user_by_tg_id(db_session, ref_id_candidate)
                    if referrer:
                        referrer_id = referrer.id
            except Exception:
                pass
            await redis_client.delete(f"pending_ref:{tg_id}")

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
    
@router.message(F.text == ReplyBtn.MY_PROFILE)
async def show_my_profile_menu(message: Message, db_session: AsyncSession) -> None:
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user or not user.completed_registration:
        await message.answer("⚠️ ابتدا باید ثبت‌نام خود را تکمیل کنید. /start")
        return

    from matching_bot_project.bot.handlers.interactions import _build_profile_card
    profile_card = _build_profile_card(user)
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ ویرایش مشخصات پروفایل", callback_data="edit_profile_triggered")]
    ])

    try:
        if user.profile_photo_file_id:
            await bot.send_photo(
                chat_id=tg_id,
                photo=user.profile_photo_file_id,
                caption=profile_card[:1024],
                parse_mode="HTML",
                reply_markup=inline_kb,
            )
        else:
            await bot.send_message(
                chat_id=tg_id,
                text=profile_card,
                parse_mode="HTML",
                reply_markup=inline_kb,
            )
            
        if user.profile_voice_file_id:
            await bot.send_voice(
                chat_id=tg_id,
                voice=user.profile_voice_file_id,
                caption="🎵 <b>آهنگ/وویس پروفایل شما</b>",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Failed to show own profile: {e}")


@router.message(F.text == ReplyBtn.REFERRAL_VIP)
async def show_referral_and_vip_zone(message: Message, db_session: AsyncSession) -> None:
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user or not user.completed_registration:
        await message.answer("⚠️ ابتدا ثبت‌نام خود را تکمیل کنید. /start")
        return

    ref_count = await crud.get_referral_count(db_session, tg_id)
    bot_name = str(settings.BOT_USERNAME).replace("@", "")
    invite_link = f"https://t.me/{bot_name}?start=ref_{tg_id}"
    
    from matching_bot_project.bot.handlers.vip import is_vip
    user_is_vip = await is_vip(db_session, tg_id)
    vip_status = "💎 فعال" if user_is_vip else "❌ غیرفعال"

    text = (
        "👑 <b>بخش ویژه زیرمجموعه‌گیری و حساب ویژه (VIP)</b>\n\n"
        f"👥 تعداد دعوت‌های موفق شما: <b>{ref_count} نفر</b>\n"
        f"🔗 لینک دعوت اختصاصی شما:\n<code>{invite_link}</code>\n\n"
        f"💎 وضعیت اشتراک VIP شما: {vip_status}\n\n"
        "💡 <i>با دعوت از دوستان خود سکه رایگان دریافت کنید. در صورت داشتن اشتراک VIP، از دکمه زیر برای مدیریت قابلیت‌های ویژه خود استفاده کنید.</i>"
    )

    kb = None
    if user_is_vip:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ ورود به پنل تنظیمات VIP", callback_data="vip_panel")]
        ])

    await message.answer(text, reply_markup=kb, parse_mode="HTML")
