from __future__ import annotations
import os
import json
from pathlib import Path

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from matching_bot_project.bot.core.loader import redis_client
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.loader import bot
from matching_bot_project.bot.filters.custom import IsAdminFilter
from matching_bot_project.database.models.models import MatchHistory, User
from matching_bot_project.database.queries.crud import get_user_by_tg_id, process_coin_transaction
from matching_bot_project.services.broadcast_worker import BroadcastWorker
from matching_bot_project.bot.states.states import AdminStates, EventStates, PBroadcastStates, QuestionAddStates
from matching_bot_project.database.queries import crud
from matching_bot_project.bot.core.config import settings
from aiogram.filters import Command
from matching_bot_project.bot.filters.custom import IsAdminFilter
logger = logging.getLogger(__name__)

router = Router()
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


@router.message(Command("addpackage"), IsAdminFilter())
async def cmd_add_package(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 3:
        return await message.answer("❌ راهنما:\n<code>/addpackage [تعداد سکه] [قیمت به تومان]</code>\nمثال:\n<code>/addpackage 50 20000</code>", parse_mode="HTML")
    
    try:
        coin_amount = int(args[1])
        price_toman = int(args[2])
    except ValueError:
        return await message.answer("❌ مقادیر باید عدد صحیح باشند.")
        
    await crud.create_coin_package(db_session, coin_amount, price_toman)
    await db_session.commit()
    await message.answer(f"✅ بسته جدید با موفقیت ساخته شد:\n🪙 {coin_amount} سکه | 💰 {price_toman:,} تومان")

@router.message(Command("packages"), IsAdminFilter())
async def cmd_list_packages(message: Message, db_session: AsyncSession):
    packages = await crud.get_all_coin_packages(db_session)
    if not packages:
        return await message.answer("⚠️ هیچ بسته‌ای در سیستم تعریف نشده است.")
        
    text = "📦 <b>لیست بسته‌های سکه:</b>\n\n"
    for p in packages:
        status = "✅ فعال" if p.is_active else "❌ غیرفعال"
        text += f"▪️ شناسه: <code>{p.id}</code> | {p.coin_amount} سکه | {p.price_toman:,} تومان | {status}\n"
        
    
    text += "\n💡 برای ویرایش قیمت: <code>/editpackage [id] [new_price]</code>\n💡 برای فعال/غیرفعال کردن: <code>/togglepackage [id]</code>"
    
    await message.answer(text, parse_mode="HTML")

@router.message(Command("editpackage"), IsAdminFilter())
async def cmd_edit_package(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 3:
        return await message.answer("❌ راهنما:\n<code>/editpackage [id] [new_price]</code>", parse_mode="HTML")
    try:
        pkg_id = int(args[1])
        new_price = int(args[2])
    except ValueError:
        return await message.answer("❌ مقادیر باید عدد صحیح باشند.")
        
    success = await crud.update_coin_package_price(db_session, pkg_id, new_price)
    if success:
        await db_session.commit()
        await message.answer(f"✅ قیمت بسته <code>{pkg_id}</code> به <b>{new_price:,}</b> تومان تغییر کرد.", parse_mode="HTML")
    else:
        await message.answer("❌ بسته‌ای با این شناسه یافت نشد.")

@router.message(Command("togglepackage"), IsAdminFilter())
async def cmd_toggle_package(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 2:
        # اصلاح: استفاده از تگ code و براکت به جای علامت‌های کوچکتر/بزرگتر
        return await message.answer("❌ راهنما:\n<code>/togglepackage [id]</code>", parse_mode="HTML")
    try:
        pkg_id = int(args[1])
    except ValueError:
        return await message.answer("❌ شناسه باید عدد باشد.")
        
    new_status = await crud.toggle_coin_package(db_session, pkg_id)
    if new_status is not None:
        await db_session.commit()
        stat_str = "فعال ✅" if new_status else "غیرفعال ❌"
        await message.answer(f"وضعیت بسته <code>{pkg_id}</code> به <b>{stat_str}</b> تغییر یافت.", parse_mode="HTML")
    else:
        await message.answer("❌ بسته‌ای با این شناسه یافت نشد.")


def get_admin_help_keyboard(current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    
    buttons = []
    
    if current_page > 0:
        buttons.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"help_admin_page_{current_page - 1}"))
        
    
    buttons.append(InlineKeyboardButton(text=f"صفحه {current_page + 1} از {total_pages}", callback_data="ignore_pagination"))
    
    if current_page < total_pages - 1:
        buttons.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"help_admin_page_{current_page + 1}"))
        
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


class ActiveEvent:
    """یه رویداد فعال."""
    def __init__(
        self,
        event_id: int,
        name: str,
        description: str,
        coin_multiplier: float,
        ends_at: datetime,
    ):
        self.event_id        = event_id
        self.name            = name
        self.description     = description
        self.coin_multiplier = coin_multiplier
        self.ends_at         = ends_at  
 
    @property
    def is_active(self) -> bool:
        return datetime.now(timezone.utc) < self.ends_at
 
    @property
    def remaining_minutes(self) -> int:
        delta = self.ends_at - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds() // 60))
 
    def to_text(self) -> str:
        status = "🟢 فعال" if self.is_active else "🔴 پایان یافته"
        return (
            f"🎉 <b>{self.name}</b> [{status}]\n"
            f"📝 {self.description}\n"
            f"💰 ضریب سکه: <b>×{self.coin_multiplier}</b>\n"
            f"⏳ زمان باقی‌مانده: <b>{self.remaining_minutes} دقیقه</b>\n"
            f"🔑 ID: <code>{self.event_id}</code>"
        )
 
 
class EventStore:
    """
    نگهداری ساده رویدادها در حافظه.
    thread-safe نیست — برای بار سنگین به Redis منتقل کن.
    """
    _events: dict[int, ActiveEvent] = {}
    _counter: int = 0
 
    @classmethod
    def add(cls, event: ActiveEvent) -> None:
        cls._events[event.event_id] = event
 
    @classmethod
    def get(cls, event_id: int) -> Optional[ActiveEvent]:
        return cls._events.get(event_id)
 
    @classmethod
    def remove(cls, event_id: int) -> bool:
        return cls._events.pop(event_id, None) is not None
 
    @classmethod
    def all_active(cls) -> list[ActiveEvent]:
        return [e for e in cls._events.values() if e.is_active]
 
    @classmethod
    def next_id(cls) -> int:
        cls._counter += 1
        return cls._counter
 
    @classmethod
    def get_current_multiplier(cls) -> float:
        """بالاترین ضریب از بین رویدادهای فعال."""
        actives = cls.all_active()
        if not actives:
            return 1.0
        return max(e.coin_multiplier for e in actives)
 

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

    await process_coin_transaction(db_session, user, amount, "Admin added coins", ignore_multiplier=True)
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
        # جلوگیری از باگ وارد کردن عدد منفی (که باعث اضافه شدن سکه می‌شد)
        amount = abs(int(args[2]))
    except ValueError:
        return await message.answer("Invalid arguments.")

    user = await get_user_by_tg_id(db_session, tg_id)
    if not user:
        return await message.answer("User not found.")

    if user.coin_balance <= 0:
        return await message.answer("User already has 0 coins.")

    actual_amount = min(amount, user.coin_balance)
    await process_coin_transaction(db_session, user, -actual_amount, "Admin removed coins", ignore_multiplier=True)
    await db_session.commit()

    await message.answer(f"Successfully removed {actual_amount} coins from {tg_id}.")

    try:
        await bot.send_message(
            chat_id=tg_id,
            text=f"⚠️ از حساب شما تعداد <b>{actual_amount}</b> سکه توسط مدیریت کسر گردید.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Could not notify user {tg_id} about coin removal: {e}")


@router.message(Command("addcoinsall"))
async def cmd_addcoinsall(message: Message, db_session: AsyncSession):
    args = message.text.split()
    if len(args) != 2:
        return await message.answer("Usage: /addcoinsall <amount>")

    try:
        amount = int(args[1])
    except ValueError:
        return await message.answer("Invalid amount.")

    user_ids = []
    
    # استفاده از stream_scalars و yield_per برای خواندن رکوردها به صورت پارت‌پارت (500تایی)
    stream_result = await db_session.stream_scalars(select(User).execution_options(yield_per=500))
    
    async for user in stream_result:
        await process_coin_transaction(db_session, user, amount, "Global admin reward", ignore_multiplier=True)
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

    user_ids = []
    
    stream_result = await db_session.stream_scalars(
        select(User).where(User.is_vip == True).execution_options(yield_per=500)
    )

    async for user in stream_result:
        await process_coin_transaction(db_session, user, amount, "VIP admin reward", ignore_multiplier=True)
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

    if tg_id in settings.parsed_admin_ids:
        return await message.answer("⚠️ شما نمی‌توانید یک ادمین را مسدود کنید!")

    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if not user:
        return await message.answer("User not found.")

    user.is_banned = True

    # --- فیکس باگ پنهان: قطع کردن ارتباط کاربری که در وسط دیت است ---
    active_match = await crud.get_active_match(db_session, tg_id)
    if active_match:
        active_match.is_active = False
        active_match.ended_at = datetime.now(timezone.utc)
        
        partner_id = active_match.user_one_id if active_match.user_two_id == tg_id else active_match.user_two_id
        try:
            from aiogram.fsm.context import FSMContext
            from aiogram.fsm.storage.base import StorageKey
            from matching_bot_project.bot.core.loader import dp, bot
            
            partner_ctx = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=partner_id, user_id=partner_id))
            await partner_ctx.set_state(None)
            
            await bot.send_message(
                chat_id=partner_id,
                text="⚠️ <b>دیت ناشناس متوقف شد!</b>\nحساب کاربری پارتنر شما توسط سیستم مسدود گردید.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Could not notify partner {partner_id} about ban: {e}")

    await db_session.commit()

    notify_status = ""
    try:
        await bot.send_message(
            chat_id=tg_id,
            text="❌ <b>حساب کاربری شما به دلیل نقض قوانین ربات مسدود شد.</b>",
            parse_mode="HTML"
        )
        notify_status = "✅ پیام اخطار به کاربر تحویل داده شد."
    except Exception:
        notify_status = "⚠️ کاربر ربات را بلاک کرده است."

    try:
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from matching_bot_project.bot.core.loader import dp, bot, matching_engine
        
        ctx = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=tg_id, user_id=tg_id))
        await ctx.set_state(None)
        await ctx.clear()
        
        await matching_engine.remove_from_queue(tg_id)
    except Exception as e:
        logger.warning(f"Could not clear FSM session for banned user {tg_id}: {e}")

    await message.answer(
        f"✅ کاربر <code>{tg_id}</code> با موفقیت مسدود و ارتباطات وی قطع شد.\nℹ️ <i>{notify_status}</i>", 
        parse_mode="HTML"
    )




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
    user.vip_expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    await db_session.commit()

    await message.answer(f"User {tg_id} is now VIP for {days} days.")

    # 🟢 اطلاع‌رسانی مستقیم به کاربر
    try:
        await bot.send_message(
            chat_id=tg_id,
            text=f"🎉 <b>تبریک!</b>\n\nاشتراک ویژه (VIP) شما از طرف مدیریت فعال شد.\nشما برای <b>{days} روز</b> آینده به تمامی امکانات ویژه دسترسی خواهید داشت.",
            parse_mode="HTML"
        )
    except TelegramForbiddenError:
        await message.answer(f"⚠️ پیام به {tg_id} ارسال نشد (کاربر ربات را بلاک کرده است).")
    except TelegramAPIError as e:
        logger.error(f"Failed to send VIP notification to {tg_id}: {e}")


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
        # اصلاح یکپارچه‌سازی زمان به حالت Timezone-aware UTC
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        count = await db_session.scalar(select(func.count(User.id)).where(User.created_at >= today))
        await call.message.answer(f"Today's Registrations: {count or 0}")

    elif action == "stats_active_hours":
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

@router.callback_query(F.data.startswith("admin_ban_"))
async def admin_quick_ban(call: CallbackQuery, db_session: AsyncSession):
    # ۱. دریافت و اعتبارسنجی آیدی کاربر
    try:
        target_tg_id = int(call.data.removeprefix("admin_ban_"))
    except ValueError:
        return await call.answer("⚠️ دیتای کالبک دکمه نامعتبر است!", show_alert=True)
    
    if target_tg_id == call.from_user.id:
        return await call.answer("⚠️ شما نمی‌توانید خودتان را مسدود کنید!", show_alert=True)

    if target_tg_id in settings.parsed_admin_ids:
        return await call.answer("⚠️ شما نمی‌توانید یک ادمین دیگر را مسدود کنید!", show_alert=True)

    user = await crud.get_user_by_tg_id(db_session, target_tg_id)
    if not user:
        return await call.answer("⚠️ این کاربر در دیتابیس یافت نشد!", show_alert=True)

    await call.answer("عملیات مسدودسازی در حال انجام است...")

    # ۲. تغییر وضعیت کاربر به مسدود
    user.is_banned = True
    
    # ۳. فیکس باگ امنیتی: قطع ارتباط در صورت وجود دیت فعال
    from datetime import datetime, timezone
    active_match = await crud.get_active_match(db_session, target_tg_id)
    if active_match:
        active_match.is_active = False
        active_match.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        partner_id = active_match.user_one_id if active_match.user_two_id == target_tg_id else active_match.user_two_id
        try:
            from aiogram.fsm.context import FSMContext
            from aiogram.fsm.storage.base import StorageKey
            from matching_bot_project.bot.core.loader import dp, bot
            
            # پاک کردن نشست پارتنر
            partner_ctx = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=partner_id, user_id=partner_id))
            await partner_ctx.set_state(None)
            
            # اطلاع‌رسانی به پارتنر
            await bot.send_message(
                chat_id=partner_id,
                text="⚠️ <b>دیت ناشناس متوقف شد!</b>\nحساب کاربری پارتنر شما توسط سیستم مسدود گردید.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Could not notify partner {partner_id} about ban: {e}")

    # ۴. ذخیره تغییرات در دیتابیس
    await db_session.commit()

    # ۵. پاکسازی کامل نشست (FSM) و خروج از صف مچ‌یابی کاربر بن‌شده
    try:
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from matching_bot_project.bot.core.loader import dp, bot, matching_engine
        
        ctx = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=target_tg_id, user_id=target_tg_id))
        await ctx.set_state(None)
        await ctx.clear()
        await matching_engine.remove_from_queue(target_tg_id)
    except Exception as e:
        logger.warning(f"Could not clear FSM session for banned user {target_tg_id}: {e}")
        
    # ۶. ارسال پیام اخطار به کاربر متخلف
    user_notification = "❌ <b>حساب کاربری شما به دلیل نقض قوانین مسدود (Ban) شد.</b>"
    try:
        await bot.send_message(chat_id=target_tg_id, text=user_notification, parse_mode="HTML")
        ban_msg_status = "✅ پیام اخطار به کاربر تحویل داده شد."
    except Exception:
        ban_msg_status = "⚠️ کاربر ربات را بلاک کرده است (پیام تحویل نشد)."

    # ۷. آپدیت پیام شیشه‌ای ادمین برای نمایش وضعیت جدید
    unban_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 رفع مسدودیت (Unban)", callback_data=f"admin_unban_{target_tg_id}")]
    ])
    
    await call.message.edit_text(
        text=(
            "📩 <b>وضعیت پیام پشتیبانی تغییر یافت:</b>\n\n"
            f"👤 شناسه کاربر متخلف: <code>{target_tg_id}</code>\n"
            "──────────────────────────────\n"
            "⛔️ <b>وضعیت: این کاربر با موفقیت مسدود شد و ارتباطات وی قطع گردید.</b>\n"
            f"ℹ️ <i>{ban_msg_status}</i>"
        ),
        parse_mode="HTML",
        reply_markup=unban_kb
    )



@router.callback_query(F.data.startswith("admin_unban_"))
async def admin_quick_unban(call: CallbackQuery, db_session: AsyncSession):
    try:
        target_tg_id = int(call.data.removeprefix("admin_unban_"))
    except ValueError:
        return await call.answer("⚠️ دیتای کالبک دکمه نامعتبر است!", show_alert=True)
        
    await call.answer("کاربر رفع مسدودیت شد.")
    
    user = await crud.get_user_by_tg_id(db_session, target_tg_id)
    if not user:
        return await call.message.answer("⚠️ این کاربر در دیتابیس یافت نشد!")

    user.is_banned = False
    await db_session.commit()
    
    try:
        await bot.send_message(
            chat_id=target_tg_id, 
            text="🟢 <b>حساب کاربری شما رفع مسدودیت (Unban) شد و می‌توانید مجدداً از ربات استفاده کنید.</b>", 
            parse_mode="HTML"
        )
        unban_msg_status = "✅ پیام رفع بن به کاربر ارسال شد."
    except Exception:
        unban_msg_status = "⚠️ کاربر ربات را بلاک کرده است."

    revert_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 پاسخ به کاربر", callback_data=f"admin_reply_{target_tg_id}")],
        [InlineKeyboardButton(text="⛔️ بن کردن کاربر", callback_data=f"admin_ban_{target_tg_id}")]
    ])
    
    await call.message.edit_text(
        text=(
            "📩 <b>وضعیت پیام پشتیبانی تغییر یافت:</b>\n\n"
            f"👤 شناسه کاربر: <code>{target_tg_id}</code>\n"
            "──────────────────────────────\n"
            f"🟢 <b>وضعیت: کاربر مجدداً فعال (Unban) شد.</b>\n"
            f"ℹ️ <i>{unban_msg_status}</i>"
        ),
        parse_mode="HTML",
        reply_markup=revert_kb
    )


# ─── ۴. هندلر کلیک روی دکمه پاسخ به کاربر ───
@router.callback_query(F.data.startswith("admin_reply_"))
async def admin_start_reply(call: CallbackQuery, state: FSMContext):
    await call.answer()
    try:
        target_tg_id = int(call.data.removeprefix("admin_reply_"))
    except ValueError:
        return await call.answer("⚠️ دیتای کالبک دکمه نامعتبر است!", show_alert=True)
    
    await state.update_data(reply_target_id=target_tg_id)
    await state.set_state(AdminStates.waiting_for_support_reply)
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_admin_reply")]
    ])
    
    await call.message.answer(
        text=f"✍️ در حال پاسخ به کاربر <code>{target_tg_id}</code>...\n\nلطفاً پیام خود را بنویسید:", 
        reply_markup=cancel_kb, 
        parse_mode="HTML"
    )


# ─── ۵. هندلر دکمه انصراف شیشه‌ای ادمین ───
@router.callback_query(F.data == "cancel_admin_reply", AdminStates.waiting_for_support_reply)
async def cancel_admin_reply(call: CallbackQuery, state: FSMContext):
    await call.answer("عملیات لغو شد.")
    await state.clear()
    await call.message.edit_text("❌ ارسال پاسخ به کاربر لغو شد.")


@router.message(AdminStates.waiting_for_support_reply)
async def admin_send_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    target_tg_id = data.get("reply_target_id")
    
    try:
        
        await bot.send_message(chat_id=target_tg_id, text="👨‍💻 <b>پیام جدید از پشتیبانی ربات:</b>", parse_mode="HTML")
        
        
        await bot.copy_message(chat_id=target_tg_id, from_chat_id=message.chat.id, message_id=message.message_id)
        
        await message.answer("✅ پاسخ شما (مدیا/متن) با موفقیت به کاربر تحویل داده شد.")
    except TelegramForbiddenError:
        await message.answer("⛔️ خطا: کاربر ربات را بلاک کرده است.")
    except TelegramAPIError as e:
        await message.answer(f"⚠️ خطای غیرمنتظره تلگرام:\n{e}")

    await state.clear()

def _load_help_pages():
    """خواندن صفحات راهنما از فایل JSON"""
    json_path = Path("json_files/help_admin.json")
    
    if not os.path.exists(json_path):
        json_path = Path("/app/json_files/help_admin.json")
        
    if not os.path.exists(json_path):
        return None
        
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("pages", [])
    except Exception as e:
        logger.error(f"Error reading help_admin.json: {e}", exc_info=True)
        return None

@router.message(Command("help_admin"))
async def cmd_help_admin(message: Message):
    pages = _load_help_pages()
    
    if not pages:
        return await message.answer(
            "⚠️ <b>فایل راهنما یافت نشد یا ساختار آن نامعتبر است!</b>\n"
            "مسیرهای بررسی شده:\n"
            "<code>json_files/help_admin.json</code>\n"
            "<code>/app/json_files/help_admin.json</code>", 
            parse_mode="HTML"
        )
        
    
    current_page = 0
    help_text = "\n".join(pages[current_page])
    keyboard = get_admin_help_keyboard(current_page, len(pages))
    
    await message.answer(text=help_text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data.startswith("help_admin_page_"))
async def cq_help_admin_pagination(call: CallbackQuery):
    pages = _load_help_pages()
    if not pages:
        return await call.answer("⚠️ خطا در خواندن صفحات راهنما.", show_alert=True)
        
    try:
        target_page = int(call.data.split("_")[-1])
    except ValueError:
        return await call.answer("⚠️ خطای سیستمی.", show_alert=True)
        
    if target_page < 0 or target_page >= len(pages):
        return await call.answer("⚠️ صفحه مورد نظر وجود ندارد.", show_alert=True)
        
    help_text = "\n".join(pages[target_page])
    keyboard = get_admin_help_keyboard(target_page, len(pages))
    
    try:
        await call.message.edit_text(text=help_text, reply_markup=keyboard, parse_mode="HTML")
        await call.answer() 
    except Exception as e:
        logger.error(f"Pagination edit error: {e}") 
        
        
        error_msg = str(e)[:150] 
        await call.answer(f"⚠️ ارور فرمت تلگرام در این صفحه:\n{error_msg}", show_alert=True)

@router.callback_query(F.data == "ignore_pagination")
async def ignore_pagination_click(call: CallbackQuery):
    """هندل کردن کلیک روی دکمه‌ی شماره صفحه (دکمه وسط)"""
    await call.answer()

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    """آغاز فرآیند ارسال پیام همگانی توسط ادمین"""
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_broadcast")]
    ])
    
    await message.answer(
        "📢 <b>ارسال پیام همگانی</b>\n\n"
        "لطفاً پیامی که می‌خواهید برای همه کاربران ربات ارسال شود را تایپ کنید:\n"
        "(می‌توانید از فرمت‌های متنی مثل بولد و ایتالیک هم استفاده کنید)\n\n"
        "برای لغو، روی دکمه زیر کلیک کنید.",
        reply_markup=cancel_kb,
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_broadcast_message)


@router.callback_query(F.data == "cancel_broadcast", AdminStates.waiting_for_broadcast_message)
async def cancel_broadcast(call: CallbackQuery, state: FSMContext):
    """لغو فرآیند پیام همگانی"""
    await call.answer("عملیات لغو شد.")
    await state.clear()
    await call.message.edit_text("❌ ارسال پیام همگانی لغو شد.")


@router.message(AdminStates.waiting_for_broadcast_message)
async def process_broadcast_message(message: Message, state: FSMContext, db_session: AsyncSession):
    """دریافت محتوا (متن، عکس، ویدیو و...) از ادمین و آغاز ارسال در پس‌زمینه"""
    
    # 💡 اصلاح باگ ۳: گارد امنیتی برای جلوگیری از برودکست اشتباه کامندها (مثل /cancel یا /start)
    if message.text and message.text.startswith("/"):
        return await message.answer(
            "⚠️ <b>خطا:</b> شما در وضعیت ارسال پیام همگانی هستید!\n"
            "ارسال دستورات سیستمی به عنوان پیام عمومی مجاز نیست.\n"
            "اگر مایل به لغو هستید، لطفاً روی دکمه شیشه‌ای <b>❌ انصراف</b> کلیک کنید.",
            parse_mode="HTML"
        )
        
    user_ids = []
    
    # فقط آیدی‌ها رو بخون، نه کل آبجکت رو (با yield_per برای جلوگیری از OOM)
    stream_result = await db_session.stream_scalars(select(User.tg_id).execution_options(yield_per=1000))
    async for tg_id in stream_result:
        user_ids.append(tg_id)

    if not user_ids:
        await state.clear()
        return await message.answer("هیچ کاربری در دیتابیس یافت نشد.")

    # استفاده از ورکر برای کپی کردن دقیق همین پیام
    worker = BroadcastWorker(bot=bot)
    worker.start_background_broadcast(
        user_ids=user_ids, 
        from_chat_id=message.chat.id,    # آیدی چت ادمین
        message_id=message.message_id,   # آیدی پیامی که ادمین فرستاده
        delay_ms=40  
    )

    await state.clear()
    await message.answer(
        f"✅ <b>عملیات موفق</b>\n\n"
        f"ارسال پیام همگانی در پس‌زمینه آغاز شد.\n"
        f"تعداد مخاطبین هدف: <b>{len(user_ids)}</b> کاربر.",
        parse_mode="HTML"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Event System
# ─────────────────────────────────────────────────────────────────────────────
 
def _cancel_keyboard(cb: str = "cancel_event") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data=cb)]
    ])
 
 
@router.message(Command("event_create"))
async def cmd_event_create(message: Message, state: FSMContext) -> None:
    """شروع فرآیند ساخت رویداد."""
    await state.set_state(EventStates.waiting_for_name)
    await message.answer(
        "🎉 <b>ساخت رویداد جدید</b>\n\n"
        "مرحله ۱/۴ — نام رویداد را وارد کنید:\n"
        "<i>مثال: جمعه شب دیتینگ</i>",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
 
 
@router.message(EventStates.waiting_for_name)
async def event_get_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not name:
        return await message.answer("⚠️ نام نمیتواند خالی باشد.")
 
    await state.update_data(event_name=name)
    await state.set_state(EventStates.waiting_for_description)
    await message.answer(
        "مرحله ۲/۴ — توضیح رویداد را بنویسید:\n"
        "<i>مثال: امشب مچ سریع‌تر، سکه بیشتر! آنلاین باش.</i>",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
 
 
@router.message(EventStates.waiting_for_description)
async def event_get_description(message: Message, state: FSMContext) -> None:
    await state.update_data(event_description=message.text.strip())
    await state.set_state(EventStates.waiting_for_duration)
    await message.answer(
        "مرحله ۳/۴ — مدت رویداد را به <b>دقیقه</b> وارد کنید:\n"
        "<i>مثال: 120 (برابر ۲ ساعت)</i>",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
 
 
@router.message(EventStates.waiting_for_duration)
async def event_get_duration(message: Message, state: FSMContext) -> None:
    try:
        minutes = int(message.text.strip())
        if minutes <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("⚠️ لطفاً یک عدد صحیح مثبت وارد کنید.")
 
    await state.update_data(event_duration_minutes=minutes)
    await state.set_state(EventStates.waiting_for_multiplier)
    await message.answer(
        "مرحله ۴/۴ — ضریب سکه را وارد کنید:\n"
        "<i>مثال: 2 یا 1.5</i>",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
 
 
@router.message(EventStates.waiting_for_multiplier)
async def event_get_multiplier(message: Message, state: FSMContext) -> None:
    try:
        multiplier = float(message.text.strip().replace(",", "."))
        if multiplier <= 1.0:
            raise ValueError
    except ValueError:
        return await message.answer("⚠️ ضریب باید یک عدد بزرگ‌تر از ۱ باشد. مثال: 2")
 
    data = await state.get_data()
    await state.update_data(event_multiplier=multiplier)
    await state.set_state(EventStates.confirming)
 
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ ایجاد رویداد", callback_data="event_confirm"),
            InlineKeyboardButton(text="❌ انصراف",      callback_data="cancel_event"),
        ]
    ])
 
    duration = data["event_duration_minutes"]
    await message.answer(
        "📋 <b>خلاصه رویداد:</b>\n\n"
        f"🏷️ نام: <b>{data['event_name']}</b>\n"
        f"📝 توضیح: {data['event_description']}\n"
        f"⏱️ مدت: <b>{duration} دقیقه</b>\n"
        f"💰 ضریب سکه: <b>×{multiplier}</b>\n\n"
        "آیا تأیید می‌کنید؟",
        parse_mode="HTML",
        reply_markup=confirm_kb,
    )
 
 
@router.callback_query(EventStates.confirming, F.data == "event_confirm")
async def event_confirm(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    
    await call.answer()
    data = await state.get_data()
    await state.clear()
 
    duration_minutes = data["event_duration_minutes"]
    duration_seconds = duration_minutes * 60
    multiplier = data["event_multiplier"]
    
    ends_at = (datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)).isoformat()
    
    # پکیج کردن دیتای رویداد برای ذخیره در ردیس
    event_data = {
        "name": data['event_name'],
        "description": data['event_description'],
        "multiplier": multiplier,
        "ends_at": ends_at
    }
 
    # ذخیره ضریب و متادیتا در ردیس با زمان انقضای خودکار (TTL)
    await redis_client.setex("bot:active_event_data", duration_seconds, json.dumps(event_data))
    await redis_client.setex("bot:active_event_multiplier", duration_seconds, str(multiplier))
 
    await call.message.edit_text(
        f"✅ رویداد با موفقیت ایجاد شد!\n\n"
        f"🏷️ نام: <b>{data['event_name']}</b>\n"
        f"⏱️ مدت: <b>{duration_minutes} دقیقه</b>\n"
        f"💰 ضریب: <b>×{multiplier}</b>\n\n"
        f"این ضریب روی تمام دریافت‌های سکه (ثبت‌نام، دعوت دوستان، مچینگ و...) اعمال خواهد شد.",
        parse_mode="HTML",
    )
 
    # بهینه‌سازی استخراج آیدی یوزرها برای نوتیفیکیشن ایونت
    user_ids = []
    stream_result = await db_session.stream_scalars(select(User.tg_id).execution_options(yield_per=1000))
    async for tg_id in stream_result:
        user_ids.append(tg_id)
 
    notification_text = (
        f"🎉 <b>رویداد ویژه شروع شد!</b>\n\n"
        f"<b>{data['event_name']}</b>\n"
        f"{data['event_description']}\n\n"
        f"💰 تا <b>{duration_minutes} دقیقه</b> دیگر سکه‌هات ×{multiplier} میشن!\n"
        "همین الان وارد ربات شو 👇"
    )
 
    worker = BroadcastWorker(bot=bot)
    worker.start_background_broadcast(user_ids=user_ids, text=notification_text, delay_ms=40)

 
@router.callback_query(F.data == "cancel_event")
async def event_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("لغو شد.")
    await state.clear()
    await call.message.edit_text("❌ ساخت رویداد لغو شد.")
 
 
@router.message(Command("event_list"))
async def cmd_event_list(message: Message) -> None:
    
    event_data_str = await redis_client.get("bot:active_event_data")
    
    if not event_data_str:
        return await message.answer("هیچ رویداد فعالی در سیستم وجود ندارد.")
 
    event_data = json.loads(event_data_str)
    ends_at = datetime.fromisoformat(event_data["ends_at"])
    remaining_minutes = max(0, int((ends_at - datetime.now(timezone.utc)).total_seconds() // 60))
    
    text = (
        "📅 <b>رویداد فعال فعلی:</b>\n\n"
        f"🎉 <b>{event_data['name']}</b> [🟢 فعال]\n"
        f"📝 {event_data['description']}\n"
        f"💰 ضریب سکه: <b>×{event_data['multiplier']}</b>\n"
        f"⏳ زمان باقی‌مانده: <b>{remaining_minutes} دقیقه</b>\n"
    )
    
    await message.answer(text, parse_mode="HTML")

 
@router.message(Command("event_end"))
async def cmd_event_end(message: Message) -> None:
    # پاک کردن کلید ضریب و دیتای متادیتا از ردیس
    deleted_mult = await redis_client.delete("bot:active_event_multiplier")
    await redis_client.delete("bot:active_event_data")
    
    if deleted_mult:
        await message.answer("✅ رویداد فعال با موفقیت پایان یافت و ضریب سکه‌ها به حالت عادی (×1.0) برگشت.")
    else:
        await message.answer("⚠️ در حال حاضر هیچ رویداد فعالی در سیستم وجود ندارد.")
        
# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Personalized Broadcast
# ─────────────────────────────────────────────────────────────────────────────
 
# متغیرهای قابل استفاده در پیام
PBROADCAST_VARS = "{name}  {city}  {coins}  {age}"
 
def _parse_filters(text: str) -> dict:
    """
    پارس فیلترهای ساده از متن.
    فرمت: gender=female age=20-25 city=تهران vip=true
    مقادیر نامعتبر نادیده گرفته میشن.
    """
    filters: dict = {}
    for token in text.strip().split():
        if "=" not in token:
            continue
        key, _, val = token.partition("=")
        key = key.lower().strip()
        val = val.strip()
 
        if key == "gender" and val in ("male", "female", "مرد", "زن"):
            filters["gender"] = val
        elif key == "vip" and val.lower() in ("true", "yes", "1"):
            filters["vip"] = True
        elif key == "city":
            filters["city"] = val
        elif key == "age":
            # فرمت: age=20-25
            parts = val.split("-")
            if len(parts) == 2:
                try:
                    filters["age_min"] = int(parts[0])
                    filters["age_max"] = int(parts[1])
                except ValueError:
                    pass
 
    return filters
 
 
def _build_filter_summary(filters: dict) -> str:
    if not filters:
        return "همه کاربران"
    parts = []
    if "gender" in filters:
        parts.append(f"جنسیت: {filters['gender']}")
    if "city" in filters:
        parts.append(f"شهر: {filters['city']}")
    if "age_min" in filters:
        parts.append(f"سن: {filters['age_min']}–{filters['age_max']}")
    if filters.get("vip"):
        parts.append("فقط VIP")
    return " | ".join(parts)
 
 
async def _fetch_filtered_users(db_session: AsyncSession, filters: dict) -> list[User]:
    """کوئری بر اساس فیلترهای ادمین."""
    conditions = []
 
    if "gender" in filters:
        conditions.append(User.gender == filters["gender"])
    if "city" in filters:
        conditions.append(User.city == filters["city"])
    if "age_min" in filters:
        conditions.append(User.age >= filters["age_min"])
        conditions.append(User.age <= filters["age_max"])
    if filters.get("vip"):
        conditions.append(User.is_vip == True)
 
    stmt = select(User)
    if conditions:
        stmt = stmt.where(and_(*conditions))
 
    result = await db_session.execute(stmt)
    return list(result.scalars().all())
 
 
def _personalize(template: str, user: User) -> str:
    """جایگزینی متغیرها با مقادیر واقعی کاربر."""
    return (
        template
        .replace("{name}",  user.first_name or "دوست عزیز")
        .replace("{city}",  user.city or "ایران")
        .replace("{coins}", str(getattr(user, "coin_balance", 0)))
        .replace("{age}",   str(user.age or ""))
    )
 
 
@router.message(Command("pbroadcast"))
async def cmd_pbroadcast(message: Message, state: FSMContext) -> None:
    await state.set_state(PBroadcastStates.waiting_for_filter)
    await message.answer(
        "📢 <b>پیام شخصی‌سازی‌شده</b>\n\n"
        "مرحله ۱/۲ — فیلتر مخاطبان:\n\n"
        "فرمت: <code>gender=female age=20-25 city=تهران vip=true</code>\n\n"
        "برای ارسال به <b>همه</b>، یک خط فاصله بفرست: <code>-</code>",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard("cancel_pbroadcast"),
    )
 
 
@router.message(PBroadcastStates.waiting_for_filter)
async def pbroadcast_get_filter(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    filters = {} if raw == "-" else _parse_filters(raw)
 
    await state.update_data(pb_filters=filters)
    await state.set_state(PBroadcastStates.waiting_for_message)
 
    await message.answer(
        "مرحله ۲/۲ — متن پیام را بنویسید:\n\n"
        f"متغیرهای قابل استفاده: <code>{PBROADCAST_VARS}</code>\n\n"
        "<i>مثال: سلام {name} جان! امشب توی {city} دنبال دیت می‌گردیم 😉</i>",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard("cancel_pbroadcast"),
    )
 
 
@router.message(PBroadcastStates.waiting_for_message)
async def pbroadcast_get_message(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    template = message.html_text or message.text or ""
    if not template.strip():
        return await message.answer("⚠️ متن پیام نمی‌تواند خالی باشد.")
 
    data = await state.get_data()
    filters: dict = data.get("pb_filters", {})
 
    # پیش‌نمایش با یه یوزر فرضی
    class _FakeUser:
        first_name  = "علی"
        city        = "تهران"
        coin_balance = 120
        age         = 23
 
    preview = _personalize(template, _FakeUser())  # type: ignore[arg-type]
 
    await state.update_data(pb_template=template)
    await state.set_state(PBroadcastStates.confirming)
 
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ ارسال", callback_data="pbroadcast_confirm"),
            InlineKeyboardButton(text="❌ لغو",   callback_data="cancel_pbroadcast"),
        ]
    ])
 
    await message.answer(
        f"📋 <b>پیش‌نمایش (نمونه):</b>\n\n{preview}\n\n"
        f"🎯 مخاطبان: <b>{_build_filter_summary(filters)}</b>\n\n"
        "آیا ارسال شود؟",
        parse_mode="HTML",
        reply_markup=confirm_kb,
    )
 

async def _background_pbroadcast(users_raw_data: list, template: str):
    # --- فیکس باگ: ساخت یک کلاس سبک برای جایگزینی متغیرها به صورت درجا ---
    class _TempUser:
        def __init__(self, fname, city, coins, age):
            self.first_name = fname
            self.city = city
            self.coin_balance = coins
            self.age = age

    sent = 0
    failed = 0
    
    for tg_id, fname, city, coins, age in users_raw_data:
        mock_user = _TempUser(fname, city, coins, age)
        text = _personalize(template, mock_user)
        try:
            await bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
        
        await asyncio.sleep(0.04) 
        
    logger.info(f"PBroadcast completed. Sent: {sent}, Failed: {failed}")




@router.callback_query(PBroadcastStates.confirming, F.data == "pbroadcast_confirm")
async def pbroadcast_confirm(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    await call.answer()
    data = await state.get_data()
    filters: dict  = data.get("pb_filters", {})
    template: str  = data.get("pb_template", "")
    await state.clear()
 
    conditions = []
    if "gender" in filters:
        conditions.append(User.gender == filters["gender"])
    if "city" in filters:
        conditions.append(User.city == filters["city"])
    if "age_min" in filters:
        conditions.append(User.age >= filters["age_min"])
        conditions.append(User.age <= filters["age_max"])
    if filters.get("vip"):
        conditions.append(User.is_vip == True)
 
    stmt = select(User.tg_id, User.first_name, User.city, User.coin_balance, User.age)
    if conditions:
        stmt = stmt.where(and_(*conditions))
 
    await call.message.edit_text(
        "⏳ <b>در حال استخراج مخاطبین و ارسال در پس‌زمینه...</b>\n\n"
        "این فرآیند به صورت بهینه مدیریت می‌شود و شما می‌توانید به کار خود ادامه دهید.",
        parse_mode="HTML",
    )
    
    users_raw_data = []
    stream_result = await db_session.stream_tuples(stmt.execution_options(yield_per=1000))
    async for row in stream_result:
        users_raw_data.append(row)
        
    if not users_raw_data:
        await call.message.answer("⚠️ عملیات پایان یافت اما هیچ کاربری با این فیلترها در دیتابیس یافت نشد.")
        return
        
    
    asyncio.create_task(_background_pbroadcast(users_raw_data, template))

 
# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Daily Report
# ─────────────────────────────────────────────────────────────────────────────

 
async def build_daily_report(db_session: AsyncSession) -> str:

    now   = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
 
    
    new_users = await db_session.scalar(
        select(func.count(User.id)).where(User.created_at >= today)
    ) or 0
    new_users_yesterday = await db_session.scalar(
        select(func.count(User.id)).where(
            and_(User.created_at >= yesterday, User.created_at < today)
        )
    ) or 0
 
   
    new_matches = await db_session.scalar(
        select(func.count(MatchHistory.id)).where(MatchHistory.created_at >= today)
    ) or 0
 
    
    successful_chats = await db_session.scalar(
        select(func.count(MatchHistory.id)).where(
            and_(MatchHistory.created_at >= today, MatchHistory.chat_approved == True)
        )
    ) or 0
 
    
    total_matches = await db_session.scalar(select(func.count(MatchHistory.id))) or 0
    total_chats   = await db_session.scalar(
        select(func.count(MatchHistory.id)).where(MatchHistory.chat_approved == True)
    ) or 0
    conv_rate = round(total_chats / total_matches * 100, 1) if total_matches else 0.0
 
    
    total_users = await db_session.scalar(select(func.count(User.id))) or 0
    vip_users   = await db_session.scalar(
        select(func.count(User.id)).where(User.is_vip == True)
    ) or 0
 
    # ── آنلاین‌های فعال ───────────────────────────────────────────────────── #
    online_now = await db_session.scalar(
        select(func.count(User.id)).where(User.is_online == True)
    ) or 0
 
    # ── محاسبه رشد ───────────────────────────────────────────────────────── #
    growth_arrow = "📈" if new_users >= new_users_yesterday else "📉"
    growth_diff  = new_users - new_users_yesterday
 
    # ── رویدادهای فعال ───────────────────────────────────────────────────── #
    active_multiplier = await redis_client.get("bot:active_event_multiplier")

    events_line = (
        f"  • ضریب سکه فعال: <b>×{float(active_multiplier)}</b>" 
        if active_multiplier else "  هیچ رویداد فعالی نیست"
    )
 
    return (
        f"📊 <b>گزارش روزانه — {now.strftime('%Y/%m/%d')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>کاربران</b>\n"
        f"  کل: <b>{total_users:,}</b> | VIP: <b>{vip_users:,}</b>\n"
        f"  آنلاین الان: <b>{online_now:,}</b>\n"
        f"  ثبت‌نام امروز: <b>{new_users:,}</b> {growth_arrow} "
        f"({'+' if growth_diff >= 0 else ''}{growth_diff} نسبت به دیروز)\n\n"
        f"💞 <b>مچینگ</b>\n"
        f"  مچ امروز: <b>{new_matches:,}</b>\n"
        f"  چت موفق امروز: <b>{successful_chats:,}</b>\n"
        f"  نرخ تبدیل کلی: <b>{conv_rate}%</b> ({total_chats:,}/{total_matches:,})\n\n"
        f"🎉 <b>رویدادهای فعال</b>\n{events_line}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>گزارش بعدی فردا شب ارسال می‌شود.</i>"
    )
 
 
@router.message(Command("report"))
async def cmd_report(message: Message, db_session: AsyncSession) -> None:
    """گزارش فوری دستی."""
    text = await build_daily_report(db_session)
    await message.answer(text, parse_mode="HTML")
 
 
@router.message(Command("report_auto"))
async def cmd_report_auto(message: Message) -> None:
    """فعال/غیرفعال کردن گزارش خودکار برای این ادمین (ذخیره در ردیس)"""
    admin_id = message.from_user.id
    
    
    is_active = await redis_client.sismember("bot:auto_report_admins", str(admin_id))
 
    if is_active:
        await redis_client.srem("bot:auto_report_admins", str(admin_id))
        await message.answer("🔕 گزارش خودکار روزانه <b>غیرفعال</b> شد.", parse_mode="HTML")
    else:
        await redis_client.sadd("bot:auto_report_admins", str(admin_id))
        await message.answer(
            "🔔 گزارش خودکار روزانه <b>فعال</b> شد.\n"
            "هر شب ساعت ۲۳:۵۹ UTC گزارش برات ارسال میشه.",
            parse_mode="HTML",
        )

 
 
async def send_daily_reports(db_session: AsyncSession) -> None:
    """ارسال گزارش به لیست ادمین‌های ذخیره‌شده در ردیس"""
    admin_ids_bytes = await redis_client.smembers("bot:auto_report_admins")
    
    if not admin_ids_bytes:
        return
 
    try:
        report_text = await build_daily_report(db_session)
    except Exception as exc:
        logger.error("Failed to build daily report: %s", exc)
        return
 
    for b_id in admin_ids_bytes:
        admin_id = int(b_id.decode('utf-8'))
        try:
            await bot.send_message(chat_id=admin_id, text=report_text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Could not send daily report to admin %d: %s", admin_id, exc)
 
async def _daily_report_loop(session_factory) -> None:
    """
    Loop داخلی برای ارسال گزارش بدون نیاز به APScheduler.
    در main.py با asyncio.create_task صدا بزن.
 
    مثال در main.py:
        from matching_bot_project.bot.handlers.admin_extensions import _daily_report_loop
        asyncio.create_task(_daily_report_loop(async_session_factory))
    """
    while True:
        now = datetime.now(timezone.utc)
        
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
 
        logger.info("Daily report scheduled in %.0f seconds.", wait_seconds)
        await asyncio.sleep(wait_seconds)
 
        async with session_factory() as db_session:
            await send_daily_reports(db_session)

@router.message(Command("addsponsor"))
async def cmd_addsponsor(message: Message):
    """افزودن کانال اسپانسر جدید با اعتبارسنجی لینک و حضور ربات"""
    args = message.text.split()
    if len(args) != 3:
        return await message.answer(
            "⚠️ <b>راهنمای استفاده:</b>\n"
            "<code>/addsponsor [channel_id_or_username] [invite_link]</code>\n\n"
            "<i>مثال:</i>\n<code>/addsponsor -10012345678 https://t.me/joinchat/...</code>\n",
            parse_mode="HTML"
        )
    
    channel_id = args[1]
    invite_link = args[2]

    # --- فیکس باگ اول: اعتبارسنجی ساختار لینک ---
    if not invite_link.startswith(("http://", "https://")):
        return await message.answer("⚠️ <b>خطا:</b> لینک دعوت باید حتماً با http یا https شروع شود!", parse_mode="HTML")

    try:
        target_chat = int(channel_id)
    except ValueError:
        target_chat = channel_id

    try:
        bot_member = await bot.get_chat_member(chat_id=target_chat, user_id=bot.id)
        if bot_member.status not in ("administrator", "creator"):
            return await message.answer(
                "⚠️ <b>خطا:</b> ربات در این کانال ادمین نیست!\nابتدا ربات را در کانال ادمین کنید.",
                parse_mode="HTML"
            )
    except TelegramAPIError as e:
        logger.error(f"Failed to verify sponsor channel {target_chat}: {e}")
        return await message.answer(
            f"⚠️ <b>خطا در دسترسی به کانال:</b>\nآیدی کانال نامعتبر است یا ربات در آن عضو نیست.\nکد خطا: <code>{e}</code>",
            parse_mode="HTML"
        )

    await redis_client.hset("bot:sponsors", channel_id, invite_link)
    await redis_client.incr("bot:sponsors_version")

    await message.answer(
        f"✅ کانال <code>{channel_id}</code> با موفقیت تایید و به لیست اسپانسرها اضافه شد.\n"
        f"⚡️ کش عضویت تمام کاربران پاکسازی شد — دفعه بعد از ورود چک مجدد انجام میشه.",
        parse_mode="HTML"
    )


@router.message(Command("removesponsor"))
async def cmd_removesponsor(message: Message):
    """حذف کانال اسپانسر"""
    args = message.text.split()
    if len(args) != 2:
        return await message.answer(
            "⚠️ <b>راهنمای استفاده:</b>\n"
            "<code>/removesponsor [channel_id_or_username]</code>\n\n"
            "<i>مثال:</i>\n<code>/removesponsor -10012345678</code>",
            parse_mode="HTML"
        )
    
    channel_id = args[1]
    deleted = await redis_client.hdel("bot:sponsors", channel_id)
    
    if deleted:
        # ── Invalidate کش force_join ──
        await redis_client.incr("bot:sponsors_version")
        # ─────────────────────────────
        await message.answer(
            f"🗑 کانال <code>{channel_id}</code> از لیست اسپانسرها حذف شد.\n"
            f"⚡️ کش عضویت کاربران ریست شد.",
            parse_mode="HTML"
        )
    else:
        await message.answer("⚠️ این کانال در لیست اسپانسرها یافت نشد.", parse_mode="HTML")
        
@router.message(Command("sponsors"))
async def cmd_sponsors(message: Message):
    """مشاهده لیست اسپانسرها"""
    sponsors = await redis_client.hgetall("bot:sponsors")
    if not sponsors:
        return await message.answer("📭 لیست اسپانسرهای داینامیک خالی است.\n(ربات فقط از تنظیمات پیش‌فرض استفاده می‌کند)")
    
    text = "📢 <b>لیست کانال‌های اسپانسر:</b>\n\n"
    for i, (ch_id, link) in enumerate(sponsors.items(), 1):
        # اگر bytes بود decode کن، اگر str بود همونطور استفاده کن
        ch_id_str = ch_id.decode('utf-8') if isinstance(ch_id, bytes) else ch_id
        link_str  = link.decode('utf-8')  if isinstance(link, bytes)  else link
        text += f"<b>{i}.</b> شناسه: <code>{ch_id_str}</code>\nلینک: {link_str}\n\n"
        
    await message.answer(text, parse_mode="HTML")
 

# ══════════════════════════════════════════════════════════════
# سیستم افزودن دستی سوال به بانک سوالات (حداکثر ۸۰ سوال)
# ══════════════════════════════════════════════════════════════

MAX_QUESTIONS = 80

def _question_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="2️⃣ دو گزینه‌ای", callback_data="qtype:2"),
            InlineKeyboardButton(text="4️⃣ چهار گزینه‌ای", callback_data="qtype:4"),
        ],
        [InlineKeyboardButton(text="❌ لغو", callback_data="qtype:cancel")],
    ])


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ ذخیره", callback_data="qconfirm:save"),
            InlineKeyboardButton(text="🔄 دوباره", callback_data="qconfirm:redo"),
        ],
        [InlineKeyboardButton(text="❌ لغو کامل", callback_data="qconfirm:cancel")],
    ])


def _build_preview(data: dict) -> str:
    q_type = data.get("q_type", 2)
    lines = [
        "📋 <b>پیش‌نمایش سوال:</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"❓ <b>متن:</b> {data.get('q_text', '')}",
        f"🅰️ گزینه الف: {data.get('opt_a', '')}",
        f"🅱️ گزینه ب: {data.get('opt_b', '')}",
    ]
    if q_type == 4:
        lines.append(f"🅲 گزینه ج: {data.get('opt_c', '')}")
        lines.append(f"🅳 گزینه د: {data.get('opt_d', '')}")
    lines.append(f"🏷 دسته‌بندی: {data.get('category', '')}")
    return "\n".join(lines)


@router.message(Command("addquestion"))
async def cmd_addquestion(message: Message, state: FSMContext, db_session: AsyncSession):
    """شروع فرآیند افزودن سوال جدید"""
    count = await crud.get_question_count(db_session)
    if count >= MAX_QUESTIONS:
        return await message.answer(
            f"⚠️ بانک سوالات پر است ({count}/{MAX_QUESTIONS}).\n"
            "برای افزودن سوال جدید ابتدا یک سوال قدیمی را حذف کنید."
        )

    await state.set_state(QuestionAddStates.choosing_type)
    await message.answer(
        f"➕ <b>افزودن سوال جدید</b>\n"
        f"📊 ظرفیت: <b>{count}/{MAX_QUESTIONS}</b>\n\n"
        "نوع سوال را انتخاب کنید:",
        reply_markup=_question_type_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(QuestionAddStates.choosing_type, F.data.startswith("qtype:"))
async def handle_question_type(call: CallbackQuery, state: FSMContext):
    action = call.data.split(":")[1]

    if action == "cancel":
        await state.clear()
        await call.message.edit_text("❌ عملیات لغو شد.")
        return await call.answer()

    q_type = int(action)  # 2 یا 4
    await state.update_data(q_type=q_type)
    await state.set_state(QuestionAddStates.entering_text)

    await call.message.edit_text(
        f"{'2️⃣' if q_type == 2 else '4️⃣'} سوال <b>{'دو' if q_type == 2 else 'چهار'} گزینه‌ای</b> انتخاب شد.\n\n"
        "✍️ متن سوال را بنویسید:",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(QuestionAddStates.entering_text)
async def handle_question_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        return await message.answer("⚠️ متن سوال نمی‌تواند خالی باشد. دوباره بنویسید:")
    if len(text) > 300:
        return await message.answer("⚠️ متن سوال حداکثر ۳۰۰ کاراکتر می‌تواند باشد.")

    await state.update_data(q_text=text)
    await state.set_state(QuestionAddStates.entering_option_a)
    await message.answer("🅰️ <b>گزینه الف</b> را بنویسید:", parse_mode="HTML")


@router.message(QuestionAddStates.entering_option_a)
async def handle_option_a(message: Message, state: FSMContext):
    opt = (message.text or "").strip()
    if not opt:
        return await message.answer("⚠️ گزینه نمی‌تواند خالی باشد.")
    await state.update_data(opt_a=opt)
    await state.set_state(QuestionAddStates.entering_option_b)
    await message.answer("🅱️ <b>گزینه ب</b> را بنویسید:", parse_mode="HTML")


@router.message(QuestionAddStates.entering_option_b)
async def handle_option_b(message: Message, state: FSMContext):
    opt = (message.text or "").strip()
    if not opt:
        return await message.answer("⚠️ گزینه نمی‌تواند خالی باشد.")

    await state.update_data(opt_b=opt)
    data = await state.get_data()

    if data["q_type"] == 4:
        await state.set_state(QuestionAddStates.entering_option_c)
        await message.answer("🅲 <b>گزینه ج</b> را بنویسید:", parse_mode="HTML")
    else:
        # ۲ گزینه‌ای — مستقیم به دسته‌بندی
        await state.set_state(QuestionAddStates.entering_category)
        await message.answer("🏷 <b>دسته‌بندی</b> سوال را بنویسید (مثال: عاطفی، مالی، تفریحات):", parse_mode="HTML")


@router.message(QuestionAddStates.entering_option_c)
async def handle_option_c(message: Message, state: FSMContext):
    opt = (message.text or "").strip()
    if not opt:
        return await message.answer("⚠️ گزینه نمی‌تواند خالی باشد.")
    await state.update_data(opt_c=opt)
    await state.set_state(QuestionAddStates.entering_option_d)
    await message.answer("🅳 <b>گزینه د</b> را بنویسید:", parse_mode="HTML")


@router.message(QuestionAddStates.entering_option_d)
async def handle_option_d(message: Message, state: FSMContext):
    opt = (message.text or "").strip()
    if not opt:
        return await message.answer("⚠️ گزینه نمی‌تواند خالی باشد.")
    await state.update_data(opt_d=opt)
    await state.set_state(QuestionAddStates.entering_category)
    await message.answer("🏷 <b>دسته‌بندی</b> سوال را بنویسید (مثال: عاطفی، مالی، تفریحات):", parse_mode="HTML")


@router.message(QuestionAddStates.entering_category)
async def handle_category(message: Message, state: FSMContext):
    cat = (message.text or "").strip()
    if not cat:
        return await message.answer("⚠️ دسته‌بندی نمی‌تواند خالی باشد.")

    await state.update_data(category=cat)
    await state.set_state(QuestionAddStates.confirming)

    data = await state.get_data()
    preview = _build_preview(data)
    await message.answer(
        f"{preview}\n\n━━━━━━━━━━━━━━━━━━━━\nذخیره شود؟",
        reply_markup=_confirm_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(QuestionAddStates.confirming, F.data.startswith("qconfirm:"))
async def handle_question_confirm(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    action = call.data.split(":")[1]

    if action == "cancel":
        await state.clear()
        await call.message.edit_text("❌ عملیات لغو شد.")
        return await call.answer()

    if action == "redo":
        # برگشت به ابتدا — نوع سوال را نگه می‌داریم
        data = await state.get_data()
        q_type = data.get("q_type", 2)
        await state.set_data({"q_type": q_type})
        await state.set_state(QuestionAddStates.entering_text)
        await call.message.edit_text(
            "🔄 از ابتدا شروع می‌کنیم.\n\n✍️ متن سوال را بنویسید:"
        )
        return await call.answer()

    # action == "save"
    data = await state.get_data()
    count = await crud.get_question_count(db_session)
    if count >= MAX_QUESTIONS:
        await state.clear()
        await call.message.edit_text(
            f"⚠️ بانک سوالات در این لحظه پر شد ({count}/{MAX_QUESTIONS}). سوال ذخیره نشد."
        )
        return await call.answer()

    q = await crud.add_question(
        session=db_session,
        question_text=data["q_text"],
        option_a=data["opt_a"],
        option_b=data["opt_b"],
        option_c=data.get("opt_c"),
        option_d=data.get("opt_d"),
        category=data["category"],
    )
    await db_session.commit()
    await state.clear()

    q_type_label = "دو گزینه‌ای" if data["q_type"] == 2 else "چهار گزینه‌ای"
    await call.message.edit_text(
        f"✅ سوال <b>{q_type_label}</b> با شناسه <code>{q.id}</code> ذخیره شد.\n"
        f"📊 بانک سوالات: <b>{count + 1}/{MAX_QUESTIONS}</b>\n\n"
        "برای افزودن سوال بعدی دوباره /addquestion بزنید.",
        parse_mode="HTML",
    )
    await call.answer()


# ══════════════════════════════════════════════════════════════
# آپلود فایل Excel سوالات — bulk import
# ══════════════════════════════════════════════════════════════

import io
import openpyxl


def _parse_question_excel(file_bytes: bytes) -> tuple[list[dict], list[str]]:
    """
    پارس کردن فایل Excel سوالات.
    برمی‌گردونه: (لیست سوالات معتبر، لیست خطاها)

    ساختار هر سوال:
        { q_text, opt_a, opt_b, opt_c|None, opt_d|None, category, row_num }
    """
    questions: list[dict] = []
    errors: list[str] = []

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active
    except Exception as e:
        return [], [f"❌ فایل قابل خواندن نیست: {e}"]

    # ردیف ۱ هدر، ردیف ۲ راهنما — از ردیف ۳ شروع می‌کنیم
    for row_idx, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
        # سطرهای خالی رو skip می‌کنیم
        if not any(row):
            continue

        def cell(i):
            v = row[i] if i < len(row) else None
            return str(v).strip() if v is not None and str(v).strip() else None

        q_text   = cell(0)
        opt_a    = cell(1)
        opt_b    = cell(2)
        opt_c    = cell(3)
        opt_d    = cell(4)
        category = cell(5)

        # اعتبارسنجی
        row_errors = []
        if not q_text:
            row_errors.append("متن سوال خالیه")
        elif len(q_text) > 300:
            row_errors.append(f"متن سوال بیشتر از ۳۰۰ کاراکتره ({len(q_text)})")

        if not opt_a:
            row_errors.append("گزینه الف خالیه")
        if not opt_b:
            row_errors.append("گزینه ب خالیه")

        # اگه ج داره، د هم باید داشته باشه
        if opt_c and not opt_d:
            row_errors.append("گزینه ج دارد ولی گزینه د ندارد")
        if opt_d and not opt_c:
            row_errors.append("گزینه د دارد ولی گزینه ج ندارد")

        if not category:
            row_errors.append("دسته‌بندی خالیه")

        if row_errors:
            errors.append(f"ردیف {row_idx}: {' | '.join(row_errors)}")
            continue

        questions.append({
            "q_text":   q_text,
            "opt_a":    opt_a,
            "opt_b":    opt_b,
            "opt_c":    opt_c,
            "opt_d":    opt_d,
            "category": category,
            "row_num":  row_idx,
        })

    return questions, errors


def _bulk_import_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ بله، همه رو ذخیره کن", callback_data="qbulk:confirm"),
            InlineKeyboardButton(text="❌ لغو", callback_data="qbulk:cancel"),
        ]
    ])


@router.message(Command("importquestions"), IsAdminFilter())
async def cmd_importquestions(message: Message, state: FSMContext, db_session: AsyncSession):
    """
    دانلود تمپلیت یا راهنمای آپلود.
    ادمین بعد از دیدن این پیام، فایل Excel رو آپلود می‌کنه.
    """
    count = await crud.get_question_count(db_session)

    capacity = MAX_QUESTIONS - count
    if capacity <= 0:
        return await message.answer(
            f"⚠️ بانک سوالات پر است ({count}/{MAX_QUESTIONS}).\n"
            "برای import جدید ابتدا سوالات قدیمی را حذف کنید."
        )

    await state.set_state(QuestionAddStates.waiting_for_excel)
    await message.answer(
        f"📥 <b>آپلود فایل سوالات</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 ظرفیت خالی: <b>{capacity}/{MAX_QUESTIONS}</b>\n\n"
        f"فایل Excel (<code>.xlsx</code>) سوالات را ارسال کنید.\n\n"
        f"💡 <i>فرمت فایل: ستون‌ها به ترتیب: متن سوال، گزینه الف، گزینه ب، گزینه ج (اختیاری)، گزینه د (اختیاری)، دسته‌بندی</i>\n\n"
        f"برای دریافت فایل نمونه: /question_template\n"
        f"برای لغو: /cancel",
        parse_mode="HTML",
    )


@router.message(Command("question_template"), IsAdminFilter())
async def cmd_question_template(message: Message):
    """ارسال فایل تمپلیت Excel برای ادمین"""
    template_path = Path("json_files/question_bank_template.xlsx")
    if not template_path.exists():
        template_path = Path("/app/json_files/question_bank_template.xlsx")

    if not template_path.exists():
        return await message.answer("⚠️ فایل تمپلیت یافت نشد. با توسعه‌دهنده تماس بگیرید.")

    from aiogram.types import FSInputFile
    file = FSInputFile(str(template_path), filename="question_bank_template.xlsx")
    await message.answer_document(
        document=file,
        caption=(
            "📋 <b>فایل تمپلیت سوالات</b>\n\n"
            "این فایل را پر کنید و با /importquestions آپلود کنید.\n"
            "راهنمای کامل در sheet دوم فایل موجوده."
        ),
        parse_mode="HTML",
    )


@router.message(QuestionAddStates.waiting_for_excel, F.document)
async def handle_question_excel_upload(
    message: Message, state: FSMContext, db_session: AsyncSession
):
    """دریافت فایل Excel و نمایش پیش‌نمایش قبل از ذخیره"""
    doc = message.document

    # چک فرمت
    if not (doc.file_name or "").lower().endswith(".xlsx"):
        return await message.answer(
            "⚠️ لطفاً فقط فایل Excel با پسوند <code>.xlsx</code> ارسال کنید.",
            parse_mode="HTML",
        )

    # چک حجم (حداکثر ۵ مگ)
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        return await message.answer("⚠️ حجم فایل بیش از ۵ مگابایت است.")

    await message.answer("⏳ در حال پردازش فایل...")

    # دانلود فایل
    try:
        file_info = await bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await bot.download_file(file_info.file_path, destination=buf)
        file_bytes = buf.getvalue()
    except Exception as e:
        logger.error(f"Failed to download question Excel: {e}")
        return await message.answer("❌ دانلود فایل ناموفق بود. دوباره تلاش کنید.")

    # پارس کردن
    questions, errors = _parse_question_excel(file_bytes)

    # نمایش خطاها
    error_text = ""
    if errors:
        error_lines = "\n".join(errors[:10])  # حداکثر ۱۰ خطا نشون بده
        extra = f"\n... و {len(errors) - 10} خطای دیگر" if len(errors) > 10 else ""
        error_text = f"\n\n⚠️ <b>خطاهای یافت‌شده ({len(errors)} ردیف نادیده گرفته شد):</b>\n<code>{error_lines}{extra}</code>"

    if not questions:
        await state.clear()
        return await message.answer(
            f"❌ هیچ سوال معتبری در فایل یافت نشد.{error_text}",
            parse_mode="HTML",
        )

    # چک ظرفیت
    current_count = await crud.get_question_count(db_session)
    capacity = MAX_QUESTIONS - current_count
    importable = questions[:capacity]
    skipped_capacity = len(questions) - len(importable)

    # ذخیره‌ی موقت در FSM state
    await state.update_data(pending_questions=importable)

    # ساخت پیش‌نمایش
    two_opt  = sum(1 for q in importable if not q["opt_c"])
    four_opt = sum(1 for q in importable if q["opt_c"])
    cats     = list(dict.fromkeys(q["category"] for q in importable))  # unique با حفظ ترتیب

    preview_samples = importable[:3]
    sample_lines = []
    for i, q in enumerate(preview_samples, 1):
        q_type = "۴ گزینه‌ای" if q["opt_c"] else "۲ گزینه‌ای"
        sample_lines.append(
            f"<b>{i}. [{q_type}]</b> {q['q_text'][:60]}{'...' if len(q['q_text']) > 60 else ''}\n"
            f"   الف: {q['opt_a']} | ب: {q['opt_b']}"
            + (f" | ج: {q['opt_c']} | د: {q['opt_d']}" if q["opt_c"] else "")
        )

    cap_warn = f"\n⚠️ <i>ظرفیت محدود است — {skipped_capacity} سوال آخر نادیده گرفته می‌شن.</i>" if skipped_capacity else ""

    preview_text = (
        f"📊 <b>پیش‌نمایش import</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ سوالات معتبر: <b>{len(importable)}</b>\n"
        f"2️⃣ دو گزینه‌ای: <b>{two_opt}</b>  |  4️⃣ چهار گزینه‌ای: <b>{four_opt}</b>\n"
        f"🏷 دسته‌بندی‌ها: <i>{', '.join(cats[:5])}{'...' if len(cats) > 5 else ''}</i>\n"
        f"{cap_warn}"
        f"{error_text}\n\n"
        f"<b>نمونه ۳ سوال اول:</b>\n"
        f"{'─' * 20}\n"
        + "\n\n".join(sample_lines)
        + f"\n{'─' * 20}\n\n"
        f"آیا این {len(importable)} سوال ذخیره شوند؟"
    )

    await state.set_state(QuestionAddStates.confirming_bulk)
    await message.answer(
        preview_text,
        reply_markup=_bulk_import_confirm_keyboard(),
        parse_mode="HTML",
    )


@router.message(QuestionAddStates.waiting_for_excel)
async def handle_excel_invalid_input(message: Message):
    """اگه چیزی غیر از فایل فرستاد"""
    if message.text and message.text.strip() in ("/cancel", "لغو"):
        return  # cancel handler جداست
    await message.answer(
        "⚠️ لطفاً فقط فایل Excel (<code>.xlsx</code>) ارسال کنید.\n"
        "برای لغو: /cancel",
        parse_mode="HTML",
    )


@router.callback_query(QuestionAddStates.confirming_bulk, F.data == "qbulk:cancel")
async def bulk_import_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ عملیات import لغو شد.")
    await call.answer()


@router.callback_query(QuestionAddStates.confirming_bulk, F.data == "qbulk:confirm")
async def bulk_import_confirm(
    call: CallbackQuery, state: FSMContext, db_session: AsyncSession
):
    """bulk insert همه سوالات تایید شده"""
    data = await state.get_data()
    questions = data.get("pending_questions", [])

    if not questions:
        await state.clear()
        await call.message.edit_text("⚠️ سوالی برای ذخیره یافت نشد.")
        return await call.answer()

    await call.message.edit_text("⏳ در حال ذخیره‌سازی...")

    # چک مجدد ظرفیت (ممکنه در فاصله تایید تغییر کرده باشه)
    current_count = await crud.get_question_count(db_session)
    capacity = MAX_QUESTIONS - current_count
    to_insert = questions[:capacity]

    saved = 0
    failed = 0
    for q in to_insert:
        try:
            await crud.add_question(
                session=db_session,
                question_text=q["q_text"],
                option_a=q["opt_a"],
                option_b=q["opt_b"],
                option_c=q.get("opt_c"),
                option_d=q.get("opt_d"),
                category=q["category"],
            )
            saved += 1
        except Exception as e:
            logger.error(f"Failed to insert question row {q['row_num']}: {e}")
            failed += 1

    await db_session.commit()
    await state.clear()

    final_count = current_count + saved
    result_text = (
        f"✅ <b>import با موفقیت انجام شد!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💾 ذخیره‌شده: <b>{saved}</b> سوال\n"
    )
    if failed:
        result_text += f"⚠️ خطا در ذخیره: <b>{failed}</b> سوال\n"
    if len(questions) > len(to_insert):
        result_text += f"⚠️ به دلیل ظرفیت، {len(questions) - len(to_insert)} سوال نادیده گرفته شد\n"

    result_text += f"📊 بانک سوالات: <b>{final_count}/{MAX_QUESTIONS}</b>"

    await call.message.edit_text(result_text, parse_mode="HTML")
    await call.answer()