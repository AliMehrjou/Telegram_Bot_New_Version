import logging
from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from matching_bot_project.bot.core.loader import redis_client

logger = logging.getLogger(__name__)

class ThrottlingMiddleware(BaseMiddleware):
    """
    Prevents message flood attacks on active bot sessions.
    Locks operations for 1.5 seconds per user on a Redis cache register.
    """
    def __init__(self, limit: float = 1.5):
        super().__init__()
        self.limit = limit

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        if not event.from_user:
            return await handler(event, data)

        user_id = event.from_user.id
        cache_key = f"throttling:{user_id}"

        try:
            # Atomic lock to prevent race conditions
            key_set = await redis_client.set(
                cache_key,
                "1",
                px=int(self.limit * 1000),
                nx=True 
            )
        except Exception as e:
            logger.error("Redis connection failed in ThrottlingMiddleware for user %s: %s", user_id, e)
            # Fail open: Better to temporarily allow spam than drop all bot traffic during a Redis blip
            return await handler(event, data)

        if not key_set:
            # Key already existed — user is sending too fast
            if isinstance(event, CallbackQuery):
                await event.answer("⚠️ لطفا اسپم نکنید! کمی صبور باشید.", show_alert=True)
            return None

        return await handler(event, data)