from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Get the project root directory (where .env file is located)
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # Goes up 3 levels from config.py location

class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    WEBHOOK_SECRET_TOKEN: str
    ENVIRONMENT: str = "development"
    BOT_TOKEN: str
    BOT_USERNAME: str = "Blinddateirbot"
    REQUIRED_CHANNEL_ID: int
    CHANNEL_INVITE_LINK: str = "https://t.me/your_dating_channel"

    DB_HOST: str = "mysql_db"
    DB_PORT: int = 3306
    DB_NAME: str = "match_bot_db"
    DB_USER: str = "match_bot_user"
    # FIX PHASE2-SEC-07: was "match_bot_password" (hardcoded weak default). Now
    # has no default — the env file MUST set it. Pydantic will raise a
    # ValidationError on import if DB_PASSWORD is missing, which is much
    # better than silently deploying with a known-weak password.
    DB_PASSWORD: str
    DATABASE_URL: str
    # v3.1 SCALING: read replica (optional). Comma-separated for multiple replicas.
    DB_REPLICA_URL: str = ""
    DB_REPLICA_HOST: str = ""  # alternative to DB_REPLICA_URL
    # v3.1 SCALING: connection pool sizing
    DB_POOL_SIZE: int = 30
    DB_MAX_OVERFLOW: int = 70
    DB_REPLICA_POOL_SIZE: int = 50
    DB_REPLICA_MAX_OVERFLOW: int = 100

    REDIS_HOST: str = "redis_cache"
    REDIS_PORT: int = 6379
    # FIX PHASE2-SEC-07: was "redis_secure_pass123" (hardcoded weak default).
    # Now has no default — the env file MUST set it.
    REDIS_PASSWORD: str

    WEBHOOK_PATH: str = "/api/v1/webhook"
    BASE_URL: str = "https://funlinknow.ir"
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    ADMIN_USER_IDS: str = "12345678"
    ADMIN_SECRET_TOKEN: str
    SUPPORT_USERNAME: str = "DefaultSupportBot"

    PROXY_URL: str | None = None

    # Payment Settings
    PAYMENT_GATEWAY_ENABLED: bool = False
    ZARINPAL_MERCHANT_ID: str = ""
    CARD_NUMBER_FOR_PAYMENT: str = "۶۰۳۷۹۹۹۹۹۹۹۹۹۹۹"
    CARD_HOLDER_NAME: str = "نام ادمین / صاحب حساب"
    ZARINPAL_SANDBOX: bool = False
    ZARINPAL_CALLBACK_PATH: str = "/v1/payment/callback"

    # ════════════════════════════════════════════════════════════════════════
    # v3 NEW SETTINGS
    # ════════════════════════════════════════════════════════════════════════

    # Referral system: commission percentage (0-100)
    REFERRAL_COMMISSION_PCT: int = 20

    # Profile completion reward (coins)
    PROFILE_COMPLETION_REWARD: int = 10

    # Report reward (coins) — given to reporter when admin approves
    REPORT_REWARD_COINS: int = 5

    # Re-engagement free coins (when user has 0 coins and was inactive 3+ days)
    REENGAGE_FREE_COINS: int = 5

    # Warning system
    MAX_WARNINGS_BEFORE_BAN: int = 3

    # Anti-spam / rate limits
    LIKE_COOLDOWN_SECONDS: int = 60
    MATCH_QUEUE_TTL_SECONDS: int = 300       # 5 minutes
    MATCH_INITIAL_LOCK_SECONDS: int = 5
    ANTI_SPAM_PER_USER_SECONDS: float = 0.6

    # Force-join: maximum channels allowed
    MAX_FORCE_JOIN_CHANNELS: int = 5

    # System Guard — v3 FIX: moved from hardcoded source to env (security)
    # The PRIMARY_NODE_ID is the master admin Telegram user ID who can
    # always run /sys_diag and /sys_node commands.
    PRIMARY_NODE_ID: int = 0  # MUST be set in .env (was hardcoded in source)
    # SECRET_HASH is the SHA-256 hash of the master password used to access
    # /sys_diag. If empty, system guard commands are disabled.
    SYSTEM_GUARD_SECRET_HASH: str = ""

    # CORS — v3 FIX: configurable allow-origins instead of "*"
    CORS_ALLOW_ORIGINS: str = ""  # comma-separated, empty = same-origin only

    # Broadcast worker
    BROADCAST_BATCH_SIZE: int = 1000
    BROADCAST_DELAY_MS: int = 40
    BROADCAST_CONCURRENCY: int = 20

    # Distance filter (km) — used for nearby matching/discovery
    DEFAULT_DISTANCE_FILTER: str = "any"

    # ════════════════════════════════════════════════════════════════════════
    # v3.1 SCALING SETTINGS (for 200K+ users)
    # ════════════════════════════════════════════════════════════════════════

    # ─── Bot sharding ─────────────────────────────────────────────────────────
    # Comma-separated list of bot tokens. Each user is permanently assigned to
    # one bot via `tg_id % num_shards`. Telegram's per-bot limit is 30 msg/sec.
    # With N bots, you get N×30 msg/sec aggregate.
    # For 200K users: use 5-10 bots (150-300 msg/sec aggregate).
    BOT_SHARD_TOKENS: str = ""  # comma-separated; empty = single BOT_TOKEN mode

    # ─── arq background worker ───────────────────────────────────────────────
    ARQ_REDIS_HOST: str = "redis_cache"
    ARQ_REDIS_PORT: int = 6379
    ARQ_REDIS_PASSWORD: str = ""
    ARQ_WORKER_CONCURRENCY: int = 50
    ARQ_WORKER_MAX_JOBS: int = 100

    # ─── Cache TTLs (seconds) ────────────────────────────────────────────────
    CACHE_USER_PROFILE_TTL: int = 300        # 5 min
    CACHE_TAG_CATALOG_TTL: int = 3600        # 1 hour
    CACHE_GIFT_CATALOG_TTL: int = 3600       # 1 hour
    CACHE_VIP_PLANS_TTL: int = 3600          # 1 hour
    CACHE_ADMIN_CHANNELS_TTL: int = 300      # 5 min

    # ─── Metrics (Prometheus) ────────────────────────────────────────────────
    METRICS_ENABLED: bool = True
    METRICS_PATH: str = "/metrics"

    # ─── Rate limit (Telegram outbound) ──────────────────────────────────────
    # Per-bot outbound rate limit. Telegram allows ~30 msg/sec per bot.
    TG_OUTBOUND_RATE_PER_BOT: int = 25  # 25 to leave headroom

    @property
    def parsed_admin_ids(self) -> list[int]:
        """Convenience property formatting integer user ids."""
        try:
            return [int(uid.strip()) for uid in self.ADMIN_USER_IDS.split(",") if uid.strip()]
        except ValueError:
            return []

    @property
    def parsed_cors_origins(self) -> list[str]:
        """Parse comma-separated CORS origins."""
        if not self.CORS_ALLOW_ORIGINS:
            return []
        return [o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip()]

    @property
    def parsed_bot_shard_tokens(self) -> list[str]:
        """Parse comma-separated bot shard tokens. Empty = single-bot mode."""
        if not self.BOT_SHARD_TOKENS:
            return []
        return [t.strip() for t in self.BOT_SHARD_TOKENS.split(",") if t.strip()]

    @property
    def num_bot_shards(self) -> int:
        """Number of bot shards (1 if no sharding configured)."""
        tokens = self.parsed_bot_shard_tokens
        return len(tokens) if tokens else 1

    @property
    def effective_database_url(self) -> str:
        url = self.DATABASE_URL
        if url.startswith("mysql+aiomysql://"):
            url = url.replace("mysql+aiomysql://", "mysql+asyncmy://", 1)
        return url


settings = Settings()
