import logging
from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from matching_bot_project.database.session import async_session_factory

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
                from matching_bot_project.database.models.models import User
                from sqlalchemy import select
                from datetime import datetime

                user_id = event.from_user.id if hasattr(event, "from_user") and event.from_user else None

                if user_id:
                    result = await session.execute(select(User).where(User.tg_id == user_id))
                    user = result.scalar_one_or_none()
                    
                    if user:
                        if getattr(user, "is_banned", False):
                            logger.info(f"Blocked request from banned user {user_id}")
                            return None

                        redis_client = data.get("redis") 
                        redis_key = f"user:online:{user_id}"

                        # Update DB state and set Redis TTL only if the cache key has expired
                        if redis_client and not await redis_client.exists(redis_key):
                            user.is_online = True
                            user.last_active = datetime.utcnow()
                            await redis_client.setex(redis_key, 300, "1")
                            
                            # session.commit() removed. The user object is now marked as 'dirty' 
                            # in SQLAlchemy's unit of work and will only flush/commit if the 
                            # downstream handler explicitly commits the session.

                # Handlers are strictly responsible for their own session.commit()
                return await handler(event, data)
            except Exception as e:
                logger.error("Exception in handler, rolling back DB session: %s", e, exc_info=True)
                await session.rollback()
                raise