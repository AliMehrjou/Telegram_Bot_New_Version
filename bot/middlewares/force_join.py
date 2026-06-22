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
    Enforces subscription to mandatory Telegram channels.
    Caches successful checks in Redis to reduce Telegram API calls.
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

        # ── Capture Referral ID before blocking the user ────────────
        if isinstance(event, Message) and event.text and event.text.startswith("/start ref_"):
            try:
                ref_id = event.text.split("_", 1)[1]
                await redis_client.setex(f"pending_ref:{user_id}", 3600, ref_id)
            except IndexError:
                pass
        # ─────────────────────────────────────────────────────────────────

        # 1. Admin bypass check first
        if user_id in settings.parsed_admin_ids:
            return await handler(event, data)

        # 2. Bypass for users in an active match / chat
        try:
            user_state = await redis_client.hget(f"user:state:{user_id}", "status")
            if user_state in (b"matched", b"chatting", "matched", "chatting"):
                return await handler(event, data)
        except Exception as e:
            pass

        # 3. Fetch dynamic sponsors from Redis FIRST
        sponsors = {}
        try:
            dynamic_sponsors = await redis_client.hgetall("bot:sponsors")
            if dynamic_sponsors:
                for k, v in dynamic_sponsors.items():
                    sponsors[k.decode('utf-8')] = v.decode('utf-8')
        except Exception as e:
            logger.warning("Redis HGETALL sponsors failed: %s", e)

        default_channel = str(getattr(settings, "REQUIRED_CHANNEL_ID", ""))
        default_link = getattr(settings, "CHANNEL_INVITE_LINK", "")
        if default_channel and default_channel not in sponsors:
            sponsors[default_channel] = default_link

        if not sponsors:
            return await handler(event, data)

        # 4. Generate a unique signature for the current sponsor list
        # با این کار اگر کانال جدیدی اضافه شود، کش همه کاربران فوراً باطل می‌شود
        sponsors_signature = "_".join(sorted(sponsors.keys()))
        cache_key = f"user:force_join:{user_id}:{sponsors_signature}"

        # 5. Safely check Redis cache
        try:
            cached_joined = await redis_client.get(cache_key)
            if cached_joined in ("1", b"1"):
                return await handler(event, data)
        except Exception as e:
            logger.warning("Redis GET failed for user %s: %s", user_id, e)

        # 6. Check Telegram API for each required channel
        missing_sponsors = {}
        try:
            for channel_id, invite_link in sponsors.items():
                
                try:
                    cid = int(channel_id)
                except ValueError:
                    cid = channel_id

                member = await bot.get_chat_member(chat_id=cid, user_id=user_id)
                if member.status not in _ALLOWED_STATUSES:
                    missing_sponsors[channel_id] = invite_link
        except TelegramAPIError as e:
            logger.error("ForceJoin lookup failed for %s: %s", user_id, e)
            error_msg = "⚠️ در حال حاضر بررسی وضعیت عضویت امکان‌پذیر نیست. لطفاً چند دقیقه دیگر تلاش کنید."
            if isinstance(event, Message):
                await event.answer(text=error_msg)
            elif isinstance(event, CallbackQuery):
                if event.message:
                    await event.message.answer(text=error_msg)
                else:
                    await bot.send_message(chat_id=user_id, text=error_msg)
                await event.answer("خطا در بررسی", show_alert=True)
            return None

        # 7. If member of ALL channels, cache it and proceed (reduced TTL to 60s)
        if not missing_sponsors:
            try:
                await redis_client.set(cache_key, "1", ex=60)  # زمان کش به ۱ دقیقه کاهش یافت
            except Exception as e:
                pass
            return await handler(event, data)

        # 8. Handle Unauthorized User
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