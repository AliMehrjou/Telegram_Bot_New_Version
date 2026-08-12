import asyncio
import logging
import time
from typing import List, Optional, Callable, Awaitable, Set
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter

from sqlalchemy import update
from matching_bot_project.database.models.models import User
from matching_bot_project.database.session import async_session_factory
from matching_bot_project.bot.core.bot_shard_manager import shard_manager
from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)

_OUTBOUND_RATE_LIMITER = None

class _TokenBucketRateLimiter:
    def __init__(self, rate: float, capacity: Optional[float] = None):
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last_refill = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                sleep_time = deficit / self.rate
                await asyncio.sleep(sleep_time)

def _get_rate_limiter(rate: float) -> _TokenBucketRateLimiter:
    global _OUTBOUND_RATE_LIMITER
    if _OUTBOUND_RATE_LIMITER is None:
        _OUTBOUND_RATE_LIMITER = _TokenBucketRateLimiter(rate=rate)
    return _OUTBOUND_RATE_LIMITER

async def mark_user_blocked(tg_id: int) -> None:
    """Mark user as blocked in DB when they've blocked the bot."""
    try:
        async with async_session_factory() as session:
            await session.execute(
                update(User)
                .where(User.tg_id == tg_id)
                .values(re_engage_blocked=True)
            )
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to mark user {tg_id} as blocked: {e}")


class BroadcastWorker:
    def __init__(self, bot: Bot):
        self.bot = bot
        self._background_tasks: Set[asyncio.Task] = set()
        
        # 💡 اصلاح مهم ۱: ضرب کردن ظرفیت خروجی در تعداد Shardهای فعال
        # اگر ۵ ربات داشته باشید، سرعت از ۲۵ به ۱۲۵ پیام در ثانیه افزایش می‌یابد.
        total_rate = float(settings.TG_OUTBOUND_RATE_PER_BOT) * shard_manager.num_shards
        self._rate_limiter = _get_rate_limiter(total_rate)

    async def broadcast_message(
        self,
        user_ids: List[int],
        text: Optional[str] = None,
        from_chat_id: Optional[int] = None,
        message_id: Optional[int] = None,
        delay_ms: int = 50,
        on_blocked: Optional[Callable[[int], Awaitable[None]]] = None,
        pin_in_chats: bool = False,
    ) -> dict:
        
        sent_count = 0
        blocked_count = 0
        error_count = 0

        empty_result = {
            "success": 0, "blocked": 0, "failed": 0, "total_scope": len(user_ids),
        }

        if not text and not (from_chat_id and message_id):
            logger.error("Broadcast failed: Neither text nor message source provided.")
            empty_result["error"] = "Invalid arguments"
            return empty_result

        logger.info("Starting async broadcast to %d users.", len(user_ids))

        # استفاده از سِمافور بزرگتر به نسبت تعداد ربات‌ها
        semaphore = asyncio.Semaphore(5 * shard_manager.num_shards)

        async def _send_to_user(tg_id: int):
            nonlocal sent_count, blocked_count, error_count
            async with semaphore:
                await self._rate_limiter.acquire()
                try:
                    # 💡 اصلاح مهم ۲: دریافت داینامیک رباتِ مختص به این کاربر
                    user_bot = await shard_manager.get_bot_for_user_async(tg_id)

                    sent_msg_id = None
                    if from_chat_id and message_id:
                        # استفاده از user_bot به جای self.bot
                        sent_msg = await user_bot.copy_message(
                            chat_id=tg_id,
                            from_chat_id=from_chat_id,
                            message_id=message_id
                        )
                        sent_msg_id = getattr(sent_msg, "message_id", None)
                    elif text:
                        sent_msg = await user_bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
                        sent_msg_id = getattr(sent_msg, "message_id", None)

                    if pin_in_chats and sent_msg_id:
                        try:
                            await user_bot.pin_chat_message(
                                chat_id=tg_id,
                                message_id=sent_msg_id,
                                disable_notification=True,
                            )
                        except TelegramAPIError as pin_err:
                            logger.debug("Pin failed for user %s: %s", tg_id, pin_err)
                        except Exception as pin_err:
                            logger.debug("Pin unexpected error for user %s: %s", tg_id, pin_err)

                    sent_count += 1
                except TelegramForbiddenError:
                    blocked_count += 1
                    if on_blocked:
                        try:
                            await on_blocked(tg_id)
                        except Exception as cb_err:
                            logger.error("Error in on_blocked callback for user %s: %s", tg_id, cb_err)
                except TelegramRetryAfter as e:
                    logger.warning("Telegram asked to retry after %s seconds for user %s", e.retry_after, tg_id)
                    try:
                        await asyncio.sleep(float(e.retry_after) + 0.5)
                        await self._rate_limiter.acquire()
                        
                        # در تلاش مجدد هم باید از user_bot استفاده شود
                        user_bot = await shard_manager.get_bot_for_user_async(tg_id)
                        sent_msg_id = None
                        if from_chat_id and message_id:
                            sent_msg = await user_bot.copy_message(
                                chat_id=tg_id, from_chat_id=from_chat_id, message_id=message_id
                            )
                            sent_msg_id = getattr(sent_msg, "message_id", None)
                        elif text:
                            sent_msg = await user_bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
                            sent_msg_id = getattr(sent_msg, "message_id", None)
                        if pin_in_chats and sent_msg_id:
                            try:
                                await user_bot.pin_chat_message(
                                    chat_id=tg_id, message_id=sent_msg_id,
                                    disable_notification=True,
                                )
                            except Exception:
                                pass
                        sent_count += 1
                    except Exception as retry_exc:
                        error_count += 1
                except Exception as e:
                    error_count += 1

        batch_size = 1000
        for i in range(0, len(user_ids), batch_size):
            batch = user_ids[i:i + batch_size]
            tasks = [_send_to_user(tg_id) for tg_id in batch]
            if tasks:
                await asyncio.gather(*tasks)

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
        on_blocked: Optional[Callable[[int], Awaitable[None]]] = None,
        pin_in_chats: bool = False,
    ) -> asyncio.Task:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            self.broadcast_message(
                user_ids, text, from_chat_id, message_id, delay_ms, on_blocked, pin_in_chats
            ),
            name=f"broadcast_to_{len(user_ids)}_users"
        )
        self._background_tasks.add(task)
        def _done_callback(t: asyncio.Task):
            self._background_tasks.discard(t)
            try:
                t.result()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error("Background broadcast task failed", exc_info=True)

        task.add_done_callback(_done_callback)
        return task

    async def wait_for_all(self) -> None:
        if not self._background_tasks:
            return
        await asyncio.gather(*self._background_tasks, return_exceptions=True)