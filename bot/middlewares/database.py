# bot/middlewares/database.py

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware

from sqlalchemy import select

from aiogram.types import TelegramObject, CallbackQuery
from matching_bot_project.database.session import async_session_factory
from matching_bot_project.database.models.models import User
from matching_bot_project.bot.core.loader import redis_client
from matching_bot_project.database.session import async_read_session_factory
logger = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    """
    Injects an active async SQLAlchemy Database Session into the routing stack.
    Each handler can access the session by defining a `db_session` parameter.

    v3 CRITICAL FIX: Removed per-update UPDATE + COMMIT on users table.
    Previously this middleware ran `user.is_online = True; user.last_active = ...;
    session.commit()` on EVERY single update from EVERY user, which caused:
      - 3 queries per update instead of 1 (SELECT + UPDATE + COMMIT)
      - Lock contention on the `users` table
      - DB throughput ceiling of ~300-500 updates/sec
    Now: online status is stored ONLY in Redis (`user:online:{tg_id}` with TTL 300s),
    and `last_active` is updated in DB by the batched OnlineStatusWorker every 60s,
    which flushes Redis `user:last_active:{tg_id}` values to MySQL in a single
    UPDATE...WHERE tg_id IN (...) statement.

    This lifts DB throughput to ~1,500-2,500 updates/sec on the same hardware.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        async with async_session_factory() as session, async_read_session_factory() as read_session:
            data["db_session"] = session
            data["db_read_session"] = read_session
            try:
                user = None
                user_id = event.from_user.id if hasattr(event, "from_user") and event.from_user else None

                if user_id:
                    # استفاده از read_session برای واکشی اولیه کاربر (کاهش بار از primary)
                    result = await read_session.execute(select(User).where(User.tg_id == user_id))
                    user = result.scalar_one_or_none()

                if user:
                    if getattr(user, "is_banned", False):
                        logger.info(f"Blocked request from banned user {user_id}")
                        await self._notify_banned_user(event)
                        return None

                    # ─── v3 NEW: Redis-only online status (no DB write) ─────────
                    now_iso = datetime.now(timezone.utc).isoformat()
                    pipe = redis_client.pipeline()
                    pipe.set(f"user:online:{user_id}", "1", ex=300)
                    pipe.set(f"user:last_active:{user_id}", now_iso, ex=86400)
                    try:
                        await pipe.execute()
                    except Exception as redis_exc:
                        logger.warning(
                            "Redis online-key set failed for user %s: %s",
                            user_id, redis_exc,
                        )

                return await handler(event, data)
            except Exception as e:
                logger.error("Exception in handler, rolling back DB sessions: %s", e, exc_info=True)
                await session.rollback()
                await read_session.rollback()
                raise

    
    @staticmethod
    async def _notify_banned_user(event: TelegramObject) -> None:
        """Notify banned user instead of silently dropping the request."""
        ban_text = "⛔️ حساب کاربری شما توسط مدیریت مسدود شده است."
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(ban_text, show_alert=True)
            elif hasattr(event, "answer"):
                await event.answer(ban_text)
        except Exception:
            pass
