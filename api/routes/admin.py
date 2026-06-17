from fastapi import APIRouter, Depends, HTTPException, Query, Header, Security
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Dict, List, Optional
from datetime import datetime
import logging

from matching_bot_project.database.session import get_db_session
from matching_bot_project.database.models.models import User, MatchHistory, Question
from matching_bot_project.database.queries.crud import get_user_by_tg_id, process_coin_transaction
from matching_bot_project.bot.core.loader import bot
from matching_bot_project.services.broadcast_worker import BroadcastWorker
from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin Control Panel"])

def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != settings.ADMIN_SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key


@router.get("/stats", dependencies=[Depends(verify_api_key)])
async def get_bot_statistics(db: AsyncSession = Depends(get_db_session)) -> Dict:
    """Provides high-level analytical performance logs for the registration metrics."""
    try:
        total_users = await db.scalar(select(func.count(User.id)))
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
    text: str = Query(..., min_length=5),
    db: AsyncSession = Depends(get_db_session)
) -> Dict:
    """Dispatches a global notification message without blocking main thread flow."""
    try:
        # Pull all target TG User IDs
        result = await db.execute(select(User.tg_id))
        user_ids = [row[0] for row in result.all()]
        
        if not user_ids:
            return {"status": "skipped", "message": "No users found in database."}
            
        worker = BroadcastWorker(bot=bot)
        # Dispatch asynchronously
        worker.start_background_broadcast(user_ids=user_ids, text=text, delay_ms=40)
        
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
    tg_id: int,
    amount: int,
    db: AsyncSession = Depends(get_db_session)
) -> Dict:
    user = await get_user_by_tg_id(db, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await process_coin_transaction(db, user, amount, "API Admin added coins")
    await db.commit()

    return {"status": "success", "tg_id": tg_id, "new_balance": user.coin_balance}

@router.post("/ban", dependencies=[Depends(verify_api_key)])
async def api_ban_user(
    tg_id: int,
    ban: bool = Query(True),
    db: AsyncSession = Depends(get_db_session)
) -> Dict:
    user = await get_user_by_tg_id(db, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_banned = ban
    await db.commit()

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
async def api_get_advanced_stats(db: AsyncSession = Depends(get_db_session)) -> Dict:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_reg = await db.scalar(select(func.count(User.id)).where(User.created_at >= today))

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
