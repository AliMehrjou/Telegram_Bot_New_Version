import logging
import secrets
import time
from fastapi import APIRouter, Request, status, HTTPException, Header
from aiogram.types import Update
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import dp, bot, redis_client
# FIX PHASE4-HIGH-13: wire metrics so /metrics endpoint reports real data.
from matching_bot_project.services.metrics import metrics
from fastapi import Query
from matching_bot_project.bot.core.bot_shard_manager import shard_manager

logger = logging.getLogger(__name__)
# FIX PHASE1-CRIT-01: prefix was "/v1" but config.WEBHOOK_PATH is "/api/v1/webhook"
# and nginx proxies /api/v1/webhook unchanged → FastAPI returned 404 for every
# Telegram update in production. Align router prefix with what Telegram & nginx use.
router = APIRouter(prefix="/api/v1", tags=["Telegram Webhook Feed"])

# FIX PHASE2-SEC-13: brute-force protection on the webhook secret token.
# At nginx's 30 r/s rate limit, an attacker can attempt 2.6M secrets/day per IP.
# This in-app layer blocks any IP that sends > N invalid attempts in a window.
_WEBHOOK_BRUTE_THRESHOLD = 10
_WEBHOOK_BRUTE_WINDOW = 60        # 1 min sliding window
_WEBHOOK_BLOCK_TTL = 1800         # 30 min block
_WEBHOOK_DEDUP_TTL = 86400        # 24h — Telegram update_ids are unique per bot


async def _check_webhook_brute_force(client_ip: str) -> bool:
    """Return True if the IP is currently blocked by webhook brute-force protection."""
    block_key = f"webhook:blocked:{client_ip}"
    try:
        return bool(await redis_client.get(block_key))
    except Exception:
        return False


async def _record_webhook_auth_failure(client_ip: str) -> None:
    """Increment the per-IP auth-failure counter; auto-block on threshold."""
    counter_key = f"webhook:auth_fail:{client_ip}"
    block_key = f"webhook:blocked:{client_ip}"
    try:
        count = await redis_client.incr(counter_key)
        if count == 1:
            await redis_client.expire(counter_key, _WEBHOOK_BRUTE_WINDOW)
        if count > _WEBHOOK_BRUTE_THRESHOLD:
            await redis_client.set(block_key, "1", ex=_WEBHOOK_BLOCK_TTL)
            logger.critical(
                "Webhook brute-force block: IP %s blocked for %ss after %s failed attempts",
                client_ip, _WEBHOOK_BLOCK_TTL, count,
            )
    except Exception:
        pass


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def telegram_webhook_endpoint(
    request: Request,
    shard: int = Query(default=0), # 👈 خواندن ایندکس شارد از Query String
    x_telegram_bot_api_secret_token: str = Header(None)
):
    """
    Acts as the target security receiver for incoming Telegram server updates.
    Feeds the events recursively to aiogram dispatcher via the correct bot shard.
    """
    # بررسی IP برای جلوگیری از حملات Brute-force[cite: 18]
    client_ip = request.client.host if request.client else "unknown"

    if await _check_webhook_brute_force(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
        )

    # 👈 پیدا کردن آبجکت Bot مخصوص همین شارد
    shard_bot = shard_manager.get_bot_by_shard_index(shard)
    if not shard_bot:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"Invalid shard index: {shard}"
        )

    # اعتبارسنجی توکن امنیتی ارسال‌شده توسط سرور تلگرام[cite: 18]
    expected_token = settings.WEBHOOK_SECRET_TOKEN or ""
    received_token = x_telegram_bot_api_secret_token or ""

    if not expected_token:
        logger.critical("WEBHOOK_SECRET_TOKEN is not configured! Rejecting all webhook requests for safety.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook secret token is not configured on the server."
        )

    if not secrets.compare_digest(received_token, expected_token):
        logger.error("Security alert! Ingestion attempted with invalid Telegram Secret Token from IP %s.", client_ip)
        await _record_webhook_auth_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden security token mismatch."
        )

    try:
        update_dict = await request.json()
        
        # 👈 اعتبارسنجی آپدیت تلگرام با استفاده از آبجکت بات اختصاصی همین شارد
        telegram_update = Update.model_validate(update_dict, context={"bot": shard_bot})

        update_id = getattr(telegram_update, "update_id", None)
        if update_id is not None:
            # 👈 کلید Dedup یکتا بر اساس آیدی همین ربات شارد تنظیم می‌شود
            dedup_key = f"webhook:upd:{shard_bot.id}:{update_id}"
            try:
                already_seen = await redis_client.set(dedup_key, "1", nx=True, ex=_WEBHOOK_DEDUP_TTL)
                if not already_seen:
                    logger.info("Duplicate update_id %s from Telegram — skipping (already processed).", update_id)
                    return {"status": "ok", "delivered": False, "deduplicated": True}
            except Exception as dedup_exc:
                logger.warning("Webhook dedup check failed (Redis?): %s — processing anyway.", dedup_exc)

        msg_type = "message"
        if telegram_update.callback_query:
            msg_type = "callback_query"
        elif telegram_update.my_chat_member:
            msg_type = "my_chat_member"
        metrics.record_message_received(msg_type)

        handler_start = time.monotonic()
        
        # 👈 فید کردن آپدیت به دیسپچر با بات اختصاصی همان شارد
        await dp.feed_update(shard_bot, telegram_update)
        
        handler_duration = time.monotonic() - handler_start
        metrics.observe_bot_response("webhook", handler_duration)

        return {"status": "ok", "delivered": True}
        
    except Exception as e:
        logger.exception("Error handling webhook request feed: %s", e)
        # هندلینگ خطاهای تجزیه مدل یا کلیدهای نامعتبر[cite: 18]
        if isinstance(e, (ValueError, KeyError, TypeError)):
            return {"status": "error", "message": "Malformed payload"}
        
        # خطاهای داخلی باید 500 برگردانند تا تلگرام مجدداً درخواست را ارسال کند[cite: 18]
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal handler error"
        )
