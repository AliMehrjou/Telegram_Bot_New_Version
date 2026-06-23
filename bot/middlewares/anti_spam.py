import logging
from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, CallbackQuery, Update
from matching_bot_project.bot.core.loader import redis_client

logger = logging.getLogger(__name__)

class ThrottlingMiddleware(BaseMiddleware):
    """
    Prevents message flood attacks on active bot sessions.
    Locks operations temporarily per user on a Redis cache register.
    """
    
    def __init__(self, limit: float = 0.6):
        super().__init__()
        self.limit = limit

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        user_id = user.id
        
        event_type = "unknown"
        actual_event = event
        
        if isinstance(event, Update):
            if event.message:
                event_type = "message"
                actual_event = event.message
            elif event.callback_query:
                event_type = "callback"
                actual_event = event.callback_query
        else:
            event_type = "callback" if isinstance(event, CallbackQuery) else "message"

        
        cache_key = f"throttling:{user_id}:{event_type}"

        try:
            
            key_set = await redis_client.set(
                cache_key,
                "1",
                px=int(self.limit * 1000),
                nx=True 
            )
        except Exception as e:
            logger.error("Redis connection failed in ThrottlingMiddleware for user %s: %s", user_id, e)
            # Fail open
            return await handler(event, data)

        if not key_set:
            
            if isinstance(actual_event, CallbackQuery):
                try:
                    
                    await actual_event.answer("⚠️ کمی کندتر!", show_alert=False)
                except Exception:
                    
                    pass 
            return None

        return await handler(event, data)