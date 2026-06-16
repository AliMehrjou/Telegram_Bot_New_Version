from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Dict, List
import logging

from matching_bot_project.database.session import get_db_session
from matching_bot_project.database.models.models import User, MatchHistory, Question
from matching_bot_project.bot.core.loader import bot
from matching_bot_project.bot.core.config import settings
from matching_bot_project.services.broadcast_worker import BroadcastWorker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin Control Panel"])

api_key_header = APIKeyHeader(name="X-Admin-Token", auto_error=True)

async def get_admin_token(api_key_header: str = Security(api_key_header)):
    if api_key_header != settings.BOT_TOKEN: # Using bot token as admin secret for simplicity, or ideally a separate ADMIN_SECRET
        raise HTTPException(status_code=403, detail="Could not validate credentials")
    return api_key_header


@router.get("/stats")
async def get_bot_statistics(
    db: AsyncSession = Depends(get_db_session),
    admin_token: str = Depends(get_admin_token)
) -> Dict:
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
    db: AsyncSession = Depends(get_db_session),
    admin_token: str = Depends(get_admin_token)
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

from pydantic import BaseModel

class AddCoinRequest(BaseModel):
    tg_id: str | int  # int or "all" or "vip"
    amount: int

@router.post("/coins/add")
async def add_coins_api(
    req: AddCoinRequest,
    db: AsyncSession = Depends(get_db_session),
    admin_token: str = Depends(get_admin_token)
):
    from sqlalchemy import update, true
    from matching_bot_project.database.queries import crud
    if req.tg_id == "all":
        await db.execute(update(User).values(coin_balance=User.coin_balance + req.amount, total_earned_coins=User.total_earned_coins + req.amount))
        await db.commit()
        return {"status": "success", "message": f"Added {req.amount} coins to all users"}
    elif req.tg_id == "vip":
        await db.execute(update(User).where(User.is_vip == true()).values(coin_balance=User.coin_balance + req.amount, total_earned_coins=User.total_earned_coins + req.amount))
        await db.commit()
        return {"status": "success", "message": f"Added {req.amount} coins to all VIP users"}
    else:
        try:
            tg_id = int(req.tg_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tg_id")
        user = await crud.get_user_by_tg_id(db, tg_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        await crud.process_coin_transaction(db, user, req.amount, "API Admin")
        await db.commit()
        return {"status": "success", "message": f"Added {req.amount} coins to user {tg_id}"}

class BanRequest(BaseModel):
    tg_id: int
    banned: bool

@router.post("/ban")
async def ban_user_api(
    req: BanRequest,
    db: AsyncSession = Depends(get_db_session),
    admin_token: str = Depends(get_admin_token)
):
    from matching_bot_project.database.queries import crud
    user = await crud.get_user_by_tg_id(db, req.tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_banned = req.banned
    await db.commit()
    return {"status": "success", "message": f"User {req.tg_id} ban status set to {req.banned}"}

@router.get("/user/{tg_id}")
async def get_user_info_api(
    tg_id: int,
    db: AsyncSession = Depends(get_db_session),
    admin_token: str = Depends(get_admin_token)
):
    from matching_bot_project.database.queries import crud
    user = await crud.get_user_by_tg_id(db, tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "tg_id": user.tg_id,
        "first_name": user.first_name,
        "gender": user.gender,
        "age": user.age,
        "province": user.province,
        "city": user.city,
        "coin_balance": user.coin_balance,
        "is_vip": user.is_vip,
        "is_banned": getattr(user, 'is_banned', False),
        "last_active": user.last_active
    }

@router.get("/stats/advanced")
async def get_advanced_stats_api(
    db: AsyncSession = Depends(get_db_session),
    admin_token: str = Depends(get_admin_token)
):
    from matching_bot_project.database.queries import crud
    return {
        "registrations_today": await crud.get_registrations_by_day(db, 1),
        "registrations_week": await crud.get_registrations_by_day(db, 7),
        "peak_hours": await crud.get_peak_hours(db),
        "top_provinces": await crud.get_top_provinces(db, 10),
        "chat_conversion_rate": await crud.get_chat_conversion_rate(db)
    }
