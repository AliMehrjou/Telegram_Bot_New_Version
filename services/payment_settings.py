"""
services/payment_settings.py
"""
import logging
import re
from typing import Optional

from matching_bot_project.bot.core.loader import redis_client
from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)

_CARD_NUMBER_KEY = "bot:payment_card_number"
_CARD_HOLDER_KEY = "bot:payment_card_holder"

# FIX L-27: validate that the card number is 16 digits (Persian/Latin digits accepted).
_CARD_NUMBER_RE = re.compile(r"^[۰-۹0-9]{16}$")


def _decode(value) -> Optional[str]:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value


async def get_card_info() -> tuple[str, str]:
    """
    شماره کارت و نام صاحب حساب فعال رو برمی‌گردونه.
    اول از Redis (اگه ادمین عوض کرده باشه)، وگرنه مقدار پیش‌فرض از .env

    FIX M-09: all Redis calls are wrapped in try/except so that a Redis outage
    falls back to the .env defaults instead of crashing the caller (which is
    typically a payment-flow handler).
    """
    raw_number: Optional[str] = None
    raw_holder: Optional[str] = None
    try:
        raw_number = _decode(await redis_client.get(_CARD_NUMBER_KEY))
        raw_holder = _decode(await redis_client.get(_CARD_HOLDER_KEY))
    except Exception as e:
        logger.warning("get_card_info: Redis failure, falling back to .env defaults: %s", e)

    return (
        raw_number or settings.CARD_NUMBER_FOR_PAYMENT,
        raw_holder or settings.CARD_HOLDER_NAME,
    )


async def set_card_info(card_number: str, card_holder: str) -> None:
    # FIX L-27: validate the card number format before storing it.
    if not isinstance(card_number, str) or not _CARD_NUMBER_RE.match(card_number):
        raise ValueError("شماره کارت باید دقیقاً ۱۶ رقم باشد.")
    try:
        await redis_client.set(_CARD_NUMBER_KEY, card_number)
        await redis_client.set(_CARD_HOLDER_KEY, card_holder)
    except Exception as e:
        logger.error("set_card_info: Redis failure: %s", e)
        raise


async def reset_card_info() -> None:
    """حذف اورراید و بازگشت به مقدار پیش‌فرض .env"""
    try:
        await redis_client.delete(_CARD_NUMBER_KEY, _CARD_HOLDER_KEY)
    except Exception as e:
        logger.error("reset_card_info: Redis failure: %s", e)
        raise


async def is_overridden() -> bool:
    # FIX L-26: check both keys, not just the card-number key.
    try:
        return (await redis_client.exists(_CARD_NUMBER_KEY, _CARD_HOLDER_KEY)) >= 1
    except Exception as e:
        logger.warning("is_overridden: Redis failure: %s", e)
        return False

# در فایل payment_settings.py
# 👈 این تابع را به فایل اضافه کن
def _normalize_persian_digits(text: str) -> str:
    if not text:
        return text
    mapping = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return text.translate(mapping)

async def set_card_info(card_number: str, card_holder: str) -> None:
    # 🛡️ رفع باگ ظاهری: تبدیل اعداد فارسی/عربی کیبورد ادمین به اعداد انگلیسی استاندارد
    if isinstance(card_number, str):
        card_number = _normalize_persian_digits(card_number)
        
    if not isinstance(card_number, str) or not _CARD_NUMBER_RE.match(card_number):
        raise ValueError("شماره کارت باید دقیقاً ۱۶ رقم انگلیسی باشد.")
    try:
        await redis_client.set(_CARD_NUMBER_KEY, card_number)
        await redis_client.set(_CARD_HOLDER_KEY, card_holder)
    except Exception as e:
        logger.error("set_card_info: Redis failure: %s", e)
        raise