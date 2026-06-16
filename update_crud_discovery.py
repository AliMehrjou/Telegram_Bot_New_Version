import re

file_path = 'database/queries/crud.py'

with open(file_path, 'r') as f:
    content = f.read()

new_crud = """
# --- Discovery / Likes ---
async def save_like(session: AsyncSession, liker_id: int, liked_id: int, is_pass: bool) -> UserLike:
    from database.models.models import UserLike
    from sqlalchemy.exc import IntegrityError

    like_record = UserLike(liker_id=liker_id, liked_id=liked_id, is_pass=is_pass)
    session.add(like_record)
    try:
        await session.flush()
    except IntegrityError:
        # Already liked/passed
        await session.rollback()
        # You could also update it here, but generally first action stands
    return like_record

async def check_mutual_like(session: AsyncSession, user_one_id: int, user_two_id: int) -> bool:
    from database.models.models import UserLike
    from sqlalchemy import and_

    stmt = select(UserLike).where(
        and_(
            UserLike.liker_id == user_two_id,
            UserLike.liked_id == user_one_id,
            UserLike.is_pass == False
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None

async def get_daily_like_count(redis_client, tg_id: int) -> int:
    count = await redis_client.get(f"user:{tg_id}:likes_today")
    return int(count) if count else 0

async def increment_daily_like_count(redis_client, tg_id: int) -> None:
    from datetime import datetime, timedelta

    key = f"user:{tg_id}:likes_today"
    count = await redis_client.incr(key)

    if count == 1:
        # Set expiration to midnight
        now = datetime.utcnow()
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_until_midnight = int((midnight - now).total_seconds())
        await redis_client.expire(key, seconds_until_midnight)

"""

if "async def save_like" not in content:
    with open(file_path, 'a') as f:
        f.write(new_crud)
    print("Added discovery methods to crud.py")
