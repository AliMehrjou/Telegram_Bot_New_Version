import uvicorn
import asyncio
import logging
import sys
from aiogram.exceptions import TelegramNetworkError
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import dp, bot, matching_engine
from matching_bot_project.bot.middlewares.database import DbSessionMiddleware
from matching_bot_project.bot.middlewares.force_join import ForceJoinMiddleware
from matching_bot_project.bot.middlewares.anti_spam import ThrottlingMiddleware

from matching_bot_project.bot.handlers import (
    start, profile, profile_edit, matching, 
    questionnaire, anonymous_chat, explore, 
    interactions, admin, discovery, transfer
)
from matching_bot_project.bot.handlers import payments

logger = logging.getLogger("launcher")


def register_bot_middlewares_and_routers():
    """Attaches all routers and intermediate global middlewares to aiogram dispatcher."""
    # The correct middleware hierarchy: ThrottlingMiddleware -> DbSessionMiddleware -> ForceJoinMiddleware
    
    # 1. ThrottlingMiddleware (must be outer so it catches before session is created if spammed)
    dp.message.outer_middleware(ThrottlingMiddleware())
    dp.callback_query.outer_middleware(ThrottlingMiddleware())
    
    # 2. DbSessionMiddleware
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())

    # 3. ForceJoinMiddleware
    dp.message.middleware(ForceJoinMiddleware())
    dp.callback_query.middleware(ForceJoinMiddleware())

    # Attach feature handlers to the core stack
    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(profile_edit.router)
    dp.include_router(matching.router)
    dp.include_router(explore.router)
    dp.include_router(interactions.router)
    dp.include_router(questionnaire.router)
    dp.include_router(anonymous_chat.router)
    dp.include_router(admin.router)
    dp.include_router(discovery.router)
    dp.include_router(transfer.router)
    dp.include_router(payments.router)
    logger.info("Bot handlers and middlewares successfully initialized.")


async def run_fastapi_server():
    """Launches the FastAPI production uvicorn daemon."""
    logger.info("Initializing Uvicorn FastAPI daemon...")
    config = uvicorn.Config(
        app="matching_bot_project.api.main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level="info",
        reload=False
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_bot_polling():
    """Fall-back long polling listener when webhook is disabled or not configured."""
    logger.info("Launching aiogram in long updates polling mode...")
    
    # Await background connections (DB/Redis lifespan) to be fully ready with a timeout
    timeout_seconds = 30
    poll_interval = 0.5
    max_attempts = int(timeout_seconds / poll_interval)
    
    redis_ready = False
    for _ in range(max_attempts):
        if hasattr(matching_engine, 'redis') and matching_engine.redis:
            redis_ready = True
            break
        await asyncio.sleep(poll_interval)
        
    if not redis_ready:
        logger.critical(f"Fatal error: matching_engine.redis did not become available after {timeout_seconds} seconds. Aborting polling startup.")
        raise RuntimeError("Redis connection timeout during bot polling startup.")
        
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

    await dp.start_polling(bot, skip_updates=True)


async def main():
    """Root async entrypoint coordinating both services."""
    register_bot_middlewares_and_routers()

    # بررسی وضعیت محیط ریلیز یا دِو لوکال بر اساس متغیر محیطی صریح
    is_production = getattr(settings, "ENVIRONMENT", "development").lower() == "production"

    if is_production:
        # Production Webhook-only mode
        logger.info("Running in PRODUCTION configuration with Webhook routing enabled.")
        await run_fastapi_server()
    else:
        # Development mode
        logger.info("Running under DEVELOPMENT configuration with concurrent Polling & Web Server.")
        fastapi_task = asyncio.create_task(run_fastapi_server())
        bot_task = asyncio.create_task(run_bot_polling())
        
        await asyncio.gather(fastapi_task, bot_task)
        

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Services terminated and exited gracefully.")