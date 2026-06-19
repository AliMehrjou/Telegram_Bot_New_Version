import uvicorn
import asyncio
import logging
import sys

from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import dp, bot
from matching_bot_project.bot.middlewares.database import DbSessionMiddleware
from matching_bot_project.bot.middlewares.force_join import ForceJoinMiddleware
from matching_bot_project.bot.middlewares.anti_spam import ThrottlingMiddleware

# Import handlers to register them on dispatcher
from matching_bot_project.bot.handlers import start, profile, matching, questionnaire, anonymous_chat, explore, interactions
from matching_bot_project.bot.core.loader import dp, bot, matching_engine
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
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
    dp.include_router(matching.router)
    dp.include_router(explore.router)
    dp.include_router(interactions.router)
    dp.include_router(questionnaire.router)
    dp.include_router(anonymous_chat.router)
    
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
    
    # Await background connections (DB/Redis lifespan) to be fully ready
    while not matching_engine.redis:
        await asyncio.sleep(0.2)
        
    # Clean any hanging webhook before start
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, skip_updates=True)

async def main():
    """Root async entrypoint coordinating both services."""
    register_bot_middlewares_and_routers()

    # If domain contains default value, run both FastAPI (for admin API) and Polling (for bot updates) concurrently.
    # Otherwise Webhook router handles bot updates, so FastAPI is sufficient.
    if "funlinknow.ir" in settings.BASE_URL:
        # Development mode
        logger.info("Running under DEVELOPMENT configuration with concurrent Polling & Web Server.")
        await asyncio.gather(
            run_fastapi_server(),
            run_bot_polling()
        )
    else:
        # Production Webhook-only mode
        logger.info("Running in PRODUCTION configuration with Webhook routing enabled.")
        await run_fastapi_server()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Services terminated and exited gracefully.")
