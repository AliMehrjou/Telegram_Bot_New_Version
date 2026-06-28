"""
Re-engagement Worker
────────────────────
هر ۶ ساعت یه‌بار اجرا می‌شه و کاربرانی که ۳ روز است
هیچ تعاملی با ربات نداشتن رو با یه پیام دوستانه ping می‌کنه.

ستون‌های مورد نیاز در جدول users:
  - last_active        : DateTime  (آخرین تعامل کاربر)
  - re_engaged_at      : DateTime  (آخرین باری که پیام re-engagement فرستادیم)
  - re_engage_blocked  : Boolean   (اگه کاربر ربات رو بلاک کرده، True می‌شه)
  - completed_registration : Boolean
  - silent_until       : DateTime  (اگه کاربر سایلنت باشه skip می‌کنیم)
  - tg_id              : BigInteger
"""

import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from sqlalchemy import select, update, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matching_bot_project.database.models.models import User

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# تنظیمات
# ──────────────────────────────────────────────
_INACTIVE_DAYS      = 3        # کاربر چند روز بی‌تحرک باشه
_COOLDOWN_DAYS      = 3        # حداقل فاصله بین دو پیام re-engagement
_CHECK_INTERVAL_SEC = 6 * 3600 # هر ۶ ساعت یه‌بار چک کن
_BATCH_SIZE         = 50       # هر دور چند نفر رو پیام بده
_SEND_DELAY_SEC     = 0.05     # تاخیر بین هر ارسال (جلوگیری از flood)

# ──────────────────────────────────────────────
# متن‌های پیام (رندوم انتخاب می‌شن)
# ──────────────────────────────────────────────
_MESSAGES = [
    (
        "👋 سلام، دلمون برات تنگ شده!\n\n"
        "چند روزیه نبودی — یه سر بزن، شاید یه مچ خوب منتظرته 🎯"
    ),
    (
        "✨ هنوز اینجاییم!\n\n"
        "آدم‌های جدید توی ربات فعال شدن، شاید یکیشون دقیقاً دنبال توئه 😊\n"
        "بیا یه نگاهی بنداز!"
    ),
    (
        "🔔 یادآوری دوستانه:\n\n"
        "پروفایلت هنوز فعاله و داره دیده می‌شه —\n"
        "ولی اگه برگردی شانست خیلی بیشتر می‌شه! 🚀"
    ),
    (
        "💬 یه وقتایی آدم مشغول می‌شه، می‌فهمیم!\n\n"
        "هر وقت حال داشتی برگرد، ما اینجاییم 🙌\n"
        "شاید یه چت جالب منتظرته!"
    ),
    (
        "🎲 یه فرصت جدید برات داریم!\n\n"
        "همین الان وارد شو و ببین چه آدم‌هایی آنلاین هستن 👀"
    ),
]


class ReEngagementWorker:
    """
    Worker ری‌اینگیجمنت کاربران بی‌تحرک.

    استفاده:
        worker = ReEngagementWorker(session_factory, bot)
        worker.start_polling()          # داخل lifespan
        await worker.stop()             # داخل teardown
    """

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
        # اولین اجرا رو ۶۰ ثانیه بعد از استارت می‌ذاریم تا DB کامل آماده بشه
        await asyncio.sleep(60)

        while True:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ReEngagementWorker: unexpected error in run cycle.")
            await asyncio.sleep(_CHECK_INTERVAL_SEC)

    async def _run_once(self) -> None:
        """یه سیکل کامل: پیدا کردن کاربران بی‌تحرک و ارسال پیام."""
        now        = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff     = now - timedelta(days=_INACTIVE_DAYS)
        re_cutoff  = now - timedelta(days=_COOLDOWN_DAYS)

        async with self._session_factory() as session:
            users = await self._fetch_inactive_users(session, cutoff, re_cutoff)

        if not users:
            logger.debug("ReEngagementWorker: no inactive users found.")
            return

        logger.info(f"ReEngagementWorker: sending re-engagement to {len(users)} users.")

        sent = blocked = errors = 0

        for user_tg_id in users:
            result = await self._send_message(user_tg_id)

            async with self._session_factory() as session:
                if result == "ok":
                    await self._mark_engaged(session, user_tg_id, now)
                    sent += 1
                elif result == "blocked":
                    await self._mark_blocked(session, user_tg_id)
                    blocked += 1
                else:
                    errors += 1

            await asyncio.sleep(_SEND_DELAY_SEC)

        logger.info(
            f"ReEngagementWorker cycle done — "
            f"sent={sent}, blocked/removed={blocked}, errors={errors}"
        )

    # ── DB Helpers ──────────────────────────────

    @staticmethod
    async def _fetch_inactive_users(
        session: AsyncSession,
        cutoff: datetime,
        re_cutoff: datetime,
    ) -> list[int]:
        """
        کاربرانی رو برمی‌گردونه که:
          - ثبت‌نام کامل دارن
          - بلاک نشدن (re_engage_blocked = False)
          - سایلنت نیستن
          - آخرین تعاملشون قدیمی‌تر از cutoff هست
          - یا اصلاً پیام re-engagement نگرفتن، یا آخرین پیام از re_cutoff قدیمی‌تره
        """
        stmt = (
            select(User.tg_id)
            .where(
                and_(
                    User.completed_registration == True,
                    User.re_engage_blocked != True,
                    # سایلنت نباشن
                    or_(
                        User.silent_until == None,
                        User.silent_until < datetime.now(timezone.utc).replace(tzinfo=None),
                    ),
                    # بی‌تحرک باشن
                    or_(
                        User.last_active == None,
                        User.last_active < cutoff,
                    ),
                    # کولداون رعایت شده باشه
                    or_(
                        User.re_engaged_at == None,
                        User.re_engaged_at < re_cutoff,
                    ),
                )
            )
            .limit(_BATCH_SIZE)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def _mark_engaged(session: AsyncSession, tg_id: int, now: datetime) -> None:
        await session.execute(
            update(User)
            .where(User.tg_id == tg_id)
            .values(re_engaged_at=now)
        )
        await session.commit()

    @staticmethod
    async def _mark_blocked(session: AsyncSession, tg_id: int) -> None:
        await session.execute(
            update(User)
            .where(User.tg_id == tg_id)
            .values(re_engage_blocked=True)
        )
        await session.commit()

    # ── Send Logic ──────────────────────────────

    async def _send_message(self, tg_id: int) -> str:
        """
        ارسال پیام به کاربر.
        برمی‌گردونه:
          "ok"      — موفق
          "blocked" — کاربر ربات رو بلاک/حذف کرده
          "error"   — خطای دیگه
        """
        text = random.choice(_MESSAGES)
        try:
            await self._bot.send_message(chat_id=tg_id, text=text)
            return "ok"
        except TelegramForbiddenError:
            # کاربر ربات رو بلاک کرده
            logger.info(f"ReEngagement: user {tg_id} has blocked the bot — flagging.")
            return "blocked"
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                # کاربر ربات رو حذف کرده یا اکانتش پاک شده
                logger.info(f"ReEngagement: chat {tg_id} not found — flagging.")
                return "blocked"
            logger.warning(f"ReEngagement: TelegramBadRequest for {tg_id}: {e}")
            return "error"
        except Exception as e:
            logger.warning(f"ReEngagement: failed to message {tg_id}: {e}")
            return "error"