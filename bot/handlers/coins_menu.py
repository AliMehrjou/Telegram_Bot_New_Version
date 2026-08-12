"""
bot/handlers/coins_menu.py

v3 NEW: Coins main menu handler.

Routes:
- Reply keyboard "سکه"             → coins main menu (purchase/free/history/transfer)
- Inline "coins_buy"               → purchase packages (delegates to payments.py)
- Inline "coins_free"              → free-coin banner flow
- Inline "coins_history"           → show recent transactions
- Inline "coins_transfer"          → transfer coins (delegates to transfer.py)
- Inline "coins_gift_transfer"     → send gift (delegates to gifts.py)
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from matching_bot_project.bot.core.constants import ReplyBtn
from matching_bot_project.bot.keyboards.inline import get_coins_main_menu_keyboard
from matching_bot_project.bot.core.loader import free_coin_banner_service, bot
from matching_bot_project.database.models.models import User
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.formatters import build_unified_profile_card

logger = logging.getLogger(__name__)
router = Router()

class BannerProofStates(StatesGroup):
    waiting_for_proof = State()

@router.message(F.text == ReplyBtn.MY_COINS)
async def coins_main_menu(message: Message, db_session):
    """Show coins main menu."""
    user = await db_session.execute(select(User).where(User.tg_id == message.from_user.id))
    user = user.scalar_one_or_none()
    if not user:
        return await message.answer("ابتدا ثبت‌نام کنید.")

    text = (
        f"💰 <b>منوی سکه</b>\n\n"
        f"موجودی فعلی شما: <b>{user.coin_balance}</b> سکه\n\n"
        f"از منوی زیر انتخاب کنید:"
    )
    await message.answer(text, reply_markup=get_coins_main_menu_keyboard())

@router.callback_query(F.data == "coins_free")
async def coins_free_menu(call: CallbackQuery, db_session, state: FSMContext):
    """Show free-coin banner flow."""
    await state.clear()
    
    campaign = await free_coin_banner_service.get_active_campaign(db_session)
    if not campaign:
        await call.message.edit_text("😕 در حال حاضر کمپین بنری فعالی وجود ندارد. بعداً دوباره تلاش کنید.")
        await call.answer()
        return

    already = await free_coin_banner_service.has_user_forwarded(db_session, call.from_user.id, campaign.id)
    if already:
        await call.message.edit_text("✅ شما قبلاً در این کمپین شرکت کرده‌اید (در انتظار تأیید ادمین یا بررسی‌شده).")
        await call.answer()
        return

    # 🌟 حذف منوی قبلی برای جلوگیری از کثیفی بصری
    try:
        await call.message.delete()
    except Exception:
        pass

    from matching_bot_project.bot.keyboards.inline import get_free_coin_banner_keyboard
    await bot.send_photo(
        chat_id=call.from_user.id,
        photo=campaign.banner_photo_file_id,
        caption=(
            f"🎁 <b>سکه رایگان!</b>\n\n"
            f"{campaign.caption_text}\n\n"
            f"💰 پاداش: {campaign.reward_coins} سکه\n\n"
            f"برای دریافت سکه، ابتدا این بنر را در کانال‌ها یا گروه‌های تلگرام فوروارد کنید، "
            f"سپس روی دکمه شیشه‌ای زیر کلیک کنید تا اسکرین‌شات یا پیام فوروارد شده را برای ما بفرستید."
        ),
        reply_markup=get_free_coin_banner_keyboard(campaign.id)
    )
    await call.answer("بنر در پیام جداگانه ارسال شد.")


@router.callback_query(F.data.startswith("banner_fwd_"))
async def ask_for_banner_proof(call: CallbackQuery, state: FSMContext):
    """User tapped 'forward banner' — ask them for proof."""
    try:
        campaign_id = int(call.data.replace("banner_fwd_", ""))
    except ValueError:
        return await call.answer("کمپین نامعتبر.", show_alert=True)

    await state.update_data(campaign_id=campaign_id)
    await state.set_state(BannerProofStates.waiting_for_proof)

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data="banner_close")]
    ])

    await call.message.answer(
        "📸 <b>درخواست دریافت سکه</b>\n\n"
        "لطفاً پیام فوروارد شده را همینجا فوروارد کنید، یا از کانال/گروهی که بنر را در آن قرار دادید یک <b>اسکرین‌شات</b> بفرستید:",
        reply_markup=cancel_kb,
        parse_mode="HTML"
    )
    await call.answer()


@router.message(BannerProofStates.waiting_for_proof)
async def receive_banner_proof(message: Message, state: FSMContext, db_session):
    """Receive user's proof and send to admins."""
    data = await state.get_data()
    campaign_id = data.get("campaign_id")

    if not campaign_id:
        await state.clear()
        return await message.answer("⚠️ نشست شما منقضی شده است. لطفاً از منوی اصلی دوباره تلاش کنید.")

    # 1. ثبت رکورد در دیتابیس
    success, msg, forward_id = await free_coin_banner_service.record_forward(
        db_session, message.from_user.id, campaign_id,
        forward_msg_id=message.message_id,
        forward_chat_id=message.chat.id,
    )

    if not success:
        await state.clear()
        return await message.answer(msg)

    # 2. ساخت دکمه‌های تایید/رد برای ادمین
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تأیید", callback_data=f"admin_banner_approve_{forward_id}"),
            InlineKeyboardButton(text="❌ رد", callback_data=f"admin_banner_reject_{forward_id}")
        ]
    ])

    admin_text = (
        f"🎁 <b>درخواست تأیید بنر سکه رایگان</b>\n\n"
        f"👤 کاربر: <code>{message.from_user.id}</code>\n"
        f"🔖 شناسه رکورد: {forward_id}\n\n"
        f"لطفاً پیام یا تصویر ارسالی کاربر را در زیر بررسی کنید:"
    )

    # 3. ارسال کپی پیام کاربر به همراه دکمه‌ها برای تمام ادمین‌ها
    delivery_count = 0
    for admin_id in settings.parsed_admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=admin_text, parse_mode="HTML")
            await bot.copy_message(
                chat_id=admin_id, 
                from_chat_id=message.chat.id, 
                message_id=message.message_id, 
                reply_markup=admin_kb
            )
            delivery_count += 1
        except Exception as e:
            logger.error(f"Failed to send banner proof to admin {admin_id}: {e}")

    await state.clear()
    
    if delivery_count > 0:
        await message.answer("✅ <b>مدرک شما با موفقیت دریافت شد!</b>\nبرای بررسی به مدیریت ارسال گردید و پس از تایید، سکه‌ها واریز می‌شود.", parse_mode="HTML")
    else:
        await message.answer("⚠️ مدرک شما ثبت شد، اما در ارسال به ادمین‌ها مشکلی پیش آمد. در سیستم ثبت شده است و بعدا بررسی می‌شود.", parse_mode="HTML")

@router.callback_query(F.data == "banner_close")
async def banner_close(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """Cancel banner flow."""
    await state.clear()
    await call.answer("❌ عملیات لغو شد.")
    
    # 🌟 بازسازی منوی سکه
    user = await db_session.execute(select(User).where(User.tg_id == call.from_user.id))
    user = user.scalar_one_or_none()
    if user:
        text = (
            f"💰 <b>منوی سکه</b>\n\n"
            f"موجودی فعلی شما: <b>{user.coin_balance}</b> سکه\n\n"
            f"از منوی زیر انتخاب کنید:"
        )
        try:
            # چون پیام قبلی عکس‌دار بوده، عکس را پاک کرده و متن می‌فرستیم
            if call.message.photo:
                await call.message.delete()
                await call.message.answer(text, reply_markup=get_coins_main_menu_keyboard(), parse_mode="HTML")
            else:
                await call.message.edit_text(text, reply_markup=get_coins_main_menu_keyboard(), parse_mode="HTML")
        except Exception:
            pass