import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from aiogram.exceptions import TelegramNetworkError

from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import bot, dp, matching_engine, dating_scheduler
from matching_bot_project.api.routes import webhook, admin
from matching_bot_project.database.session import engine, Base, async_session_factory
from matching_bot_project.database.queries.crud import seed_question_bank_if_empty
from matching_bot_project.bot.handlers.admin import _daily_report_loop
from matching_bot_project.services.scheduler import OnlineStatusWorker
from matching_bot_project.services.reengagement import ReEngagementWorker

# ── StateLockMiddleware ───────────────────────────────────────────────────────
from matching_bot_project.bot.middlewares.state_lock import StateLockMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Register StateLockMiddleware روی هر دو نوع event ────────────────────
    # باید روی message و callback_query هر دو register شود.
    # outer_middleware یعنی قبل از هر handler دیگری اجرا می‌شود.
    dp.message.outer_middleware(StateLockMiddleware())
    dp.callback_query.outer_middleware(StateLockMiddleware())

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
                raise e
            delay = 2 ** attempt
            logger.warning(f"Database connection failed (Attempt {attempt}/{max_retries}): {e}. Retrying in {delay}s...")
            await asyncio.sleep(delay)

    # ۲. سیدر بانک سوالات
    async with async_session_factory() as session:
        await seed_question_bank_if_empty(session)

    # ۳. اتصال به سرویس‌های اصلی
    await matching_engine.connect()
    dating_scheduler.start_polling()

    online_worker = OnlineStatusWorker(async_session_factory, idle_minutes=5)
    online_worker.start_polling()

    reengagement_worker = ReEngagementWorker(async_session_factory, bot)
    reengagement_worker.start_polling()

    asyncio.create_task(_daily_report_loop(async_session_factory))

    # ۴. تنظیم webhook
    if getattr(settings, "ENVIRONMENT", "development").lower() == "production":
        webhook_url = f"{settings.BASE_URL}{settings.WEBHOOK_PATH}"
        logger.info(f"Setting Telegram webhook url: {webhook_url}")
        await bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message", "callback_query", "my_chat_member"],
            drop_pending_updates=True,
            secret_token=settings.WEBHOOK_SECRET_TOKEN,
        )
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

    await reengagement_worker.stop()
    await matching_engine.disconnect()
    await bot.session.close()
    await engine.dispose()
    logger.info("Lifespan teardown finished successfully.")


app = FastAPI(
    title="Telegram Matchmaker API",
    description="Backend microservice handling Webhook loops and matching dashboards.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook.router)
app.include_router(admin.router)


@app.get("/health")
async def check_health_status():
    return {"status": "healthy", "service": "match_bot", "engine": "alive"}