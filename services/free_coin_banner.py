"""
services/free_coin_banner.py

v3 NEW: Banner-based free coin reward system.

How it works:
1. Admin creates a banner campaign (photo + caption + reward amount).
2. User taps "سکه رایگان" → sees the active banner.
3. User forwards the banner to friends/channels.
4. User sends back the forwarded message as proof.
5. Admin reviews the forward → approves → user is credited reward_coins.

Tables:
- banner_campaigns — admin-defined campaigns
- banner_forwards  — per-user forward records (one reward per user per campaign)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.models.models import (
    User, BannerCampaign, BannerForward, CoinTransaction,
)

logger = logging.getLogger(__name__)


class FreeCoinBannerService:
    """Manages banner campaigns and reward payouts."""

    def __init__(self, redis_client=None):
        self.redis = redis_client

    async def get_active_campaign(self, session: AsyncSession) -> Optional[BannerCampaign]:
        """Return the most recent active banner campaign."""
        result = await session.execute(
            select(BannerCampaign)
            .where(BannerCampaign.is_active == True)
            .order_by(BannerCampaign.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_campaign(self, session: AsyncSession, campaign_id: int) -> Optional[BannerCampaign]:
        result = await session.execute(
            select(BannerCampaign).where(BannerCampaign.id == campaign_id)
        )
        return result.scalar_one_or_none()

    async def has_user_forwarded(
        self, session: AsyncSession, user_tg_id: int, campaign_id: int
    ) -> bool:
        """بررسی می‌کند که آیا کاربر رکوردی در انتظار تأیید یا تأیید شده دارد یا خیر."""
        result = await session.execute(
            select(BannerForward).where(
                and_(
                    BannerForward.user_tg_id == user_tg_id,
                    BannerForward.campaign_id == campaign_id,
                )
            )
        )
        record = result.scalar_one_or_none()
        
        # 💡 فیکس لاجیک: اگر رکورد قبلاً رد (reject) شده باشد، به کاربر اجازه تلاش مجدد می‌دهد
        if record and record.status == "rejected":
            return False
            
        return record is not None

    async def record_forward(
        self,
        session: AsyncSession,
        user_tg_id: int,
        campaign_id: int,
        forward_msg_id: Optional[int] = None,
        forward_chat_id: Optional[int] = None,
    ) -> tuple[bool, str, Optional[int]]:
        """ثبت فوروارد بنر. اگر قبلاً رد شده باشد، آن را دوباره به pending برمی‌گرداند."""
        from sqlalchemy.dialects.mysql import insert as mysql_insert

        campaign = await self.get_campaign(session, campaign_id)
        if not campaign or not campaign.is_active:
            return False, "این کمپین دیگر فعال نیست.", None

        stmt = mysql_insert(BannerForward).values(
            user_tg_id=user_tg_id,
            campaign_id=campaign_id,
            forward_msg_id=forward_msg_id,
            forward_chat_id=forward_chat_id,
            status="pending",
        ).on_duplicate_key_update(
            forward_msg_id=forward_msg_id,
            forward_chat_id=forward_chat_id,
            status="pending", # 💡 فیکس بحرانی: وضعیت را مجدداً به pending تغییر می‌دهد تا ادمین بتواند دوباره بررسی کند
        )
        
        result = await session.execute(stmt)
        await session.flush() # همگام‌سازی با دیتابیس برای دریافت آیدی

        # استخراج آیدی رکوردی که ذخیره یا آپدیت شده
        fw_stmt = select(BannerForward.id).where(
            and_(BannerForward.user_tg_id == user_tg_id, BannerForward.campaign_id == campaign_id)
        )
        fw_id = (await session.execute(fw_stmt)).scalar_one()

        if result.rowcount == 2:
            await session.commit()
            return True, "مدرک جدید شما ثبت شد و مجدداً در انتظار تأیید ادمین قرار گرفت.", fw_id

        await session.commit()
        return True, "بنر شما برای بررسی ادمین ارسال شد. پس از تأیید، سکه به شما تعلق می‌گیرد.", fw_id
    

    async def approve_forward(
        self,
        session: AsyncSession,
        forward_id: int,
        admin_tg_id: int,
    ) -> dict:
        """Admin approves a forward — credit user with reward_coins."""
        result = await session.execute(
            select(BannerForward).where(BannerForward.id == forward_id).with_for_update()
        )
        forward = result.scalar_one_or_none()
        if not forward:
            return {"error": "Forward record not found"}
        if forward.status != "pending":
            return {"error": f"Forward already {forward.status}"}

        campaign = await self.get_campaign(session, forward.campaign_id)
        if not campaign:
            return {"error": "Campaign not found"}

        reward = campaign.reward_coins

        # Lock user row
        result = await session.execute(
            select(User).where(User.tg_id == forward.user_tg_id).with_for_update()
        )
        user = result.scalar_one_or_none()
        if not user:
            return {"error": "User not found"}

        user.coin_balance += reward
        user.total_earned_coins += reward

        forward.status = "approved"
        forward.awarded_coins = reward
        forward.resolved_at = datetime.now(timezone.utc)
        forward.admin_note = f"Approved by admin {admin_tg_id}"

        ct = CoinTransaction(
            user_id=user.tg_id,
            amount=reward,
            description=f"پاداش بنر کمپین #{campaign.id}",
            reference_id=forward.id,
            tx_type="free_banner",
        )
        session.add(ct)

        await session.commit()

        # FIX PHASE4-HIGH-03: invalidate user's profile cache so the new coin
        # balance is visible immediately.
        try:
            from matching_bot_project.services.cache import cache
            await cache.invalidate_user_profile(user.tg_id)
        except Exception:
            pass

        return {
            "reward_coins": reward,
            "user_tg_id": user.tg_id,
        }

    async def reject_forward(
        self,
        session: AsyncSession,
        forward_id: int,
        admin_tg_id: int,
        note: str = "",
    ) -> dict:
        result = await session.execute(
            select(BannerForward).where(BannerForward.id == forward_id).with_for_update()
        )
        forward = result.scalar_one_or_none()
        if not forward:
            return {"error": "Forward record not found"}
        if forward.status != "pending":
            return {"error": f"Forward already {forward.status}"}

        forward.status = "rejected"
        forward.resolved_at = datetime.now(timezone.utc)
        forward.admin_note = f"Rejected by admin {admin_tg_id}: {note}"

        await session.commit()
        return {"ok": True}

    async def get_pending_forwards(self, session: AsyncSession, limit: int = 20) -> list:
        result = await session.execute(
            select(BannerForward, BannerCampaign)
            .join(BannerCampaign, BannerForward.campaign_id == BannerCampaign.id)
            .where(BannerForward.status == "pending")
            .order_by(BannerForward.forwarded_at.asc())
            .limit(limit)
        )
        return result.all()

    async def create_campaign(
        self,
        session: AsyncSession,
        banner_photo_file_id: str,
        caption_text: str,
        reward_coins: int = 2,
    ) -> BannerCampaign:
        """Admin creates a new banner campaign."""
        campaign = BannerCampaign(
            banner_photo_file_id=banner_photo_file_id,
            caption_text=caption_text,
            reward_coins=reward_coins,
            is_active=True,
        )
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)
        return campaign
