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
                        await self._notify_banned_user(event)
                        return None

                    # آپدیت زمان آخرین فعالیت و وضعیت آنلاین
                    # باگ فیکس شد: حذف NX چون باعث می‌شد در ۵ دقیقه آخرین بازدید آپدیت نشه
                    redis_key = f"user:online:{user_id}"
                    await redis_client.set(redis_key, "1", ex=300)

                    user.is_online = True
                    user.last_active = datetime.now(timezone.utc).replace(tzinfo=None)

                    # 💡 commit این بخش جدا و ایزوله شده تا خطای آن
                    # کل پردازش پیام کاربر را متوقف نکند.
                    try:
                        await session.commit()
                    except Exception as commit_exc:
                        logger.warning(
                            "Failed to persist online-status for user %s (non-fatal): %s",
                            user_id, commit_exc,
                        )
                        await session.rollback()
                        # آزاد کردن کلید ردیس تا تلاش بعدی دوباره امتحان کند
                        try:
                            await redis_client.delete(redis_key)
                        except Exception:
                            pass

                # Handlers are strictly responsible for their own session.commit()
                return await handler(event, data)
            except Exception as e:
                logger.error("Exception in handler, rolling back DB session: %s", e, exc_info=True)
                await session.rollback()
                raise

    @staticmethod
    async def _notify_banned_user(event: TelegramObject) -> None:
        """
        اطلاع‌رسانی به کاربر بن‌شده به‌جای drop کامل و بی‌صدای ریکوئست.
        برای CallbackQuery از answer (alert) و برای پیام از answer متنی استفاده می‌شود.
        تمام خطاها بی‌صدا نادیده گرفته می‌شوند تا این نوتیفیکیشن خودش باعث کرش نشود.
        """
        ban_text = "⛔️ حساب کاربری شما توسط مدیریت مسدود شده است."
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(ban_text, show_alert=True)
            elif hasattr(event, "answer"):
                await event.answer(ban_text)
        except Exception:
            pass