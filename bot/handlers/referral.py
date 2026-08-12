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
