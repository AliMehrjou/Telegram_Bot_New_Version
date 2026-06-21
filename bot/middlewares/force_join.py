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
    Enforces subscription to mandatory Telegram channels (dynamic sponsors + default).
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

        # 1.5. Bypass for users in an active match / chat
        try:
            user_state = await redis_client.hget(f"user:state:{user_id}", "status")
            # تبدیل به string برای اطمینان در ردیس
            if user_state in (b"matched", b"chatting", "matched", "chatting"):
                return await handler(event, data)
        except Exception as e:
            logger.warning("Redis HGET failed in ForceJoinMiddleware bypass check for user %s: %s", user_id, e)

        cache_key = f"user:force_join_cache:{user_id}"

        # 2. Safely check Redis cache
        try:
            cached_joined = await redis_client.get(cache_key)
            if cached_joined in ("1", b"1"):
                return await handler(event, data)
        except Exception as e:
            logger.warning("Redis GET failed in ForceJoinMiddleware for user %s: %s", user_id, e)

        # 3. Fetch dynamic sponsors from Redis
        sponsors = {}
        try:
            dynamic_sponsors = await redis_client.hgetall("bot:sponsors")
            if dynamic_sponsors:
                for k, v in dynamic_sponsors.items():
                    sponsors[k.decode('utf-8')] = v.decode('utf-8')
        except Exception as e:
            logger.warning("Redis HGETALL sponsors failed: %s", e)

        # Merge with default channel from settings (Backward Compatibility)
        default_channel = str(getattr(settings, "REQUIRED_CHANNEL_ID", ""))
        default_link = getattr(settings, "CHANNEL_INVITE_LINK", "")
        if default_channel and default_channel not in sponsors:
            sponsors[default_channel] = default_link

        # If absolutely no sponsors are defined, skip middleware
        if not sponsors:
            return await handler(event, data)

        # 4. Check Telegram API for each required channel
        missing_sponsors = {}
        try:
            for channel_id, invite_link in sponsors.items():
                member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
                if member.status not in _ALLOWED_STATUSES:
                    missing_sponsors[channel_id] = invite_link
        except TelegramAPIError as e:
            logger.error("ForceJoin membership lookup failed for user %s: %s", user_id, e)

            error_msg = "⚠️ در حال حاضر بررسی وضعیت عضویت امکان‌پذیر نیست. لطفاً چند دقیقه دیگر مجدداً تلاش کنید."
            if isinstance(event, Message):
                await event.answer(text=error_msg)
            elif isinstance(event, CallbackQuery):
                if event.message:
                    await event.message.answer(text=error_msg)
                else:
                    await bot.send_message(chat_id=user_id, text=error_msg)
                await event.answer("خطا در بررسی عضویت", show_alert=True)

            return None

        # 5. If member of ALL channels, cache it and proceed
        if not missing_sponsors:
            try:
                await redis_client.set(cache_key, "1", ex=600)  # 10 minutes cache
            except Exception as e:
                logger.warning("Redis SET failed in ForceJoinMiddleware for user %s: %s", user_id, e)
            return await handler(event, data)

        # 6. Handle Unauthorized User (Generate dynamic inline keyboard)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        
        count = 1
        for channel_id, link in missing_sponsors.items():
            btn_text = f"📢 عضویت در کانال {count}" if len(missing_sponsors) > 1 else "📢 عضویت در کانال"
            keyboard.inline_keyboard.append([InlineKeyboardButton(text=btn_text, url=link)])
            count += 1
            
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="✅ بررسی عضویت مجدد", callback_data="check_membership")])

        alert_text = (
            "⚠️ *جهت استفاده از ربات، ابتدا باید عضو کانال‌های حامی ما شوید!*\n\n"
            "پس از عضویت در تمامی کانال‌ها از دکمه زیر جهت بررسی مجدد استفاده کنید."
        )

        if isinstance(event, Message):
            await event.answer(text=alert_text, reply_markup=keyboard, parse_mode="Markdown")

        elif isinstance(event, CallbackQuery):
            if event.message:
                try:
                    await event.message.edit_text(text=alert_text, reply_markup=keyboard, parse_mode="Markdown")
                except TelegramAPIError:
                    pass
            else:
                await bot.send_message(chat_id=user_id, text=alert_text, reply_markup=keyboard, parse_mode="Markdown")

            await event.answer("نیاز به تایید عضویت!", show_alert=True)

        return None