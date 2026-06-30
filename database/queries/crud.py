import logging
import string
import random
import math
from typing import Optional, List
from datetime import datetime, timezone

from sqlalchemy import select, and_, or_, func, update, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.mysql import insert

from matching_bot_project.bot.core.loader import redis_client
from matching_bot_project.database.models.models import (
    User, MatchHistory, Question, UserAnswer,
    CoinTransaction, FriendList, BlockList, UserLike, UserReport,
    CoinPackage, CoinPurchaseOrder, ProfileComment
)
from sqlalchemy.orm import selectinload

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
    final_amount = amount

    if amount < 0:
        deduction = abs(amount)
        # Atomic update to prevent race conditions on spend/transfer
        stmt = (
            update(User)
            .where(and_(User.tg_id == user.tg_id, User.coin_balance >= deduction))
            .values(
                coin_balance=User.coin_balance - deduction,
                total_spent_coins=User.total_spent_coins + deduction
            )
        )
        
        # غیرفعال کردن همگام‌سازی خودکار SQLAlchemy
        result = await session.execute(stmt, execution_options={"synchronize_session": False})
        
        if result.rowcount == 0:
            return False # Insufficient funds or user not found
            
        # آپدیت آبجکت در حافظه
        user.coin_balance -= deduction
        user.total_spent_coins += deduction

    else:
        # اعمال ضریب ایونت فقط برای واریزی‌ها
        if not ignore_multiplier:
            try:
                active_multiplier_str = await redis_client.get("bot:active_event_multiplier")
                if active_multiplier_str:
                    multiplier = float(active_multiplier_str)
                    final_amount = int(final_amount * multiplier)
                    if multiplier > 1.0:
                        description += f" (ضریب رویداد ×{multiplier})"
            except Exception as e:
                logger.error(f"Error fetching event multiplier from Redis: {e}")
                
        # ⭐ آپدیت اتمیک برای واریز سکه (جلوگیری از Lost Update در ترافیک بالا)
        stmt = (
            update(User)
            .where(User.tg_id == user.tg_id)
            .values(
                coin_balance=User.coin_balance + final_amount,
                total_earned_coins=User.total_earned_coins + final_amount
            )
        )
        await session.execute(stmt, execution_options={"synchronize_session": False})
        
        # آپدیت آبجکت در حافظه
        user.coin_balance += final_amount
        user.total_earned_coins += final_amount
        
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
        # ✅ فرم صحیح:
        stmt = select(User).where(User.tg_id == user.referrer_id)
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
    return res.scalars().first()


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
    """Creates a new report record for a user and handles auto-banning."""
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
        
        # بن خودکار اکانت در صورتی که تعداد گزارش‌ها به حد نصاب (مثلاً ۵) برسد
        if reported_user.report_count >= 5:
            reported_user.is_banned = True
            logger.info(f"User {reported_id} has been auto-banned due to reaching {reported_user.report_count} reports.")

    await session.flush()
    return report_record



async def save_like(session: AsyncSession, liker_id: int, liked_id: int, is_pass: bool) -> UserLike:
    """Saves a like or pass interaction into the database and updates likes_count."""
    
    stmt = insert(UserLike).values(
        liker_id=liker_id, 
        liked_id=liked_id, 
        is_pass=is_pass
    ).on_duplicate_key_update(
        is_pass=is_pass
    )
    result = await session.execute(stmt)
    
    # فقط زمانی که رکورد جدیدی واقعاً INSERT شده (rowcount == 1) شمارنده را ببر بالا
    if not is_pass and result.rowcount == 1:
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
    # FIXED: Unified gender opposite mapping
    _GENDER_OPPOSITE = {
        "male": "female",
        "female": "male",
        "boy": "girl",
        "girl": "boy",
    }
    target_gender = _GENDER_OPPOSITE.get(current_user_gender.lower(), "female")

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
            func.lower(User.gender) == target_gender, # FIXED: Compare against lowercase dictionary result
            User.completed_registration == True,
            User.invisible_mode.is_(False),           # FIXED: Prevented the getattr() bug here too
            ~acted_by_me_exists,
            ~blocked_by_me_exists,
            ~blocked_me_exists
        )
    ).order_by(
        priority_expr.desc(),
        User.last_active.desc() 
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


async def seed_question_bank_if_empty(session: AsyncSession):
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
        
    await session.flush()
    logger.info("Successfully seeded 80 questions into MySQL database Questions schema.")


async def get_question_count(session: AsyncSession) -> int:
    """تعداد کل سوالات موجود در بانک سوالات"""
    result = await session.execute(select(func.count()).select_from(Question))
    return result.scalar() or 0


async def add_question(
    session: AsyncSession,
    question_text: str,
    option_a: str,
    option_b: str,
    category: str,
    option_c: Optional[str] = None,
    option_d: Optional[str] = None,
) -> Question:
    """
    اضافه کردن یک سوال جدید به بانک سوالات.
    سوالات ۲ گزینه‌ای: option_c و option_d خالی می‌مونن.
    سوالات ۴ گزینه‌ای: همه چهار گزینه پر می‌شن.
    """
    q = Question(
        question_text=question_text,
        option_a=option_a,
        option_b=option_b,
        option_c=option_c,
        option_d=option_d,
        category=category,
    )
    session.add(q)
    await session.flush()
    return q


async def get_referral_count(session: AsyncSession, tg_id: int) -> int:
    stmt = select(func.count(User.id)).where(User.referrer_id == tg_id)
    result = await session.execute(stmt)
    return result.scalar() or 0


async def get_nearby_candidates(
    session: AsyncSession, 
    current_user: User, 
    gender_filter: Optional[str] = None, 
    limit: int = 5
) -> List[User]:
    """
    کاربران نزدیک هم‌شهری را با احتساب فیلتر جنسیت عودت می‌دهد.
    اگر gender_filter برابر با male یا female باشد اعمال می‌شود، 
    و اگر none یا both باشد فیلتر جنسیتی اعمال نخواهد شد.
    """
    from sqlalchemy import and_, func

    # شروط پایه‌ای و عمومی برای یافتن کاربران معتبر نزدیک
    conditions = [
        User.tg_id != current_user.tg_id,
        User.completed_registration == True,
        User.province == current_user.province,
        User.city == current_user.city,
        User.invisible_mode == False,
        User.is_banned == False
    ]

    # اعمال فیلتر جنسیت به صورت هوشمند و داینامیک
    if gender_filter:
        gender_lower = gender_filter.lower()
        if gender_lower == "male":
            conditions.append(func.lower(User.gender) == "male")
        elif gender_lower == "female":
            conditions.append(func.lower(User.gender) == "female")

    stmt = (
        select(User)
        .where(and_(*conditions))
        .order_by(User.last_active.desc())
        .limit(limit)
    )
    
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
        # استفاده از تراکنش تودرتو (Savepoint) برای جلوگیری از خراب شدن سایر کوئری‌ها
        async with session.begin_nested():
            session.add(FriendList(user_id=user_id, friend_id=friend_id))
        return True
    except IntegrityError:
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
        
    # کسر سکه از فرستنده (چک کردن خروجی تابع برای جلوگیری از باگ رایس کاندیشن)
    success = await process_coin_transaction(session, sender, -amount, f"انتقال سکه به کاربر {to_tg_id}", ignore_multiplier=True)
    
    if not success:
        return False, "تراکنش ناموفق بود (احتمالاً موجودی در لحظه کافی نبوده است)."
        
    # واریز سکه به گیرنده (فقط در صورتی که کسر موفقیت‌آمیز بوده)
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

    # گرفتن استخر بزرگتر (مثلا 100 نفر اول) برای مرتب‌سازی دقیق در پایتون
    stmt   = select(User).where(*conditions).order_by(User.last_active.desc()).limit(100)
    result = await session.execute(stmt)
    candidates = list(result.scalars().all())

    caller_set = set(interests_list)

    def _shared_count(u: User) -> int:
        if not u.interests:
            return 0
        return len(set(u.interests.split(",")).intersection(caller_set))

    candidates.sort(key=_shared_count, reverse=True)
    
    # برگرداندن فقط به اندازه درخواستی (limit)
    return candidates[:limit]

    def _shared_count(u: User) -> int:
        if not u.interests:
            return 0
        return len(set(u.interests.split(",")).intersection(caller_set))

    candidates.sort(key=_shared_count, reverse=True)
    return candidates


def _score_discovery_candidate(
    candidate:          User,
    caller_interests:   set,
    caller_province:    Optional[str],
    caller_city:        Optional[str],
    caller_lat:         Optional[float],
    caller_lng:         Optional[float],
    same_province_bonus: bool,
) -> float:
    """
    امتیازدهی ترکیبی به یک کاندیدا برای رتبه‌بندی نتایج جستجو (به‌جای فیلتر خام).
    خروجی هرچه بالاتر، تطابق بهتر. این تابع خالص و بدون I/O است تا راحت قابل تست باشد.

    وزن‌ها:
      - علایق مشترک (Jaccard)......... 0 تا 45 امتیاز   [مهم‌ترین فاکتور]
      - فعالیت اخیر (last_active)..... 0 تا 20 امتیاز
      - فاصله جغرافیایی (در صورت وجود) 0 تا 20 امتیاز
      - هم‌شهری / هم‌استانی بودن....... 0 تا 10 امتیاز
      - اعتبار پروفایل (trust_score)... 0 تا 5 امتیاز
    """
    score = 0.0

    # --- ۱) علایق مشترک (Jaccard similarity) ---
    if caller_interests and candidate.interests:
        cand_interests = {i.strip() for i in candidate.interests.split(",") if i.strip()}
        if cand_interests:
            shared = caller_interests & cand_interests
            union  = caller_interests | cand_interests
            jaccard = (len(shared) / len(union)) if union else 0.0
            score += jaccard * 45.0
            # پاداش کوچک اضافه برای هر علاقه مشترک (جدا از نسبت)، تا کاربرانی با
            # تعداد علاقه مشترک بیشتر (حتی با لیست‌های نامتقارن) عقب نیفتند
            score += min(len(shared), 5) * 2.0

    # --- ۲) فعالیت اخیر ---
    if candidate.last_active:
        # توجه: به‌خاطر ناهماهنگی شناخته‌شده در ذخیره‌سازی timezone بین مدل‌های پروژه
        # (برخی ستون‌ها naive و برخی aware هستند)، اینجا محافظه‌کارانه با مقدار naive
        # مقایسه می‌کنیم تا با خطای "can't subtract offset-naive and offset-aware
        # datetimes" کرش نکند. اگر در آینده تمام ستون‌های datetime به‌صورت
        # یکدست aware (UTC) ذخیره شوند، این بخش باید به‌روزرسانی شود.
        last_active = candidate.last_active
        if last_active.tzinfo is not None:
            last_active = last_active.replace(tzinfo=None)
        now = datetime.utcnow()
        hours_inactive = max((now - last_active).total_seconds() / 3600.0, 0.0)
        if hours_inactive <= 1:
            score += 20.0
        elif hours_inactive <= 24:
            score += 15.0
        elif hours_inactive <= 24 * 7:
            score += 8.0
        elif hours_inactive <= 24 * 30:
            score += 3.0

    # --- ۳) فاصله جغرافیایی (در صورت وجود مختصات از هر دو طرف) ---
    if (
        caller_lat is not None and caller_lng is not None
        and candidate.location_lat is not None and candidate.location_lng is not None
    ):
        dist_km = calculate_distance_km(caller_lat, caller_lng, candidate.location_lat, candidate.location_lng)
        if dist_km <= 5:
            score += 20.0
        elif dist_km <= 20:
            score += 14.0
        elif dist_km <= 50:
            score += 8.0
        elif dist_km <= 150:
            score += 3.0
    elif same_province_bonus and caller_province and candidate.province == caller_province:
        # اگر مختصات نداریم ولی هم‌استان هستند، جایگزین تقریبی برای فاصله
        score += 10.0
        if caller_city and candidate.city == caller_city:
            score += 6.0

    # --- ۴) اعتبار پروفایل ---
    trust = getattr(candidate, "trust_score", None)
    if trust:
        score += min(max(trust, 0), 100) / 100.0 * 5.0

    return round(score, 3)


async def get_filtered_discovery_candidates(
    session:        AsyncSession,
    caller_tg_id:   int,
    province:       Optional[str]       = None,
    interests:       Optional[List[str]] = None,
    min_age:         int = 0,
    max_age:         int = 99,
    exclude_ids:     Optional[List[int]] = None,
    limit:           int = 10,
    pool_size:       int = 150,
) -> List[User]:
    """
    جستجوی پیشرفته و رتبه‌بندی‌شده کاربران برای ویزارد فیلتر.

    تفاوت کلیدی نسبت به نسخه قبلی: به‌جای فقط فیلتر کردن و گرفتن آخرین افراد فعال،
    یک استخر بزرگ‌تر از کاندیداهای واجد شرط (`pool_size`) را از دیتابیس می‌گیرد و سپس
    آن‌ها را بر اساس ترکیب «علایق مشترک + فعالیت اخیر + نزدیکی جغرافیایی/استانی +
    اعتبار پروفایل» امتیازدهی و رتبه‌بندی می‌کند. استان همچنان به‌عنوان فیلتر سخت
    اعمال می‌شود (چون کاربر صریحاً انتخابش کرده)، اما علایق دیگر یک فیلتر سخت
    (AND/OR روی LIKE) نیست — بلکه یک سیگنال امتیازی است، بنابراین کاربرانی با
    تطابق نسبی هم در نتایج می‌مانند و در رتبه مناسب خودشان نمایش داده می‌شوند.

    exclude_ids: لیست آیدی‌هایی که کاربر همین حالا دیده (معمولاً از Redis Set
    دیده‌شده‌ها) تا نتایج تکراری در صفحه بعدی نمایش داده نشوند.
    """
    caller = await get_user_by_tg_id(session, caller_tg_id)
    if not caller:
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
    ]
    if province:
        conditions.append(User.province == province)
    if min_age > 0:
        conditions.append(User.age >= min_age)
    if max_age < 99:
        conditions.append(User.age <= max_age)
    if exclude_ids:
        conditions.append(User.tg_id.not_in(exclude_ids))

    # علایق دیگر شرط سخت WHERE نیست؛ صرفاً برای رتبه‌بندی در پایتون استفاده می‌شود
    # تا نتایج نزدیک ولی ناقص هم به کاربر نشان داده شوند.

    # استخر بزرگ‌تری از last_active نسبتاً تازه می‌گیریم تا بعد در پایتون رتبه‌بندی شود.
    stmt = (
        select(User)
        .where(*conditions)
        .order_by(User.last_active.desc())
        .limit(pool_size)
    )
    result = await session.execute(stmt)
    pool = list(result.scalars().all())

    if not pool:
        return []

    caller_interests = (
        {i.strip() for i in caller.interests.split(",") if i.strip()}
        if caller.interests else set()
    )
    interest_filter = {i.strip() for i in interests if i.strip()} if interests else set()

    scored: List[tuple] = []
    for cand in pool:
        # اگر کاربر علایق خاصی را در ویزارد انتخاب کرده، حداقل یک تطابق لازم است
        # وگرنه نتایج کاملاً نامرتبط هم نمایش داده می‌شوند. اما این هم‌چنان لیبرال‌تر
        # از حالت قبلی است چون باقی رتبه‌بندی روی این فیلتر اولیه اعمال می‌شود.
        if interest_filter:
            cand_interests = {i.strip() for i in cand.interests.split(",")} if cand.interests else set()
            if not (interest_filter & cand_interests):
                continue

        effective_caller_interests = interest_filter or caller_interests
        score = _score_discovery_candidate(
            candidate=cand,
            caller_interests=effective_caller_interests,
            caller_province=caller.province,
            caller_city=caller.city,
            caller_lat=caller.location_lat,
            caller_lng=caller.location_lng,
            same_province_bonus=not bool(province),  # اگه استان فیلتر سخت بود، این بونوس بی‌اثره
        )
        scored.append((score, cand))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [cand for _, cand in scored[:limit]]


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

async def remove_friend(session: AsyncSession, user_id: int, friend_id: int) -> bool:
    """حذف یک کاربر از لیست دوستان"""
    from sqlalchemy import delete, and_
    
    stmt = delete(FriendList).where(
        and_(
            FriendList.user_id == user_id, 
            FriendList.friend_id == friend_id
        )
    )
    result = await session.execute(stmt)
    await session.flush()

    return result.rowcount > 0

async def is_friend(session: AsyncSession, user_id: int, friend_id: int) -> bool:
    """بررسی اینکه آیا کاربر هدف در لیست دوستان قرار دارد یا خیر"""
    from sqlalchemy import select, and_
    
    stmt = select(1).where(
        and_(
            FriendList.user_id == user_id, 
            FriendList.friend_id == friend_id
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def get_active_coin_packages(session: AsyncSession) -> List[CoinPackage]:
    stmt = select(CoinPackage).where(CoinPackage.is_active == True).order_by(CoinPackage.coin_amount.asc())
    res = await session.execute(stmt)
    return list(res.scalars().all())

async def get_all_coin_packages(session: AsyncSession) -> List[CoinPackage]:
    stmt = select(CoinPackage).order_by(CoinPackage.coin_amount.asc())
    res = await session.execute(stmt)
    return list(res.scalars().all())

async def create_coin_package(session: AsyncSession, coin_amount: int, price_toman: int) -> CoinPackage:
    package = CoinPackage(coin_amount=coin_amount, price_toman=price_toman)
    session.add(package)
    await session.flush()
    return package

async def update_coin_package_price(session: AsyncSession, package_id: int, new_price_toman: int) -> bool:
    package = await session.get(CoinPackage, package_id)
    if not package:
        return False
    package.price_toman = new_price_toman
    await session.flush()
    return True

async def toggle_coin_package(session: AsyncSession, package_id: int) -> Optional[bool]:
    package = await session.get(CoinPackage, package_id)
    if not package:
        return None
    package.is_active = not package.is_active
    await session.flush()
    return package.is_active

async def create_purchase_order(
    session: AsyncSession, 
    user_tg_id: int, 
    package_id: int, 
    payment_method: str, 
    receipt_photo_file_id: Optional[str] = None
) -> CoinPurchaseOrder:
    order = CoinPurchaseOrder(
        user_tg_id=user_tg_id,
        package_id=package_id,
        payment_method=payment_method,
        receipt_photo_file_id=receipt_photo_file_id
    )
    session.add(order)
    await session.flush()
    return order
    
async def get_purchase_order(session: AsyncSession, order_id: int) -> Optional[CoinPurchaseOrder]:
    return await session.get(CoinPurchaseOrder, order_id)

def calculate_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """محاسبه فاصله جغرافیایی بین دو نقطه بر حسب کیلومتر (فرمول Haversine)"""
    R = 6371.0 # شعاع زمین
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 1)



# ══════════════════════════════════════════════════════════════
# توابع سیستم کامنت پروفایل
# ══════════════════════════════════════════════════════════════

_COMMENTS_PER_PAGE = 3


async def is_blocked(session: AsyncSession, blocker_id: int, blocked_id: int) -> bool:
    """
    آیا blocker_id کاربر blocked_id را مسدود کرده است؟
    استفاده می‌شود برای جلوگیری از ثبت کامنت توسط کاربری که قبلاً
    توسط صاحب پروفایل (target) بلاک شده.
    """
    result = await session.execute(
        select(BlockList.id).where(
            and_(
                BlockList.blocker_id == blocker_id,
                BlockList.blocked_id == blocked_id,
            )
        )
    )
    return result.scalar_one_or_none() is not None


async def are_comments_disabled(session: AsyncSession, target_tg_id: int) -> bool:
    """آیا صاحب پروفایل (target_tg_id) کلاً امکان کامنت‌گذاری را بسته است؟"""
    result = await session.execute(
        select(User.comments_disabled).where(User.tg_id == target_tg_id)
    )
    value = result.scalar_one_or_none()
    return bool(value)


async def toggle_comments_disabled(session: AsyncSession, tg_id: int) -> Optional[bool]:
    """
    وضعیت فعلی comments_disabled کاربر را برعکس می‌کند و مقدار جدید را برمی‌گرداند.
    اگه کاربر پیدا نشه None برمی‌گردونه.
    """
    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        return None
    user.comments_disabled = not user.comments_disabled
    await session.flush()
    return user.comments_disabled


async def upsert_profile_comment(
    session: AsyncSession,
    author_tg_id: int,
    target_tg_id: int,
    text: str,
) -> ProfileComment:
    """
    اگه کاربر قبلاً کامنت گذاشته → ویرایش می‌کنه.
    اگه نه → کامنت جدید می‌سازه.
    """
    stmt = select(ProfileComment).where(
        and_(
            ProfileComment.author_tg_id == author_tg_id,
            ProfileComment.target_tg_id == target_tg_id,
        )
    )
    result = await session.execute(stmt)
    comment = result.scalar_one_or_none()

    if comment:
        comment.text = text
        comment.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        comment = ProfileComment(
            author_tg_id=author_tg_id,
            target_tg_id=target_tg_id,
            text=text,
        )
        session.add(comment)

    await session.flush()
    return comment


async def get_profile_comments(
    session: AsyncSession,
    target_tg_id: int,
    page: int = 0,
) -> tuple[list[ProfileComment], int]:
    """
    کامنت‌های یک پروفایل با pagination.
    برمی‌گردونه: (لیست کامنت‌ها، تعداد کل)
    """
    count_stmt = select(func.count()).where(
        ProfileComment.target_tg_id == target_tg_id
    )
    total = (await session.execute(count_stmt)).scalar() or 0

    stmt = (
        select(ProfileComment)
        .options(selectinload(ProfileComment.author))
        .where(ProfileComment.target_tg_id == target_tg_id)
        .order_by(ProfileComment.created_at.desc())
        .offset(page * _COMMENTS_PER_PAGE)
        .limit(_COMMENTS_PER_PAGE)
    )
    result = await session.execute(stmt)
    comments = list(result.scalars().all())

    return comments, total


async def get_comment_by_id(
    session: AsyncSession,
    comment_id: int,
) -> Optional[ProfileComment]:
    return await session.get(ProfileComment, comment_id)


async def get_my_comment_on_profile(
    session: AsyncSession,
    author_tg_id: int,
    target_tg_id: int,
) -> Optional[ProfileComment]:
    """کامنت فعلی این کاربر روی این پروفایل (اگه وجود داشته باشه)"""
    result = await session.execute(
        select(ProfileComment).where(
            and_(
                ProfileComment.author_tg_id == author_tg_id,
                ProfileComment.target_tg_id == target_tg_id,
            )
        )
    )
    return result.scalar_one_or_none()


async def delete_profile_comment(
    session: AsyncSession,
    comment_id: int,
    requester_tg_id: int,
) -> bool:
    """
    حذف کامنت — فقط اگه requester صاحب پروفایل (target) یا نویسنده خودش باشه.
    """
    comment = await session.get(ProfileComment, comment_id)
    if not comment:
        return False
    if requester_tg_id not in (comment.target_tg_id, comment.author_tg_id):
        return False

    await session.delete(comment)
    await session.flush()
    return True