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
from matching_bot_project.bot.states.states import AdminStates, EventStates, PBroadcastStates
logger = logging.getLogger(__name__)

router = Router()
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


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
    user.vip_expires_at = datetime.now(timezone.utc) + timedelta(days=days)
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
    try:
        target_tg_id = int(call.data.split("_")[2])
    except (IndexError, ValueError):
        return await call.answer("⚠️ دیتای کالبک دکمه نامعتبر است!", show_alert=True)
    
    # جلوگیری از بن کردن خودت با استفاده از آیدی ادمین کلیک‌کننده
    if target_tg_id == call.from_user.id:
        return await call.answer("⚠️ شما نمی‌توانید خودتان را مسدود کنید!", show_alert=True)

    user = await get_user_by_tg_id(db_session, target_tg_id)
    if not user:
        return await call.answer("⚠️ این کاربر یافت نشد!", show_alert=True)

    # ۱. متوقف کردن فوری لودینگ تلگرام
    await call.answer("کاربر مسدود شد.")

    user.is_banned = True
    await db_session.commit()
    
    # ارسال پیام به کاربر
    user_notification = "❌ <b>حساب کاربری شما به دلیل نقض قوانین مسدود (Ban) شد.</b>"
    try:
        await bot.send_message(chat_id=target_tg_id, text=user_notification, parse_mode="HTML")
        ban_msg_status = "✅ پیام اخطار به کاربر تحویل داده شد."
    except Exception:
        ban_msg_status = "⚠️ کاربر ربات را بلاک کرده است."

    unban_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 رفع مسدودیت (Unban)", callback_data=f"admin_unban_{target_tg_id}")]
    ])
    
    await call.message.edit_text(
        text=(
            "📩 <b>وضعیت پیام پشتیبانی تغییر یافت:</b>\n\n"
            f"👤 شناسه کاربر متخلف: <code>{target_tg_id}</code>\n"
            "──────────────────────────────\n"
            "⛔️ <b>وضعیت: این کاربر مسدود شد.</b>\n"
            f"ℹ️ <i>{ban_msg_status}</i>"
        ),
        parse_mode="HTML",
        reply_markup=unban_kb
    )


@router.callback_query(F.data.startswith("admin_unban_"))
async def admin_quick_unban(call: CallbackQuery, db_session: AsyncSession):
    try:
        target_tg_id = int(call.data.split("_")[2])
    except (IndexError, ValueError):
        return await call.answer("⚠️ دیتای کالبک دکمه نامعتبر است!", show_alert=True)
        
    # ۱. متوقف کردن فوری لودینگ تلگرام
    await call.answer("کاربر رفع مسدودیت شد.")
    
    user = await get_user_by_tg_id(db_session, target_tg_id)
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

    # 🛠️ اصلاح باگ: کاراکتر آندرسکور (_) اصلاح شد تا دکمه مجدداً کار کند
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
    target_tg_id = int(call.data.split("_")[2])
    
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

# ─── ۶. گرفتن متن پاسخ از ادمین و ارسال به کاربر ───
@router.message(AdminStates.waiting_for_support_reply)
async def admin_send_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    target_tg_id = data.get("reply_target_id")
    
    reply_text = f"👨‍💻 <b>پیام جدید از پشتیبانی ربات:</b>\n\n{message.html_text}"

    try:
        await bot.send_message(chat_id=target_tg_id, text=reply_text, parse_mode="HTML")
        await message.answer("✅ پاسخ شما با موفقیت به کاربر تحویل داده شد.")
    except TelegramForbiddenError:
        await message.answer("⛔️ خطا: کاربر ربات را بلاک کرده است و پیام شما تحویل داده نشد.")
    except TelegramAPIError as e:
        if "chat not found" in str(e).lower() or "user is deactivated" in str(e).lower():
            await message.answer("💀 خطا: کاربر حساب تلگرام خود را دلیت‌اکانت کرده است.")
        else:
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
    except Exception as e:
        logger.debug(f"Pagination edit error: {e}")
        
    await call.answer()

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
    """دریافت متن از ادمین و آغاز ارسال پیام در پس‌زمینه"""
    # استخراج متن پیام با حفظ فرمت‌های HTML
    broadcast_text = message.html_text if message.text else message.caption
    
    if not broadcast_text:
         return await message.answer("⚠️ لطفاً یک پیام متنی (Text) ارسال کنید.")

    # کوئری بهینه برای دریافت فقط آیدی کاربران (بدون لود کردن کل آبجکت User)
    result = await db_session.execute(select(User.tg_id))
    user_ids = result.scalars().all()

    if not user_ids:
        await state.clear()
        return await message.answer("هیچ کاربری در دیتابیس یافت نشد.")

    # استفاده از ورکر موجود برای ارسال همگانی در پس‌زمینه
    worker = BroadcastWorker(bot=bot)
    worker.start_background_broadcast(
        user_ids=list(user_ids), 
        text=broadcast_text, 
        delay_ms=40  # تاخیر ۴۰ میلی‌ثانیه برای جلوگیری از لیمیت شدن توسط تلگرام
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
 
    ends_at = datetime.now(timezone.utc) + timedelta(minutes=data["event_duration_minutes"])
 
    event = ActiveEvent(
        event_id=EventStore.next_id(),
        name=data["event_name"],
        description=data["event_description"],
        coin_multiplier=data["event_multiplier"],
        ends_at=ends_at,
    )
    EventStore.add(event)
 
    await call.message.edit_text(
        f"✅ رویداد با موفقیت ایجاد شد!\n\n{event.to_text()}",
        parse_mode="HTML",
    )
 
    # اطلاع‌رسانی به همه یوزرها
    result = await db_session.execute(select(User.tg_id))
    user_ids = result.scalars().all()
 
    notification_text = (
        f"🎉 <b>رویداد ویژه شروع شد!</b>\n\n"
        f"<b>{event.name}</b>\n"
        f"{event.description}\n\n"
        f"💰 تا <b>{data['event_duration_minutes']} دقیقه</b> دیگر سکه‌هات ×{event.coin_multiplier} میشن!\n"
        "همین الان وارد ربات شو 👇"
    )
 
    worker = BroadcastWorker(bot=bot)
    worker.start_background_broadcast(user_ids=list(user_ids), text=notification_text, delay_ms=40)
 
    logger.info("Event '%s' created, notifying %d users.", event.name, len(user_ids))
 
 
@router.callback_query(F.data == "cancel_event")
async def event_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("لغو شد.")
    await state.clear()
    await call.message.edit_text("❌ ساخت رویداد لغو شد.")
 
 
@router.message(Command("event_list"))
async def cmd_event_list(message: Message) -> None:
    actives = EventStore.all_active()
    if not actives:
        return await message.answer("هیچ رویداد فعالی وجود ندارد.")
 
    text = "📅 <b>رویدادهای فعال:</b>\n\n" + "\n\n".join(e.to_text() for e in actives)
    await message.answer(text, parse_mode="HTML")
 
 
@router.message(Command("event_end"))
async def cmd_event_end(message: Message) -> None:
    args = message.text.split()
    if len(args) != 2:
        return await message.answer("Usage: /event_end <event_id>")
 
    try:
        event_id = int(args[1])
    except ValueError:
        return await message.answer("⚠️ event_id باید عدد باشد.")
 
    if EventStore.remove(event_id):
        await message.answer(f"✅ رویداد {event_id} پایان یافت.")
    else:
        await message.answer(f"⚠️ رویداد {event_id} یافت نشد.")
 
 
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
 
 
@router.callback_query(PBroadcastStates.confirming, F.data == "pbroadcast_confirm")
async def pbroadcast_confirm(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    await call.answer()
    data = await state.get_data()
    filters: dict  = data.get("pb_filters", {})
    template: str  = data.get("pb_template", "")
    await state.clear()
 
    users = await _fetch_filtered_users(db_session, filters)
    if not users:
        return await call.message.edit_text("⚠️ هیچ کاربری با این فیلتر یافت نشد.")
 
    await call.message.edit_text(
        f"⏳ در حال ارسال به <b>{len(users)}</b> کاربر...",
        parse_mode="HTML",
    )
 
    # ارسال شخصی‌سازی‌شده در background
    async def _send_personalized() -> None:
        sent = failed = 0
        for user in users:
            text = _personalize(template, user)
            try:
                await bot.send_message(chat_id=user.tg_id, text=text, parse_mode="HTML")
                sent += 1
            except (TelegramForbiddenError, TelegramAPIError) as exc:
                logger.debug("pbroadcast skip user %d: %s", user.tg_id, exc)
                failed += 1
            await asyncio.sleep(0.04)  # 40ms = ~25 msg/s (زیر لیمیت تلگرام)
 
        try:
            await call.message.answer(
                f"✅ <b>ارسال تمام شد</b>\n\n"
                f"موفق: <b>{sent}</b> | ناموفق: <b>{failed}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass
 
    asyncio.create_task(_send_personalized())
    logger.info("Personalized broadcast started for %d users with filters %s.", len(users), filters)
 
 
@router.callback_query(F.data == "cancel_pbroadcast")
async def pbroadcast_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("لغو شد.")
    await state.clear()
    await call.message.edit_text("❌ ارسال لغو شد.")
 
 
# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Daily Report
# ─────────────────────────────────────────────────────────────────────────────
 
# لیست ادمین‌هایی که گزارش روزانه میگیرن
# در پروداکشن از config یا DB بخون
_AUTO_REPORT_ADMIN_IDS: set[int] = set()
 
 
async def build_daily_report(db_session: AsyncSession) -> str:
    """
    ساخت متن گزارش روزانه.
    تمام کوئری‌ها برای بازه ۲۴ ساعت گذشته هستن.
    """
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
    active_events = EventStore.all_active()
    events_line = (
        "\n".join(f"  • {e.name} (×{e.coin_multiplier}، {e.remaining_minutes} دقیقه)" for e in active_events)
        if active_events else "  هیچ رویداد فعالی نیست"
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
    """فعال/غیرفعال کردن گزارش خودکار برای این ادمین."""
    admin_id = message.from_user.id
 
    if admin_id in _AUTO_REPORT_ADMIN_IDS:
        _AUTO_REPORT_ADMIN_IDS.discard(admin_id)
        await message.answer("🔕 گزارش خودکار روزانه <b>غیرفعال</b> شد.", parse_mode="HTML")
    else:
        _AUTO_REPORT_ADMIN_IDS.add(admin_id)
        await message.answer(
            "🔔 گزارش خودکار روزانه <b>فعال</b> شد.\n"
            "هر شب ساعت ۲۳:۵۹ UTC گزارش برات ارسال میشه.",
            parse_mode="HTML",
        )
 
 
async def send_daily_reports(db_session: AsyncSession) -> None:
    """
    این تابع رو از scheduler صدا بزن — هر شب یه بار.
 
    مثال با APScheduler:
        scheduler.add_job(
            send_daily_reports,
            trigger=CronTrigger(hour=23, minute=59),
            args=[db_session_factory()],
        )
 
    یا با aiogram-الهام‌گرفته asyncio loop:
        asyncio.create_task(_daily_report_loop(session_factory))
    """
    if not _AUTO_REPORT_ADMIN_IDS:
        return
 
    try:
        report_text = await build_daily_report(db_session)
    except Exception as exc:
        logger.error("Failed to build daily report: %s", exc)
        return
 
    for admin_id in list(_AUTO_REPORT_ADMIN_IDS):
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
    """افزودن کانال اسپانسر جدید"""
    args = message.text.split()
    if len(args) != 3:
        return await message.answer(
            "⚠️ <b>راهنمای استفاده:</b>\n"
            "<code>/addsponsor [channel_id_or_username] [invite_link]</code>\n\n"
            "<i>مثال:</i>\n<code>/addsponsor -10012345678 https://t.me/joinchat/...</code>\n"
            "یا\n<code>/addsponsor @MyChannel https://t.me/MyChannel</code>",
            parse_mode="HTML"
        )
    
    channel_id = args[1]
    invite_link = args[2]
    
    await redis_client.hset("bot:sponsors", channel_id, invite_link)
    await message.answer(f"✅ کانال <code>{channel_id}</code> با موفقیت به لیست اسپانسرهای اجباری اضافه شد.", parse_mode="HTML")

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
        await message.answer(f"🗑 کانال <code>{channel_id}</code> از لیست اسپانسرها حذف شد.", parse_mode="HTML")
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
        text += f"<b>{i}.</b> شناسه: <code>{ch_id.decode('utf-8')}</code>\nلینک: {link.decode('utf-8')}\n\n"
        
    await message.answer(text, parse_mode="HTML")
 