import string
import secrets
from datetime import datetime, timezone
from typing import Optional, List
from geoalchemy2 import Geometry

from sqlalchemy import (
    BigInteger, Integer, String, Boolean, DateTime, ForeignKey,
    Text, UniqueConstraint, Column, Float, Index, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from matching_bot_project.database.session import Base


# ─────────────────────────────────────────────────────────────
# Timezone-aware UTC timestamp helper (SQLAlchemy 2.0 compatible)
# ─────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Used as the ``default`` / ``onupdate`` callable for every
    ``DateTime(timezone=True)`` column in every model.  Centralising
    this here guarantees that *every* timestamp stored in the database
    is a clean, aware ``datetime`` with ``tzinfo=timezone.utc`` — no
    ``.replace(tzinfo=None)`` stripping, no mixed naive/aware values.
    """
    return datetime.now(timezone.utc)


class DeletedAccount(Base):
    """
    Tracks Telegram IDs of users who have deleted their accounts
    to prevent referral abuse upon re-registration.
    """
    __tablename__ = "deleted_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )


def generate_random_public_id(length=6) -> str:
    """Generate a random public ID like ``user_aB3xY9``.

    FIX PHASE3-H-07: switched from ``random.choice`` (Mersenne Twister,
    predictable) to ``secrets.choice`` (CSPRNG). At 6 chars the keyspace
    is ~56 billion, which gives ~35% collision probability at 200K users
    (birthday paradox). The caller (crud.create_user /
    crud.ensure_public_id_exists) now retries on IntegrityError, and the
    unique index uq_users_public_id (migration 004) catches duplicates.
    """
    characters = string.ascii_letters + string.digits
    return f"user_{''.join(secrets.choice(characters) for _ in range(length))}"


class User(Base):
    __tablename__ = "users"

    __table_args__ = (
        Index("ix_user_online_last_active", "is_online", "last_active"),
        Index("ix_user_vip_expires_at", "vip_expires_at"),
        Index("ix_user_gender_city", "gender", "city"),
        Index("ix_user_completed_reg", "completed_registration"),
        UniqueConstraint("tg_id", name="uq_users_tg_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    public_id: Mapped[str] = mapped_column(
        String(20), unique=True, index=True,
        default=generate_random_public_id, nullable=False,
    )

    username: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    first_name: Mapped[str] = mapped_column(String(150), nullable=False)

    # Onboarding & Profile details
    age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    province: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # legacy field kept for backward compat
    profile_photo_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # === Gramophone infrastructure (profile voice) ===
    profile_voice_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Economy System (Coins)
    coin_balance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    total_earned_coins: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    total_spent_coins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # === XP & Gacha (Loot Box) infrastructure ===
    xp_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    lootbox_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Permissions and Quotas
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vip_quota: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vip_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    report_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trust_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    # v3 NEW: Warning system (3 strikes → permanent ban)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Silent / invisible mode
    invisible_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    silent_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Disable comments from others on this user's profile
    comments_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Profile Extensions
    bio: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    interests: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # legacy

    # Cached like count for DB performance
    likes_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Activity & Status
    is_online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_active: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )

    re_engaged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    re_engage_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Bot shard assignment (CRIT-FIX): persisted once at user creation so that
    # adding/removing shards later never remaps an existing user's bot.
    # NULL means "not yet assigned" (only possible for rows created before this
    # column existed) — bot_shard_manager computes it once and backfills it.
    shard_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Referral system (references users.tg_id)
    referrer_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="SET NULL"), nullable=True,
    )
    # v3 NEW: Referral code for sharing (unique, 8-10 chars)
    referral_code: Mapped[Optional[str]] = mapped_column(String(20), unique=True, nullable=True, index=True)
    # v3 NEW: Total commission earned from referrals
    referral_earnings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    completed_registration: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
    )

    # Relationships
    referred_users = relationship("User", backref="referrer", remote_side=[tg_id])

    # Match preferences
    pref_min_age: Mapped[Optional[int]] = mapped_column(Integer, default=18, nullable=True)
    pref_max_age: Mapped[Optional[int]] = mapped_column(Integer, default=99, nullable=True)
    pref_province: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    marital_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    location_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    location_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    location_point = mapped_column(
        Geometry('POINT', srid=4326, spatial_index=False), 
        nullable=True
    )
    # v3 NEW: Profile completion tracking (denormalized for fast queries)
    profile_completion_pct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    profile_completion_rewarded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # v3 NEW: Last time we reminded user about profile completion
    last_profile_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # v3 NEW: Accepted rules manually before dating
    rules_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

class CoinTransaction(Base):
    __tablename__ = "coin_transactions"

    # v3 FIX: added index on user_id (was missing — caused full table scans)
    __table_args__ = (
        Index("ix_coin_transactions_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    
    reference_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    
    # v3 NEW: transaction type for filtering/reporting
    tx_type: Mapped[str] = mapped_column(String(30), default="generic", nullable=False)
    # Possible: 'match', 'gift_purchase', 'gift_send', 'gift_receive',
    #           'vip_purchase', 'referral_commission', 'report_reward',
    #           'profile_completion', 'free_banner', 'admin_adjust', 'generic'
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class FriendList(Base):
    __tablename__ = "friend_lists"
    __table_args__ = (UniqueConstraint("user_id", "friend_id", name="uq_user_friend"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    friend_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class BlockList(Base):
    __tablename__ = "block_lists"
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id", name="uq_blocker_blocked"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    blocker_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    blocked_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    option_a: Mapped[str] = mapped_column(String(200), nullable=False)
    option_b: Mapped[str] = mapped_column(String(200), nullable=False)
    option_c: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    option_d: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    category: Mapped[str] = mapped_column(String(50), default="General", nullable=False)
    short_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


class MatchHistory(Base):
    __tablename__ = "match_histories"

    # v3 FIX: MySQL does NOT support partial indexes (WHERE clauses on indexes).
    # Previous code used `mysql_where=text("is_active = 1")` which raises
    # `ArgumentError: Argument 'mysql_where' is not accepted by dialect 'mysql'`
    # at table-create time on MySQL. SQLAlchemy silently accepts the kwarg only
    # for SQLite/PostgreSQL. For MySQL we use a regular composite unique index
    # on (user_one_id, user_two_id, is_active) — this is slightly larger than a
    # true partial index (it also indexes inactive rows) but works correctly.
    #
    # For SQLite/PostgreSQL (used in tests), we still use the partial index for
    # efficiency. The two are functionally equivalent for the constraint we need:
    # "at most one ACTIVE match per pair".
    __table_args__ = (
        # این بخش را کامنت یا پاک کن چون MySQL از آن پشتیبانی نمی‌کند
        # Index(
        #     "uq_match_history_active_pair_partial",
        #     "user_one_id", "user_two_id",
        #     unique=True,
        #     postgresql_where=text("is_active IS TRUE"),
        #     sqlite_where=text("is_active = 1"),
        # ),
        
        # فقط این یکی باید بماند (که مخصوص MySQL است)
        Index(
            "uq_match_history_active_pair",
            "user_one_id", "user_two_id", "is_active",
            
        ),
        Index("ix_match_histories_user_one", "user_one_id"),
        Index("ix_match_histories_user_two", "user_two_id"),
        Index("ix_match_histories_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_one_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    user_two_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    questionnaire_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user_one_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_two_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    chat_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # v3 NEW: Match type for analytics ('random', 'boy', 'girl', 'nearby', 'same_age')
    match_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class UserAnswer(Base):
    __tablename__ = "user_answers"
    __table_args__ = (
        UniqueConstraint("user_id", "question_id", "match_history_id", name="uq_user_question_match"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    question_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False,
    )
    match_history_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("match_histories.id", ondelete="CASCADE"), nullable=False,
    )

    selected_option: Mapped[str] = mapped_column(String(5), nullable=False)
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class UserLike(Base):
    __tablename__ = "user_likes"

    # v3 FIX: added indexes on both liker_id and liked_id (was missing on liked_id)
    __table_args__ = (
        UniqueConstraint("liker_id", "liked_id", name="uq_liker_liked"),
        Index("ix_user_likes_liked", "liked_id"),
        Index("ix_user_likes_liker", "liker_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    liker_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    liked_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    is_pass: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class UserReport(Base):
    __tablename__ = "user_reports"
    __table_args__ = (
        Index("ix_user_reports_reported", "reported_id"),
        Index("ix_user_reports_reporter", "reporter_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reporter_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    reported_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    match_history_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("match_histories.id", ondelete="SET NULL"), nullable=True,
    )

    # v3 NEW: workflow state for new 3-strike warning system
    # 'pending' → admin reviews → 'approved' (reporter rewarded, warned user) OR 'rejected' (reporter warned)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    admin_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class CoinPackage(Base):
    __tablename__ = "coin_packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coin_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    price_toman: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class CoinPurchaseOrder(Base):
    __tablename__ = "coin_purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    package_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("coin_packages.id"), nullable=True,
    )
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False)
    # v3 NEW: 'coins', 'vip_subscription', 'gift_purchase'
    order_type: Mapped[str] = mapped_column(String(30), default="coins", nullable=False)
    # v3 NEW: JSON-encoded payload depending on order_type (plan_code, gift_code, gift_recipient_tg_id, etc.)
    order_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    receipt_photo_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    gateway_authority: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ProfileComment(Base):
    __tablename__ = "profile_comments"
    __table_args__ = (
        UniqueConstraint("author_tg_id", "target_tg_id", name="uq_author_target_comment"),
        Index("ix_comment_target_created", "target_tg_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    author_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    target_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False, index=True,
    )

    text: Mapped[str] = mapped_column(String(300), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
    )

    author = relationship("User", foreign_keys=[author_tg_id])


# ═══════════════════════════════════════════════════════════════════════════
# v3 NEW MODELS — Gifts, VIP Subscriptions, Warnings, Referrals, Tags,
#                  Direct Messages, Banner Campaigns, Profile Completion
# ═══════════════════════════════════════════════════════════════════════════


class GiftType(Base):
    """Catalog of giftable items (e.g. teddy, rose, diamond, etc.)"""
    __tablename__ = "gift_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    emoji: Mapped[str] = mapped_column(String(20), nullable=False)
    price_coins: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class UserGift(Base):
    """Inventory of gifts owned by each user (purchased or received)."""
    __tablename__ = "user_gifts"
    __table_args__ = (
        UniqueConstraint("owner_tg_id", "gift_type_id", name="uq_owner_gift_type"),
        Index("ix_user_gifts_owner", "owner_tg_id"),
        Index("ix_user_gifts_type", "gift_type_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    gift_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gift_types.id", ondelete="CASCADE"), nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 'purchase' | 'transfer_in' | 'transfer_out' (last update source)
    last_source: Mapped[str] = mapped_column(String(20), default="purchase", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
    )


class GiftTransaction(Base):
    """Log of gift purchases and transfers between users."""
    __tablename__ = "gift_transactions"
    __table_args__ = (
        Index("ix_gift_tx_sender", "sender_tg_id"),
        Index("ix_gift_tx_receiver", "receiver_tg_id"),
        Index("ix_gift_tx_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # For purchases: sender = receiver = buyer. For transfers: sender ≠ receiver.
    sender_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    receiver_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    gift_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gift_types.id", ondelete="CASCADE"), nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 'purchase' | 'transfer'
    tx_kind: Mapped[str] = mapped_column(String(20), default="purchase", nullable=False)
    # Total coins spent (0 for transfers)
    coins_spent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class VIPSubscription(Base):
    """Subscription records for VIP membership (1 week / 2 weeks / 1 month)."""
    __tablename__ = "vip_subscriptions"
    __table_args__ = (
        Index("ix_vip_user_active", "user_tg_id", "is_active"),
        Index("ix_vip_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    plan_code: Mapped[str] = mapped_column(String(20), nullable=False)  # '1w', '2w', '1m'
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payment_order_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("coin_purchase_orders.id", ondelete="SET NULL"), nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class UserWarning(Base):
    """Warning records for the 3-strike system. 3 warnings → permanent ban."""
    __tablename__ = "user_warnings"
    __table_args__ = (
        Index("ix_warnings_user", "user_tg_id"),
        Index("ix_warnings_issued", "issued_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    # 'false_report' | 'admin_action' | 'spam' | 'inappropriate'
    issued_by: Mapped[str] = mapped_column(String(20), nullable=False)
    report_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("user_reports.id", ondelete="SET NULL"), nullable=True,
    )
    issued_by_admin_tg_id: Mapped[Optional[int]] = mapped_column(
        # FIX PHASE3-M-05: was missing FK to users.tg_id. Now linked with
        # ON DELETE SET NULL so deleting an admin user keeps the warning
        # record (audit trail) but nulls out the issuer.
        BigInteger, ForeignKey("users.tg_id", ondelete="SET NULL"), nullable=True,
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class ReferralCommission(Base):
    """Commission earned by referrers when their referrals purchase coins."""
    __tablename__ = "referral_commissions"
    __table_args__ = (
        Index("ix_ref_comm_referrer", "referrer_tg_id"),
        Index("ix_ref_comm_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referrer_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    referred_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    purchase_order_id: Mapped[int] = mapped_column(
        # FIX PHASE3-H-06 / M-06: was ON DELETE CASCADE — destroyed the finance
        # audit trail when a purchase order was deleted. Now RESTRICT so a
        # purchase order with linked commissions cannot be deleted without
        # first removing the commissions.
        Integer, ForeignKey("coin_purchase_orders.id", ondelete="RESTRICT"), nullable=False,
    )
    commission_pct: Mapped[int] = mapped_column(Integer, nullable=False)  # 20
    commission_coins: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class DirectMessage(Base):
    """Private messages between users (visible only when not in active chat/date)."""
    __tablename__ = "direct_messages"
    __table_args__ = (
        Index("ix_dm_receiver_unread", "receiver_tg_id", "is_read"),
        Index("ix_dm_sender", "sender_tg_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sender_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    receiver_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BannerCampaign(Base):
    """Banner campaigns for free-coin reward via forwarding."""
    __tablename__ = "banner_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    banner_photo_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    caption_text: Mapped[str] = mapped_column(Text, nullable=False)
    reward_coins: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class BannerForward(Base):
    """Per-user forwarding record per campaign (1 reward per user per campaign)."""
    __tablename__ = "banner_forwards"
    __table_args__ = (
        UniqueConstraint("user_tg_id", "campaign_id", name="uq_user_campaign"),
        Index("ix_banner_fwd_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("banner_campaigns.id", ondelete="CASCADE"), nullable=False,
    )
    forward_msg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    forward_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # 'pending' → admin reviews → 'approved' (rewarded) OR 'rejected'
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    awarded_coins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    admin_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    forwarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TagCatalog(Base):
    """Master catalog of available tags (replaces free-text interests)."""
    __tablename__ = "tag_catalog"
    __table_args__ = (
        Index("ix_tag_catalog_category", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    emoji: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # 'lifestyle' | 'physical' | 'interest' | 'habit' | 'personality'
    category: Mapped[str] = mapped_column(String(30), default="lifestyle", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class UserTag(Base):
    """Tags assigned to each user (max 3 for normal, max 10 for VIP)."""
    __tablename__ = "user_tags"
    __table_args__ = (
        UniqueConstraint("user_tg_id", "tag_code", name="uq_user_tag"),
        Index("ix_user_tags_user", "user_tg_id"),
        Index("ix_user_tags_code", "tag_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    tag_code: Mapped[str] = mapped_column(String(50), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class ProfileCompletionLog(Base):
    """Tracks step-by-step profile completion (state machine)."""
    __tablename__ = "profile_completion_logs"
    __table_args__ = (
        UniqueConstraint("user_tg_id", "step_code", name="uq_user_step"),
        Index("ix_pclog_user", "user_tg_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
    )
    # 'photo', 'gps', 'tags', 'bio', 'city', 'voice'
    step_code: Mapped[str] = mapped_column(String(30), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class AdminChannel(Base):
    """Force-join channels (admin-managed, up to 5)."""
    __tablename__ = "admin_channels"
    __table_args__ = (
        Index("ix_admin_channels_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    channel_username: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    invite_link: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )

class AnonymousMessage(Base):
    """صندوق ورودی پیام‌های ناشناس"""
    __tablename__ = "anonymous_messages"
    __table_args__ = (
        Index("ix_anon_msg_target_unread", "target_tg_id", "is_read"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sender_tg_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    target_tg_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_media: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    media_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)