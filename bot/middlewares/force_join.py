import asyncio
import logging
from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from aiogram.exceptions import TelegramAPIError
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import redis_client, bot
from matching_bot_project.database.queries import crud
from aiogram.fsm.context import FSMContext

logger = logging.getLogger(__name__)

my_chat_member_router = Router()

_ALLOWED_STATUSES = {"creator", "administrator", "member", "restricted"}
_PROMPT_COOLDOWN_SECONDS = 20


def _to_str(val) -> str:
    return val.decode('utf-8') if isinstance(val, bytes) else val


class ForceJoinMiddleware(BaseMiddleware):
    """
    Enforces subscription to mandatory Telegram channels.
    Caches successful checks and broken channels in Redis to reduce Telegram API calls.
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
        try:
            is_banned = await redis_client.exists(f"user:banned:{user_id}")
            if is_banned:
                if isinstance(event, CallbackQuery):
                    await event.answer("⛔️ حساب کاربری شما مسدود شده است.", show_alert=True)
                return None  
        except Exception:
            pass

        # ── Capture Deep Links (Referral & Anon) ──────────────────
        if isinstance(event, Message) and event.text:
            if event.text.startswith("/start ref_"):
                try:
                    ref_id = event.text.split("_", 1)[1]
                    await redis_client.setex(f"pending_ref:{user_id}", 3600, ref_id)
                except Exception:
                    pass
            elif event.text.startswith("/start anon_"):
                try:
                    target_public_id = event.text.split("_", 1)[1]
                    await redis_client.setex(f"pending_anon:{user_id}", 3600, target_public_id)
                except Exception:
                    pass
                
                # دور زدن عضویت اجباری در لحظه ورود با لینک ناشناس
                return await handler(event, data)
        # ─────────────────────────────────────────

        # 1. Admin bypass
        if user_id in settings.parsed_admin_ids:
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.data == "check_membership":
            return await handler(event, data)

        # 2. Bypass برای کاربران در پروسه فعال (چت، مچینگ، ارسال پیام ناشناس)
        try:
            is_pipeline = False
            state: FSMContext = data.get("state")
            if state:
                current_state = await state.get_state()
                if current_state:
                    current_state_lower = current_state.lower()
                    if any(phase in current_state_lower for phase in ["chat", "matching", "questionnaire"]):
                        is_pipeline = True
                    # 👈 حل مشکل فورس جوین پیام‌های ناشناس: وقتی کاربر در حال تایپ پیام است
                    elif "anonymouslinkstates" in current_state_lower:
                        return await handler(event, data)

            if not is_pipeline:
                user_state = await redis_client.hget(f"user:state:{user_id}", "status")
                if user_state is not None and _to_str(user_state) in ("matched", "chatting", "in_chat", "in_date"):
                    is_pipeline = True

            if is_pipeline:
                db_session = data.get("db_session")
                if db_session:
                    active_match = await crud.get_active_match(db_session, user_id)
                    if active_match:
                        return await handler(event, data)
        except Exception as e:
            logger.warning("Bypass check failed for %s (forcing check): %s", user_id, e)

        # 3. دریافت اسپانسرهای داینامیک
        sponsors: dict[str, str] = {}
        try:
            dynamic_sponsors = await redis_client.hgetall("bot:sponsors")
            if dynamic_sponsors:
                for k, v in dynamic_sponsors.items():
                    sponsors[_to_str(k)] = _to_str(v)
        except Exception:
            pass

        default_channel = str(getattr(settings, "REQUIRED_CHANNEL_ID", ""))
        default_link = getattr(settings, "CHANNEL_INVITE_LINK", "")
        if default_channel and default_channel not in sponsors:
            sponsors[default_channel] = default_link

        if not sponsors:
            return await handler(event, data)

        # 4. بررسی کش
        try:
            sponsors_version = await redis_client.get("bot:sponsors_version") or "0"
            sponsors_version = _to_str(sponsors_version)
        except Exception:
            sponsors_version = "0"

        cache_key = f"user:force_join:{user_id}:v{sponsors_version}"

        try:
            cached_joined = await redis_client.get(cache_key)
            if cached_joined is not None and _to_str(cached_joined) == "1":
                return await handler(event, data)
        except Exception:
            pass

        # 5. بررسی زنده با Telegram API
        missing_sponsors: dict[str, str] = {}
        broken_channels: list[str] = []

        channel_ids = list(sponsors.keys())
        bad_channel_keys = [f"bot:bad_sponsor:{cid}" for cid in channel_ids]
        try:
            bad_channel_flags = await asyncio.gather(
                *[redis_client.exists(k) for k in bad_channel_keys],
                return_exceptions=True,
            )
        except Exception:
            bad_channel_flags = [False] * len(channel_ids)

        already_known_broken = {
            channel_id
            for channel_id, flag in zip(channel_ids, bad_channel_flags)
            if flag and not isinstance(flag, Exception)
        }

        async def _check_one(channel_id: str, invite_link: str):
            try:
                cid = int(channel_id)
            except ValueError:
                cid = channel_id
            if channel_id in already_known_broken:
                return ("broken", channel_id, invite_link)
            try:
                member = await bot.get_chat_member(chat_id=cid, user_id=user_id)
            except TelegramAPIError:
                try:
                    await redis_client.setex(f"bot:bad_sponsor:{cid}", 900, "1")
                except Exception:
                    pass
                return ("broken", channel_id, invite_link)
            if member.status not in _ALLOWED_STATUSES:
                return ("missing", channel_id, invite_link)
            return ("ok", channel_id, invite_link)

        results = await asyncio.gather(
            *[_check_one(cid, link) for cid, link in sponsors.items()],
            return_exceptions=False,
        )
        for status, channel_id, invite_link in results:
            if status == "broken":
                broken_channels.append(channel_id)
            elif status == "missing":
                missing_sponsors[channel_id] = invite_link

        if broken_channels and len(broken_channels) == len(sponsors):
            return await handler(event, data)

        if not missing_sponsors:
            try:
                await redis_client.set(cache_key, "1", ex=300)
            except Exception:
                pass
            return await handler(event, data)

        # 6. نمایش دکمه‌های عضویت
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for count, (channel_id, link) in enumerate(missing_sponsors.items(), 1):
            btn_text = f"📢 عضویت در کانال {count}" if len(missing_sponsors) > 1 else "📢 عضویت در کانال"
            keyboard.inline_keyboard.append([InlineKeyboardButton(text=btn_text, url=link)])

        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="✅ بررسی عضویت مجدد", callback_data="check_membership")
        ])

        alert_text = (
            "⚠️ <b>جهت استفاده از ربات، ابتدا باید عضو کانال‌های حامی ما شوید!</b>\n\n"
            "پس از عضویت در تمامی کانال‌ها از دکمه زیر جهت بررسی مجدد استفاده کنید."
        )

        try:
            prompt_guard_key = f"user:force_join_prompted:{user_id}"
            already_prompted = bool(await redis_client.exists(prompt_guard_key))

            if not already_prompted:
                await bot.send_message(chat_id=user_id, text=alert_text, reply_markup=keyboard, parse_mode="HTML")
                try:
                    await redis_client.setex(prompt_guard_key, _PROMPT_COOLDOWN_SECONDS, "1")
                except Exception:
                    pass
            
            if isinstance(event, CallbackQuery):
                await event.answer("⚠️ نیاز به تایید عضویت!", show_alert=True)
                
        except TelegramAPIError as e:
            logger.error(f"Failed to send force-join message to {user_id}: {e}")

        return None

@my_chat_member_router.my_chat_member()
async def on_chat_member_updated(event: ChatMemberUpdated):
    try:
        new_status = event.new_chat_member.status
        if new_status not in ("left", "kicked"):
            if new_status == "restricted":
                member = event.new_chat_member
                if getattr(member, "is_member", True):
                    return  
            else:
                return  

        user_id = event.from_user.id
        if not user_id:
            return

        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(
                cursor=cursor,
                match=f"user:force_join:{user_id}:v*",
                count=100,
            )
            if keys:
                await redis_client.delete(*keys)
                logger.info(
                    "Force-join cache invalidated for user %s (status → %s in chat %s)",
                    user_id, new_status, event.chat.id,
                )
            if cursor == 0:
                break
    except Exception:
        logger.exception("Error in my_chat_member handler — cache invalidation skipped")