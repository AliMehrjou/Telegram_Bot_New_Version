import logging
import html
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.queries import crud
from matching_bot_project.bot.core.config import settings
import os
import json
from pathlib import Path

logger = logging.getLogger(__name__)
router = Router(name="profile_handler")


@router.message(F.text == "🪬 پروفایل من")
async def view_user_profile(message: Message, db_session: AsyncSession):
    """داشبورد مشخصات کاربر را نشان می‌دهد (بدون ارسال پیام دوبل)"""
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not user or not user.completed_registration:
        await message.answer("⚠️ شما هنوز ثبت نام نکرده‌اید! لطفا دکمه /start را ارسال کنید.")
        return

    gender_txt = "آقا 🙋‍♂️" if user.gender == "Male" else "خانم 🙋‍♀️" if user.gender == "Female" else "نامشخص ❓"
    vip_status = "👑 عضو VIP" if user.is_vip else "🏷️ عضو عادی"

    safe_first_name = html.escape(user.first_name or "کاربر")
    safe_city = html.escape((user.city or "نامشخص").replace('_', ' '))
    safe_bio = html.escape(user.bio or "هنوز نوشته نشده")
    
    user_interests = []
    if user.interests:
        from matching_bot_project.bot.handlers.profile_edit import INTERESTS
        for key in user.interests.split(","):
            if key in INTERESTS:
                user_interests.append(INTERESTS[key])
    interests_txt = "، ".join(user_interests) if user_interests else "هنوز انتخاب نشده"

    profile_card = (
        "👤 <b>پروفایل کاربری بلایند دیت شما:</b>\n\n"
        f"🆔 شناسه تلگرام: <code>{user.tg_id}</code>\n"
        f"🏷️ نام: <b>{safe_first_name}</b>\n"
        f"🙋‍♂️ جنسیت: <b>{gender_txt}</b>\n"
        f"🎂 سن: <b>{user.age}</b> سال\n"
        f"📍 استان: <b>{html.escape(user.province or 'نامشخص')}</b>\n"
        f"📍 شهر: <b>{safe_city}</b>\n"
        f"📝 بیوگرافی: <i>{safe_bio}</i>\n"
        f"🎯 علایق: <b>{interests_txt}</b>\n\n"
        f"⚡ وضعیت اشتراک: <b>{vip_status}</b>\n"
        f"🔋 سهمیه مچینگ ویژه (VIP): <b>{user.vip_quota} عدد</b>\n"
    )

    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 ویرایش مشخصات و علایق", callback_data="edit_profile_triggered")]
    ])

    # ارسال فقط یک پیام (اگر عکس داشت با عکس، وگرنه متنی)
    if user.profile_photo_file_id:
        try:
            await message.answer_photo(
                photo=user.profile_photo_file_id,
                caption=profile_card,
                parse_mode=ParseMode.HTML,
                reply_markup=inline_kb
            )
        except Exception as e:
            logger.error(f"Failed to send profile photo: {e}")
            await message.answer(text=profile_card, parse_mode=ParseMode.HTML, reply_markup=inline_kb)
    else:
        await message.answer(text=profile_card, parse_mode=ParseMode.HTML, reply_markup=inline_kb)


@router.message(F.text == "📍 نزدیک من")
async def view_nearby_users(message: Message, db_session: AsyncSession):
    """پیدا کردن کاربران جنس مخالف در همان شهر و استان"""
    tg_id = message.from_user.id
    current_user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not current_user or not current_user.completed_registration:
        await message.answer("⚠️ شما هنوز ثبت‌نام نکرده‌اید! لطفاً ابتدا دکمه /start را بزنید.")
        return

    nearby_users = await crud.get_nearby_candidates(db_session, current_user, limit=5)

    if not nearby_users:
        await message.answer(
            f"📍 در حال حاضر کاربر جدیدی از جنس مخالف در شهر <b>{current_user.city}</b> یافت نشد.",
            parse_mode=ParseMode.HTML
        )
        return

    response_text = f"📍 <b>کاربران نزدیک شما در شهر {current_user.city}:</b>\n\n"
    for index, idx_user in enumerate(nearby_users, start=1):
        user_bio = idx_user.bio if idx_user.bio else "بدون بیوگرافی"
        response_text += (
            f"{index}️⃣ <b>{html.escape(idx_user.first_name)}</b> | 🎂 {idx_user.age} ساله\n"
            f"📝 <i>{html.escape(user_bio)}</i>\n"
            f"🆔 شناسه: <code>{idx_user.tg_id}</code>\n"
            f"───────────────────\n"
        )
    response_text += "\n🔍 می‌توانید با دکمه <b>«🔍 جستجوی کاربران»</b> به آن‌ها درخواست مچ بدهید!"
    await message.answer(text=response_text, parse_mode=ParseMode.HTML)


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