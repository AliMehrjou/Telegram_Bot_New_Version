from datetime import datetime
from typing import Optional, List
from sqlalchemy import BigInteger, Integer, String, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from matching_bot_project.database.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    first_name: Mapped[str] = mapped_column(String(150), nullable=False)
    
    # Onboarding & Profile details
    age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(10), nullable=True) # "Male", "Female"
    province: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    profile_photo_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Economy System (Coins)
    coin_balance: Mapped[int] = mapped_column(Integer, default=3, nullable=False) # 3 coins on start
    total_earned_coins: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    total_spent_coins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Permissions and Quotas
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vip_quota: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vip_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    report_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trust_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    invisible_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Profile Extensions
    bio: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    interests: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Activity & Status
    is_online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_active: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Referral system
    referrer_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="SET NULL"), nullable=True)
    completed_registration: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    referred_users = relationship("User", backref="referrer", remote_side=[tg_id])


class CoinTransaction(Base):
    """
    Logs all economy activities: earning from invites, spending on matches/DMs.
    """
    __tablename__ = "coin_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False) # Positive (earned) or Negative (spent)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class FriendList(Base):
    """
    Stores users added to the 'My Friends' section.
    """
    __tablename__ = "friend_lists"
    __table_args__ = (UniqueConstraint("user_id", "friend_id", name="uq_user_friend"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    friend_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class BlockList(Base):
    """
    Stores blocked users to prevent future matches or messages.
    """
    __tablename__ = "block_lists"
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id", name="uq_blocker_blocked"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    blocker_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    blocked_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    option_a: Mapped[str] = mapped_column(String(200), nullable=False)
    option_b: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="General", nullable=False)


class MatchHistory(Base):
    __tablename__ = "match_histories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    user_one_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    user_two_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    questionnaire_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    user_one_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_two_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    chat_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class UserAnswer(Base):
    __tablename__ = "user_answers"
    __table_args__ = (
        UniqueConstraint("user_id", "question_id", "match_history_id", name="uq_user_question_match"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    match_history_id: Mapped[int] = mapped_column(Integer, ForeignKey("match_histories.id", ondelete="CASCADE"), nullable=False)
    
    selected_option: Mapped[str] = mapped_column(String(5), nullable=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class UserLike(Base):
    __tablename__ = "user_likes"
    __table_args__ = (
        UniqueConstraint("liker_id", "liked_id", name="uq_liker_liked"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    liker_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    liked_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    is_pass: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class UserReport(Base):
    __tablename__ = "user_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reporter_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    reported_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    match_history_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("match_histories.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)