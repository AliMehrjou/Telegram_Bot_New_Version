import json
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from matching_bot_project.bot.filters.custom import IsAdminFilter
from matching_bot_project.database.models.models import CoinPackage, CoinPurchaseOrder
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import bot, referral_engine, vip_manager  # ✅ ایمپورت سرویس‌های رفرال و VIP
from matching_bot_project.database.queries import crud
from matching_bot_project.bot.states.states import PaymentStates
from matching_bot_project.bot.keyboards.inline import (
    get_coin_packages_keyboard, 
    get_payment_method_keyboard, 
    get_admin_receipt_keyboard
)
from matching_bot_project.services.payment_settings import get_card_info
from matching_bot_project.services import zarinpal

logger = logging.getLogger(__name__)
router = Router(name="payments_handler")


# 1. نمایش فروشگاه
# 1. نمایش فروشگاه
@router.callback_query(F.data.in_({"coins_purchase", "coins_buy"}))
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
    
    await call.message.edit_text(
        text, 
        reply_markup=get_payment_method_keyboard(settings.PAYMENT_GATEWAY_ENABLED)
    )


@router.callback_query(PaymentStates.choosing_method, F.data == "pay_method_card")
async def process_card_payment(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    data = await state.get_data()
    package_id = data.get("selected_package_id")
    package = await db_session.get(CoinPackage, package_id)
    if not package:
        await state.clear()
        return await call.answer("❌ نشست منقضی شده. لطفاً دوباره بسته را انتخاب کنید.", show_alert=True)

    card_number, card_holder = await get_card_info()

    text = (
        "💳 <b>پرداخت کارت به کارت</b>\n\n"
        f"لطفاً مبلغ <b>{package.price_toman:,} تومان</b> را به شماره کارت زیر واریز کنید:\n\n"
        f"<code>{card_number}</code>\n"
        f"👤 به نام: {card_holder}\n\n"
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
    if not package:
        await state.clear()
        return await message.answer(
            "❌ نشست منقضی شده. لطفاً از /start مجدداً بسته مورد نظر را انتخاب کنید."
        )

    photo_file_id = message.photo[-1].file_id
    
    order = await crud.create_purchase_order(
        session=db_session,
        user_tg_id=message.from_user.id,
        package_id=package_id,
        payment_method="card_to_card",
        receipt_photo_file_id=photo_file_id
    )
    await db_session.commit()
    
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
        await message.answer(
            f"✅ فیش واریزی شما با موفقیت ثبت شد.\n\n"
            f"🧾 <b>شماره سفارش شما:</b> <code>{order.id}</code>\n\n"
            f"پس از بررسی توسط پشتیبانی، سکه‌ها به حساب شما منظور خواهد شد.\n"
            f"📌 لطفاً شماره سفارش را برای پیگیری نگه دارید.",
            parse_mode="HTML"
        )
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
async def process_gateway_payment(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    if not settings.PAYMENT_GATEWAY_ENABLED:
        return await call.answer("⚠️ درگاه پرداخت در حال حاضر غیرفعال است.", show_alert=True)

    data = await state.get_data()
    package_id = data.get("selected_package_id")
    package = await db_session.get(CoinPackage, package_id)
    if not package or not package.is_active:
        return await call.answer("❌ این بسته دیگر در دسترس نیست.", show_alert=True)

    await call.answer("🔗 در حال دریافت لینک پرداخت...")

    order = await crud.create_purchase_order(
        session=db_session,
        user_tg_id=call.from_user.id,
        package_id=package.id,
        payment_method="gateway",
    )
    
    # 🔄 فاز ۳: فریز کردن قیمت و ثبت اسنپ‌شات در دیتابیس
    # با ذخیره کردن price_toman_snapshot در order_payload، کال‌بک زرین‌پال دقیقاً
    # با همین قیمت پردازش می‌شود و تغییر قیمت توسط ادمین باعث خطای وریفای نمی‌شود.
    import json
    order.order_payload = json.dumps({"price_toman_snapshot": package.price_toman})
    
    await db_session.commit()

    callback_url = f"{settings.BASE_URL}{settings.ZARINPAL_CALLBACK_PATH}?order_id={order.id}"

    success, result = await zarinpal.request_payment(
        amount_toman=package.price_toman,
        description=f"خرید {package.coin_amount} سکه (سفارش #{order.id})",
        callback_url=callback_url,
    )

    if not success:
        order.status = "failed"
        await db_session.commit()
        logger.error(f"Zarinpal request failed for order {order.id}: {result}")
        await state.clear()
        return await call.message.edit_text(
            "❌ متأسفانه در ارتباط با درگاه پرداخت مشکلی پیش آمد.\n"
            "لطفاً بعداً تلاش کنید یا از روش «کارت به کارت» استفاده کنید."
        )

    authority = result
    order.gateway_authority = authority
    await db_session.commit()

    pay_url = zarinpal.build_payment_redirect_url(authority)
    pay_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 پرداخت آنلاین", url=pay_url)],
    ])

    await state.clear()
    await call.message.edit_text(
        f"🔗 <b>لینک پرداخت آماده شد.</b>\n\n"
        f"🧾 <b>شماره سفارش:</b> <code>{order.id}</code>\n"
        f"📦 بسته: {package.coin_amount} سکه\n"
        f"💰 مبلغ: {package.price_toman:,} تومان\n\n"
        "روی دکمه زیر بزنید تا به درگاه پرداخت منتقل شوید.\n"
        "بعد از پرداخت موفق، سکه‌ها به‌صورت خودکار به حساب شما اضافه می‌شود.",
        reply_markup=pay_kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cancel_payment")
async def cancel_payment_flow(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    await state.clear()
    await call.answer("❌ عملیات خرید لغو شد.")
    
    
    await show_store(call, state, db_session)

@router.callback_query(F.data.startswith("verify_receipt_"))
async def admin_verify_receipt(call: CallbackQuery, db_session: AsyncSession):
    order_id = int(call.data.replace("verify_receipt_", ""))
    
    # 1. پیدا کردن سفارش
    order = await db_session.get(CoinPurchaseOrder, order_id)
    # اصلاح: پشتیبانی از pending_receipt برای سفارش‌های VIP
    if not order or order.status not in ("pending", "pending_receipt"):
        return await call.answer("سفارش یافت نشد یا قبلاً تعیین تکلیف شده است.", show_alert=True)
        
    order_type = getattr(order, "order_type", "coins")
    
    # ─── حالت اول: خرید VIP ───
    if order_type == "vip_subscription":
        payload = json.loads(order.order_payload or "{}")
        plan_code = payload.get("plan_code")
        
        try:
            await vip_manager.activate_subscription(
                session=db_session,
                user_tg_id=order.user_tg_id,
                plan_code=plan_code,
                payment_order_id=order.id
            )
            order.status = "completed"
            order.resolved_at = datetime.now(timezone.utc)
            await db_session.commit()
            
            await bot.send_message(
                chat_id=order.user_tg_id,
                text="🎉 <b>تبریک!</b> رسید پرداخت شما تأیید شد و اشتراک VIP شما هم‌اکنون فعال است.\nمی‌توانید از منوی اصلی وارد «بخش ویژه VIP» شوید.",
                parse_mode="HTML"
            )
            await call.message.edit_caption(
                caption=call.message.caption + "\n\n✅ <b>توسط شما تأیید شد (VIP).</b>",
                parse_mode="HTML"
            )
            await call.answer("رسید تأیید و VIP برای کاربر فعال شد.", show_alert=True)
            
        except Exception as e:
            await db_session.rollback()
            await call.answer("خطا در سیستم فعال‌سازی VIP.", show_alert=True)
            
    # ─── حالت دوم: خرید بسته سکه (بخش اضافه شده) ───
    elif order_type == "coins":
        try:
            package = await db_session.get(CoinPackage, order.package_id)
            if not package:
                return await call.answer("بسته سکه مرتبط با این سفارش یافت نشد.", show_alert=True)
                
            target_user = await crud.get_user_by_tg_id(db_session, order.user_tg_id)
            if not target_user:
                return await call.answer("کاربر خریدار یافت نشد.", show_alert=True)
                
            # تخصیص سکه به کاربر
            await crud.process_coin_transaction(
                session=db_session,
                user=target_user,
                amount=package.coin_amount,
                description=f"خرید بسته {package.coin_amount} سکه‌ای (کارت به کارت)",
                ignore_multiplier=False
            )
            
            # اعمال پورسانت رفرال
            await referral_engine.process_commission_on_purchase(
                db_session, order.id, target_user.tg_id, package.coin_amount
            )
            
            order.status = "completed"
            order.resolved_at = datetime.now(timezone.utc)
            await db_session.commit()
            
            await bot.send_message(
                chat_id=order.user_tg_id,
                text=f"🎉 <b>تبریک!</b> رسید پرداخت شما تأیید شد و {package.coin_amount} سکه به حساب شما اضافه گردید.",
                parse_mode="HTML"
            )
            await call.message.edit_caption(
                caption=call.message.caption + "\n\n✅ <b>توسط شما تأیید شد (سکه).</b>",
                parse_mode="HTML"
            )
            await call.answer("رسید تأیید و سکه‌ها واریز شد.", show_alert=True)
            
        except Exception as e:
            await db_session.rollback()
            await call.answer("خطا در سیستم تخصیص سکه.", show_alert=True)
    else:
        await call.answer("نوع سفارش نامشخص است.", show_alert=True)


@router.callback_query(F.data.startswith("reject_receipt_"))
async def admin_reject_receipt(call: CallbackQuery, db_session: AsyncSession):
    order_id = int(call.data.replace("reject_receipt_", ""))
    
    order = await db_session.get(CoinPurchaseOrder, order_id)
    # اصلاح: پشتیبانی از pending_receipt برای سفارش‌های VIP
    if not order or order.status not in ("pending", "pending_receipt"):
        return await call.answer("سفارش یافت نشد یا قبلاً تعیین تکلیف شده است.", show_alert=True)
        
    order.status = "rejected"
    order.resolved_at = datetime.now(timezone.utc)
    await db_session.commit()
    
    await bot.send_message(
        chat_id=order.user_tg_id,
        text="❌ <b>متأسفانه رسید پرداخت شما توسط پشتیبانی تأیید نشد.</b>\nاگر فکر می‌کنید اشتباهی رخ داده به پشتیبانی پیام دهید.",
        parse_mode="HTML"
    )
    
    await call.message.edit_caption(
        caption=call.message.caption + "\n\n❌ <b>توسط شما رد شد.</b>",
        parse_mode="HTML"
    )
    await call.answer("رسید رد شد.", show_alert=True)