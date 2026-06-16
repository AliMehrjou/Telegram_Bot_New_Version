from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Dict, List
import logging

from matching_bot_project.database.session import get_db_session
from matching_bot_project.database.models.models import User, MatchHistory, Question
from matching_bot_project.bot.core.loader import bot
from matching_bot_project.services.broadcast_worker import BroadcastWorker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin Control Panel"])


@router.get("/stats")
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


@router.post("/broadcast")
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
