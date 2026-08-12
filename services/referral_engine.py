"""
services/referral_engine.py

v3 NEW: Referral commission engine.

When a user signs up via another user's referral link, the referrer's tg_id
is stored on the new user's row (User.referrer_id).

When the referred user purchases coins, the referrer earns a commission
(default 20%) of the purchased coins. This is recorded in referral_commissions
table and credited to the referrer's coin_balance + referral_earnings.

This module also generates unique referral codes for each user (8-char alnum)
and builds the bot's referral link.
"""

import logging
import secrets
import string
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.models.models import (
    User, ReferralCommission, CoinPurchaseOrder, CoinTransaction,
)
from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)


def generate_referral_code(length: int = 8) -> str:
    """Generate a unique 8-character alphanumeric referral code."""
    alphabet = string.ascii_uppercase + string.digits
    # Exclude ambiguous chars (0/O, 1/I) for clarity
    alphabet = alphabet.replace("O", "").replace("0", "").replace("I", "").replace("1", "")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def build_referral_link(referral_code: str, bot_username: str) -> str:
    """Build the bot's referral URL."""
    return f"https://t.me/{bot_username}?start=ref_{referral_code}"


class ReferralEngine:
    """Manages referral codes, attribution, and commission payouts."""

    def __init__(self, redis_client=None):
        self.redis = redis_client

    async def ensure_referral_code(
        self, session: AsyncSession, user_tg_id: int
    ) -> str:
        """Assign a referral code to user if they don't have one. Return the code."""
        result = await session.execute(
            select(User.referral_code).where(User.tg_id == user_tg_id)
        )
        code = result.scalar_one_or_none()
        if code:
            return code

        # Generate unique code (check for collisions)
        for _ in range(5):
            new_code = generate_referral_code()
            existing = await session.execute(
                select(User).where(User.referral_code == new_code)
            )
            if not existing.scalar_one_or_none():
                await session.execute(
                    update(User)
                    .where(User.tg_id == user_tg_id)
                    .values(referral_code=new_code)
                )
                await session.commit()
                return new_code

        # Fallback — extremely unlikely
        new_code = f"R{user_tg_id}"
        await session.execute(
            update(User)
            .where(User.tg_id == user_tg_id)
            .values(referral_code=new_code)
        )
        await session.commit()
        return new_code

    async def attribute_referral(
        self,
        session: AsyncSession,
        new_user_tg_id: int,
        referrer_code: str,
    ) -> bool:
        """
        Called during user onboarding. If `referrer_code` matches an existing
        user's referral_code, set the new user's referrer_id.
        Returns True if attribution succeeded.
        """
        if not referrer_code:
            return False

        # Strip 'ref_' prefix if present (from deep link)
        if referrer_code.startswith("ref_"):
            referrer_code = referrer_code[4:]

        result = await session.execute(
            select(User).where(User.referral_code == referrer_code)
        )
        referrer = result.scalar_one_or_none()
        if not referrer:
            return False
        if referrer.tg_id == new_user_tg_id:
            return False  # Can't refer yourself

        await session.execute(
            update(User)
            .where(User.tg_id == new_user_tg_id)
            .values(referrer_id=referrer.tg_id)
        )
        await session.commit()
        logger.info(
            "Referral attributed: new user %s → referrer %s",
            new_user_tg_id, referrer.tg_id,
        )
        return True

    async def process_commission_on_purchase(
        self,
        session: AsyncSession,
        purchase_order_id: int,
        buyer_tg_id: int,
        coins_purchased: int,
    ) -> Optional[ReferralCommission]:
        """
        Called after a coin purchase is verified. If the buyer has a referrer,
        credit the referrer with commission_pct% of the purchased coins.
        """
        if coins_purchased <= 0:
            return None

        # Lock buyer row to read referrer_id
        result = await session.execute(
            select(User).where(User.tg_id == buyer_tg_id).with_for_update()
        )
        buyer = result.scalar_one_or_none()
        if not buyer or not buyer.referrer_id:
            return None

        referrer_tg_id = buyer.referrer_id
        commission_pct = settings.REFERRAL_COMMISSION_PCT
        commission_coins = (coins_purchased * commission_pct) // 100
        if commission_coins <= 0:
            return None

        # Lock referrer row
        result = await session.execute(
            select(User).where(User.tg_id == referrer_tg_id).with_for_update()
        )
        referrer = result.scalar_one_or_none()
        if not referrer:
            return None

        # Credit referrer
        referrer.coin_balance += commission_coins
        referrer.total_earned_coins += commission_coins
        referrer.referral_earnings += commission_coins

        # Record commission
        commission = ReferralCommission(
            referrer_tg_id=referrer_tg_id,
            referred_tg_id=buyer_tg_id,
            purchase_order_id=purchase_order_id,
            commission_pct=commission_pct,
            commission_coins=commission_coins,
        )
        session.add(commission)

        # Record coin transaction for referrer
        ct = CoinTransaction(
            user_id=referrer_tg_id,
            amount=commission_coins,
            description=f"پورسانت زیرمجموعه‌گیری ({commission_pct}٪ از خرید)",
            reference_id=purchase_order_id,
            tx_type="referral_commission",
        )
        session.add(ct)

        # FIX PHASE4-HIGH-08: previously, this method called `await session.commit()`
        # here — but it's invoked from the payment callback (api/routes/payment.py)
        # which ALREADY commits the order at the end. If the order commit failed
        # AFTER this inner commit, the referrer would be credited but the order
        # would be rolled back → inconsistency (coins credited for a payment that
        # didn't complete).
        #
        # Now we DO NOT commit here. The caller (payment callback) is responsible
        # for committing the entire transaction atomically. If the caller rolls
        # back, the referrer credit is also rolled back.
        #
        # We flush so `commission.id` is available for any subsequent reference.
        await session.flush()

        logger.info(
            "Referral commission: referrer %s earned %d coins (buyer %s purchased %d)",
            referrer_tg_id, commission_coins, buyer_tg_id, coins_purchased,
        )
        return commission

    async def get_referral_stats(
        self, session: AsyncSession, referrer_tg_id: int
    ) -> dict:
        """Return referral dashboard stats for a user."""
        # Count of referred users
        result = await session.execute(
            select(func.count()).where(User.referrer_id == referrer_tg_id)
        )
        total_referred = result.scalar() or 0

        # Total commission earned
        result = await session.execute(
            select(func.sum(ReferralCommission.commission_coins))
            .where(ReferralCommission.referrer_tg_id == referrer_tg_id)
        )
        total_commission = result.scalar() or 0

        # Recent commissions
        result = await session.execute(
            select(ReferralCommission)
            .where(ReferralCommission.referrer_tg_id == referrer_tg_id)
            .order_by(ReferralCommission.created_at.desc())
            .limit(10)
        )
        recent = result.scalars().all()

        return {
            "total_referred": total_referred,
            "total_commission": total_commission,
            "recent_commissions": recent,
        }
