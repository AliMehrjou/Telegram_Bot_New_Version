import re

file_path = 'api/routes/admin.py'

with open(file_path, 'r') as f:
    content = f.read()

new_endpoints = """
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
"""

if "add_coins_api" not in content:
    with open(file_path, 'a') as f:
        f.write(new_endpoints)
    print("Added advanced stats to api/routes/admin.py")
else:
    print("Stats already in api/routes/admin.py")
