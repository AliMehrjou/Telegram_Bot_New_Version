import logging
from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import redis_client

logger = logging.getLogger(__name__)


def _to_str(val) -> str:
    return val.decode('utf-8') if isinstance(val, bytes) else val


class IsAdminFilter(BaseFilter):
    """Verifies if user belongs to the predefined administration lists."""
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        # FIX HIGH-21: anonymous group admin messages have from_user=None.
        if not event.from_user:
            return False
        return event.from_user.id in settings.parsed_admin_ids


class IsVIPFilter(BaseFilter):
    """
    Checks if user is currently VIP.
    Utilizes Redis to check and fallback to DB states.
    """
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        # FIX HIGH-21
        if not event.from_user:
            return False
        user_id = event.from_user.id

        # FIX HIGH-22 + M-19: on Redis cache miss, fall back to DB so users don't
        # lose VIP access after a Redis restart. Also wrap Redis in try/except so
        # a Redis outage does not silently reject every VIP-gated handler.
        vip_cache_key = f"user:vip_status:{user_id}"
        try:
            cached_status = await redis_client.get(vip_cache_key)
            if cached_status is not None:
                # FIX L-19: handle both str (decode_responses=True) and bytes.
                return _to_str(cached_status) == "1"
        except Exception as e:
            logger.warning("IsVIPFilter: Redis failure for user %s, falling back to DB: %s", user_id, e)

        # DB fallback: open a short-lived session.
        try:
            from matching_bot_project.database.session import async_session_factory
            from matching_bot_project.database.models.models import User
            from sqlalchemy import select
            from datetime import datetime, timezone
            async with async_session_factory() as session:
                user = (await session.execute(
                    select(User).where(User.tg_id == user_id)
                )).scalar_one_or_none()
                if not user:
                    return False
                is_vip = bool(user.is_vip) and (
                    not user.vip_expires_at or user.vip_expires_at > datetime.now(timezone.utc)
                )
                # Repopulate cache (best-effort).
                try:
                    await redis_client.set(vip_cache_key, "1" if is_vip else "0", ex=300)
                except Exception:
                    pass
                return is_vip
        except Exception:
            logger.exception("IsVIPFilter: DB fallback failed")
            return False


class ChatActiveFilter(BaseFilter):
    """
    Verifies if user currently holds an active anonymous chat pairing status in Redis.
    Matches can only transmit anonymized direct messages under this condition.
    """
    async def __call__(self, event: Message) -> bool:
        # FIX HIGH-21
        if not event.from_user:
            return False
        user_id = event.from_user.id
        user_state_key = f"user:state:{user_id}"
        # FIX M-19: wrap Redis in try/except so the filter doesn't raise on Redis outage.
        try:
            status = await redis_client.hget(user_state_key, "status")
        except Exception as e:
            logger.warning("ChatActiveFilter: Redis failure for user %s: %s", user_id, e)
            return False
        # FIX M-20: accept both `matched` and `chatting` (consistent with force_join.py).
        return _to_str(status) in ("matched", "chatting")
