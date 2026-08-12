"""
services/cron_reminders.py

v3 NEW: Time-based reminder crons.

Cron jobs:
1. Profile completion reminder (24h after registration, then daily):
   - For users who haven't completed their profile.
   - Sends Messages.PROFILE_COMPLETION_REMINDER.
   - Updates User.last_profile_reminder_at to avoid spamming.

2. Chat silence reminder (after 30 min of silence, then every 24h):
   - For active chat/date sessions where neither party has messaged.
   - Sends Messages.CHAT_SILENCE_REMINDER.
   - Implemented via Redis (last_message_at timestamp per chat session).

3. VIP expiry reminder (3 days before expiry):
   - Notifies user their VIP is expiring soon.

4. (Existing) Re-engagement for inactive users (3+ days) — kept in reengagement.py.
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, and_, update, or_

from matching_bot_project.bot.core.constants import Messages as SystemMsg
from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)


class CronRemindersService:
    """Background cron service for timed reminders."""

    def __init__(self, bot, redis_client, session_factory):
        self.bot = bot
        self.redis = redis_client
        self.session_factory = session_factory
        self._profile_reminder_task: Optional[asyncio.Task] = None
        self._chat_silence_task: Optional[asyncio.Task] = None
        self._vip_expiry_task: Optional[asyncio.Task] = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._profile_reminder_task = asyncio.create_task(self._profile_reminder_loop())
        self._chat_silence_task = asyncio.create_task(self._chat_silence_loop())
        self._vip_expiry_task = asyncio.create_task(self._vip_expiry_loop())
        logger.info("CronRemindersService started (profile, chat-silence, vip-expiry).")

    async def stop(self):
        self._running = False
        for task in [self._profile_reminder_task, self._chat_silence_task, self._vip_expiry_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("CronRemindersService stopped.")

    # ─── Profile completion reminder (every 1 hour, checks daily per-user) ───
    async def _profile_reminder_loop(self):
        while self._running:
            try:
                await asyncio.sleep(3600)  # check every hour
                await self._send_profile_reminders()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("profile_reminder_loop error: %s", e, exc_info=True)
                await asyncio.sleep(60)

    async def _send_profile_reminders(self):
        """Send reminders to users who haven't completed profile (max 50 per cycle)."""
        from matching_bot_project.database.models.models import User
        from matching_bot_project.services.profile_completion import ProfileCompletionService

        pc_service = ProfileCompletionService(redis_client=self.redis)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        async with self.session_factory() as session:
            result = await session.execute(
                select(User).where(
                    and_(
                        User.completed_registration == True,
                        User.is_banned == False,
                        User.profile_completion_rewarded == False,
                        # Only remind users registered > 2 minutes ago (first reminder grace)
                        User.created_at < datetime.now(timezone.utc) - timedelta(minutes=2),
                        # Either never reminded, or last reminded > 24h ago
                        or_(User.last_profile_reminder_at == None,
                            User.last_profile_reminder_at < cutoff),
                    )
                ).order_by(User.created_at.desc()).limit(50)
            )
            users_to_remind = result.scalars().all()

            for user in users_to_remind:
                try:
                    text = Messages.PROFILE_COMPLETION_REMINDER.format(
                        first_name=user.first_name
                    )
                    await self.bot.send_message(user.tg_id, text)
                    # Update reminder timestamp
                    await session.execute(
                        update(User)
                        .where(User.tg_id == user.tg_id)
                        .values(last_profile_reminder_at=datetime.now(timezone.utc))
                    )
                    await asyncio.sleep(0.05)  # 20 msg/sec rate
                except Exception as e:
                    logger.warning("Failed to send profile reminder to %s: %s", user.tg_id, e)
            await session.commit()

    # ─── Chat silence reminder (every 5 min, checks 30-min silence) ──────────
    async def _chat_silence_loop(self):
        while self._running:
            try:
                await asyncio.sleep(300)  # check every 5 min
                await self._send_silence_reminders()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("chat_silence_loop error: %s", e, exc_info=True)
                await asyncio.sleep(60)

    async def _send_silence_reminders(self):
        """Send reminders to silent chat/date sessions."""
        # Scan Redis for active chat sessions: chat:last_msg:{match_id} keys
        # If last_msg_ts is > 30 min ago, send silence reminder to both parties.
        try:
            cursor = b"0"
            now_ts = datetime.now(timezone.utc).timestamp()
            thirty_min_ago = now_ts - 1800

            while cursor:
                cursor, keys = await self.redis.scan(
                    cursor=cursor,
                    match="chat:last_msg:*",
                    count=100,
                )
                if not keys:
                    continue
                for key in keys:
                    try:
                        last_ts_str = await self.redis.get(key)
                        if not last_ts_str:
                            continue
                        last_ts = float(last_ts_str)
                        if last_ts > thirty_min_ago:
                            continue  # not silent yet

                        # Extract match_id and partner info
                        match_id = key.split(":")[-1]
                        await self._send_silence_for_match(match_id)
                    except Exception as e:
                        logger.warning("Silence check failed for key %s: %s", key, e)

                if cursor == b"0" or cursor == 0:
                    break
        except Exception as e:
            logger.error("send_silence_reminders error: %s", e, exc_info=True)

    async def _send_silence_for_match(self, match_id: str):
        """Send silence reminder to both parties in a match.

        FIX PHASE4-HIGH-04: previously, this fired every 5 minutes for as
        long as the chat stayed silent — up to ~18 reminders per user per
        silent match. Now we use a Redis SET NX key per (match_id, user_id)
        with a 24h TTL to ensure only ONE silence reminder per user per
        match per day.
        """
        from matching_bot_project.database.models.models import MatchHistory, User
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(MatchHistory).where(
                        and_(
                            MatchHistory.id == int(match_id),
                            MatchHistory.is_active == True,
                        )
                    )
                )
                match = result.scalar_one_or_none()
                if not match:
                    return

                for user_tg_id, partner_tg_id in [
                    (match.user_one_id, match.user_two_id),
                    (match.user_two_id, match.user_one_id),
                ]:
                    # FIX PHASE4-HIGH-04: dedup — only send if not already
                    # reminded in the last 24h for this match.
                    dedup_key = f"cron:silence_reminded:{match_id}:{user_tg_id}"
                    try:
                        already_reminded = await self.redis.set(
                            dedup_key, "1", nx=True, ex=86400  # 24h
                        )
                        if not already_reminded:
                            # Key already existed — we already reminded this
                            # user for this match today. Skip.
                            continue
                    except Exception as dedup_exc:
                        # If Redis is down, fall back to sending (better to
                        # risk a duplicate than to drop the reminder).
                        logger.warning("silence dedup check failed: %s", dedup_exc)

                    # Get partner's public_id
                    p_result = await session.execute(
                        select(User.public_id).where(User.tg_id == partner_tg_id)
                    )
                    partner_pid = p_result.scalar_one_or_none() or "نامشخص"
                    try:
                        await self.bot.send_message(
                            user_tg_id,
                            Messages.CHAT_SILENCE_REMINDER.format(user_tag=partner_pid),
                        )
                    except Exception as e:
                        logger.warning("Failed to send silence reminder to %s: %s", user_tg_id, e)
        except Exception as e:
            logger.error("_send_silence_for_match error: %s", e)

    # ─── VIP expiry reminder (every 6 hours, 3 days before expiry) ───────────
    async def _vip_expiry_loop(self):
        while self._running:
            try:
                await asyncio.sleep(21600)  # 6 hours
                await self._send_vip_expiry_reminders()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("vip_expiry_loop error: %s", e, exc_info=True)
                await asyncio.sleep(60)

    async def _send_vip_expiry_reminders(self):
        """Notify VIP users 3 days before their subscription expires.

        FIX PHASE4-HIGH-05: previously, this fired every 6 hours for the
        full 3-day window — up to ~12 messages per user. Now we use a Redis
        SET NX key per user with a 3-day TTL to ensure only ONE expiry
        reminder per subscription period.
        """
        from matching_bot_project.database.models.models import User
        now = datetime.now(timezone.utc)
        three_days = now + timedelta(days=3)

        async with self.session_factory() as session:
            result = await session.execute(
                select(User).where(
                    and_(
                        User.is_vip == True,
                        User.is_banned == False,
                        User.vip_expires_at != None,
                        User.vip_expires_at > now,
                        User.vip_expires_at < three_days,
                    )
                ).limit(100)
            )
            users = result.scalars().all()

            for user in users:
                try:
                    # FIX PHASE4-HIGH-05: dedup — only send if not already
                    # reminded for this subscription's expiry window.
                    # Key includes the expiry timestamp so a renewed
                    # subscription (new expiry) gets a fresh reminder.
                    expires_at = user.vip_expires_at
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    dedup_key = f"cron:vip_expiry_reminded:{user.tg_id}:{int(expires_at.timestamp())}"
                    try:
                        already_reminded = await self.redis.set(
                            dedup_key, "1", nx=True, ex=3 * 86400  # 3 days
                        )
                        if not already_reminded:
                            continue  # already reminded for this expiry
                    except Exception as dedup_exc:
                        logger.warning("vip expiry dedup check failed: %s", dedup_exc)

                    remaining = (expires_at - now).days
                    text = (
                        f"⏰ اشتراک VIP شما {remaining} روز دیگر منقضی می‌شود.\n\n"
                        f"برای تمدید اشتراک و استفاده بدون وقفه از امکانات ویژه، "
                        f"به «اکانت VIP» بروید."
                    )
                    await self.bot.send_message(user.tg_id, text)
                    await asyncio.sleep(0.05)
                except Exception as e:
                    logger.warning("Failed to send VIP expiry reminder to %s: %s", user.tg_id, e)
