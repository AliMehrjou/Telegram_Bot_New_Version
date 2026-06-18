import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import bot, dp, matching_engine, dating_scheduler
from matching_bot_project.api.routes import webhook, admin
from matching_bot_project.database.session import engine, Base
from matching_bot_project.database.queries.crud import seed_sixty_question_bank_if_empty
from matching_bot_project.database.session import async_session_factory

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles critical microservice startup and teardown lifecycles:
    - Connects to the Redis queuing pools.
    - Configures Telegram Bot Webhook URLs.
    - Launches background activity polling tasks.
    """
    # Seeds question repository
    async with async_session_factory() as session:
        await seed_sixty_question_bank_if_empty(session)

    # Core engine bindings
    await matching_engine.connect()
    
    # Active 3-mins date timeout scanner activation
    dating_scheduler.start_polling()

    # Webhook setup rule in production, fallback to deletion during local test ranges
    if settings.BASE_URL and "yourdomain.com" not in settings.BASE_URL:
        webhook_url = f"{settings.BASE_URL}{settings.WEBHOOK_PATH}"
        logger.info(f"Setting Telegram webhook url: {webhook_url}")
        await bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message", "callback_query", "my_chat_member"],
            drop_pending_updates=True,
            secret_token=settings.ADMIN_SECRET_TOKEN
        )
    else:
        logger.warning("No BASE_URL configured or still using default. Bot will require getUpdates Polling mode.")
        await bot.delete_webhook(drop_pending_updates=True)

    yield # Lifespan execution margin (App continues serving)

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
