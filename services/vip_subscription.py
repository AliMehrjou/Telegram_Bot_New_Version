"""
services/vip_subscription.py

v3 NEW: VIP subscription manager.

Handles:
- Activating a new subscription (creates VIPSubscription record, updates User.is_vip,
  User.vip_expires_at, User.vip_quota).
- Expiring subscriptions (background worker calls this when expires_at < now).
- Checking if a user is currently VIP.
- Returning remaining days.

Subscription plans are stored in json_files/vip_plans.json (admin-editable).
"""

import logging
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.models.models import (
    User, VIPSubscription, CoinPurchaseOrder,
)
from matching_bot_project.bot.core.constants import VIPPlan

logger = logging.getLogger(__name__)


# Cache VIP plans loaded from JSON file
_VIP_PLANS_CACHE: Optional[dict] = None
_VIP_PLANS_LOADED_AT: Optional[datetime] = None


def load_vip_plans() -> dict:
    """Load VIP plans from json_files/vip_plans.json (cached for 5 min)."""
    global _VIP_PLANS_CACHE, _VIP_PLANS_LOADED_AT
    now = datetime.now(timezone.utc)
    if _VIP_PLANS_CACHE and _VIP_PLANS_LOADED_AT and (now - _VIP_PLANS_LOADED_AT).total_seconds() < 300:
        return _VIP_PLANS_CACHE

    json_path = Path("json_files/vip_plans.json")
    if not json_path.exists():
        json_path = Path("/app/json_files/vip_plans.json")
    if not json_path.exists():
        # Fallback to defaults from constants
        _VIP_PLANS_CACHE = {
            VIPPlan.WEEK_1: {
                "code": VIPPlan.WEEK_1,
                "label": VIPPlan.LABELS[VIPPlan.WEEK_1],
                "duration_days": VIPPlan.DURATION_DAYS[VIPPlan.WEEK_1],
                "price_toman": VIPPlan.DEFAULT_PRICES_TOMAN[VIPPlan.WEEK_1],
            },
            VIPPlan.WEEK_2: {
                "code": VIPPlan.WEEK_2,
                "label": VIPPlan.LABELS[VIPPlan.WEEK_2],
                "duration_days": VIPPlan.DURATION_DAYS[VIPPlan.WEEK_2],
                "price_toman": VIPPlan.DEFAULT_PRICES_TOMAN[VIPPlan.WEEK_2],
            },
            VIPPlan.MONTH_1: {
                "code": VIPPlan.MONTH_1,
                "label": VIPPlan.LABELS[VIPPlan.MONTH_1],
                "duration_days": VIPPlan.DURATION_DAYS[VIPPlan.MONTH_1],
                "price_toman": VIPPlan.DEFAULT_PRICES_TOMAN[VIPPlan.MONTH_1],
            },
        }
    else:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                _VIP_PLANS_CACHE = json.load(f)
        except Exception as e:
            logger.error("Error loading vip_plans.json: %s", e)
            _VIP_PLANS_CACHE = {}
    _VIP_PLANS_LOADED_AT = now
    return _VIP_PLANS_CACHE


class VIPSubscriptionManager:
    """Manages VIP subscription lifecycle."""

    def __init__(self, redis_client=None):
        self.redis = redis_client

# در فایل vip_subscription.py

    async def activate_subscription(
        self,
        session: AsyncSession,
        user_tg_id: int,
        plan_code: str,
        payment_order_id: Optional[int] = None,
    ) -> VIPSubscription:
        """Activate a new VIP subscription for a user."""
        plans = load_vip_plans()
        if plan_code not in plans:
            raise ValueError(f"Unknown VIP plan code: {plan_code}")

        duration_days = plans[plan_code]["duration_days"]
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=duration_days)

        # Lock user row for atomic activation.
        result = await session.execute(
            select(User).where(User.tg_id == user_tg_id).with_for_update()
        )
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError(f"User {user_tg_id} not found")

        if user.is_vip and user.vip_expires_at and user.vip_expires_at > now:
            # Extend from current expiry
            base = user.vip_expires_at
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
            expires_at = base + timedelta(days=duration_days)

        # 🔄 فاز ۴: غیرفعال کردن تمامی سابسکریپشن‌های فعال قبلی کاربر برای جلوگیری از انباشت دیتا
        from sqlalchemy import update, and_
        await session.execute(
            update(VIPSubscription)
            .where(
                and_(
                    VIPSubscription.user_tg_id == user_tg_id, 
                    VIPSubscription.is_active == True
                )
            )
            .values(is_active=False)
        )

        # Create new subscription record
        sub = VIPSubscription(
            user_tg_id=user_tg_id,
            plan_code=plan_code,
            started_at=now,
            expires_at=expires_at,
            payment_order_id=payment_order_id,
            is_active=True,
        )
        session.add(sub)

        # Update user record
        user.is_vip = True
        user.vip_expires_at = expires_at
        # v3: VIP quota is daily 5 free matches (refreshed by scheduler)
        user.vip_quota = 5

        await session.commit()
        await session.refresh(sub)

        # Invalidate cache so new VIP status is visible immediately.
        try:
            from matching_bot_project.services.cache import cache
            await cache.invalidate_user_profile(user_tg_id)
            await cache.invalidate_user_vip_status(user_tg_id)
        except Exception as cache_exc:
            logger.warning("Failed to invalidate VIP cache after activation: %s", cache_exc)

        logger.info(
            "VIP activated for user %s, plan %s, expires_at %s",
            user_tg_id, plan_code, expires_at,
        )
        return sub
    
    
    async def is_vip_active(self, session: AsyncSession, user_tg_id: int) -> bool:
        """Check if a user currently has an active VIP subscription."""
        result = await session.execute(
            select(User.is_vip, User.vip_expires_at).where(User.tg_id == user_tg_id)
        )
        row = result.first()
        if not row:
            return False
        is_vip, expires_at = row
        if not is_vip:
            return False
        if expires_at is None:
            return False
        # Normalize to aware UTC for the comparison (DB may return naive datetime)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)

    async def get_remaining_days(self, session: AsyncSession, user_tg_id: int) -> int:
        """Return remaining days of VIP subscription (0 if expired/inactive)."""
        result = await session.execute(
            select(User.vip_expires_at).where(User.tg_id == user_tg_id)
        )
        expires_at = result.scalar_one_or_none()
        if not expires_at:
            return 0
        now = datetime.now(timezone.utc)
        # Normalize to aware UTC (DB may return naive datetime)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        delta = expires_at - now
        return max(0, delta.days)

    async def expire_due_subscriptions(self, session: AsyncSession) -> int:
        """
        Background worker: find all subscriptions past their expiry and deactivate.
        Returns count of expired subscriptions.

        FIX PHASE4-HIGH-03: previously, this method did NOT invalidate the
        cache:user:{tg_id} and cache:vip_status:{tg_id} keys. Users kept
        seeing VIP features (invisible mode, age filter, rematch) for up to
        5 minutes (cache TTL) after their subscription actually expired.
        Now we invalidate both cache keys for each expired user.
        """
        now = datetime.now(timezone.utc)

        # Find expired but still-active subscriptions.
        # The column is timezone-aware in MySQL but SQLite stores naive datetimes.
        # We pass a naive UTC datetime to be SQLite-compatible; MySQL accepts it too.
        now_naive = now.replace(tzinfo=None)
        result = await session.execute(
            select(VIPSubscription).where(
                and_(
                    VIPSubscription.is_active == True,
                    VIPSubscription.expires_at < now_naive,
                )
            )
        )
        expired_subs = result.scalars().all()

        # FIX PHASE4-HIGH-03: collect user IDs whose cache we need to invalidate.
        expired_user_ids: set[int] = set()

        count = 0
        for sub in expired_subs:
            sub.is_active = False
            # Deactivate user's VIP status (only if no OTHER active subscription exists)
            other_active = await session.execute(
                select(VIPSubscription).where(
                    and_(
                        VIPSubscription.user_tg_id == sub.user_tg_id,
                        VIPSubscription.is_active == True,
                        VIPSubscription.id != sub.id,
                    )
                )
            )
            if not other_active.scalars().first():
                await session.execute(
                    update(User)
                    .where(User.tg_id == sub.user_tg_id)
                    .values(is_vip=False, vip_quota=0)
                )
                expired_user_ids.add(sub.user_tg_id)
            count += 1

        if count:
            await session.commit()
            logger.info("Expired %d VIP subscriptions", count)

            # FIX PHASE4-HIGH-03: invalidate cache for each expired user so
            # they don't keep seeing VIP features for up to 5 min (cache TTL).
            if self.redis and expired_user_ids:
                try:
                    from matching_bot_project.services.cache import cache
                    for uid in expired_user_ids:
                        await cache.invalidate_user_profile(uid)
                        await cache.invalidate_user_vip_status(uid)
                    logger.info("Invalidated VIP cache for %d users", len(expired_user_ids))
                except Exception as cache_exc:
                    logger.warning("Failed to invalidate VIP cache: %s", cache_exc)

        return count

    async def get_subscription_history(
        self, session: AsyncSession, user_tg_id: int, limit: int = 10
    ) -> list:
        """Return subscription history for a user."""
        result = await session.execute(
            select(VIPSubscription)
            .where(VIPSubscription.user_tg_id == user_tg_id)
            .order_by(VIPSubscription.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
