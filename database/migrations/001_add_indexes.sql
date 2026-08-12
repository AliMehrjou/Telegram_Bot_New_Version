-- ═══════════════════════════════════════════════════════════════════════════
-- Migration 001: Add missing critical indexes to existing tables
-- Fixes full-table-scan issues in StateLockMiddleware.get_active_match
-- and several other queries.
-- ═══════════════════════════════════════════════════════════════════════════

-- Index on match_histories.user_one_id (used by get_active_match on every update)
CREATE INDEX ix_match_histories_user_one ON match_histories (user_one_id);

-- Index on match_histories.user_two_id
CREATE INDEX ix_match_histories_user_two ON match_histories (user_two_id);

-- Index on match_histories.is_active (frequently filtered)
CREATE INDEX ix_match_histories_active ON match_histories (is_active);

-- Index on coin_transactions.user_id (was missing — caused full table scans)
CREATE INDEX ix_coin_transactions_user_id ON coin_transactions (user_id);

-- Index on user_likes.liked_id (was missing — caused full scans for "who liked me")
CREATE INDEX ix_user_likes_liked ON user_likes (liked_id);

-- Index on user_likes.liker_id
CREATE INDEX ix_user_likes_liker ON user_likes (liker_id);

-- Composite index on users.gender + users.city for discovery queries
CREATE INDEX ix_user_gender_city ON users (gender, city);

-- Index on users.completed_registration for admin reporting
CREATE INDEX ix_user_completed_reg ON users (completed_registration);

-- Index on user_reports.reported_id and reporter_id
CREATE INDEX ix_user_reports_reported ON user_reports (reported_id);
CREATE INDEX ix_user_reports_reporter ON user_reports (reporter_id);

-- Index on coin_purchase_orders.gateway_authority (was missing)
CREATE INDEX ix_coin_orders_authority ON coin_purchase_orders (gateway_authority);
