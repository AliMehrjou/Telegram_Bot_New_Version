-- ═══════════════════════════════════════════════════════════════════════════
-- Migration 002: Add new columns to existing tables (v3 features)
-- ═══════════════════════════════════════════════════════════════════════════

-- users table additions: warning_count, referral_code, referral_earnings,
-- profile completion tracking, match_type
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS warning_count INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS referral_code VARCHAR(20) UNIQUE NULL,
  ADD COLUMN IF NOT EXISTS referral_earnings INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS profile_completion_pct INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS profile_completion_rewarded BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS last_profile_reminder_at DATETIME NULL;

CREATE INDEX IF NOT EXISTS ix_users_referral_code ON users (referral_code);

-- coin_transactions: add tx_type for filtering/reporting
ALTER TABLE coin_transactions
  ADD COLUMN IF NOT EXISTS tx_type VARCHAR(30) NOT NULL DEFAULT 'generic';

CREATE INDEX IF NOT EXISTS ix_coin_tx_type ON coin_transactions (tx_type);

-- user_reports: add status workflow for 3-strike warning system
ALTER TABLE user_reports
  ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS admin_note VARCHAR(500) NULL,
  ADD COLUMN IF NOT EXISTS resolved_at DATETIME NULL;

CREATE INDEX IF NOT EXISTS ix_user_reports_status ON user_reports (status);

-- coin_purchase_orders: add order_type and order_payload for VIP/Gift purchases
ALTER TABLE coin_purchase_orders
  ADD COLUMN IF NOT EXISTS order_type VARCHAR(30) NOT NULL DEFAULT 'coins',
  ADD COLUMN IF NOT EXISTS order_payload TEXT NULL,
  MODIFY COLUMN package_id INT NULL;

-- match_histories: add match_type for analytics
ALTER TABLE match_histories
  ADD COLUMN IF NOT EXISTS match_type VARCHAR(20) NULL;
