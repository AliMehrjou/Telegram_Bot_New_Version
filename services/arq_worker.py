"""
services/arq_worker.py

v3.1 SCALING: Background task queue using arq (async Redis queue).

Why arq?
- At 200K users, broadcasts take 1-2 hours and would block the FastAPI
  event loop. arq runs in a separate process, freeing the webhook handlers.
- Re-engagement, profile completion reminders, VIP expiry reminders —
  all run as arq jobs instead of in-process asyncio tasks.
- arq persists jobs in Redis, so they survive worker restarts.

Architecture:
- FastAPI handlers enqueue jobs via `await ctx.enqueue_job('task_name', *args)`
- arq worker process (separate container) picks up jobs and runs them
- Failed jobs are retried with exponential backoff

Deploy:
- `docker-compose.yml` runs `arq services.arq_worker.WorkerSettings` in a
  separate container, scaled to N replicas.

Job routing:
- High-priority queue: payment callbacks, VIP activation
- Default queue: broadcasts, reminders
- Low-priority queue: nightly cleanup, stats aggregation
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from arq import create_pool
from arq.connections import RedisSettings
from arq.connections import ArqRedis
from matching_bot_project.services.broadcast_worker import mark_user_blocked
from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)


def get_arq_redis_settings() -> RedisSettings:
    """Build arq RedisSettings from app config."""
    return RedisSettings(
        host=settings.ARQ_REDIS_HOST,
        port=settings.ARQ_REDIS_PORT,
        password=settings.REDIS_PASSWORD or None,
        max_connections=20,
    )


# ─── Job context (passed to every job) ──────────────────────────────────────
class JobContext:
    """Singleton-style holder for shared resources used by jobs."""
    _bot = None
    _session_factory = None
    _redis = None

    @classmethod
    def set_bot(cls, bot) -> None:
        cls._bot = bot

    @classmethod
    def set_session_factory(cls, sf) -> None:
        cls._session_factory = sf

    @classmethod
    def set_redis(cls, redis) -> None:
        cls._redis = redis

    @classmethod
    def get_bot(cls):
        if cls._bot is None:
            from matching_bot_project.bot.core.loader import bot
            cls._bot = bot
        return cls._bot

    @classmethod
    def get_session_factory(cls):
        if cls._session_factory is None:
            from matching_bot_project.database.session import async_session_factory
            cls._session_factory = async_session_factory
        return cls._session_factory

    @classmethod
    def get_redis(cls):
        if cls._redis is None:
            from matching_bot_project.bot.core.loader import redis_client
            cls._redis = redis_client
        return cls._redis


# ─── Job functions ──────────────────────────────────────────────────────────

async def send_broadcast_job(ctx, user_ids: list[int], text: str = None,
                              from_chat_id: int = None, message_id: int = None,
                              pin_in_chats: bool = False):
    """
    Broadcast a message to many users via arq.
    Picks up where in-process BroadcastWorker left off, but runs in a separate process.
    """
    from matching_bot_project.services.broadcast_worker import BroadcastWorker, mark_user_blocked

    bot = JobContext.get_bot()
    worker = BroadcastWorker(bot=bot)

    result = await worker.broadcast_message(
        user_ids=user_ids,
        text=text,
        from_chat_id=from_chat_id,
        message_id=message_id,
        delay_ms=settings.BROADCAST_DELAY_MS,
        on_blocked=mark_user_blocked,  # جایگزین شده با تابع گلوبال
        pin_in_chats=pin_in_chats,
    )
    return result


async def send_reengagement_job(ctx, max_users: int = 50):
    """Re-engage inactive users (3+ days)."""
    from matching_bot_project.services.reengagement import ReEngagementWorker
    bot = JobContext.get_bot()
    sf = JobContext.get_session_factory()
    worker = ReEngagementWorker(sf, bot)
    await worker.run_cycle(max_users=max_users)


async def send_profile_reminder_job(ctx, max_users: int = 50):
    """Send profile completion reminders."""
    from matching_bot_project.services.cron_reminders import CronRemindersService
    bot = JobContext.get_bot()
    redis = JobContext.get_redis()
    sf = JobContext.get_session_factory()
    svc = CronRemindersService(bot, redis, sf)
    await svc._send_profile_reminders()


async def send_silence_reminder_job(ctx):
    """Send silence reminders for inactive chat sessions."""
    from matching_bot_project.services.cron_reminders import CronRemindersService
    bot = JobContext.get_bot()
    redis = JobContext.get_redis()
    sf = JobContext.get_session_factory()
    svc = CronRemindersService(bot, redis, sf)
    await svc._send_silence_reminders()


async def expire_vip_subscriptions_job(ctx):
    """Expire VIP subscriptions whose expires_at < now."""
    from matching_bot_project.bot.core.loader import vip_manager
    sf = JobContext.get_session_factory()
    async with sf() as session:
        count = await vip_manager.expire_due_subscriptions(session)
    return {"expired": count}


async def batch_flush_last_active_job(ctx):
    """Batch-flush Redis last_active keys to MySQL (replaces in-process worker)."""
    from matching_bot_project.services.scheduler import OnlineStatusWorker
    redis = JobContext.get_redis()
    sf = JobContext.get_session_factory()
    worker = OnlineStatusWorker(sf, idle_minutes=5, redis_client=redis)
    await worker._batch_flush_last_active()


async def process_referral_commission_job(ctx, purchase_order_id: int,
                                           buyer_tg_id: int, coins_purchased: int):
    """Process referral commission for a coin purchase."""
    from matching_bot_project.bot.core.loader import referral_engine
    sf = JobContext.get_session_factory()
    async with sf() as session:
        commission = await referral_engine.process_commission_on_purchase(
            session, purchase_order_id, buyer_tg_id, coins_purchased
        )
    return {"commission_id": commission.id if commission else None}


# ─── arq worker configuration ───────────────────────────────────────────────

class WorkerSettings:
    """arq worker configuration."""
    functions = [
        send_broadcast_job,
        send_reengagement_job,
        send_profile_reminder_job,
        send_silence_reminder_job,
        expire_vip_subscriptions_job,
        batch_flush_last_active_job,
        process_referral_commission_job,
    ]
    redis_settings = get_arq_redis_settings()
    max_jobs = settings.ARQ_WORKER_MAX_JOBS
    job_timeout = 3600  # 1 hour max per job (for large broadcasts)
    max_tries = 3       # retry failed jobs up to 3 times
    queue_name = "default"

    async def on_startup(ctx):
        logger.info("arq worker started.")

    async def on_shutdown(ctx):
        logger.info("arq worker shutting down.")


# ─── Client helper (for enqueuing jobs from FastAPI/handlers) ───────────────

_arq_pool: Optional[ArqRedis] = None


async def get_arq_pool() -> ArqRedis:
    """Get or create the arq connection pool (singleton)."""
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(get_arq_redis_settings())
    return _arq_pool


async def enqueue_job(job_name: str, *args, **kwargs) -> Optional[str]:
    """
    Enqueue a background job. Returns job_id or None on failure.
    Safe to call from handlers — fails silently if arq is not available.
    """
    try:
        pool = await get_arq_pool()
        job = await pool.enqueue_job(job_name, *args, **kwargs)
        return job.job_id if job else None
    except Exception as e:
        logger.warning(f"Failed to enqueue job '{job_name}': {e}")
        return None


async def close_arq_pool() -> None:
    """Close arq pool on shutdown."""
    global _arq_pool
    if _arq_pool is not None:
        await _arq_pool.close()
        _arq_pool = None
