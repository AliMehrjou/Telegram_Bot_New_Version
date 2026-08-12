import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder

from redis.asyncio import Redis

from matching_bot_project.bot.core.config import settings
from matching_bot_project.services.matching_engine import MatchingEngine
from matching_bot_project.services.scheduler import DatingScheduler
# v3 NEW services
from matching_bot_project.services.vip_subscription import VIPSubscriptionManager
from matching_bot_project.services.gift_engine import GiftEngine
from matching_bot_project.services.referral_engine import ReferralEngine
from matching_bot_project.services.warning_engine import WarningEngine
from matching_bot_project.services.profile_completion import ProfileCompletionService
from matching_bot_project.services.free_coin_banner import FreeCoinBannerService
from matching_bot_project.services.cron_reminders import CronRemindersService
# v3.1 SCALING: bot sharding + cache + metrics
from matching_bot_project.bot.core.bot_shard_manager import shard_manager
from matching_bot_project.services.cache import cache
from matching_bot_project.services.metrics import metrics

from matching_bot_project.database.session import async_session_factory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# =====================================================
# Telegram Bot Session
# =====================================================

proxy_url = settings.PROXY_URL

if proxy_url:
    logger.info(f"Using Telegram proxy: {proxy_url}")
    session = AiohttpSession(proxy=proxy_url)
    bot = Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode="HTML")
    )
else:
    logger.warning(
        "No PROXY_URL configured. Telegram requests will use direct connection."
    )
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
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
    key_builder=DefaultKeyBuilder(with_destiny=True)
)

# =====================================================
# Dispatcher
# =====================================================

dp = Dispatcher(storage=fsm_storage)

# =====================================================
# Matching Engine
# =====================================================

matching_engine = MatchingEngine(
    redis_host=settings.REDIS_HOST,
    redis_port=settings.REDIS_PORT,
    redis_password=settings.REDIS_PASSWORD
)

# =====================================================
# v3 NEW: Service instances (singletons)
# =====================================================

vip_manager = VIPSubscriptionManager(redis_client=redis_client)
gift_engine = GiftEngine(redis_client=redis_client)
referral_engine = ReferralEngine(redis_client=redis_client)
warning_engine = WarningEngine(redis_client=redis_client)
profile_completion_service = ProfileCompletionService(redis_client=redis_client)
free_coin_banner_service = FreeCoinBannerService(redis_client=redis_client)
cron_reminders_service = CronRemindersService(
    bot=bot,
    redis_client=redis_client,
    session_factory=async_session_factory,
)

# =====================================================
# v3.1 SCALING: Initialize bot sharding (multi-bot for 200K+ users)
# =====================================================
try:
    shard_manager.initialize()
    if shard_manager.is_sharded:
        logger.info(f"Bot sharding enabled with {shard_manager.num_shards} shards.")
    else:
        logger.info("Single-bot mode (no sharding).")
except Exception as e:
    logger.error(f"Failed to initialize bot sharding: {e} — falling back to single-bot.")
    # shard_manager will gracefully fall back to single-bot mode

# =====================================================
# Dating Scheduler
# =====================================================

dating_scheduler = DatingScheduler(
    bot=bot,
    dp=dp,
    redis_client=redis_client,
    session_factory=async_session_factory,
)

logger.info("Bot loader initialized successfully (v3 services registered).")