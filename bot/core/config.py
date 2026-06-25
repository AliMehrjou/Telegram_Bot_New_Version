import os
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

    BOT_TOKEN: str
    BOT_USERNAME: str = "Blinddateirbot"
    REQUIRED_CHANNEL_ID: int
    CHANNEL_INVITE_LINK: str = "https://t.me/your_dating_channel"

    DB_HOST: str = "mysql_db"
    DB_PORT: int = 3306
    DB_NAME: str = "match_bot_db"
    DB_USER: str = "match_bot_user"
    DB_PASSWORD: str = "match_bot_password"
    DATABASE_URL: str

    REDIS_HOST: str = "redis_cache"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = "redis_secure_pass123"

    WEBHOOK_PATH: str = "/api/v1/webhook"
    BASE_URL: str = "https://funlinknow.ir"
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    ADMIN_USER_IDS: str = "12345678"
    ADMIN_SECRET_TOKEN: str
    SUPPORT_USERNAME: str = "DefaultSupportBot"

    PROXY_URL: str | None = None   # 👈 FIX THIS

    # ================== کدهای افزودنی ==================
    # Payment Settings
    PAYMENT_GATEWAY_ENABLED: bool = False
    ZARINPAL_MERCHANT_ID: str = ""
    CARD_NUMBER_FOR_PAYMENT: str = "۶۰۳۷۹۹۹۹۹۹۹۹۹۹۹۹" # شماره کارت پیش‌فرض
    CARD_HOLDER_NAME: str = "نام ادمین / صاحب حساب"
    
    @property
    def parsed_admin_ids(self) -> list[int]:
        try:
            return [int(uid.strip()) for uid in self.ADMIN_USER_IDS.split(",") if uid.strip()]
        except ValueError:
            return []

    @property
    def parsed_admin_ids(self) -> list[int]:
        """Convenience property formatting integer user ids."""
        try:
            return [int(uid.strip()) for uid in self.ADMIN_USER_IDS.split(",") if uid.strip()]
        except ValueError:
            return []
settings = Settings()