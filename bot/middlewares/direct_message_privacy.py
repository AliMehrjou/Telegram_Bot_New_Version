"""
bot/middlewares/direct_message_privacy.py

v3 NEW: Implements the privacy layer required by the client's spec:
  - When user A is in active chat/date with user B, and a notification
    arrives (like, direct message, tag-view callback), the partner B
    must NOT see any sign of it.
  - When user A views user C's profile via tag-tap while in chat with B,
    B must NOT be notified.

This middleware checks (via Redis) whether the calling user is in an
active chat/date. If they are, it sets `data["is_in_active_chat"] = True`
and `data["active_chat_partner_id"] = <partner_tg_id>` so handlers can
decide to silently route the notification differently (e.g. suppress
sending to the partner).

It also exposes a helper for handlers to check whether a target user is
currently in any active chat/date — so a direct-message notification is
queued instead of pushed.
"""

import logging
from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, CallbackQuery
from matching_bot_project.bot.core.loader import redis_client

logger = logging.getLogger(__name__)


class DirectMessagePrivacyMiddleware(BaseMiddleware):
    """
    v3 NEW: Annotates the handler data with active-chat context so handlers
    can suppress notifications that would otherwise leak to the chat partner.
    """

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
        try:
            state_json = await redis_client.hgetall(f"user:state:{user_id}")
        except Exception as e:
            logger.warning("DM-privacy: Redis failure for user %s: %s", user_id, e)
            state_json = {}

        status = state_json.get("status") if state_json else None
        partner_id_str = state_json.get("matched_with") if state_json else None

        if status in ("chatting", "matched") and partner_id_str:
            try:
                partner_id = int(partner_id_str)
            except (ValueError, TypeError):
                partner_id = None
            data["is_in_active_chat"] = True
            data["active_chat_partner_id"] = partner_id
        else:
            data["is_in_active_chat"] = False
            data["active_chat_partner_id"] = None

        return await handler(event, data)


async def is_user_in_active_chat(user_id: int) -> bool:
    """Check if a user is currently in an active chat/date (Redis-based)."""
    try:
        state_json = await redis_client.hgetall(f"user:state:{user_id}")
        if not state_json:
            return False
        status = state_json.get("status")
        return status in ("chatting", "matched")
    except Exception as e:
        logger.warning("is_user_in_active_chat Redis failure: %s", e)
        return False


async def get_active_chat_partner(user_id: int) -> int | None:
    """Return the partner's tg_id if the user is in an active chat/date, else None."""
    try:
        state_json = await redis_client.hgetall(f"user:state:{user_id}")
        if not state_json:
            return None
        status = state_json.get("status")
        partner_str = state_json.get("matched_with")
        if status in ("chatting", "matched") and partner_str:
            try:
                return int(partner_str)
            except (ValueError, TypeError):
                return None
        return None
    except Exception as e:
        logger.warning("get_active_chat_partner Redis failure: %s", e)
        return None
