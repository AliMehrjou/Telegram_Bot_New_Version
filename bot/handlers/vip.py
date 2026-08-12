import logging
import time
from datetime import datetime, timezone

from aiogram.exceptions import TelegramBadRequest
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from matching_bot_project.bot.core.loader import redis_client
from matching_bot_project.database.queries.crud import get_user_by_tg_id
from matching_bot_project.database.models.models import User
from matching_bot_project.bot.keyboards.inline import get_vip_panel_keyboard
from matching_bot_project.bot.states.states import VIPStates
from aiogram.fsm.context import FSMContext
logger = logging.getLogger(__name__)
router = Router(name="vip_handler")


def _is_vip_active(user: User) -> bool:
    """
    بررسی فعال بودن VIP روی یک آبجکت User که از قبل از دیتابیس خوانده شده.
    هیچ کوئری جدیدی به دیتابیس نمی‌زند.

    FIX M-01 / HIGH-36-like: is_vip=True alone is not enough — the expiry must
    also be in the future (or absent). Previously a stale is_vip flag would grant
    VIP forever even after vip_expires_at had passed.
    """
    if not user:
        return False
    if not user.is_vip:
        return False
    if not user.vip_expires_at:
        return True
    # Normalize both sides to aware UTC for the comparison.
    expires = user.vip_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > datetime.now(timezone.utc)


async def is_vip(db_session: AsyncSession, tg_id: int) -> bool:
    """
    برای حفظ سازگاری با کدهای قدیمی نگه داشته شده.
    در هندلرهایی که از قبل آبجکت user را fetch کرده‌اند،
    به‌جای این تابع از _is_vip_active(user) استفاده کنید.
    """
    user = await get_user_by_tg_id(db_session, tg_id)
    return _is_vip_active(user)


@router.callback_query(F.data == "vip_panel")
async def open_vip_panel(call: CallbackQuery, db_session: AsyncSession):
    tg_id = call.from_user.id
    user = await get_user_by_tg_id(db_session, tg_id)

    from matching_bot_project.database.queries import crud
    if await crud.get_active_match(db_session, tg_id):
        await call.answer("⚠️ در حین چت/دیت فعال امکان ورود به پنل VIP وجود ندارد.", show_alert=True)
        return
    
    if not _is_vip_active(user):
        await call.answer("این بخش مخصوص کاربران VIP است! 💎", show_alert=True)
        return

    vip_text = "💎 <b>پنل مدیریت VIP</b>\n\nاز امکانات زیر برای مدیریت حساب ویژه خود استفاده کنید:"
    kb = get_vip_panel_keyboard(user.invisible_mode)

    if call.message.photo or call.message.voice or call.message.audio:
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass
        await call.message.answer(text=vip_text, reply_markup=kb, parse_mode="HTML")
    else:
        try:
            await call.message.edit_text(text=vip_text, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest:
            await call.message.answer(text=vip_text, reply_markup=kb, parse_mode="HTML")

    await call.answer()

@router.callback_query(F.data == "vip_viewers")
async def show_profile_viewers(call: CallbackQuery, db_session: AsyncSession):
    tg_id = call.from_user.id
    user = await get_user_by_tg_id(db_session, tg_id)
    if not _is_vip_active(user):
        await call.answer("دسترسی غیرمجاز.", show_alert=True)
        return

    key = f"user:{tg_id}:viewers"
    # FIX HIGH-26 (resilience): wrap Redis in try/except so a Redis outage does not crash the handler.
    try:
        viewers = await redis_client.zrevrange(key, 0, 19, withscores=True)
    except Exception as e:
        logger.warning("vip_viewers: Redis failure for user %s: %s", tg_id, e)
        return await call.answer("اطلاعات فعلاً در دسترس نیست. لطفاً بعداً تلاش کنید.", show_alert=True)

    if not viewers:
        await call.answer("هیچ بازدیدکننده‌ای ثبت نشده است.", show_alert=True)
        return

    text_lines = ["👀 <b>بازدیدکنندگان اخیر پروفایل شما:</b>\n"]
    now = time.time()

    for member, score in viewers:
        viewer_id = int(member)
        viewer = await get_user_by_tg_id(db_session, viewer_id)
        if viewer:
            name = viewer.first_name or "کاربر"
            anon_name = name[:2] + "***" if len(name) >= 2 else name + "***"

            diff = now - float(score)
            if diff < 3600:
                time_str = f"{int(diff/60)} دقیقه پیش"
            elif diff < 86400:
                time_str = f"{int(diff/3600)} ساعت پیش"
            else:
                time_str = f"{int(diff/86400)} روز پیش"

            gender = "پسر" if viewer.gender in ["Male", "boy"] else "دختر" if viewer.gender in ["Female", "girl"] else "?"
            text_lines.append(f"👤 {anon_name} ({gender}) - {time_str}")

    await call.message.answer("\n".join(text_lines), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "vip_toggle_invisible")
async def toggle_invisible_mode(call: CallbackQuery, db_session: AsyncSession):
    tg_id = call.from_user.id
    user = await get_user_by_tg_id(db_session, tg_id)
    if not _is_vip_active(user):
        await call.answer("دسترسی غیرمجاز.", show_alert=True)
        return

    user.invisible_mode = not user.invisible_mode
    await db_session.commit()

    status = "روشن 🟢" if user.invisible_mode else "خاموش 🔴"
    await call.answer(f"حالت مخفی {status} شد.", show_alert=True)

    try:
        await call.message.edit_reply_markup(reply_markup=get_vip_panel_keyboard(user.invisible_mode))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "vip_rematch")
async def rematch_previous_partner(call: CallbackQuery, db_session: AsyncSession):
    tg_id = call.from_user.id
    user = await get_user_by_tg_id(db_session, tg_id)
    
    if not _is_vip_active(user):
        await call.answer("دسترسی غیرمجاز.", show_alert=True)
        return

    from matching_bot_project.database.queries import crud
    if await crud.get_active_match(db_session, tg_id):
        await call.answer("⚠️ شما در حال حاضر در یک چت/دیت فعال هستید! ابتدا آن را پایان دهید.", show_alert=True)
        return

    last_partner_id_str = await redis_client.get(f"user:{tg_id}:last_match_partner")
    if not last_partner_id_str:
        await call.answer("هیچ پارتنر قبلی یافت نشد.", show_alert=True)
        return

    partner_id = int(last_partner_id_str)
    partner = await get_user_by_tg_id(db_session, partner_id)

    if not partner or partner.is_banned:
        await call.answer("❌ کاربر قبلی در حال حاضر در دسترس نیست.", show_alert=True)
        return

    # --- بررسی وضعیت سایلنت برای هر دو کاربر ---
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    
    if user.silent_until and user.silent_until > now_utc:
        await call.answer("🔕 شما در حالت بی‌صدا (سایلنت) هستید. لطفاً ابتدا این حالت را از پروفایل خود غیرفعال کنید.", show_alert=True)
        return

    if partner.silent_until and partner.silent_until > now_utc:
        await call.answer("🔕 کاربر قبلی در حالت بی‌صدا (سایلنت) قرار دارد و در حال حاضر درخواستی دریافت نمی‌کند.", show_alert=True)
        return
    # ------------------------------------------

    # 🌟 فیکس باگ تداخل استیت پارتنر (شناسایی زامبی استیت)
    partner_active_match = await crud.get_active_match(db_session, partner_id)
    if partner_active_match:
        await call.answer("❌ کاربر قبلی در حال حاضر در یک دیت فعال است.", show_alert=True)
        return
        
    # دیتابیس می‌گوید پارتنر آزاد است. اگر ردیس او را گیر کرده نشان می‌دهد، آن را هیل (Heal) می‌کنیم
    partner_state = await redis_client.hget(f"user:state:{partner_id}", "status")
    if partner_state in ["matched", "chatting", b"matched", b"chatting"]:
        await redis_client.delete(f"user:state:{partner_id}")
        # استیت فیک بوده، پس پاکش می‌کنیم و بدون ارور دادن ادامه می‌دیم

    from matching_bot_project.database.models.models import BlockList
    from sqlalchemy import select, or_, and_

    # --- بررسی بلاک بودن دوطرفه ---
    block_check = await db_session.execute(
        select(BlockList).where(
            or_(
                and_(BlockList.blocker_id == tg_id, BlockList.blocked_id == partner_id),
                and_(BlockList.blocker_id == partner_id, BlockList.blocked_id == tg_id)
            )
        )
    )
    if block_check.scalar_one_or_none() is not None:
        await call.answer("❌ کاربر قبلی در دسترس نیست (مسدود شده).", show_alert=True)
        return

    # 🛡️ ارسال کیبورد شیشه‌ای دعوت
    target_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ قبول درخواست", callback_data=f"accept_req_chat_{tg_id}"),
            InlineKeyboardButton(text="❌ رد", callback_data=f"reject_req_chat_{tg_id}")
        ],
        [InlineKeyboardButton(text="👤 پروفایل فرستنده", callback_data=f"view_profile_{tg_id}")]
    ])
    
    try:
        from matching_bot_project.bot.core.loader import bot
        await bot.send_message(
            chat_id=partner_id,
            text="🔔 <b>درخواست اتصال مجدد!</b>\nپارتنر قبلی شما (که کاربر VIP است) درخواست داده تا دوباره با هم صحبت کنید. مایلید؟",
            parse_mode="HTML",
            reply_markup=target_kb
        )
        await call.answer("✅ درخواست اتصال مجدد (رایگان) برای پارتنر قبلی ارسال شد.", show_alert=True)
        try:
            await call.message.edit_text("⏳ درخواست اتصال مجدد (ویژه VIP) ارسال شد. منتظر تایید کاربر باشید...")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Failed to send VIP rematch request to {partner_id}: {e}")
        await call.answer("⚠️ کاربر ربات را مسدود کرده است.", show_alert=True)



# ═══════════════════════════════════════════════════════════════════════════
# VIP subscription purchase flow
# ═══════════════════════════════════════════════════════════════════════════

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from matching_bot_project.bot.core.constants import VIPPlan, Messages
from matching_bot_project.bot.keyboards.inline import get_vip_subscription_plans_keyboard
from matching_bot_project.bot.core.loader import vip_manager


async def show_vip_main_menu(message: Message, db_session: AsyncSession) -> None:
    """v3 NEW: Main menu when user taps 'اکانت VIP (پریمیوم)'."""
    user = await get_user_by_tg_id(db_session, message.from_user.id)
    if not user:
        return await message.answer("ابتدا ثبت‌نام کنید.")

    is_active = _is_vip_active(user)
    if is_active:
        # Show VIP panel
        from datetime import timezone
        expires = user.vip_expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        remaining_days = (expires - now).days if expires else 0

        text = (
            f"⭐ <b>اشتراک VIP شما فعال است</b>\n\n"
            f"📅 تاریخ انقضا: {expires.strftime('%Y-%m-%d %H:%M') if expires else 'نامشخص'}\n"
            f"⏳ روزهای باقی‌مانده: <b>{remaining_days}</b> روز\n\n"
            f"از امکانات زیر استفاده کنید:"
        )
        await message.answer(text, reply_markup=get_vip_panel_keyboard(user.invisible_mode))
        return

    # Not VIP — show subscription plans
    from matching_bot_project.services.vip_subscription import load_vip_plans
    plans = load_vip_plans()
    
    # ساخت متن داینامیک برای تمام پلن‌های موجود
    plans_text = "\n".join(
        f"• {plan['label']}: {plan['price_toman']:,} تومان"
        for code, plan in plans.items()
    )

    text = (
        f"👑 <b>اشتراک VIP (پریمیوم)</b>\n\n"
        f"با خرید اشتراک VIP، تمام امکانات ربات برای شما رایگان می‌شود:\n\n"
        f"✅ مچینگ بدون مصرف سکه\n"
        f"✅ ۱۰ تگ به جای ۳ تگ\n"
        f"✅ ستاره آبی ⭐ کنار نام شما\n"
        f"✅ کامنت‌ها فقط برای VIP‌ها فعال\n"
        f"✅ حالت مخفی + بازدیدکنندگان پروفایل\n"
        f"✅ فیلتر سنی در مچینگ\n"
        f"✅ اتصال مجدد به پارتنر قبلی\n\n"
        f"💎 <b>پلن‌های اشتراک:</b>\n{plans_text}\n\n"
        f"برای خرید، یکی از پلن‌های زیر را انتخاب کنید:"
    )
    # پاس دادن plans به کیبورد
    await message.answer(text, reply_markup=get_vip_subscription_plans_keyboard(plans))


@router.callback_query(F.data.startswith("vip_buy_"))
async def vip_buy_plan(call: CallbackQuery, db_session: AsyncSession, state):
    """User selected a VIP plan — show payment options."""
    plan_code = call.data.replace("vip_buy_", "")
    from matching_bot_project.services.vip_subscription import load_vip_plans
    plans = load_vip_plans()
    if plan_code not in plans:
        return await call.answer("پلن نامعتبر.", show_alert=True)

    plan = plans[plan_code]
    await state.update_data(vip_plan_code=plan_code)

    # Show payment method options
    from matching_bot_project.bot.core.config import settings
    rows = [
        [InlineKeyboardButton(text="💳 کارت به کارت", callback_data=f"vip_pay_card_{plan_code}",
                              style="primary")]
    ]
    if settings.PAYMENT_GATEWAY_ENABLED:
        rows.append([InlineKeyboardButton(text="🔗 پرداخت آنلاین (درگاه)",
                                          callback_data=f"vip_pay_gateway_{plan_code}",
                                          style="success")])
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data="vip_buy_cancel",
                                       style="danger")])

    await call.message.edit_text(
        f"👑 <b>خرید اشتراک VIP</b>\n\n"
        f"پلن انتخابی: <b>{plan['label']}</b>\n"
        f"مدت اعتبار: {plan['duration_days']} روز\n"
        f"💰 مبلغ: <b>{plan['price_toman']:,}</b> تومان\n\n"
        f"روش پرداخت را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await call.answer()

@router.callback_query(F.data.startswith("vip_pay_card_"))
async def vip_pay_card(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    """User chose card-to-card payment for VIP subscription."""
    plan_code = call.data.replace("vip_pay_card_", "")
    from matching_bot_project.services.vip_subscription import load_vip_plans
    from matching_bot_project.services.payment_settings import get_card_info # FIX BUGS-2: Import dynamic card info
    
    plans = load_vip_plans()
    if plan_code not in plans:
        return await call.answer("پلن نامعتبر.", show_alert=True)

    plan = plans[plan_code]
    await state.update_data(vip_plan_code=plan_code, vip_payment_method="card")

    # FIX BUGS-2: Use dynamic card_number and card_holder instead of settings.*
    card_number, card_holder = await get_card_info()

    text = (
        f"💳 <b>پرداخت کارت به کارت</b>\n\n"
        f"مبلغ: <b>{plan['price_toman']:,}</b> تومان\n"
        f"شماره کارت: <code>{card_number}</code>\n"
        f"به نام: <b>{card_holder}</b>\n\n"
        f"پس از واریز، عکس فیش را برای ربات ارسال کنید."
    )
    from matching_bot_project.bot.states.states import VIPStates
    await state.set_state(VIPStates.waiting_for_receipt)
    await call.message.edit_text(text)
    await call.answer()

@router.callback_query(F.data.in_({"vip_buy_cancel", "close_vip_panel"}))
async def vip_buy_cancel(call: CallbackQuery, state: FSMContext):
    """لغو و بستن ایمن پنل‌های VIP"""
    await state.clear()
    try:
        # تلاش برای پاک کردن کامل پیام پنل
        await call.message.delete()
    except TelegramBadRequest:
        # اگر تلگرام اجازه حذف نداد، حداقل کیبورد را برمی‌داریم تا دکمه‌ها غیرفعال شوند
        try:
            await call.message.edit_text("❌ پنل بسته شد.", reply_markup=None)
        except Exception:
            pass
    except Exception:
        pass
    
    await call.answer("بسته شد.", show_alert=False)


@router.callback_query(F.data.startswith("vip_pay_gateway_"))
async def vip_pay_gateway(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    """User chose online gateway payment for VIP subscription."""
    plan_code = call.data.replace("vip_pay_gateway_", "")
    from matching_bot_project.bot.core.config import settings
    
    if not settings.PAYMENT_GATEWAY_ENABLED:
        return await call.answer("درگاه آنلاین در دسترس نیست.", show_alert=True)

    from matching_bot_project.services.vip_subscription import load_vip_plans
    from matching_bot_project.database.models.models import CoinPurchaseOrder
    import json
    
    plans = load_vip_plans()
    if plan_code not in plans:
        return await call.answer("پلن نامعتبر.", show_alert=True)

    plan = plans[plan_code]
    order = CoinPurchaseOrder(
        user_tg_id=call.from_user.id,
        package_id=None,
        payment_method="gateway",
        order_type="vip_subscription",
        order_payload=json.dumps({"plan_code": plan_code, "price_toman": plan["price_toman"]}),
        status="pending",
    )
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    # گرفتن Authority از زرین پال
    from matching_bot_project.services.zarinpal import request_payment, build_payment_redirect_url
    
    callback_url = f"{settings.BASE_URL}{settings.ZARINPAL_CALLBACK_PATH}?order_id={order.id}&type=vip"
    description = f"خرید اشتراک VIP - {plan['label']}"
    
    # اصلاح باگ نوع داده (دریافت Tuple به جای Dictionary)
    success, result = await request_payment(
        amount_toman=plan["price_toman"],
        description=description,
        callback_url=callback_url,
    )

    if not success:
        order.status = "failed"
        await db_session.commit()
        await call.message.edit_text(
            f"❌ خطا در ارتباط با درگاه پرداخت: {result}"
        )
        return await call.answer()

    # ذخیره Authority برای جلوگیری از خطای امنیتی در کال‌بک
    authority = result
    order.gateway_authority = authority
    await db_session.commit()

    # ساخت لینک پرداخت
    pay_url = build_payment_redirect_url(authority)
    
    pay_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 ورود به درگاه پرداخت", url=pay_url)],
    ])

    await call.message.edit_text(
        f"🔗 <b>لینک پرداخت آماده شد.</b>\n\n"
        f"💎 پلن: {plan['label']}\n"
        f"💰 مبلغ: {plan['price_toman']:,} تومان\n\n"
        "برای پرداخت روی دکمه زیر کلیک کنید:",
        reply_markup=pay_kb,
        parse_mode="HTML"
    )
    await call.answer()

@router.message(VIPStates.waiting_for_receipt, F.photo)
async def vip_receipt_uploaded(message: Message, state: FSMContext, db_session: AsyncSession):
    """User uploaded a payment receipt photo for VIP card-to-card payment."""
    data = await state.get_data()
    plan_code = data.get("vip_plan_code")
    if not plan_code:
        await state.clear()
        await message.answer("نشست منقضی شده. لطفاً دوباره از منوی VIP اقدام کنید.")
        return

    from matching_bot_project.services.vip_subscription import load_vip_plans
    from matching_bot_project.database.models.models import CoinPurchaseOrder
    from matching_bot_project.bot.core.config import settings

    plans = load_vip_plans()
    if plan_code not in plans:
        await state.clear()
        await message.answer("پلن انتخاب‌شده دیگر معتبر نیست. لطفاً دوباره تلاش کنید.")
        return

    plan = plans[plan_code]
    receipt_file_id = message.photo[-1].file_id  # highest resolution

    # Create a pending purchase order for admin review.
    # status='pending_receipt' distinguishes this from gateway orders
    # (status='pending') so admin_verify_receipt can find it.
    import json as _json
    order = CoinPurchaseOrder(
        user_tg_id=message.from_user.id,
        order_type="vip_subscription",
        payment_method="card",  # <--- این فیلد اجباری است و اضافه شد
        status="pending_receipt",
        order_payload=_json.dumps({
            "plan_code": plan_code,
            "price_toman": plan.get("price_toman", 0),
            "duration_days": plan.get("duration_days", 7),
            "receipt_photo_file_id": receipt_file_id,
        }),
    )
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    await state.clear()
    await message.answer(
        f"✅ <b>رسید پرداخت شما دریافت شد.</b>\n\n"
        f"🔖 کد پیگیری: <code>{order.id}</code>\n"
        f"⏳ وضعیت: در انتظار تأیید ادمین\n\n"
        f"پس از تأیید، اشتراک VIP شما به‌صورت خودکار فعال می‌شود.",
        parse_mode="HTML",
    )

# Notify admins (best-effort).
    try:
        from matching_bot_project.bot.core.loader import bot
        from matching_bot_project.bot.keyboards.inline import get_admin_receipt_keyboard # ایمپورت اضافه شد
        admin_text = (
            f"📥 <b>رسید VIP جدید</b>\n\n"
            f"👤 کاربر: <code>{message.from_user.id}</code>\n"
            f"🔖 سفارش #{order.id}\n"
            f"💎 پلن: {plan.get('label', plan_code)}\n"
            f"💰 مبلغ: {plan.get('price_toman', 0):,} تومان\n\n"
            f"برای تأیید، از دکمه‌های زیر استفاده کنید:"
        )
        for admin_id in settings.parsed_admin_ids:
            try:
                # دکمه‌های تأیید و رد به پیام ادمین متصل شد
                await bot.send_photo(
                    chat_id=admin_id, 
                    photo=receipt_file_id, 
                    caption=admin_text, 
                    parse_mode="HTML",
                    reply_markup=get_admin_receipt_keyboard(order.id) 
                )
            except Exception:
                pass
    except Exception:
        pass


@router.message(VIPStates.waiting_for_receipt)
async def vip_receipt_non_photo(message: Message, state: FSMContext):
    """User sent something other than a photo while in receipt-upload state."""
    await message.answer(
        "⚠️ لطفاً عکس فیش پرداخت را ارسال کنید.\n\n"
        "برای لغو، /cancel را بفرستید یا دکمه «انصراف» را بزنید."
    )


@router.message(VIPStates.waiting_for_receipt, Command("cancel"))
async def vip_receipt_cancel(message: Message, state: FSMContext):
    """User cancelled the receipt upload."""
    await state.clear()
    from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
    await message.answer("❌ عملیات پرداخت VIP لغو شد.", reply_markup=get_main_menu_keyboard())
