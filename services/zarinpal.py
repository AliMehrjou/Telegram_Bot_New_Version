"""
services/zarinpal.py
──────────────────────────────────────────────────────────────────────────────
لایه‌ی ارتباط با REST API v4 زرین‌پال (Request / Verify / StartPay).
──────────────────────────────────────────────────────────────────────────────
"""
import logging
from typing import Optional

import aiohttp

from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)

_PRODUCTION_REQUEST_URL  = "https://payment.zarinpal.com/pg/v4/payment/request.json"
_PRODUCTION_VERIFY_URL   = "https://payment.zarinpal.com/pg/v4/payment/verify.json"
_PRODUCTION_STARTPAY_URL = "https://payment.zarinpal.com/pg/StartPay/{authority}"

_SANDBOX_REQUEST_URL  = "https://sandbox.zarinpal.com/pg/v4/payment/request.json"
_SANDBOX_VERIFY_URL   = "https://sandbox.zarinpal.com/pg/v4/payment/verify.json"
_SANDBOX_STARTPAY_URL = "https://sandbox.zarinpal.com/pg/StartPay/{authority}"

_TIMEOUT = aiohttp.ClientTimeout(total=15)


def _urls() -> tuple[str, str, str]:
    if settings.ZARINPAL_SANDBOX:
        return _SANDBOX_REQUEST_URL, _SANDBOX_VERIFY_URL, _SANDBOX_STARTPAY_URL
    return _PRODUCTION_REQUEST_URL, _PRODUCTION_VERIFY_URL, _PRODUCTION_STARTPAY_URL


def _extract_error(body: dict) -> str:
    errors = body.get("errors") or []
    if isinstance(errors, dict):
        return errors.get("message", "خطای نامشخص از درگاه")
    return str(errors) if errors else "خطای نامشخص از درگاه"


async def request_payment(
    amount_toman: int,
    description: str,
    callback_url: str,
    mobile: Optional[str] = None,
) -> tuple[bool, str]:
    """
    از زرین‌پال Authority می‌گیره.
    خروجی: (success, authority_or_error_message)
    """
    request_url, _, _ = _urls()
    # FIX L-29: cast to int so float amounts don't leak through to Zarinpal.
    amount_rial = int(amount_toman) * 10
    payload = {
        "merchant_id": settings.ZARINPAL_MERCHANT_ID,
        "amount": amount_rial,
        "description": description,
        "callback_url": callback_url,
    }
    if mobile:
        payload["metadata"] = {"mobile": mobile}

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(request_url, json=payload) as resp:
                body = await resp.json()
    except Exception as e:
        logger.error(f"Zarinpal request_payment network error: {e}")
        return False, "خطای شبکه در ارتباط با درگاه پرداخت"

    # FIX M-08: guard against non-dict JSON responses.
    if not isinstance(body, dict):
        logger.error("Zarinpal request_payment returned non-dict JSON: %r", body)
        return False, "پاسخ نامعتبر از درگاه"

    data = body.get("data") or {}
    if data.get("code") == 100 and data.get("authority"):
        return True, data["authority"]

    logger.error(f"Zarinpal request_payment rejected: {body}")
    return False, _extract_error(body)


def build_payment_redirect_url(authority: str) -> str:
    _, _, startpay_url = _urls()
    return startpay_url.format(authority=authority)


async def verify_payment(amount_toman: int, authority: str) -> tuple[bool, str, Optional[str], Optional[int]]:
    """
    خروجی: (success, message, ref_id, code)
    code == 100 یعنی تایید تازه، code == 101 یعنی قبلاً تایید شده بود
    (idempotent — بازم باید موفق در نظر گرفته بشه، فقط دقت کن دوباره سکه شارژ نکن).
    """
    # FIX HIGH-17 (validation): validate inputs before making the network call.
    if not authority or not isinstance(authority, str) or len(authority) != 36 or not authority.startswith("A"):
        logger.warning("verify_payment: invalid authority format: %r", authority)
        return False, "Authority نامعتبر", None, None
    if not isinstance(amount_toman, (int, float)) or amount_toman <= 0:
        logger.warning("verify_payment: non-positive amount: %r", amount_toman)
        return False, "مبلغ نامعتبر", None, None

    _, verify_url, _ = _urls()
    # FIX L-29
    amount_rial = int(amount_toman) * 10
    payload = {
        "merchant_id": settings.ZARINPAL_MERCHANT_ID,
        "amount": amount_rial,
        "authority": authority,
    }

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(verify_url, json=payload) as resp:
                body = await resp.json()
    except Exception as e:
        logger.error(f"Zarinpal verify_payment network error: {e}")
        return False, "خطای شبکه در تایید پرداخت", None, None

    # FIX M-08: guard against non-dict JSON responses.
    if not isinstance(body, dict):
        logger.error("Zarinpal verify_payment returned non-dict JSON: %r", body)
        return False, "پاسخ نامعتبر از درگاه", None, None

    data = body.get("data") or {}
    code = data.get("code")

    if code in (100, 101):
        # FIX M-07: `data.get("ref_id", "")` returns None when key exists with null value,
        # which then becomes the literal string "None". Use `or ""` instead.
        ref_id_raw = data.get("ref_id")
        ref_id = str(ref_id_raw) if ref_id_raw is not None else ""
        return True, "verified", ref_id, code

    logger.error(f"Zarinpal verify_payment rejected: {body}")
    return False, _extract_error(body), None, code
