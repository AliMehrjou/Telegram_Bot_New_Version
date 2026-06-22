import uvicorn
import asyncio
import logging
import sys

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
    # 🛠️ اصلاح فنی: اضافه کردن یک لایه برای جلوگیری از قفل شدن لوپ در صورت عدم وجود آبجکت ردیس
    try:
        while not hasattr(matching_engine, 'redis') or not matching_engine.redis:
            await asyncio.sleep(0.2)
    except Exception as e:
        logger.warning(f"Waiting for matching_engine.redis generated an alert: {e}")
        await asyncio.sleep(1)
        
    # در محیط لوکال یکبار مطمئن می‌شویم وب‌هوک پاک شده تا پولینگ گیر نکند
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, skip_updates=True)


async def main():
    """Root async entrypoint coordinating both services."""
    register_bot_middlewares_and_routers()

    # بررسی وضعیت محیط ریلیز یا دِو لوکال
    is_production_domain = settings.BASE_URL and any(ext in settings.BASE_URL for ext in [".com", ".ir", ".net", ".org"])

    if is_production_domain and "yourdomain.com" not in settings.BASE_URL:
        # Production Webhook-only mode
        logger.info("Running in PRODUCTION configuration with Webhook routing enabled.")
        await run_fastapi_server()
    else:
        # Development mode
        logger.info("Running under DEVELOPMENT configuration with concurrent Polling & Web Server.")
        # 🛠️ اصلاح بسیار مهم: در ویندوز/لوکال uvicorn.serve یک لوپ بلاک‌کننده دارد.
        # برای اینکه وب‌سرور تسکِ پولینگ بات را خفه نکند، uvicorn را در یک تسک مجزا استارت می‌زنیم.
        fastapi_task = asyncio.create_task(run_fastapi_server())
        bot_task = asyncio.create_task(run_bot_polling())
        
        await asyncio.gather(fastapi_task, bot_task)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Services terminated and exited gracefully.")