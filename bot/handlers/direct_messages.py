"""
bot/handlers/direct_messages.py

v3 NEW: Direct message handler with privacy layer.

Privacy rules:
- When user A sends a direct message to user B, B gets a notification.
- BUT if B is currently in an active chat/date with someone else, the notification
  is queued (not pushed) — B will only see it when their chat/date ends.
- When user A views user B's profile via tag-tap while A is in active chat/date
  with C, C must NOT see any sign of it. This is handled by DirectMessagePrivacyMiddleware
  which annotates `data["is_in_active_chat"]` and `data["active_chat_partner_id"]`.

Routes:
- Inline "req_direct_{target_tg_id}" → enter typing state for direct message
- Text in DirectMessageStates.typing_message → save & notify recipient
- Inline "dm_view_{msg_id}"              → view a received DM
- Inline "dm_reply_{msg_id}_{sender_id}" → reply to a DM
- Inline "dm_delete_{msg_id}"            → delete a DM
"""

import logging
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, and_, select
from matching_bot_project.database.queries import crud
from matching_bot_project.database.models.models import BlockList
from matching_bot_project.bot.keyboards.reply import get_cancel_keyboard, get_main_menu_keyboard
from matching_bot_project.bot.core.constants import ReplyBtn
from matching_bot_project.bot.handlers.anonymous_chat import apply_security_filters
from matching_bot_project.bot.core.constants import Messages as SystemMsg
from matching_bot_project.bot.core.loader import bot
from matching_bot_project.bot.states.states import DirectMessageStates
from matching_bot_project.bot.keyboards.inline import get_dm_inbox_keyboard, get_dm_message_keyboard
from matching_bot_project.bot.middlewares.direct_message_privacy import (
    is_user_in_active_chat, get_active_chat_partner,
)
from matching_bot_project.database.models.models import DirectMessage, User

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith("req_direct_"))
async def req_direct_message(call: CallbackQuery, state: FSMContext, db_session):
    try:
        target_tg_id = int(call.data.replace("req_direct_", ""))
    except ValueError:
        return await call.answer("گیرنده نامعتبر.", show_alert=True)

    caller_id = call.from_user.id
    
    active_match = await crud.get_active_match(db_session, caller_id)
    if active_match:
        return await call.answer("⚠️ شما در حال حاضر در یک چت/دیت فعال هستید و نمی‌توانید پیام دایرکت بفرستید.", show_alert=True)
    caller = await crud.get_user_by_tg_id(db_session, caller_id)
    if not caller:
        return await call.answer("❌ حساب کاربری شما یافت نشد.", show_alert=True)

    # 🛡️ رفع باگ Timezone (حفظ حالت timezone.utc)
    now_utc = datetime.now(timezone.utc)
    
    silent = caller.silent_until
    if silent:
        if silent.tzinfo is None:
            silent = silent.replace(tzinfo=timezone.utc)
        if silent > now_utc:
            return await call.answer("🔕 شما در حالت سایلنت هستید!", show_alert=True)

    block_check = await db_session.execute(
        select(BlockList).where(
            or_(
                and_(BlockList.blocker_id == target_tg_id, BlockList.blocked_id == caller_id),
                and_(BlockList.blocker_id == caller_id, BlockList.blocked_id == target_tg_id)
            )
        )
    )
    if block_check.scalar_one_or_none():
        return await call.answer("🚫 امکان ارسال دایرکت به این کاربر وجود ندارد (مسدود شده).", show_alert=True)

    # 🛡️ مدیریت ایمن Timezone برای VIP
    expires = caller.vip_expires_at
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    is_vip = caller.is_vip and (not expires or expires > now_utc)
    
    if not is_vip:
        success = await crud.process_coin_transaction(db_session, caller, -1, "رزرو هزینه ارسال پیام دایرکت")
        if not success:
            return await call.answer("❌ سکه‌های شما برای ارسال پیام کافی نیست!", show_alert=True)
        await db_session.commit()

    current_state = await state.get_state()
    await state.update_data(
        dm_recipient_tg_id=target_tg_id,
        __prev_dm_state__=current_state,
        is_vip_free=is_vip
    )
    await state.set_state(DirectMessageStates.typing_message)

    cost_text = "هزینه ارسال رایگان (ویژه VIP)." if is_vip else "هزینه ۱ سکه کسر شد. در صورت لغو، سکه بازگردانده می‌شود."
    
    await call.message.answer(
        f"📨 <b>ارسال پیام دایرکت</b>\n\n"
        f"پیام خود را بنویسید (حداکثر ۱۰۰۰ کاراکتر):\n"
        f"{cost_text}\n\n"
        f"<i>نکته: اگر گیرنده در حال حاضر در چت یا دیت باشد، پیام شما در صف قرار می‌گیرد.</i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

@router.message(DirectMessageStates.typing_message, F.text)
async def send_direct_message(message: Message, state: FSMContext, db_session: AsyncSession):
    data = await state.get_data()
    recipient_tg_id = data.get("dm_recipient_tg_id")
    prev_state = data.get("__prev_dm_state__")
    caller_id = message.from_user.id

    if message.text == ReplyBtn.CANCEL:
        caller = await crud.get_user_by_tg_id(db_session, caller_id)
        is_vip_free = data.get("is_vip_free", False)
        
        if caller and recipient_tg_id and not is_vip_free:
            await crud.process_coin_transaction(db_session, caller, 1, "برگشت هزینه رزرو دایرکت (لغو)")
            await db_session.commit()
            
        await state.set_data({k: v for k, v in data.items() if k not in ["dm_recipient_tg_id", "__prev_dm_state__", "reply_to_msg_id", "is_vip_free"]})
        if prev_state:
            await state.set_state(prev_state)
        else:
            await state.set_state(None) 
            
        return await message.answer("❌ عملیات لغو شد.", reply_markup=get_main_menu_keyboard())   

    body = message.text.strip()
    if not body:
        return await message.answer("پیام خالی است.")
    if len(body) > 1000:
        return await message.answer("پیام بیش از حد طولانی است (حداکثر ۱۰۰۰ کاراکتر).")

    filtered_body, was_filtered = apply_security_filters(body)

    if not recipient_tg_id:
        await state.set_state(prev_state)
        return await message.answer("نشست منقضی شده. دوباره تلاش کنید.", reply_markup=get_main_menu_keyboard())

    dm = DirectMessage(
        sender_tg_id=caller_id,
        receiver_tg_id=recipient_tg_id,
        body=filtered_body,
    )
    db_session.add(dm)
    await db_session.commit()
    await db_session.refresh(dm)

    sender_result = await db_session.execute(select(User.public_id).where(User.tg_id == caller_id))
    sender_pid = sender_result.scalar_one_or_none() or "نامشخص"

    recipient_result = await db_session.execute(select(User.public_id).where(User.tg_id == recipient_tg_id))
    recipient_pid = recipient_result.scalar_one_or_none() or "نامشخص"

    in_chat = await crud.get_active_match(db_session, recipient_tg_id) 
    
    # 🟢 جلوگیری از ارسال نوتیفیکیشن اگر گیرنده در دیت/چت فعال باشد
    if not in_chat:
        try:
            view_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✉️ مشاهده پیام دایرکت", callback_data=f"dm_view_{dm.id}")]
            ])
            
            await bot.send_message(
                recipient_tg_id,
                SystemMsg.DM_RECEIVED.format(user_tag=sender_pid),
                reply_markup=view_kb
            )
        except Exception as e:
            logger.warning("Failed to send DM notification to %s: %s", recipient_tg_id, e)

    # 🟢 پیام تایید به فرستنده همراه با وضعیت آنلاین بودن پارتنر
    if in_chat:
        await message.answer(
            f"✅ پیام شما به <code>{recipient_pid}</code> ارسال شد.\n"
            f"<i>(گیرنده در حال حاضر در دیت/چت است. پیام شما در صندوق پیام‌هایش قرار گرفت و پس از پایان دیت به او یادآوری خواهد شد.)</i>",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    else:
        await message.answer(f"✅ پیام شما به <code>{recipient_pid}</code> ارسال شد.", reply_markup=get_main_menu_keyboard(), parse_mode="HTML")

    await state.set_state(prev_state)
    await state.update_data(dm_recipient_tg_id=None, __prev_dm_state__=None)

@router.callback_query(F.data == "dm_inbox")
async def dm_inbox(call: CallbackQuery, db_session):
    """Show user's DM inbox (most recent unread first)."""
    result = await db_session.execute(
        select(DirectMessage, User)
        .join(User, DirectMessage.sender_tg_id == User.tg_id)
        .where(DirectMessage.receiver_tg_id == call.from_user.id)
        .order_by(DirectMessage.sent_at.desc())
        .limit(10)
    )
    rows = result.all()

    if not rows:
        await call.message.edit_text("📭 صندوق پیام دایرکت شما خالی است.")
        await call.answer()
        return

    messages = []
    for dm, sender in rows:
        preview = dm.body[:30] + ("…" if len(dm.body) > 30 else "")
        sent_str = dm.sent_at.strftime("%m-%d %H:%M")
        unread_marker = "🔵" if not dm.is_read else "⚪"
        messages.append((dm.id, sender.public_id, f"{unread_marker} {preview}", sent_str))

    await call.message.edit_text(
        "📨 <b>صندوق پیام دایرکت</b>\n\nآخرین ۱۰ پیام:",
        reply_markup=get_dm_inbox_keyboard(messages)
    )
    await call.answer()


@router.callback_query(F.data.startswith("dm_view_"))
async def dm_view_message(call: CallbackQuery, db_session):
    """View a specific DM."""
    try:
        msg_id = int(call.data.replace("dm_view_", ""))
    except ValueError:
        return await call.answer("پیام نامعتبر.", show_alert=True)

    result = await db_session.execute(
        select(DirectMessage, User)
        .join(User, DirectMessage.sender_tg_id == User.tg_id)
        .where(DirectMessage.id == msg_id)
    )
    row = result.first()
    if not row:
        return await call.answer("پیام یافت نشد.", show_alert=True)
    dm, sender = row
    if dm.receiver_tg_id != call.from_user.id:
        return await call.answer("دسترسی غیرمجاز.", show_alert=True)

    # Mark as read
    if not dm.is_read:
        dm.is_read = True
        dm.read_at = datetime.now(timezone.utc)
        await db_session.commit()

    text = (
        f"📨 <b>پیام دایرکت از {sender.public_id}</b>\n"
        f"📅 {dm.sent_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"{dm.body}"
    )
    await call.message.edit_text(text, reply_markup=get_dm_message_keyboard(dm.id, sender.tg_id))
    await call.answer()

# در فایل direct_messages.py

@router.callback_query(F.data.startswith("dm_reply_"))
async def dm_reply(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """پاسخ به پیام دایرکت با گارد امنیتی ضد زامبی"""
    # 🛡️ جلوگیری از نشت استیت: کاربر در دیت نباید به دایرکت پاسخ دهد
    active_match = await crud.get_active_match(db_session, call.from_user.id)
    if active_match:
        return await call.answer("⚠️ شما در حال حاضر در یک چت/دیت فعال هستید و نمی‌توانید پاسخ دایرکت بفرستید.", show_alert=True)

    parts = call.data.split("_")
    if len(parts) < 4:
        return await call.answer("درخواست نامعتبر.", show_alert=True)
    try:
        msg_id = int(parts[2])
        sender_tg_id = int(parts[3])
    except ValueError:
        return await call.answer("پارامتر نامعتبر.", show_alert=True)

    # 🛡️ ذخیره استیت قبلی
    current_state = await state.get_state()
    await state.update_data(
        dm_recipient_tg_id=sender_tg_id, 
        reply_to_msg_id=msg_id,
        __prev_dm_state__=current_state
    )
    await state.set_state(DirectMessageStates.typing_message)

    await call.message.answer(
        "📨 <b>پاسخ به پیام دایرکت</b>\n\nپیام خود را بنویسید:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data.startswith("dm_delete_"))
async def dm_delete(call: CallbackQuery, db_session: AsyncSession):
    """Delete a DM."""
    try:
        msg_id = int(call.data.replace("dm_delete_", ""))
    except ValueError:
        return await call.answer("پیام نامعتبر.", show_alert=True)

    result = await db_session.execute(
        select(DirectMessage).where(DirectMessage.id == msg_id)
    )
    dm = result.scalar_one_or_none()
    if not dm:
        return await call.answer("پیام یافت نشد.", show_alert=True)
    if dm.receiver_tg_id != call.from_user.id:
        return await call.answer("دسترسی غیرمجاز.", show_alert=True)

    await db_session.delete(dm)
    await db_session.commit()
    await call.answer("🗑 پیام حذف شد.")
    
    # 🌟 رفع بن‌بست: بازگشت روان به صندوقچه پیام‌ها به جای محو شدن منو
    await dm_inbox(call, db_session)

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# 🚀 قابلیت جدید: داشبورد یکپارچه صندوق پیام‌ها
from aiogram.filters import StateFilter
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# 🚀 قابلیت جدید: داشبورد یکپارچه صندوق پیام‌ها
@router.message(F.text == "📬 صندوق پیام‌ها", StateFilter("*"))
async def unified_inbox_dashboard(message: Message, db_session: AsyncSession):
    tg_id = message.from_user.id
    
    active_match = await crud.get_active_match(db_session, tg_id)
    if active_match:
        try:
            await message.delete()  # پاک کردن پیام ارسالی کاربر برای تمیز ماندن چت
        except TelegramBadRequest:
            pass
        return await message.answer("⚠️ شما در حال حاضر در یک دیت فعال هستید! لطفاً تمرکزتان را روی چت حفظ کنید.")

    unread_dms = await crud.get_user_unread_dm_count(db_session, tg_id)
    
    anon_msgs = await crud.get_unread_anonymous_messages(db_session, tg_id)
    unread_anons = len(anon_msgs) if anon_msgs else 0

    text = (
        "📬 <b>صندوق پیام‌های شما</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "به صندوق پیام‌های شخصی خود خوش آمدید. کدام بخش را می‌خواهید بررسی کنید؟"
    )
    
    # 🟢 برطرف کردن باگ نمایشی: کلمه "جدید" فقط در صورت وجود پیام نخوانده نمایش داده می‌شود
    dm_text = f"✉️ پیام‌های دایرکت ({unread_dms} جدید)" if unread_dms and unread_dms > 0 else "✉️ پیام‌های دایرکت"
    anon_text = f"💌 پیام‌های لینک ناشناس ({unread_anons} جدید)" if unread_anons and unread_anons > 0 else "💌 پیام‌های لینک ناشناس"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=dm_text, callback_data="dm_inbox")],
        [InlineKeyboardButton(text=anon_text, callback_data="open_anon_inbox")],
        [InlineKeyboardButton(text="🔗 دریافت لینک اختصاصی من", callback_data="get_my_anon_link")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


from aiogram.exceptions import TelegramBadRequest

from aiogram.exceptions import TelegramBadRequest

@router.callback_query(F.data == "open_anon_inbox")
async def route_to_anon_inbox(call: CallbackQuery, db_session: AsyncSession):
    await call.answer()
    
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass 
        
    from matching_bot_project.bot.handlers.anonymous_link import open_anonymous_inbox
    # ساخت یک کپی از پیام با فرستنده جدید (روش صحیح Pydantic V2)
    msg = call.message.model_copy(update={"from_user": call.from_user})
    await open_anonymous_inbox(msg, db_session)


@router.callback_query(F.data == "get_my_anon_link")
async def route_to_get_link(call: CallbackQuery, db_session: AsyncSession):
    await call.answer()
    
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass 
        
    from matching_bot_project.bot.handlers.anonymous_link import generate_my_anon_link
    # ساخت یک کپی از پیام با فرستنده جدید
    msg = call.message.model_copy(update={"from_user": call.from_user})
    await generate_my_anon_link(msg, db_session)


@router.callback_query(F.data == "get_my_anon_link")
async def route_to_get_link(call: CallbackQuery, db_session: AsyncSession):
    await call.answer()
    from matching_bot_project.bot.handlers.anonymous_link import generate_my_anon_link
    await call.message.delete()
    call.message.from_user = call.from_user
    await generate_my_anon_link(call.message, db_session)

# آپدیت تابع لیست دایرکت‌ها
@router.callback_query(F.data == "dm_inbox")
async def dm_inbox(call: CallbackQuery, db_session: AsyncSession):
    """Show user's DM inbox (most recent unread first)."""
    result = await db_session.execute(
        select(DirectMessage, User)
        .join(User, DirectMessage.sender_tg_id == User.tg_id)
        .where(DirectMessage.receiver_tg_id == call.from_user.id)
        .order_by(DirectMessage.is_read.asc(), DirectMessage.sent_at.desc()) # نخونده‌ها بالاتر
        .limit(10)
    )
    rows = result.all()

    if not rows:
        await call.message.edit_text("📭 صندوق پیام دایرکت شما خالی است.")
        await call.answer()
        return

    messages = []
    for dm, sender in rows:
        sent_str = dm.sent_at.strftime("%m-%d %H:%M")
        unread_marker = "🔵" if not dm.is_read else "⚪"
        # 🚀 فیکس: استایل دیتای ارسالی به کیبورد عوض شد
        messages.append((dm.id, sender.public_id, unread_marker, sent_str))

    await call.message.edit_text(
        "📨 <b>صندوق پیام دایرکت</b>\n\nآخرین پیام‌های شما:",
        reply_markup=get_dm_inbox_keyboard(messages),
        parse_mode="HTML"
    )
    await call.answer()