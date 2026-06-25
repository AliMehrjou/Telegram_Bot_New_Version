import logging
from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import redis_client, bot

logger = logging.getLogger(__name__)

_ALLOWED_STATUSES = {"creator", "administrator", "member"}


def _to_str(val) -> str:
    """تبدیل bytes یا str به str — سازگار با هر دو حالت decode_responses."""
    return val.decode('utf-8') if isinstance(val, bytes) else val


class ForceJoinMiddleware(BaseMiddleware):
    """
    Enforces subscription to mandatory Telegram channels.
    Caches successful checks in Redis to reduce Telegram API calls.
    Cache key includes sponsors_version — invalidated automatically
    whenever admin adds/removes a sponsor channel.
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

        # ── Capture Referral ID before blocking the user ──────────────────
        if isinstance(event, Message) and event.text and event.text.startswith("/start ref_"):
            try:
                ref_id = event.text.split("_", 1)[1]
                await redis_client.setex(f"pending_ref:{user_id}", 3600, ref_id)
            except IndexError:
                pass
        # ──────────────────────────────────────────────────────────────────

        # 1. Admin bypass
        if user_id in settings.parsed_admin_ids:
            return await handler(event, data)

        # 2. Bypass برای کاربرانی که وسط چت یا دیت هستن
        try:
            user_state = await redis_client.hget(f"user:state:{user_id}", "status")
            if user_state is not None and _to_str(user_state) in ("matched", "chatting"):
                return await handler(event, data)
        except Exception as e:
            logger.warning("Redis HGET user state failed for %s: %s", user_id, e)

        # 3. دریافت اسپانسرهای داینامیک از Redis
        sponsors: dict[str, str] = {}
        try:
            dynamic_sponsors = await redis_client.hgetall("bot:sponsors")
            if dynamic_sponsors:
                for k, v in dynamic_sponsors.items():
                    sponsors[_to_str(k)] = _to_str(v)
        except Exception as e:
            logger.warning("Redis HGETALL sponsors failed: %s", e)

        default_channel = str(getattr(settings, "REQUIRED_CHANNEL_ID", ""))
        default_link = getattr(settings, "CHANNEL_INVITE_LINK", "")
        if default_channel and default_channel not in sponsors:
            sponsors[default_channel] = default_link

        if not sponsors:
            return await handler(event, data)

        # 4. دریافت version اسپانسرها برای cache key
        # هر بار ادمین اسپانسر اضافه/حذف کنه، version عوض میشه → کش همه invalid میشه
        try:
            sponsors_version = await redis_client.get("bot:sponsors_version") or "0"
            sponsors_version = _to_str(sponsors_version)
        except Exception:
            sponsors_version = "0"

        cache_key = f"user:force_join:{user_id}:v{sponsors_version}"

        # 5. چک کش Redis
        try:
            cached_joined = await redis_client.get(cache_key)
            if cached_joined is not None and _to_str(cached_joined) == "1":
                return await handler(event, data)
        except Exception as e:
            logger.warning("Redis GET failed for user %s: %s", user_id, e)

        # 6. چک Telegram API برای هر کانال
        # نکته مهم: خطای هر کانال باید مجزا handle شه، وگرنه یک کانال خراب
        # (مثلاً ربات از ادمینی خارج شده یا آیدی نامعتبره) کل ربات رو برای همه قفل می‌کنه.
        missing_sponsors: dict[str, str] = {}
        broken_channels: list[str] = []
        for channel_id, invite_link in sponsors.items():
            try:
                cid = int(channel_id)
            except ValueError:
                cid = channel_id

            try:
                member = await bot.get_chat_member(chat_id=cid, user_id=user_id)
            except TelegramAPIError as e:
                logger.error("ForceJoin lookup failed for channel %s (user %s): %s", channel_id, user_id, e)
                broken_channels.append(channel_id)
                continue

            if member.status not in _ALLOWED_STATUSES:
                missing_sponsors[channel_id] = invite_link

        # اگه همه‌ی کانال‌ها خراب بودن (هیچ کدوم قابل چک نبودن)، کاربر رو بلاک نکن —
        # فقط به کاربر اطلاع بده و اجازه بده از ربات استفاده کنه تا ادمین مشکل رو حل کنه.
        if broken_channels and len(broken_channels) == len(sponsors):
            logger.error("All sponsor channels unreachable, bypassing force-join check.")
            return await handler(event, data)

        # 7. اگه عضو همه کانال‌هاست → کش کن و ادامه بده (TTL: 5 دقیقه)
        if not missing_sponsors:
            try:
                await redis_client.set(cache_key, "1", ex=300)
            except Exception:
                pass
            return await handler(event, data)

        # 8. کاربر عضو نیست → نمایش دکمه‌های عضویت
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for count, (channel_id, link) in enumerate(missing_sponsors.items(), 1):
            btn_text = f"📢 عضویت در کانال {count}" if len(missing_sponsors) > 1 else "📢 عضویت در کانال"
            keyboard.inline_keyboard.append([InlineKeyboardButton(text=btn_text, url=link)])

        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="✅ بررسی عضویت مجدد", callback_data="check_membership")
        ])

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