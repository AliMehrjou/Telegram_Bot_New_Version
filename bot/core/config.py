import os
from pydantic_settings import BaseSettings, SettingsConfigDict



class Settings(BaseSettings):
    """
    Manages robust strongly-typed environment configurations for the bot workspace.
    Loads automatically from .env if present.
    """
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Core Bot Credentials
    BOT_TOKEN: str
    BOT_USERNAME: str = "your_bot_id"
    REQUIRED_CHANNEL_ID: int
    CHANNEL_INVITE_LINK: str = "https://t.me/your_dating_channel"

    # Deep SQL Connection Configs
    DB_HOST: str = "mysql_db"
    DB_PORT: int = 3306
    DB_NAME: str = "match_bot_db"
    DB_USER: str = "match_bot_user"
    DB_PASSWORD: str = "match_bot_password"
    DATABASE_URL: str

    # Redis Sync configuration
    REDIS_HOST: str = "redis_cache"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = "redis_secure_pass123"

    # API endpoints
    WEBHOOK_PATH: str = "/api/v1/webhook"
    BASE_URL: str = "https://yourdomain.com"
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # Administrator user lists (comma separated strings)
    ADMIN_USER_IDS: str = "12345678"
    ADMIN_SECRET_TOKEN: str

    @property
    def parsed_admin_ids(self) -> list[int]:
        """Convenience property formatting integer user ids."""
        try:
            return [int(uid.strip()) for uid in self.ADMIN_USER_IDS.split(",") if uid.strip()]
        except ValueError:
            return []
        
settings = Settings()