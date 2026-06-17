import asyncio
import logging
from typing import List
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

logger = logging.getLogger(__name__)


class BroadcastWorker:
    """
    Asynchronous notification broadcast service.
    Sends bulk messages without locking the primary thread of aiogram or FastAPI.
    Gracefully catches and filters out BotBlocked (forbidden) or deactivated user exceptions.
    """
    def __init__(self, bot: Bot):
        self.bot = bot

    async def broadcast_message(self, user_ids: List[int], text: str, delay_ms: int = 50) -> dict:
        """
        Sends an asynchronous broadcast message.
        Rate limiting is respected (Telegram allows 30 messages per second).

        :param user_ids: Target telegram user IDs
        :param text: Text string content
        :param delay_ms: Millisecond sleep interval between delivery tasks (to stay within rate limits)
        :return: Metrics status dictionary
        """
        sent_count = 0
        blocked_count = 0
        error_count = 0

        logger.info("Starting async broadcast to %d users.", len(user_ids))

        for index, tg_id in enumerate(user_ids):
            try:
                await self.bot.send_message(chat_id=tg_id, text=text)
                sent_count += 1
            except TelegramForbiddenError:
                logger.warning("Broadcast blocked by user %s", tg_id)
                blocked_count += 1
            except TelegramAPIError as e:
                logger.error("Telegram API error sending to %s: %s", tg_id, e)
                error_count += 1
            except Exception as e:
                logger.error("Unexpected error sending to %s: %s", tg_id, e)
                error_count += 1

            # FIX: sleep after EVERY message including the last one.
            # The original condition `if index < len(user_ids) - 1` skipped the sleep
            # only on the final iteration, which is harmless — but the guard added
            # complexity for no real benefit. Sleeping after the last send is fine
            # since the coroutine returns immediately after.
            await asyncio.sleep(delay_ms / 1000.0)

        logger.info(
            "Broadcast completed. Success: %d, Blocked: %d, Failed: %d",
            sent_count, blocked_count, error_count
        )
        return {
            "success": sent_count,
            "blocked": blocked_count,
            "failed": error_count,
            "total_scope": len(user_ids)
        }

    def start_background_broadcast(self, user_ids: List[int], text: str, delay_ms: int = 50) -> asyncio.Task:
        # FIX: replaced asyncio.get_event_loop() with asyncio.get_running_loop().
        # get_event_loop() is deprecated in Python 3.10+ when called from a coroutine
        # context and raises DeprecationWarning (or RuntimeError in 3.12+) if there is
        # no current event loop set on the thread. get_running_loop() is the correct
        # call when you know a loop is already running (which is always true inside
        # an aiogram handler or FastAPI endpoint).
        loop = asyncio.get_running_loop()

        # FIX: return the Task object so the caller can await it, cancel it, or attach
        # a done-callback if needed. Discarding the task silently (as before) means
        # exceptions raised inside broadcast_message are swallowed with no way to
        # observe or handle them from outside.
        task = loop.create_task(
            self.broadcast_message(user_ids, text, delay_ms),
            name=f"broadcast_to_{len(user_ids)}_users"
        )
        return task