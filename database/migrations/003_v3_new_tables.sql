-- ═══════════════════════════════════════════════════════════════════════════
-- Migration 003: New tables for v3 — Gift system, VIP subscriptions, Warnings,
-- Referral commissions, Direct messages, Banner campaigns, Tags,
-- Profile completion logs, Admin channels.
-- ═══════════════════════════════════════════════════════════════════════════

-- ─── Gift catalog ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gift_types (
  id           INT PRIMARY KEY AUTO_INCREMENT,
  code         VARCHAR(30) UNIQUE NOT NULL,
  display_name VARCHAR(50) NOT NULL,
  emoji        VARCHAR(20) NOT NULL,
  price_coins  INT NOT NULL,
  description  VARCHAR(200) NULL,
  is_active    BOOLEAN DEFAULT TRUE NOT NULL,
  sort_order   INT DEFAULT 0 NOT NULL
);

-- ─── User inventory of gifts ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_gifts (
  id           BIGINT PRIMARY KEY AUTO_INCREMENT,
  owner_tg_id  BIGINT NOT NULL,
  gift_type_id INT NOT NULL,
  quantity     INT DEFAULT 0 NOT NULL,
  last_source  VARCHAR(20) DEFAULT 'purchase' NOT NULL,
  updated_at   DATETIME NOT NULL,
  UNIQUE KEY uq_owner_gift_type (owner_tg_id, gift_type_id),
  INDEX ix_user_gifts_owner (owner_tg_id),
  INDEX ix_user_gifts_type (gift_type_id),
  CONSTRAINT fk_user_gifts_owner FOREIGN KEY (owner_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE,
  CONSTRAINT fk_user_gifts_type FOREIGN KEY (gift_type_id) REFERENCES gift_types(id) ON DELETE CASCADE
);

-- ─── Gift transactions (purchases + transfers) ─────────────────────────────
CREATE TABLE IF NOT EXISTS gift_transactions (
  id              BIGINT PRIMARY KEY AUTO_INCREMENT,
  sender_tg_id    BIGINT NOT NULL,
  receiver_tg_id  BIGINT NOT NULL,
  gift_type_id    INT NOT NULL,
  quantity        INT DEFAULT 1 NOT NULL,
  tx_kind         VARCHAR(20) DEFAULT 'purchase' NOT NULL,
  coins_spent     INT DEFAULT 0 NOT NULL,
  created_at      DATETIME NOT NULL,
  INDEX ix_gift_tx_sender (sender_tg_id),
  INDEX ix_gift_tx_receiver (receiver_tg_id),
  INDEX ix_gift_tx_created (created_at),
  CONSTRAINT fk_gift_tx_sender FOREIGN KEY (sender_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE,
  CONSTRAINT fk_gift_tx_receiver FOREIGN KEY (receiver_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE,
  CONSTRAINT fk_gift_tx_type FOREIGN KEY (gift_type_id) REFERENCES gift_types(id) ON DELETE CASCADE
);

-- ─── VIP subscription records ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vip_subscriptions (
  id               BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_tg_id       BIGINT NOT NULL,
  plan_code        VARCHAR(20) NOT NULL,
  started_at       DATETIME NOT NULL,
  expires_at       DATETIME NOT NULL,
  payment_order_id BIGINT NULL,
  is_active        BOOLEAN DEFAULT TRUE NOT NULL,
  created_at       DATETIME NOT NULL,
  INDEX ix_vip_user_active (user_tg_id, is_active),
  INDEX ix_vip_expires (expires_at),
  CONSTRAINT fk_vip_user FOREIGN KEY (user_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE,
  CONSTRAINT fk_vip_order FOREIGN KEY (payment_order_id) REFERENCES coin_purchase_orders(id) ON DELETE SET NULL
);

-- ─── User warnings (3-strike system) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_warnings (
  id                    BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_tg_id            BIGINT NOT NULL,
  reason                VARCHAR(255) NOT NULL,
  issued_by             VARCHAR(20) NOT NULL,
  report_id             BIGINT NULL,
  issued_by_admin_tg_id BIGINT NULL,
  issued_at             DATETIME NOT NULL,
  INDEX ix_warnings_user (user_tg_id),
  INDEX ix_warnings_issued (issued_at),
  CONSTRAINT fk_warn_user FOREIGN KEY (user_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE,
  CONSTRAINT fk_warn_report FOREIGN KEY (report_id) REFERENCES user_reports(id) ON DELETE SET NULL
);

-- ─── Referral commissions ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS referral_commissions (
  id                BIGINT PRIMARY KEY AUTO_INCREMENT,
  referrer_tg_id    BIGINT NOT NULL,
  referred_tg_id    BIGINT NOT NULL,
  purchase_order_id BIGINT NOT NULL,
  commission_pct    INT NOT NULL,
  commission_coins  INT NOT NULL,
  created_at        DATETIME NOT NULL,
  INDEX ix_ref_comm_referrer (referrer_tg_id),
  INDEX ix_ref_comm_created (created_at),
  CONSTRAINT fk_ref_comm_referrer FOREIGN KEY (referrer_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE,
  CONSTRAINT fk_ref_comm_referred FOREIGN KEY (referred_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE,
  CONSTRAINT fk_ref_comm_order FOREIGN KEY (purchase_order_id) REFERENCES coin_purchase_orders(id) ON DELETE CASCADE
);

-- ─── Direct messages (with privacy layer) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS direct_messages (
  id              BIGINT PRIMARY KEY AUTO_INCREMENT,
  sender_tg_id    BIGINT NOT NULL,
  receiver_tg_id  BIGINT NOT NULL,
  body            TEXT NOT NULL,
  sent_at         DATETIME NOT NULL,
  is_read         BOOLEAN DEFAULT FALSE NOT NULL,
  read_at         DATETIME NULL,
  INDEX ix_dm_receiver_unread (receiver_tg_id, is_read),
  INDEX ix_dm_sender (sender_tg_id),
  CONSTRAINT fk_dm_sender FOREIGN KEY (sender_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE,
  CONSTRAINT fk_dm_receiver FOREIGN KEY (receiver_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
);

-- ─── Banner campaigns for free-coin reward ─────────────────────────────────
CREATE TABLE IF NOT EXISTS banner_campaigns (
  id                   INT PRIMARY KEY AUTO_INCREMENT,
  banner_photo_file_id VARCHAR(255) NOT NULL,
  caption_text         TEXT NOT NULL,
  reward_coins         INT DEFAULT 2 NOT NULL,
  is_active            BOOLEAN DEFAULT TRUE NOT NULL,
  created_at           DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS banner_forwards (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_tg_id    BIGINT NOT NULL,
  campaign_id   INT NOT NULL,
  forward_msg_id BIGINT NULL,
  forward_chat_id BIGINT NULL,
  status        VARCHAR(20) DEFAULT 'pending' NOT NULL,
  awarded_coins INT DEFAULT 0 NOT NULL,
  admin_note    VARCHAR(500) NULL,
  forwarded_at  DATETIME NOT NULL,
  resolved_at   DATETIME NULL,
  UNIQUE KEY uq_user_campaign (user_tg_id, campaign_id),
  INDEX ix_banner_fwd_status (status),
  CONSTRAINT fk_bf_user FOREIGN KEY (user_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE,
  CONSTRAINT fk_bf_campaign FOREIGN KEY (campaign_id) REFERENCES banner_campaigns(id) ON DELETE CASCADE
);

-- ─── Tags (replaces free-text interests) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS tag_catalog (
  id           INT PRIMARY KEY AUTO_INCREMENT,
  code         VARCHAR(50) UNIQUE NOT NULL,
  display_name VARCHAR(100) NOT NULL,
  emoji        VARCHAR(20) NULL,
  category     VARCHAR(30) DEFAULT 'lifestyle' NOT NULL,
  is_active    BOOLEAN DEFAULT TRUE NOT NULL,
  sort_order   INT DEFAULT 0 NOT NULL,
  INDEX ix_tag_catalog_category (category)
);

CREATE TABLE IF NOT EXISTS user_tags (
  id           BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_tg_id   BIGINT NOT NULL,
  tag_code     VARCHAR(50) NOT NULL,
  assigned_at  DATETIME NOT NULL,
  UNIQUE KEY uq_user_tag (user_tg_id, tag_code),
  INDEX ix_user_tags_user (user_tg_id),
  INDEX ix_user_tags_code (tag_code),
  CONSTRAINT fk_ut_user FOREIGN KEY (user_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
);

-- ─── Profile completion logs ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profile_completion_logs (
  id           BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_tg_id   BIGINT NOT NULL,
  step_code    VARCHAR(30) NOT NULL,
  completed_at DATETIME NOT NULL,
  UNIQUE KEY uq_user_step (user_tg_id, step_code),
  INDEX ix_pclog_user (user_tg_id),
  CONSTRAINT fk_pclog_user FOREIGN KEY (user_tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
);

-- ─── Admin-managed force-join channels (up to 5) ───────────────────────────
CREATE TABLE IF NOT EXISTS admin_channels (
  id               INT PRIMARY KEY AUTO_INCREMENT,
  channel_id       BIGINT UNIQUE NOT NULL,
  channel_username VARCHAR(150) NULL,
  invite_link      VARCHAR(255) NULL,
  is_active        BOOLEAN DEFAULT TRUE NOT NULL,
  sort_order       INT DEFAULT 0 NOT NULL,
  created_at       DATETIME NOT NULL,
  INDEX ix_admin_channels_active (is_active)
);
