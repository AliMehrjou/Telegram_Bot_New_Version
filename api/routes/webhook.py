import logging
from fastapi import APIRouter, Request, status, HTTPException
from aiogram.types import Update
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import dp, bot
from fastapi import APIRouter, Request, status, HTTPException, Header
import logging
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Telegram Webhook Feed"])


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def telegram_webhook_endpoint(
    request: Request, 
    x_telegram_bot_api_secret_token: str = Header(None)
):
    """
    Acts as the target security receiver for incoming Telegram server updates.
    Feeds the events recursively to aiogram dispatcher.
    """
    # Validate the secure header sent by Telegram
    # ---> FIX: Verify against the dedicated webhook token <---
    if x_telegram_bot_api_secret_token != settings.WEBHOOK_SECRET_TOKEN:
        logger.error("Security alert! Ingestion attempted with invalid Telegram Secret Token.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Forbidden security token mismatch."
        )
    

    try:
        update_dict = await request.json()
        telegram_update = Update.model_validate(update_dict, context={"bot": bot})
        
        # Route updates asynchronously to dispatcher flow
        await dp.feed_update(bot, telegram_update)
        return {"status": "ok", "delivered": True}
    except Exception as e:
        logger.error(f"Error handling webhook request feed: {str(e)}")
        # Return 200 OK always to Telegram as requested, avoiding retry hammering, but log details
        return {"status": "error", "message": str(e)}
    
