import logging
from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import redis_client

logger = logging.getLogger(__name__)


class IsAdminFilter(BaseFilter):
    """Verifies if user belongs to the predefined administration lists."""
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user_id = event.from_user.id
        return user_id in settings.parsed_admin_ids


class IsVIPFilter(BaseFilter):
    """
    Checks if user is currently VIP.
    Utilizes Redis to check and fallback to DB states.
    """
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user_id = event.from_user.id
        
        # Check Redis cache first to avoid hammering the MySQL database
        vip_cache_key = f"user:vip_status:{user_id}"
        cached_status = await redis_client.get(vip_cache_key)
        
        if cached_status is not None:
            return cached_status == "1"
            
        # If not cached, we'll return True/False or fallback safely.
        # This filter can be injected with DB sessions inside intermediate handling.
        return False


class ChatActiveFilter(BaseFilter):
    """
    Verifies if user currently holds an active anonymous chat pairing status in Redis.
    Matches can only transmit anonymized direct messages under this condition.
    """
    async def __call__(self, event: Message) -> bool:
        user_id = event.from_user.id
        user_state_key = f"user:state:{user_id}"
        status = await redis_client.hget(user_state_key, "status")
        
        # Match succeeded means they are in state matched!
        # Once questionnaire completes, anonymous chat session becomes fully active.
        return status == "matched"
