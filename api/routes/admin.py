from fastapi import APIRouter, Depends, HTTPException, Query, Header, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Dict, Optional
from datetime import datetime, timezone
import logging
import secrets
from pydantic import BaseModel, Field
from matching_bot_project.services.broadcast_worker import mark_user_blocked
from matching_bot_project.database.session import get_db_session
from matching_bot_project.database.models.models import User, MatchHistory
from matching_bot_project.database.queries.crud import get_user_by_tg_id, process_coin_transaction
from matching_bot_project.bot.core.loader import bot
from matching_bot_project.services.broadcast_worker import BroadcastWorker
from matching_bot_project.bot.core.config import settings
from matching_bot_project.database.session import get_read_db_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin Control Panel"])

# FIX PHASE2-SEC-19: upper bound on coin minting. Without this, a leaked admin
# API key could mint 10^18 coins and crash the economy. 1,000,000 is well above
# any legitimate single-transaction grant but small enough to limit blast radius.
_MAX_COIN_MINT_PER_CALL = 1_000_000

# FIX CRIT-11: Refuse to operate if ADMIN_SECRET_TOKEN is empty. Otherwise an attacker
# can send an empty X-Api-Key header and `secrets.compare_digest("", "")` returns True,
# granting full admin access.
def verify_api_key(x_api_key: str = Header(...)):
    if not settings.ADMIN_SECRET_TOKEN:
        logger.error("ADMIN_SECRET_TOKEN is empty — admin API disabled")
        raise HTTPException(status_code=503, detail="Admin API is not configured")
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.ADMIN_SECRET_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key


# FIX PHASE2-SEC-17: previously admin POST endpoints took all params as Query
# strings, which leak into nginx access logs, browser history, and the Referer
# header. Now they use Pydantic request-body models — params are in the JSON
# body, not the URL. GET endpoints still use Query (idiomatic for reads).

class BroadcastRequest(BaseModel):
    text: str = Field(..., min_length=5, max_length=4096, description="Broadcast message text")


class AddCoinsRequest(BaseModel):
    tg_id: int = Field(..., gt=0, description="Target user Telegram ID")
    amount: int = Field(..., gt=0, le=_MAX_COIN_MINT_PER_CALL, description="Coins to add (1–1,000,000)")
    reason: Optional[str] = Field(default="API Admin added coins", max_length=200)


class BanUserRequest(BaseModel):
    tg_id: int = Field(..., gt=0)
    ban: bool = Field(default=True)


class ReportActionRequest(BaseModel):
    admin_tg_id: int = Field(..., gt=0, description="Admin's Telegram ID (for audit log)")


class BannerActionRequest(BaseModel):
    admin_tg_id: int = Field(..., gt=0)
    note: Optional[str] = Field(default="", max_length=500)


class ChannelAddRequest(BaseModel):
    channel_id: int = Field(..., lt=0, description="Telegram channel ID (negative, e.g. -1001234567890)")
    channel_username: Optional[str] = Field(default=None, max_length=64)
    invite_link: Optional[str] = Field(default=None, max_length=200)


class ChannelRemoveRequest(BaseModel):
    channel_id: int = Field(..., lt=0)


@router.get("/stats", dependencies=[Depends(verify_api_key)])
async def get_bot_statistics(db_read_session: AsyncSession = Depends(get_read_db_session)) -> Dict:
    """Provides high-level analytical performance logs for the registration metrics."""
    try:
        total_users = await db_read_session.scalar(select(func.count(User.id)))
        vip_users = await db.scalar(select(func.count(User.id)).where(User.is_vip == True))
        registered_completed = await db.scalar(select(func.count(User.id)).where(User.completed_registration == True))
        total_coins = await db.scalar(select(func.sum(User.coin_balance)))
        active_dates = await db.scalar(select(func.count(MatchHistory.id)).where(MatchHistory.is_active == True))
        completed_dates = await db.scalar(select(func.count(MatchHistory.id)).where(MatchHistory.questionnaire_completed == True))

        return {
            "total_users": total_users,
            "vip_users": vip_users,
            "completed_onboarding": registered_completed,
            "running_matches": active_dates,
            "total_economy_coins": total_coins or 0,
            "gamified_completed_matches": completed_dates
        }
    except Exception as e:
        logger.error(f"Error fetching administrative dashboard metrics: {str(e)}")
        raise HTTPException(status_code=500, detail="Database stats fetch error")


@router.post("/broadcast", dependencies=[Depends(verify_api_key)])
async def trigger_admin_broadcast(
    payload: BroadcastRequest,
    db: AsyncSession = Depends(get_db_session)
) -> Dict:
    """Dispatches a global notification message without blocking main thread flow."""
    # FIX PHASE2-SEC-17: was `text: str = Query(...)` — leaked broadcast text into
    # nginx access logs. Now takes a JSON body.
    text = payload.text
    try:
        # FIX HIGH-26: stream IDs instead of `result.all()` to avoid OOM on large user bases.
        result = await db.stream(select(User.tg_id).where(User.is_banned == False))
        user_ids: list[int] = []
        async for row in result:
            user_ids.append(row[0])

        if not user_ids:
            return {"status": "skipped", "message": "No active users found in database."}

        worker = BroadcastWorker(bot=bot)
        # Dispatch asynchronously
        worker.start_background_broadcast(user_ids=user_ids, text=text, delay_ms=40, on_blocked=mark_user_blocked)

        return {
            "status": "enqueued",
            "active_users_notified": len(user_ids),
            "delay_ms_per_task": 40
        }
    except Exception as e:
        logger.error(f"Broadcast process trigger failure: {str(e)}")
        raise HTTPException(status_code=500, detail="Unable to initiate global broadcaster pool")

@router.post("/coins/add", dependencies=[Depends(verify_api_key)])
async def api_add_coins(
    payload: AddCoinsRequest,
    db: AsyncSession = Depends(get_db_session)
) -> Dict:
    # FIX PHASE2-SEC-17: was taking tg_id and amount as Query params (leaked
    # into logs). Now takes a JSON body.
    # FIX PHASE2-SEC-19: amount is now bounded by AddCoinsRequest.amount ≤ 1M
    # via Pydantic Field(le=...). The previous `if amount <= 0` check only
    # blocked non-positive amounts; an attacker with a leaked key could mint
    # 10^18 coins in a single call.
    tg_id = payload.tg_id
    amount = payload.amount
    reason = payload.reason

    user = await get_user_by_tg_id(db, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    success = await process_coin_transaction(db, user, amount, reason)
    if not success:
        raise HTTPException(status_code=400, detail="Insufficient balance for this deduction")

    await db.commit()

    # FIX PHASE2-SEC-19: log the mint for audit trail. Previously only the
    # CoinTransaction row recorded this; now we also emit a structured log
    # line that operators can grep for "COIN_MINT".
    logger.info("COIN_MINT admin_tg_id=? user_tg_id=%s amount=%s reason=%s", tg_id, amount, reason)

    return {"status": "success", "tg_id": tg_id, "new_balance": user.coin_balance, "amount_added": amount}


@router.post("/ban", dependencies=[Depends(verify_api_key)])
async def api_ban_user(
    payload: BanUserRequest,
    db: AsyncSession = Depends(get_db_session)
) -> Dict:
    # FIX PHASE2-SEC-17: was taking tg_id and ban as Query params. Now JSON body.
    tg_id = payload.tg_id
    ban = payload.ban

    user = await get_user_by_tg_id(db, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_banned = ban
    await db.commit()

    logger.info("USER_BAN tg_id=%s ban=%s", tg_id, ban)
    return {"status": "success", "tg_id": tg_id, "is_banned": user.is_banned}

@router.get("/user/{tg_id}", dependencies=[Depends(verify_api_key)])
async def api_get_user(
    tg_id: int,
    db: AsyncSession = Depends(get_db_session)
) -> Dict:
    user = await get_user_by_tg_id(db, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    matches = await db.scalar(select(func.count(MatchHistory.id)).where(
        (MatchHistory.user_one_id == tg_id) | (MatchHistory.user_two_id == tg_id)
    ))
    chats = await db.scalar(select(func.count(MatchHistory.id)).where(
        and_((MatchHistory.user_one_id == tg_id) | (MatchHistory.user_two_id == tg_id), MatchHistory.chat_approved == True)
    ))

    return {
        "tg_id": user.tg_id,
        "first_name": user.first_name,
        "gender": user.gender,
        "age": user.age,
        "city": user.city,
        "coin_balance": user.coin_balance,
        "is_vip": user.is_vip,
        "is_banned": user.is_banned,
        "matches": matches or 0,
        "chat_success": chats or 0,
        "is_online": user.is_online
    }

@router.get("/stats/advanced", dependencies=[Depends(verify_api_key)])
async def api_get_advanced_stats(db_read_session: AsyncSession = Depends(get_read_db_session)) -> Dict:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_reg = await db_read_session.scalar(select(func.count(User.id)).where(User.created_at >= today))

    result = await db.execute(
        select(User.province, func.count(User.id).label('count'))
        .where(User.province != None)
        .group_by(User.province)
        .order_by(func.count(User.id).desc())
        .limit(5)
    )
    top_provinces = {row.province: row.count for row in result.all()}

    total_matches = await db.scalar(select(func.count(MatchHistory.id)))
    successful_chats = await db.scalar(select(func.count(MatchHistory.id)).where(MatchHistory.chat_approved == True))
    conv_rate = (successful_chats / total_matches * 100) if total_matches else 0

    return {
        "today_registrations": today_reg or 0,
        "top_provinces": top_provinces,
        "chat_conversion_rate_percent": round(conv_rate, 2)
    }


# ═══════════════════════════════════════════════════════════════════════════
# v3 NEW: Admin endpoints for v3 features
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/warnings/pending", dependencies=[Depends(verify_api_key)])
async def list_pending_warnings(db: AsyncSession = Depends(get_db_session)) -> Dict:
    """List pending reports for admin review (3-strike system)."""
    from matching_bot_project.database.models.models import UserReport, User
    result = await db.execute(
        select(UserReport, User)
        .join(User, UserReport.reporter_id == User.tg_id)
        .where(UserReport.status == "pending")
        .order_by(UserReport.created_at.asc())
        .limit(50)
    )
    rows = result.all()
    return {
        "count": len(rows),
        "reports": [
            {
                "id": r.id,
                "reporter_tg_id": r.reporter_id,
                "reporter_public_id": reporter.public_id,
                "reported_tg_id": r.reported_id,
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            }
            for r, reporter in rows
        ],
    }


@router.post("/reports/{report_id}/approve", dependencies=[Depends(verify_api_key)])
async def admin_approve_report_api(
    report_id: int,
    payload: ReportActionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict:
    """Approve a report — rewards reporter, warns reported user."""
    from matching_bot_project.bot.core.loader import warning_engine
    result = await warning_engine.approve_report(db, report_id, payload.admin_tg_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # --- 🔔 ارسال نوتیفیکیشن‌ها به کاربران ---
    from matching_bot_project.bot.core.loader import bot
    from matching_bot_project.bot.core.constants import Messages
    import asyncio

    reporter_id = result.get('reporter_tg_id')
    reported_id = result.get('reported_tg_id')
    reward_coins = result.get('reward_coins', 0)
    wr = result.get('warning_result', {})

    async def _notify():
        # پیام پاداش به گزارش‌دهنده
        if reporter_id:
            try:
                await bot.send_message(
                    chat_id=reporter_id,
                    text=Messages.REPORT_REWARDED.format(coins=reward_coins)
                )
            except Exception:
                pass

        # پیام اخطار/بن به متخلف
        if reported_id:
            try:
                if wr.get('is_banned'):
                    await bot.send_message(chat_id=reported_id, text=Messages.BANNED_PERMANENT)
                else:
                    await bot.send_message(
                        chat_id=reported_id,
                        text=Messages.WARNING_ISSUED.format(
                            reason="تأیید گزارش تخلف شما توسط مدیریت",
                            count=wr.get('warning_count', 1)
                        )
                    )
            except Exception:
                pass

    # اجرای تسک در پس‌زمینه برای جلوگیری از کند شدن پاسخ API
    asyncio.create_task(_notify())
    # ----------------------------------------

    logger.info("REPORT_APPROVE report_id=%s admin_tg_id=%s", report_id, payload.admin_tg_id)
    return {"message": "Report approved", "details": result}


@router.post("/reports/{report_id}/reject", dependencies=[Depends(verify_api_key)])
async def admin_reject_report_api(
    report_id: int,
    payload: ReportActionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict:
    """Reject a report (false report) — warns the reporter."""
    from matching_bot_project.bot.core.loader import warning_engine
    result = await warning_engine.reject_report(db, report_id, payload.admin_tg_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # --- 🔔 ارسال نوتیفیکیشن اخطار به گزارش‌دهنده ---
    from matching_bot_project.bot.core.loader import bot
    from matching_bot_project.bot.core.constants import Messages
    import asyncio

    reporter_id = result.get('reporter_tg_id')
    wr = result.get('warning_result', {})

    async def _notify():
        if reporter_id:
            try:
                if wr.get('is_banned'):
                    await bot.send_message(chat_id=reporter_id, text=Messages.BANNED_PERMANENT)
                else:
                    await bot.send_message(
                        chat_id=reporter_id,
                        text=Messages.WARNING_ISSUED.format(
                            reason="ثبت گزارش کاذب و اشتباه",
                            count=wr.get('warning_count', 1)
                        )
                    )
            except Exception:
                pass

    # اجرای تسک در پس‌زمینه برای جلوگیری از کند شدن پاسخ API
    asyncio.create_task(_notify())
    # -----------------------------------------------

    logger.info("REPORT_REJECT report_id=%s admin_tg_id=%s", report_id, payload.admin_tg_id)
    return {"message": "Report rejected", "details": result}


@router.get("/banners/pending", dependencies=[Depends(verify_api_key)])
async def list_pending_banners(db: AsyncSession = Depends(get_db_session)) -> Dict:
    """List pending banner forwards for admin review."""
    from matching_bot_project.database.models.models import BannerForward, BannerCampaign, User
    result = await db.execute(
        select(BannerForward, BannerCampaign, User)
        .join(BannerCampaign, BannerForward.campaign_id == BannerCampaign.id)
        .join(User, BannerForward.user_tg_id == User.tg_id)
        .where(BannerForward.status == "pending")
        .order_by(BannerForward.forwarded_at.asc())
        .limit(50)
    )
    rows = result.all()
    return {
        "count": len(rows),
        "forwards": [
            {
                "id": bf.id,
                "user_tg_id": bf.user_tg_id,
                "user_public_id": user.public_id,
                "campaign_id": bf.campaign_id,
                "reward_coins": campaign.reward_coins,
                "forwarded_at": bf.forwarded_at.isoformat(),
            }
            for bf, campaign, user in rows
        ],
    }


@router.post("/banners/{forward_id}/approve", dependencies=[Depends(verify_api_key)])
async def admin_approve_banner_api(
    forward_id: int,
    payload: ReportActionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict:
    """Approve a banner forward — credits user with reward_coins."""
    from matching_bot_project.bot.core.loader import free_coin_banner_service
    result = await free_coin_banner_service.approve_forward(db, forward_id, payload.admin_tg_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    logger.info("BANNER_APPROVE forward_id=%s admin_tg_id=%s", forward_id, payload.admin_tg_id)
    return result


@router.post("/banners/{forward_id}/reject", dependencies=[Depends(verify_api_key)])
async def admin_reject_banner_api(
    forward_id: int,
    payload: BannerActionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict:
    """Reject a banner forward."""
    from matching_bot_project.bot.core.loader import free_coin_banner_service
    result = await free_coin_banner_service.reject_forward(db, forward_id, payload.admin_tg_id, payload.note or "")
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    logger.info("BANNER_REJECT forward_id=%s admin_tg_id=%s", forward_id, payload.admin_tg_id)
    return result


@router.post("/channels/add", dependencies=[Depends(verify_api_key)])
async def admin_add_channel_api(
    payload: ChannelAddRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict:
    """Add a force-join channel (max 5)."""
    from matching_bot_project.database.queries.crud import add_admin_channel
    success, msg = await add_admin_channel(db, payload.channel_id, payload.channel_username, payload.invite_link)
    return {"success": success, "message": msg}


@router.post("/channels/remove", dependencies=[Depends(verify_api_key)])
async def admin_remove_channel_api(
    payload: ChannelRemoveRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict:
    """Remove a force-join channel."""
    from matching_bot_project.database.queries.crud import remove_admin_channel
    success = await remove_admin_channel(db, payload.channel_id)
    return {"success": success}


@router.get("/channels", dependencies=[Depends(verify_api_key)])
async def admin_list_channels_api(db: AsyncSession = Depends(get_db_session)) -> Dict:
    """List all force-join channels."""
    from matching_bot_project.database.queries.crud import get_active_admin_channels
    channels = await get_active_admin_channels(db)
    return {
        "count": len(channels),
        "channels": [
            {
                "id": c.id,
                "channel_id": c.channel_id,
                "channel_username": c.channel_username,
                "invite_link": c.invite_link,
                "is_active": c.is_active,
            }
            for c in channels
        ],
    }


@router.get("/referrals/stats", dependencies=[Depends(verify_api_key)])
async def admin_referral_stats(db_read_session: AsyncSession = Depends(get_read_db_session)) -> Dict:
    """Global referral statistics."""
    from matching_bot_project.database.models.models import ReferralCommission, User
    from sqlalchemy import func as _f
    total_commissions = await db_read_session.scalar(
        select(_f.sum(ReferralCommission.commission_coins))
    )
    total_payouts = await db.scalar(select(_f.count(ReferralCommission.id)))
    total_referrers = await db.scalar(
        select(_f.count(_f.distinct(ReferralCommission.referrer_tg_id)))
    )
    top_referrers_result = await db.execute(
        select(
            ReferralCommission.referrer_tg_id,
            _f.sum(ReferralCommission.commission_coins).label("total"),
        )
        .group_by(ReferralCommission.referrer_tg_id)
        .order_by(_f.sum(ReferralCommission.commission_coins).desc())
        .limit(10)
    )
    top_referrers = [
        {"tg_id": r[0], "total_commission": r[1]}
        for r in top_referrers_result.all()
    ]
    return {
        "total_commission_coins": total_commissions or 0,
        "total_payouts": total_payouts or 0,
        "unique_referrers": total_referrers or 0,
        "top_referrers": top_referrers,
    }


@router.get("/warnings/user/{tg_id}", dependencies=[Depends(verify_api_key)])
async def admin_get_user_warnings(
    tg_id: int, db: AsyncSession = Depends(get_db_session)
) -> Dict:
    """Get warnings history for a specific user."""
    from matching_bot_project.bot.core.loader import warning_engine
    warnings = await warning_engine.get_user_warnings(db, tg_id, limit=20)
    return {
        "user_tg_id": tg_id,
        "warning_count": len(warnings),
        "warnings": [
            {
                "id": w.id,
                "reason": w.reason,
                "issued_by": w.issued_by,
                "issued_at": w.issued_at.isoformat(),
            }
            for w in warnings
        ],
    }
