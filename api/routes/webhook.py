import logging
import secrets
from fastapi import APIRouter, Request, status, HTTPException, Header
from aiogram.types import Update
from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import dp, bot

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
    # 💡 فیکس: مقایسه با == /‌ != در پایتون constant-time نیست (character-by-character
    # و در اولین تفاوت متوقف می‌شه)، یعنی تئوریاً قابل سوءاستفاده با حملات timing.
    # secrets.compare_digest همیشه به‌اندازه‌ی کامل دو رشته زمان می‌بره، مهم نیست
    # کجا تفاوت پیدا شد یا نشد.
    expected_token = settings.WEBHOOK_SECRET_TOKEN or ""
    received_token = x_telegram_bot_api_secret_token or ""

    # 💡 محافظت اضافی: اگه WEBHOOK_SECRET_TOKEN توی تنظیمات خالی/تنظیم‌نشده باشه،
    # expected_token می‌شه "". در اون حالت اگه تلگرام هم هدر نفرسته (received_token
    # هم می‌شه "")، compare_digest("", "") مقدار True برمی‌گردونه و یعنی همه‌ی
    # درخواست‌ها بدون هیچ چک واقعی رد می‌شن! این چک صریح، اون حالت fail-open رو می‌بنده.
    if not expected_token:
        logger.critical("WEBHOOK_SECRET_TOKEN is not configured! Rejecting all webhook requests for safety.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook secret token is not configured on the server."
        )

    if not secrets.compare_digest(received_token, expected_token):
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
        # 💡 توجه: عمداً همچنان 200 OK برمی‌گردونیم تا تلگرام به‌خاطر خطای داخلی ما
        # وارد چرخه‌ی retry نشه (طبق طراحی قبلی، درست بود و دستش نمی‌زنیم).
        # اما قبلاً فقط پیام خطا (str(e)) لاگ می‌شد که برای دیباگ کردن کرش‌های
        # واقعی توی هندلرها (مثل یک AttributeError ناخواسته توی matching/discovery)
        # کافی نبود — چون آپدیت از نظر تلگرام "delivered" حساب می‌شه و دیگه دوباره
        # ارسال نمی‌شه، تنها سرنخی که از کرش باقی می‌مونه همین لاگه. الان با
        # logger.exception (که traceback کامل رو هم ثبت می‌کنه) لاگ می‌گیریم.
        logger.exception("Error handling webhook request feed: %s", e)
        return {"status": "error", "message": str(e)}
