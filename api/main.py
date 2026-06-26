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

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles critical microservice startup and teardown lifecycles:
    - Creates database tables if they don't exist (with retry logic).
    - Connects to the Redis queuing pools.
    - Configures Telegram Bot Webhook URLs securely.
    - Launches background activity polling tasks.
    """
    
    # ۱. ساخت جداول دیتابیس با مکانیزم Retry و Exponential Backoff برای پایداری در داکر
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
            
            delay = 2 ** attempt  # 2s, 4s, 8s, 16s
            logger.warning(f"Database connection failed (Attempt {attempt}/{max_retries}): {e}. Retrying in {delay}s...")
            await asyncio.sleep(delay)
    
    # ۲. اجرای سیدر بانک سوالات (با نام متد اصلاح‌شده و بدون کامیت داخلی)
    async with async_session_factory() as session:
        await seed_question_bank_if_empty(session)

    # Core engine bindings
    await matching_engine.connect()
    
    # Active 3-mins date timeout scanner activation
    dating_scheduler.start_polling()

    # راه‌اندازی سرویس پاکسازی وضعیت آنلاین (Sync Offline Users)
    online_worker = OnlineStatusWorker(async_session_factory, idle_minutes=5)
    online_worker.start_polling()

    # ایجاد تسک بک‌گراند برای گزارش روزانه
    asyncio.create_task(_daily_report_loop(async_session_factory))

    # ۳. مدیریت وبهوک بر اساس متغیر محیطی صریح (ENVIRONMENT) و توکن اختصاصی وبهوک
    if getattr(settings, "ENVIRONMENT", "development").lower() == "production":
        webhook_url = f"{settings.BASE_URL}{settings.WEBHOOK_PATH}"
        logger.info(f"Setting Telegram webhook url: {webhook_url}")
        await bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message", "callback_query", "my_chat_member"],
            drop_pending_updates=True,
            secret_token=settings.WEBHOOK_SECRET_TOKEN  # استفاده از توکن اختصاصی وبهوک
        )
        
    else:
        logger.warning(
            f"Running in {getattr(settings, 'ENVIRONMENT', 'development').upper()}/POLLING mode. Deleting active webhooks to prevent conflicts..."
        )
        try:
            await bot.delete_webhook(
                drop_pending_updates=True,
                request_timeout=60
            )
            logger.info("Webhook deleted successfully.")
        except TelegramNetworkError as e:
            logger.warning(f"Telegram unreachable while deleting webhook: {e}")
        except Exception:
            logger.exception("Unexpected error while deleting webhook")

    yield # حد فاصل اجرای Lifespan و Teardown
    
    # Tear-down connections
    await matching_engine.disconnect()
    await bot.session.close()
    await engine.dispose()
    logger.info("Lifespan teardown finished successfully.")


# Instantiating server base
app = FastAPI(
    title="Telegram Matchmaker API",
    description="Backend microservice handling Webhook loops and matching dashboards.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for browser integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Route attachment
app.include_router(webhook.router)
app.include_router(admin.router)


@app.get("/health")
async def check_health_status():
    """Provides instant status telemetry for external monitors."""
    return {"status": "healthy", "service": "match_bot", "engine": "alive"}