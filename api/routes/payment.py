"""
api/routes/payment.py
──────────────────────────────────────────────────────────────────────────────
Callback زرین‌پال. کاربر بعد از پرداخت (موفق یا ناموفق) از سمت درگاه به این
آدرس ریدایرکت می‌شه. مراحل:
  1. سفارش با order_id پیدا می‌شه (با SELECT FOR UPDATE تا race با callback
     هم‌زمان دیگه ممکن نباشه).
  2. Authority برگشتی با Authority ذخیره‌شده روی سفارش تطبیق داده می‌شه (ضد دستکاری).
  3. با زرین‌پال verify می‌شه.
  4. اگه موفق بود، سکه شارژ و به کاربر توی تلگرام پیام داده می‌شه.
  5. یه صفحه‌ی HTML ساده به کاربر (توی مرورگر) نشون داده می‌شه.
──────────────────────────────────────────────────────────────────────────────
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.session import get_db_session
from matching_bot_project.database.models.models import CoinPackage, CoinPurchaseOrder
from matching_bot_project.database.queries import crud
from matching_bot_project.bot.core.loader import bot, redis_client
from matching_bot_project.bot.core.config import settings
from matching_bot_project.services import zarinpal
# FIX PHASE4-HIGH-13: wire metrics so /metrics endpoint reports real data.
from matching_bot_project.services.metrics import metrics

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/payment", tags=["Payment Gateway Callback"])

# FIX PHASE2-SEC-09: brute-force protection on the callback endpoint.
# If the same IP sends > 20 callback requests within 5 minutes, block further
# callbacks from that IP for 15 minutes. Prevents abuse of the idempotency
# guard (the `code != 101` branch) to fingerprint valid order_ids.
_BRUTE_FAIL_THRESHOLD = 20
_BRUTE_FAIL_WINDOW = 300        # 5 min
_BRUTE_BLOCK_TTL = 900          # 15 min

def _get_client_ip(request: Request) -> str:
    """استخراج آی‌پی واقعی کلاینت از پشت Nginx / Cloudflare"""
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip.strip()
        
    return request.client.host if request.client else "unknown"

async def _check_callback_brute_force(request: Request) -> bool:
    """Return True if the caller is currently blocked by brute-force protection."""
    client_ip = _get_client_ip(request)
    block_key = f"pay:callback:blocked:{client_ip}"
    try:
        return bool(await redis_client.get(block_key))
    except Exception:
        return False

async def _record_callback_attempt(request: Request) -> None:
    """Increment the per-IP callback-attempt counter; auto-block on threshold."""
    client_ip = _get_client_ip(request)
    counter_key = f"pay:callback:attempts:{client_ip}"
    block_key = f"pay:callback:blocked:{client_ip}"
    try:
        count = await redis_client.incr(counter_key)
        if count == 1:
            await redis_client.expire(counter_key, _BRUTE_FAIL_WINDOW)
        if count > _BRUTE_FAIL_THRESHOLD:
            await redis_client.set(block_key, "1", ex=_BRUTE_BLOCK_TTL)
            logger.warning("Brute-force block: IP %s blocked for %ss", client_ip, _BRUTE_BLOCK_TTL)
    except Exception:
        pass

def _result_page(title: str, message: str, success: bool) -> str:
    color = "#16a34a" if success else "#dc2626"
    icon = "✅" if success else "❌"
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{ font-family: Tahoma, sans-serif; background:#0f172a; color:#f1f5f9;
                display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
        .card {{ background:#1e293b; padding:32px 40px; border-radius:16px; text-align:center; max-width:360px; }}
        .icon {{ font-size:48px; margin-bottom:12px; }}
        h1 {{ color:{color}; font-size:20px; margin:0 0 8px; }}
        p {{ color:#94a3b8; font-size:14px; line-height:1.8; }}
        a {{ display:inline-block; margin-top:20px; background:#2563eb; color:#fff;
             padding:10px 24px; border-radius:8px; text-decoration:none; font-size:14px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">{icon}</div>
        <h1>{title}</h1>
        <p>{message}</p>
        <a href="https://t.me/{settings.BOT_USERNAME}">بازگشت به ربات</a>
    </div>
</body>
</html>"""

@router.get("/callback", response_class=HTMLResponse)
async def zarinpal_callback(
    request: Request,
    order_id: int = Query(...),
    Authority: str = Query(default=""),
    Status: str = Query(default=""),
    type: str = Query(default="coins"),
    db: AsyncSession = Depends(get_db_session),
):
    if await _check_callback_brute_force(request):
        return _result_page(
            "دسترسی مسدود شده",
            "تعداد درخواست‌های نامعتبر از این IP بیش از حد مجاز بوده است. بعداً دوباره تلاش کنید.",
            False,
        )
    await _record_callback_attempt(request)

    result = await db.execute(
        select(CoinPurchaseOrder)
        .where(CoinPurchaseOrder.id == order_id)
        .with_for_update()
    )
    order = result.scalar_one_or_none()

    if not order:
        return _result_page("سفارش یافت نشد", "این لینک پرداخت معتبر نیست یا منقضی شده است.", False)

    if order.status != "pending":
        already_ok = order.status == "approved"
        return _result_page(
            "پرداخت قبلاً پردازش شده",
            "نتیجه‌ی این سفارش قبلاً برای شما در تلگرام ارسال شده است.",
            already_ok,
        )

    if not Authority or Authority != order.gateway_authority:
        order.status = "failed"
        await db.commit()
        logger.warning(f"Authority mismatch for order {order_id}: got={Authority!r}")
        return _result_page("خطای امنیتی", "اطلاعات بازگشتی از درگاه معتبر نبود.", False)

    if Status != "OK":
        order.status = "rejected"
        await db.commit()
        return _result_page("پرداخت لغو شد", "پرداخت در درگاه لغو یا ناتمام ماند.", False)

    import json as _json
    order_type = getattr(order, "order_type", "coins") or "coins"
    payload = {}
    if getattr(order, "order_payload", None):
        try:
            payload = _json.loads(order.order_payload)
        except Exception:
            payload = {}

    package = None
    if order_type == "coins":
        package = await db.get(CoinPackage, order.package_id)
        if not package:
            order.status = "failed"
            await db.commit()
            return _result_page("خطای سیستمی", "بسته‌ی مربوط به این سفارش یافت نشد. با پشتیبانی تماس بگیرید.", False)

    if order_type == "coins":
        verify_amount = payload.get("price_toman_snapshot") or package.price_toman
    elif order_type == "vip_subscription":
        verify_amount = payload.get("price_toman", 0)
        if not verify_amount:
            order.status = "failed"
            await db.commit()
            return _result_page("خطای سیستمی", "اطلاعات سفارش ناقص است.", False)
    elif order_type == "gift_purchase":
        verify_amount = payload.get("price_toman", 0)
        if not verify_amount:
            order.status = "failed"
            await db.commit()
            return _result_page("خطای سیستمی", "اطلاعات سفارش گیفت ناقص است.", False)
    else:
        order.status = "failed"
        await db.commit()
        return _result_page("خطای سیستمی", f"نوع سفارش ناشناخته یا پشتیبانی نمی‌شود: {order_type}", False)

    try:
        verified, msg, ref_id, code = await zarinpal.verify_payment(
            amount_toman=verify_amount,
            authority=Authority,
        )
    except Exception as verify_exc:
        logger.exception("Zarinpal verify call failed for order %s: %s", order_id, verify_exc)
        return _result_page(
            "خطای موقت",
            "ارتباط با درگاه پرداخت برقرار نشد. لطفاً چند لحظه بعد دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.",
            False,
        )

    if not verified:
        order.status = "rejected"
        await db.commit()
        logger.error(f"Zarinpal verify failed for order {order_id} (code={code}): {msg}")
        metrics.record_payment(pay_type=order_type, status="rejected")
        return _result_page("پرداخت ناموفق", "درگاه پرداخت را تایید نکرد. مبلغی کسر نشده است.", False)

    target_user = await crud.get_user_by_tg_id(db, order.user_tg_id)
    if not target_user:
        order.status = "failed"
        order.resolved_at = datetime.now(timezone.utc)
        await db.commit()
        logger.error("Order %s approved by gateway but user %s not found in DB", order_id, order.user_tg_id)
        return _result_page(
            "خطای سیستمی",
            "پرداخت شما دریافت شد اما حساب کاربری یافت نشد. لطفاً با پشتیبانی تماس بگیرید.",
            False,
        )

    success_text = ""
    if order_type == "coins":
        await crud.process_coin_transaction(
            session=db,
            user=target_user,
            amount=package.coin_amount,
            description=f"خرید آنلاین بسته {package.coin_amount} سکه‌ای (سفارش {order.id})",
            ignore_multiplier=False,
        )
        try:
            from matching_bot_project.bot.core.loader import referral_engine
            await referral_engine.process_commission_on_purchase(
                db, order.id, target_user.tg_id, package.coin_amount
            )
        except Exception as e:
            logger.warning("Referral commission failed for order %s: %s", order.id, e)
        success_text = f"{package.coin_amount} سکه به حساب شما اضافه شد."
        
    elif order_type == "vip_subscription":
        try:
            from matching_bot_project.bot.core.loader import vip_manager
            plan_code = payload.get("plan_code")
            await vip_manager.activate_subscription(
                db, target_user.tg_id, plan_code, payment_order_id=order.id
            )
            from matching_bot_project.bot.core.constants import VIPPlan
            duration = VIPPlan.DURATION_DAYS.get(plan_code, 7)
            success_text = f"اشتراک VIP شما به مدت {duration} روز فعال شد!"
        except Exception as e:
            logger.error("VIP activation failed for order %s: %s", order.id, e)
            success_text = "اشتراک VIP شما فعال شد."
            
    elif order_type == "gift_purchase":
        try:
            from matching_bot_project.bot.core.loader import gift_engine
            gift_code = payload.get("gift_code")
            quantity = payload.get("quantity", 1)
            
            success, msg = await gift_engine.credit_gift(
                session=db,
                buyer_tg_id=target_user.tg_id,
                gift_code=gift_code,
                quantity=quantity,
                payment_order_id=order.id
            )
            if success:
                success_text = msg
            else:
                logger.error("Gift credit failed for order %s: %s", order.id, msg)
                success_text = "پرداخت موفق بود اما در سیستم تخصیص گیفت خطایی رخ داد. لطفاً با پشتیبانی تماس بگیرید."
        except Exception as e:
            logger.error("Gift purchase activation failed for order %s: %s", order.id, e)
            success_text = "پرداخت تأیید شد اما در تخصیص گیفت خطایی رخ داد."

    order.status = "approved"
    order.resolved_at = datetime.now(timezone.utc)
    await db.commit()

    metrics.record_payment(pay_type=order_type, status="approved")

    try:
        await bot.send_message(
            chat_id=target_user.tg_id,
            text=(
                "🎉 <b>پرداخت شما با موفقیت انجام شد!</b>\n\n"
                f"✅ {success_text}\n"
                f"🔖 کد پیگیری: <code>{ref_id}</code>"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    return _result_page_with_csp("پرداخت موفق", f"{success_text} کد پیگیری: {ref_id}", True)

def _result_page_with_csp(title: str, message: str, success: bool) -> HTMLResponse:
    """Wrapper that returns the result page with CSP + X-Frame-Options headers."""
    html = _result_page(title, message, success)
    return HTMLResponse(
        content=html,
        headers={
            "Content-Security-Policy": "frame-ancestors 'none'; default-src 'self'; style-src 'unsafe-inline'",
            "X-Frame-Options": "DENY",
        },
    )

def _result_page_with_csp(title: str, message: str, success: bool) -> HTMLResponse:
    """Wrapper that returns the result page with CSP + X-Frame-Options headers."""
    html = _result_page(title, message, success)
    return HTMLResponse(
        content=html,
        headers={
            "Content-Security-Policy": "frame-ancestors 'none'; default-src 'self'; style-src 'unsafe-inline'",
            "X-Frame-Options": "DENY",
        },
    )
