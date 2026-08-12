"""
bot/middlewares/anti_spam.py

v3 ADDITIONS:
- Added `LikeRateLimitMiddleware` for the explicit 1-like-per-minute rule.
  This is a separate middleware because the per-user throttle (0.6s) is too
  coarse for likes specifically, and we want a different feedback message.
- Original `ThrottlingMiddleware` is unchanged.
"""

import logging
from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, CallbackQuery, Update, Message
from aiogram.enums import ContentType  # 👈 اضافه شد
from matching_bot_project.bot.core.loader import redis_client
from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)


class ThrottlingMiddleware(BaseMiddleware):
    """Prevents message flood attacks on active bot sessions."""

    def __init__(self, limit: float = None):
        super().__init__()
        # v3: read limit from settings (default 0.6s)
        self.limit = float(limit) if limit is not None else settings.ANTI_SPAM_PER_USER_SECONDS

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)
            
        # 🚀 فیکس فاز چهارم: جلوگیری از اعمال آنتی‌اسپم روی پیام‌های خود ربات
        if user.is_bot:
            return await handler(event, data)

        user_id = user.id
        event_type = "unknown"
        actual_event = event

        if isinstance(event, Update):
            if event.message:
                # 🚀 فیکس فاز چهارم: نادیده گرفتن پیام‌های سیستمی تلگرام
                if getattr(event.message, "content_type", None) in {
                    ContentType.PINNED_MESSAGE, 
                    ContentType.NEW_CHAT_MEMBERS, 
                    ContentType.LEFT_CHAT_MEMBER
                }:
                    return await handler(event, data)
                    
                event_type = "message"
                actual_event = event.message
            elif event.callback_query:
                event_type = "callback"
                actual_event = event.callback_query
            elif event.my_chat_member:
                event_type = "my_chat_member"
                actual_event = event.my_chat_member
            elif event.edited_message:
                event_type = "edited_message"
                actual_event = event.edited_message
        else:
            if isinstance(event, Message):
                if getattr(event, "content_type", None) in {
                    ContentType.PINNED_MESSAGE, 
                    ContentType.NEW_CHAT_MEMBERS, 
                    ContentType.LEFT_CHAT_MEMBER
                }:
                    return await handler(event, data)
            event_type = "callback" if isinstance(event, CallbackQuery) else "message"

        if event_type == "my_chat_member":
            return await handler(event, data)

        cache_key = f"throttling:{user_id}:{event_type}"

        try:
            key_set = await redis_client.set(
                cache_key, "1",
                px=int(self.limit * 1000),
                nx=True
            )
        except Exception as e:
            logger.error("Redis connection failed in ThrottlingMiddleware for user %s: %s", user_id, e)
            return await handler(event, data)

        if not key_set:
            if isinstance(actual_event, CallbackQuery):
                try:
                    await actual_event.answer("⚠️ کمی کندتر!", show_alert=False)
                except Exception:
                    pass
            elif hasattr(actual_event, "answer") and event_type == "message":
                try:
                    await actual_event.answer("یکم یواش تر :) پیامت نرفت دوباره بهش بده")
                except Exception:
                    pass
            return None

        return await handler(event, data)


class LikeRateLimitMiddleware(BaseMiddleware):
    """
    v3 NEW: Enforces 1 like per minute per user.
    Applied only to callback queries whose data starts with "like_user_".
    """

    def __init__(self, cooldown_seconds: int = None):
        super().__init__()
        self.cooldown = cooldown_seconds or settings.LIKE_COOLDOWN_SECONDS

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Only applies to like callbacks
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)
        cb_data = getattr(event, "data", "") or ""
        if not cb_data.startswith("like_user_"):
            return await handler(event, data)

        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        cache_key = f"like_rl:{user.id}"

        try:
            key_set = await redis_client.set(
                cache_key, "1",
                ex=self.cooldown,
                nx=True
            )
        except Exception as e:
            logger.error("Redis failure in LikeRateLimitMiddleware: %s", e)
            return await handler(event, data)

        if not key_set:
            # How long until they can like again?
            try:
                ttl = await redis_client.ttl(cache_key)
                ttl_str = f"{ttl} ثانیه" if ttl > 0 else "کمی صبر"
            except Exception:
                ttl_str = "کمی صبر"
            try:
                await event.answer(
                    f"⚠️ هر دقیقه فقط یک لایک مجاز است. {ttl_str} دیگر دوباره تلاش کنید.",
                    show_alert=True
                )
            except Exception:
                pass
            return None

        return await handler(event, data)