"""
bot/handlers/referral.py

v3 NEW: Referral system handler.

Routes:
- /referral        → show referral dashboard
- Inline "referral_show_link"  → show unique referral link
- Inline "referral_show_stats" → show statistics

On /start ref_{CODE}, the start.py handler calls ReferralEngine.attribute_referral()
to bind the new user to their referrer.
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from sqlalchemy import select

from matching_bot_project.bot.core.loader import referral_engine
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.constants import Messages as SystemMsg
from matching_bot_project.bot.core.constants import ReplyBtn, Messages as SystemMsg
from matching_bot_project.bot.keyboards.inline import get_referral_dashboard_keyboard
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
from matching_bot_project.database.models.models import User
from matching_bot_project.database.queries import crud
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
logger = logging.getLogger(__name__)
router = Router()

@router.message(Command("referral"))
@router.message(F.text == ReplyBtn.REFERRAL_VIP) # 👈 این خط اضافه شد تا دکمه منو کار کند
async def cmd_referral(message: Message, db_session):
    user = await db_session.execute(select(User).where(User.tg_id == message.from_user.id))
    user = user.scalar_one_or_none()
    if not user or not user.completed_registration:
        return await message.answer("⚠️ ابتدا ثبت‌نام خود را کامل کنید.")

    # ساخت لینک اختصاصی ۸ کاراکتری نسخه V3
    code = await referral_engine.ensure_referral_code(db_session, user.tg_id)
    link = f"https://t.me/{settings.BOT_USERNAME}?start=ref_{code}"

    text = (
        f"🔗 <b>سیستم زیرمجموعه‌گیری</b>\n\n"
        f"لینک اختصاصی شما:\n<code>{link}</code>\n\n"
        f"💎 هر بار که یکی از زیرمجموعه‌های شما سکه خریداری کند، "
        f"<b>{settings.REFERRAL_COMMISSION_PCT}٪</b> از خرید او به‌صورت سکه به شما تعلق می‌گیرد!\n\n"
        f"📈 سکه‌های کسب‌شده از زیرمجموعه‌گیری: <b>{user.referral_earnings}</b>"
    )
    # 👈 اضافه شدن parse_mode="HTML" 
    await message.answer(text, reply_markup=get_referral_dashboard_keyboard(), parse_mode="HTML")
@router.callback_query(F.data == "referral_show_link")
async def referral_show_link(call: CallbackQuery, db_session):
    code = await referral_engine.ensure_referral_code(db_session, call.from_user.id)
    link = f"https://t.me/{settings.BOT_USERNAME}?start=ref_{code}"
    await call.message.edit_text(
        f"🔗 <b>لینک اختصاصی شما</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"این لینک را برای دوستان خود بفرستید. هر بار که یکی از آن‌ها سکه بخرد، "
        f"{settings.REFERRAL_COMMISSION_PCT}٪ از خرید او به شما تعلق می‌گیرد.",
        reply_markup=get_referral_dashboard_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data == "referral_show_stats")
async def referral_show_stats(call: CallbackQuery, db_session):
    stats = await referral_engine.get_referral_stats(db_session, call.from_user.id)
    text = (
        f"📊 <b>آمار زیرمجموعه‌گیری</b>\n\n"
        f"👥 تعداد زیرمجموعه‌ها: <b>{stats['total_referred']}</b>\n"
        f"💎 کل سکه‌های کسب‌شده: <b>{stats['total_commission']}</b>\n\n"
    )
    if stats["recent_commissions"]:
        text += "<b>آخرین پورسانت‌ها:</b>\n"
        for c in stats["recent_commissions"][:5]:
            text += f"• {c.commission_coins} سکه (از کاربر {c.referred_tg_id})\n"
    await call.message.edit_text(text, reply_markup=get_referral_dashboard_keyboard(), parse_mode="HTML")
    await call.answer()


_REFERRAL_BANNERS: list[tuple[str, str, str, str]] = [
    ("banner_1", "📋 بنر متنی ۱", "", "👋 سلام!\n\nیه ربات خفن پیدا کردم که میتونی توش آدم‌های جدید پیدا کنی، چت ناشناس داشته باشی و حتی دیت بری! 🎯\n\n🔗 با لینک زیر بیا داخل، هم تو هم من سکه رایگان می‌گیریم:\n{invite_link}"),
    ("banner_2", "🏠 بنر متنی ۲ — همشهری", "city", "📍 دنبال یه همشهری خوب می‌گردی؟\n\nتوی این ربات میتونی از همون شهر خودت آدم پیدا کنی، چت کنی و آشنا بشی! 😊\n\n🔗 از لینک زیر بیا داخل:\n{invite_link}"),
    ("banner_3", "👦 بنر متنی ۳ — دنبال پسر", "male", "🙋‍♀️ دنبال یه پسر جالب برای آشنایی می‌گردی؟\n\nاینجا میتونی به‌صورت ناشناس شروع کنی، اگه جور بودید ادامه بدید! 🎲\n\n🔗 از لینک زیر ثبت‌نام کن:\n{invite_link}"),
    ("banner_4", "👧 بنر متنی ۴ — دنبال دختر", "female", "🙋‍♂️ دنبال یه دختر باحال برای آشنایی می‌گردی؟\n\nتوی این ربات میتونی ناشناس شروع کنی و ببینی باهم جور هستید یا نه! ✨\n\n🔗 از لینک زیر وارد شو:\n{invite_link}"),
    ("banner_5", "🎂 بنر متنی ۵ — هم‌سن", "sameage", "⏳ دنبال یه نفر هم‌سن و هم‌نسل خودتی؟\n\nاین ربات بر اساس سن هم بهت پیشنهاد میده، یعنی احتمال جور بودنتون خیلی بالاست! 🔥\n\n🔗 بیا امتحان کن:\n{invite_link}"),
]

def _build_banner_keyboard() -> InlineKeyboardMarkup:
    rows = []
    buttons = [InlineKeyboardButton(text=label, callback_data=f"ref_banner:{key}") for key, label, _, _ in _REFERRAL_BANNERS]
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i: i + 2])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data == "referral_banners")
async def show_referral_banners(call: CallbackQuery, db_session):
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user or not user.completed_registration:
        return await call.answer("⚠️ ابتدا ثبت‌نام را کامل کنید.", show_alert=True)

    ref_count = await crud.get_referral_count(db_session, call.from_user.id)
    text = (
        "🔗 <b>بنرهای دعوت اختصاصی شما</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 تا الان <b>{ref_count} نفر</b> با لینک شما وارد شدن.\n\n"
        "💡 <i>یه بنر انتخاب کن تا متن آماده برای فوروارد کردن بهت نشون داده بشه.</i>"
    )
    
    try:
        await call.message.edit_text(text, reply_markup=_build_banner_keyboard(), parse_mode="HTML")
    except Exception:
        await call.message.answer(text, reply_markup=_build_banner_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("ref_banner:"))
async def send_referral_banner(call: CallbackQuery, db_session):
    banner_key = call.data.split(":", 1)[1]
    banner = next((b for b in _REFERRAL_BANNERS if b[0] == banner_key), None)
    if not banner:
        return await call.answer("⚠️ بنر یافت نشد.", show_alert=True)

    _, _, filter_param, banner_text = banner
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user:
        return await call.answer("⚠️ کاربر یافت نشد.", show_alert=True)

    bot_name = str(settings.BOT_USERNAME).replace("@", "")
    
    # 👈 تغییر مهم: گرفتن کد اختصاصی ۸ کاراکتری به جای tg_id
    code = await referral_engine.ensure_referral_code(db_session, call.from_user.id)
    
    # 👈 تغییر مهم: استفاده از code به جای tg_id در لینک
    invite_link = f"https://t.me/{bot_name}?start=ref_{code}_{filter_param}" if filter_param else f"https://t.me/{bot_name}?start=ref_{code}"

    final_text = banner_text.format(invite_link=invite_link)
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت به بنرها", callback_data="referral_banners")]])

    await call.message.answer(
        f"📋 <b>متن بنر — کپی کن و بفرست:</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{final_text}",
        parse_mode="HTML",
        reply_markup=back_kb,
    )
    await call.answer()