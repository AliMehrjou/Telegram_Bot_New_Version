import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder
from redis.asyncio import Redis
from matching_bot_project.bot.core.config import settings
from matching_bot_project.services.matching_engine import MatchingEngine
from matching_bot_project.services.scheduler import DatingScheduler
from matching_bot_project.database.session import async_session_factory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Initialize raw Bot instance using HTML formatting constraints
bot = Bot(token=settings.BOT_TOKEN, parse_mode="HTML")

# Setup safe Redis connection for FSM states, throttling and matching sessions
redis_client = Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    decode_responses=True
)

# AIogram FSM persistent Redis storage client
fsm_storage = RedisStorage(
    redis=redis_client,
    key_builder=DefaultKeyBuilder(with_destiny=True)
)

# Root dispatcher setup
dp = Dispatcher(storage=fsm_storage)

# Matchmaking Engine instantiation
matching_engine = MatchingEngine(
    redis_host=settings.REDIS_HOST,
    redis_port=settings.REDIS_PORT,
    redis_password=settings.REDIS_PASSWORD
)

# Scheduler instance to clean stale active questionnaires
dating_scheduler = DatingScheduler(
    bot=bot,
    dp=dp,
    redis_client=redis_client,
    session_factory=async_session_factory,
    timeout_seconds=180
)
