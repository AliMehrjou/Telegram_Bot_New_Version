"""
services/profile_completion.py

v3 NEW: Step-by-step profile completion state machine.

Steps (in order):
  1. city    — set city (auto-skipped if set during onboarding)
  2. photo   — upload profile photo
  3. gps     — share live location
  4. tags    — pick 3 (or 10 for VIP) tags
  5. bio     — write a short bio
  6. voice   — upload a profile voice clip (optional — can skip)

When all steps are completed, the user is rewarded with
PROFILE_COMPLETION_REWARD coins (default 10) — only once.

The completion percentage is denormalized on User.profile_completion_pct
for fast queries (e.g., reminder cron can find incomplete profiles quickly).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.models.models import (
    User, ProfileCompletionLog, CoinTransaction,
)
from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)


REQUIRED_STEPS = ["city", "photo", "gps", "tags", "bio"]
OPTIONAL_STEPS = ["voice"]
ALL_STEPS = REQUIRED_STEPS + OPTIONAL_STEPS


class ProfileCompletionService:
    """Manages step-by-step profile completion."""

    def __init__(self, redis_client=None):
        self.redis = redis_client

    async def mark_step_done(
        self,
        session: AsyncSession,
        user_tg_id: int,
        step_code: str,
    ) -> None:
        """Record that a user has completed a step."""
        # Upsert (idempotent)
        result = await session.execute(
            select(ProfileCompletionLog).where(
                and_(
                    ProfileCompletionLog.user_tg_id == user_tg_id,
                    ProfileCompletionLog.step_code == step_code,
                )
            )
        )
        log = result.scalar_one_or_none()
        if log:
            log.completed_at = datetime.now(timezone.utc)
        else:
            log = ProfileCompletionLog(
                user_tg_id=user_tg_id,
                step_code=step_code,
            )
            session.add(log)
        await session.commit()

    async def is_step_done(
        self,
        session: AsyncSession,
        user_tg_id: int,
        step_code: str,
    ) -> bool:
        result = await session.execute(
            select(ProfileCompletionLog).where(
                and_(
                    ProfileCompletionLog.user_tg_id == user_tg_id,
                    ProfileCompletionLog.step_code == step_code,
                )
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_completion_state(
        self, session: AsyncSession, user_tg_id: int
    ) -> dict:
        """Return current completion state for a user."""
        result = await session.execute(
            select(ProfileCompletionLog.step_code).where(
                ProfileCompletionLog.user_tg_id == user_tg_id
            )
        )
        done_steps = {row[0] for row in result.all()}

        # Also re-derive from User fields (in case they were set elsewhere)
        result = await session.execute(
            select(User).where(User.tg_id == user_tg_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return {"done": set(), "pct": 0, "next_step": "city"}

        if user.city:
            done_steps.add("city")
        if user.profile_photo_file_id:
            done_steps.add("photo")
        if user.location_lat is not None and user.location_lng is not None:
            done_steps.add("gps")
        if user.tags:
            done_steps.add("tags")
        if user.bio:
            done_steps.add("bio")
        if user.profile_voice_file_id:
            done_steps.add("voice")

        # Compute percentage based on required steps only
        required_done = sum(1 for s in REQUIRED_STEPS if s in done_steps)
        pct = int((required_done / len(REQUIRED_STEPS)) * 100)

        next_step = None
        for step in ALL_STEPS:
            if step not in done_steps:
                next_step = step
                break

        return {
            "done": done_steps,
            "pct": pct,
            "next_step": next_step,
            "is_complete": required_done == len(REQUIRED_STEPS),
        }

    async def refresh_completion_pct(
        self, session: AsyncSession, user_tg_id: int
    ) -> int:
        """Update User.profile_completion_pct with current value."""
        state = await self.get_completion_state(session, user_tg_id)
        await session.execute(
            update(User)
            .where(User.tg_id == user_tg_id)
            .values(profile_completion_pct=state["pct"])
        )
        await session.commit()
        return state["pct"]

    async def try_award_completion_reward(
        self, session: AsyncSession, user_tg_id: int
    ) -> Optional[int]:
        """
        If user has completed all required steps AND not yet been rewarded,
        credit the PROFILE_COMPLETION_REWARD coins.
        Returns the reward amount if awarded, else None.
        """
        # Lock user row
        result = await session.execute(
            select(User).where(User.tg_id == user_tg_id).with_for_update()
        )
        user = result.scalar_one_or_none()
        if not user:
            return None
        if user.profile_completion_rewarded:
            return None

        state = await self.get_completion_state(session, user_tg_id)
        if not state["is_complete"]:
            return None

        reward = settings.PROFILE_COMPLETION_REWARD
        user.coin_balance += reward
        user.total_earned_coins += reward
        user.profile_completion_rewarded = True
        user.profile_completion_pct = 100

        ct = CoinTransaction(
            user_id=user_tg_id,
            amount=reward,
            description="پاداش تکمیل پروفایل",
            tx_type="profile_completion",
        )
        session.add(ct)

        await session.commit()
        logger.info(
            "Profile completion reward: user %s earned %d coins",
            user_tg_id, reward,
        )
        return reward

    async def get_users_with_incomplete_profile(
        self, session: AsyncSession, limit: int = 100
    ) -> list:
        """Find users who haven't completed their profile (for cron reminder)."""
        result = await session.execute(
            select(User.tg_id, User.first_name, User.last_profile_reminder_at)
            .where(
                and_(
                    User.completed_registration == True,
                    User.profile_completion_rewarded == False,
                    User.is_banned == False,
                )
            )
            .order_by(User.last_active.desc())
            .limit(limit)
        )
        return result.all()
