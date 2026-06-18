import logging
import html
from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ParseMode
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.queries import crud
from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)
router = Router(name="profile_handler")


@router.message(F.text == "👤 پروفایل من")
async def view_user_profile(message: Message, db_session: AsyncSession):
    """Generates user registered dashboard."""
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not user or not user.completed_registration:
        await message.answer("⚠️ شما هنوز ثبت نام نکرده‌اید! لطفا دکمه /start را ارسال کنید.")
        return

    if user.gender == "Male":
        gender_txt = "آقا 🙋‍♂️"
    elif user.gender == "Female":
        gender_txt = "خانم 🙋‍♀️"
    else:
        gender_txt = "نامشخص ❓"

    vip_status = "👑 عضو VIP" if user.is_vip else "🏷️ عضو عادی"

    # CRITICAL FIX 1: Sanitize user inputs to prevent Telegram ParseMode injection crashes
    # Using html.escape ensures characters like <, >, and & do not break the formatting
    safe_first_name = html.escape(user.first_name or "کاربر")
    safe_city = html.escape((user.city or "نامشخص").replace('_', ' '))

    profile_card = (
        "👤 <b>پروفایل کاربری بلایند دیت شما:</b>\n\n"
        f"🆔 شناسه تلگرام: <code>{user.tg_id}</code>\n"
        f"🏷️ نام: <b>{safe_first_name}</b>\n"
        f"🙋‍♂️ جنسیت: <b>{gender_txt}</b>\n"
        f"🎂 سن: <b>{user.age}</b> سال\n"
        f"📍 شهر: <b>{safe_city}</b>\n"
        f"⚡ وضعیت اشتراک: <b>{vip_status}</b>\n"
        f"🔋 سهمیه مچینگ ویژه (VIP): <b>{user.vip_quota} عدد</b>\n"
    )

    await message.answer(text=profile_card, parse_mode=ParseMode.HTML)


# profile.py
@router.message(F.text == "🎁 زیرمجموعه‌گیری & VIP")
async def view_referral_panel(message: Message, db_session: AsyncSession):
    """Displays user invite link, rewards, and total referrals."""
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not user:
        await message.answer("⚠️ کاربری یافت نشد. لطفا /start را بزنید.")
        return

    # CRITICAL FIX 2: Ensure BOT_USERNAME doesn't break the deep link format
    # Stripping '@' guarantees the link remains valid (t.me/botname not t.me/@botname)
    bot_name = str(settings.BOT_USERNAME).replace("@", "")
    invite_link = f"https://t.me/{bot_name}?start=ref_{tg_id}"

    referral_count = await crud.get_referral_count(db_session, tg_id)

    ref_text = (
        "🎁 <b>سیستم کسب سهمیه رایگان مچینگ پیشرفته (VIP):</b>\n\n"
        "دوستان خود را به ربات همسریابی دعوت کنید و به ازای هر دعوت موفق که ثبت‌نام خود را تکمیل کند، "
        "<b>۱ سهمیه مچ ویژه (فیلتردار)</b> دریافت کنید!\n\n"
        f"🔗 <b>لینک اختصاصی دعوت شما:</b>\n<code>{invite_link}</code>\n\n"
        f"👥 تعداد زیرمجموعه‌های فعال شما: <b>{referral_count} نفر</b>\n"
        f"🔋 تعداد مچ‌های پیشرفته باقیمانده شما: <b>{user.vip_quota} عدد</b>"
    )

    await message.answer(text=ref_text, parse_mode=ParseMode.HTML)

@router.message(F.text == "❔ راهنما و پشتیبانی")
async def view_help_panel(message: Message):
    """Displays standard FAQ and support contacts."""
    support_username = str(settings.SUPPORT_USERNAME).replace("@", "")

    help_text = (
        "❔ <b>راهنمای استفاده از ربات همسریابی:</b>\n\n"
        "1️⃣ ابتدا با منوی پروفایل مشخصات خود را دقیق تنظیم کنید.\n"
        "2️⃣ با دکمه <b>شروع دیت یابی</b> وارد صف انتظار مچینگ شوید.\n"
        "3️⃣ سیستم به طور هوشمند و خودکار کاربران جنس مخالف نزدیک شما را پیشنهاد می‌دهد.\n"
        "4️⃣ پس از اتصال، باید در مسابقه پرسشنامه ۲۰ سوالی تفاهم‌سنجی شرکت کنید.\n"
        "5️⃣ در صورتی که پاسخ‌های شما و پارتنر مشترک تایید شود، سیستم چت ناشناس امن را برقرار می‌سازد.\n\n"
        f"📌 <b>پشتیبانی ربات:</b> @{support_username}"
    )
    await message.answer(text=help_text, parse_mode=ParseMode.HTML)