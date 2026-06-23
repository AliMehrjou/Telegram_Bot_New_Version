# bot/middlewares/database.py

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware

from sqlalchemy import select

from aiogram.types import TelegramObject
from matching_bot_project.database.session import async_session_factory
from matching_bot_project.database.models.models import User

logger = logging.getLogger(__name__)

class DbSessionMiddleware(BaseMiddleware):
    """
    Injects an active async SQLAlchemy Database Session into the routing stack.
    Each handler can access the session by defining a `db_session` parameter.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        async with async_session_factory() as session:
            data["db_session"] = session
            try:
                user_id = event.from_user.id if hasattr(event, "from_user") and event.from_user else None

                if user_id:
                    result = await session.execute(select(User).where(User.tg_id == user_id))
                    user = result.scalar_one_or_none()

                    if user:
                        if getattr(user, "is_banned", False):
                            logger.info(f"Blocked request from banned user {user_id}")
                            return None

                        redis_client = data.get("redis")
                        if redis_client:
                            redis_key = f"user:online:{user_id}"

                            # Update DB state and set Redis TTL only if the cache key has expired
                            if not await redis_client.exists(redis_key):
                                user.is_online = True
                                user.last_active = datetime.now(timezone.utc)
                                await redis_client.setex(redis_key, 300, "1")

                                # FIXED: Send the change to DB without concluding the transaction
                                # so downstream handlers still govern the commit/rollback logic.
                                await session.flush()

                # Handlers are strictly responsible for their own session.commit()
                return await handler(event, data)
            except Exception as e:
                logger.error("Exception in handler, rolling back DB session: %s", e, exc_info=True)
                await session.rollback()
                raise