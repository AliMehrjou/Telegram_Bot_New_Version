import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from aiogram.exceptions import TelegramNetworkError
from matching_bot_project.services.scheduler import OnlineStatusWorker, ZombieCleanerWorker
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import bot, dp, matching_engine, dating_scheduler, redis_client
# FIX L-25: removed duplicate `webhook, admin` import — was imported twice on lines 10 and 16.
from matching_bot_project.api.routes import webhook, admin, payment
from matching_bot_project.database.session import engine, Base, async_session_factory
from matching_bot_project.database.queries.crud import seed_question_bank_if_empty
from matching_bot_project.bot.handlers.admin import _daily_report_loop
from matching_bot_project.services.scheduler import OnlineStatusWorker
from matching_bot_project.services.reengagement import ReEngagementWorker
# خط ایمپورت قبلی را پیدا کرده و به این شکل اصلاح کنید:
from matching_bot_project.services.scheduler import OnlineStatusWorker, ZombieCleanerWorker, QueueTimeoutWorker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ۱. ساخت جداول دیتابیس
    logger.info("Initializing database tables...")
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables initialized successfully.")
            break
        except Exception as e:
            if attempt == max_retries:
                logger.critical(f"Fatal error: Could not connect to database after {max_retries} attempts.")
                # FIX L-20: use bare `raise` to preserve the original traceback.
                raise
            delay = 2 ** attempt
            logger.warning(f"Database connection failed (Attempt {attempt}/{max_retries}): {e}. Retrying in {delay}s...")
            await asyncio.sleep(delay)

    # ۲. سیدر بانک سوالات
    async with async_session_factory() as session:
        await seed_question_bank_if_empty(session)

    # ۳. اتصال به سرویس‌های اصلی
    await matching_engine.connect()

    # FIX PHASE1-CRIT-12: previously every FastAPI replica (docker-compose
    # deploy.replicas=4) started these background workers in its lifespan,
    # causing 4× duplicate reminders / re-engagement messages / daily reports
    # and 4× DB polling load. Now we use a Redis leader-election lock so that
    # only ONE replica at a time owns the in-process workers. The arq_worker
    # container is the long-term home for these jobs; the in-process workers
    # here are a fallback for single-replica dev deployments.
    leader_lock_key = "lock:fastapi:background_workers"
    leader_lock_ttl = 30  # seconds — renewed every 10s by the heartbeat task
    is_leader = False
    try:
        # SET NX = only acquire if no one else holds it
        is_leader = await redis_client.set(leader_lock_key, "1", nx=True, ex=leader_lock_ttl)
    except Exception:
        logger.exception("Failed to acquire background-workers leader lock; assuming follower.")

    app.state.is_background_workers_leader = is_leader

    if is_leader:
        logger.info("Acquired background-workers leader lock — starting in-process workers.")
        dating_scheduler.start_polling()

        online_worker = OnlineStatusWorker(bot,async_session_factory, idle_minutes=5, redis_client=redis_client)
        online_worker.start_polling()
        app.state.online_worker = online_worker
        # ---> کدهای جدید: راه‌اندازی Zombie Cleaner <---
        zombie_cleaner = ZombieCleanerWorker(bot, redis_client, async_session_factory, poll_interval_minutes=30)
        zombie_cleaner.start_polling()
        app.state.zombie_cleaner = zombie_cleaner
        queue_timeout_worker = QueueTimeoutWorker(bot, dp, redis_client, poll_interval_seconds=30)
        queue_timeout_worker.start_polling()
        app.state.queue_timeout_worker = queue_timeout_worker
        # -----------------------------------------------
        reengagement_worker = ReEngagementWorker(async_session_factory, bot)
        reengagement_worker.start_polling()
        app.state.reengagement_worker = reengagement_worker

        # v3 NEW: Start the cron-reminders service (profile completion, silence, vip expiry)
        from matching_bot_project.bot.core.loader import cron_reminders_service
        cron_reminders_service.start()
        app.state.cron_reminders_started = True

        # Leader-lock heartbeat — renew every 10s so the lock doesn't expire
        # while the leader is still alive. If the leader crashes, the lock
        # expires after `leader_lock_ttl` and another replica picks it up.
        async def _leader_heartbeat():
            while True:
                try:
                    await asyncio.sleep(10)
                    # Extend the lock (SET XX only succeeds if we still own it)
                    renewed = await redis_client.set(leader_lock_key, "1", xx=True, ex=leader_lock_ttl)
                    if not renewed:
                        logger.warning("Lost background-workers leader lock — stopping heartbeat.")
                        return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Error renewing leader lock")

        app.state.leader_heartbeat_task = asyncio.create_task(_leader_heartbeat())
    else:
        logger.info("Did NOT acquire background-workers leader lock — running as follower (no in-process workers).")
        app.state.online_worker = None
        app.state.reengagement_worker = None
        app.state.cron_reminders_started = False

    # FIX HIGH-24: keep a strong reference to the daily-report task so it is not
    # garbage-collected mid-flight (Python's GC can drop unreferenced tasks).
    # Only the leader runs the daily report — otherwise 4 replicas would each
    # send a copy.
    if is_leader:
        app.state.daily_report_task = asyncio.create_task(_daily_report_loop(async_session_factory))
        # Surface otherwise-silent failures.
        def _on_daily_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.error("daily_report_loop crashed: %r", exc, exc_info=exc)
        app.state.daily_report_task.add_done_callback(_on_daily_done)
    else:
        app.state.daily_report_task = None

    # FIX M-21: wrap set_webhook in retry loop so a transient Telegram outage does not
    # prevent the app from starting (the previous code would raise and abort lifespan).
    if getattr(settings, "ENVIRONMENT", "development").lower() == "production":
        webhook_url = f"{settings.BASE_URL}{settings.WEBHOOK_PATH}"
        logger.info(f"Setting Telegram webhook url: {webhook_url}")
        for attempt in range(1, 4):
            try:
                await bot.set_webhook(
                    url=webhook_url,
                    allowed_updates=["message", "callback_query", "my_chat_member"],
                    drop_pending_updates=True,
                    secret_token=settings.WEBHOOK_SECRET_TOKEN,
                )
                logger.info("Webhook registered with Telegram.")
                break
            except TelegramNetworkError as e:
                if attempt == 3:
                    logger.error(f"Failed to set webhook after 3 attempts: {e}")
                    raise
                delay = 2 ** attempt
                logger.warning(f"set_webhook failed (attempt {attempt}/3): {e}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
    else:
        logger.warning(
            f"Running in {getattr(settings, 'ENVIRONMENT', 'development').upper()}/POLLING mode. Deleting active webhooks..."
        )
        try:
            await bot.delete_webhook(drop_pending_updates=True, request_timeout=60)
            logger.info("Webhook deleted successfully.")
        except TelegramNetworkError as e:
            logger.warning(f"Telegram unreachable while deleting webhook: {e}")
        except Exception:
            logger.exception("Unexpected error while deleting webhook")

    yield
    
    # ── Teardown ──
    online_worker = getattr(app.state, "online_worker", None)
    if online_worker is not None:
        try:
            await online_worker.stop()
        except Exception:
            logger.exception("Error stopping online_worker")

    # ---> کدهای جدید: متوقف کردن Zombie Cleaner <---
    zombie_cleaner = getattr(app.state, "zombie_cleaner", None)
    if zombie_cleaner is not None:
        try:
            await zombie_cleaner.stop()
        except Exception:
            logger.exception("Error stopping zombie_cleaner")
    # -----------------------------------------------
    queue_timeout_worker = getattr(app.state, "queue_timeout_worker", None)
    if queue_timeout_worker is not None:
        try:
            await queue_timeout_worker.stop()
        except Exception:
            logger.exception("Error stopping queue_timeout_worker")

    # ── Teardown ──
    # FIX HIGH-23: properly stop OnlineStatusWorker (only if we started it).
    online_worker = getattr(app.state, "online_worker", None)
    if online_worker is not None:
        try:
            await online_worker.stop()
        except Exception:
            logger.exception("Error stopping online_worker")

    reengagement_worker = getattr(app.state, "reengagement_worker", None)
    if reengagement_worker is not None:
        try:
            await reengagement_worker.stop()
        except Exception:
            logger.exception("Error stopping reengagement_worker")

    # FIX PHASE1: stop cron_reminders_service if we started it.
    if getattr(app.state, "cron_reminders_started", False):
        try:
            from matching_bot_project.bot.core.loader import cron_reminders_service
            cron_reminders_service.stop()
        except Exception:
            logger.exception("Error stopping cron_reminders_service")

    # FIX PHASE1: stop dating_scheduler if we started it.
    if getattr(app.state, "is_background_workers_leader", False):
        try:
            dating_scheduler.stop()
        except Exception:
            logger.exception("Error stopping dating_scheduler")

    # FIX PHASE1: cancel leader heartbeat task.
    heartbeat_task = getattr(app.state, "leader_heartbeat_task", None)
    if heartbeat_task and not heartbeat_task.done():
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    # FIX HIGH-24: cancel the daily-report task.
    daily_task = getattr(app.state, "daily_report_task", None)
    if daily_task and not daily_task.done():
        daily_task.cancel()
        try:
            await daily_task
        except asyncio.CancelledError:
            pass

    await matching_engine.disconnect()

    try:
        await bot.session.close()
    except Exception:
        logger.exception("Error closing bot session")

    # FIX M-22: close Redis client to avoid leaking connections.
    try:
        await redis_client.aclose()
    except Exception:
        logger.exception("Error closing redis_client")

    try:
        await engine.dispose()
    except Exception:
        logger.exception("Error disposing engine")

    logger.info("Lifespan teardown finished successfully.")


app = FastAPI(
    title="Telegram Matchmaker API",
    description="Backend microservice handling Webhook loops and matching dashboards.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # v3 FIX: was allow_origins=["*"] + allow_credentials=True — that combination
    # is invalid per CORS spec. Now configurable via env; defaults to no origins
    # (same-origin only).
    allow_origins=settings.parsed_cors_origins,
    allow_credentials=bool(settings.parsed_cors_origins),
    # FIX PHASE2-SEC-15: was allow_methods=["*"] + allow_headers=["*"]. Tightened
    # to the actual methods/headers the bot's API uses. This prevents preflight
    # from approving exotic methods (PUT/DELETE/CONNECT) that no route needs.
    allow_methods=["GET", "POST"],
    allow_headers=["X-Api-Key", "Content-Type", "X-Telegram-Bot-Api-Secret-Token"],
)

app.include_router(webhook.router)
app.include_router(admin.router)
app.include_router(payment.router)

@app.get("/health")
async def check_health_status():
    return {"status": "healthy", "service": "match_bot", "engine": "alive"}


# v3.1 SCALING: Prometheus metrics endpoint
@app.get(settings.METRICS_PATH)
async def prometheus_metrics():
    from fastapi import Response
    from matching_bot_project.services.metrics import metrics as _metrics
    content_type, body = _metrics.get_metrics()
    return Response(content=body, media_type=content_type)


# v3.1 SCALING: Shard-aware webhook endpoint
# Legacy /api/v1/webhook is kept for backward compat (single-bot mode).
# Sharded mode uses /api/v1/webhook?shard={index}
@app.get("/shards")
async def list_shards():
    """List all bot shards (for health checking)."""
    from matching_bot_project.bot.core.loader import shard_manager
    return {
        "num_shards": shard_manager.num_shards,
        "is_sharded": shard_manager.is_sharded,
    }
