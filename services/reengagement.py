"""
Re-engagement Worker
────────────────────
هر ۶ ساعت یه‌بار اجرا می‌شه و کاربرانی که ۳ روز است
هیچ تعاملی با ربات نداشتن رو با یه پیام هدفمند ping می‌کنه.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matching_bot_project.database.models.models import User
from matching_bot_project.database.queries import crud
from matching_bot_project.bot.core.constants import Messages

logger = logging.getLogger(__name__)

_INACTIVE_DAYS      = 3
_COOLDOWN_DAYS      = 3
_CHECK_INTERVAL_SEC = 6 * 3600
_BATCH_SIZE         = 50
_SEND_DELAY_SEC     = 0.05
_RETRY_COOLDOWN_HOURS = 24


class ReEngagementWorker:
    def __init__(self, session_factory: async_sessionmaker, bot: Bot):
        self._session_factory = session_factory
        self._bot             = bot
        self._task: asyncio.Task | None = None

    # ── Public API ──────────────────────────────

    def start_polling(self) -> None:
        if self._task and not self._task.done():
            logger.warning("ReEngagementWorker is already running.")
            return
        self._task = asyncio.create_task(self._loop(), name="reengagement_worker")
        logger.info("ReEngagementWorker started.")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ReEngagementWorker stopped.")

    # ── Core Loop ───────────────────────────────

    async def _loop(self) -> None:
        """حلقه اصلی — هر _CHECK_INTERVAL_SEC یه‌بار اجرا می‌شه."""
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

        while True:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ReEngagementWorker: unexpected error in run cycle.")
            
            try:
                await asyncio.sleep(_CHECK_INTERVAL_SEC)
            except asyncio.CancelledError:
                raise

    async def _run_once(self) -> None:
        """یه سیکل کامل: پیدا کردن کاربران بی‌تحرک، بررسی موجودی، شارژ و ارسال پیام."""
        from matching_bot_project.bot.core.loader import redis_client as _redis
        LOCK_KEY = "lock:reengagement:cycle"
        try:
            got_lock = await _redis.set(LOCK_KEY, "1", ex=_CHECK_INTERVAL_SEC - 60, nx=True)
        except Exception as exc:
            logger.warning("ReEngagementWorker: Redis failure acquiring lock: %s", exc)
            got_lock = None  
            
        if got_lock is None:
            logger.debug("ReEngagementWorker: another replica holds the lock, skipping cycle.")
            return

        now        = datetime.now(timezone.utc)
        cutoff     = now - timedelta(days=_INACTIVE_DAYS)
        re_cutoff  = now - timedelta(days=_COOLDOWN_DAYS)

        try:
            # 🛡️ تمامی عملیاتِ واکشی و آپدیت برای کاربران داخل یک سشن واحد انجام می‌شود
            async with self._session_factory() as session:
                users = await self._fetch_inactive_users(session, cutoff, re_cutoff)
                
                if not users:
                    logger.debug("ReEngagementWorker: no inactive users found.")
                    return

                logger.info(f"ReEngagementWorker: sending re-engagement to {len(users)} users.")

                sent = blocked = errors = 0

                for user_tg_id in users:
                    # ۱. خواندن آبجکت کاربر متصل به همین Session
                    user = await crud.get_user_by_tg_id(session, user_tg_id)
                    if not user:
                        continue
                        
                    # ۲. محاسبه موجودی کل (سکه + سهمیه VIP)
                    total_balance = user.coin_balance + getattr(user, 'vip_quota', 0)
                    
                    if total_balance > 0:
                        text = Messages.REENGAGE_WITH_COINS
                    else:
                        text = Messages.REENGAGE_NO_COINS
                        # ۳. شارژ ۵ سکه به عنوان هدیه
                        await crud.process_coin_transaction(
                            session, 
                            user, 
                            5, 
                            "هدیه فعال‌سازی مجدد"
                        )

                    # ۴. ارسال پیام
                    result = await self._send_message(user_tg_id, text)

                    # ۵. آپدیت وضعیت کاربر به صورت مستقیم در آبجکت
                    if result == "ok":
                        user.re_engaged_at = now
                        sent += 1
                    elif result == "blocked":
                        user.re_engage_blocked = True
                        blocked += 1
                    else:
                        retry_cutoff = now - timedelta(days=_COOLDOWN_DAYS) + timedelta(hours=_RETRY_COOLDOWN_HOURS)
                        user.re_engaged_at = retry_cutoff
                        errors += 1
                        
                    # ۶. کامیت کردن تراکنش برای این کاربر خاص
                    # (تغییرات سکه و تاریخِ پیگیری، هر دو با موفقیت ذخیره می‌شوند حتی اگر پیام خطا داده باشد)
                    await session.commit()
                    
                    # استراحت کوتاه برای جلوگیری از اسپم شدن ربات در تلگرام
                    await asyncio.sleep(_SEND_DELAY_SEC)

                logger.info(
                    f"ReEngagementWorker cycle done — "
                    f"sent={sent}, blocked/removed={blocked}, errors={errors}"
                )

        except Exception as e:
            logger.error(f"ReEngagementWorker: DB error during cycle: {e}")
            return

    # ── DB Helpers ──────────────────────────────

    @staticmethod
    async def _fetch_inactive_users(
        session: AsyncSession,
        cutoff: datetime,
        re_cutoff: datetime,
    ) -> list[int]:
        """لیست شناسه کاربرانی که شرایط دریافت پیام را دارند برمی‌گرداند."""
        stmt = (
            select(User.tg_id)
            .where(
                and_(
                    User.completed_registration == True,
                    User.re_engage_blocked != True,
                    or_(
                        User.silent_until == None,
                        User.silent_until < datetime.now(timezone.utc),
                    ),
                    or_(
                        User.last_active == None,
                        User.last_active < cutoff,
                    ),
                    or_(
                        User.re_engaged_at == None,
                        User.re_engaged_at < re_cutoff,
                    ),
                )
            )
            .order_by(User.last_active.asc())
            .limit(_BATCH_SIZE)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # ── Send Logic ──────────────────────────────

    async def _send_message(self, tg_id: int, text: str) -> str:
        """
        ارسال پیام به کاربر.
        برمی‌گردونه:
          "ok"      — موفق
          "blocked" — کاربر ربات رو بلاک/حذف کرده
          "error"   — خطای دیگه
        """
        try:
            await self._bot.send_message(chat_id=tg_id, text=text)
            return "ok"
        except TelegramForbiddenError:
            logger.info(f"ReEngagement: user {tg_id} has blocked the bot — flagging.")
            return "blocked"
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                logger.info(f"ReEngagement: chat {tg_id} not found — flagging.")
                return "blocked"
            logger.warning(f"ReEngagement: TelegramBadRequest for {tg_id}: {e}")
            return "error"
        except Exception as e:
            logger.warning(f"ReEngagement: failed to message {tg_id}: {e}")
            return "error"