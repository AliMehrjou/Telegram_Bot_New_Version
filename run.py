import uvicorn
import asyncio
import logging
from aiogram.exceptions import TelegramNetworkError
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import dp, bot, matching_engine
from matching_bot_project.bot.middlewares.database import DbSessionMiddleware
from matching_bot_project.bot.middlewares.force_join import ForceJoinMiddleware, my_chat_member_router
from matching_bot_project.bot.middlewares.system_guard import SystemGuardMiddleware, guard_router
from matching_bot_project.bot.middlewares.anti_spam import ThrottlingMiddleware, LikeRateLimitMiddleware
from matching_bot_project.bot.middlewares.direct_message_privacy import DirectMessagePrivacyMiddleware
from matching_bot_project.bot.middlewares.state_lock import StateLockMiddleware

from matching_bot_project.bot.handlers import vip
from matching_bot_project.bot.handlers import (
    start, profile, profile_edit, matching,
    questionnaire, anonymous_chat, explore,
    interactions, admin, discovery, transfer, gacha, transactions,
   
    gifts, referral, coins_menu, help as help_handler, direct_messages, anonymous_link, fallback
)
from matching_bot_project.bot.handlers import payments
from matching_bot_project.bot.handlers import comments

logger = logging.getLogger("launcher")


def register_bot_middlewares_and_routers():
    """Attaches all routers and intermediate global middlewares to aiogram dispatcher."""
    dp.message.outer_middleware(SystemGuardMiddleware())
    dp.callback_query.outer_middleware(SystemGuardMiddleware())

    dp.message.outer_middleware(ThrottlingMiddleware())
    dp.callback_query.outer_middleware(ThrottlingMiddleware())

    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())

    dp.message.middleware(ForceJoinMiddleware())
    dp.callback_query.middleware(ForceJoinMiddleware())


    dp.message.middleware(DirectMessagePrivacyMiddleware())
    dp.callback_query.middleware(DirectMessagePrivacyMiddleware())

    # v3 NEW: Like rate limit middleware (1 like per minute)
    dp.callback_query.middleware(LikeRateLimitMiddleware())

    dp.message.middleware(StateLockMiddleware())
    dp.callback_query.middleware(StateLockMiddleware())

    dp.include_router(guard_router)

    # FIX PHASE2-SEC-06: register my_chat_member_router BEFORE other routers
    # so force-join cache invalidation runs on every chat-member status change.
    dp.include_router(my_chat_member_router)

    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(profile_edit.router)
    dp.include_router(matching.router)
    dp.include_router(explore.router)
    dp.include_router(interactions.router)
    dp.include_router(questionnaire.router)
    dp.include_router(anonymous_chat.router)
    dp.include_router(vip.router)
    dp.include_router(admin.router)
    dp.include_router(admin.admin_router)
    dp.include_router(discovery.router)
    dp.include_router(anonymous_link.router)
    dp.include_router(transfer.router)
    dp.include_router(payments.router)
    dp.include_router(comments.router)
    dp.include_router(gacha.gacha_router)
    dp.include_router(transactions.router)
    # v3 NEW routers
    dp.include_router(gifts.router)
    dp.include_router(referral.router)
    dp.include_router(coins_menu.router)
    dp.include_router(help_handler.router)
    dp.include_router(direct_messages.router)
    dp.include_router(fallback.router)
    logger.info("Bot handlers and middlewares successfully initialized (v3).")


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
        logger.warning(f"Telegram unreachable while deleting webhook: {e}")
    except Exception:
        logger.exception("Unexpected error while deleting webhook")

    # FIX L-07: drop_pending_updates above already drops pending updates;
    # passing skip_updates=True here is redundant and may drop legitimate recent updates.
    await dp.start_polling(bot)


async def main():
    """Root async entrypoint coordinating both services."""
    register_bot_middlewares_and_routers()

    is_production = getattr(settings, "ENVIRONMENT", "development").lower() == "production"

    if is_production:
        logger.info("Running in PRODUCTION configuration with Webhook routing enabled.")
        await run_fastapi_server()
    else:
        logger.info("Running under DEVELOPMENT configuration with concurrent Polling & Web Server.")
        # FIX PHASE1-CRIT-17: previously this block also called
        # `asyncio.create_task(run_fastapi_server())` and
        # `asyncio.create_task(run_bot_polling())` BEFORE the TaskGroup below,
        # leaking two unmanaged tasks that raced for port 8000 and double-polled
        # Telegram. Those two lines have been removed; only the TaskGroup tasks
        # are now created.
        # FIX HIGH-05: use TaskGroup so if one task crashes, the other is cancelled
        # automatically — no more half-alive processes leaking ports / connections.
        try:
            tg = asyncio.TaskGroup()
            async with tg:
                tg.create_task(run_fastapi_server())
                tg.create_task(run_bot_polling())
        except* Exception as eg:
            # Re-raise the first non-cancelled exception for the outer handler.
            for exc in eg.exceptions:
                if not isinstance(exc, asyncio.CancelledError):
                    raise exc


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Services terminated and exited gracefully.")
    except Exception:
        # FIX HIGH-24: don't let any other exception escape without a clear log line.
        logger.exception("Bot crashed unexpectedly.")
        raise
