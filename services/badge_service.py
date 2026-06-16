import logging
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from aiogram import Bot
from matching_bot_project.database.models.models import UserBadge, MatchHistory, UserLike
from matching_bot_project.database.queries import crud

logger = logging.getLogger(__name__)

BADGES = {
    "first_match":    ("🌟 ستاره نو",    "اولین مچ موفق"),
    "chat_10":        ("💬 پرحرف",       "۱۰ چت کامل"),
    "likes_50":       ("❤️ محبوب",       "۵۰ لایک دریافتی"),
    "streak_7":       ("🔥 داغ",         "۷ روز فعال متوالی"),
    "matches_100":    ("👑 افسانه",       "۱۰۰ مچ موفق"),
    "profile_100":    ("✅ کامل",         "پروفایل ۱۰۰٪ تکمیل"),
    "mutual_like_5":  ("💘 دلبر",        "۵ مچ از طریق لایک"),
}

REWARDS = {
    "streak_7": 10,
    "matches_100": 50,
    "mutual_like_5": 5,
}

async def _award_badge(session: AsyncSession, bot: Bot, tg_id: int, badge_key: str):
    # Try insert
    badge_entry = UserBadge(user_id=tg_id, badge_key=badge_key)
    session.add(badge_entry)
    try:
        await session.flush()
    except IntegrityError:
        # Already has badge
        await session.rollback()
        return False

    badge_name, badge_desc = BADGES[badge_key]

    # Process reward
    reward = REWARDS.get(badge_key, 0)
    user = await crud.get_user_by_tg_id(session, tg_id)
    if reward > 0 and user:
        await crud.process_coin_transaction(session, user, reward, f"پاداش نشان {badge_name}")
        reward_text = f"\n🎁 شما {reward} سکه جایزه گرفتید!"
    else:
        reward_text = ""

    # Notify user
    try:
        await bot.send_message(
            chat_id=tg_id,
            text=f"🎉 تبریک! شما نشان جدیدی کسب کردید:\n\n{badge_name} - {badge_desc}{reward_text}"
        )
    except Exception as e:
        logger.warning(f"Failed to notify badge {badge_key} to {tg_id}: {e}")

    return True

async def check_and_award_badges(session: AsyncSession, redis_client, bot: Bot, tg_id: int) -> list[str]:
    """Check conditions and award any missing badges."""
    new_badges = []

    # Check already awarded
    stmt = select(UserBadge.badge_key).where(UserBadge.user_id == tg_id)
    res = await session.execute(stmt)
    owned = {row[0] for row in res.all()}

    # 1. Matches (first_match, matches_100)
    if "first_match" not in owned or "matches_100" not in owned:
        matches_count = await session.scalar(
            select(func.count(MatchHistory.id)).where(
                (MatchHistory.user_one_id == tg_id) | (MatchHistory.user_two_id == tg_id)
            )
        )
        if matches_count >= 1 and "first_match" not in owned:
            if await _award_badge(session, bot, tg_id, "first_match"):
                new_badges.append("first_match")
        if matches_count >= 100 and "matches_100" not in owned:
            if await _award_badge(session, bot, tg_id, "matches_100"):
                new_badges.append("matches_100")

    # 2. Chats (chat_10)
    if "chat_10" not in owned:
        chats_count = await session.scalar(
            select(func.count(MatchHistory.id)).where(
                ((MatchHistory.user_one_id == tg_id) | (MatchHistory.user_two_id == tg_id)) &
                (MatchHistory.chat_approved == True)
            )
        )
        if chats_count >= 10:
            if await _award_badge(session, bot, tg_id, "chat_10"):
                new_badges.append("chat_10")

    # 3. Likes received (likes_50)
    if "likes_50" not in owned:
        likes_recv = await session.scalar(
            select(func.count(UserLike.id)).where(
                (UserLike.liked_id == tg_id) & (UserLike.is_pass == False)
            )
        )
        if likes_recv >= 50:
            if await _award_badge(session, bot, tg_id, "likes_50"):
                new_badges.append("likes_50")

    # 4. Profile 100% (profile_100)
    if "profile_100" not in owned:
        user = await crud.get_user_by_tg_id(session, tg_id)
        if user and user.bio and user.interests and user.profile_photo_file_id: # Photo logic assuming implemented elsewhere
            if await _award_badge(session, bot, tg_id, "profile_100"):
                new_badges.append("profile_100")

    # 5. Streak 7 (streak_7) -> Checked in gamification checkin directly or here if redis available
    if "streak_7" not in owned:
        streak_str = await redis_client.get(f"user:streak:{tg_id}")
        if streak_str and int(streak_str) >= 7:
            if await _award_badge(session, bot, tg_id, "streak_7"):
                new_badges.append("streak_7")

    # Commit any changes (mostly the coin rewards that happen inside _award_badge)
    await session.commit()
    return new_badges
