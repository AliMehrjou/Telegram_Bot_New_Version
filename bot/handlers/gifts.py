"""
bot/handlers/gifts.py

v3 NEW: Gift shop & transfer handler.

Routes:
- Reply keyboard "🎁 گیفت‌ها"     → main gift menu
- Inline "gift_buy"               → gift picker (purchase)
- Inline "gift_send"              → gift transfer (needs recipient tag)
- Inline "gift_inventory"         → show user's owned gifts
- Inline "gift_pick_{code}"       → select a gift type
- Inline "gift_qty_{code}_{n}"    → select quantity
- Inline "gift_cancel"            → cancel
"""

import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from matching_bot_project.bot.core.loader import gift_engine
from matching_bot_project.bot.core.constants import ReplyBtn, InlineBtn, GiftCode
from matching_bot_project.bot.keyboards.inline import (
    get_gifts_main_menu_keyboard,
    get_gift_picker_keyboard,
    get_gift_quantity_keyboard,
)
from matching_bot_project.bot.keyboards.reply import (
    get_main_menu_keyboard, 
    get_cancel_keyboard,
    get_chat_phase_keyboard,
    get_date_phase_keyboard
)
from matching_bot_project.bot.states.states import GiftStates, ChatStates
from matching_bot_project.bot.core.formatters import build_unified_profile_card
from matching_bot_project.database.models.models import User

from matching_bot_project.database.queries import crud
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard

from sqlalchemy import select, or_, and_
from matching_bot_project.database.models.models import BlockList
import secrets
from matching_bot_project.bot.core.loader import redis_client

logger = logging.getLogger(__name__)
router = Router()

# ════════════════════════════════════════════════════════════════════════════
# FSM State Preservation & Restoration
# ════════════════════════════════════════════════════════════════════════════

_CHAT_METADATA_KEYS = (
    "partner_id",
    "match_history_id",
    "partner_tg_id",
    "match_type",
    "chat_started_at",
    "chat_role",
)

_GIFT_TEMP_KEYS = frozenset({
    "chosen_gift_code",
    "recipient_tg_id",
    "send_code",
    "send_recipient",
    "__gift_prev_state__",
    "__gift_chat_meta_snapshot__",
})

_GIFT_STATES = frozenset({
    GiftStates.choosing_quantity.state,
    GiftStates.waiting_for_recipient.state,
    GiftStates.confirming_transfer.state,
})


async def _preserve_current_state(state: FSMContext) -> None:
    """ذخیره وضعیت فعلی و تمام متادیتاها قبل از ورود به فلوی گیفت."""
    current_state = await state.get_state()
    
    # 🛡️ جلوگیری از بازنویسی استیت اصلی در صورت کلیک‌های متوالی روی دکمه‌های گیفت
    if current_state in _GIFT_STATES:
        return

    data = await state.get_data()
    
    # 🛡️ ذخیره کل دیتای کاربر به جای استفاده از کلیدهای محدود تا دیتای پرسشنامه پاک نشود
    chat_meta_snapshot = {
        k: v for k, v in data.items() 
        if k not in _GIFT_TEMP_KEYS and not k.startswith("__gift_")
    }
    
    await state.update_data(
        __gift_prev_state__=current_state,
        __gift_chat_meta_snapshot__=chat_meta_snapshot,
    )


async def _restore_previous_state(state: FSMContext, db_session, tg_id: int) -> str | None:
    """بازگردانی FSM به وضعیت قبل با اعتبارسنجی زنده بودن چت/دیت"""
    data = await state.get_data()
    previous_state = data.pop("__gift_prev_state__", None)
    chat_meta_snapshot = data.pop("__gift_chat_meta_snapshot__", {})

    for key in _GIFT_TEMP_KEYS:
        data.pop(key, None)

    for key, value in chat_meta_snapshot.items():
        if data.get(key) is None and value is not None:
            data[key] = value

    # 🛡️ گارد امنیتی ضد زامبی
    is_pipeline = previous_state and any(p in previous_state.lower() for p in ["chat", "matching", "questionnaire"])
    if is_pipeline:
        active_match = await crud.get_active_match(db_session, tg_id)
        if not active_match:
            await state.clear()
            return None

    if previous_state in _GIFT_STATES:
        previous_state = None

    await state.set_data(data)
    
    # 👇 اطمینان از خروج از استیت گیفت
    if previous_state:
        await state.set_state(previous_state)
    else:
        await state.set_state(None) # 👈 خروج کامل از استیت گیفت

    return previous_state

async def _get_active_session_context(prev_state: str | None, state: FSMContext):
    """بررسی می‌کند کاربر در کدام فاز (چت یا دیت) بوده و کیبورد مناسب را برمی‌گرداند."""
    if prev_state:
        # اگر کاربر در فاز پرسشنامه و دیت بوده
        if "QuestionnaireStates" in prev_state:
            return True, get_date_phase_keyboard(), "دیت"
        # اگر کاربر در فاز چت آزاد بوده
        if "ChatStates" in prev_state:
            return True, get_chat_phase_keyboard(), "چت ناشناس"
            
    # پشتیبان: اگر استیت دقیق نبود اما آیدی پارتنر وجود داشت
    data = await state.get_data()
    if data.get("partner_id") or data.get("match_history_id"):
        return True, get_chat_phase_keyboard(), "چت ناشناس"
        
    return False, None, None

# ════════════════════════════════════════════════════════════════════════════
# Handlers
# ════════════════════════════════════════════════════════════════════════════

@router.message(F.text == ReplyBtn.GIFTS)
async def gifts_main_menu(message: Message, db_session):
    """Main gifts menu."""
    user = await db_session.execute(select(User).where(User.tg_id == message.from_user.id))
    user = user.scalar_one_or_none()
    if not user:
        return await message.answer("ابتدا ثبت‌نام کنید.")

    inventory = await gift_engine.get_user_inventory(db_session, user.tg_id)
    inv_text = ""
    if inventory:
        inv_lines = []
        for ug, gt in inventory:
            if ug.quantity > 0:
                inv_lines.append(f" ┣ {gt.emoji} <b>{gt.display_name}:</b> {ug.quantity} عدد")
        if inv_lines:
            inv_text = "\n\n📦 <b>موجودی کیف شما:</b>\n<blockquote>" + "\n".join(inv_lines) + "\n┗ ───────────────</blockquote>"

    text = (
        f"🎁 <b>منوی گیفت‌ها</b>\n\n"
        f"گیفت‌ها هدایایی هستند که می‌توانید بخرید یا برای دیگران بفرستید.\n"
        f"گیفت‌های شما در پروفایلتان نمایش داده می‌شوند."
        f"{inv_text}"
    )
    
    await message.answer(text, reply_markup=get_gifts_main_menu_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "gift_buy")
async def gift_buy_picker(call: CallbackQuery, db_session):
    """Show picker for buying gifts."""
    gift_types = await gift_engine.get_all_active_gift_types(db_session)
    if not gift_types:
        return await call.answer("در حال حاضر گیفتی موجود نیست.", show_alert=True)

    owned_summary = await gift_engine.get_user_gifts_summary(db_session, call.from_user.id)
    
    rows = []
    for gt in gift_types:
        owned = owned_summary.get(gt.emoji, 0)
        text_btn = f"{gt.emoji} {gt.display_name} — {gt.price_coins} سکه"
        if owned > 0:
            text_btn += f" (دارید: {owned})"
        
        rows.append([InlineKeyboardButton(text=text_btn, callback_data=f"gift_pick_{gt.code}")])
        
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data="gift_cancel", style="danger")])
    
    await call.message.edit_text(
        "🎁 <b>خرید گیفت</b>\n\n"
        "گیفت مورد نظر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await call.answer()


@router.callback_query(F.data.startswith("gift_pick_"))
async def gift_pick_quantity(call: CallbackQuery, db_session, state: FSMContext):
    """After picking a gift, ask for quantity."""
    code = call.data.replace("gift_pick_", "")
    gift_types = await gift_engine.get_all_active_gift_types(db_session)
    gift_type = next((gt for gt in gift_types if gt.code == code), None)
    if not gift_type:
        return await call.answer("گیفت نامعتبر.", show_alert=True)

    # حفظ وضعیت قبل از ورود به فاز انتخاب تعداد جهت خرید
    await _preserve_current_state(state)
    await state.update_data(chosen_gift_code=code)
    await state.set_state(GiftStates.choosing_quantity)

    await call.message.edit_text(
        f"🎁 <b>{gift_type.emoji} {gift_type.display_name}</b>\n"
        f"💰 قیمت: {gift_type.price_coins} سکه برای هر عدد\n\n"
        f"تعداد مورد نظر را انتخاب کنید:",
        reply_markup=get_gift_quantity_keyboard(code)
    )
    await call.answer()


@router.callback_query(F.data.startswith("gift_qty_"))
async def gift_buy_confirm(call: CallbackQuery, db_session, state: FSMContext):
    """Execute the gift purchase."""
    parts = call.data.split("_")
    if len(parts) < 4:
        return await call.answer("درخواست نامعتبر.", show_alert=True)
    code = parts[2]
    try:
        qty = int(parts[3])
    except ValueError:
        return await call.answer("تعداد نامعتبر.", show_alert=True)
    if qty < 1 or qty > 100:
        return await call.answer("تعداد باید بین ۱ تا ۱۰۰ باشد.", show_alert=True)

    success, msg = await gift_engine.purchase_gift(
        db_session, call.from_user.id, code, qty
    )
    
    # بازگردانی وضعیت به جای state.clear()
    prev_state = await _restore_previous_state(state, db_session, call.from_user.id)
    is_active, kb, phase_name = await _get_active_session_context(prev_state, state)
    
    if success:
        await call.message.edit_text(f"✅ {msg}", reply_markup=None)
    else:
        await call.message.edit_text(f"❌ {msg}", reply_markup=None)
        
    if is_active:
        await call.message.answer(
            f"🟢 خرید انجام شد. شما به {phase_name} برگشتید.", 
            reply_markup=kb
        )
        
    await call.answer()


@router.callback_query(F.data.in_({"gift_send", "coins_gift_transfer"}))
async def gift_send_prompt(call: CallbackQuery, state: FSMContext):
    """Ask user to enter recipient's public_id tag."""
    # حفظ وضعیت قبلی کاربر در هنگام شروع ارسال
    await _preserve_current_state(state)
    await state.set_state(GiftStates.waiting_for_recipient)
    
    await call.message.edit_text(
        "📤 <b>ارسال گیفت</b>\n\n"
        "لطفاً تگ کاربر گیرنده را وارد کنید (مثال: <code>user_abCD12</code>):\n"
        "<i>💡 نکته: برای راحتی بیشتر، می‌توانید گیفت‌ها را مستقیماً از روی پروفایل کاربران (در بخش کشف کاربران) ارسال کنید!</i>",
        reply_markup=None,
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("gift_send_direct_"))
async def gift_send_direct(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    """استارت ارسال گیفت از روی پروفایل اشخاص (به صورت پیام جداگانه)"""
    try:
        target_id = int(call.data.replace("gift_send_direct_", ""))
    except ValueError:
        return await call.answer("کاربر نامعتبر.", show_alert=True)

    if target_id == call.from_user.id:
        return await call.answer("نمی‌توانید به خودتان گیفت بفرستید.", show_alert=True)

    # بررسی بلاک بودن دوطرفه
    from sqlalchemy import select, or_, and_
    from matching_bot_project.database.models.models import BlockList
    
    block_check = await db_session.execute(
        select(BlockList).where(
            or_(
                and_(BlockList.blocker_id == call.from_user.id, BlockList.blocked_id == target_id),
                and_(BlockList.blocker_id == target_id, BlockList.blocked_id == call.from_user.id)
            )
        )
    )
    if block_check.scalar_one_or_none() is not None:
        return await call.answer("🚫 امکان تبادل گیفت با این کاربر وجود ندارد.", show_alert=True)

    inventory = await gift_engine.get_user_inventory(db_session, call.from_user.id)
    if not inventory or all(ug.quantity == 0 for ug, _ in inventory):
        return await call.answer(
            "🎁 شما هیچ گیفتی در کیف خود ندارید!\nابتدا از منوی گیفت‌ها گیفت خریداری کنید.", 
            show_alert=True
        )

    # حفظ وضعیت قبلی کاربر
    await _preserve_current_state(state)
    await state.set_state(GiftStates.waiting_for_recipient)
    await state.update_data(recipient_tg_id=target_id)
    
    rows = []
    for ug, gt in inventory:
        if ug.quantity > 0:
            rows.append([
                InlineKeyboardButton(
                    text=f"{gt.emoji} {gt.display_name} ({ug.quantity} دارید)",
                    callback_data=f"gift_send_pick_{gt.code}_{target_id}",
                    style="primary"
                )
            ])
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data="gift_cancel", style="danger")])
    
    # 🌟 اینجا پیام به صورت مجزا (answer) ارسال می‌شود تا پروفایل سر جایش بماند
    await call.message.answer(
        f"🎁 <b>ارسال گیفت</b>\n\n"
        f"لطفاً گیفتی که می‌خواهید تقدیم این کاربر کنید را از لیست زیر انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )
    
    # پایان لودینگ دکمه شیشه‌ای و یک راهنمای کوچک
    await call.answer("👇 لطفاً از پیام جدید گیفت خود را انتخاب کنید.", show_alert=False)

 
@router.message(GiftStates.waiting_for_recipient)
async def gift_send_choose_gift(message: Message, db_session, state: FSMContext):
    """User entered recipient tag — show gift picker for sending."""
    recipient_tag = message.text.strip()
    if not recipient_tag.startswith("user_"):
        await message.answer("تگ نامعتبر. مثال صحیح: <code>user_abCD12</code>")
        return

    result = await db_session.execute(
        select(User).where(User.public_id == recipient_tag)
    )
    recipient = result.scalar_one_or_none()
    if not recipient:
        await message.answer("کاربر با این تگ یافت نشد.")
        return
    if recipient.tg_id == message.from_user.id:
        await message.answer("نمی‌توانید به خودتان گیفت بفرستید.")
        return

    inventory = await gift_engine.get_user_inventory(db_session, message.from_user.id)
    if not inventory or all(ug.quantity == 0 for ug, _ in inventory):
        await message.answer("شما هیچ گیفتی برای ارسال ندارید. ابتدا گیفت بخرید.")
        await _restore_previous_state(state, db_session, message.from_user.id)
        return

    rows = []
    for ug, gt in inventory:
        if ug.quantity > 0:
            rows.append([
                InlineKeyboardButton(
                    text=f"{gt.emoji} {gt.display_name} ({ug.quantity} دارید)",
                    callback_data=f"gift_send_pick_{gt.code}_{recipient.tg_id}",
                    style="primary"
                )
            ])
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data="gift_cancel", style="danger")])
    
    await state.update_data(recipient_tg_id=recipient.tg_id)
    await message.answer(
        f"🎁 گیفت مورد نظر را برای ارسال به <code>{recipient_tag}</code> انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data.startswith("gift_send_pick_"))
async def gift_send_execute(call: CallbackQuery, db_session, state: FSMContext):
    """Execute the gift transfer."""
    parts = call.data.split("_")
    if len(parts) < 5:
        return await call.answer("درخواست نامعتبر.", show_alert=True)
    code = parts[3]
    try:
        recipient_tg_id = int(parts[4])
    except ValueError:
        return await call.answer("گیرنده نامعتبر.", show_alert=True)

    await state.update_data(send_code=code, send_recipient=recipient_tg_id)
    await state.set_state(GiftStates.confirming_transfer)

    await call.message.edit_text(
        "تعداد مورد نظر برای ارسال را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="×1", callback_data=f"gift_send_qty_1", style="primary"),
             InlineKeyboardButton(text="×2", callback_data=f"gift_send_qty_2", style="primary"),
             InlineKeyboardButton(text="×3", callback_data=f"gift_send_qty_3", style="primary")],
            [InlineKeyboardButton(text="❌ انصراف", callback_data="gift_cancel", style="danger")]
        ])
    )
    await call.answer()


@router.callback_query(F.data.startswith("gift_send_qty_"))
async def gift_send_qty_execute(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    try:
        qty = int(call.data.replace("gift_send_qty_", ""))
    except ValueError:
        return await call.answer("تعداد نامعتبر.", show_alert=True)

    data = await state.get_data()
    code = data.get("send_code")
    recipient_tg_id = data.get("send_recipient")
    
    if not code or not recipient_tg_id:
        await _restore_previous_state(state, db_session, call.from_user.id)
        return await call.answer("نشست منقضی شده. دوباره تلاش کنید.", show_alert=True)

    success, msg = await gift_engine.transfer_gift(
        db_session, call.from_user.id, recipient_tg_id, code, qty
    )
    
    await _restore_previous_state(state, db_session, call.from_user.id)
    
    if success:
        # 🛡️ تغییر UX: نمایش آلرت پاپ‌آپ و حذف پنل درخواست بدون خراب شدن دکمه‌های پروفایل
        await call.answer(f"✅ {msg}", show_alert=True)
        try:
            # 💡 به جای حذف کامل پیام، فقط کیبورد شیشه‌ای را حذف می‌کنیم
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        try:
            from matching_bot_project.bot.core.loader import bot
            gift_types = await gift_engine.get_all_active_gift_types(db_session)
            gt = next((g for g in gift_types if g.code == code), None)
            if gt:
                from matching_bot_project.database.queries import crud
                active_match = await crud.get_active_match(db_session, call.from_user.id)
                
                is_in_same_chat = False
                if active_match:
                    partner_id = active_match.user_two_id if active_match.user_one_id == call.from_user.id else active_match.user_one_id
                    if partner_id == recipient_tg_id:
                        is_in_same_chat = True

                if is_in_same_chat:
                    await bot.send_message(
                        recipient_tg_id,
                        f"🎁 <b>هدیه از پارتنر شما!</b>\nپارتنر فعلی شما در چت ناشناس، {qty} عدد {gt.emoji} <b>{gt.display_name}</b> برایتان فرستاد!",
                        parse_mode="HTML"
                    )
                else:
                    import secrets
                    from matching_bot_project.bot.core.loader import redis_client
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    opaque_token = secrets.token_urlsafe(12)
                    await redis_client.setex(
                        f"transfer:view_profile:{opaque_token}",
                        3600,
                        str(call.from_user.id),
                    )
                    
                    profile_kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="👤 مشاهده پروفایل فرستنده", callback_data=f"sender_profile_token_{opaque_token}")]
                    ])
                    
                    await bot.send_message(
                        recipient_tg_id,
                        f"🎁 <b>هدیه جدید!</b>\nشما {qty} عدد {gt.emoji} <b>{gt.display_name}</b> از طرف یک کاربر دریافت کردید!",
                        parse_mode="HTML",
                        reply_markup=profile_kb
                    )
        except Exception as e:
            logger.warning("Failed to notify gift recipient: %s", e)
    else:
        # در صورت خطا نیز پیام خطا را پاپ‌آپ می‌دهیم و کیبورد را برمی‌داریم
        await call.answer(f"❌ {msg}", show_alert=True)
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


@router.callback_query(F.data == "gift_inventory")
async def gift_inventory_show(call: CallbackQuery, db_session):
    """Show user's gift inventory."""
    inventory = await gift_engine.get_user_inventory(db_session, call.from_user.id)
    
    if not inventory or all(ug.quantity == 0 for ug, _ in inventory):
        await call.message.edit_text(
            "📦 <b>شما هنوز هیچ گیفتی ندارید!</b>\n\n"
            "می‌توانید از منوی «خرید گیفت» برای خودتان یا دیگران گیفت تهیه کنید.",
            parse_mode="HTML"
        )
        await call.answer()
        return

    lines = []
    for ug, gt in inventory:
        if ug.quantity > 0:
            lines.append(f" ┣ {gt.emoji} <b>{gt.display_name}:</b> {ug.quantity} عدد")
            
    text = (
        "📦 <b>موجودی گیفت‌های شما</b>\n\n"
        "<blockquote>" + "\n".join(lines) + "\n┗ ───────────────</blockquote>"
    )
    
    await call.message.edit_text(text, parse_mode="HTML")
    await call.answer()

# ==========================================
# هندلرهای بخش فروش گیفت
# ==========================================

from matching_bot_project.bot.keyboards.inline import get_gift_sell_picker_keyboard, get_gift_sell_quantity_keyboard

@router.callback_query(F.data == "gift_sell_start")
async def gift_sell_start_flow(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    """Start sell flow: check inventory and show sellable gifts."""
    inventory = await gift_engine.get_user_inventory(db_session, call.from_user.id)
    
    # اگر کاربر هیچ گیفتی نداشت (یا تعداد همه صفر بود)، فقط یک آلرت می‌دهیم و لودینگ را می‌بندیم
    if not inventory or all(ug.quantity == 0 for ug, _ in inventory):
        return await call.answer("📦 شما هیچ گیفتی در کیف خود برای فروش ندارید!", show_alert=True)

    # حفظ وضعیت قبلی (در صورتی که داخل چت ناشناس باشد)
    await _preserve_current_state(state)
    await state.set_state(GiftStates.choosing_sell_gift)
    
    await call.message.edit_text(
        "🛍 <b>فروش گیفت به سیستم</b>\n\n"
        "گیفتی که می‌خواهید بفروشید را انتخاب کنید:\n"
        "<i>(سیستم گیفت‌های شما را با ۲۰٪ کسر قیمت خریداری می‌کند)</i>",
        reply_markup=get_gift_sell_picker_keyboard(inventory),
        parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data.startswith("gift_sell_pick_"))
async def gift_sell_pick_quantity(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    """After picking a gift to sell, ask for quantity."""
    code = call.data.replace("gift_sell_pick_", "")
    
    inventory = await gift_engine.get_user_inventory(db_session, call.from_user.id)
    # پیدا کردن گیفت و موجودی کاربر از داخل اینونتوری
    user_gift = next((ug for ug, gt in inventory if gt.code == code and ug.quantity > 0), None)
    gift_type = next((gt for ug, gt in inventory if gt.code == code), None)
    
    if not user_gift or not gift_type:
        return await call.answer("شما این گیفت را برای فروش ندارید.", show_alert=True)

    await state.update_data(sell_gift_code=code)
    await state.set_state(GiftStates.choosing_sell_quantity)
    
    sell_price = (gift_type.price_coins * 80) // 100
    
    await call.message.edit_text(
        f"🛍 <b>فروش {gift_type.emoji} {gift_type.display_name}</b>\n\n"
        f"📦 موجودی شما: {user_gift.quantity} عدد\n"
        f"💰 قیمت خرید سیستم: {sell_price} سکه به ازای هر عدد\n\n"
        f"چه تعداد می‌خواهید بفروشید؟",
        reply_markup=get_gift_sell_quantity_keyboard(code, max_qty=user_gift.quantity),
        parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data.startswith("gift_sell_qty_"))
async def gift_sell_execute(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    """Execute the sell operation via GiftEngine."""
    parts = call.data.split("_")
    if len(parts) < 5:
        return await call.answer("درخواست نامعتبر.", show_alert=True)
        
    code = parts[3]
    try:
        qty = int(parts[4])
    except ValueError:
        return await call.answer("تعداد نامعتبر.", show_alert=True)

    # اجرای متد فروش در موتور گیفت‌ها
    success, msg = await gift_engine.sell_gift(
        db_session, call.from_user.id, code, qty
    )
    
    # بازگردانی وضعیت به جای پاک کردن کامل استیت
    prev_state = await _restore_previous_state(state, db_session, call.from_user.id)
    is_active, kb, phase_name = await _get_active_session_context(prev_state, state)
    
    if success:
        await call.answer(msg, show_alert=True)
        # ویرایش پیام به حالت موفقیت‌آمیز و حذف دکمه‌ها
        await call.message.edit_text(f"✅ {msg}", reply_markup=None)
    else:
        await call.answer(msg, show_alert=True)
        await call.message.edit_text(f"❌ {msg}", reply_markup=None)
        
    # اگر کاربر داخل چت/دیت بوده، او را برمی‌گردانیم
    if is_active:
        await call.message.answer(
            f"🟢 فروش انجام شد. شما به {phase_name} برگشتید.", 
            reply_markup=kb
        )

@router.callback_query(F.data == "gift_cancel")
async def gift_cancel(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    prev_state = await _restore_previous_state(state, db_session, call.from_user.id)
    is_active, kb, phase_name = await _get_active_session_context(prev_state, state)

    # 🌟 تغییر کلیدی: به جای ویرایش پیام به "عملیات لغو شد"، پیام موقت را کلاً پاک می‌کنیم
    try:
        await call.message.delete()
    except TelegramBadRequest:
        # فال‌بک در صورت محدودیت تلگرام
        try:
            await call.message.edit_text("❌ عملیات لغو شد.", reply_markup=None)
        except Exception:
            pass

    if is_active:
        # فقط یک آلرت می‌دهیم که به چت برگشته است، بدون ارسال پیام جدید
        await call.answer(f"لغو شد. شما در {phase_name} هستید. 🟢", show_alert=False)
    else:
        await call.answer("❌ عملیات لغو شد.", show_alert=False)


@router.message(F.text == "🎁 ارسال گیفت") # یا استفاده از متغیر ReplyBtn معادل
async def gift_send_from_chat_or_date(message: Message, db_session: AsyncSession, state: FSMContext):
    """مدیریت دکمه ریپلای 'ارسال گیفت' در حین دیت یا چت با اعمال محدودیت بلاک"""
    
    # ۱. پیدا کردن پارتنر فعال
    active_match = await crud.get_active_match(db_session, message.from_user.id)
    if not active_match:
        return await message.answer("⚠️ شما در حال حاضر در دیت یا چت فعالی نیستید.")
        
    target_id = active_match.user_two_id if active_match.user_one_id == message.from_user.id else active_match.user_one_id
    
    # ۲. بررسی بلاک بودن دوطرفه (جلوگیری از ارسال گیفت به کاربر بلاک‌شده/بلاک‌کننده)
    block_check = await db_session.execute(
        select(BlockList).where(
            or_(
                and_(BlockList.blocker_id == message.from_user.id, BlockList.blocked_id == target_id),
                and_(BlockList.blocker_id == target_id, BlockList.blocked_id == message.from_user.id)
            )
        )
    )
    if block_check.scalar_one_or_none() is not None:
        return await message.answer("🚫 امکان تبادل گیفت با این کاربر وجود ندارد (مسدود شده).")

    # ۳. بررسی موجودی کیف کاربر
    inventory = await gift_engine.get_user_inventory(db_session, message.from_user.id)
    if not inventory or all(ug.quantity == 0 for ug, _ in inventory):
        return await message.answer(
            "🎁 شما هیچ گیفتی در کیف خود ندارید!\nابتدا از منوی اصلی یا فروشگاه، گیفت تهیه کنید."
        )

    # ۴. حفظ استیت فعلی چت/دیت
    await _preserve_current_state(state)
    await state.set_state(GiftStates.waiting_for_recipient)
    await state.update_data(recipient_tg_id=target_id)
    
    # ۵. ساخت کیبورد شیشه‌ای
    rows = []
    for ug, gt in inventory:
        if ug.quantity > 0:
            rows.append([
                InlineKeyboardButton(
                    text=f"{gt.emoji} {gt.display_name} ({ug.quantity} دارید)",
                    callback_data=f"gift_send_pick_{gt.code}_{target_id}",
                    style="primary"
                )
            ])
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data="gift_cancel", style="danger")])
    
    await message.answer(
        f"🎁 <b>ارسال گیفت به پارتنر</b>\n\n"
        f"لطفاً گیفتی که می‌خواهید به پارتنرتان تقدیم کنید را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )