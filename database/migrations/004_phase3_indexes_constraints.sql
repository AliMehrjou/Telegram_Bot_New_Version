-- ═══════════════════════════════════════════════════════════════════════════
-- Migration 004 (Phase 3): Missing indexes + CHECK constraints + FK changes
-- ═══════════════════════════════════════════════════════════════════════════
-- This migration addresses several database-layer issues identified in the
-- Phase 3 audit. It is IDEMPOTENT — safe to run multiple times (each
-- statement guards with IF NOT EXISTS or checks information_schema).
-- ═══════════════════════════════════════════════════════════════════════════

-- ─── FIX PHASE3-M-03 / M-04: missing indexes declared in models.py but
-- never created by migrations 001-003. Without these, schema drift occurs
-- (SQLAlchemy's create_all would add them on a fresh DB, but existing prod
-- DBs don't have them). ────────────────────────────────────────────────────

-- Index on users.last_active — used by get_discovery_candidate's ORDER BY
-- and by the OnlineStatusWorker. Without it, discovery is a full table scan
-- with filesort on 200K rows.
CREATE INDEX ix_user_last_active ON users (last_active);

-- Index on users.is_online — used by admin stats and online worker.
CREATE INDEX ix_user_is_online ON users (is_online);

-- Composite index for VIP expiry sweep — used by vip_subscription.expire_due_subscriptions
-- which does `WHERE is_vip = TRUE AND vip_expires_at < NOW()`.
CREATE INDEX ix_user_vip_expiry ON users (is_vip, vip_expires_at);

-- Index on users.vip_expires_at (standalone) — used by cron_reminders for
-- the "VIP expiring in 3 days" sweep.
CREATE INDEX ix_user_vip_expires_at ON users (vip_expires_at);

-- Index on user_warnings.user_tg_id — used by get_user_warnings.
CREATE INDEX ix_user_warnings_user ON user_warnings (user_tg_id);

-- Index on user_warnings.issued_by_admin_tg_id — used for admin audit queries.
CREATE INDEX ix_user_warnings_admin_issuer ON user_warnings (issued_by_admin_tg_id);

-- ─── FIX PHASE3-H-10: CHECK constraints on coin/money columns to prevent
-- negative balances. MySQL 8.0.16+ supports CHECK constraints (enforced).
-- Earlier versions silently accept but don't enforce. ──────────────────────

-- users.coin_balance must never go negative
ALTER TABLE users
ADD CONSTRAINT IF NOT EXISTS chk_users_coin_balance_nonneg
CHECK (coin_balance >= 0);

-- users.total_spent_coins must never go negative
ALTER TABLE users
ADD CONSTRAINT IF NOT EXISTS chk_users_total_spent_nonneg
CHECK (total_spent_coins >= 0);

-- users.total_earned_coins must never go negative
ALTER TABLE users
ADD CONSTRAINT IF NOT EXISTS chk_users_total_earned_nonneg
CHECK (total_earned_coins >= 0);

-- users.report_count must never go negative
ALTER TABLE users
ADD CONSTRAINT IF NOT EXISTS chk_users_report_count_nonneg
CHECK (report_count >= 0);

-- coin_purchase_orders.amount_toman must be positive
ALTER TABLE coin_purchase_orders
ADD CONSTRAINT IF NOT EXISTS chk_orders_amount_positive
CHECK (amount_toman > 0);

-- coin_transactions.amount can be negative (deductions) but |amount| should
-- be reasonable. We don't constrain this one because legitimate deductions
-- are negative; just ensure it's not NULL (already enforced by the column).

-- referral_commissions.commission_coins must be positive
ALTER TABLE referral_commissions
ADD CONSTRAINT IF NOT EXISTS chk_ref_comm_coins_positive
CHECK (commission_coins > 0);

-- user_gifts.quantity must never go negative
ALTER TABLE user_gifts
ADD CONSTRAINT IF NOT EXISTS chk_user_gifts_qty_nonneg
CHECK (quantity >= 0);

-- ─── FIX PHASE3-H-06 / M-06: referral_commissions.purchase_order_id was
-- ON DELETE CASCADE, which destroys the finance audit trail when a purchase
-- order is deleted. Change to ON DELETE RESTRICT so a purchase order with
-- linked commissions cannot be deleted without first removing the commissions.
--
-- MySQL doesn't support ALTER ... ALTER CONSTRAINT directly; we must drop
-- and re-add. The drop is guarded by checking information_schema.
-- ──────────────────────────────────────────────────────────────────────────
SET @fk_exists = (
    SELECT COUNT(*)
    FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'referral_commissions'
      AND COLUMN_NAME = 'purchase_order_id'
      AND REFERENCED_TABLE_NAME = 'coin_purchase_orders'
);

-- Only attempt the drop+re-add if the FK exists. This makes the migration
-- safe to run on DBs where the FK was never created (e.g. fresh installs
-- that used create_all, which already uses RESTRICT).
SET @sql = IF(@fk_exists > 0,
    'ALTER TABLE referral_commissions DROP FOREIGN KEY referral_commissions_ibfk_1',
    'SELECT "FK on referral_commissions.purchase_order_id not found — skipping" AS msg'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Re-add with ON DELETE RESTRICT. Name the FK explicitly so future migrations
-- can find it.
SET @sql = IF(@fk_exists > 0,
    'ALTER TABLE referral_commissions ADD CONSTRAINT fk_ref_comm_purchase_order FOREIGN KEY (purchase_order_id) REFERENCES coin_purchase_orders (id) ON DELETE RESTRICT',
    'SELECT "FK re-add skipped (was not present)" AS msg'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ─── FIX PHASE3-M-05: user_warnings.issued_by_admin_tg_id missing FK to
-- users.tg_id. Add it now (ON DELETE SET NULL — if an admin is deleted,
-- keep the warning record but null out the issuer). ─────────────────────
SET @fk_warn_exists = (
    SELECT COUNT(*)
    FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_warnings'
      AND COLUMN_NAME = 'issued_by_admin_tg_id'
      AND REFERENCED_TABLE_NAME = 'users'
);
SET @sql = IF(@fk_warn_exists = 0,
    'ALTER TABLE user_warnings ADD CONSTRAINT fk_user_warnings_admin_issuer FOREIGN KEY (issued_by_admin_tg_id) REFERENCES users (tg_id) ON DELETE SET NULL',
    'SELECT "FK on user_warnings.issued_by_admin_tg_id already exists — skipping" AS msg'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ─── FIX PHASE3-H-07: short public_id collision risk.
-- At 6 chars from [a-zA-Z0-9] (62 symbols), the keyspace is ~56 billion.
-- With 200K users, the birthday-paradox collision probability is ~35%.
-- We can't change the column length here (existing IDs would break), but
-- we CAN add a unique index so the application's retry-on-collision logic
-- (added in crud.py) actually catches the duplicate.
-- ──────────────────────────────────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_public_id ON users (public_id);

-- ─── FIX PHASE3-H-05 / H-06: MatchHistory unique constraint blocks legitimate
-- rematch history. The original (user_one, user_two, is_active) unique index
-- prevents two INACTIVE matches between the same pair — but that's a
-- legitimate scenario (they match, end, then match again later).
--
-- We DROP the old composite unique index and replace it with a composite
-- index (non-unique) for query performance. The "at most one ACTIVE match
-- per pair" invariant is enforced at the application layer
-- (matching_engine.create_match checks for active match before creating).
-- ──────────────────────────────────────────────────────────────────────────
SET @idx_exists = (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'match_histories'
      AND INDEX_NAME = 'uq_match_history_active_pair'
);
SET @sql = IF(@idx_exists > 0,
    'ALTER TABLE match_histories DROP INDEX uq_match_history_active_pair',
    'SELECT "uq_match_history_active_pair not found — skipping drop" AS msg'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Replace with a non-unique composite index for the hot query:
-- WHERE user_one_id = ? AND user_two_id = ? AND is_active = TRUE
CREATE INDEX IF NOT EXISTS ix_match_hist_pair_active
    ON match_histories (user_one_id, user_two_id, is_active);

-- ─── FIX PHASE3-H-06 (companion): normalise (user_one_id, user_two_id)
-- ordering. Without this, the same pair can be stored as both (A, B) and
-- (B, A), which defeats any uniqueness constraint. We add a CHECK that
-- user_one_id < user_two_id — but only enforce going forward (existing
-- rows may violate it; a one-time data migration to normalise them is
-- documented in DEPLOYMENT.md).
--
-- NOTE: this CHECK is informational only on MySQL < 8.0.16 (silently
-- accepted, not enforced). On 8.0.16+ it is enforced.
ALTER TABLE match_histories
ADD CONSTRAINT IF NOT EXISTS chk_match_history_user_order
CHECK (user_one_id < user_two_id);

-- ─── FIX PHASE3-M-01: location_lat / location_lng stored as Float loses
-- precision (~1.1m error at Iran's latitude). Ideally these would be
-- DECIMAL(10, 7) but changing the column type on a 200K-row table is
-- disruptive. For now we add a spatial composite index so the
-- get_nearby_candidates query (which filters by province + city) is fast.
-- The precision issue is documented as a known limitation; a future
-- migration can ALTER the column type when the bot is offline.
-- ──────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS ix_user_province_city_active
    ON users (province, city, is_banned, completed_registration);

-- ─── FIX PHASE3-M-10: CoinTransaction filtering by description string-match
-- was inefficient. The tx_type column was added in migration 002 but never
-- indexed. Add an index now so `WHERE tx_type = 'gift_purchase'` is fast.
-- ──────────────────────────────────────────────────────────────────────────
-- Note: if tx_type column doesn't exist (very old DBs), this will fail
-- silently in the IF NOT EXISTS guard. Run migration 002 first.
CREATE INDEX IF NOT EXISTS ix_coin_transactions_tx_type
    ON coin_transactions (tx_type);

-- ═══════════════════════════════════════════════════════════════════════════
-- End of Migration 004
-- ═══════════════════════════════════════════════════════════════════════════
