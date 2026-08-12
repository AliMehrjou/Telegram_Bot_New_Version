import html
import json
import logging
import os
import asyncio
from pathlib import Path
from typing import Optional

from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
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
from sqlalchemy import select  # v3: needed for referral attribution lookup
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import bot, redis_client, matching_engine, dp, dating_scheduler
from matching_bot_project.database.models.models import User  # v3: needed for referral lookup
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

# Moved these imports to the top level
from matching_bot_project.bot.handlers.profile_edit import IRAN_DATA, get_cities_reply_keyboard, get_provinces_reply_keyboard

# --- NEW CONSTANTS IMPORT ---
from matching_bot_project.bot.core.constants import ReplyBtn
from datetime import datetime, timezone

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

def _pe(emoji_id: str, char: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{char}</tg-emoji>'

class PEmoji:
    WAVE = _pe("5472055112702629499", "👋")
    ROCKET = _pe("5445284980978621387", "🚀")
    CROWN = _pe("5467406098367521267", "👑")
    SWIRL = _pe("5469741319330996757", "💫")
    STAR = _pe("5458799228719472718", "🌟")
    SPARKLES = _pe("5472164874886846699", "✨")
    FIRE = _pe("5420315771991497307", "🔥")
    ROSE = _pe("5440911110838425969", "🌹")
    COIN = _pe("5379600444098093058", "🪙")
    MONEY_BAG = _pe("5375296873982604963", "💰")
    DIAMOND = _pe("5471952986970267163", "💎")
    GIFT = _pe("5199749070830197566", "🎁")
    CONFETTI = _pe("5435933711893797296", "🎊")
    PARTY_POPPER = _pe("5436040291507247633", "🎉")
    PARTY_FACE = _pe("5370870691140737817", "🥳")
    CHART_UP = _pe("5373001317042101552", "📈")
    CHART_DOWN = _pe("5361748661640372834", "📉")
    LINK = _pe("5375129357373165375", "🔗")
    CHECK = _pe("5427009714745517609", "✅")
    CROSS = _pe("5465665476971471368", "❌")
    CAKE = _pe("5370999492914976897", "🎂")
    POINT_DOWN = _pe("5470177992950946662", "👇")
    PEOPLE = _pe("5372926953978341366", "👥")
    FOLDED_HANDS = _pe("5472189549473963781", "🙏")
    PHONE = _pe("5467539229468793355", "📞")
    BOOK = _pe("5226512880362332956", "📖")
    COMPASS = _pe("5433825729060018456", "🧭")
    HOUSE = _pe("5465226866321268133", "🏠")
    LOCK = _pe("5472308992514464048", "🔐")

def _build_welcome_and_terms_text(first_name: str) -> str:
    safe_name = html.escape(first_name or "کاربر")
    return (
        f"{PEmoji.WAVE} <b>سلام {safe_name} عزیز!</b>\n"
        f"{PEmoji.SPARKLES} به ربات دیت ناشناس خوش اومدی\n"
        "──────────────────────\n"
        f"{PEmoji.BOOK} برای استفاده از ربات، رعایت قوانین <b>الزامی</b>‌ه.\n"
        "هرگونه قانون‌شکنی مساوی با مسدود شدن اکانت و ثبت تخلفه.\n\n"
        f"{PEmoji.FOLDED_HANDS} لطفاً قوانین رو با دقت بخون تا به مشکل نخوری."
    )

def _registration_cancelled_text() -> str:
    return f"{PEmoji.CROSS} فرآیند ثبت‌نام لغو شد.\nبرای شروع دوباره، دستور /start رو بفرست."

def _registration_required_text() -> str:
    return f"⚠️ اول باید ثبت‌نامت رو تکمیل کنی.\nدستور /start رو بفرست 👇"

def get_user_state(user_id: int) -> FSMContext:
    """Helper to get FSMContext for any user (used for zombie state detection)."""
    return FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
    )

# ═══════════════════════════════════════════════════════════════════════════════
#  🚨 UNIVERSAL EMERGENCY RESET HANDLERS (/reset, /fix, /cancel & inline button)
# ═══════════════════════════════════════════════════════════════════════════════

async def _execute_emergency_reset(tg_id: int, state: FSMContext, db_session: AsyncSession) -> None:
    """Core logic for atomic deactivation of any active DB match, deleting Redis keys, and clearing FSM."""
    # 1. Atomically deactivate any active DB match
    active_match = await crud.get_active_match(db_session, tg_id)
    if active_match:
        active_match.is_active = False
        active_match.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            if hasattr(dating_scheduler, 'cancel_match_timeout'):
                await dating_scheduler.cancel_match_timeout(active_match.id)
            await redis_client.delete(f"date:timeout:{active_match.id}")
        except Exception as exc:
            logger.warning(f"Failed to clear timeouts during emergency reset: {exc}")
            
        # --- پاکسازی پارتنر و اطلاع‌رسانی ---
        partner_id = active_match.user_two_id if active_match.user_one_id == tg_id else active_match.user_one_id
        partner_ctx = get_user_state(partner_id)
        await partner_ctx.set_state(None)
        await partner_ctx.clear()
        
        try:
            await redis_client.delete(f"user:state:{partner_id}")
            await bot.send_message(
                chat_id=partner_id,
                text="⚠️ <b>دیت متوقف شد!</b>\nپارتنر شما اتصال را ریست کرد.",
                parse_mode="HTML",
                reply_markup=get_main_menu_keyboard()
            )
        except Exception:
            pass
        # -------------------------------------
        
        await db_session.commit()

    # 2. Delete all associated Redis keys for the caller
    try:
        await matching_engine.remove_from_queue(tg_id)
        await redis_client.delete(f"user:state:{tg_id}")
    except Exception as exc:
        logger.warning(f"Failed to clear Redis state for {tg_id}: {exc}")

    # 3. Clear FSM unconditionally
    await state.clear()


@router.message(Command("reset", "fix", "cancel"))
async def emergency_reset_command(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    """High-priority global command handler for emergency reset."""
    tg_id = message.from_user.id
    await _execute_emergency_reset(tg_id, state, db_session)
    
    await message.answer(
        "🔄 Your state has been successfully reset.\n"
        "شما به منوی اصلی بازگشتید.",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "force_reset_state")
async def emergency_reset_callback(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    """Inline emergency button fallback."""
    tg_id = call.from_user.id
    await _execute_emergency_reset(tg_id, state, db_session)
    
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
        
    await call.message.answer(
        "🔄 Your state has been successfully reset.\n"
        "شما به منوی اصلی بازگشتید.",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )
    await call.answer("State reset successfully!", show_alert=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  Smart Orphan / Zombie State Detection
# ═══════════════════════════════════════════════════════════════════════════════

async def auto_heal_ghost_state(tg_id: int, state: FSMContext, db_session: AsyncSession) -> bool:
    """
    Smart detection of Orphan / Zombie States.
    If a user has an active FSM state, it DOES NOT blindly abort. 
    Instead, it inspects the partner in Redis and MySQL. 
    If the partner is invalid, it recognizes this as an ORPHANED ZOMBIE STATE and automatically wipes the caller's FSM, Redis, and DB states.
    """
    current_state = await state.get_state()
    is_in_pipeline = current_state and any(phase in current_state.lower() for phase in ["chat", "matching", "questionnaire"])
    
    if is_in_pipeline:
        # Smart Zombie Detection: Verify if the pipeline is genuinely active
        active_match = await crud.get_active_match(db_session, tg_id)
        fsm_data = await state.get_data()
        partner_id = fsm_data.get("partner_id")
        
        # Scenario A: User thinks they are in a match, but DB has no active match.
        if not active_match:
            logger.warning(f"Zombie state detected for {tg_id}: FSM={current_state}, but no active DB match. Healing...")
            await state.clear()
            try:
                await matching_engine.remove_from_queue(tg_id)
                await redis_client.delete(f"user:state:{tg_id}")
            except Exception:
                pass
            return True
            
        # Scenario B: DB has active match. Check partner's validity.
        if partner_id:
            partner_active_match = await crud.get_active_match(db_session, partner_id)
            partner_fsm = get_user_state(partner_id)
            partner_state_val = await partner_fsm.get_state()
            partner_in_pipeline = partner_state_val and any(phase in partner_state_val.lower() for phase in ["chat", "matching", "questionnaire"])
            
            # If partner has no active match, or partner is not in pipeline -> Orphaned Zombie
            if not partner_active_match or not partner_in_pipeline:
                logger.warning(f"Orphaned zombie state detected for {tg_id}: Partner {partner_id} is not in active match/state. Healing...")
                active_match.is_active = False
                active_match.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await db_session.commit()
                
                await state.clear()
                try:
                    await matching_engine.remove_from_queue(tg_id)
                    await redis_client.delete(f"user:state:{tg_id}")
                    await redis_client.delete(f"date:timeout:{active_match.id}")
                except Exception:
                    pass
                return True
        else:
            # Scenario C: In pipeline state, has active DB match, but no partner_id in FSM. Inconsistent!
            logger.warning(f"Inconsistent zombie state for {tg_id}: Active DB match but no partner_id in FSM. Healing...")
            active_match.is_active = False
            active_match.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db_session.commit()
            await state.clear()
            try:
                await redis_client.delete(f"user:state:{tg_id}")
                await redis_client.delete(f"date:timeout:{active_match.id}")
            except Exception:
                pass
            return True
            
        # If we reach here, the pipeline state is genuinely valid. Do not heal.
        return False

    # If NOT in pipeline, check standard ghost states (FSM empty, but DB/Redis dirty)
    healed = False
    active_match = await crud.get_active_match(db_session, tg_id)
    if active_match:
        active_match.is_active = False
        active_match.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            if hasattr(dating_scheduler, 'cancel_match_timeout'):
                await dating_scheduler.cancel_match_timeout(active_match.id)
            await redis_client.delete(f"date:timeout:{active_match.id}")
        except Exception as exc:
            logger.warning(f"Failed to clear timeouts during auto-heal: {exc}")
        await db_session.commit()
        logger.info(f"Auto-healed ghost DB match {active_match.id} for user {tg_id}")
        healed = True

    redis_state_exists = await redis_client.exists(f"user:state:{tg_id}")
    if redis_state_exists:
        await matching_engine.remove_from_queue(tg_id)
        await redis_client.delete(f"user:state:{tg_id}")
        logger.info(f"Auto-healed ghost Redis state for user {tg_id}")
        healed = True
        
    return healed


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
    
    # Try to auto-heal first. If it heals, we proceed as if normal.
    healed = await auto_heal_ghost_state(tg_id, state, db_session)
    
    current_state = await state.get_state()
    if not healed and current_state in _ACTIVE_PIPELINE_STATES:
        # Genuinely in an active state. Provide escape hatch instead of hard block.
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 ریست اجباری و بازگشت به منو", callback_data="force_reset_state")]
        ])
        await message.answer(
            "⚠️ <b>یک لحظه صبر کن!</b>\n\n"
            f"<blockquote>الان وسط یه فرآیند فعالی (پرسشنامه یا چت ناشناس) {PEmoji.SWIRL}</blockquote>\n\n"
            "اگر گیر کرده‌اید یا می‌خواهید خارج شوید، از دکمه زیر استفاده کنید:",
            reply_markup=kb,
            parse_mode="HTML",
        )
        return

    await state.clear()

    # 🟢 فیکس قطعی باگ لوپ ناشناس: خواندن و پاک کردن فوری کلید از ردیس
    try:
        pending_anon = await redis_client.get(f"pending_anon:{tg_id}")
        if pending_anon:
            await redis_client.delete(f"pending_anon:{tg_id}")  # 👈 پاکسازی فوری برای جلوگیری از گیر کردن
    except Exception:
        pending_anon = None

    if (command.args and command.args.startswith("anon_")) or pending_anon:
        # پیدا کردن آیدی شخص گیرنده
        if command.args and command.args.startswith("anon_"):
            target_public_id = command.args[5:]
        else:
            target_public_id = pending_anon.decode('utf-8') if isinstance(pending_anon, bytes) else pending_anon
            
        target_user = await crud.get_user_by_public_id(db_session, target_public_id)
        
        if not target_user:
            await message.answer("اوه اوه! 🙈 مثل اینکه لینک اشتباهه یا این کاربر دیگه تو ربات نیست.")
            return
            
        # ساخت کاربر موقت برای کسی که از بیرون اومده
        user = await crud.get_user_by_tg_id(db_session, tg_id)
        if user and getattr(user, 'is_banned', False):
            await redis_client.set(f"user:banned:{tg_id}", "1")
            await message.answer("⛔️ حساب کاربری شما به دلیل نقض قوانین مسدود شده است.")
            return
            
        if not user:
            try:
                user = await crud.create_user(
                    session=db_session,
                    tg_id=tg_id,
                    first_name=message.from_user.first_name or "کاربر",
                    username=message.from_user.username,
                )
                await db_session.commit()
            except IntegrityError:
                await db_session.rollback()

        # انتقال مستقیم و بدون معطلی به وضعیت ارسال پیام
        from matching_bot_project.bot.states.states import AnonymousLinkStates
        await state.set_state(AnonymousLinkStates.waiting_for_message)
        await state.update_data(target_anon_id=target_user.tg_id)
        
        if target_user.tg_id == tg_id:
            await message.answer(
                f"چه جالب! داری لینک خودت رو تست می‌کنی 😂\n\n"
                f"اشکالی نداره، پیامت رو بنویس (متن، عکس، ویس و...). 🤫\n\n"
                f"💡 <i>(راستی! اگه می‌خوای از بقیه امکانات باحال ربات استفاده کنی، کافیه هر زمان خواستی /start رو بزنی و ثبت‌نامت رو تکمیل کنی)</i>",
                parse_mode="HTML"
            )
        else:
            await message.answer(
                f"🎉 قراره یه پیام کاملاً ناشناس برای <b>{html.escape(target_user.first_name)}</b> بفرستی!\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👇 <i>همین الان پیامت رو بنویس و بفرست (متن، عکس، ویس و...).</i>\n\n"
                f"💡 <i>(راستی! اگه تو هم دلت می‌خواد لینک اختصاصی خودت رو داشته باشی و امکانات ربات رو ببینی، کافیه /start رو بزنی و ثبت‌نام کنی)</i>",
                parse_mode="HTML"
            )
        return

    # ─────────────────────────────────────────────────────────────────
    # بقیه کدهای هندلر برای زمانی که کاربر به صورت عادی /start رو میزنه
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    # 🟢 اگر کاربر قبلاً ثبت‌نام کرده، او را مستقیم به منو بفرست
    if user and user.completed_registration:
        safe_name = html.escape(user.first_name or "کاربر")
        await message.answer(
            f"{PEmoji.WAVE} <b>سلام {safe_name} عزیز!</b>\n"
            f"به ربات خوش برگشتی {PEmoji.SPARKLES}",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
        return

    # 🟢 در غیر این صورت (کاربر جدید یا ثبت‌نام ناقص)، ساخت کاربر و هدایت به قوانین
    if not user:
        referrer_id = None
        try:
            pending_ref = await redis_client.get(f"pending_ref:{tg_id}")
            if pending_ref:
                ref_candidate_str = pending_ref.decode('utf-8') if isinstance(pending_ref, bytes) else pending_ref
                
                # 👈 بخش اصلاح‌شده: تمیز کردن کد از پیشوند ref_ و فیلترهای اضافی
                if ref_candidate_str.startswith("ref_"):
                    ref_candidate_str = ref_candidate_str[4:]
                actual_ref_code = ref_candidate_str.split('_')[0]
                
                # پشتیبانی همزمان از لینک‌های قدیمی (عددی) و لینک‌های جدید V3 (حروف و عدد)
                if actual_ref_code.isdigit():
                    referrer = await crud.get_user_by_tg_id(db_session, int(actual_ref_code))
                else:
                    from sqlalchemy import select
                    from matching_bot_project.database.models.models import User
                    res = await db_session.execute(select(User).where(User.referral_code == actual_ref_code))
                    referrer = res.scalar_one_or_none()

                if referrer and referrer.tg_id != tg_id:
                    referrer_id = referrer.tg_id
                    
                await redis_client.delete(f"pending_ref:{tg_id}")
        except Exception as e:
            logger.error(f"Error processing pending_ref during onboarding: {e}")

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


    # ارسال پیام خوش‌آمدگویی و نمایش دکمه قوانین
    await message.answer(
        _build_welcome_and_terms_text(message.from_user.first_name),
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
        rules_text = "⚠️ خطا در بارگذاری قوانین. لطفاً بعداً دوباره تلاش کن."
    await call.answer()
    await call.message.answer(rules_text, parse_mode="HTML")

@router.callback_query(OnboardingStates.waiting_for_terms_acceptance, F.data == "terms_accept")
async def accept_terms(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("✅ قوانین پذیرفته شد!")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    await call.message.answer(
        f"{PEmoji.CHECK} <b>ممنون بابت پذیرش قوانین!</b>\n\n"
        f"برای شروع، لطفاً یک <b>نام</b> برای خودت بنویس (این نام به بقیه کاربرا نمایش داده میشه) {PEmoji.POINT_DOWN}",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML",
    )
    await state.set_state(OnboardingStates.waiting_for_name)

@router.message(OnboardingStates.waiting_for_name)
async def register_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) < 2 or len(name) > 50:
        await message.answer("⚠️ لطفاً یک نام معتبر (بین ۲ تا ۵۰ کاراکتر) وارد کن:")
        return

    safe_name = html.escape(name)
    await state.update_data(first_name=safe_name)

    gender_reply_kb = get_gender_reply_keyboard()
    await message.answer(
        f"{PEmoji.CHECK} نامت ثبت شد: <b>{safe_name}</b>\n\n"
        f"حالا بگو جنسیتت چیه {PEmoji.POINT_DOWN}",
        reply_markup=gender_reply_kb,
        parse_mode="HTML",
    )
    await state.set_state(OnboardingStates.waiting_for_gender)


# ===============================================================================
#  Onboarding FSM — Step 1: Gender
# ===============================================================================

@router.message(OnboardingStates.waiting_for_gender, F.text.in_(set(GENDER_LABELS.values())))
async def register_gender(message: Message, state: FSMContext) -> None:
    raw_text = message.text
    gender = "Male" if raw_text == GENDER_LABELS["Male"] else "Female"
    gender_label = GENDER_LABELS[gender]

    await state.update_data(gender=gender)
    await state.set_state(OnboardingStates.waiting_for_age)
    
    await message.answer(
        f"{PEmoji.CHECK} جنسیت ثبت شد: <b>{gender_label}</b>\n\n"
        f"{PEmoji.CAKE} حالا سنت رو به صورت عدد بنویس (مثال: <code>25</code>) {PEmoji.POINT_DOWN}",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(OnboardingStates.waiting_for_gender)
async def reject_unknown_gender_message(message: Message) -> None:
    gender_reply_kb = get_gender_reply_keyboard()
    await message.answer(
        f"⚠️ لطفاً جنسیتت رو فقط از دکمه‌های زیر انتخاب کن {PEmoji.POINT_DOWN}",
        reply_markup=gender_reply_kb,
        parse_mode="HTML",
    )

@router.callback_query(OnboardingStates.waiting_for_gender)
async def reject_unknown_gender_callback(call: CallbackQuery) -> None:
    await call.answer("⚠️ لطفاً از دکمه‌های ارائه‌شده استفاده کنید.", show_alert=True)

# ===============================================================================
#  Onboarding FSM — Step 2: Age
# ===============================================================================

@router.message(OnboardingStates.waiting_for_age)
async def register_age(message: Message, state: FSMContext) -> None:
    if message.text == ReplyBtn.CANCEL:
        await state.clear()
        await message.answer(_registration_cancelled_text(), reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
        return

    raw_input = (message.text or "").strip()
    # FIX PHASE5-HIGH-51: normalize Persian/Arabic digits before int().
    # Previously int("۲۵") raised ValueError → onboarding stalled for
    # Persian-keyboard users. Now "۲۵" → "25" → 25.
    from matching_bot_project.bot.core.normalizers import normalize_digits
    normalized_input = normalize_digits(raw_input)
    try:
        age = int(normalized_input)
        if not (18 <= age <= 75):
            raise ValueError()
    except ValueError:
        await message.reply("⚠️ سن باید یک عدد صحیح بین ۱۸ تا ۷۵ باشه.\nلطفاً دوباره وارد کن (مثال: ۲۵):")
        return

    await state.update_data(age=age)
    await state.set_state(OnboardingStates.waiting_for_province)
    
    await message.answer(
        f"{PEmoji.CHECK} سنت ثبت شد!\n\n"
        f"{PEmoji.COMPASS} حالا <b>استان</b> محل زندگیت رو از کیبورد زیر انتخاب کن {PEmoji.POINT_DOWN}",
        reply_markup=get_provinces_reply_keyboard(),
        parse_mode="HTML"
    )

# ===============================================================================
#  Onboarding FSM — Step 3: Province
# ===============================================================================

@router.message(OnboardingStates.waiting_for_province)
async def register_province(message: Message, state: FSMContext) -> None:
    if message.text in {ReplyBtn.CANCEL, ReplyBtn.BACK_TO_MENU}:
        await state.clear()
        await message.answer(_registration_cancelled_text(), reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
        return

    province_raw = (message.text or "").strip()
    if province_raw not in IRAN_DATA:
        await message.answer("⚠️ لطفاً استانت رو فقط و فقط از روی کیبورد متنی زیر انتخاب کن:")
        return

    await state.update_data(province=province_raw)
    await state.set_state(OnboardingStates.waiting_for_city)
    
    await message.answer(
        f"{PEmoji.CHECK} استان <b>{province_raw}</b> ثبت شد!\n\n"
        f"{PEmoji.HOUSE} حالا <b>شهر</b> محل زندگیت رو از کیبورد زیر انتخاب کن یا تایپش کن {PEmoji.POINT_DOWN}",
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
        await message.answer(_registration_cancelled_text(), reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
        return

    city_raw = (message.text or "").strip()
    if not city_raw or len(city_raw) > 30:
        await message.reply("⚠️ نام شهر نامعتبره. لطفاً یک نام معتبر وارد کن:")
        return

    city = city_raw
    data = await state.get_data()
    first_name: Optional[str] = data.get("first_name")
    gender: Optional[str] = data.get("gender")
    age: Optional[int] = data.get("age")
    province: Optional[str] = data.get("province")
    tg_id = message.from_user.id

    if not all([first_name, gender, age is not None, province]): # 👈 بررسی نام
        logger.error("Incomplete onboarding FSM data for user %d — stored data: %s", tg_id, data)
        await state.clear()
        await message.answer("⚠️ اطلاعات نشستت ناقص یا منقضی شده.\nلطفاً دوباره از /start شروع کن.", reply_markup=ReplyKeyboardRemove())
        return

    try:
        result: dict = await crud.complete_user_registration(
            session=db_session,
            tg_id=tg_id,
            first_name=first_name, # 👈 ارسال به دیتابیس
            gender=gender,
            age=age,
            province=province,
            city=city,
        )
        success = result.get("success", False)
        referrer_tg_id = result.get("referrer_tg_id")
    except Exception:
        logger.exception("complete_user_registration raised unexpectedly for user %d", tg_id)
        await db_session.rollback()
        await message.answer("⚠️ خطای سرور در ذخیره اطلاعات. لطفاً دوباره تلاش کن.")
        return

    if not success:
        await message.answer("⚠️ مشکلی در ثبت اطلاعات پیش اومد. لطفاً دوباره /start رو بفرست.")
        return

    await db_session.commit()
    
    if referrer_tg_id:
        try:
            await bot.send_message(
                chat_id=referrer_tg_id,
                text=(
                    f"{PEmoji.PARTY_POPPER} <b>تبریک!</b>\n\n"
                    "یکی از دوستات با لینک دعوت تو ثبت‌نامش رو کامل کرد و "
                    f"{PEmoji.COIN} <b>۵ سکه</b> به کیف پولت اضافه شد!"
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Could not notify referrer %s: %s", referrer_tg_id, exc)

    await state.clear()
    
    if referrer_tg_id:
        msg_text = (
            f"{PEmoji.PARTY_FACE} <b>ثبت‌نامت با موفقیت تکمیل شد!</b>\n\n"
            f"<blockquote>{PEmoji.GIFT} <b>۵ سکه</b> پاداش تکمیل پروفایل\n"
            f"{PEmoji.GIFT} <b>۵ سکه</b> پاداش ورود با لینک دعوت</blockquote>\n\n"
            f"{PEmoji.ROCKET} حالا می‌تونی وارد مچ‌یابی بشی و یه دیت جدید رو شروع کنی.\n"
            f"از منوی اصلی زیر استفاده کن {PEmoji.POINT_DOWN}"
        )
    else:
        msg_text = (
            f"{PEmoji.PARTY_FACE} <b>ثبت‌نامت با موفقیت تکمیل شد!</b>\n\n"
            f"<blockquote>{PEmoji.GIFT} <b>۵ سکه</b> پاداش تکمیل پروفایل به حسابت اضافه شد</blockquote>\n\n"
            f"{PEmoji.ROCKET} حالا می‌تونی وارد مچ‌یابی بشی و یه دیت جدید رو شروع کنی.\n"
            f"از منوی اصلی زیر استفاده کن {PEmoji.POINT_DOWN}"
        )

    # 🚀 فیکس باگ: حذف منطق اشتباه هدایت مجدد به صفحه چت ناشناس پس از ثبت‌نام.
    # کاربر وقتی ثبت‌نام کرد باید مستقیم بره به منوی اصلی تا بتونه از ربات استفاده کنه.

    await message.answer(msg_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")


# ===============================================================================
#  Main Menu
# ===============================================================================
# ----------------- کدهای آپدیت شده در bot/handlers/start.py -----------------

@router.message(F.text == ReplyBtn.START_DATE)
async def start_anonymous_dating(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not user or not user.completed_registration:
        await message.answer("⚠️ رفیق، اول باید ثبت‌نامت رو تکمیل کنی.\nدستور /start رو بفرست تا شروع کنیم.")
        return

    # --- بخش ۱: گیت اجباری قوانین ---
    if getattr(user, 'rules_accepted_at', None) is None:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="مطالعه کردم ✅", callback_data="accept_rules_and_start")]
        ])
        await message.answer(
            "قبل از انتخاب گزینه‌ها حتما قوانین استفاده از ربات /qavanin را مطالعه کنید.\n"
            "با چه کسی دوست داری بری دیت؟ انتخاب کن 👇",
            reply_markup=kb
        )
        return
    # --------------------------------

    await auto_heal_ghost_state(tg_id, state, db_session)

    current_state = await state.get_state()
    if current_state and any(phase in current_state.lower() for phase in ["chat", "matching", "questionnaire"]):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 ریست اجباری و بازگشت به منو", callback_data="force_reset_state")]
        ])
        await message.answer(
            "⚠️ الان تو یه دیت فعال هستی!\n"
            "لطفاً اول اونو تموم کن، بعد بیا سراغ یه دیت جدید.\n"
           " اگر گیر کرده اید از دکمه زیر استفاده کنید یا دستور /reset را بفرستید:",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return

    text = (
        f"{PEmoji.ROCKET} <b>آماده‌ای برای یه آشنایی جدید؟</b>\n"
        "انتخاب کن دوست داری چطوری مچ بشی:\n\n"
        f"<blockquote>{PEmoji.SWIRL} <b>مچ تصادفی (رایگان):</b>\n"
        "یه دیت شانسی و هیجان‌انگیز با یه نفر از هر جای ایران!</blockquote>\n"
        f"<blockquote>{PEmoji.CROWN} <b>مچ پیشرفته (VIP):</b>\n"
        "دیت با فیلترهای خاص! با هم‌شهری‌ها یا افراد مدنظرت مچ شو (با پرداخت سکه).</blockquote>"
    )

    await message.answer(
        text=text,
        reply_markup=get_matching_type_keyboard(),
        parse_mode="HTML" 
    )

@router.callback_query(F.data == "accept_rules_and_start")
async def accept_rules_and_start_callback(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    tg_id = call.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    
    # ۱. ثبت تایید قوانین در دیتابیس
    if user:
        user.rules_accepted_at = datetime.now(timezone.utc)
        await db_session.commit()
    
    await call.answer("قوانین تایید شد ✅")
    
    # ۲. پاک کردن پیام حاوی دکمه تایید قوانین
    try:
        await call.message.delete()
    except Exception:
        pass
    
    # ۳. بررسی وضعیت‌های معلق کاربر (جلوگیری از ایجاد زامبی استیت)
    await auto_heal_ghost_state(tg_id, state, db_session)

    # ۴. بررسی اینکه آیا کاربر در حال حاضر در دیت فعالی هست یا نه
    current_state = await state.get_state()
    if current_state and any(phase in current_state.lower() for phase in ["chat", "matching", "questionnaire"]):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 ریست اجباری و بازگشت به منو", callback_data="force_reset_state")]
        ])
        await call.message.answer(
            "⚠️ الان تو یه دیت فعال هستی!\n"
            "لطفاً اول اونو تموم کن، بعد بیا سراغ یه دیت جدید.\n"
            "اگر گیر کرده اید از دکمه زیر استفاده کنید:",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return

    # ۵. نمایش منوی انتخاب نوع مچ (اجرای مستقیم منطق به جای کپی کردن پیام)
    text = (
        f"{PEmoji.ROCKET} <b>آماده‌ای برای یه آشنایی جدید؟</b>\n"
        "انتخاب کن دوست داری چطوری مچ بشی:\n\n"
        f"<blockquote>{PEmoji.SWIRL} <b>مچ تصادفی (رایگان):</b>\n"
        "یه دیت شانسی و هیجان‌انگیز با یه نفر از هر جای ایران!</blockquote>\n"
        f"<blockquote>{PEmoji.CROWN} <b>مچ پیشرفته (VIP):</b>\n"
        "دیت با فیلترهای خاص! با هم‌شهری‌ها یا افراد مدنظرت مچ شو (با پرداخت سکه).</blockquote>"
    )

    await call.message.answer(
        text=text,
        reply_markup=get_matching_type_keyboard(),
        parse_mode="HTML" 
    )


@router.message(F.text == ReplyBtn.NEARBY)
async def show_nearby_people(message: Message, db_session: AsyncSession) -> None:
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if not user or not user.completed_registration:
        await message.answer(_registration_required_text(), parse_mode="HTML")
        return

    await message.answer(
        f"{PEmoji.COMPASS} <b>افراد نزدیکت رو پیدا کن</b>\n\n"
        f"نوع افراد مورد نظرت رو انتخاب کن {PEmoji.POINT_DOWN}",
        reply_markup=get_nearby_options_keyboard(),
        parse_mode="HTML",
    )

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
        safe_friend_name = friend.first_name or "کاربر"
        label = f"{safe_friend_name} ({friend.age} سال)"
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"view_profile_{friend.tg_id}")])
        
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await message.answer(
        f"{PEmoji.PEOPLE} <b>لیست دوستان تو</b>\n"
        f"برای دیدن پروفایل و مدیریت هرکدوم، روی اسمش بزن {PEmoji.POINT_DOWN}",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

@router.message(F.text == ReplyBtn.MY_COINS)
async def show_coin_wallet(message: Message, db_session: AsyncSession) -> None:
    """v3: Delegates to coins_menu handler for the new coins main menu."""
    # Import here to avoid circular import
    from matching_bot_project.bot.handlers.coins_menu import coins_main_menu
    await coins_main_menu(message, db_session)


@router.message(F.text == ReplyBtn.DISCOVER)
async def show_discover_menu(message: Message, db_session: AsyncSession) -> None:
    """v3 NEW: 'کشف کاربران' main menu button — opens discovery sub-menu."""
    from matching_bot_project.bot.handlers.discovery import show_discovery_main_menu
    await show_discovery_main_menu(message, db_session)


@router.message(F.text == ReplyBtn.VIP_SUBSCRIPTION)
async def show_vip_subscription_menu(message: Message, db_session: AsyncSession) -> None:
    """v3 NEW: 'اکانت VIP (پریمیوم)' main menu button — shows VIP plans."""
    from matching_bot_project.bot.handlers.vip import show_vip_main_menu
    await show_vip_main_menu(message, db_session)


@router.message(F.text == ReplyBtn.GIFTS)
async def show_gifts_menu(message: Message, db_session: AsyncSession) -> None:
    """v3 NEW: '🎁 گیفت‌ها' main menu button — shows gift shop."""
    from matching_bot_project.bot.handlers.gifts import gifts_main_menu
    await gifts_main_menu(message, db_session)


@router.message(F.text == ReplyBtn.HELP)
async def show_help_menu(message: Message) -> None:
    """v3 NEW: 'راهنما' main menu button — shows /qavanin menu."""
    from matching_bot_project.bot.handlers.help import cmd_qavanin
    await cmd_qavanin(message)


@router.message(F.text == ReplyBtn.RULES)
async def show_rules(message: Message):
    """v3: Rules are now only accessible via /qavanin, but if someone has stale
    reply keyboard with the RULES button we still handle it."""
    try:
        json_path = Path("json_files/rules.json")
        if not json_path.exists():
            json_path = Path("/app/json_files/rules.json")

        if not json_path.exists():
            return await message.answer("⚠️ فایل قوانین و مقررات ربات پیدا نشد!")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        rules_data = data.get("rules_text", "متنی یافت نشد.")
        rules_text = "\n".join(rules_data) if isinstance(rules_data, list) else rules_data

        await message.answer(rules_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error reading rules.json: {e}", exc_info=True)
        await message.answer(f"{PEmoji.CROSS} خطایی در بازخوانی قوانین ربات رخ داد.", parse_mode="HTML")

@router.message(F.text == ReplyBtn.SUPPORT)
async def start_support_chat(message: Message, state: FSMContext) -> None:
    await message.answer(
        f"{PEmoji.PHONE} <b>ارتباط با تیم پشتیبانی</b>\n\n"
        f"پیامت رو بنویس؛ به صورت <b>کاملاً ناشناس</b> برای تیم پشتیبانی ارسال میشه {PEmoji.LOCK}\n\n"
        f"برای انصراف از دکمه «{PEmoji.CROSS} انصراف» استفاده کن {PEmoji.POINT_DOWN}",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SupportStates.waiting_for_support_message)
    
@router.message(SupportStates.waiting_for_support_message)
async def receive_support_message(message: Message, state: FSMContext) -> None:
    if message.text == ReplyBtn.CANCEL:
        await state.clear()
        await message.answer(f"{PEmoji.WAVE} بازگشت به منوی اصلی.", reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
        return

    if not message.text:
        await message.reply("⚠️ لطفاً پیامت رو به صورت متنی ارسال کن.\nبرای لغو از دکمه «❌ انصراف» استفاده کن.")
        return

    tg_id = message.from_user.id
    safe_user_msg = html.escape(message.text)
    
    admin_notification = (
        "📩 <b>پیام پشتیبانی ناشناس جدید</b>\n\n"
        f"<blockquote>{safe_user_msg}</blockquote>\n"
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
        await message.answer(
            f"{PEmoji.CHECK} پیامت با موفقیت به تیم پشتیبانی ارسال شد.\nبه زودی بهت پاسخ داده میشه {PEmoji.SPARKLES}",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "⚠️ در ارسال پیام به پشتیبانی خطایی رخ داد.\nلطفاً مستقیماً از طریق پشتیبانی تماس بگیر.",
            reply_markup=get_main_menu_keyboard(),
        )

    await state.clear()


@router.callback_query(F.data == "check_membership")
async def process_check_membership_callback(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:

    tg_id = call.from_user.id

    if tg_id in settings.parsed_admin_ids:
        await call.answer("عضویت شما تایید شد! خیلی خوش اومدی 🌹", show_alert=True)
    else:
        # Gather sponsors the same way ForceJoinMiddleware does.
        sponsors: dict[str, str] = {}
        try:
            dynamic_sponsors = await redis_client.hgetall("bot:sponsors")
            if dynamic_sponsors:
                for k, v in dynamic_sponsors.items():
                    kk = k.decode('utf-8') if isinstance(k, bytes) else k
                    vv = v.decode('utf-8') if isinstance(v, bytes) else v
                    sponsors[kk] = vv
        except Exception as e:
            logger.warning("check_membership: Redis hgetall failed: %s", e)

        default_channel = str(getattr(settings, "REQUIRED_CHANNEL_ID", ""))
        default_link = getattr(settings, "CHANNEL_INVITE_LINK", "")
        if default_channel and default_channel not in sponsors:
            sponsors[default_channel] = default_link

        # Re-check each sponsor in parallel.
        _ALLOWED = {"creator", "administrator", "member", "restricted"}

        async def _check_one(channel_id_str: str) -> bool:
            try:
                cid = int(channel_id_str)
            except ValueError:
                cid = channel_id_str
            try:
                member = await bot.get_chat_member(chat_id=cid, user_id=tg_id)
                return member.status in _ALLOWED
            except TelegramAPIError as e:
                logger.warning("check_membership: get_chat_member failed for %s: %s", channel_id_str, e)
                return False  # treat failure as "not joined" so user is prompted to retry

        results = await asyncio.gather(
            *[_check_one(cid) for cid in sponsors.keys()],
            return_exceptions=True,
        )
        all_joined = all(r is True for r in results) if results else True

        if not all_joined:
            await call.answer(
                "❌ هنوز در همه‌ی کانال‌ها عضو نشده‌اید. لطفاً ابتدا عضو شوید سپس دوباره بررسی کنید.",
                show_alert=True,
            )
            return  # do NOT delete the prompt message — user still needs the join buttons
        else:
            try:
                sponsors_version = await redis_client.get("bot:sponsors_version") or "0"
                sponsors_version = sponsors_version.decode('utf-8') if isinstance(sponsors_version, bytes) else sponsors_version
                cache_key = f"user:force_join:{tg_id}:v{sponsors_version}"
                await redis_client.delete(cache_key)
            except Exception:
                pass
            await call.answer("عضویت شما تایید شد! خیلی خوش اومدی 🌹", show_alert=True)

    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Unexpected error deleting message: {e}")

    user = await crud.get_user_by_tg_id(db_session, tg_id)
    
    if not user:
        referrer_id: Optional[int] = None
        
        try:
            pending_ref = await redis_client.get(f"pending_ref:{tg_id}")
        except Exception as exc:
            logger.warning("callback start: Redis failure reading pending_ref: %s", exc)
            pending_ref = None
            
        if pending_ref:
            try:
                ref_candidate_str = pending_ref.decode('utf-8') if isinstance(pending_ref, bytes) else pending_ref
                
                # 👈 بخش اصلاح‌شده: تمیز کردن کد از پیشوند ref_ و فیلترهای اضافی
                if ref_candidate_str.startswith("ref_"):
                    ref_candidate_str = ref_candidate_str[4:]
                actual_ref_code = ref_candidate_str.split('_')[0]
                
                if actual_ref_code.isdigit():
                    referrer = await crud.get_user_by_tg_id(db_session, int(actual_ref_code))
                else:
                    from sqlalchemy import select
                    from matching_bot_project.database.models.models import User
                    res = await db_session.execute(select(User).where(User.referral_code == actual_ref_code))
                    referrer = res.scalar_one_or_none()

                if referrer and referrer.tg_id != tg_id:
                    referrer_id = referrer.tg_id
            except Exception as exc:
                logger.error(f"Error finding referrer in callback: {exc}")
                
            try:
                await redis_client.delete(f"pending_ref:{tg_id}")
            except Exception as exc:
                logger.warning("callback start: Redis failure deleting pending_ref: %s", exc)

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
            user = await crud.get_user_by_tg_id(db_session, tg_id)
        except Exception as exc:
            logger.error("Error creating user after force join: %s", exc)
            await call.message.answer("⚠️ خطای سرور. لطفاً دوباره /start رو بفرست.")
            return
        
        
    # 🟢 تغییر اصلی: بررسی اینکه آیا کاربر از طریق لینک ناشناس آمده است یا خیر
    try:
        pending_anon = await redis_client.get(f"pending_anon:{tg_id}")
    except Exception:
        pending_anon = None

    if pending_anon:
        target_public_id = pending_anon.decode('utf-8') if isinstance(pending_anon, bytes) else pending_anon
        await redis_client.delete(f"pending_anon:{tg_id}")
        
        target_user = await crud.get_user_by_public_id(db_session, target_public_id)
        if target_user:
            import html
            from matching_bot_project.bot.states.states import AnonymousLinkStates
            await state.set_state(AnonymousLinkStates.waiting_for_message)
            await state.update_data(target_anon_id=target_user.tg_id)
            
            # پیام‌های دوستانه و بدون اجبار به ثبت‌نام
            if target_user.tg_id == tg_id:
                await call.message.answer(
                    f"چه جالب! داری لینک خودت رو تست می‌کنی 😂\n\n"
                    f"اشکالی نداره، پیامت رو بنویس (متن، عکس، ویس و...). 🤫\n\n"
                    f"💡 <i>(راستی! اگه می‌خوای از بقیه امکانات باحال ربات استفاده کنی، کافیه هر زمان خواستی /start رو بزنی و ثبت‌نامت رو تکمیل کنی)</i>",
                    parse_mode="HTML"
                )
            else:
                await call.message.answer(
                    f"🎉 قراره یه پیام کاملاً ناشناس برای <b>{html.escape(target_user.first_name)}</b> بفرستی!\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"👇 <i>همین الان پیامت رو بنویس و بفرست (متن، عکس، ویس و...).</i>\n\n"
                    f"💡 <i>(راستی! اگه تو هم دلت می‌خواد لینک اختصاصی خودت رو داشته باشی و امکانات ربات رو ببینی، کافیه /start رو بزنی و ثبت‌نام کنی)</i>",
                    parse_mode="HTML"
                )
            return  # خروج از تابع برای جلوگیری از ورود به فاز اجباری ثبت‌نام
            
        # ✅ تغییر اعمال شده: اگر گیرنده در دیتابیس یافت نشد، فرآیند را متوقف کرده و خطای زیر را ارسال می‌کند
        else:
            await call.message.answer("اوه اوه! 🙈 مثل اینکه لینک اشتباهه یا این کاربر دیگه تو ربات نیست.")
            return

    # اگر کاربری بود که ثبت‌نامش از قبل کامل شده، کاری نمی‌کنیم
    if user and user.completed_registration:
        return

    # اگر کاربر لینک ناشناسی نداشت، به صورت عادی وارد فاز ثبت‌نام اجباری می‌شود
    await call.message.answer(
        _build_welcome_and_terms_text(call.from_user.first_name),
        reply_markup=get_terms_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(OnboardingStates.waiting_for_terms_acceptance)