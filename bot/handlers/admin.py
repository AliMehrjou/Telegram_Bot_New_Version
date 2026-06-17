import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, and_
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from matching_bot_project.bot.filters.custom import IsAdminFilter
from matching_bot_project.database.models.models import User, MatchHistory, CoinTransaction
from matching_bot_project.database.queries.crud import get_user_by_tg_id, process_coin_transaction
from matching_bot_project.bot.core.loader import bot
from matching_bot_project.services.broadcast_worker import BroadcastWorker

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())

@router.message(Command("addcoins"))
async def cmd_addcoins(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 3:
        return await message.answer("Usage: /addcoins <tg_id> <amount>")

    try:
        tg_id = int(args[1])
        amount = int(args[2])
    except ValueError:
        return await message.answer("Invalid arguments. tg_id and amount must be integers.")

    user = await get_user_by_tg_id(db_session, tg_id)
    if not user:
        return await message.answer("User not found.")

    await process_coin_transaction(db_session, user, amount, "Admin added coins")
    await db_session.commit()

    await message.answer(f"Successfully added {amount} coins to {tg_id}.")

    try:
        await bot.send_message(tg_id, f"شما {amount} سکه از طرف مدیریت دریافت کردید.")
    except (TelegramForbiddenError, TelegramAPIError) as e:
        logger.warning(f"Could not notify user {tg_id}: {e}")

@router.message(Command("removecoins"))
async def cmd_removecoins(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 3:
        return await message.answer("Usage: /removecoins <tg_id> <amount>")

    try:
        tg_id = int(args[1])
        amount = int(args[2])
    except ValueError:
        return await message.answer("Invalid arguments.")

    user = await get_user_by_tg_id(db_session, tg_id)
    if not user:
        return await message.answer("User not found.")

    actual_amount = min(amount, user.coin_balance)
    await process_coin_transaction(db_session, user, -actual_amount, "Admin removed coins")
    await db_session.commit()

    await message.answer(f"Successfully removed {actual_amount} coins from {tg_id}.")

@router.message(Command("addcoinsall"))
async def cmd_addcoinsall(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 2:
        return await message.answer("Usage: /addcoinsall <amount>")

    try:
        amount = int(args[1])
    except ValueError:
        return await message.answer("Invalid amount.")

    users = await db_session.execute(select(User))
    all_users = users.scalars().all()
    user_ids = []

    for user in all_users:
        await process_coin_transaction(db_session, user, amount, "Global admin reward")
        user_ids.append(user.tg_id)

    await db_session.commit()

    await message.answer(f"Added {amount} coins to {len(user_ids)} users. Starting broadcast notification...")

    worker = BroadcastWorker(bot=bot)
    text = f"شما {amount} سکه هدیه عمومی از طرف مدیریت دریافت کردید!"
    worker.start_background_broadcast(user_ids=user_ids, text=text, delay_ms=40)

@router.message(Command("addcoinsvip"))
async def cmd_addcoinsvip(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 2:
        return await message.answer("Usage: /addcoinsvip <amount>")

    try:
        amount = int(args[1])
    except ValueError:
        return await message.answer("Invalid amount.")

    users = await db_session.execute(select(User).where(User.is_vip == True))
    vip_users = users.scalars().all()
    user_ids = []

    for user in vip_users:
        await process_coin_transaction(db_session, user, amount, "VIP admin reward")
        user_ids.append(user.tg_id)

    await db_session.commit()

    await message.answer(f"Added {amount} coins to {len(user_ids)} VIP users. Starting broadcast notification...")

    worker = BroadcastWorker(bot=bot)
    text = f"کاربر ویژه عزیز، شما {amount} سکه هدیه از طرف مدیریت دریافت کردید!"
    worker.start_background_broadcast(user_ids=user_ids, text=text, delay_ms=40)

@router.message(Command("banuser"))
async def cmd_banuser(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 2:
        return await message.answer("Usage: /banuser <tg_id>")

    try:
        tg_id = int(args[1])
    except ValueError:
        return await message.answer("Invalid tg_id.")

    user = await get_user_by_tg_id(db_session, tg_id)
    if not user:
        return await message.answer("User not found.")

    user.is_banned = True
    await db_session.commit()

    await message.answer(f"User {tg_id} has been banned.")

@router.message(Command("unbanuser"))
async def cmd_unbanuser(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 2:
        return await message.answer("Usage: /unbanuser <tg_id>")

    try:
        tg_id = int(args[1])
    except ValueError:
        return await message.answer("Invalid tg_id.")

    user = await get_user_by_tg_id(db_session, tg_id)
    if not user:
        return await message.answer("User not found.")

    user.is_banned = False
    await db_session.commit()

    await message.answer(f"User {tg_id} has been unbanned.")

@router.message(Command("userinfo"))
async def cmd_userinfo(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 2:
        return await message.answer("Usage: /userinfo <tg_id>")

    try:
        tg_id = int(args[1])
    except ValueError:
        return await message.answer("Invalid tg_id.")

    user = await get_user_by_tg_id(db_session, tg_id)
    if not user:
        return await message.answer("User not found.")

    matches = await db_session.scalar(select(func.count(MatchHistory.id)).where(
        (MatchHistory.user_one_id == tg_id) | (MatchHistory.user_two_id == tg_id)
    ))
    chats = await db_session.scalar(select(func.count(MatchHistory.id)).where(
        and_((MatchHistory.user_one_id == tg_id) | (MatchHistory.user_two_id == tg_id), MatchHistory.chat_approved == True)
    ))

    card = f"""
<b>User Info</b>
ID: {user.tg_id}
Name: {user.first_name}
Gender: {user.gender or 'N/A'}
Age: {user.age or 'N/A'}
City: {user.city or 'N/A'}
Coins: {user.coin_balance}
VIP: {user.is_vip}
Banned: {user.is_banned}
Matches: {matches or 0}
Chat Success: {chats or 0}
Online: {user.is_online}
"""
    await message.answer(card, parse_mode="HTML")

@router.message(Command("setvip"))
async def cmd_setvip(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 3:
        return await message.answer("Usage: /setvip <tg_id> <days>")

    try:
        tg_id = int(args[1])
        days = int(args[2])
    except ValueError:
        return await message.answer("Invalid arguments.")

    user = await get_user_by_tg_id(db_session, tg_id)
    if not user:
        return await message.answer("User not found.")

    user.is_vip = True
    user.vip_expires_at = datetime.utcnow() + timedelta(days=days)
    await db_session.commit()

    await message.answer(f"User {tg_id} is now VIP for {days} days.")

@router.message(Command("resetprofile"))
async def cmd_resetprofile(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 2:
        return await message.answer("Usage: /resetprofile <tg_id>")

    try:
        tg_id = int(args[1])
    except ValueError:
        return await message.answer("Invalid tg_id.")

    user = await get_user_by_tg_id(db_session, tg_id)
    if not user:
        return await message.answer("User not found.")

    user.gender = None
    user.age = None
    user.province = None
    user.city = None
    user.tags = None
    user.completed_registration = False

    await db_session.commit()
    await message.answer(f"Profile reset for {tg_id}.")

def get_stats_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Today's Registrations", callback_data="stats_today_reg")],
        [InlineKeyboardButton(text="Active Hours", callback_data="stats_active_hours")],
        [InlineKeyboardButton(text="Top Provinces", callback_data="stats_top_prov")],
        [InlineKeyboardButton(text="Chat Conversion Rate", callback_data="stats_conv_rate")]
    ])
    return keyboard

@router.message(Command("adminstats"))
async def cmd_adminstats(message: Message):
    await message.answer("Admin Statistics Dashboard:", reply_markup=get_stats_keyboard())

@router.callback_query(F.data.startswith("stats_"))
async def cq_stats(call: CallbackQuery, db_session: AsyncSession):
    action = call.data

    if action == "stats_today_reg":
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        count = await db_session.scalar(select(func.count(User.id)).where(User.created_at >= today))
        await call.message.answer(f"Today's Registrations: {count or 0}")

    elif action == "stats_active_hours":
        # Simplified for now, real implementation would group by hour
        await call.message.answer("Active Hours tracking requires complex grouping. Not fully implemented yet.")

    elif action == "stats_top_prov":
        result = await db_session.execute(
            select(User.province, func.count(User.id).label('count'))
            .where(User.province != None)
            .group_by(User.province)
            .order_by(func.count(User.id).desc())
            .limit(5)
        )
        provs = result.all()
        text = "Top Provinces:\n" + "\n".join([f"{p.province}: {p.count}" for p in provs]) if provs else "No data."
        await call.message.answer(text)

    elif action == "stats_conv_rate":
        total_matches = await db_session.scalar(select(func.count(MatchHistory.id)))
        successful_chats = await db_session.scalar(select(func.count(MatchHistory.id)).where(MatchHistory.chat_approved == True))
        rate = (successful_chats / total_matches * 100) if total_matches else 0
        await call.message.answer(f"Chat Conversion Rate: {rate:.2f}% ({successful_chats}/{total_matches})")

    await call.answer()
