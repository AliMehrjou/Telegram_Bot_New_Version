import logging
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from matching_bot_project.bot.filters.custom import IsAdminFilter
from matching_bot_project.database.models.models import CoinPackage
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import bot
from matching_bot_project.database.queries import crud
from matching_bot_project.bot.states.states import PaymentStates
from matching_bot_project.bot.keyboards.inline import get_coin_packages_keyboard, get_payment_method_keyboard, get_admin_receipt_keyboard

logger = logging.getLogger(__name__)
router = Router(name="payments_handler")

# 1. نمایش فروشگاه
@router.callback_query(F.data == "coins_purchase")
async def show_store(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    packages = await crud.get_active_coin_packages(db_session)
    if not packages:
        return await call.answer("⚠️ در حال حاضر هیچ بسته‌ای برای خرید فعال نیست.", show_alert=True)
    
    await state.set_state(PaymentStates.choosing_package)
    await call.message.edit_text(
        "🛒 <b>فروشگاه سکه</b>\n\nلطفاً بسته مورد نظر خود را انتخاب کنید:",
        reply_markup=get_coin_packages_keyboard(packages),
        parse_mode="HTML"
    )

# 2. انتخاب روش پرداخت
@router.callback_query(PaymentStates.choosing_package, F.data.startswith("buy_package_"))
async def choose_payment_method(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    try:
        package_id = int(call.data.removeprefix("buy_package_"))
    except ValueError:
        return await call.answer("❌ خطای سیستمی.", show_alert=True)
        
    package = await db_session.get(CoinPackage, package_id)
    if not package or not package.is_active:
        return await call.answer("❌ این بسته دیگر در دسترس نیست.", show_alert=True)
        
    await state.update_data(selected_package_id=package.id)
    await state.set_state(PaymentStates.choosing_method)
    
    text = (
        f"📦 <b>بسته انتخابی:</b> {package.coin_amount} سکه\n"
        f"💳 <b>مبلغ قابل پرداخت:</b> {package.price_toman:,} تومان\n\n"
        f"لطفاً روش پرداخت را انتخاب کنید:"
    )
    await call.message.edit_text(text, reply_markup=get_payment_method_keyboard(settings.PAYMENT_GATEW
                                                                                

# 3. مسیر کارت به کارت
@router.callback_query(PaymentStates.choosing_method, F.data == "pay_method_card")
async def process_card_payment(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    data = await state.get_data()
    package_id = data.get("selected_package_id")
    package = await db_session.get(CoinPackage, package_id)
    
    text = (
        "💳 <b>پرداخت کارت به کارت</b>\n\n"
        f"لطفاً مبلغ <b>{package.price_toman:,} تومان</b> را به شماره کارت زیر واریز کنید:\n\n"
        f"<code>{settings.CARD_NUMBER_FOR_PAYMENT}</code>\n"
        f"👤 به نام: {settings.CARD_HOLDER_NAME}\n\n"
        "📸 <b>سپس عکس فیش واریزی خود را همینجا ارسال کنید.</b> (فقط یک عکس بفرستید)"
    )
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_payment")]])
    
    await state.set_state(PaymentStates.waiting_for_receipt_photo)
    await call.message.edit_text(text, reply_markup=cancel_kb, parse_mode="HTML")


# 4. دریافت عکس فیش و ارسال برای ادمین
@router.message(PaymentStates.waiting_for_receipt_photo, F.photo)
async def receive_receipt_photo(message: Message, state: FSMContext, db_session: AsyncSession):
    data = await state.get_data()
    package_id = data.get("selected_package_id")
    package = await db_session.get(CoinPackage, package_id)
    
    photo_file_id = message.photo[-1].file_id
    
    # ثبت سفارش در دیتابیس
    order = await crud.create_purchase_order(
        session=db_session,
        user_tg_id=message.from_user.id,
        package_id=package_id,
        payment_method="card_to_card",
        receipt_photo_file_id=photo_file_id
    )
    await db_session.commit()
    
    # ارسال برای ادمین‌ها
    admin_text = (
        "🚨 <b>درخواست تأیید واریز کارت به کارت</b>\n\n"
        f"👤 <b>آیدی کاربر:</b> <code>{message.from_user.id}</code>\n"
        f"📦 <b>بسته:</b> {package.coin_amount} سکه\n"
        f"💳 <b>مبلغ:</b> {package.price_toman:,} تومان\n"
        f"🧾 <b>شماره سفارش:</b> {order.id}"
    )
    
    delivery_success = False
    for admin_id in settings.parsed_admin_ids:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=photo_file_id,
                caption=admin_text,
                parse_mode="HTML",
                reply_markup=get_admin_receipt_keyboard(order.id)
            )
            delivery_success = True
        except Exception as e:
            logger.error(f"Failed to send receipt to admin {admin_id}: {e}")
            
    await state.clear()
    
    if delivery_success:
        await message.answer("✅ فیش واریزی شما با موفقیت ثبت شد و پس از بررسی توسط پشتیبانی، سکه‌ها به حساب شما منظور خواهد شد.")
    else:
        await message.answer("❌ متأسفانه در ارسال فیش برای پشتیبانی مشکلی پیش آمد. لطفاً مجدداً تلاش کنید یا با پشتیبانی تماس بگیرید.")


@router.message(PaymentStates.waiting_for_receipt_photo)
async def fallback_receipt_input(message: Message):
    await message.answer(
        "⚠️ <b>لطفاً عکس فیش واریزی را ارسال کنید.</b>\n"
        "متن یا فایل پی‌دی‌اف قابل قبول نیست.\n"
        "اگر منصرف شده‌اید، روی دکمه «❌ انصراف» کلیک کنید.",
        parse_mode="HTML"
    )

@router.callback_query(PaymentStates.choosing_method, F.data == "pay_method_gateway")
async def process_gateway_payment(call: CallbackQuery, state: FSMContext):
    if not settings.PAYMENT_GATEWAY_ENABLED:
        return await call.answer("⚠️ درگاه پرداخت در حال حاضر غیرفعال است.", show_alert=True)
    # TODO: Zarinpal API Request, Authority Generation, and URL routing goes here.
    await call.answer("🔗 در حال انتقال به درگاه پرداخت...", show_alert=False)
    await state.clear()
    await call.message.edit_text("⏳ لینک پرداخت به زودی از طریق وب‌هوک متصل می‌شود. در دست توسعه...")

@router.callback_query(F.data == "cancel_payment")
async def cancel_payment_flow(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ عملیات خرید لغو شد.")

# --- ادمین: تأیید و رد کردن فیش ---
@router.callback_query(IsAdminFilter(), F.data.startswith("verify_receipt_"))
async def admin_verify_receipt(call: CallbackQuery, db_session: AsyncSession):
    order_id = int(call.data.removeprefix("verify_receipt_"))
    order = await crud.get_purchase_order(db_session, order_id)
    
    if not order or order.status != "pending":
        return await call.answer("⚠️ این سفارش قبلاً پردازش شده یا وجود ندارد.", show_alert=True)
        
    package = await db_session.get(CoinPackage, order.package_id)
    target_user = await crud.get_user_by_tg_id(db_session, order.user_tg_id)
    
    if target_user and package:
        # کسر سکه اصولی با پروسس تراکنش
        await crud.process_coin_transaction(
            session=db_session, 
            user=target_user, 
            amount=package.coin_amount, 
            description=f"خرید بسته {package.coin_amount} سکه‌ای (سفارش {order.id})",
            ignore_multiplier=True
        )
        order.status = "approved"
        order.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db_session.commit()
        
        try:
            new_caption = call.message.html_text + "\n\n✅ <b>تأیید شد.</b>"
            await call.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Edit caption error: {e}")
            
        try:
            await bot.send_message(
                chat_id=target_user.tg_id, 
                text=f"🎉 <b>پرداخت شما تأیید شد!</b>\n\nتعداد {package.coin_amount} سکه به حساب شما اضافه گردید.",
                parse_mode="HTML"
            )
        except (TelegramAPIError, TelegramForbiddenError):
            logger.warning(f"Failed to notify user {target_user.tg_id} about payment approval.")
            
    await call.answer("✅ فیش تأیید و سکه‌ها واریز شد.")


@router.callback_query(IsAdminFilter(), F.data.startswith("reject_receipt_"))
async def admin_reject_receipt(call: CallbackQuery, db_session: AsyncSession):
    order_id = int(call.data.removeprefix("reject_receipt_"))
    order = await crud.get_purchase_order(db_session, order_id)
    
    if not order or order.status != "pending":
        return await call.answer("⚠️ این سفارش قبلاً پردازش شده یا وجود ندارد.", show_alert=True)
        
    order.status = "rejected"
    order.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db_session.commit()
    
    try:
        new_caption = call.message.html_text + "\n\n❌ <b>رد شد.</b>"
        await call.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Edit caption error: {e}")
        
    try:
        await bot.send_message(
            chat_id=order.user_tg_id, 
            text=f"❌ <b>پرداخت شما تأیید نشد.</b>\n\nفیش ارسالی برای سفارش #{order.id} معتبر نبود. در صورت بروز مشکل با پشتیبانی تماس بگیرید.",
            parse_mode="HTML"
        )
    except (TelegramAPIError, TelegramForbiddenError):
        pass
        
    await call.answer("❌ فیش رد شد.")
    