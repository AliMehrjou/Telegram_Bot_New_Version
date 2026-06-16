import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select, func, update, true
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.loader import bot
from matching_bot_project.bot.filters.custom import IsAdminFilter
from matching_bot_project.bot.keyboards.inline import get_admin_stats_keyboard
from matching_bot_project.database.models.models import User, MatchHistory, CoinTransaction
from matching_bot_project.database.queries import crud
from matching_bot_project.services.broadcast_worker import BroadcastWorker

logger = logging.getLogger(__name__)
router = Router(name="admin_handler")

# Filter all commands in this router to only admins
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ─────────────────────────────────────────────────────────────────────────────
# 1. Coin Management
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("addcoins"))
async def add_coins_command(message: Message, command: CommandObject, db_session: AsyncSession):
    if not command.args:
        await message.answer("استفاده: /addcoins <tg_id> <amount>")
        return

    args = command.args.split()
    if len(args) != 2:
        await message.answer("استفاده: /addcoins <tg_id> <amount>")
        return

    try:
        tg_id = int(args[0])
        amount = int(args[1])
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("مقادیر نامعتبر است.")
        return

    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user:
        await message.answer("کاربر یافت نشد.")
        return

    await crud.process_coin_transaction(db_session, user, amount, "اعطای ادمین")
    await db_session.commit()

    await message.answer(f"✅ {amount} سکه با موفقیت به کاربر {tg_id} اضافه شد.")
    try:
        await bot.send_message(chat_id=tg_id, text=f"🎁 ادمین {amount} سکه به حساب شما اضافه کرد!")
    except Exception as e:
        logger.warning(f"Could not notify user {tg_id} of coin addition: {e}")

@router.message(Command("removecoins"))
async def remove_coins_command(message: Message, command: CommandObject, db_session: AsyncSession):
    if not command.args:
        await message.answer("استفاده: /removecoins <tg_id> <amount>")
        return

    args = command.args.split()
    if len(args) != 2:
        await message.answer("استفاده: /removecoins <tg_id> <amount>")
        return

    try:
        tg_id = int(args[0])
        amount = int(args[1])
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("مقادیر نامعتبر است.")
        return

    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user:
        await message.answer("کاربر یافت نشد.")
        return

    remove_amount = min(amount, user.coin_balance)
    if remove_amount > 0:
        await crud.process_coin_transaction(db_session, user, -remove_amount, "کسر توسط ادمین")
        await db_session.commit()

    await message.answer(f"✅ {remove_amount} سکه از کاربر {tg_id} کسر شد.")


@router.message(Command("addcoinsall"))
async def add_coins_all_command(message: Message, command: CommandObject, db_session: AsyncSession):
    if not command.args:
        await message.answer("استفاده: /addcoinsall <amount>")
        return

    try:
        amount = int(command.args)
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("مقدار نامعتبر است.")
        return

    # Update in DB
    await db_session.execute(
        update(User)
        .values(coin_balance=User.coin_balance + amount, total_earned_coins=User.total_earned_coins + amount)
    )
    # Insert transactions in bulk would be better, but for simplicity we log the mass operation.
    await db_session.commit()

    # Broadcast notification
    result = await db_session.execute(select(User.tg_id))
    user_ids = [row[0] for row in result.all()]

    worker = BroadcastWorker(bot=bot)
    text = f"🎁 ادمین {amount} سکه به حساب شما اضافه کرد!"
    worker.start_background_broadcast(user_ids=user_ids, text=text, delay_ms=40)

    await message.answer(f"✅ پروسه افزودن {amount} سکه به {len(user_ids)} کاربر شروع شد.")


@router.message(Command("addcoinsvip"))
async def add_coins_vip_command(message: Message, command: CommandObject, db_session: AsyncSession):
    if not command.args:
        await message.answer("استفاده: /addcoinsvip <amount>")
        return

    try:
        amount = int(command.args)
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("مقدار نامعتبر است.")
        return

    # Update in DB
    await db_session.execute(
        update(User)
        .where(User.is_vip == true())
        .values(coin_balance=User.coin_balance + amount, total_earned_coins=User.total_earned_coins + amount)
    )
    await db_session.commit()

    # Broadcast notification
    result = await db_session.execute(select(User.tg_id).where(User.is_vip == true()))
    user_ids = [row[0] for row in result.all()]

    worker = BroadcastWorker(bot=bot)
    text = f"🎁 ادمین {amount} سکه به عنوان هدیه VIP به حساب شما اضافه کرد!"
    worker.start_background_broadcast(user_ids=user_ids, text=text, delay_ms=40)

    await message.answer(f"✅ پروسه افزودن {amount} سکه به {len(user_ids)} کاربر VIP شروع شد.")

# ─────────────────────────────────────────────────────────────────────────────
# 2. User Management
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("banuser"))
async def ban_user_command(message: Message, command: CommandObject, db_session: AsyncSession):
    if not command.args:
        await message.answer("استفاده: /banuser <tg_id>")
        return

    try:
        tg_id = int(command.args)
    except ValueError:
        await message.answer("مقدار نامعتبر است.")
        return

    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user:
        await message.answer("کاربر یافت نشد.")
        return

    user.is_banned = True
    await db_session.commit()

    # Also clear from matching queue
    from matching_bot_project.bot.core.loader import matching_engine
    await matching_engine.remove_from_queue(tg_id)

    await message.answer(f"✅ کاربر {tg_id} مسدود شد.")


@router.message(Command("unbanuser"))
async def unban_user_command(message: Message, command: CommandObject, db_session: AsyncSession):
    if not command.args:
        await message.answer("استفاده: /unbanuser <tg_id>")
        return

    try:
        tg_id = int(command.args)
    except ValueError:
        await message.answer("مقدار نامعتبر است.")
        return

    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user:
        await message.answer("کاربر یافت نشد.")
        return

    user.is_banned = False
    await db_session.commit()
    await message.answer(f"✅ کاربر {tg_id} از مسدودی خارج شد.")


@router.message(Command("setvip"))
async def set_vip_command(message: Message, command: CommandObject, db_session: AsyncSession):
    if not command.args:
        await message.answer("استفاده: /setvip <tg_id> <days>")
        return

    args = command.args.split()
    if len(args) != 2:
        await message.answer("استفاده: /setvip <tg_id> <days>")
        return

    try:
        tg_id = int(args[0])
        days = int(args[1])
    except ValueError:
        await message.answer("مقادیر نامعتبر است.")
        return

    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user:
        await message.answer("کاربر یافت نشد.")
        return

    user.is_vip = True
    user.vip_expires_at = datetime.utcnow() + timedelta(days=days)
    await db_session.commit()

    await message.answer(f"✅ کاربر {tg_id} برای {days} روز VIP شد.")
    try:
        await bot.send_message(chat_id=tg_id, text=f"👑 تبریک! حساب شما برای {days} روز ویژه (VIP) شد!")
    except Exception:
        pass


@router.message(Command("resetprofile"))
async def reset_profile_command(message: Message, command: CommandObject, db_session: AsyncSession):
    if not command.args:
        await message.answer("استفاده: /resetprofile <tg_id>")
        return

    try:
        tg_id = int(command.args)
    except ValueError:
        await message.answer("مقدار نامعتبر است.")
        return

    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user:
        await message.answer("کاربر یافت نشد.")
        return

    user.gender = None
    user.age = None
    user.province = None
    user.city = None
    user.tags = None
    user.profile_photo_file_id = None
    user.completed_registration = False
    await db_session.commit()

    await message.answer(f"✅ پروفایل کاربر {tg_id} ریست شد.")


@router.message(Command("userinfo"))
async def user_info_command(message: Message, command: CommandObject, db_session: AsyncSession):
    if not command.args:
        await message.answer("استفاده: /userinfo <tg_id>")
        return

    try:
        tg_id = int(command.args)
    except ValueError:
        await message.answer("مقدار نامعتبر است.")
        return

    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user:
        await message.answer("کاربر یافت نشد.")
        return

    # Get total matches
    matches = await db_session.scalar(
        select(func.count(MatchHistory.id)).where(
            (MatchHistory.user_one_id == tg_id) | (MatchHistory.user_two_id == tg_id)
        )
    )
    # Get successful chats
    chats = await db_session.scalar(
        select(func.count(MatchHistory.id)).where(
            ((MatchHistory.user_one_id == tg_id) | (MatchHistory.user_two_id == tg_id)) &
            (MatchHistory.chat_approved == true())
        )
    )

    is_vip = "بله" if user.is_vip else "خیر"
    is_banned = "بن‌شده" if getattr(user, 'is_banned', False) else "فعال"

    card = f"""
👤 tg_id: {user.tg_id} | نام: {user.first_name}
⚧ جنسیت: {user.gender or '-'} | سن: {user.age or '-'}
🗺 استان: {user.province or '-'} | شهر: {user.city or '-'}
🪙 سکه: {user.coin_balance} | 💎 VIP: {is_vip}
📊 مچ‌ها: {matches} | چت‌های موفق: {chats}
🕐 آخرین فعالیت: {user.last_active.strftime('%Y-%m-%d %H:%M:%S')}
🚫 وضعیت: {is_banned}
    """
    await message.answer(card)

# ─────────────────────────────────────────────────────────────────────────────
# 3. Advanced Stats
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("adminstats"))
async def admin_stats_command(message: Message):
    await message.answer("📊 آمار کلی", reply_markup=get_admin_stats_keyboard())

@router.callback_query(F.data == "admin_stats_today")
async def admin_stats_today(call: CallbackQuery, db_session: AsyncSession):
    stats = await crud.get_registrations_by_day(db_session, 1)
    await call.message.answer(f"📅 ثبت‌نام‌های امروز: {stats}")
    await call.answer()

@router.callback_query(F.data == "admin_stats_week")
async def admin_stats_week(call: CallbackQuery, db_session: AsyncSession):
    stats = await crud.get_registrations_by_day(db_session, 7)
    await call.message.answer(f"📅 ثبت‌نام‌های ۷ روز گذشته: {stats}")
    await call.answer()

@router.callback_query(F.data == "admin_stats_hours")
async def admin_stats_hours(call: CallbackQuery, db_session: AsyncSession):
    stats = await crud.get_peak_hours(db_session)
    # stats is list of dicts/tuples
    text = "🔥 فعال‌ترین ساعات:\n"
    for h, c in stats:
        text += f"ساعت {h}: {c} کاربر\n"
    await call.message.answer(text or "آمار موجود نیست.")
    await call.answer()

@router.callback_query(F.data == "admin_stats_provinces")
async def admin_stats_provinces(call: CallbackQuery, db_session: AsyncSession):
    stats = await crud.get_top_provinces(db_session, 10)
    text = "🗺 برترین استان‌ها:\n"
    for p, c in stats:
        text += f"{p or 'نامشخص'}: {c} کاربر\n"
    await call.message.answer(text or "آمار موجود نیست.")
    await call.answer()

@router.callback_query(F.data == "admin_stats_conversion")
async def admin_stats_conversion(call: CallbackQuery, db_session: AsyncSession):
    rate = await crud.get_chat_conversion_rate(db_session)
    await call.message.answer(f"💬 نرخ تبدیل مچ به چت: {rate:.1f}%")
    await call.answer()

@router.callback_query(F.data == "admin_stats_vip")
async def admin_stats_vip(call: CallbackQuery, db_session: AsyncSession):
    count = await db_session.scalar(select(func.count(User.id)).where(User.is_vip == true()))
    await call.message.answer(f"💎 تعداد VIP فعال: {count}")
    await call.answer()
