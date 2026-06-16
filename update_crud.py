import re

file_path = 'database/queries/crud.py'

with open(file_path, 'r') as f:
    content = f.read()

new_functions = """
# --- Admin Stats Additions ---
async def get_registrations_by_day(session: AsyncSession, days: int) -> int:
    from datetime import datetime, timedelta
    from sqlalchemy import func
    cutoff = datetime.utcnow() - timedelta(days=days)
    stmt = select(func.count(User.id)).where(User.created_at >= cutoff)
    return await session.scalar(stmt) or 0

async def get_peak_hours(session: AsyncSession) -> list:
    from sqlalchemy import func
    # Using func.hour for MySQL. SQLite/Postgres might need different functions.
    stmt = select(func.hour(User.last_active).label('hr'), func.count(User.id).label('cnt')).group_by('hr').order_by(func.count(User.id).desc()).limit(5)
    res = await session.execute(stmt)
    return res.all()

async def get_top_provinces(session: AsyncSession, limit: int = 10) -> list:
    from sqlalchemy import func
    stmt = select(User.province, func.count(User.id)).group_by(User.province).order_by(func.count(User.id).desc()).limit(limit)
    res = await session.execute(stmt)
    return res.all()

async def get_chat_conversion_rate(session: AsyncSession) -> float:
    from sqlalchemy import func
    # chat_approved=True / questionnaire_completed=True
    total_completed = await session.scalar(select(func.count(MatchHistory.id)).where(MatchHistory.questionnaire_completed == True))
    if not total_completed:
        return 0.0
    total_approved = await session.scalar(select(func.count(MatchHistory.id)).where(MatchHistory.chat_approved == True))
    return (total_approved / total_completed) * 100.0
"""

if "get_registrations_by_day" not in content:
    with open(file_path, 'a') as f:
        f.write(new_functions)
    print("Added stats to crud.py")
else:
    print("Stats already in crud.py")
