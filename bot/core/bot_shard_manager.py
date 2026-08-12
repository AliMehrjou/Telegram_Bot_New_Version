"""
bot/core/bot_shard_manager.py

v3.1 SCALING: Multi-bot sharding for scaling beyond Telegram's 30 msg/sec limit.

How it works:
1. Admin creates N bot accounts via @BotFather (e.g. 5-10 bots).
2. Each bot token is added to BOT_SHARD_TOKENS env var (comma-separated).
3. Each user is permanently assigned to one bot via `tg_id % num_shards`.
4. The user interacts only with their assigned bot.
5. Webhook URL for each bot: /api/v1/webhook?shard={index}
6. When sending outbound messages, we look up the user's shard and use
   the corresponding bot instance.

This gives us N×30 msg/sec aggregate throughput. For 200K users:
- 5 bots → 150 msg/sec
- 10 bots → 300 msg/sec

Memory cost: ~10MB per bot instance (aiogram session + connection pool).
For 10 bots = ~100MB extra RAM, which is negligible.

Webhook setup:
- Each bot must be registered with Telegram via setWebhook with its own URL.
- URL pattern: https://yourdomain.com/api/v1/webhook?shard=0
                https://yourdomain.com/api/v1/webhook?shard=1
                ...
"""

import logging
from typing import Optional
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from sqlalchemy import update
from matching_bot_project.database.session import async_session_factory
from matching_bot_project.database.models.models import User

from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)


class BotShardManager:
    """
    Manages multiple Bot instances for horizontal scaling.
    Falls back to single-bot mode if BOT_SHARD_TOKENS is not set.
    """

    def __init__(self):
        self._bots: list[Bot] = []
        self._initialized = False

    def initialize(self) -> None:
        """Initialize all bot instances from settings. Idempotent."""
        if self._initialized:
            return

        tokens = settings.parsed_bot_shard_tokens
        if not tokens:
            # Single-bot mode — use BOT_TOKEN
            logger.info("BotShardManager: single-bot mode (BOT_SHARD_TOKENS not set).")
            self._bots = []  # Will fall back to legacy `bot` instance
            self._initialized = True
            return

        # Multi-bot mode
        proxy_url = settings.PROXY_URL
        for idx, token in enumerate(tokens):
            try:
                if proxy_url:
                    session = AiohttpSession(proxy=proxy_url)
                    bot = Bot(
                        token=token,
                        session=session,
                        default=DefaultBotProperties(parse_mode="HTML"),
                    )
                else:
                    bot = Bot(
                        token=token,
                        default=DefaultBotProperties(parse_mode="HTML"),
                    )
                self._bots.append(bot)
                logger.info(f"BotShardManager: initialized shard #{idx}.")
            except Exception as e:
                logger.error(f"BotShardManager: failed to init shard #{idx}: {e}")
                raise

        logger.info(f"BotShardManager: {len(self._bots)} shards initialized.")
        self._initialized = True

    def get_shard_index_for_user(self, tg_id: int) -> int:
        """
        Deterministically route a user to a shard.
        Same user → same shard always (so user always talks to the same bot).
        """
        n = settings.num_bot_shards
        if n <= 1:
            return 0
        return tg_id % n

    async def get_bot_for_user_async(self, tg_id: int) -> Bot:
        """
        Get the Bot instance responsible for the given user by checking the DB.
        If shard_index is NULL (old user), calculate it, save it, and return.
        """
        if not self._bots:
            from matching_bot_project.bot.core.loader import bot as _legacy_bot
            return _legacy_bot

        from matching_bot_project.database.session import async_session_factory
        from matching_bot_project.database.models.models import User
        from sqlalchemy import select, update

        # ابتدا شارد را از دیتابیس می‌خوانیم
        async with async_session_factory() as session:
            result = await session.execute(select(User.shard_index).where(User.tg_id == tg_id))
            shard_idx = result.scalar_one_or_none()

            # اگر رکورد وجود داشت ولی NULL بود (کاربر قدیمی قبل از Migration)
            if shard_idx is None:
                shard_idx = self.get_shard_index_for_user(tg_id) # محاسبه live
                # بلافاصله در دیتابیس آپدیت و ذخیره می‌کنیم تا در دفعات بعد ثابت بماند
                await session.execute(
                    update(User).where(User.tg_id == tg_id).values(shard_index=shard_idx)
                )
                await session.commit()

        # اگر کاربر در دیتابیس نبود (مثلا ربات رو استارت نکرده و فقط ما بهش پیام میدیم)
        # روی فرمول live فال‌بک می‌کنیم ولی به صورت ایده آل نباید به اینجا برسد.
        if shard_idx is None:
            shard_idx = self.get_shard_index_for_user(tg_id)

        # اگر عدد از رنج خارج شده بود (مثلا شاردها کم شدن) باید کلمپ کنیم
        if shard_idx >= len(self._bots):
             shard_idx = shard_idx % len(self._bots)

        return self._bots[shard_idx]
    def get_bot_by_shard_index(self, shard_idx: int) -> Optional[Bot]:
        """Get bot by shard index (used by webhook handler)."""
        if not self._bots:
            from matching_bot_project.bot.core.loader import bot as _legacy_bot
            return _legacy_bot
        if 0 <= shard_idx < len(self._bots):
            return self._bots[shard_idx]
        return None

    @property
    def num_shards(self) -> int:
        return len(self._bots) if self._bots else 1

    @property
    def is_sharded(self) -> bool:
        return len(self._bots) > 1

    async def send_message(self, tg_id: int, text: str, **kwargs):
        bot = await self.get_bot_for_user_async(tg_id) # 👈 استفاده از تابع جدید
        return await bot.send_message(chat_id=tg_id, text=text, **kwargs)

    async def send_photo(self, tg_id: int, photo: str, **kwargs):
        bot = await self.get_bot_for_user_async(tg_id) # 👈 استفاده از تابع جدید
        return await bot.send_photo(chat_id=tg_id, photo=photo, **kwargs)

    async def close_all(self) -> None:
        """Close all bot sessions on shutdown."""
        for bot in self._bots:
            try:
                await bot.session.close()
            except Exception as e:
                logger.warning(f"Error closing bot session: {e}")
        self._bots = []
        self._initialized = False
        logger.info("BotShardManager: all bot sessions closed.")


# Singleton
shard_manager = BotShardManager()
