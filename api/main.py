import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from aiogram.exceptions import TelegramNetworkError
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import bot, dp, matching_engine, dating_scheduler
from matching_bot_project.api.routes import webhook, admin
from matching_bot_project.database.session import engine, Base
from matching_bot_project.database.queries.crud import get_user_by_tg_id, process_coin_transaction, seed_sixty_question_bank_if_empty
from matching_bot_project.database.session import async_session_factory
from matching_bot_project.database.models import models
from matching_bot_project.bot.handlers.admin import _daily_report_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles critical microservice startup and teardown lifecycles:
    - Creates database tables if they don't exist.
    - Connects to the Redis queuing pools.
    - Configures Telegram Bot Webhook URLs.
    - Launches background activity polling tasks.
    """
    
    # ۱. ابتدا ساخت تمامی جداول دیتابیس (رفع خطای Table doesn't exist)
    logger.info("Initializing database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    
    async with async_session_factory() as session:
        await seed_sixty_question_bank_if_empty(session)

    # Core engine bindings
    await matching_engine.connect()
    
    # Active 3-mins date timeout scanner activation
    dating_scheduler.start_polling()

    asyncio.create_task(_daily_report_loop(async_session_factory))

    is_production_domain = settings.BASE_URL and any(ext in settings.BASE_URL for ext in [".com", ".ir", ".net", ".org"])
    # Webhook setup rule in production, fallback to deletion during local test ranges
    if is_production_domain and "yourdomain.com" not in settings.BASE_URL:
        webhook_url = f"{settings.BASE_URL}{settings.WEBHOOK_PATH}"
        logger.info(f"Setting Telegram webhook url: {webhook_url}")
        await bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message", "callback_query", "my_chat_member"],
            drop_pending_updates=True,
            secret_token=settings.ADMIN_SECRET_TOKEN
        )
    else:
        logger.warning(
            "Running in LOCAL/POLLING mode. Deleting active webhooks to prevent conflicts..."
        )

        try:
            await bot.delete_webhook(
                drop_pending_updates=True,
                request_timeout=60
            )
            logger.info("Webhook deleted successfully.")

        except TelegramNetworkError as e:
            logger.warning(
                f"Telegram unreachable while deleting webhook: {e}"
            )

        except Exception:
            logger.exception(
                "Unexpected error while deleting webhook"
            )

    yield # Lifespan execution margin
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