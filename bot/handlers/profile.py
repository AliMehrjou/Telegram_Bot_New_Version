import logging
import html
import os
import json
import string
import random
from pathlib import Path
from datetime import datetime, timedelta, timezone
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

from matching_bot_project.database.queries import crud
from matching_bot_project.bot.core.config import settings

from matching_bot_project.bot.core.constants import ReplyBtn
from matching_bot_project.bot.core.formatters import build_unified_profile_card
from matching_bot_project.bot.keyboards.inline import get_user_action_keyboard
from matching_bot_project.database.models.models import BlockList
from matching_bot_project.bot.states.states import ProfileCommentStates
from matching_bot_project.database.queries.crud import upsert_profile_comment
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = Router(name="profile_handler")

def generate_public_id(length=6):
    
    characters = string.ascii_letters + string.digits
    return f"user_{''.join(random.choice(characters) for _ in range(length))}"


@router.message(F.text == ReplyBtn.MY_PROFILE)
async def view_user_profile(message: Message, db_session: AsyncSession, state: FSMContext):
    current_state = await state.get_state()
    if current_state and ("chat" in current_state.lower() or "matching" in current_state.lower() or "questionnaire" in current_state.lower()):
        return await message.answer("⚠️ شما در حال حاضر در یک فرآیند فعال (چت یا مچینگ) هستید. لطفاً اول آن را پایان دهید.")

    await state.clear()

    try:
        tg_id = message.from_user.id
        user = await crud.get_user_by_tg_id(db_session, tg_id)

        if not user or not user.completed_registration:
            await message.answer("⚠️ رفیق هنوز ثبت‌نامت کامل نشده! /start رو بفرست تا شروع کنیم.")
            return

        await db_session.refresh(user)

        # ساخت شناسه در صورت نیاز
        if not getattr(user, 'public_id', None):
            import string, random
            chars = string.ascii_letters + string.digits
            user.public_id = f"user_{''.join(random.choice(chars) for _ in range(6))}"
            await db_session.commit()
            await db_session.refresh(user)  

        profile_card = build_unified_profile_card(user, is_own_profile=True)

        inline_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 ویرایش پروفایل", callback_data="edit_profile_triggered")],
            [InlineKeyboardButton(text="💎 بخش ویژه VIP", callback_data="vip_panel")]
        ])

        # ---- ارسال عکس (با سیستم شکارچی ارور) ----
        photo_id = getattr(user, 'profile_photo_file_id', None)
        photo_sent = False
        if photo_id:
            try:
                if len(profile_card) > 1024:
                    await message.answer_photo(photo=photo_id)
                    await message.answer(text=profile_card, parse_mode=ParseMode.HTML, reply_markup=inline_kb)
                else:
                    await message.answer_photo(photo=photo_id, caption=profile_card, parse_mode=ParseMode.HTML, reply_markup=inline_kb)
                photo_sent = True
            except Exception as photo_err:
                err_str = str(photo_err)
                if "DOCUMENT_INVALID" in err_str or "wrong file identifier" in err_str:
                    logger.warning(f"Invalid Photo ID for user {tg_id}. Clearing from DB.")
                    user.profile_photo_file_id = None
                    await db_session.commit()
                else:
                    logger.warning(f"Photo failed for unknown reason: {photo_err}")

        # اگر عکس ارسال نشد (یا خراب بود و پاک شد)، پروفایل متنی رو بفرست
        if not photo_sent:
            await message.answer(text=profile_card, parse_mode=ParseMode.HTML, reply_markup=inline_kb)

        # ---- ارسال آهنگ / وویس (با سیستم شکارچی ارور) ----
        voice_id = getattr(user, 'profile_voice_file_id', None)
        if voice_id:
            try:
                await message.answer_voice(voice=voice_id, caption="🎵 <b>صدای پروفایل شما</b>", parse_mode=ParseMode.HTML)
            except Exception as voice_err:
                err_str = str(voice_err)
                if "DOCUMENT_INVALID" in err_str or "wrong file identifier" in err_str:
                    logger.warning(f"Invalid Voice ID for user {tg_id}. Clearing from DB.")
                    user.profile_voice_file_id = None
                    await db_session.commit()
                else:
                    logger.warning(f"Voice failed for user {tg_id}: {voice_err}")

    except Exception as e:
        err_str = str(e)
        if "DOCUMENT_INVALID" in err_str or "wrong file identifier" in err_str:
            if 'user' in locals():
                user.profile_photo_file_id = None
                user.profile_voice_file_id = None
                await db_session.commit()
            await message.answer("⚠️ یکی از فایل‌های پروفایل شما (عکس یا وویس) نامعتبر بود و توسط سیستم امنیتی پاک شد. لطفاً دوباره روی «پروفایل من» کلیک کن.")
        else:
            logger.error(f"Error in view_user_profile: {e}", exc_info=True)
            await message.answer("⚠️ یه مشکلی پیش اومد! لطفاً دوباره تلاش کنید.")


# ==========================================
# سیستم سایلنت مود
# ==========================================

# ================== کد جایگزین ==================
def get_silent_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 غیر فعال کردن سایلنت", callback_data="silent_off")],
        [
            InlineKeyboardButton(text="🔕 تا ۱ ساعت", callback_data="silent_1h"),
            InlineKeyboardButton(text="🔕 تا ۱ روز", callback_data="silent_1d")
        ],
        [
            InlineKeyboardButton(text="🔕 تا ۱ هفته", callback_data="silent_1w"),
            InlineKeyboardButton(text="🔕 همیشه سایلنت", callback_data="silent_forever")
        ],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="close_menu")]
    ])

@router.message(Command("silent"))
async def silent_mode_command(message: Message, db_session: AsyncSession):
    # گرفتن اطلاعات کاربر از دیتابیس
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if not user:
        await message.answer("⚠️ حساب کاربری یافت نشد.")
        return

    # بررسی وضعیت سایلنت کاربر
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if user.silent_until and user.silent_until > now_utc:
        status_text = "فعال 🔕"
    else:
        status_text = "غیرفعال 🔔"

    text = (
        f"🔻 حالت سایلنت: <b>{status_text}</b>\n"
        "───────────────────\n"
        "💡 با فعال شدن حالت سایلنت، درخواست چت یا دیت دریافت نخواهید کرد."
    )
    await message.answer(text, reply_markup=get_silent_keyboard(), parse_mode=ParseMode.HTML)
    
# ================== کد جایگزین ==================
@router.callback_query(F.data.startswith("silent_"))
async def handle_silent_options(call: CallbackQuery, db_session: AsyncSession):
    action = call.data.split("_")[1]
    now = datetime.now(timezone.utc).replace(tzinfo=None) 
    
    if action == "off":
        silent_until = None
        msg = "🔔 حالت سایلنت با موفقیت غیرفعال شد."
    elif action == "1h":
        silent_until = now + timedelta(hours=1)
        msg = "🔕 با فعال کردن حالت سایلنت، درخواست‌های چت و دیت تا ۱ ساعت برای شما ارسال نخواهد شد."
    elif action == "1d":
        silent_until = now + timedelta(days=1)
        msg = "🔕 با فعال کردن حالت سایلنت، درخواست‌های چت و دیت تا ۱ روز برای شما ارسال نخواهد شد."
    elif action == "1w":
        silent_until = now + timedelta(weeks=1)
        msg = "🔕 با فعال کردن حالت سایلنت، درخواست‌های چت و دیت تا ۱ هفته برای شما ارسال نخواهد شد."
    elif action == "forever":
        # یک تاریخ خیلی دور برای حالت همیشه سایلنت
        silent_until = now + timedelta(days=3650)
        msg = "🔕 با فعال کردن حالت سایلنت، درخواست‌های چت و دیت دیگر برای شما ارسال نخواهد شد."
    else:
        await call.answer("⚠️ گزینه نامعتبر.", show_alert=True)
        return
    
    # 👈 فراخوانی تابع دیتابیس و کامیت کردن تغییرات
    await crud.update_silent_mode(db_session, call.from_user.id, silent_until)
    await db_session.commit()
    
    await call.answer(msg, show_alert=True)
    await call.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data="close_menu")]]
    ))

    
# ==========================================
# سیستم حذف اکانت
# ==========================================

@router.message(Command("delete_account"))
async def delete_account_command(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ بله، اکانتم حذف شود", callback_data="confirm_delete_account")],
        [InlineKeyboardButton(text="🔙 انصراف", callback_data="close_menu")]
    ])
    await message.answer("⚠️ آیا از حذف اکانت خود مطمئن هستید؟ تمام اطلاعات، مچ‌ها و امتیازات شما پاک خواهد شد.", reply_markup=kb)

@router.callback_query(F.data == "confirm_delete_account")
async def confirm_delete_account_handler(call: CallbackQuery, db_session: AsyncSession):
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if user:
        await db_session.delete(user)
        await db_session.commit()
    await call.message.edit_text("✅ اکانت شما و تمامی اطلاعاتتان با موفقیت حذف شد. برای استفاده مجدد /start را بفرستید.")

@router.callback_query(F.data == "close_menu")
async def close_menu_handler(call: CallbackQuery):
    await call.message.delete()
    
@router.message(F.text == ReplyBtn.REFERRAL_VIP)
async def view_referral_panel(message: Message, db_session: AsyncSession):
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    
    # بررسی تکمیل ثبت‌نام کاربر
    if not user or not user.completed_registration:
        await message.answer("⚠️ رفیق اول باید ثبت‌نامت رو تکمیل کنی. /start رو بزن تا شروع کنیم.")
        return

    # دریافت تعداد زیرمجموعه‌ها و ساخت لینک دعوت با آیدی جدید ربات
    ref_count = await crud.get_referral_count(db_session, tg_id)
    
    bot_name = str(settings.BOT_USERNAME).replace("@", "")
    invite_link = f"https://t.me/{bot_name}?start=ref_{tg_id}"
    
    # بررسی وضعیت VIP کاربر
    from matching_bot_project.bot.handlers.vip import is_vip
    user_is_vip = await is_vip(db_session, tg_id)
    vip_status = "فعاله بفرما تو 😎" if user_is_vip else "متاسفانه نداری 💔"

    # ساخت متن دوستانه با ایموجی‌های پریمیوم و ساختار تمیز
    text = (
        "<tg-emoji emoji-id=\"5467406098367521267\">👑</tg-emoji> <b>بخش خفن زیرمجموعه‌گیری و حساب VIP</b>\n\n"
        f"<tg-emoji emoji-id=\"5372926953978341366\">👥</tg-emoji> تا الان <b>{ref_count} تا</b> از رفیقات رو با موفقیت دعوت کردی!\n"
        f"🔗 اینم لینک دعوت اختصاصی خودته، کپیش کن و بفرست واسه بقیه:\n"
        f"<code>{invite_link}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<tg-emoji emoji-id=\"5467666648263564704\">💎</tg-emoji> <b>وضعیت اشتراک VIP تو:</b> {vip_status}\n"
        f"<tg-emoji emoji-id=\"5379600444098093058\">🔋</tg-emoji> <b>مچ‌های پیشرفته‌ت (سهمیه VIP):</b> <b>{user.vip_quota} تا</b> مونده\n\n"
        "<blockquote><tg-emoji emoji-id=\"5427009714745517609\">💡</tg-emoji> <i>رفیق، با دعوت از دوستات هم سکه می‌گیری هم سهمیه مچ زدن رایگان بهت می‌رسه! اگرم VIP داری که معطل نکن، از دکمه پایین تنظیماتتو شخصی‌سازی کن.</i></blockquote>"
    )

    inline_rows = [
        [InlineKeyboardButton(text="🔗 بنرهای دعوت اختصاصی", callback_data="referral_banners")],
    ]
    if user_is_vip:
        inline_rows.append(
            [InlineKeyboardButton(text="⚙️ ورود به پنل تنظیمات VIP", callback_data="vip_panel")]
        )
    kb = InlineKeyboardMarkup(inline_keyboard=inline_rows)

    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    

@router.message(F.text == ReplyBtn.HELP)
async def view_help_panel(message: Message):
    """خواند داینامیک متن راهنمای ربات از فایل JSON موجود در json_files"""
    try:
        # تنظیم مسیر فایل راهنما
        json_path = Path("json_files/help.json")

        if not json_path.exists():
            # مسیر بک‌آپ برای محیط داخل کانتینر داکر
            json_path = Path("/app/json_files/help.json")

        if not json_path.exists():
            return await message.answer("⚠️ فایل راهنمای ربات یافت نشد!")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        help_data = data.get("help_text", "متنی یافت نشد.")
        
        # چسباندن خطوط آرایه با کاراکتر خط بعد
        if isinstance(help_data, list):
            help_text = "\n".join(help_data)
        else:
            help_text = help_data

        await message.answer(help_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error reading help.json: {e}", exc_info=True)
        await message.answer("❌ خطایی در بازخوانی اطلاعات راهنما رخ داد.")

@router.message(F.text.startswith("/user_"))
async def view_profile_by_public_id(message: Message, db_session: AsyncSession):
    command_text = message.text.strip()
    public_id = command_text[1:] 

    # جستجوی کاربر مورد نظر در دیتابیس
    target_user = await crud.get_user_by_public_id(db_session, public_id)

    if not target_user or not target_user.completed_registration:
        await message.answer("⚠️ کاربری با این آیدی یافت نشد یا پروفایلش تکمیل نیست.")
        return

    # بررسی اینکه آیا کاربر دکمه‌ی آیدی خودش را زده یا شخص دیگری
    is_own_profile = (message.from_user.id == target_user.tg_id)
    profile_card = build_unified_profile_card(target_user, is_own_profile=is_own_profile)

    # ساخت کیبورد بر اساس مالکیت پروفایل
    if is_own_profile:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 ویرایش پروفایل", callback_data="edit_profile_triggered")],
            [InlineKeyboardButton(text="💬 کامنت‌های پروفایل من", callback_data=f"view_comments:{target_user.tg_id}:0")],
        ])
        profile_card += "\n💡 <i>شما در حال مشاهده پروفایل خودتان هستید.</i>"
    else:
        # بررسی وضعیت بلاک بودن و دوستی برای نمایش دکمه‌های صحیح
        block_result = await db_session.execute(
            select(BlockList).where(
                BlockList.blocker_id == message.from_user.id,
                BlockList.blocked_id == target_user.tg_id,
            )
        )
        is_blocked = block_result.scalar_one_or_none() is not None
        
        try:
            already_friend = await crud.is_friend(db_session, message.from_user.id, target_user.tg_id)
        except Exception:
            already_friend = False

        # کیبورد اکشن‌های پروفایل — دکمه کامنت داخل get_user_action_keyboard هست
        markup = get_user_action_keyboard(
            target_tg_id=target_user.tg_id,
            is_blocked=is_blocked,
            is_friend=already_friend
        )

    # ارسال مدیا یا متن ساختاریافته‌ی پروفایل
    if target_user.profile_photo_file_id:
        try:
            # کنترل طول کپشن برای جلوگیری از شکستن تگ‌های HTML
            if len(profile_card) <= 1024:
                await message.answer_photo(
                    photo=target_user.profile_photo_file_id,
                    caption=profile_card,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup
                )
            else:
                await message.answer_photo(photo=target_user.profile_photo_file_id)
                await message.answer(
                    text=profile_card,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup
                )
        except Exception as e:
            logger.error(f"Failed to send profile photo: {e}")
            await message.answer(text=profile_card, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await message.answer(text=profile_card, parse_mode=ParseMode.HTML, reply_markup=markup)

    # ارسال وویس/آهنگ پروفایل هدف در صورت وجود
    profile_voice = getattr(target_user, 'profile_voice_file_id', None)
    if profile_voice:
        try:
            await message.answer_voice(
                voice=profile_voice,
                caption="🎵 <b>آهنگ/وویس پروفایل</b>",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send profile voice: {e}")


# ==========================================
# پنل بنرهای دعوت (رفرال)
# ==========================================

# تعریف بنرها: کلید = filter_tag، مقدار = (متن دکمه، پارامتر لینک، متن بنر)
# {invite_link} در متن بنر جایگزین لینک واقعی می‌شه
_REFERRAL_BANNERS: list[tuple[str, str, str, str]] = [
    (
        "banner_1",
        "📋 بنر متنی ۱",
        "",   # بدون فیلتر — لینک ساده
        (
            "👋 سلام!\n\n"
            "یه ربات خفن پیدا کردم که میتونی توش آدم‌های جدید پیدا کنی، "
            "چت ناشناس داشته باشی و حتی دیت بری! 🎯\n\n"
            "🔗 با لینک زیر بیا داخل، هم تو هم من سکه رایگان می‌گیریم:\n"
            "{invite_link}"
        ),
    ),
    (
        "banner_2",
        "🏠 بنر متنی ۲ — همشهری",
        "city",
        (
            "📍 دنبال یه همشهری خوب می‌گردی؟\n\n"
            "توی این ربات میتونی از همون شهر خودت آدم پیدا کنی، "
            "چت کنی و آشنا بشی! 😊\n\n"
            "🔗 از لینک زیر بیا داخل:\n"
            "{invite_link}"
        ),
    ),
    (
        "banner_3",
        "👦 بنر متنی ۳ — دنبال پسر",
        "male",
        (
            "🙋‍♀️ دنبال یه پسر جالب برای آشنایی می‌گردی؟\n\n"
            "اینجا میتونی به‌صورت ناشناس شروع کنی، "
            "اگه جور بودید ادامه بدید! 🎲\n\n"
            "🔗 از لینک زیر ثبت‌نام کن:\n"
            "{invite_link}"
        ),
    ),
    (
        "banner_4",
        "👧 بنر متنی ۴ — دنبال دختر",
        "female",
        (
            "🙋‍♂️ دنبال یه دختر باحال برای آشنایی می‌گردی؟\n\n"
            "توی این ربات میتونی ناشناس شروع کنی و "
            "ببینی باهم جور هستید یا نه! ✨\n\n"
            "🔗 از لینک زیر وارد شو:\n"
            "{invite_link}"
        ),
    ),
    (
        "banner_5",
        "🎂 بنر متنی ۵ — هم‌سن",
        "sameage",
        (
            "⏳ دنبال یه نفر هم‌سن و هم‌نسل خودتی؟\n\n"
            "این ربات بر اساس سن هم بهت پیشنهاد میده، "
            "یعنی احتمال جور بودنتون خیلی بالاست! 🔥\n\n"
            "🔗 بیا امتحان کن:\n"
            "{invite_link}"
        ),
    ),
]


def _build_banner_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب بنر"""
    rows = []
    # دو تا دو تا کنار هم
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"ref_banner:{key}")
        for key, label, _, _ in _REFERRAL_BANNERS
    ]
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i: i + 2])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "referral_banners")
async def show_referral_banners(call: CallbackQuery, db_session: AsyncSession):
    """نمایش منوی انتخاب بنر دعوت"""
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user or not user.completed_registration:
        await call.answer("⚠️ ابتدا ثبت‌نام را کامل کنید.", show_alert=True)
        return

    ref_count = await crud.get_referral_count(db_session, call.from_user.id)

    text = (
        "🔗 <b>بنرهای دعوت اختصاصی شما</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 تا الان <b>{ref_count} نفر</b> با لینک شما وارد شدن.\n\n"
        "💡 <i>یه بنر انتخاب کن تا متن آماده برای فوروارد کردن بهت نشون داده بشه.</i>"
    )

    try:
        await call.message.edit_text(text, reply_markup=_build_banner_keyboard(), parse_mode=ParseMode.HTML)
    except Exception:
        await call.message.answer(text, reply_markup=_build_banner_keyboard(), parse_mode=ParseMode.HTML)
    await call.answer()


@router.callback_query(F.data.startswith("ref_banner:"))
async def send_referral_banner(call: CallbackQuery, db_session: AsyncSession):
    """ارسال متن بنر انتخاب‌شده با لینک فیلتردار"""
    banner_key = call.data.split(":", 1)[1]

    # پیدا کردن بنر
    banner = next((b for b in _REFERRAL_BANNERS if b[0] == banner_key), None)
    if not banner:
        await call.answer("⚠️ بنر یافت نشد.", show_alert=True)
        return

    _, _, filter_param, banner_text = banner

    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user:
        await call.answer("⚠️ کاربر یافت نشد.", show_alert=True)
        return

    # ساخت لینک — اگه فیلتر داره پارامتر اضافه می‌شه
    bot_name = str(settings.BOT_USERNAME).replace("@", "")
    tg_id = call.from_user.id
    if filter_param:
        invite_link = f"https://t.me/{bot_name}?start=ref_{tg_id}_{filter_param}"
    else:
        invite_link = f"https://t.me/{bot_name}?start=ref_{tg_id}"

    # جایگزینی لینک در متن بنر
    final_text = banner_text.format(invite_link=invite_link)

    # دکمه بازگشت به لیست بنرها
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت به بنرها", callback_data="referral_banners")]
    ])

    await call.message.answer(
        f"📋 <b>متن بنر — کپی کن و بفرست:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{final_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb,
    )
    await call.answer()

# ==========================================
# سیستم ثبت و ویرایش کامنت پروفایل
# ==========================================

@router.callback_query(F.data.startswith("write_comment:"))
async def trigger_write_comment(call: CallbackQuery, state: FSMContext):
    """هندلر دکمه شیشه‌ای برای شروع فرآیند ثبت کامنت"""
    target_tg_id = int(call.data.split(":")[1])
    
    # ذخیره آیدی پروفایل هدف در حافظه FSM
    await state.update_data(target_profile_id=target_tg_id)
    await state.set_state(ProfileCommentStates.waiting_for_comment_text)
    
    cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 لغو و برگشت")]], resize_keyboard=True)
    await call.message.answer("✍️ لطفاً کامنت خود را برای این پروفایل بنویسید:", reply_markup=cancel_kb)
    await call.answer()


@router.message(ProfileCommentStates.waiting_for_comment_text)
async def save_comment_text(message: Message, state: FSMContext, db_session: AsyncSession):
    """هندلر دریافت متن کامنت و ذخیره در دیتابیس"""
    if message.text == "🔙 لغو و برگشت":
        await state.clear()
        # برگشت به منوی اصلی با استفاده از کیبورد منوی اصلی ربات
        from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
        return await message.answer("❌ ثبت کامنت لغو شد.", reply_markup=get_main_menu_keyboard())

    data = await state.get_data()
    target_tg_id = data.get("target_profile_id")
    
    # ذخیره یا ویرایش کامنت در دیتابیس
    await upsert_profile_comment(
        session=db_session,
        author_tg_id=message.from_user.id,
        target_tg_id=target_tg_id,
        text=message.text
    )
    await db_session.commit()
    
    await state.clear()
    
    # نمایش پیام موفقیت با دکمه شیشه‌ای مشاهده کامنت‌ها
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="مشاهده کامنت‌ها 💬", callback_data=f"view_comments:{target_tg_id}:0")]
    ])
    
    await message.answer("✅ کامنت شما با موفقیت ثبت/ویرایش شد.", reply_markup=kb)

