import logging
import html
import os
import json
import string
import random
from pathlib import Path
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

from matching_bot_project.database.queries import crud
from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)
router = Router(name="profile_handler")

def generate_public_id(length=6):
    
    characters = string.ascii_letters + string.digits
    return f"user_{''.join(random.choice(characters) for _ in range(length))}"


@router.message(F.text == "🪬 پروفایل من")
async def view_user_profile(message: Message, db_session: AsyncSession):
    tg_id = message.from_user.id
    
    # گرفتن یوزر از دیتابیس
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not user or not user.completed_registration:
        await message.answer("⚠️ شما هنوز ثبت نام نکرده‌اید! لطفا دکمه /start را ارسال کنید.")
        return

    if not getattr(user, 'public_id', None):
        characters = string.ascii_letters + string.digits
        new_public_id = f"user_{''.join(random.choice(characters) for _ in range(6))}"
        user.public_id = new_public_id
        await db_session.commit() 
        
    await db_session.refresh(user) 
    public_id = user.public_id

    # دریافت مقادیر
    likes_count = getattr(user, 'likes_count', 0)
    coin_balance = getattr(user, 'coin_balance', 0)
    is_vip = getattr(user, 'is_vip', False)

    gender_txt = "پسر 👱‍♂️" if user.gender == "Male" else "دختر 👩‍🦰" if user.gender == "Female" else "نامشخص ❓"
    vip_status = "👑 عضو VIP" if is_vip else "🏷️ عضو عادی"

    safe_first_name = html.escape(user.first_name or "کاربر")
    safe_city = html.escape((user.city or "نامشخص").replace('_', ' '))
    safe_bio = html.escape(user.bio or "تنظیم نشده")
    safe_interests = html.escape(user.interests or "تنظیم نشده")

    profile_card = (
        f"╔═════════════════════════╗\n"
        f"║ 👤 <b>پروفایل کاربری شما</b> ║\n"
        f"╚═════════════════════════╝\n"
        f"🆔 شناسه تلگرام: <code>{user.tg_id}</code>\n"
        f"🆔 آیدی شما: <code>{public_id}</code> /\n"
        f"───────────────────\n"
        f"🔹 نام: <b>{safe_first_name}</b>\n"
        f"🔹 جنسیت: <b>{gender_txt}</b>\n"
        f"🔹 سن: <b>{user.age} سال</b>\n"
        f"🔹 استان: <b>{html.escape(user.province or 'نامشخص')}</b>\n"
        f"🔹 شهر: <b>{safe_city}</b>\n"
        f"───────────────────\n"
        f"📝 بیوگرافی:\n<i>{safe_bio}</i>\n\n"
        f"🎯 علایق:\n<i>{safe_interests}</i>\n"
        f"───────────────────\n"
        f"⚡ وضعیت اشتراک: <b>{vip_status}</b>\n"
        f"🪙 موجودی سکه: <b>{coin_balance} سکه</b>\n"
        f"❤️ تعداد لایک‌ها: <b>{likes_count}</b>\n\n"
        f"🔔 تنظیم حالت سایلنت: /silent\n"
        f"❌ حذف اکانت ربات: /delete_account"
    )

    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 ویرایش پروفایل", callback_data="edit_profile_triggered")],
        [InlineKeyboardButton(text="💎 بخش ویژه VIP", callback_data="vip_section_triggered")]
    ])

    if user.profile_photo_file_id:
        try:
            await message.answer_photo(
                photo=user.profile_photo_file_id,
                caption=profile_card[:1024],
                parse_mode=ParseMode.HTML,
                reply_markup=inline_kb
            )
        except Exception as e:
            logger.error(f"Failed to send profile photo: {e}")
            await message.answer(text=profile_card, parse_mode=ParseMode.HTML, reply_markup=inline_kb)
    else:
        await message.answer(text=profile_card, parse_mode=ParseMode.HTML, reply_markup=inline_kb)


    profile_voice = getattr(user, 'profile_voice_file_id', None)
    if profile_voice:
        try:
            await message.answer_voice(
                voice=profile_voice,
                caption="🎵 <b>آهنگ/وویس پروفایل شما</b>",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send profile voice: {e}")

# ==========================================
# سیستم سایلنت مود
# ==========================================

def get_silent_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 غیر فعال کردن سایلنت", callback_data="silent_off")],
        [
            InlineKeyboardButton(text="🔕 تا یک ساعت", callback_data="silent_1h"),
            InlineKeyboardButton(text="🔕 تا 20 دقیقه", callback_data="silent_20m")
        ],
        [InlineKeyboardButton(text="🔕 همیشه سایلنت", callback_data="silent_forever")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="close_menu")]
    ])
@router.message(Command("silent"))
async def silent_mode_command(message: Message):
    text = (
        "🔻 حالت سایلنت: <b>غیرفعال</b> 🔔\n"
        "───────────────────\n"
        "💡 با فعال شدن حالت سایلنت، درخواست چت دریافت نخواهید کرد."
    )
    await message.answer(text, reply_markup=get_silent_keyboard(), parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith("silent_"))
async def handle_silent_options(call: CallbackQuery, db_session: AsyncSession):
    action = call.data.split("_")[1]
    now = datetime.now()
    
    if action == "off":
        silent_until = None
        msg = "🔔 حالت سایلنت با موفقیت غیرفعال شد."
    elif action == "20m":
        silent_until = now + timedelta(minutes=20)
        msg = "🔕 ربات تا 20 دقیقه برای شما سایلنت شد."
    elif action == "1h":
        silent_until = now + timedelta(hours=1)
        msg = "🔕 ربات تا ۱ ساعت برای شما سایلنت شد."
    elif action == "forever":
        # یک تاریخ خیلی دور برای حالت همیشه سایلنت
        silent_until = now + timedelta(days=3650)
        msg = "🔕 حالت همیشه سایلنت فعال شد."
    
    # 👈 دقیقاً اینجا: فراخوانی تابع دیتابیس و کامیت کردن تغییرات
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
    
@router.callback_query(F.data.startswith("nearby_"))
async def view_nearby_users_callback(call: CallbackQuery, db_session: AsyncSession):
    """
    دریافت کالبک از کیبورد شیشه‌ایِ نزدیک من (موجود در start.py)
    و نمایش لیست کاربران بر اساس فیلتر انتخابی
    """
    tg_id = call.from_user.id
    current_user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not current_user or not current_user.completed_registration:
        await call.answer("⚠️ شما هنوز ثبت‌نام نکرده‌اید! لطفاً ابتدا دکمه /start را بزنید.", show_alert=True)
        return

    filter_type = call.data.replace("nearby_", "") 

    await call.answer("🔍 در حال جستجوی کاربران نزدیک...")

    
    nearby_users = await crud.get_nearby_candidates(db_session, current_user, limit=5)

    if not nearby_users:
        empty_text = f"📍 در حال حاضر کاربر جدیدی با این مشخصات در شهر <b>{current_user.city}</b> یافت نشد."
        
        
        await call.message.edit_text(
            text=empty_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 متوجه شدم", callback_data="close_menu")]])
        )
        return

    
    response_text = f"📍 <b>کاربران نزدیک شما در شهر {current_user.city}:</b>\n\n"
    for index, idx_user in enumerate(nearby_users, start=1):
        user_bio = idx_user.bio if idx_user.bio else "بدون بیوگرافی"
        
        display_id = getattr(idx_user, 'public_id', idx_user.tg_id)
        
        response_text += (
            f"{index}️⃣ <b>{html.escape(idx_user.first_name)}</b> | 🎂 {idx_user.age} ساله\n"
            f"📝 <i>{html.escape(user_bio)}</i>\n"
            f"🆔 شناسه: <code>{display_id}</code>\n"
            f"───────────────────\n"
        )
    
    response_text += "\n🔍 می‌توانید با استفاده از بخش <b>«🔍 جستجوی کاربران»</b> به آن‌ها درخواست چت بدهید!"
    
    await call.message.edit_text(text=response_text, parse_mode=ParseMode.HTML)

@router.message(F.text == "🎁 زیرمجموعه‌گیری & VIP")
async def view_referral_panel(message: Message, db_session: AsyncSession):
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not user:
        await message.answer("⚠️ کاربری یافت نشد.")
        return

    bot_name = str(settings.BOT_USERNAME).replace("@", "")
    invite_link = f"https://t.me/{bot_name}?start=ref_{tg_id}"
    referral_count = await crud.get_referral_count(db_session, tg_id)

    ref_text = (
        "🎁 <b>سیستم کسب سهمیه رایگان مچینگ پیشرفته (VIP):</b>\n\n"
        "دوستان خود را به ربات دعوت کنید و به ازای هر دعوت موفق، سهمیه مچ دریافت کنید!\n\n"
        f"🔗 <b>لینک اختصاصی دعوت شما:</b>\n<code>{invite_link}</code>\n\n"
        f"👥 تعداد زیرمجموعه‌های فعال شما: <b>{referral_count} نفر</b>\n"
        f"🔋 تعداد مچ‌های پیشرفته باقیمانده شما: <b>{user.vip_quota} عدد</b>"
    )
    await message.answer(text=ref_text, parse_mode=ParseMode.HTML)


@router.message(F.text == "❔ راهنما")
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
    # جدا کردن عبارت بعد از اسلش (مثلا از /user_7mPUCm میشه user_7mPUCm)
    command_text = message.text.strip()
    public_id = command_text[1:] 

    # جستجوی کاربر در دیتابیس
    target_user = await crud.get_user_by_public_id(db_session, public_id)

    if not target_user or not target_user.completed_registration:
        await message.answer("⚠️ کاربری با این آیدی یافت نشد یا پروفایلش تکمیل نیست.")
        return

    # بررسی اینکه آیا کاربر آیدی خودش رو وارد کرده یا کس دیگه‌ای رو
    is_own_profile = (message.from_user.id == target_user.tg_id)

    # دریافت مقادیر
    likes_count = getattr(target_user, 'likes_count', 0)
    coin_balance = getattr(target_user, 'coin_balance', 0)
    is_vip = getattr(target_user, 'is_vip', False)

    gender_txt = "پسر 👱‍♂️" if target_user.gender == "Male" else "دختر 👩‍🦰" if target_user.gender == "Female" else "نامشخص ❓"
    vip_status = "👑 عضو VIP" if is_vip else "🏷️ عضو عادی"

    safe_first_name = html.escape(target_user.first_name or "کاربر")
    safe_city = html.escape((target_user.city or "نامشخص").replace('_', ' '))
    safe_bio = html.escape(target_user.bio or "تنظیم نشده")
    safe_interests = html.escape(target_user.interests or "تنظیم نشده")

    # فرمت پروفایل
    profile_card = (
        f"╔═════════════════════════╗\n"
        f"║ 👤 <b>پروفایل کاربری</b> ║\n"
        f"╚═════════════════════════╝\n"
        f"🆔 شناسه: <code>{target_user.public_id}</code>\n"
        f"───────────────────\n"
        f"🔹 نام: <b>{safe_first_name}</b>\n"
        f"🔹 جنسیت: <b>{gender_txt}</b>\n"
        f"🔹 سن: <b>{target_user.age} سال</b>\n"
        f"🔹 استان: <b>{html.escape(target_user.province or 'نامشخص')}</b>\n"
        f"🔹 شهر: <b>{safe_city}</b>\n"
        f"───────────────────\n"
        f"📝 بیوگرافی:\n<i>{safe_bio}</i>\n\n"
        f"🎯 علایق:\n<i>{safe_interests}</i>\n"
        f"───────────────────\n"
        f"⚡ وضعیت اشتراک: <b>{vip_status}</b>\n"
        f"❤️ تعداد لایک‌ها: <b>{likes_count}</b>\n"
    )

    
    inline_kb = []
    if is_own_profile:
        inline_kb.append([InlineKeyboardButton(text="📝 ویرایش پروفایل", callback_data="edit_profile_triggered")])
        profile_card += "\n💡 <i>شما در حال مشاهده پروفایل خودتان هستید.</i>"
    else:
        
        inline_kb.append([InlineKeyboardButton(text="💬 ارسال درخواست چت", callback_data=f"req_chat_{target_user.tg_id}")])
        inline_kb.append([InlineKeyboardButton(text="❤️ لایک کردن پروفایل", callback_data=f"like_user_{target_user.tg_id}")])

    markup = InlineKeyboardMarkup(inline_keyboard=inline_kb)

    
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
