import logging
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot
from sqlalchemy import select, and_, func
from matching_bot_project.database.models.models import User

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self, bot: Bot, session_factory, redis_client):
        self.bot = bot
        self.session_factory = session_factory
        self.redis = redis_client

    async def send_inactivity_reminders(self) -> None:
        """Query users inactive for 3+ days, send reminder"""
        try:
            now = datetime.utcnow()
            cutoff = now - timedelta(days=3)

            async with self.session_factory() as session:
                stmt = select(User.tg_id).where(User.last_active < cutoff)
                result = await session.execute(stmt)
                inactive_users = [row[0] for row in result.all()]

            for tg_id in inactive_users:
                redis_key = f"user:{tg_id}:notified_inactive"
                already = await self.redis.get(redis_key)
                if not already:
                    try:
                        await self.bot.send_message(
                            chat_id=tg_id,
                            text="👀 دلمون برات تنگ شده! بیا یه دیت جدید شروع کن 💘"
                        )
                        await self.redis.set(redis_key, "1", ex=86400 * 7) # 7 days
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Inactivity reminders error: {e}")

    async def send_online_alerts(self) -> None:
        """For users online now, find users from same province also online"""
        try:
            async with self.session_factory() as session:
                # Assuming is_online is reasonably accurate or we check last_active within 15 mins
                now = datetime.utcnow()
                cutoff = now - timedelta(minutes=15)

                stmt = select(User.province, func.count(User.id)).where(
                    and_(
                        User.last_active > cutoff,
                        User.province != None,
                        User.invisible_mode == False
                    )
                ).group_by(User.province)

                res = await session.execute(stmt)
                province_counts = {row[0]: row[1] for row in res.all() if row[1] > 5}

                if not province_counts:
                    return

                # Find users in these provinces
                provinces = list(province_counts.keys())
                stmt2 = select(User.tg_id, User.province).where(User.province.in_(provinces))
                res2 = await session.execute(stmt2)

                for tg_id, province in res2.all():
                    redis_key = f"user:{tg_id}:online_alert"
                    already = await self.redis.get(redis_key)
                    if not already:
                        count = province_counts[province]
                        try:
                            await self.bot.send_message(
                                chat_id=tg_id,
                                text=f"🔥 الان {count} نفر از استان شما آنلاینن!"
                            )
                            await self.redis.set(redis_key, "1", ex=3600 * 6) # 6h TTL
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Online alerts error: {e}")

    async def run_all(self) -> None:
        await asyncio.gather(
            self.send_inactivity_reminders(),
            self.send_online_alerts(),
        )
