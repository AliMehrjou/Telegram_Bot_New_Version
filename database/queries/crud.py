import logging
import string
import random
from typing import Optional, List
from datetime import datetime
from sqlalchemy import select, and_, or_, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.mysql import insert
from matching_bot_project.database.models.models import User
from sqlalchemy import select
from matching_bot_project.bot.core.loader import redis_client
from matching_bot_project.database.models.models import (
    User, MatchHistory, Question, UserAnswer,
    CoinTransaction, FriendList, BlockList, UserLike, UserReport
)

logger = logging.getLogger(__name__)


async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> Optional[User]:
    stmt = select(User).where(User.tg_id == tg_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def get_user_by_public_id(session: AsyncSession, public_id: str) -> Optional[User]:
    stmt = select(User).where(User.public_id == public_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def process_coin_transaction(
    session: AsyncSession, 
    user: User, 
    amount: int, 
    description: str,
    ignore_multiplier: bool = False
) -> bool:
    """Safely processes coin addition/deduction and logs the transaction.
    Automatically applies active event multipliers for positive amounts unless ignored."""
    if amount < 0 and user.coin_balance < abs(amount):
        return False # Insufficient funds
        
    final_amount = amount

    # اعمال ضریب ایونت فقط برای واریزی‌ها (نه کسر سکه)
    if final_amount > 0 and not ignore_multiplier:
        try:
            # خواندن ضریب ایونت فعال از ردیس
            active_multiplier_str = await redis_client.get("bot:active_event_multiplier")
            if active_multiplier_str:
                multiplier = float(active_multiplier_str)
                # ضرب کردن و رند کردن به عدد صحیح
                final_amount = int(final_amount * multiplier)
                
                # اگر ضریب اعمال شد، به توضیحات تراکنش هم اضافه می‌کنیم
                if multiplier > 1.0:
                    description += f" (ضریب رویداد ×{multiplier})"
        except Exception as e:
            logger.error(f"Error fetching event multiplier from Redis: {e}")
            
    user.coin_balance += final_amount
    if final_amount > 0:
        user.total_earned_coins += final_amount
    else:
        user.total_spent_coins += abs(final_amount)
        
    transaction = CoinTransaction(
        user_id=user.tg_id,
        amount=final_amount,
        description=description
    )
    session.add(transaction)
    return True


async def create_user(
    session: AsyncSession, 
    tg_id: int, 
    first_name: str, 
    username: Optional[str] = None, 
    referrer_id: Optional[int] = None
) -> User:
    """Inserts a new user record upon first start command and grants 3 coins."""
    user = User(
        tg_id=tg_id,
        first_name=first_name,
        username=username,
        referrer_id=referrer_id,
        completed_registration=False,
        coin_balance=3,
        total_earned_coins=3
    )
    session.add(user)
    await session.flush()
    
    # Log initial welcome coins
    start_tx = CoinTransaction(user_id=tg_id, amount=3, description="هدیه عضویت اولیه")
    session.add(start_tx)
    
    return user


async def complete_user_registration(
    session: AsyncSession, 
    tg_id: int, 
    gender: str, 
    age: int, 
    province: str,
    city: str,
    tags: str = None,
    profile_photo_file_id: str = None
) -> dict:
    """Completes profile, rewards coins, handles referral rewards and returns status dictionary."""
    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        return {"success": False, "referrer_tg_id": None}
        
    user.gender = gender
    user.age = age
    user.province = province
    user.city = city
    user.tags = tags
    user.profile_photo_file_id = profile_photo_file_id
    user.completed_registration = True
    
    # Reward for completing profile (+5 Coins)
    await process_coin_transaction(session, user, 5, "تکمیل اطلاعات پروفایل")
    
    referrer_tg_id = None
    
    # Process Referral Reward
    if user.referrer_id:
        stmt = select(User).where(User.id == user.referrer_id)
        result = await session.execute(stmt)
        referrer = result.scalar_one_or_none()
        
        if referrer:
            # واریز سکه به دعوت‌کننده
            await process_coin_transaction(session, referrer, 5, f"پاداش دعوت کاربر {tg_id}")
            # واریز سکه اضافه به کاربر دعوت‌شده
            await process_coin_transaction(session, user, 5, "پاداش ورود از طریق لینک دعوت")
            
            referrer_tg_id = referrer.tg_id
            logger.info(f"Referral Success: User {tg_id} completed onboarding. Referrer {referrer.tg_id} awarded 5 coins, invited user awarded extra 5 coins.")

    await session.flush()
    return {"success": True, "referrer_tg_id": referrer_tg_id}


async def create_match_history(
    session: AsyncSession, 
    user_one_id: int, 
    user_two_id: int
) -> MatchHistory:
    """Logs a new active match history record."""
    match_rec = MatchHistory(
        user_one_id=user_one_id,
        user_two_id=user_two_id,
        is_active=True
    )
    session.add(match_rec)
    await session.flush()
    return match_rec


async def get_active_match(session: AsyncSession, tg_id: int) -> Optional[MatchHistory]:
    """Retrieves current active match recording for a user."""
    stmt = select(MatchHistory).where(
        and_(
            MatchHistory.is_active == True,
            or_(
                MatchHistory.user_one_id == tg_id,
                MatchHistory.user_two_id == tg_id
            )
        )
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def get_random_questions(session: AsyncSession, limit: int = 20) -> List[Question]:
    """Retrieves random questions from the 60-question database bank."""
    from sqlalchemy.sql import func
    stmt = select(Question).order_by(func.rand()).limit(limit)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def update_user_profile(
    session: AsyncSession,
    tg_id: int,
    bio: Optional[str] = None,
    interests: Optional[str] = None,
    trust_score: Optional[int] = None,
    invisible_mode: Optional[bool] = None,
    is_banned: Optional[bool] = None,
    report_count: Optional[int] = None,
) -> bool:
    """Updates user profile fields including the newly added model fields."""
    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        return False

    if bio is not None:
        user.bio = bio
    if interests is not None:
        user.interests = interests
    if trust_score is not None:
        user.trust_score = trust_score
    if invisible_mode is not None:
        user.invisible_mode = invisible_mode
    if is_banned is not None:
        user.is_banned = is_banned
    if report_count is not None:
        user.report_count = report_count

    await session.flush()
    return True


async def create_user_like(
    session: AsyncSession,
    liker_id: int,
    liked_id: int,
    is_pass: bool = False
) -> UserLike:
    """Creates a new like or pass record between two users."""
    like_record = UserLike(
        liker_id=liker_id,
        liked_id=liked_id,
        is_pass=is_pass
    )
    session.add(like_record)
    await session.flush()
    return like_record


async def create_user_report(
    session: AsyncSession,
    reporter_id: int,
    reported_id: int,
    reason: str,
    match_history_id: Optional[int] = None
) -> UserReport:
    """Creates a new report record for a user."""
    report_record = UserReport(
        reporter_id=reporter_id,
        reported_id=reported_id,
        reason=reason,
        match_history_id=match_history_id
    )
    session.add(report_record)

    # Increment reported user's report_count
    reported_user = await get_user_by_tg_id(session, reported_id)
    if reported_user:
        reported_user.report_count += 1

    await session.flush()
    return report_record


async def save_like(session: AsyncSession, liker_id: int, liked_id: int, is_pass: bool) -> UserLike:
    """Saves a like or pass interaction into the database and updates likes_count."""
    
    check_stmt = select(UserLike.is_pass).where(
        and_(UserLike.liker_id == liker_id, UserLike.liked_id == liked_id)
    )
    existing_record = await session.execute(check_stmt)
    existing_is_pass = existing_record.scalar_one_or_none()
    
    stmt = insert(UserLike).values(
        liker_id=liker_id, 
        liked_id=liked_id, 
        is_pass=is_pass
    ).on_duplicate_key_update(
        is_pass=is_pass
    )
    await session.execute(stmt)
    
    if not is_pass and (existing_is_pass is None or existing_is_pass is True):
        await session.execute(
            update(User)
            .where(User.tg_id == liked_id)
            .values(likes_count=User.likes_count + 1)
        )
        
    await session.flush()
    
    fetch_stmt = select(UserLike).where(
        and_(UserLike.liker_id == liker_id, UserLike.liked_id == liked_id)
    )
    res = await session.execute(fetch_stmt)
    return res.scalar_one_or_none()


async def update_silent_mode(session: AsyncSession, tg_id: int, silent_until: Optional[datetime]) -> bool:
    """آپدیت زمان سایلنت مود برای جلوگیری از دریافت نوتیفیکیشن مچ"""
    result = await session.execute(
        update(User)
        .where(User.tg_id == tg_id)
        .values(silent_until=silent_until)
    )
    await session.flush()
    return result.rowcount > 0


async def ensure_public_id_exists(session: AsyncSession, tg_id: int) -> str:
    """بررسی می‌کند که آیا کاربر public_id دارد یا نه، اگر نداشت برایش می‌سازد"""
    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        return ""
        
    if not user.public_id:
        characters = string.ascii_letters + string.digits
        new_id = f"user_{''.join(random.choice(characters) for _ in range(6))}"
        
        user.public_id = new_id
        await session.flush()
        return new_id
        
    return user.public_id


async def get_discovery_candidate(session: AsyncSession, current_user_id: int, current_user_gender: str) -> Optional[User]:
    target_gender = "Female" if current_user_gender.lower() == "male" else "Male"
    if current_user_gender.lower() == "boy": target_gender = "girl"
    if current_user_gender.lower() == "girl": target_gender = "boy"

    from sqlalchemy import select, case, func, and_, exists

    liked_me_exists = select(1).where(
        and_(
            UserLike.liker_id == User.tg_id, 
            UserLike.liked_id == current_user_id, 
            UserLike.is_pass == False
        )
    ).correlate(User).exists()

    acted_by_me_exists = select(1).where(
        and_(
            UserLike.liker_id == current_user_id, 
            UserLike.liked_id == User.tg_id
        )
    ).correlate(User).exists()

    blocked_me_exists = select(1).where(
        and_(
            BlockList.blocker_id == User.tg_id, 
            BlockList.blocked_id == current_user_id
        )
    ).correlate(User).exists()

    blocked_by_me_exists = select(1).where(
        and_(
            BlockList.blocker_id == current_user_id, 
            BlockList.blocked_id == User.tg_id
        )
    ).correlate(User).exists()

    priority_expr = case(
        (liked_me_exists, 1),
        else_=0
    )

    stmt = select(User).where(
        and_(
            User.tg_id != current_user_id,
            func.lower(User.gender) == target_gender.lower(),
            User.completed_registration == True,
            getattr(User, "invisible_mode", False) == False,
            ~acted_by_me_exists,
            ~blocked_by_me_exists,
            ~blocked_me_exists
        )
    ).order_by(
        priority_expr.desc(),
        func.rand()
    ).limit(1)

    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def check_mutual_like(session: AsyncSession, user_one_id: int, user_two_id: int) -> bool:
    """Checks if two users have both liked each other (is_pass=False)."""
    stmt = select(func.count(UserLike.id)).where(
        or_(
            and_(UserLike.liker_id == user_one_id, UserLike.liked_id == user_two_id, UserLike.is_pass == False),
            and_(UserLike.liker_id == user_two_id, UserLike.liked_id == user_one_id, UserLike.is_pass == False)
        )
    )
    result = await session.execute(stmt)
    count = result.scalar()
    return count == 2


async def save_user_answer(
    session: AsyncSession, 
    user_id: int, 
    question_id: int, 
    match_history_id: int, 
    selected_option: str
) -> UserAnswer:
    ans = UserAnswer(
        user_id=user_id,
        question_id=question_id,
        match_history_id=match_history_id,
        selected_option=selected_option
    )
    session.add(ans)
    await session.flush()
    return ans


async def check_question_status(
    session: AsyncSession, 
    match_history_id: int, 
    question_id: int
) -> List[UserAnswer]:
    stmt = select(UserAnswer).where(
        and_(
            UserAnswer.match_history_id == match_history_id,
            UserAnswer.question_id == question_id
        )
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def seed_sixty_question_bank_if_empty(session: AsyncSession):
    stmt = select(Question).limit(1)
    res = await session.execute(stmt)
    if res.scalar_one_or_none():
        return
        
    questions_data = [
        ("به نظر شما در رابطه عاطفی، کدام گزینه از اهمیت بشتری برخوردار است؟", "احترام متقابل و درک شرایط", "عشق پرشور و هیجان عاطفی", "عاطفی"),
        ("ترجیح می‌دهید اوقات فراغت خود را چگونه سپری کنید؟", "استراحت در خانه و تماشای فیلم", "تفریحات گروهی و سفرهای ماجراجویانه", "تفریحات"),
        ("اگر در بین زوجین اختلافی پیش بیاید، بهترین راه حل چیست؟", "گفتگوی منطقی و سریع درباره موضوع", "کمی صبوری و صحبت کردن در زمان مناسب‌تر", "حل‌مسئله"),
        ("در مورد مدیریت هزینه‌ها در زندگی مشترک، نظر شما چیست؟", "برنامه‌ریزی دقیق مالی و پس‌انداز مشترک", "تعادل بین خرج کردن و زندگی در لحظه حال", "مالی"),
        ("آیا با کار کردن موازی هر دو زوج در خانواده موافق هستید؟", "بله، همکاری در تامین رفاه ضروری است", "ترجیح بر تمرکز یکی از طرفین روی خانه است", "اشتغال"),
        ("کدام روش ابراز علاقه را ترجیح می‌دهید؟", "کلامی و شنیدن جملات محبت‌آمیز", "عملی و کمک در کارهای روزمره و هدیه", "ابرازعلاقه"),
        ("آیا صمیمیت فکری و اشتراک نظرات اولویت دارد یا تفاهم رفتاری؟", "صمیمیت فکری و عقیدتی عمیق", "تفاهم رفتاری و سازش در برخوردها", "روانی"),
        ("میزان رفت‌وآمد و صمیمیت با خانواده همسر باید چگونه باشد؟", "بسیار زیاد و کاملاً صمیمی", "کنترل‌شده و بر پایه احترام متقابل", "خانواده"),
        ("تصمیم‌گیری‌های کلان زندگی مشترک مثل خرید خانه بر چه اساسی باشد؟", "مشورت کامل دو طرفه و توافق صد درصدی", "تصمیم نهایی توسط مدیر با تجربه خانواده", "تصمیم‌گیری"),
        ("میزان فعالیت در شبکه‌های اجتماعی همسرتان چقدر برایتان مهم است؟", "باید محدود و تحت نظارت مشترک باشد", "یک حریم شخصی است و چندان مهم نیست", "فضای‌مجازی"),
    ]
    
    for i in range(11, 81):
        questions_data.append((
            f"سوال نمونه {i}: نظر شما در مورد معیار زندگی مشترک برای انتخاب {i} چیست؟",
            "گزینه اول و ملاک تفاهم اصولی",
            "گزینه دوم و انعطاف در رفتارهای متقابل",
            "رابطه"
        ))
        
    for q_text, opt_a, opt_b, cat in questions_data:
        q = Question(question_text=q_text, option_a=opt_a, option_b=opt_b, category=cat)
        session.add(q)
        
    await session.commit()
    logger.info("Successfully seeded 80 questions into MySQL database Questions schema.")


async def get_referral_count(session: AsyncSession, tg_id: int) -> int:
    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        return 0
    stmt = select(func.count(User.id)).where(User.referrer_id == user.id)
    result = await session.execute(stmt)
    return result.scalar() or 0


async def get_nearby_candidates(session: AsyncSession, current_user: User, limit: int = 5) -> List[User]:
    target_gender = "Female" if current_user.gender == "Male" else "Male"
    
    stmt = select(User).where(
        and_(
            User.tg_id != current_user.tg_id,
            User.completed_registration == True,
            User.gender == target_gender,
            User.province == current_user.province,
            User.city == current_user.city,
            User.invisible_mode == False,
            User.is_banned == False
        )
    ).order_by(User.last_active.desc()).limit(limit)
    
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def get_received_like_count(session: AsyncSession, tg_id: int) -> int:
    stmt = select(func.count(UserLike.id)).where(
        UserLike.liked_id == tg_id,
        UserLike.is_pass  == False
    )
    result = await session.execute(stmt)
    return result.scalar() or 0


async def add_friend(session: AsyncSession, user_id: int, friend_id: int) -> bool:
    from sqlalchemy.exc import IntegrityError
    try:
        session.add(FriendList(user_id=user_id, friend_id=friend_id))
        await session.flush()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def transfer_coins(session: AsyncSession, from_tg_id: int, to_tg_id: int, amount: int) -> tuple[bool, str]:
    if amount <= 0:
        return False, "مقدار انتقال باید بیشتر از صفر باشد."
    sender   = await get_user_by_tg_id(session, from_tg_id)
    receiver = await get_user_by_tg_id(session, to_tg_id)
    if not sender:
        return False, "حساب فرستنده یافت نشد."
    if not receiver:
        return False, "حساب گیرنده یافت نشد."
    if sender.coin_balance < amount:
        return False, f"موجودی کافی نیست. موجودی فعلی: {sender.coin_balance} سکه."
    await process_coin_transaction(session, sender, -amount, f"انتقال سکه به کاربر {to_tg_id}", ignore_multiplier=True)
    await process_coin_transaction(session, receiver, +amount, f"دریافت سکه از کاربر {from_tg_id}", ignore_multiplier=True)
    return True, f"✅ {amount} سکه با موفقیت منتقل شد."


async def find_interest_match_candidates(
    session:              AsyncSession,
    caller_tg_id:         int,
    caller_interests_str: str,
    target_gender:        Optional[str] = None,
    limit:                int = 20
) -> List[User]:
    if not caller_interests_str:
        return []
    interests_list = [i.strip() for i in caller_interests_str.split(",") if i.strip()]
    if not interests_list:
        return []

    blocked_by_caller  = (
        select(BlockList.blocked_id)
        .where(BlockList.blocker_id == caller_tg_id)
        .scalar_subquery()
    )
    blockers_of_caller = (
        select(BlockList.blocker_id)
        .where(BlockList.blocked_id == caller_tg_id)
        .scalar_subquery()
    )

    conditions = [
        User.tg_id != caller_tg_id,
        User.completed_registration == True,
        User.is_banned              == False,
        User.invisible_mode         == False,
        User.tg_id.not_in(blocked_by_caller),
        User.tg_id.not_in(blockers_of_caller),
        or_(*[User.interests.like(f"%{i}%") for i in interests_list])
    ]
    if target_gender:
        conditions.append(func.lower(User.gender) == target_gender.lower())

    stmt   = select(User).where(*conditions).order_by(func.rand()).limit(limit)
    result = await session.execute(stmt)
    candidates = list(result.scalars().all())

    caller_set = set(interests_list)

    def _shared_count(u: User) -> int:
        if not u.interests:
            return 0
        return len(set(u.interests.split(",")).intersection(caller_set))

    candidates.sort(key=_shared_count, reverse=True)
    return candidates


async def get_filtered_discovery_candidates(
    session:    AsyncSession,
    caller_tg_id: int,
    province:   Optional[str]       = None,
    interests:  Optional[List[str]] = None,
    min_age:    int = 0,
    max_age:    int = 99,
    limit:      int = 10
) -> List[User]:
    blocked_by_caller  = (
        select(BlockList.blocked_id)
        .where(BlockList.blocker_id == caller_tg_id)
        .scalar_subquery()
    )
    blockers_of_caller = (
        select(BlockList.blocker_id)
        .where(BlockList.blocked_id == caller_tg_id)
        .scalar_subquery()
    )

    conditions = [
        User.tg_id != caller_tg_id,
        User.completed_registration == True,
        User.is_banned              == False,
        User.invisible_mode         == False,
        User.tg_id.not_in(blocked_by_caller),
        User.tg_id.not_in(blockers_of_caller),
    ]
    if province:
        conditions.append(User.province == province)
    if min_age > 0:
        conditions.append(User.age >= min_age)
    if max_age < 99:
        conditions.append(User.age <= max_age)
    if interests:
        conditions.append(or_(*[User.interests.like(f"%{i}%") for i in interests]))

    stmt   = select(User).where(*conditions).order_by(func.rand()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_user_friends(session: AsyncSession, tg_id: int) -> List[User]:
    stmt = (
        select(User)
        .join(FriendList, FriendList.friend_id == User.tg_id)
        .where(FriendList.user_id == tg_id)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())

async def add_xp_to_user(session: AsyncSession, tg_id: int, amount: int) -> bool:
    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        return False
        
    user.xp_points += amount
    next_level_xp = user.level * 100 
    
    if user.xp_points >= next_level_xp:
        user.level += 1
        user.lootbox_count += 1 
        user.xp_points -= next_level_xp 
        
        await session.flush()
        return True 
        
    await session.flush()
    return False