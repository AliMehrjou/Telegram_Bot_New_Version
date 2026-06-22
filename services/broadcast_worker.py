import asyncio
import logging
from typing import List, Optional
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

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
        delay_ms: int = 50
    ) -> dict:
        """
        Sends an asynchronous broadcast message.
        """
        sent_count = 0
        blocked_count = 0
        error_count = 0

        if not text and not (from_chat_id and message_id):
            logger.error("Broadcast failed: Neither text nor message source provided.")
            return {"error": "Invalid arguments"}

        logger.info("Starting async broadcast to %d users.", len(user_ids))

        for index, tg_id in enumerate(user_ids):
            try:
                # اگر پیام شامل آیدی چت و آیدی پیام بود (برای انواع مدیا)
                if from_chat_id and message_id:
                    await self.bot.copy_message(
                        chat_id=tg_id,
                        from_chat_id=from_chat_id,
                        message_id=message_id
                    )
                # در غیر این صورت فقط متن ارسال می‌شود (برای پیام‌های سیستمی و ایونت‌ها)
                elif text:
                    await self.bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
                
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

    def start_background_broadcast(
        self, 
        user_ids: List[int], 
        text: Optional[str] = None, 
        from_chat_id: Optional[int] = None, 
        message_id: Optional[int] = None, 
        delay_ms: int = 50
    ) -> asyncio.Task:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            self.broadcast_message(user_ids, text, from_chat_id, message_id, delay_ms),
            name=f"broadcast_to_{len(user_ids)}_users"
        )
        return task