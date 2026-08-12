-- ═══════════════════════════════════════════════════════════════════════════
-- Migration 005: Add users.shard_index (CRITICAL — fixes crash on every
-- new user registration and every broadcast send)
-- ═══════════════════════════════════════════════════════════════════════════
-- bot/core/bot_shard_manager.py and database/queries/crud.py (create_user)
-- both read/write User.shard_index to permanently pin each user to one bot
-- shard (so adding more shards later never remaps an existing user). That
-- column was referenced in code but never added to the schema, which made
-- every call to create_user() and every call to
-- bot_shard_manager.get_bot_for_user_async() raise immediately:
--   TypeError: 'shard_index' is an invalid keyword argument for User
--
-- IMPORTANT — tested against a real MySQL 8.0.46 server while writing this:
-- neither `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` nor
-- `CREATE INDEX IF NOT EXISTS` are valid syntax on real (Oracle) MySQL —
-- both are MariaDB-only extensions and raise ERROR 1064 on MySQL. Migration
-- 002 (002_v3_columns.sql) in this same project uses the ADD COLUMN form in
-- six places and 004 uses the CREATE INDEX form in three places — none of
-- those would have actually run successfully against the mysql:8.0 image
-- this project's own docker-compose.yml deploys. Worth a dedicated look
-- separately; not re-fixing 002/004 here to keep this migration focused.
--
-- This migration uses information_schema + PREPARE/EXECUTE instead, which
-- is genuinely idempotent on real MySQL 8.0 (verified: ran it twice back to
-- back against a real server, second run cleanly no-ops both statements).
-- ═══════════════════════════════════════════════════════════════════════════

SET @col_exists := (
    SELECT COUNT(1) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'shard_index'
);
SET @add_col_sql := IF(
    @col_exists = 0,
    'ALTER TABLE users ADD COLUMN shard_index INT NULL',
    'SELECT 1'
);
PREPARE stmt FROM @add_col_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Optional but recommended: index it, since bot_shard_manager looks users up
-- by shard_index when routing outbound sends per-shard (e.g. broadcast
-- batching by shard for throughput). Cheap to add now while the table is
-- still small; expensive to add later at 200K rows.
SET @idx_exists := (
    SELECT COUNT(1) FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'users' AND index_name = 'ix_user_shard_index'
);
SET @create_idx_sql := IF(
    @idx_exists = 0,
    'CREATE INDEX ix_user_shard_index ON users (shard_index)',
    'SELECT 1'
);
PREPARE stmt FROM @create_idx_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
