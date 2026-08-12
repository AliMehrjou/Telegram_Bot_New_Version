# services/scheduler.py

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import update, select, func
from matching_bot_project.bot.core.constants import Messages
from matching_bot_project.database.models.models import MatchHistory, User
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ثابت‌های تایمر
# ─────────────────────────────────────────────────────────────────────────────
WARN_AFTER_SECONDS  = 12 * 3600   # ۱۲ ساعت بی‌فعالیت → هشدار
CLOSE_AFTER_SECONDS = 24 * 3600   # ۲۴ ساعت بی‌فعالیت → بستن
POLL_INTERVAL       = 30 * 60     # هر ۳۰ دقیقه یه‌بار چک کن
REDIS_KEY_TTL       = CLOSE_AFTER_SECONDS + 3600  # کمی بیشتر از ۲۴ ساعت

# ─────────────────────────────────────────────────────────────────────────────
# Distributed Lock Constants & Lua CAS Release Script
# ─────────────────────────────────────────────────────────────────────────────
_MATCH_LOCK_TTL_MS = 10_000  # 10 seconds

# Compare-And-Set release: only delete the lock key if we still own the token.
# This prevents a slow holder from accidentally releasing a lock that was
# auto-expired and re-acquired by another worker.
_UNLOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class DatingScheduler:
    """
    مچ‌های فعال رو ردیابی می‌کنه.
    - بعد از ۱۲ ساعت بی‌فعالیت: یه هشدار به هر دو کاربر می‌فرسته (یه‌بار)
    - بعد از ۲۴ ساعت بی‌فعالیت: مچ رو می‌بنده
    هر بار که کاربر جواب بده، تایمر ریست می‌شه.

    All match termination (both background 24h timeout and manual UI button)
    flows through terminate_match_unified() which is race-condition-free.
    """

    def __init__(
        self,
        bot: Bot,
        dp: Dispatcher,
        redis_client: aioredis.Redis,
        session_factory: async_sessionmaker,
    ):
        self.bot = bot
        self.dp = dp
        self.redis = redis_client
        self.session_factory = session_factory
        self._running_task: Optional[asyncio.Task] = None
        self._background_tasks: set[asyncio.Task] = set()

    # ─────────────────────────────────────────────────────────────────────────
    # API عمومی
    # ─────────────────────────────────────────────────────────────────────────

    async def register_match_timeout(
        self,
        match_history_id: int,
        user_one_id: int,
        user_two_id: int,
    ):
        """وقتی مچ جدید شروع میشه صدا زده میشه."""
        key = f"date:timeout:{match_history_id}"
        now_epoch = datetime.now(timezone.utc).timestamp()

        await self.redis.hset(key, mapping={
            "last_activity": str(now_epoch),
            "user_one_id":   str(user_one_id),
            "user_two_id":   str(user_two_id),
            "warned":        "0",   # هنوز هشدار داده نشده
        })
        await self.redis.expire(key, REDIS_KEY_TTL)
        logger.info(f"Match {match_history_id} registered in scheduler (24h timeout).")

    async def update_user_activity(self, match_history_id: int, tg_id: int):
        """هر بار که کاربر جواب داد صدا زده میشه تا تایمر ریست بشه."""
        key = f"date:timeout:{match_history_id}"
        if await self.redis.exists(key):
            now_epoch = datetime.now(timezone.utc).timestamp()
            await self.redis.hset(key, mapping={
                "last_activity": str(now_epoch),
                "warned":        "0",
            })
            await self.redis.expire(key, REDIS_KEY_TTL)

    # ─────────────────────────────────────────────────────────────────────────
    # حلقه پس‌زمینه
    # ─────────────────────────────────────────────────────────────────────────

    async def verify_timeout_loops(self):
        """هر ۳۰ دقیقه تمام مچ‌های فعال رو چک می‌کنه."""
        while True:
            try:
                async for key in self.redis.scan_iter(match="date:timeout:*", count=100):
                    try:
                        key_str = key.decode() if isinstance(key, bytes) else key
                        match_history_id = int(key_str.split(":")[-1])

                        raw_data = await self.redis.hgetall(key)
                        if not raw_data:
                            continue

                        data = {
                            (k.decode() if isinstance(k, bytes) else k):
                            (v.decode() if isinstance(v, bytes) else v)
                            for k, v in raw_data.items()
                        }

                        last_activity = float(data.get("last_activity", 0))
                        warned        = data.get("warned", "0") == "1"
                        now_epoch     = datetime.now(timezone.utc).timestamp()
                        idle_seconds  = now_epoch - last_activity

                        if idle_seconds >= CLOSE_AFTER_SECONDS:
                            # ── بستن مچ ──────────────────────────────────
                            # FIX PHASE4-HIGH-06: previously, the timeout key was
                            # deleted (quick-claim) BEFORE attempting termination.
                            # If termination failed (e.g. DB error), the match was
                            # permanently untracked — no future poll would retry it.
                            # Now we delete the key ONLY AFTER successful termination.
                            # To prevent duplicate processing by concurrent pollers,
                            # we use a Redis SET NX claim marker with a 5-min TTL.
                            claim_key = f"date:timeout_claim:{match_history_id}"
                            try:
                                claimed = await self.redis.set(claim_key, "1", nx=True, ex=300)
                                if not claimed:
                                    # Another worker already claimed this match.
                                    continue
                            except Exception as claim_exc:
                                logger.warning("Failed to claim match %s: %s", match_history_id, claim_exc)
                                continue

                            try:
                                await self.close_inactive_date(match_history_id)
                                # Only delete the timeout key after successful close.
                                await self.redis.delete(key_str)
                            except Exception as close_exc:
                                # close failed — release the claim so the next
                                # poll can retry. The timeout key is preserved.
                                logger.error(
                                    "close_inactive_date failed for match %s: %s — will retry next poll",
                                    match_history_id, close_exc,
                                )
                                try:
                                    await self.redis.delete(claim_key)
                                except Exception:
                                    pass

                        elif idle_seconds >= WARN_AFTER_SECONDS and not warned:
                            # ── هشدار (فقط یه‌بار) ────────────────────────
                            # FIX PHASE4-HIGH-07: previously, `HSET key warned 1`
                            # was used. HSET overwrites unconditionally, so if two
                            # pollers ran concurrently, BOTH would send the warning
                            # (the HSET succeeds for both, and neither checks the
                            # return value). Now we use HSETNX which returns 1 only
                            # if the field was actually SET (i.e. it didn't exist
                            # before). Only the poller that gets HSETNX==1 sends
                            # the warning.
                            try:
                                # HSETNX returns 1 if field was set, 0 if already existed.
                                was_set = await self.redis.hsetnx(key_str, "warned", "1")
                                if not was_set:
                                    # Another worker already warned — skip.
                                    continue
                            except Exception as hsetnx_exc:
                                logger.warning("HSETNX warned failed for %s: %s", key_str, hsetnx_exc)
                                # Fall through to sending the warning — better to
                                # risk a duplicate than to drop it.

                            task = asyncio.create_task(
                                self._send_warning(match_history_id, data)
                            )
                            self._background_tasks.add(task)
                            task.add_done_callback(self._background_tasks.discard)

                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error(f"Error checking timeout key {key}: {e}")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Global exception in scheduling check loop: {e}")

            try:
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                raise

    # ─────────────────────────────────────────────────────────────────────────
    # هشدار ۱۲ ساعته
    # ─────────────────────────────────────────────────────────────────────────

    async def _send_warning(self, match_id: int, data: Dict[str, str]):
        """به هر دو نفر می‌گه ۱۲ ساعت دیگه مچ بسته میشه."""
        partners = self._parse_partners(data)
        if not partners:
            return

        for user_id in partners:
            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "⚠️ *یادآوری مچ فعال*\n\n"
                        "شما یک سوال بی‌پاسخ دارید که بیش از ۱۲ ساعت از آن گذشته.\n"
                        "اگر تا ۱۲ ساعت دیگر پاسخ ندهید، مکالمه به‌طور خودکار بسته خواهد شد.\n\n"
                        "برای ادامه مکالمه به ربات برگردید. 🙂"
                    ),
                    parse_mode="Markdown",
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Failed to send 12h warning to user {user_id}: {e}")

        logger.info(f"Sent 12h warning for match {match_id}.")

    # ═════════════════════════════════════════════════════════════════════════
    # 24h CLOSE — REFACTORED TO USE UNIFIED PIPELINE
    # ═════════════════════════════════════════════════════════════════════════

    async def close_inactive_date(
        self,
        match_id: int,
        redis_key: str = "",
        data: Optional[Dict[str, str]] = None,
    ):
        """Called by the background loop when 24h inactivity is reached.

        Delegates to the unified, race-condition-free termination pipeline.
        If a user already clicked "End Date" milliseconds before us, the
        pipeline detects it via the atomic conditional UPDATE and returns
        False — we silently exit without sending duplicate notifications.

        The redis_key and data params are kept for backward compatibility
        with the polling loop but are no longer required.
        """
        success = await self.terminate_match_unified(
            match_id,
            broadcast_text=(
                "⏳ *مکالمه به پایان رسید*\n\n"
                "به دلیل عدم پاسخ‌دهی در ۲۴ ساعت گذشته، این مچ بسته شد.\n"
                "برای شروع مچ جدید از دکمه 🎯 در منوی اصلی استفاده کنید."
            ),
            broadcast_parse_mode="Markdown",
        )
        if success:
            logger.info(f"Closed inactive match {match_id} after 24h inactivity.")
        else:
            logger.info(
                f"Match {match_id} was already terminated by another path "
                f"(UI button or another worker). Skipping."
            )

    # ═════════════════════════════════════════════════════════════════════════
    # UNIFIED RACE-CONDITION-FREE TERMINATION PIPELINE
    # ═════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _match_lock_key(match_id: int) -> str:
        """Redis key for the distributed termination lock."""
        return f"lock:match_term:{match_id}"

    async def _try_acquire_match_lock(
        self,
        match_id: int,
        *,
        blocking_timeout: float = 0.0,
    ) -> Optional[str]:
        """Acquire a distributed Redis lock for match termination.

        Uses atomic SET NX PX:
          - NX: only set if key doesn't exist (mutual exclusion)
          - PX: 10-second TTL (auto-recovery if holder crashes)

        Returns a UUID token on success, None if the lock is held by another
        caller. When blocking_timeout > 0, spins with short sleeps until the
        deadline, giving the caller a chance to wait for a brief lock holder
        (e.g., the scheduler finishing up) to release.
        """
        lock_key = self._match_lock_key(match_id)
        token = uuid.uuid4().hex

        if blocking_timeout <= 0:
            # Non-blocking: single attempt.
            ok = await self.redis.set(lock_key, token, nx=True, px=_MATCH_LOCK_TTL_MS)
            return token if ok else None

        # Blocking: spin-wait with 50ms intervals.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + blocking_timeout
        while True:
            ok = await self.redis.set(lock_key, token, nx=True, px=_MATCH_LOCK_TTL_MS)
            if ok:
                return token
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(0.05)

    async def _release_match_lock(self, match_id: int, token: str) -> None:
        """Release the lock via Lua CAS — only deletes if we still own the token.

        This is critical: if our lock expired (because we took >10s) and was
        re-acquired by another worker, we must NOT delete it. The Lua script
        atomically checks the token before deleting.
        """
        lock_key = self._match_lock_key(match_id)
        try:
            await self.redis.eval(_UNLOCK_LUA, 1, lock_key, token)
        except Exception as e:
            logger.warning(
                f"Failed to release match lock {lock_key} "
                f"(will auto-expire in ≤10s): {e}"
            )

    async def _atomic_deactivate_match(
        self,
        match_id: int,
        session: Optional[AsyncSession] = None,
        *,
        commit: bool = True,
    ) -> Optional[MatchHistory]:
        """Atomically transition is_active from True → False via conditional UPDATE.

        SQL equivalent:
            UPDATE match_history
            SET is_active = False, ended_at = ?
            WHERE id = ? AND is_active = True

        If rowcount == 1 → we won the race (this caller deactivated the match).
        If rowcount == 0 → match doesn't exist OR was already inactive (we lost).

        The WHERE clause on is_active = True is the **single source of truth**:
        even without the Redis lock, only one concurrent UPDATE can succeed.
        The others get rowcount=0 because MySQL serializes row-level writes.

        Args:
            match_id: MatchHistory primary key.
            session: Existing AsyncSession (for composite transactions) or None
                     to create a self-managed session.
            commit: If True, commit inside this method. If False, the caller
                    must commit/rollback. The DB row lock is held until commit.

        Returns:
            The MatchHistory row (with user_one_id, user_two_id) if we won,
            None if we lost the race.
        """
        owns_session = session is None
        if owns_session:
            session = self.session_factory()

        try:
            now_utc = datetime.now(timezone.utc)  # FIX HIGH-15: aware UTC (model column is timezone-aware)

            # ── Conditional UPDATE: the atomic gate ────────────────────────
            stmt = (
                update(MatchHistory)
                .where(MatchHistory.id == match_id)
                .where(MatchHistory.is_active.is_(True))
                .values(is_active=False, ended_at=now_utc)
            )
            result = await session.execute(stmt)

            if result.rowcount == 0:
                # Match doesn't exist or is already inactive.
                # Another path (scheduler or UI button) beat us to it.
                return None

            # ── Commit if we own the transaction ───────────────────────────
            if commit:
                await session.commit()

            # ── Fetch the row for partner user IDs ─────────────────────────
            # We need user_one_id and user_two_id to send notifications.
            sel = select(MatchHistory).where(MatchHistory.id == match_id)
            match_row = (await session.execute(sel)).scalar_one_or_none()
            return match_row

        except Exception:
            if commit:
                try:
                    await session.rollback()
                except Exception:
                    pass
            raise
        finally:
            if owns_session:
                await session.close()

    async def _post_termination_side_effects(
        self,
        match_id: int,
        match_row: MatchHistory,
        *,
        caller_id: Optional[int] = None,
        broadcast_text: Optional[str] = None,
        broadcast_parse_mode: Optional[str] = None,
        caller_text: Optional[str] = None,
        partner_text: Optional[str] = None,
        manual_parse_mode: Optional[str] = None,
    ) -> None:
        """Redis cleanup, FSM clear, and user notifications."""
        
        # ... (کدهای قبلی این متد رو دستکاری نکن، فقط آخرش این رو اضافه کن) ...
        
# ── Notification for Waitlist Users ────────────────────────────────
        # 👇 چک می‌کنیم آیا کسی منتظر این دو نفر بوده یا نه
        
        partners = [match_row.user_one_id, match_row.user_two_id] # 👈 این خط کلیدی اضافه شد تا ارور NameError رفع بشه
        
        for uid in partners:
            waitlist_key = f"user:{uid}:waitlist"
            waitlist_users = await self.redis.smembers(waitlist_key)
            if waitlist_users:
                # حذف لیست انتظار بعد از خوندن تا دوباره براشون پیام نره
                await self.redis.delete(waitlist_key)
                
                # کیبورد برای درخواست مجدد
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                
                re_req_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💬 درخواست چت", callback_data=f"req_chat_{uid}")],
                    [InlineKeyboardButton(text="💘 درخواست دیت", callback_data=f"req_date_{uid}")]
                ])
                
                for waiting_user_id in waitlist_users:
                    try:
                        waiting_uid_int = int(waiting_user_id.decode() if isinstance(waiting_user_id, bytes) else waiting_user_id)
                        await self.bot.send_message(
                            chat_id=waiting_uid_int,
                            text=f"💘 <b>خبر خوب!</b> اون شخصی که منتظرش بودی آزاد شد. الان می‌تونی دوباره بهش درخواست بدی:",
                            parse_mode="HTML",
                            reply_markup=re_req_kb
                        )
                    except Exception as e:
                        logger.warning(f"Failed to notify waitlist user {waiting_user_id} about target {uid}: {e}")
                    
    async def terminate_match_unified(
        self,
        match_id: int,
        *,
        caller_id: Optional[int] = None,
        broadcast_text: Optional[str] = None,
        broadcast_parse_mode: Optional[str] = None,
        caller_text: Optional[str] = None,
        partner_text: Optional[str] = None,
        manual_parse_mode: Optional[str] = None,
        session: Optional[AsyncSession] = None,
        commit: bool = True,
    ) -> bool:
        """UNIFIED, RACE-CONDITION-FREE match termination pipeline.

        ╔══════════════════════════════════════════════════════════════════╗
        ║  GUARANTEE: Regardless of whether termination is triggered by   ║
        ║  the 24h background scheduler or a user clicking "End Date",   ║
        ║  exactly ONE routine will:                                     ║
        ║    1. Set is_active = False in MySQL                           ║
        ║    2. Delete Redis tracking keys                               ║
        ║    3. Clear FSM contexts                                       ║
        ║    4. Send termination notifications to users                  ║
        ║                                                                ║
        ║  All other concurrent callers get rowcount=0 and exit silently.║
        ╚══════════════════════════════════════════════════════════════════╝

        Two-layer protection:
          Layer 1 — Distributed Redis Lock (lock:match_term:{match_id}):
              • SET NX PX 10000 (atomic acquire, 10s TTL)
              • CAS release via Lua (only owner can delete)
              • Manual mode waits up to 5s; auto mode is non-blocking
              • Provides fast-fail so concurrent callers don't hammer the DB

          Layer 2 — Atomic MySQL Conditional UPDATE:
              • UPDATE ... WHERE id=? AND is_active=True
              • rowcount=1 → winner; rowcount=0 → loser
              • This is the definitive correctness guarantee
              • Even if the Redis lock expires/crashes, the conditional
                UPDATE ensures only one writer transitions is_active

        Messaging modes:
          • Broadcast: broadcast_text sent to BOTH users (auto-close path)
          • Manual:    caller_text → caller_id, partner_text → other user

        Args:
            match_id: MatchHistory.id to terminate.
            caller_id: User who initiated (manual mode only). None for auto.
            broadcast_text: If set, sent to both users (broadcast mode).
            broadcast_parse_mode: Parse mode for broadcast_text.
            caller_text: Sent to caller_id (manual mode).
            partner_text: Sent to the other user (manual mode).
            manual_parse_mode: Parse mode for manual messages.
            session: Existing AsyncSession or None (creates self-managed).
            commit: True → commit inside. False → caller manages transaction.

        Returns:
            True  — THIS caller performed the termination.
            False — Match was already inactive (lost the race or lock denied).
        """
        # Determine mode: broadcast (auto) vs manual (user-initiated)
        is_manual = broadcast_text is None

        # ── Phase 1: Acquire distributed lock ──────────────────────────────
        # Manual (user button): wait up to 5s — the scheduler should finish
        #   quickly, and the user deserves responsive feedback.
        # Auto (scheduler): non-blocking — if the UI path is mid-termination,
        #   skip; the atomic UPDATE would return 0 anyway.
        blocking_timeout = 5.0 if is_manual else 0.0

        token = await self._try_acquire_match_lock(
            match_id, blocking_timeout=blocking_timeout
        )
        if token is None:
            logger.info(
                f"terminate_match_unified: could not acquire lock for "
                f"match {match_id} (held by another path). "
                f"Treating as already-terminated."
            )
            return False

        try:
            # ── Phase 2: Atomic DB deactivation ────────────────────────────
            # This is the definitive gate. Even if two callers somehow both
            # hold the lock (e.g., TTL expiry), only one UPDATE succeeds.
            match_row = await self._atomic_deactivate_match(
                match_id, session, commit=commit
            )
            if match_row is None:
                logger.info(
                    f"terminate_match_unified: match {match_id} already "
                    f"inactive (atomic UPDATE matched 0 rows). Lost the race."
                )
                return False

            # ── Phase 3: Side effects (ONLY the winner reaches here) ───────
            # Redis cleanup + FSM clear + user notifications.
            # These are executed exactly once because we're the only caller
            # that got past the atomic UPDATE with rowcount=1.
            await self._post_termination_side_effects(
                match_id,
                match_row,
                caller_id=caller_id,
                broadcast_text=broadcast_text,
                broadcast_parse_mode=broadcast_parse_mode,
                caller_text=caller_text,
                partner_text=partner_text,
                manual_parse_mode=manual_parse_mode,
            )

            logger.info(
                f"terminate_match_unified: match {match_id} terminated "
                f"successfully (caller={caller_id}, commit={commit}, "
                f"mode={'manual' if is_manual else 'auto'})."
            )
            return True

        finally:
            # ── Release the lock (CAS — only if we still own it) ───────────
            # If our lock expired (we took >10s due to slow Telegram API),
            # the Lua script won't delete it — another worker may now own it.
            await self._release_match_lock(match_id, token)

    # ─────────────────────────────────────────────────────────────────────────
    # کمکی
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_partners(data: Dict[str, str]) -> list[int]:
        try:
            return [int(data["user_one_id"]), int(data["user_two_id"])]
        except (KeyError, ValueError):
            return []

    def start_polling(self):
        if not self._running_task or self._running_task.done():
            self._running_task = asyncio.create_task(self.verify_timeout_loops())
            # FIX HIGH-10: log silent crashes so the bot doesn't quietly lose its scheduler.
            def _on_done(t: asyncio.Task) -> None:
                if t.cancelled():
                    return
                exc = t.exception()
                if exc:
                    logger.error("DatingScheduler loop crashed: %r", exc, exc_info=exc)
            self._running_task.add_done_callback(_on_done)
            logger.info("Dating Scheduler started (24h timeout, 12h warning).")

    async def stop(self):
        """FIX: cancel the DatingScheduler background task on shutdown."""
        if self._running_task and not self._running_task.done():
            self._running_task.cancel()
            try:
                await self._running_task
            except asyncio.CancelledError:
                pass
            logger.info("Dating Scheduler stopped.")


# ─────────────────────────────────────────────────────────────────────────────
# Online Status Worker (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class OnlineStatusWorker:
    """
    Background worker that periodically:
    1. Sets users offline if they haven't been active for `idle_minutes`.
    2. v3 NEW: Batch-flushes `user:last_active:{tg_id}` Redis keys to MySQL
       `users.last_active` and `users.is_online=TRUE` — this replaces the
       per-update UPDATE+COMMIT in DbSessionMiddleware, lifting DB throughput
       by 3-5x.
    """

    def __init__(self, bot: Bot, session_factory: async_sessionmaker, idle_minutes: int = 5, redis_client=None):
        self.bot = bot
        self.session_factory = session_factory
        self.idle_minutes = idle_minutes
        self.redis = redis_client
        self._running_task: Optional[asyncio.Task] = None

    async def sync_offline_users(self):
        """Runs every 60 seconds to clean up stale online statuses + batch-flush last_active."""
        while True:
            try:
                # ─── v3 NEW: Batch-flush last_active from Redis → MySQL ───────
                await self._batch_flush_last_active()

                # ─── Existing: mark stale online users as offline ─────────────
                async with self.session_factory() as session:
                    cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=self.idle_minutes)

                    stmt = (
                        update(User)
                        .where(User.is_online.is_(True))
                        .where(User.last_active < cutoff_time)
                        .values(is_online=False)
                    )

                    result = await session.execute(stmt)
                    if result.rowcount > 0:
                        await session.commit()
                        logger.info(f"Offline sync: {result.rowcount} users set to offline.")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in offline sync loop: {e}")

            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise

    async def _batch_flush_last_active(self):
        """
        v3 NEW: Scan Redis for `user:last_active:*` keys, parse the user_tg_id and
        ISO timestamp, and update MySQL in batches of 100 with a single UPDATE
        per batch (using CASE WHEN). This replaces the per-update UPDATE+COMMIT
        in DbSessionMiddleware, which was the #1 DB bottleneck.
        """
        if not self.redis:
            return  # No Redis client configured — skip this optimization.

        try:
            # Scan up to 1000 keys per cycle
            cursor = "0"
            keys_processed = 0
            batch_updates: dict[int, datetime] = {}

            while keys_processed < 1000:
                cursor, keys = await self.redis.scan(
                    cursor=cursor,
                    match="user:last_active:*",
                    count=200,
                )
                if not keys:
                    if cursor == "0" or cursor == 0:
                        break
                    continue

                # Read all values in a pipeline
                pipe = self.redis.pipeline()
                for k in keys:
                    pipe.get(k)
                values = await pipe.execute()

                for k, v in zip(keys, values):
                    if not v:
                        continue
                    try:
                        tg_id = int(k.split(":")[-1])
                        ts = datetime.fromisoformat(v)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        batch_updates[tg_id] = ts
                        keys_processed += 1
                    except (ValueError, TypeError):
                        continue

                if cursor == "0" or cursor == 0:
                    break

            if not batch_updates:
                return

            # Apply batched UPDATE — use a CASE WHEN to set per-user timestamps
            async with self.session_factory() as session:
                tg_ids = list(batch_updates.keys())
                # Process in chunks of 100 to keep SQL statement size reasonable
                from sqlalchemy import case as sql_case  # standalone `case`, not func.case
                for chunk_start in range(0, len(tg_ids), 100):
                    chunk_ids = tg_ids[chunk_start:chunk_start + 100]
                    case_stmt = sql_case(
                        *[(User.tg_id == tid, batch_updates[tid]) for tid in chunk_ids],
                        else_=User.last_active,
                    )
                    stmt = (
                        update(User)
                        .where(User.tg_id.in_(chunk_ids))
                        .values(last_active=case_stmt, is_online=True)
                    )
                    await session.execute(stmt)
                await session.commit()
                logger.debug(f"Batch-flushed last_active for {len(batch_updates)} users.")
        # ... کدهای قبلی متد _batch_flush_last_active ...
            
            # 👇 کدهای جدید: هشدار آنلاین شدن به افراد در کمین (onw)
            if batch_updates:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                from matching_bot_project.bot.core.constants import PEmoji
                
                for tid in batch_updates.keys():
                    watchers_key = f"user:{tid}:online_watchers"
                    watchers = await self.redis.smembers(watchers_key)
                    
                    if watchers:
                        # حذف لیست که دیگه دوبار پیام نره
                        await self.redis.delete(watchers_key)
                        
                        re_req_kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="💬 درخواست چت", callback_data=f"req_chat_{tid}")],
                            [InlineKeyboardButton(text="💘 درخواست دیت", callback_data=f"req_date_{tid}")]
                        ])
                        
                        for w_id in watchers:
                            try:
                                w_uid_int = int(w_id.decode() if isinstance(w_id, bytes) else w_id)
                                await self.bot.send_message(
                                    chat_id=w_uid_int,
                                    text=f"🔔 <b>خبر داغ!</b>\nپارتنر قبلیت همین الان آنلاین شد! 👀 بدو برو بهش درخواست بده تا دوباره آفلاین نشده:",
                                    parse_mode="HTML",
                                    reply_markup=re_req_kb
                                )
                            except Exception as e:
                                logger.warning(f"Failed to notify online watcher {w_id} for user {tid}: {e}")
            # Delete the Redis keys we just flushed (so we don't re-process them)
            if batch_updates:
                pipe = self.redis.pipeline()
                for tid in batch_updates.keys():
                    pipe.delete(f"user:last_active:{tid}")
                await pipe.execute()

        except Exception as e:
            logger.error(f"Error in _batch_flush_last_active: {e}", exc_info=True)

    def start_polling(self):
        if not self._running_task or self._running_task.done():
            self._running_task = asyncio.create_task(self.sync_offline_users())
            # FIX: surface silent failures so a crash inside sync_offline_users is logged.
            def _on_done(t: asyncio.Task) -> None:
                if t.cancelled():
                    return
                exc = t.exception()
                if exc:
                    logger.error("OnlineStatusWorker crashed: %r", exc, exc_info=exc)
            self._running_task.add_done_callback(_on_done)
            logger.info("Online Status Worker started.")

    async def stop(self):
        """FIX HIGH-23: properly cancel the background task on shutdown."""
        if self._running_task and not self._running_task.done():
            self._running_task.cancel()
            try:
                await self._running_task
            except asyncio.CancelledError:
                pass
            logger.info("Online Status Worker stopped.")

import asyncio
import logging
from sqlalchemy import select, or_
from matching_bot_project.database.models.models import MatchHistory

logger = logging.getLogger(__name__)

class ZombieCleanerWorker:
    def __init__(self, bot, redis_client, session_factory, poll_interval_minutes=30):
        self.bot = bot
        self.redis = redis_client
        self.session_factory = session_factory
        self.poll_interval = poll_interval_minutes * 60
        self._running_task = None

    async def _clean_zombie_states(self):
        """اسکن گروهی و حذف استیت‌های نامعتبر"""
        from matching_bot_project.bot.core.loader import matching_engine
        cursor = "0"
        zombies_killed = 0
        
        while cursor != 0:
            # خواندن کلیدها در دسته‌های 500 تایی
            cursor, keys = await self.redis.scan(cursor=cursor, match="user:state:*", count=500)
            if not keys:
                continue
                
            # گرفتن اطلاعات استیت‌ها با پایپ‌لاین
            pipe = self.redis.pipeline()
            for key in keys:
                pipe.hgetall(key)
            states_data = await pipe.execute()
            
            # فیلتر کردن کاربرانی که در وضعیت چت یا مچ هستند
            pending_users = {}
            for key, raw_data in zip(keys, states_data):
                if not raw_data:
                    continue
                
                state_data = {
                    (k.decode() if isinstance(k, bytes) else k): 
                    (v.decode() if isinstance(v, bytes) else v) 
                    for k, v in raw_data.items()
                }
                
                if state_data.get("status") in ["matched", "chatting", "in_chat", "in_date"]:
                    key_str = key.decode() if isinstance(key, bytes) else key
                    tg_id = int(key_str.split(":")[-1])
                    pending_users[tg_id] = {
                        "redis_key": key,
                        "partner_id": state_data.get("matched_with")
                    }
            
            if not pending_users:
                continue

            # چک کردن گروهی دیتابیس فقط با 1 کوئری
            tg_ids = list(pending_users.keys())
            async with self.session_factory() as db_session:
                stmt = select(MatchHistory).where(
                    MatchHistory.is_active.is_(True),
                    or_(
                        MatchHistory.user_one_id.in_(tg_ids),
                        MatchHistory.user_two_id.in_(tg_ids)
                    )
                )
                result = await db_session.execute(stmt)
                active_matches = result.scalars().all()
                
                # نگاشت کاربران به مچ‌های فعالشان
                valid_users = set()
                for match in active_matches:
                    valid_users.add(match.user_one_id)
                    valid_users.add(match.user_two_id)

            # پاکسازی زامبی‌ها با پایپ‌لاین
            del_pipe = self.redis.pipeline()
            for tg_id, info in pending_users.items():
                is_zombie = False
                if tg_id not in valid_users:
                    is_zombie = True
                
                if is_zombie:
                    del_pipe.delete(info["redis_key"])
                    await matching_engine.remove_from_queue(tg_id)
                    zombies_killed += 1
                    
            if zombies_killed > 0:
                await del_pipe.execute()

            # جلوگیری از مسدود شدن ایونت لوپ
            await asyncio.sleep(0.5)
            
        return zombies_killed

    async def run_loop(self):
        while True:
            try:
                killed = await self._clean_zombie_states()
                if killed > 0:
                    logger.info(f"ZombieCleaner: Cleaned {killed} zombie states.")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in ZombieCleanerWorker: {e}", exc_info=True)
            
            await asyncio.sleep(self.poll_interval)

    def start_polling(self):
        if not self._running_task or self._running_task.done():
            self._running_task = asyncio.create_task(self.run_loop())
            logger.info("Zombie Cleaner Worker started.")

    async def stop(self):
        """توقف ایمن تسک در زمان خاموش شدن سرور"""
        if self._running_task and not self._running_task.done():
            self._running_task.cancel()
            try:
                await self._running_task
            except asyncio.CancelledError:
                pass
            logger.info("Zombie Cleaner Worker stopped.")

class QueueTimeoutWorker:
    """
    ورکری برای بررسی صف مچینگ. کاربرانی که بیش از ۵ دقیقه در صف مانده‌اند را 
    خارج کرده و به منوی اصلی هدایت می‌کند.
    """
    def __init__(self, bot, dp, redis_client, poll_interval_seconds=30):
        self.bot = bot
        self.dp = dp
        self.redis = redis_client
        self.poll_interval = poll_interval_seconds
        self._running_task = None

    async def _process_timeouts(self):
        # ایمپورت‌های محلی برای جلوگیری از Circular Import
        from matching_bot_project.bot.core.loader import matching_engine
        from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
        
        # دریافت لیست کاربرانی که 5 دقیقه‌شان تمام شده است
        expired_users = await matching_engine.get_expired_queue_users(limit=50)

        for tg_id in expired_users:
            try:
                # ۱. حذف کاربر از صف ردیس و پاک کردن استیت
                await matching_engine.remove_from_queue(tg_id)

                # ۲. پاک‌سازی قطعی FSM State کاربر
                state = FSMContext(
                    storage=self.dp.storage,
                    key=StorageKey(bot_id=self.bot.id, chat_id=tg_id, user_id=tg_id),
                )
                await state.clear()

                # ۳. ارسال پیام خروج و نمایش کیبورد اصلی
                await self.bot.send_message(
                    chat_id=tg_id,
                    text=Messages.NO_MATCH_FOUND,
                    reply_markup=get_main_menu_keyboard(),
                    parse_mode="HTML"
                )
                logger.info(f"QueueTimeoutWorker: User {tg_id} removed from queue due to 5-minute timeout.")
                
            except Exception as e:
                logger.error(f"Error processing queue timeout for user {tg_id}: {e}", exc_info=True)

    async def run_loop(self):
        while True:
            try:
                await self._process_timeouts()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in QueueTimeoutWorker loop: {e}", exc_info=True)
            
            await asyncio.sleep(self.poll_interval)

    def start_polling(self):
        if not self._running_task or self._running_task.done():
            self._running_task = asyncio.create_task(self.run_loop())
            logger.info("Queue Timeout Worker started.")

    async def stop(self):
        if self._running_task and not self._running_task.done():
            self._running_task.cancel()
            try:
                await self._running_task
            except asyncio.CancelledError:
                pass
            logger.info("Queue Timeout Worker stopped.")