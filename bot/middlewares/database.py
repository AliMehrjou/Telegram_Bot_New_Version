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
                # Handlers are strictly responsible for their own session.commit()
                return await handler(event, data)
            except Exception as e:
                logger.error("Exception in handler, rolling back DB session: %s", e, exc_info=True)
                await session.rollback()
                raise