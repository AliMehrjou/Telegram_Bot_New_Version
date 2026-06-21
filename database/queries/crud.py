import logging
from typing import Optional, List
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from matching_bot_project.database.models.models import (
    User, MatchHistory, Question, UserAnswer, CoinTransaction, FriendList, BlockList, UserLike, UserReport
)

logger = logging.getLogger(__name__)


async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> Optional[User]:
    stmt = select(User).where(User.tg_id == tg_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def process_coin_transaction(
    session: AsyncSession, 
    user: User, 
    amount: int, 
    description: str
) -> bool:
    """Safely processes coin addition/deduction and logs the transaction."""
    if amount < 0 and user.coin_balance < abs(amount):
        return False # Insufficient funds
        
    user.coin_balance += amount
    if amount > 0:
        user.total_earned_coins += amount
    else:
        user.total_spent_coins += abs(amount)
        
    transaction = CoinTransaction(
        user_id=user.tg_id,
        amount=amount,
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
) -> bool:
    """Completes profile, rewards 5 coins, and handles referral rewards."""
    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        return False
        
    user.gender = gender
    user.age = age
    user.province = province
    user.city = city
    user.tags = tags
    user.profile_photo_file_id = profile_photo_file_id
    user.completed_registration = True
    
    # Reward for completing profile (+5 Coins)
    await process_coin_transaction(session, user, 5, "تکمیل اطلاعات پروفایل")
    
    # Process Referral Reward
    if user.referrer_id:
        referrer = await get_user_by_tg_id(session, user.referrer_id)
        if referrer:
            await process_coin_transaction(session, referrer, 5, f"پاداش دعوت کاربر {tg_id}")
            logger.info(f"Referral Success: User {tg_id} completed onboarding. Referrer {referrer.tg_id} awarded 5 coins.")
            
    await session.flush()
    return True

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
    # We can use order_by(func.rand()) in MySQL or equivalent, or fetch a sample
    # Here, using standard SQLAlchemy order limit. In production, order_by(func.random()) or MySQL order_by(func.rand()) is standard.
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
    """Saves a like or pass interaction into the database."""
    from sqlalchemy.dialects.mysql import insert
    from sqlalchemy import select, and_

    # Use MySQL's ON DUPLICATE KEY UPDATE to prevent throwing an IntegrityError 
    # which would otherwise wipe the current transaction state.
    stmt = insert(UserLike).values(
        liker_id=liker_id, 
        liked_id=liked_id, 
        is_pass=is_pass
    ).on_duplicate_key_update(
        is_pass=is_pass
    )
    
    await session.execute(stmt)
    await session.flush()
    
    # Fetch and return the updated/inserted record to maintain the original return type
    fetch_stmt = select(UserLike).where(
        and_(UserLike.liker_id == liker_id, UserLike.liked_id == liked_id)
    )
    res = await session.execute(fetch_stmt)
    return res.scalar_one_or_none()


async def get_discovery_candidate(session: AsyncSession, current_user_id: int, current_user_gender: str) -> Optional[User]:
    """
    Fetches the next random profile for discovery based on strict filtering:
    - Opposite gender
    - Not self
    - completed_registration = True
    - Not in BlockList (mutual)
    - Not already liked/passed by the current user
    - Not invisible mode
    - Orders by (Target Liked Caller DESC), RAND()
    """
    target_gender = "Female" if current_user_gender.lower() == "male" else "Male"
    if current_user_gender.lower() == "boy": target_gender = "girl"
    if current_user_gender.lower() == "girl": target_gender = "boy"

    from sqlalchemy import select, case, func, and_, exists

    # Exists Subquery: Users who have already liked the current user
    liked_me_exists = select(1).where(
        and_(
            UserLike.liker_id == User.tg_id, 
            UserLike.liked_id == current_user_id, 
            UserLike.is_pass == False
        )
    ).correlate(User).exists()

    # Exists Subquery: Users the current user has already acted upon
    acted_by_me_exists = select(1).where(
        and_(
            UserLike.liker_id == current_user_id, 
            UserLike.liked_id == User.tg_id
        )
    ).correlate(User).exists()

    # Exists Subquery: Users who blocked the current user
    blocked_me_exists = select(1).where(
        and_(
            BlockList.blocker_id == User.tg_id, 
            BlockList.blocked_id == current_user_id
        )
    ).correlate(User).exists()

    # Exists Subquery: Users the current user blocked
    blocked_by_me_exists = select(1).where(
        and_(
            BlockList.blocker_id == current_user_id, 
            BlockList.blocked_id == User.tg_id
        )
    ).correlate(User).exists()

    # Priority condition: If target user is IN the list of users who liked the current user, priority = 1, else 0
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
    """Saves answer option choice atomically and checks for synchronization rules."""
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
    """Gets all answers for a specific questionnaire query inside an active date session."""
    stmt = select(UserAnswer).where(
        and_(
            UserAnswer.match_history_id == match_history_id,
            UserAnswer.question_id == question_id
        )
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def seed_sixty_question_bank_if_empty(session: AsyncSession):
    """Ensures 60 production questionnaire models exist inside the table database."""
    stmt = select(Question).limit(1)
    res = await session.execute(stmt)
    if res.scalar_one_or_none():
        return # Already seeded
        
    questions_data = [
        # Relationship Preferences
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
    
    # Add dummy/real lines to reach 60 items so user gets exact rich schema seeded
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
    """کاربران جنس مخالف که در همون استان و شهر کاربر هستن رو پیدا میکنه"""
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
