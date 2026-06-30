import asyncio
import logging
from typing import List, Optional
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from typing import Callable, Awaitable
logger = logging.getLogger(__name__)


class BroadcastWorker:
    """
    Asynchronous notification broadcast service.
    Supports both direct text messages and copying any media type (photo, video, etc.).
    """
    def __init__(self, bot: Bot):
        self.bot = bot

        async def broadcast_message(
        self, 
        user_ids: List[int], 
        text: Optional[str] = None, 
        from_chat_id: Optional[int] = None, 
        message_id: Optional[int] = None, 
        delay_ms: int = 50,
        on_blocked: Optional[Callable[[int], Awaitable[None]]] = None
    ) -> dict:
        """
        Sends an asynchronous broadcast message concurrently using bounded workers.
        """
        sent_count = 0
        blocked_count = 0
        error_count = 0

        if not text and not (from_chat_id and message_id):
            logger.error("Broadcast failed: Neither text nor message source provided.")
            return {"error": "Invalid arguments"}

        logger.info("Starting async broadcast to %d users.", len(user_ids))

        # Bounded concurrency: allow up to 20 concurrent requests
        semaphore = asyncio.Semaphore(20)

        async def _send_to_user(tg_id: int):
            nonlocal sent_count, blocked_count, error_count
            async with semaphore:
                try:
                    if from_chat_id and message_id:
                        await self.bot.copy_message(
                            chat_id=tg_id,
                            from_chat_id=from_chat_id,
                            message_id=message_id
                        )
                    elif text:
                        await self.bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
                    
                    sent_count += 1
                except TelegramForbiddenError:
                    logger.warning("Broadcast blocked by user %s", tg_id)
                    blocked_count += 1
                    if on_blocked:
                        try:
                            await on_blocked(tg_id)
                        except Exception as cb_err:
                            logger.error("Error in on_blocked callback for user %s: %s", tg_id, cb_err)
                except TelegramAPIError as e:
                    logger.error("Telegram API error sending to %s: %s", tg_id, e)
                    error_count += 1
                except Exception as e:
                    logger.error("Unexpected error sending to %s: %s", tg_id, e)
                    error_count += 1
                finally:
                    if delay_ms > 0:
                        await asyncio.sleep(delay_ms / 1000.0)

        # ⭐ پردازش به صورت دسته‌ای (Batch) برای جلوگیری از OOM در کاربران بالا
        batch_size = 1000
        for i in range(0, len(user_ids), batch_size):
            batch = user_ids[i:i + batch_size]
            tasks = [_send_to_user(tg_id) for tg_id in batch]
            if tasks:
                await asyncio.gather(*tasks)

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
    

    def start_background_broadcast(
        self, 
        user_ids: List[int], 
        text: Optional[str] = None, 
        from_chat_id: Optional[int] = None, 
        message_id: Optional[int] = None, 
        delay_ms: int = 50,
        on_blocked: Optional[Callable[[int], Awaitable[None]]] = None
    ) -> asyncio.Task:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            self.broadcast_message(user_ids, text, from_chat_id, message_id, delay_ms, on_blocked),
            name=f"broadcast_to_{len(user_ids)}_users"
        )

        def _done_callback(t: asyncio.Task):
            try:
                t.result()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(
                    "Background broadcast task '%s' failed unexpectedly: %s", 
                    t.get_name(), e, exc_info=True
                )

        task.add_done_callback(_done_callback)
        return task
    
    