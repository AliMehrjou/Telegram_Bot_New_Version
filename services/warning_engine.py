"""
services/warning_engine.py

v3 NEW: 3-strike warning system.

- When admin approves a report: the reported user gets +1 warning.
  If their warning_count reaches MAX_WARNINGS_BEFORE_BAN (default 3),
  they are permanently banned.
- When admin rejects a report (false report): the reporter gets +1 warning.
  Same 3-strike rule applies.
- Each warning is recorded in user_warnings table.
- Reporters of valid reports get a coin reward (default 5 coins).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.models.models import (
    User, UserWarning, UserReport, CoinTransaction,
)
from matching_bot_project.bot.core.config import settings
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard



logger = logging.getLogger(__name__)


class WarningEngine:
    """Manages the 3-strike warning system."""

    def __init__(self, redis_client=None):
        self.redis = redis_client

    async def issue_warning(
        self,
        session: AsyncSession,
        user_tg_id: int,
        reason: str,
        issued_by: str,
        report_id: Optional[int] = None,
        admin_tg_id: Optional[int] = None,
    ) -> dict:
        from matching_bot_project.bot.core.loader import dp, bot, redis_client
        from matching_bot_project.database.queries.crud import get_active_match
        result = await session.execute(
            select(User).where(User.tg_id == user_tg_id).with_for_update()
        )
        user = result.scalar_one_or_none()
        if not user:
            return {"error": "User not found"}

        warning = UserWarning(
            user_tg_id=user_tg_id,
            reason=reason,
            issued_by=issued_by,
            report_id=report_id,
            issued_by_admin_tg_id=admin_tg_id,
        )
        session.add(warning)

        new_count = user.warning_count + 1
        user.warning_count = new_count

        is_banned = False
        if new_count >= settings.MAX_WARNINGS_BEFORE_BAN:
            user.is_banned = True
            is_banned = True

        await session.flush()

        logger.info(
            "Warning issued to user %s (count=%d, banned=%s, reason=%s)",
            user_tg_id, new_count, is_banned, reason,
        )

        # --- پاکسازی دیت در صورت بن شدن ---
        if is_banned:
            try:
                active_match = await get_active_match(session, user_tg_id)
                if active_match:
                    active_match.is_active = False
                    active_match.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    
                    partner_id = active_match.user_two_id if active_match.user_one_id == user_tg_id else active_match.user_one_id
                    
                    bad_ctx = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=user_tg_id, user_id=user_tg_id))
                    await bad_ctx.set_state(None)
                    await bad_ctx.clear()
                    await redis_client.delete(f"user:state:{user_tg_id}")
                    
                    partner_ctx = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=partner_id, user_id=partner_id))
                    await partner_ctx.set_state(None)
                    await partner_ctx.clear()
                    await redis_client.delete(f"user:state:{partner_id}")
                    
                    await bot.send_message(
                        chat_id=partner_id,
                        text="⚠️ <b>دیت متوقف شد!</b>\nحساب کاربر مقابل توسط مدیریت ربات مسدود گردید.",
                        parse_mode="HTML",
                        reply_markup=get_main_menu_keyboard()
                    )
            except Exception as e:
                logger.error("Failed to terminate active match for banned user %s: %s", user_tg_id, e)
        # -----------------------------------

        return {
            "warning_count": new_count,
            "is_banned": is_banned,
            "max_warnings": settings.MAX_WARNINGS_BEFORE_BAN,
        }
    
    async def approve_report(
        self,
        session: AsyncSession,
        report_id: int,
        admin_tg_id: int,
    ) -> dict:
        """
        Admin approves a report. The reported user gets a warning.
        The reporter gets a coin reward.

        FIX PHASE4-HIGH-09: previously, `issue_warning` (called below) did its
        own `session.commit()` BEFORE the reporter reward was applied. If the
        reward step failed (e.g. DB error), the warning was already committed
        but the reporter got nothing — partial state.
        Now `issue_warning` no longer commits (see its updated docstring);
        the entire approve_report operation commits atomically at the end.
        """
        # Lock report row
        result = await session.execute(
            select(UserReport).where(UserReport.id == report_id).with_for_update()
        )
        report = result.scalar_one_or_none()
        if not report:
            return {"error": "Report not found"}
        if report.status != "pending":
            return {"error": f"Report already {report.status}"}

        # Mark report approved
        report.status = "approved"
        report.resolved_at = datetime.now(timezone.utc)
        report.admin_note = "Approved by admin"

        # Issue warning to reported user (no inner commit now)
        warning_result = await self.issue_warning(
            session=session,
            user_tg_id=report.reported_id,
            reason=f"گزارش تأیید شده: {report.reason}",
            issued_by="admin_action",
            report_id=report_id,
            admin_tg_id=admin_tg_id,
        )

        # Reward reporter
        reward = settings.REPORT_REWARD_COINS
        result = await session.execute(
            select(User).where(User.tg_id == report.reporter_id).with_for_update()
        )
        reporter = result.scalar_one_or_none()
        if reporter:
            reporter.coin_balance += reward
            reporter.total_earned_coins += reward
            ct = CoinTransaction(
                user_id=reporter.tg_id,
                amount=reward,
                description=f"پاداش گزارش تأیید شده (report #{report_id})",
                reference_id=report_id,
                tx_type="report_reward",
            )
            session.add(ct)

        # FIX PHASE4-HIGH-09: single atomic commit for the whole operation.
        await session.commit()

        # FIX PHASE4-HIGH-03: invalidate reporter's profile cache so the new
        # coin balance is visible immediately.
        try:
            from matching_bot_project.services.cache import cache
            await cache.invalidate_user_profile(report.reporter_id)
            await cache.invalidate_user_profile(report.reported_id)
        except Exception:
            pass

        return {
            "warning_result": warning_result,
            "reward_coins": reward,
            "reporter_tg_id": report.reporter_id,
            "reported_tg_id": report.reported_id,
        }

    async def reject_report(
        self,
        session: AsyncSession,
        report_id: int,
        admin_tg_id: int,
    ) -> dict:
        """
        Admin rejects a report (false report). The reporter gets a warning.
        If their warning_count reaches the limit, they are banned.
        """
        result = await session.execute(
            select(UserReport).where(UserReport.id == report_id).with_for_update()
        )
        report = result.scalar_one_or_none()
        if not report:
            return {"error": "Report not found"}
        if report.status != "pending":
            return {"error": f"Report already {report.status}"}

        report.status = "rejected"
        report.resolved_at = datetime.now(timezone.utc)
        report.admin_note = "False report — rejected by admin"

        # Issue warning to the reporter (false report)
        warning_result = await self.issue_warning(
            session=session,
            user_tg_id=report.reporter_id,
            reason="گزارش اشتباه/کاذب",
            issued_by="false_report",
            report_id=report_id,
            admin_tg_id=admin_tg_id,
        )

        await session.commit()
        return {
            "warning_result": warning_result,
            "reporter_tg_id": report.reporter_id,
        }

    async def get_user_warnings(
        self, session: AsyncSession, user_tg_id: int, limit: int = 10
    ) -> list:
        """Return list of warnings for a user."""
        result = await session.execute(
            select(UserWarning)
            .where(UserWarning.user_tg_id == user_tg_id)
            .order_by(UserWarning.issued_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_pending_reports(self, session: AsyncSession, limit: int = 20) -> list:
        """Return list of pending reports for admin review."""
        result = await session.execute(
            select(UserReport)
            .where(UserReport.status == "pending")
            .order_by(UserReport.created_at.asc())
            .limit(limit)
        )
        return result.scalars().all()
