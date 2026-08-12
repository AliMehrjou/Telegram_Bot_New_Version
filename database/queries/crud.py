import logging
import string
import random
import math
from typing import Optional, List
from datetime import datetime, timezone, timedelta
import math
from matching_bot_project.database.models.models import GiftType
from sqlalchemy import select, func
from sqlalchemy import select, and_, or_, func, update, case, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import selectinload
from sqlalchemy import update, func
from sqlalchemy.ext.asyncio import AsyncSession
from matching_bot_project.database.models.models import User
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from matching_bot_project.database.models.models import User, BlockList
from sqlalchemy import select, and_, or_, case, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from matching_bot_project.database.models.models import User, UserLike, BlockList, MatchHistory
from matching_bot_project.bot.core.loader import redis_client
from matching_bot_project.database.models.models import (
    User, MatchHistory, Question, UserAnswer,
    CoinTransaction, FriendList, BlockList, UserLike, UserReport,
    CoinPackage, CoinPurchaseOrder, ProfileComment, DeletedAccount,
    generate_random_public_id
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from matching_bot_project.bot.core.loader import dp, bot, redis_client
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
from matching_bot_project.bot.core.bot_shard_manager import shard_manager
from matching_bot_project.services.cache import cache
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# User Lookup
# ══════════════════════════════════════════════════════════════

async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> Optional[User]:
    stmt = select(User).where(User.tg_id == tg_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_public_id(session: AsyncSession, public_id: str) -> Optional[User]:
    stmt = select(User).where(User.public_id == public_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ══════════════════════════════════════════════════════════════
# Coin / Economy
# ══════════════════════════════════════════════════════════════

async def process_coin_transaction(
    session: AsyncSession,
    user: User,
    amount: int,
    description: str,
    ignore_multiplier: bool = False,
    reference_id: Optional[int] = None,  # ⭐ NEW PARAMETER
) -> bool:
    """Safely processes coin addition/deduction and logs the transaction.
    Automatically applies active event multipliers for positive amounts unless ignored.
    
    Args:
        reference_id: Optional ID linking this transaction to an order, match, or admin action.
    """
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
        description=description,
        reference_id=reference_id  # ⭐ STORE SYSTEMATICALLY
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
    from sqlalchemy.exc import IntegrityError
    from matching_bot_project.bot.core.bot_shard_manager import shard_manager # اضافه شده

    # محاسبه شارد برای کاربر جدید در لحظه ساخت
    initial_shard = shard_manager.get_shard_index_for_user(tg_id)

    max_attempts = 5
    user = None
    for attempt in range(max_attempts):
        user = User(
            tg_id=tg_id,
            first_name=first_name,
            username=username,
            referrer_id=referrer_id,
            completed_registration=False,
            coin_balance=3,
            total_earned_coins=3,
            public_id=generate_random_public_id(),
            shard_index=initial_shard,  # 👈 ثبت قطعی شارد محاسبه‌شده
        )
        session.add(user)
        try:
            async with session.begin_nested():
                await session.flush()
            break
        except IntegrityError as e:
            if "public_id" in str(e).lower() or "uq_users_public_id" in str(e).lower():
                logger.warning(
                    "public_id collision on create_user attempt %d/%d for tg_id %s — retrying",
                    attempt + 1, max_attempts, tg_id,
                )
                session.expunge(user)
                continue
            raise

    if user is None or user.id is None:
        raise RuntimeError(f"Failed to create user {tg_id} after {max_attempts} public_id collision retries")

    start_tx = CoinTransaction(user_id=tg_id, amount=3, description="هدیه عضویت اولیه")
    session.add(start_tx)

    return user


# ══════════════════════════════════════════════════════════════
# Deleted Accounts
# ══════════════════════════════════════════════════════════════

async def mark_account_deleted(session: AsyncSession, tg_id: int) -> None:
    """Logs a Telegram ID into the deleted accounts ledger if not already present."""
    stmt = select(DeletedAccount).where(DeletedAccount.tg_id == tg_id)
    result = await session.execute(stmt)
    if not result.scalar_one_or_none():
        session.add(DeletedAccount(tg_id=tg_id))

async def has_deleted_account(session: AsyncSession, tg_id: int) -> bool:
    """Checks if a user has ever deleted an account."""
    stmt = select(DeletedAccount).where(DeletedAccount.tg_id == tg_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


# ══════════════════════════════════════════════════════════════
# Registration & Referral
# ══════════════════════════════════════════════════════════════

async def complete_user_registration(
    session: AsyncSession, 
    tg_id: int, 
    gender: str, 
    age: int, 
    province: str,
    city: str,
    first_name: str = None,
    tags: str = None,
    profile_photo_file_id: str = None
) -> dict:
    """
    Completes profile, rewards coins, and handles referral rewards strictly 
    within an atomic transaction, preventing returning users from exploiting referrals.
    """
    # 1. Lock the user row atomically to prevent double-request race conditions
    stmt = select(User).where(User.tg_id == tg_id).with_for_update()
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    
    # Block if user doesn't exist or already finished registration
    if not user or user.completed_registration:
        return {"success": False, "referrer_tg_id": None}
        
    if first_name:              
        user.first_name = first_name

    
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
    
    # 2. Check historical deletions to block infinite referral exploits
    is_returning_user = await has_deleted_account(session, tg_id)
    
    # 3. Process Referral Reward
    if user.referrer_id:
        if not is_returning_user:
            # Lock the referrer row atomically
            stmt_referrer = select(User).where(User.tg_id == user.referrer_id).with_for_update()
            result_referrer = await session.execute(stmt_referrer)
            referrer = result_referrer.scalar_one_or_none()
            
            if referrer:
                await process_coin_transaction(session, referrer, 5, f"پاداش دعوت کاربر {tg_id}")
                await process_coin_transaction(session, user, 5, "پاداش ورود از طریق لینک دعوت")
                
                referrer_tg_id = referrer.tg_id
                logger.info(f"Referral Success: User {tg_id} completed onboarding. Referrer {referrer.tg_id} awarded.")
        else:
            logger.warning(f"Referral Exploit Blocked: TG ID {tg_id} used a referral link but has a deleted account history.")

    await session.flush()
    return {"success": True, "referrer_tg_id": referrer_tg_id}


# ══════════════════════════════════════════════════════════════
# Match History
# ══════════════════════════════════════════════════════════════

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


async def is_active_match_partner(session: AsyncSession, viewer_id: int, target_id: int) -> bool:
    """
    بررسی می‌کند آیا `target_id` همین الان پارتنر چت/دیت فعال `viewer_id` است یا نه.
    """
    if viewer_id == target_id:
        return False
    active_match = await get_active_match(session, viewer_id)
    if not active_match:
        return False
    return target_id in (active_match.user_one_id, active_match.user_two_id)


# ══════════════════════════════════════════════════════════════
# Questions
# ══════════════════════════════════════════════════════════════

async def get_random_questions(session: AsyncSession, limit: int = 20) -> List[Question]:
    """Retrieves random questions from the 60-question database bank."""
    stmt = select(Question).order_by(func.rand()).limit(limit)
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


# ══════════════════════════════════════════════════════════════
# Profile Updates
# ══════════════════════════════════════════════════════════════

async def update_user_profile(
    session: AsyncSession,
    tg_id: int,
    first_name: Optional[str] = None,
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

    if first_name is not None:
        user.first_name = first_name
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
    """بررسی می‌کند که آیا کاربر public_id دارد یا نه، اگر نداشت برایش می‌سازد.

    FIX PHASE3-H-07: retry on IntegrityError (collision). At 6 chars the
    keyspace is ~56 billion, giving ~35% collision probability at 200K users.
    The unique index uq_users_public_id (migration 004) catches duplicates;
    we retry with a fresh ID up to 5 times before giving up.
    """
    from sqlalchemy.exc import IntegrityError

    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        return ""

    if user.public_id:
        return user.public_id

    # FIX PHASE3-H-07: retry loop for public_id collisions.
    max_attempts = 5
    for attempt in range(max_attempts):
        new_id = generate_random_public_id()
        user.public_id = new_id
        try:
            await session.flush()
            return new_id
        except IntegrityError:
            # Collision — rollback the flush, clear the ID, and try again.
            # NOTE: we don't rollback the whole session (that would lose
            # other pending changes); we just clear the attribute and let
            # the next iteration set a new one.
            user.public_id = None
            # Clear the SQLAlchemy error state for this object so we can
            # continue using the session.
            from sqlalchemy import inspect as sa_inspect
            sa_inspect(user).session.expire(user)
            logger.warning(
                "public_id collision on attempt %d/%d for user %s — retrying",
                attempt + 1, max_attempts, tg_id,
            )
            continue

    # If we get here, all attempts collided. This is astronomically unlikely
    # (5 attempts × 56B keyspace), but we handle it gracefully.
    logger.critical("Failed to generate unique public_id after %d attempts for user %s", max_attempts, tg_id)
    return ""


# ══════════════════════════════════════════════════════════════
# Likes & Reports
# ══════════════════════════════════════════════════════════════

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
    report_record = UserReport(
        reporter_id=reporter_id,
        reported_id=reported_id,
        reason=reason,
        match_history_id=match_history_id
    )
    session.add(report_record)

    reported_user = await get_user_by_tg_id_for_update(session, reported_id)
    if reported_user:
        reported_user.report_count += 1

        if reported_user.report_count == 5:
            reported_user.is_banned = True
            logger.info(f"User {reported_id} has been auto-banned due to reaching {reported_user.report_count} reports.")
            
            try:
                await redis_client.set(f"user:banned:{reported_id}", "1")
                await bot.send_message(
                    chat_id=reported_id,
                    text="❌ <b>حساب کاربری شما به دلیل دریافت گزارش‌های متعدد و نقض قوانین، مسدود شد.</b>",
                    parse_mode="HTML"
                )
                
                # --- پاکسازی دیت در زمان اتو-بن ---
                active_match = await get_active_match(session, reported_id)
                if active_match:
                    active_match.is_active = False
                    active_match.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    
                    partner_id = active_match.user_two_id if active_match.user_one_id == reported_id else active_match.user_one_id
                    
                    bad_ctx = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=reported_id, user_id=reported_id))
                    await bad_ctx.set_state(None)
                    await bad_ctx.clear()
                    await redis_client.delete(f"user:state:{reported_id}")
                    
                    partner_ctx = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=partner_id, user_id=partner_id))
                    await partner_ctx.set_state(None)
                    await partner_ctx.clear()
                    await redis_client.delete(f"user:state:{partner_id}")
                    
                    await bot.send_message(
                        chat_id=partner_id,
                        text="⚠️ <b>دیت متوقف شد!</b>\nحساب کاربر مقابل به دلیل دریافت گزارش‌های متعدد و نقض قوانین مسدود گردید.",
                        parse_mode="HTML",
                        reply_markup=get_main_menu_keyboard()
                    )
                # ----------------------------------
            except Exception as e:
                logger.error(f"Failed to process auto-ban side effects for {reported_id}: {e}")

    await session.flush()
    return report_record

async def get_user_by_tg_id_for_update(session: AsyncSession, tg_id: int) -> Optional[User]:
    """Like get_user_by_tg_id but acquires a row-level lock (FOR UPDATE).

    Use this whenever you're about to mutate user fields that could race
    with another concurrent request (coin_balance, report_count, is_banned,
    vip_expires_at, etc.). The lock is held until COMMIT/ROLLBACK.
    """
    stmt = select(User).where(User.tg_id == tg_id).with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def save_like(session: AsyncSession, liker_id: int, liked_id: int, is_pass: bool) -> UserLike:
    """Saves a like or pass interaction into the database and updates likes_count.

    FIX HIGH-03: Previously only INSERT (rowcount == 1) was detected.
    In MySQL, ON DUPLICATE KEY UPDATE returns rowcount == 2 when the row actually changed,
    so transitions like pass→like or like→pass were silently ignored by the counter.
    Now we read the prior state first and adjust likes_count based on the real transition.
    """
    # Read the prior state so we can detect a real transition.
    prior_stmt = select(UserLike).where(
        and_(UserLike.liker_id == liker_id, UserLike.liked_id == liked_id)
    )
    prior_res = await session.execute(prior_stmt)
    prior = prior_res.scalar_one_or_none()
    prior_was_like = bool(prior and not prior.is_pass)

    stmt = insert(UserLike).values(
        liker_id=liker_id,
        liked_id=liked_id,
        is_pass=is_pass
    ).on_duplicate_key_update(
        is_pass=is_pass
    )
    await session.execute(stmt)

    new_is_like = not is_pass

    # Adjust likes_count only when there is a real transition.
    if new_is_like and not prior_was_like:
        # Either a brand-new like, or a pass converted into a like.
        await session.execute(
            update(User)
            .where(User.tg_id == liked_id)
            .values(likes_count=User.likes_count + 1)
        )
    elif not new_is_like and prior_was_like:
        # A previously stored like has been flipped to a pass.
        await session.execute(
            update(User)
            .where(User.tg_id == liked_id)
            .values(likes_count=User.likes_count - 1)
        )

    await session.flush()

    fetch_stmt = select(UserLike).where(
        and_(UserLike.liker_id == liker_id, UserLike.liked_id == liked_id)
    )
    res = await session.execute(fetch_stmt)
    return res.scalar_one_or_none()

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


# ══════════════════════════════════════════════════════════════
# Referral Count
# ══════════════════════════════════════════════════════════════

async def get_referral_count(session: AsyncSession, tg_id: int) -> int:
    stmt = select(func.count(User.id)).where(User.referrer_id == tg_id)
    result = await session.execute(stmt)
    return result.scalar() or 0


# ══════════════════════════════════════════════════════════════
# Discovery & Matching
# ══════════════════════════════════════════════════════════════
async def get_discovery_candidate(
    session: AsyncSession, 
    current_user_id: int, 
    current_user_gender: str,
    distance_bucket: Optional[str] = None,
    discovery_filter: Optional[str] = None
) -> Optional[User]:
    """
    دریافت کاندیدای دیسکاوری (تک‌موردی) با استفاده از Spatial Index و Geometry دیتابیس.
    """
    _GENDER_OPPOSITE = {
        "male": "female",
        "female": "male",
        "boy": "girl",
        "girl": "boy",
    }
    target_gender = _GENDER_OPPOSITE.get(current_user_gender.lower(), "female").lower()

    # دریافت اطلاعات کاربر جستجوگر در صورت فعال بودن فیلترها
    caller = None
    if distance_bucket or discovery_filter:
        caller = await get_user_by_tg_id(session, current_user_id)
        # اگر فیلتر مسافت فعال است اما کاربر لوکیشن ندارد، جستجو لغو می‌شود
        if distance_bucket and distance_bucket != "any":
            if not caller or caller.location_lat is None or caller.location_lng is None:
                return None

    # --- ساب‌کوئری‌های تعاملات قبلی ---
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

    # --- شروط پایه ---
    conditions = [
        User.tg_id != current_user_id,
        User.gender == target_gender,
        User.completed_registration == True,
        User.invisible_mode.is_(False),
        User.is_banned == False,          # اضافه شده
        User.re_engage_blocked == False,  # اضافه شده
        ~acted_by_me_exists,
        ~blocked_by_me_exists,
        ~blocked_me_exists
    ]

    # --- فیلترهای دیسکاوری ---
    if discovery_filter == "same_city" and caller and caller.city:
        conditions.append(User.city == caller.city)
        
    elif discovery_filter == "same_province" and caller and caller.province:
        conditions.append(User.province == caller.province)
        
    elif discovery_filter == "same_interests" and caller and caller.interests:
        interests_list = [i.strip() for i in caller.interests.split(",") if i.strip()]
        if interests_list:
            # 🚀 استفاده از FIND_IN_SET به جای LIKE برای جلوگیری از تداخل کلمات مشابه
            # مثلاً LIKE '%art%' ممکن است با 'martial_arts' اشتباه گرفته شود!
            conditions.append(or_(*[func.FIND_IN_SET(i, User.interests) > 0 for i in interests_list]))
            
    elif discovery_filter == "no_chat":
        chatted_as_one = select(MatchHistory.user_two_id).where(MatchHistory.user_one_id == current_user_id)
        chatted_as_two = select(MatchHistory.user_one_id).where(MatchHistory.user_two_id == current_user_id)
        conditions.append(User.tg_id.not_in(chatted_as_one))
        conditions.append(User.tg_id.not_in(chatted_as_two))

    # --- فیلتر جغرافیایی (GIS Database Level) ---
    dist_expr = None
    if distance_bucket and distance_bucket != "any" and caller:
        conditions.append(User.location_point.is_not(None))

        # ساخت آبجکت هندسی کاربر جستجوگر با تابع Native دیتابیس
        caller_point = func.ST_GeomFromText(f'POINT({caller.location_lng} {caller.location_lat})', 4326)
        
        # محاسبه فاصله کروی
        dist_expr = func.ST_Distance_Sphere(User.location_point, caller_point)

        if distance_bucket == "0_50":
            conditions.append(dist_expr <= 50000)
        elif distance_bucket == "50_100":
            conditions.append(dist_expr.between(50000, 100000))
        elif distance_bucket == "100_200":
            conditions.append(dist_expr.between(100000, 200000))

    # --- مرتب‌سازی (Ranking) ---
    order_by_clauses = [priority_expr.desc()]
    
    if dist_expr is not None:
        # کاربرانی که نزدیک‌تر هستند در اولویت نمایش قرار می‌گیرند
        order_by_clauses.append(dist_expr.asc())
        
    order_by_clauses.append(User.last_active.desc())

    # --- اجرای کوئری با Limit 1 ---
    stmt = select(User).where(and_(*conditions)).order_by(*order_by_clauses).limit(1)
    
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def get_nearby_candidates(
    session: AsyncSession, 
    current_user: User, 
    gender_filter: Optional[str] = None, 
    limit: int = 5
) -> List[User]:
    """
    کاربران نزدیک هم‌شهری را با احتساب فیلتر جنسیت عودت می‌دهد.
    """
    conditions = [
        User.tg_id != current_user.tg_id,
        User.completed_registration == True,
        User.province == current_user.province,
        User.city == current_user.city,
        User.invisible_mode == False,
        User.is_banned == False,
        User.re_engage_blocked == False   # اضافه شده
    ]

    if gender_filter:
        # FIX PHASE3-M-02: sargable — compare directly instead of func.lower().
        gender_lower = gender_filter.lower()
        if gender_lower == "male":
            conditions.append(User.gender == "male")
        elif gender_lower == "female":
            conditions.append(User.gender == "female")

    stmt = (
        select(User)
        .where(and_(*conditions))
        .order_by(User.last_active.desc(), User.tg_id.asc())  # tg_id tie-breaker for deterministic pagination
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
        User.is_banned == False,
        User.re_engage_blocked == False,  # اضافه شده
        User.invisible_mode == False,
        User.tg_id.not_in(blocked_by_caller),
        User.tg_id.not_in(blockers_of_caller),
        or_(*[User.interests.like("%" + i.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + "%") for i in interests_list])
    ]

    if target_gender:
        conditions.append(func.lower(User.gender) == target_gender.lower())

    # گرفتن استخر بزرگتر (مثلا 100 نفر اول) برای مرتب‌سازی دقیق در پایتون
    stmt   = select(User).where(*conditions).order_by(User.last_active.desc(), User.tg_id.asc()).limit(100)
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
            score += min(len(shared), 5) * 2.0

    # --- ۲) فعالیت اخیر ---
    if candidate.last_active:
        # Both `candidate.last_active` and `datetime.now(timezone.utc)` are aware UTC datetimes
        now = datetime.now(timezone.utc)
        hours_inactive = max((now.replace(tzinfo=None) - candidate.last_active.replace(tzinfo=None)).total_seconds() / 3600.0, 0.0)
        if hours_inactive <= 1:
            score += 20.0
        elif hours_inactive <= 24:
            score += 15.0
        elif hours_inactive <= 24 * 7:
            score += 8.0
        elif hours_inactive <= 24 * 30:
            score += 3.0

    # --- ۳) فاصله جغرافیایی ---
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
    interests:      Optional[List[str]] = None,
    min_age:        int = 0,
    max_age:        int = 99,
    distance_filter: Optional[str]      = None,
    exclude_ids:    Optional[List[int]] = None,
    gender_filter:  Optional[str]       = None,
    online_only:    bool                = False,
    limit:          int = 10,
    pool_size:      int = 100, 
    discovery_filter: Optional[str]     = None, # 👈 این خط باید اضافه شود
) -> List[User]:
    caller = await get_user_by_tg_id(session, caller_tg_id)
    if not caller:
        return []

    # 1. بهینه‌سازی Subqueryها و استفاده از EXISTS برای پرفورمنس بالاتر
    acted_by_me_exists = select(1).where(
        and_(
            UserLike.liker_id == caller_tg_id,
            UserLike.liked_id == User.tg_id
        )
    ).correlate(User).exists()

    blocked_by_caller_exists = select(1).where(
        and_(
            BlockList.blocker_id == caller_tg_id,
            BlockList.blocked_id == User.tg_id
        )
    ).correlate(User).exists()

    blockers_of_caller_exists = select(1).where(
        and_(
            BlockList.blocker_id == User.tg_id,
            BlockList.blocked_id == caller_tg_id
        )
    ).correlate(User).exists()

    # 2. شروط پایه
    conditions = [
        User.tg_id != caller_tg_id,
        User.completed_registration == True,
        User.is_banned == False,
        User.re_engage_blocked == False,  # اضافه شده
        User.invisible_mode == False,
        ~acted_by_me_exists,
        ~blocked_by_caller_exists,
        ~blockers_of_caller_exists,
    ]
    
    # 3. اعمال فیلترهای استاندارد
    # --- اعمال فیلترهای دیسکاوری ---
    if discovery_filter == "same_city" and caller.city:
        conditions.append(User.city == caller.city)
    elif discovery_filter == "same_province" and caller.province:
        conditions.append(User.province == caller.province)
    elif discovery_filter == "same_interests" and caller.interests:
        interests_list = [i.strip() for i in caller.interests.split(",") if i.strip()]
        if interests_list:
            from sqlalchemy import or_
            conditions.append(or_(*[func.FIND_IN_SET(i, User.interests) > 0 for i in interests_list]))
    elif discovery_filter == "no_chat":
        chatted_as_one = select(MatchHistory.user_two_id).where(MatchHistory.user_one_id == caller_tg_id)
        chatted_as_two = select(MatchHistory.user_one_id).where(MatchHistory.user_two_id == caller_tg_id)
        conditions.append(User.tg_id.not_in(chatted_as_one))
        conditions.append(User.tg_id.not_in(chatted_as_two))
    # --------------------------------
    if gender_filter:
        conditions.append(func.lower(User.gender) == gender_filter.lower())
        
    if online_only:
        active_threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        conditions.append(User.last_active >= active_threshold)
        
    if province:
        conditions.append(User.province == province)
        
    if min_age > 0:
        conditions.append(User.age >= min_age)
        
    if max_age < 99:
        conditions.append(User.age <= max_age)
        
    if exclude_ids:
        conditions.append(User.tg_id.not_in(exclude_ids))
        
    # 4. اعمال فیلتر جغرافیایی با استفاده از فرمول Haversine مستقیماً روی lat/lng
    if distance_filter and distance_filter != "any":
        if caller.location_lat is None or caller.location_lng is None:
            return []
            
        # اطمینان از اینکه کاربر مقابل نیز لوکیشن دارد
        conditions.append(User.location_lat.is_not(None))
        conditions.append(User.location_lng.is_not(None))
        
        # محاسبه فاصله به کیلومتر (6371 شعاع زمین به کیلومتر است)
        dist_expr = (
            6371 * 2 * func.asin(
                func.sqrt(
                    func.pow(func.sin(func.radians(User.location_lat - caller.location_lat) / 2), 2) +
                    func.cos(func.radians(caller.location_lat)) * func.cos(func.radians(User.location_lat)) *
                    func.pow(func.sin(func.radians(User.location_lng - caller.location_lng) / 2), 2)
                )
            )
        )

        if distance_filter == "0_50":
            conditions.append(dist_expr <= 50)
        elif distance_filter == "50_100":
            conditions.append(dist_expr.between(50, 100))
        elif distance_filter == "100_200":
            conditions.append(dist_expr.between(100, 200))

    # 5. واکشی استخر اولیه از دیتابیس (DB-Level Filtering)
    stmt = (
        select(User)
        .where(and_(*conditions))
        # tg_id as a tie-breaker makes this ordering deterministic across repeated
        # calls even when several users share the same last_active value — without
        # it, pagination (which re-runs this whole query per page) can show the
        # same user twice on different pages, or skip one, whenever ties get
        # resolved differently between calls.
        .order_by(User.last_active.desc(), User.tg_id.asc())
        .limit(pool_size)
    )
    result = await session.execute(stmt)
    pool = list(result.scalars().all())

    if not pool:
        return []

    # 6. اسکوردهی و رتبه‌بندی نهایی در لایه پایتون
    caller_interests = {i.strip() for i in caller.interests.split(",") if i.strip()} if caller.interests else set()
    interest_filter = {i.strip() for i in interests if i.strip()} if interests else set()

    scored: List[tuple] = []
    for cand in pool:
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
            same_province_bonus=not bool(province),
        )
        scored.append((score, cand))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [cand for _, cand in scored[:limit]]


# ══════════════════════════════════════════════════════════════
# Friends — Race-Condition-Free Upsert
# ══════════════════════════════════════════════════════════════

async def add_friend(session: AsyncSession, user_id: int, friend_id: int) -> bool:
    """Adds a friend using MySQL INSERT ... ON DUPLICATE KEY UPDATE.

    This replaces the previous ``begin_nested()`` / savepoint approach that
    caused deadlock contention under high concurrency.  
    """
    stmt = insert(FriendList).values(
        user_id=user_id,
        friend_id=friend_id,
    ).on_duplicate_key_update(
        created_at=FriendList.created_at,
    )

    result = await session.execute(stmt)
    await session.flush()

    # MySQL rowcount semantics for ON DUPLICATE KEY UPDATE:
    #   1 → new row inserted
    #   0 → duplicate existed, no-op update (values unchanged)
    return result.rowcount > 0


async def remove_friend(session: AsyncSession, user_id: int, friend_id: int) -> bool:
    """حذف یک کاربر از لیست دوستان"""
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
    stmt = select(1).where(
        and_(
            FriendList.user_id == user_id, 
            FriendList.friend_id == friend_id
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def get_user_friends(session: AsyncSession, tg_id: int) -> List[User]:
    stmt = (
        select(User)
        .join(FriendList, FriendList.friend_id == User.tg_id)
        .where(FriendList.user_id == tg_id)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())

async def transfer_coins(session: AsyncSession, from_tg_id: int, to_tg_id: int, amount: int) -> tuple[bool, str]:
    """Transfer coins between two users atomically.

    FIX PHASE3-H-01 / H-02: previously `get_user_by_tg_id` (no lock) was
    used, so two concurrent transfers from the same sender could both read
    coin_balance=10, both deduct 8, and leave the balance at 2 instead of
    -6 (or one would fail silently). Now both sender AND receiver rows are
    locked with FOR UPDATE before any balance mutation.

    FIX PHASE3-H-02: process_coin_transaction's positive branch (receiver
    credit) didn't check result.rowcount. Now we rely on the row lock +
    the atomic UPDATE statement, which guarantees the row exists (we just
    locked it), so rowcount will always be 1.
    """
    if amount <= 0:
        return False, "مقدار انتقال باید بیشتر از صفر باشد."

    # FIX PHASE3-H-01: lock BOTH rows for the duration of the transaction.
    # Order by tg_id to avoid deadlocks (consistent lock ordering).
    lock_order = sorted([from_tg_id, to_tg_id])
    for lock_id in lock_order:
        await session.execute(
            select(User).where(User.tg_id == lock_id).with_for_update()
        )

    # Re-fetch sender and receiver (now with locks held).
    sender = await get_user_by_tg_id(session, from_tg_id)
    receiver = await get_user_by_tg_id(session, to_tg_id)
    if not sender:
        return False, "حساب فرستنده یافت نشد."
    if not receiver:
        return False, "حساب گیرنده یافت نشد."
    if sender.coin_balance < amount:
        return False, f"موجودی کافی نیست. موجودی فعلی: {sender.coin_balance} سکه."

    # 💡 اطمینان از اینکه هر دو کاربر public_id دارند
    if not sender.public_id:
        await ensure_public_id_exists(session, sender.tg_id)
    if not receiver.public_id:
        await ensure_public_id_exists(session, receiver.tg_id)

    # 💡 استفاده از public_id در توضیحات تراکنش برای حفظ حریم خصوصی
    sender_desc = f"انتقال سکه به کاربر {receiver.public_id}"
    receiver_desc = f"دریافت سکه از کاربر /{sender.public_id}"

    # کسر سکه از فرستنده (atomic UPDATE with balance check)
    success = await process_coin_transaction(session, sender, -amount, sender_desc, ignore_multiplier=True)

    if not success:
        return False, "تراکنش ناموفق بود (احتمالاً موجودی در لحظه کافی نبوده است)."

    # واریز سکه به گیرنده
    # FIX PHASE3-H-02: with the row lock held, this UPDATE is guaranteed to
    # affect exactly 1 row. The previous code didn't check rowcount on the
    # positive branch, which could silently succeed on a non-existent user
    # (if the receiver was deleted between the lock and the update — impossible
    # now, but defense-in-depth).
    await process_coin_transaction(session, receiver, +amount, receiver_desc, ignore_multiplier=True)
    return True, f"✅ {amount} سکه با موفقیت منتقل شد."


# ══════════════════════════════════════════════════════════════
# XP & Leveling
# ══════════════════════════════════════════════════════════════
async def add_xp_to_user(session: AsyncSession, tg_id: int, amount: int) -> bool:
    """
    Adds XP to a user safely using row-level locking (FOR UPDATE).
    Supports multiple level-ups at once via a while loop.
    """
    stmt = select(User).where(User.tg_id == tg_id).with_for_update()
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        return False
        
    # 🛡️ جلوگیری از باگ لول صفر (در صورت نقص مقادیر پیش‌فرض دیتابیس)
    if user.level < 1:
        user.level = 1
        
    user.xp_points += amount
    leveled_up = False
    
    # 🔄 حلقه while برای هندل کردن چند لول‌آپ همزمان (وقتی XP جایزه زیاد است)
    while True:
        next_level_xp = user.level * 100 
        if user.xp_points >= next_level_xp:
            user.level += 1
            user.lootbox_count += 1 
            user.xp_points -= next_level_xp 
            leveled_up = True
        else:
            break
            
    await session.flush()
    return leveled_up

# ══════════════════════════════════════════════════════════════
# Coin Packages & Purchase Orders
# ══════════════════════════════════════════════════════════════

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


async def get_pending_orders(session: AsyncSession, limit: int = 20) -> List[CoinPurchaseOrder]:
    """دریافت لیست سفارش‌های در انتظار تأیید.
    سفارشات درگاه آنلاین بیش از ۲۴ ساعت گذشته به عنوان رها شده فیلتر می‌شوند."""
    threshold = datetime.now(timezone.utc) - timedelta(hours=24)
    
    stmt = (
        select(CoinPurchaseOrder)
        .where(
            CoinPurchaseOrder.status == "pending",
            # Exclude gateway orders older than 24 hours (abandoned)
            ~(
                and_(
                    CoinPurchaseOrder.payment_method == "gateway",
                    CoinPurchaseOrder.created_at < threshold
                )
            )
        )
        .order_by(CoinPurchaseOrder.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def expire_abandoned_orders(session: AsyncSession, hours: int = 24) -> int:
    """منقضی کردن سفارشات درگاهی که بیش از مدت مشخص رها شده‌اند.

    FIX HIGH-04: previously this CRUD-layer function called `session.commit()` itself,
    which is inconsistent with the rest of the CRUD layer (which only flushes and lets
    the caller decide when to commit). If called inside a larger transaction, the early
    commit would break atomicity. Now we only flush; the caller (scheduler / endpoint)
    is responsible for committing.
    """
    threshold = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = (
        update(CoinPurchaseOrder)
        .where(
            CoinPurchaseOrder.status == "pending",
            CoinPurchaseOrder.payment_method == "gateway",
            CoinPurchaseOrder.created_at < threshold
        )
        .values(status="expired")
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount


async def get_order_with_details(session: AsyncSession, order_id: int) -> Optional[dict]:
    """دریافت جزئیات کامل یک سفارش (سفارش + بسته + کاربر)"""
    stmt = (
        select(CoinPurchaseOrder, CoinPackage, User)
        .join(CoinPackage, CoinPurchaseOrder.package_id == CoinPackage.id)
        .join(User, CoinPurchaseOrder.user_tg_id == User.tg_id)
        .where(CoinPurchaseOrder.id == order_id)
    )
    result = await session.execute(stmt)
    row = result.first()
    if not row:
        return None
    return {"order": row[0], "package": row[1], "user": row[2]}


async def get_user_orders(
    session: AsyncSession, 
    tg_id: int, 
    limit: int = 10
) -> List[CoinPurchaseOrder]:
    """دریافت تاریخچه سفارش‌های یک کاربر"""
    stmt = (
        select(CoinPurchaseOrder)
        .where(CoinPurchaseOrder.user_tg_id == tg_id)
        .order_by(CoinPurchaseOrder.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_user_transactions(
    session: AsyncSession,
    tg_id: int,
    category: str = "all",
    limit: int = 5,
    offset: int = 0
) -> tuple[List[CoinTransaction], int]:
    """دریافت تاریخچه تراکنش‌های سکه کاربر با صفحه‌بندی و فیلتر دسته‌بندی."""
    
    conditions = [CoinTransaction.user_id == tg_id]
    
    # فیلتر بر اساس دسته‌بندی
    if category == "purchase":
        conditions.append(CoinTransaction.description.like("خرید بسته%"))
    elif category == "received":
        # سکه‌های دریافتی (مثبت) به جز خرید بسته
        conditions.append(CoinTransaction.amount > 0)
        conditions.append(~CoinTransaction.description.like("خرید بسته%"))
    elif category == "spent":
        # سکه‌های خرج شده (منفی)
        conditions.append(CoinTransaction.amount < 0)

    count_stmt = select(func.count()).select_from(CoinTransaction).where(*conditions)
    total = (await session.execute(count_stmt)).scalar() or 0

    stmt = (
        select(CoinTransaction)
        .where(*conditions)
        .order_by(CoinTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    transactions = list(result.scalars().all())

    return transactions, total


# ══════════════════════════════════════════════════════════════
# VIP / Quota Consumption
# ══════════════════════════════════════════════════════════════
async def consume_vip_quota_or_coin(
    session: AsyncSession, 
    tg_id: int, 
    cost: int = 1, 
    description: str = "هزینه مچینگ / جستجوی پیشرفته",
    reference_id: Optional[int] = None  # ⭐ NEW PARAMETER
) -> bool:
    """
    Atomically verifies and consumes VIP quota or coin balance.
    Prioritizes active unlimited VIP -> VIP Quota -> Coin Balance.
    Returns True if successful, False if the user lacks sufficient funds/quota.
    """
    if cost <= 0:
        return True

    # Lock the user row atomically to prevent race conditions during concurrent requests
    stmt = select(User).where(User.tg_id == tg_id).with_for_update()
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        return False
        
    # برطرف کردن خطای offset-naive با حذف اطلاعات timezone
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    
    # FIX M-01: is_vip flag alone is not enough — the expiry must also be valid.
    # Previously a stale is_vip=True (with vip_expires_at in the past) granted free quota.
    is_vip_active = bool(user.is_vip) and (
        not user.vip_expires_at or user.vip_expires_at > now_utc
    )
    
    # 1. Check for Active Unlimited VIP
    if is_vip_active:
        return True
        
    # 2. Check and consume Limited VIP Quota
    if user.vip_quota >= cost:
        user.vip_quota -= cost
        await session.flush()
        return True
        
    # 3. Fallback to Coin Balance
    if user.coin_balance >= cost:
        user.coin_balance -= cost
        user.total_spent_coins += cost
        
        # Log the transaction
        tx = CoinTransaction(
            user_id=user.tg_id,
            amount=-cost,
            description=description,
            reference_id=reference_id  # ⭐ STORE SYSTEMATICALLY
        )
        session.add(tx)
        await session.flush()
        return True
        
    return False

# ══════════════════════════════════════════════════════════════
# Profile Comments System
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


async def get_blocked_users(
    session: AsyncSession,
    tg_id: int,
    limit: int = 5,
    offset: int = 0,
) -> tuple[List[User], int]:
    """
    دریافت لیست کاربرانی که توسط tg_id مسدود شده‌اند (با صفحه‌بندی).
    برمی‌گردونه: (لیست کاربران بلاک‌شده، تعداد کل)

    FIX PHASE1-CRIT-07: there was a duplicate `get_blocked_users` definition at
    line ~1555 (simple, unpaged) that silently shadowed this paged version.
    Callers passing `limit=` / `offset=` kwargs raised TypeError. The duplicate
    has been removed; this paged version is the canonical one.
    """
    count_stmt = (
        select(func.count())
        .select_from(BlockList)
        .where(BlockList.blocker_id == tg_id)
    )
    total = (await session.execute(count_stmt)).scalar() or 0

    stmt = (
        select(User)
        .join(BlockList, BlockList.blocked_id == User.tg_id)
        .where(BlockList.blocker_id == tg_id)
        .order_by(BlockList.id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    blocked_users = list(result.scalars().all())

    return blocked_users, total


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
    """اگه کاربر قبلاً کامنت گذاشته → ویرایش می‌کنه.
    اگه نه → کامنت جدید می‌سازه.

    FIX PHASE3-H-04: previously read-then-write (SELECT then INSERT/UPDATE),
    which races when two concurrent comments from the same author arrive.
    One would INSERT, the other would also INSERT (if the SELECT returned
    None for both), causing a UniqueConstraint violation on
    (author_tg_id, target_tg_id). Now we use INSERT ... ON DUPLICATE KEY
    UPDATE, which is atomic at the MySQL level.

    Note: SQLAlchemy's `insert(...).on_duplicate_key_update(...)` returns
    a Result, not the row. We re-SELECT after the upsert to get the row
    with the correct id and timestamps.
    """
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    stmt = mysql_insert(ProfileComment).values(
        author_tg_id=author_tg_id,
        target_tg_id=target_tg_id,
        text=text,
    ).on_duplicate_key_update(
        text=text,
        updated_at=datetime.now(timezone.utc),
    )
    await session.execute(stmt)
    await session.flush()

    # Re-fetch the row to get the id (for new inserts) and the correct
    # timestamps (for updates).
    fetch_stmt = select(ProfileComment).where(
        and_(
            ProfileComment.author_tg_id == author_tg_id,
            ProfileComment.target_tg_id == target_tg_id,
        )
    )
    result = await session.execute(fetch_stmt)
    return result.scalar_one()


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


# ══════════════════════════════════════════════════════════════
# Geo Utilities
# ══════════════════════════════════════════════════════════════

def calculate_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """محاسبه فاصله جغرافیایی بین دو نقطه بر حسب کیلومتر (فرمول Haversine)"""
    R = 6371.0 # شعاع زمین
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 1)

# ═══════════════════════════════════════════════════════════════════════════
# v3 NEW QUERIES — Gifts, VIP, Warnings, Referrals, Direct Messages, Tags,
#                   Profile Completion, Banner Campaigns, Admin Channels
# ═══════════════════════════════════════════════════════════════════════════

# ── Tag management ──────────────────────────────────────────────────────────

async def get_user_tags(session: AsyncSession, user_tg_id: int) -> list[str]:
    """Return list of tag_code strings for a user."""
    from matching_bot_project.database.models.models import UserTag
    result = await session.execute(
        select(UserTag.tag_code).where(UserTag.user_tg_id == user_tg_id)
    )
    return [row[0] for row in result.all()]

async def get_tag_catalog(session: AsyncSession, category: Optional[str] = None) -> list:
    """Return list of TagCatalog entries (cached), optionally filtered by category."""
    from matching_bot_project.database.models.models import TagCatalog

    # 1. تلاش برای خواندن از کش
    cached_catalog = await cache.get_tag_catalog()
    if cached_catalog is not None:
        if category:
            return [tag for tag in cached_catalog if tag.get("category") == category]
        return cached_catalog

    # 2. اگر در کش نبود، خواندن از دیتابیس
    stmt = select(TagCatalog).where(TagCatalog.is_active == True).order_by(TagCatalog.sort_order, TagCatalog.id)
    result = await session.execute(stmt)
    db_tags = result.scalars().all()

    # 3. تبدیل خروجی ORM به دیکشنری برای ذخیره در کش
    tags_data = [
        {
            "id": tag.id,
            "code": tag.code,
            "display_name": tag.display_name,
            "emoji": tag.emoji,
            "category": tag.category,
            "sort_order": tag.sort_order
        }
        for tag in db_tags
    ]

    # 4. ذخیره در کش
    await cache.set_tag_catalog(tags_data)

    if category:
        return [tag for tag in tags_data if tag.get("category") == category]
    return tags_data


async def set_user_tags(
    session: AsyncSession, user_tg_id: int, tag_codes: list[str], max_tags: int
) -> tuple[bool, str]:
    """Replace a user's tags. Validates against max_tags limit."""
    from matching_bot_project.database.models.models import UserTag, TagCatalog
    if len(tag_codes) > max_tags:
        return False, f"حداکثر {max_tags} تگ مجاز است."

    # Validate all codes exist in catalog
    catalog_result = await session.execute(
        select(TagCatalog.code).where(TagCatalog.code.in_(tag_codes))
    )
    valid_codes = {row[0] for row in catalog_result.all()}
    invalid = set(tag_codes) - valid_codes
    if invalid:
        return False, f"تگ‌های نامعتبر: {', '.join(invalid)}"

    # Delete existing tags
    await session.execute(
        delete(UserTag).where(UserTag.user_tg_id == user_tg_id)
    )

    # Add new tags
    for code in tag_codes:
        session.add(UserTag(user_tg_id=user_tg_id, tag_code=code))

    await session.flush()
    return True, "تگ‌های شما با موفقیت ذخیره شدند."


# ── Direct messages ─────────────────────────────────────────────────────────

async def get_user_unread_dm_count(session: AsyncSession, user_tg_id: int) -> int:
    """Count unread direct messages for a user."""
    from matching_bot_project.database.models.models import DirectMessage
    from sqlalchemy import func
    result = await session.execute(
        select(func.count()).select_from(DirectMessage).where(
            and_(
                DirectMessage.receiver_tg_id == user_tg_id,
                DirectMessage.is_read == False,
            )
        )
    )
    return result.scalar() or 0


async def get_recent_direct_messages(
    session: AsyncSession, user_tg_id: int, limit: int = 10
) -> list:
    """Return recent DMs for a user (with sender info)."""
    from matching_bot_project.database.models.models import DirectMessage, User
    result = await session.execute(
        select(DirectMessage, User)
        .join(User, DirectMessage.sender_tg_id == User.tg_id)
        .where(DirectMessage.receiver_tg_id == user_tg_id)
        .order_by(DirectMessage.sent_at.desc())
        .limit(limit)
    )
    return result.all()


# ── Friends & Blocks (lookup helpers) ───────────────────────────────────────

async def get_friends(session: AsyncSession, user_tg_id: int) -> list:
    """Return list of User objects that are friends of the given user."""
    from matching_bot_project.database.models.models import FriendList, User
    result = await session.execute(
        select(User)
        .join(FriendList, FriendList.friend_id == User.tg_id)
        .where(FriendList.user_id == user_tg_id)
    )
    return result.scalars().all()


async def _get_blocked_users_simple_REMOVED(session: AsyncSession, user_tg_id: int) -> list:
    """
    FIX PHASE1-CRIT-07: this duplicate definition has been removed.
    Use `get_blocked_users(session, tg_id, limit, offset)` above — it returns
    a `(list, int)` tuple. If you need the unpaged list, just call it with a
    large `limit` and discard the count.
    """
    raise NotImplementedError("Removed duplicate. Use get_blocked_users() above.")


async def get_users_who_liked_me(
    session: AsyncSession, user_tg_id: int, limit: int = 20
) -> list:
    """Return users who liked me (using the new index and filtering banned/blocked users)."""
    from matching_bot_project.database.models.models import UserLike, User, BlockList
    from sqlalchemy import select, and_

    # 🔄 فاز ۳: استخراج لیست مسدودی‌های دوطرفه با استفاده از Subquery
    blocked_by_me = select(BlockList.blocked_id).where(BlockList.blocker_id == user_tg_id).scalar_subquery()
    blocked_me = select(BlockList.blocker_id).where(BlockList.blocked_id == user_tg_id).scalar_subquery()

    result = await session.execute(
        select(User)
        .join(UserLike, UserLike.liker_id == User.tg_id)
        .where(
            and_(
                UserLike.liked_id == user_tg_id, 
                UserLike.is_pass == False,
                # 🔄 فاز ۳: فیلتر کردن اکانت‌های بن شده و مسدود شده
                User.is_banned == False,
                User.tg_id.not_in(blocked_by_me),
                User.tg_id.not_in(blocked_me)
            )
        )
        .order_by(UserLike.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())

# ── Admin channel management ────────────────────────────────────────────────
async def get_active_admin_channels(session: AsyncSession) -> list:
    """Return all active force-join channels (cached, up to 5)."""
    from matching_bot_project.database.models.models import AdminChannel

    cached_channels = await cache.get_admin_channels()
    if cached_channels is not None:
        return cached_channels

    result = await session.execute(
        select(AdminChannel)
        .where(AdminChannel.is_active == True)
        .order_by(AdminChannel.sort_order, AdminChannel.id)
        .limit(5)
    )
    db_channels = result.scalars().all()

    channels_data = [
        {
            "id": c.id,
            "channel_id": c.channel_id,
            "channel_username": c.channel_username,
            "invite_link": c.invite_link,
            "is_active": c.is_active,
            "sort_order": c.sort_order
        }
        for c in db_channels
    ]

    await cache.set_admin_channels(channels_data)
    return channels_data


async def add_admin_channel(
    session: AsyncSession,
    channel_id: int,
    channel_username: Optional[str] = None,
    invite_link: Optional[str] = None,
) -> tuple[bool, str]:
    """Add a force-join channel (max 5)."""
    from matching_bot_project.database.models.models import AdminChannel
    # Check count limit
    existing = await get_active_admin_channels(session)
    if len(existing) >= 5:
        return False, "حداکثر ۵ کانال مجاز است."

    # Check for duplicate
    result = await session.execute(
        select(AdminChannel).where(AdminChannel.channel_id == channel_id)
    )
    if result.scalar_one_or_none():
        return False, "این کانال قبلاً اضافه شده است."

    channel = AdminChannel(
        channel_id=channel_id,
        channel_username=channel_username,
        invite_link=invite_link,
    )
    session.add(channel)
    await session.commit()
    return True, "کانال با موفقیت اضافه شد."


async def remove_admin_channel(session: AsyncSession, channel_id: int) -> bool:
    from matching_bot_project.database.models.models import AdminChannel
    result = await session.execute(
        select(AdminChannel).where(AdminChannel.channel_id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        return False
    await session.delete(channel)
    await session.commit()
    return True


# ── Purchase order helper ───────────────────────────────────────────────────
# FIX PHASE1-CRIT-07: there was a duplicate `get_purchase_order` definition here
# that silently shadowed the original at line ~1074. The duplicate used a lazy
# import of CoinPurchaseOrder (the original imports it at the top of the module),
# so callers sometimes got a different code path. Removed; use the canonical one above.


# ── Set last_active helper (used by batched worker) ─────────────────────────

async def batch_update_users_last_active(
    session: AsyncSession, updates: dict[int, "datetime"]
) -> int:
    """Batch-update last_active for multiple users in a single statement."""
    if not updates:
        return 0
    from matching_bot_project.database.models.models import User
    # v3 FIX: use `case` (not `func.case`) — the standalone `case()` from
    # sqlalchemy.sql is the correct API. `func.case(...)` does not exist.
    case_stmt = case(
        *[(User.tg_id == tid, ts) for tid, ts in updates.items()],
        else_=User.last_active,
    )
    stmt = (
        update(User)
        .where(User.tg_id.in_(list(updates.keys())))
        .values(last_active=case_stmt, is_online=True)
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount

async def add_gift_type(
    session: AsyncSession, 
    code: str, 
    display_name: str, 
    emoji: str, 
    price_coins: int, 
    description: str
) -> GiftType:
    """یک گیفت جدید در دیتابیس ایجاد می‌کند."""
    # پیدا کردن بیشترین sort_order برای قرار گرفتن در انتهای لیست
    max_order_stmt = select(func.max(GiftType.sort_order))
    max_order_result = await session.execute(max_order_stmt)
    max_order = max_order_result.scalar() or 0

    new_gift = GiftType(
        code=code.upper(),
        display_name=display_name,
        emoji=emoji,
        price_coins=price_coins,
        description=description,
        is_active=True,
        sort_order=max_order + 1
    )
    session.add(new_gift)
    await session.flush()
    return new_gift

async def check_gift_code_exists(session: AsyncSession, code: str) -> bool:
    """بررسی می‌کند آیا گیفتی با این کد انگلیسی قبلاً ثبت شده است یا خیر."""
    stmt = select(GiftType).where(GiftType.code == code.upper())
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None

from matching_bot_project.database.models.models import AnonymousMessage

async def save_anonymous_message(session: AsyncSession, sender_id: int, target_id: int, text: str, is_media: bool = False, media_type: str = None, file_id: str = None):
    msg = AnonymousMessage(
        sender_tg_id=sender_id,
        target_tg_id=target_id,
        text=text,
        is_media=is_media,
        media_type=media_type,
        file_id=file_id
    )
    session.add(msg)
    await session.commit()
    return msg

async def get_unread_anonymous_messages(session: AsyncSession, target_id: int):
    result = await session.execute(
        select(AnonymousMessage)
        .where(AnonymousMessage.target_tg_id == target_id, AnonymousMessage.is_read == False)
        .order_by(AnonymousMessage.created_at.desc())
        .limit(50) # نمایش ۱۰ پیام آخر
    )
    return result.scalars().all()

async def get_anonymous_message_by_id(session: AsyncSession, msg_id: int):
    return await session.get(AnonymousMessage, msg_id)

async def mark_anonymous_message_as_read(session: AsyncSession, msg_id: int):
    msg = await session.get(AnonymousMessage, msg_id)
    if msg:
        msg.is_read = True
        await session.commit()

async def update_user_location(
    session: AsyncSession, 
    tg_id: int, 
    lat: float, 
    lng: float
) -> bool:
    """
    آپدیت موقعیت مکانی کاربر در دیتابیس (نسخه بهینه‌شده برای پروداکشن).
    به جای واکشی کامل کاربر (SELECT)، مستقیماً کوئری UPDATE ارسال می‌شود.
    """
    # ۱. اعتبارسنجی مختصات (جلوگیری از خطای Out of Range در MySQL Spatial)
    # عرض جغرافیایی (Latitude) باید بین ۹۰- تا ۹۰ و طول جغرافیایی (Longitude) بین ۱۸۰- تا ۱۸۰ باشد.
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return False

    # ۲. ساخت کوئری آپدیت اتمیک
    stmt = (
        update(User)
        .where(User.tg_id == tg_id)
        .values(
            location_lat=lat,
            location_lng=lng,
            # استفاده از تابع نیتیو MySQL (ST_GeomFromText) برای تبدیل استرینگ به Geometry
            # دقت: فرمت استاندارد همیشه POINT(Longitude Latitude) است
            location_point=func.ST_GeomFromText(f'POINT({lng} {lat})', 4326)
        )
    )
    
    # ۳. غیرفعال کردن همگام‌سازی بی‌مورد در نشست (session) برای پرفورمنس بالاتر
    result = await session.execute(stmt, execution_options={"synchronize_session": False})
    await session.flush()
    
    # اگر rowcount بزرگتر از ۰ باشد، یعنی کاربر وجود داشته و لوکیشن آپدیت شده است
    return result.rowcount > 0

# در فایل crud.py اضافه شود
async def notify_missed_messages(session: AsyncSession, tg_id: int):
    """بررسی پیام‌های نخوانده و ارسال یادآوری پس از اتمام دیت"""
    from matching_bot_project.bot.core.loader import bot
    
    unread_dms = await get_user_unread_dm_count(session, tg_id)
    
    anon_msgs = await get_unread_anonymous_messages(session, tg_id)
    unread_anons = len(anon_msgs) if anon_msgs else 0

    if unread_dms > 0 or unread_anons > 0:
        text = "📬 <b>پیام‌های دریافت شده در حین دیت:</b>\n\n"
        if unread_dms > 0:
            text += f"✉️ {unread_dms} پیام دایرکت جدید\n"
        if unread_anons > 0:
            text += f"💌 {unread_anons} پیام ناشناس جدید\n"
        
        text += "\n<i>برای خواندن پیام‌ها از منوی اصلی روی «📬 صندوق پیام‌ها» کلیک کنید.</i>"
        
        try:
            await bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
        except Exception:
            pass