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

            await message.answer(error_details, parse_mode=ParseMode.HTML)
            

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

    kb = None
    if user_is_vip:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ ورود به پنل تنظیمات VIP", callback_data="vip_panel")]
        ])

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
            [InlineKeyboardButton(text="📝 ویرایش پروفایل", callback_data="edit_profile_triggered")]
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

        # 👈 فراخوانی تابع جامع دکمه‌های پروفایل به جای دکمه‌های دستی
        markup = get_user_action_keyboard(
            target_tg_id=target_user.tg_id, 
            is_blocked=is_blocked, 
            is_friend=already_friend
        )

    # ارسال مدیا یا متن ساختاریافته‌ی پروفایل
    if target_user.profile_photo_file_id:
        try:
            await message.answer_photo(
                photo=target_user.profile_photo_file_id,
                caption=profile_card[:1024],
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