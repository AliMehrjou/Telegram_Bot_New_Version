import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder

from redis.asyncio import Redis

from matching_bot_project.bot.core.config import settings
from matching_bot_project.services.matching_engine import MatchingEngine
from matching_bot_project.services.scheduler import DatingScheduler
from matching_bot_project.database.session import (
    async_session_factory as session_factory_instance
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# =====================================================
# Telegram Bot Session
# =====================================================

proxy_url = getattr(settings, "PROXY_URL", None)

if proxy_url:
    logger.info(f"Using Telegram proxy: {proxy_url}")

    session = AiohttpSession(
        proxy=proxy_url
    )

    bot = Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(
            parse_mode="HTML"
        )
    )
else:
    logger.warning(
        "No PROXY_URL configured. Telegram requests will use direct connection."
    )

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode="HTML"
        )
    )

# =====================================================
# Redis Client
# =====================================================

redis_client = Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    decode_responses=True
)

# =====================================================
# FSM Storage
# =====================================================

fsm_storage = RedisStorage(
    redis=redis_client,
    key_builder=DefaultKeyBuilder(
        with_destiny=True
    )
)

# =====================================================
# Dispatcher
# =====================================================

dp = Dispatcher(
    storage=fsm_storage
)

# =====================================================
# Matching Engine
# =====================================================

matching_engine = MatchingEngine(
    redis_host=settings.REDIS_HOST,
    redis_port=settings.REDIS_PORT,
    redis_password=settings.REDIS_PASSWORD
)

# =====================================================
# Dating Scheduler
# =====================================================

dating_scheduler = DatingScheduler(
    bot=bot,
    dp=dp,
    redis_client=redis_client,
    session_factory=session_factory_instance,
    timeout_seconds=180
)

logger.info("Bot loader initialized successfully.")