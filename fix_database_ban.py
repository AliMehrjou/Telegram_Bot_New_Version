import re

file_path = 'bot/middlewares/database.py'

with open(file_path, 'r') as f:
    content = f.read()

# We need to correctly inject the ban check before executing handler
# Let's fix it manually

new_content = """import logging
from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, CallbackQuery
from matching_bot_project.database.session import async_session_factory

logger = logging.getLogger(__name__)

class DbSessionMiddleware(BaseMiddleware):
    \"\"\"
    Injects an active async SQLAlchemy Database Session into the routing stack.
    Each handler can access the session by defining a `db_session` parameter.
    \"\"\"
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        async with async_session_factory() as session:
            data["db_session"] = session
            try:
                # Update user online status
                from matching_bot_project.database.models.models import User
                from sqlalchemy import select
                from datetime import datetime

                user_id = None
                if hasattr(event, "from_user") and event.from_user:
                    user_id = event.from_user.id

                if user_id:
                    result = await session.execute(select(User).where(User.tg_id == user_id))
                    user = result.scalar_one_or_none()
                    if user:
                        if getattr(user, 'is_banned', False):
                            if isinstance(event, CallbackQuery):
                                await event.answer("حساب شما مسدود شده است.", show_alert=True)
                            return None

                        user.is_online = True
                        user.last_active = datetime.utcnow()
                        await session.commit()

                # Handlers are strictly responsible for their own session.commit()
                return await handler(event, data)
            except Exception as e:
                logger.error("Exception in handler, rolling back DB session: %s", e, exc_info=True)
                await session.rollback()
                raise
"""
with open(file_path, 'w') as f:
    f.write(new_content)

print("Fixed ban check in database middleware")
