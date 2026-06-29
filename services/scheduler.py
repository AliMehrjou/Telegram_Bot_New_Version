# services/scheduler.py

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import update

from matching_bot_project.database.models.models import MatchHistory, User
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ثابت‌های تایمر
# ─────────────────────────────────────────────────────────────────────────────
WARN_AFTER_SECONDS  = 12 * 3600   # ۱۲ ساعت بی‌فعالیت → هشدار
CLOSE_AFTER_SECONDS = 24 * 3600   # ۲۴ ساعت بی‌فعالیت → بستن
POLL_INTERVAL       = 30 * 60     # هر ۳۰ دقیقه یه‌بار چک کن
REDIS_KEY_TTL       = CLOSE_AFTER_SECONDS + 3600  # کمی بیشتر از ۲۴ ساعت


class DatingScheduler:
    """
    مچ‌های فعال رو ردیابی می‌کنه.
    - بعد از ۱۲ ساعت بی‌فعالیت: یه هشدار به هر دو کاربر می‌فرسته (یه‌بار)
    - بعد از ۲۴ ساعت بی‌فعالیت: مچ رو می‌بنده
    هر بار که کاربر جواب بده، تایمر ریست می‌شه.
    """

    def __init__(
        self,
        bot: Bot,
        dp: Dispatcher,
        redis_client: aioredis.Redis,
        session_factory: async_sessionmaker,
    ):
        self.bot = bot
        self.dp = dp
        self.redis = redis_client
        self.session_factory = session_factory
        self._running_task: Optional[asyncio.Task] = None
        self._background_tasks: set[asyncio.Task] = set()

    # ─────────────────────────────────────────────────────────────────────────
    # API عمومی
    # ─────────────────────────────────────────────────────────────────────────

    async def register_match_timeout(
        self,
        match_history_id: int,
        user_one_id: int,
        user_two_id: int,
    ):
        """وقتی مچ جدید شروع میشه صدا زده میشه."""
        key = f"date:timeout:{match_history_id}"
        now_epoch = datetime.now(timezone.utc).timestamp()

        await self.redis.hset(key, mapping={
            "last_activity": str(now_epoch),
            "user_one_id":   str(user_one_id),
            "user_two_id":   str(user_two_id),
            "warned":        "0",   # هنوز هشدار داده نشده
        })
        await self.redis.expire(key, REDIS_KEY_TTL)
        logger.info(f"Match {match_history_id} registered in scheduler (24h timeout).")

    async def update_user_activity(self, match_history_id: int, tg_id: int):
        """هر بار که کاربر جواب داد صدا زده میشه تا تایمر ریست بشه."""
        key = f"date:timeout:{match_history_id}"
        if await self.redis.exists(key):
            now_epoch = datetime.now(timezone.utc).timestamp()
            # تایمر ریست میشه + وضعیت warned هم ریست میشه (چون دوباره فعال شدن)
            await self.redis.hset(key, mapping={
                "last_activity": str(now_epoch),
                "warned":        "0",
            })
            await self.redis.expire(key, REDIS_KEY_TTL)

    # ─────────────────────────────────────────────────────────────────────────
    # حلقه پس‌زمینه
    # ─────────────────────────────────────────────────────────────────────────

    async def verify_timeout_loops(self):
        """هر ۳۰ دقیقه تمام مچ‌های فعال رو چک می‌کنه."""
        while True:
            try:
                async for key in self.redis.scan_iter(match="date:timeout:*", count=100):
                    try:
                        key_str = key.decode() if isinstance(key, bytes) else key
                        match_history_id = int(key_str.split(":")[-1])

                        raw_data = await self.redis.hgetall(key)
                        if not raw_data:
                            continue

                        data = {
                            (k.decode() if isinstance(k, bytes) else k):
                            (v.decode() if isinstance(v, bytes) else v)
                            for k, v in raw_data.items()
                        }

                        last_activity = float(data.get("last_activity", 0))
                        warned        = data.get("warned", "0") == "1"
                        now_epoch     = datetime.now(timezone.utc).timestamp()
                        idle_seconds  = now_epoch - last_activity

                        if idle_seconds >= CLOSE_AFTER_SECONDS:
                            # ── بستن مچ ──────────────────────────────────
                            deleted = await self.redis.delete(key_str)
                            if deleted == 0:
                                continue  # یه worker دیگه قبلاً گرفته

                            task = asyncio.create_task(
                                self.close_inactive_date(match_history_id, key_str, data)
                            )
                            self._background_tasks.add(task)
                            task.add_done_callback(self._background_tasks.discard)

                        elif idle_seconds >= WARN_AFTER_SECONDS and not warned:
                            # ── هشدار (فقط یه‌بار) ────────────────────────
                            await self.redis.hset(key_str, "warned", "1")

                            task = asyncio.create_task(
                                self._send_warning(match_history_id, data)
                            )
                            self._background_tasks.add(task)
                            task.add_done_callback(self._background_tasks.discard)

                    except Exception as e:
                        logger.error(f"Error checking timeout key {key}: {e}")

            except Exception as e:
                logger.error(f"Global exception in scheduling check loop: {e}")

            await asyncio.sleep(POLL_INTERVAL)

    # ─────────────────────────────────────────────────────────────────────────
    # هشدار ۱۲ ساعته
    # ─────────────────────────────────────────────────────────────────────────

    async def _send_warning(self, match_id: int, data: Dict[str, str]):
        """به هر دو نفر می‌گه ۱۲ ساعت دیگه مچ بسته میشه."""
        partners = self._parse_partners(data)
        if not partners:
            return

        for user_id in partners:
            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "⚠️ *یادآوری مچ فعال*\n\n"
                        "شما یک سوال بی‌پاسخ دارید که بیش از ۱۲ ساعت از آن گذشته.\n"
                        "اگر تا ۱۲ ساعت دیگر پاسخ ندهید، مکالمه به‌طور خودکار بسته خواهد شد.\n\n"
                        "برای ادامه مکالمه به ربات برگردید. 🙂"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Failed to send 12h warning to user {user_id}: {e}")

        logger.info(f"Sent 12h warning for match {match_id}.")

    # ─────────────────────────────────────────────────────────────────────────
    # بستن مچ ۲۴ ساعته
    # ─────────────────────────────────────────────────────────────────────────

    async def close_inactive_date(self, match_id: int, redis_key: str, data: Dict[str, str]):
        partners = self._parse_partners(data)
        if not partners:
            logger.error(f"Missing partner IDs for match {match_id}. Skipping close.")
            return

        # غیرفعال کردن در دیتابیس
        try:
            async with self.session_factory() as session:
                match_row = await session.get(MatchHistory, match_id)
                if match_row:
                    match_row.is_active = False
                    await session.commit()
                else:
                    logger.warning(f"No MatchHistory row for match_id {match_id}.")
        except Exception as e:
            logger.error(f"Failed to deactivate match {match_id} in DB: {e}")

        # پاک‌سازی state و اطلاع به کاربرها
        for user_id in partners:
            await self.redis.delete(f"user:state:{user_id}")

            try:
                context = FSMContext(
                    storage=self.dp.storage,
                    key=StorageKey(bot_id=self.bot.id, chat_id=user_id, user_id=user_id)
                )
                await context.clear()
            except Exception as e:
                logger.error(f"Failed to clear FSM state for user {user_id}: {e}")

            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "⏳ *مکالمه به پایان رسید*\n\n"
                        "به دلیل عدم پاسخ‌دهی در ۲۴ ساعت گذشته، این مچ بسته شد.\n"
                        "برای شروع مچ جدید از دکمه 🎯 در منوی اصلی استفاده کنید."
                    ),
                    parse_mode="Markdown",
                    reply_markup=get_main_menu_keyboard(),
                )
            except Exception as e:
                logger.warning(f"Failed to send close notification to user {user_id}: {e}")

        # پاک‌سازی کلیدهای وابسته (redis_key خودش قبلاً حذف شده)
        await self.redis.delete(f"match:questions:{match_id}")
        await self.redis.delete(f"match:current_q_index:{match_id}")

        logger.info(f"Closed inactive match {match_id} after 24h inactivity.")

    # ─────────────────────────────────────────────────────────────────────────
    # کمکی
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_partners(data: Dict[str, str]) -> list[int]:
        try:
            return [int(data["user_one_id"]), int(data["user_two_id"])]
        except (KeyError, ValueError):
            return []

    def start_polling(self):
        if not self._running_task or self._running_task.done():
            self._running_task = asyncio.create_task(self.verify_timeout_loops())
            logger.info("Dating Scheduler started (24h timeout, 12h warning).")


# ─────────────────────────────────────────────────────────────────────────────
# Online Status Worker
# ─────────────────────────────────────────────────────────────────────────────

class OnlineStatusWorker:
    """
    Background worker that periodically checks for users who are marked as online
    but whose last activity was more than 5 minutes ago, and sets them offline.
    """

    def __init__(self, session_factory: async_sessionmaker, idle_minutes: int = 5):
        self.session_factory = session_factory
        self.idle_minutes = idle_minutes
        self._running_task: Optional[asyncio.Task] = None

    async def sync_offline_users(self):
        """Runs every 60 seconds to clean up stale online statuses."""
        while True:
            try:
                async with self.session_factory() as session:
                    cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=self.idle_minutes)

                    stmt = (
                        update(User)
                        .where(User.is_online.is_(True))
                        .where(User.last_active < cutoff_time)
                        .values(is_online=False)
                    )

                    result = await session.execute(stmt)
                    if result.rowcount > 0:
                        await session.commit()
                        logger.info(f"Offline sync: {result.rowcount} users set to offline.")

            except Exception as e:
                logger.error(f"Error in offline sync loop: {e}")

            await asyncio.sleep(60)

    def start_polling(self):
        if not self._running_task or self._running_task.done():
            self._running_task = asyncio.create_task(self.sync_offline_users())
            logger.info("Online Status Worker started.")