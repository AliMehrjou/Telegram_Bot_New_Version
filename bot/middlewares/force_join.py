import logging
from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import redis_client, bot

logger = logging.getLogger(__name__)

_ALLOWED_STATUSES = {"creator", "administrator", "member"}

class ForceJoinMiddleware(BaseMiddleware):
    """
    Enforces subscription to a mandatory Telegram channel.
    Caches successful checks in Redis (10 minutes TTL) to reduce Telegram API calls.
    """
    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        if not event.from_user:
            return await handler(event, data)

        user_id = event.from_user.id

        # 1. Admin bypass check first (zero external calls)
        if user_id in settings.parsed_admin_ids:
            return await handler(event, data)

        cache_key = f"user:force_join_cache:{user_id}"

        # 2. Safely check Redis cache
        try:
            cached_joined = await redis_client.get(cache_key)
            # Handle both string and byte responses depending on redis-py configuration
            if cached_joined in ("1", b"1"):
                return await handler(event, data)
        except Exception as e:
            logger.warning("Redis GET failed in ForceJoinMiddleware for user %s: %s", user_id, e)
            # Do not return here; fall through to the Telegram API check

        # 3. Fallback to Telegram API
        try:
            member = await bot.get_chat_member(
                chat_id=settings.REQUIRED_CHANNEL_ID,
                user_id=user_id
            )

            if member.status in _ALLOWED_STATUSES:
                try:
                    await redis_client.set(cache_key, "1", ex=600)
                except Exception as e:
                    logger.warning("Redis SET failed in ForceJoinMiddleware for user %s: %s", user_id, e)
                return await handler(event, data)

        except TelegramAPIError as e:
            logger.error("ForceJoin membership lookup failed for user %s: %s", user_id, e)
            # Fail open if the channel ID is invalid or bot was kicked
            return await handler(event, data)

        # 4. Handle Unauthorized User
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 عضویت در کانال", url=settings.CHANNEL_INVITE_LINK)],
            [InlineKeyboardButton(text="✅ بررسی عضویت مجدد", callback_data="check_membership")]
        ])

        alert_text = (
            "⚠️ *جهت استفاده از ربات همسریابی، ابتدا باید عضو کانال پشتیبانی ما شوید!*\n\n"
            "پس از عضویت در کانال از دکمه زیر جهت فعالسازی ربات استفاده کنید."
        )

        if isinstance(event, Message):
            await event.answer(text=alert_text, reply_markup=keyboard, parse_mode="Markdown")
        
        elif isinstance(event, CallbackQuery):
            # Guard against InaccessibleMessage exceptions on old inline keyboards
            if event.message:
                await event.message.answer(text=alert_text, reply_markup=keyboard, parse_mode="Markdown")
            else:
                await bot.send_message(chat_id=user_id, text=alert_text, reply_markup=keyboard, parse_mode="Markdown")
            
            await event.answer("نیاز به تایید عضویت!", show_alert=True)

        return None