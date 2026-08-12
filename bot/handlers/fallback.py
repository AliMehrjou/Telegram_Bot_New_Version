import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ContentType
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.constants import Messages
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
from matching_bot_project.database.queries import crud

logger = logging.getLogger(__name__)
router = Router(name="fallback_router")

# 🚀 فیکس فاز چهارم: شکار و حذف پیام‌های سیستمی تلگرام
@router.message(F.content_type.in_({ContentType.PINNED_MESSAGE, ContentType.NEW_CHAT_MEMBERS, ContentType.LEFT_CHAT_MEMBER}))
async def ignore_service_messages(message: Message) -> None:
    """
    پیام‌های سیستمی که تلگرام به صورت خودکار تولید می‌کند را پاک می‌کند تا کاربر گیج نشود.
    """
    try:
        await message.delete()
    except Exception:
        pass
    return

@router.message(~F.text.startswith("/"))
async def catch_all_unhandled_messages(message: Message, db_session: AsyncSession) -> None:
    """
    هندلر Catch-all برای پیام‌های متنی که توسط هیچ روتر دیگری پردازش نشده‌اند.
    """
    # بررسی وضعیت دیت/چت فعال کاربر
    active_match = await crud.get_active_match(db_session, message.from_user.id)
    
    # انتخاب پیام بر اساس وضعیت
    fallback_text = Messages.UNKNOWN_MESSAGE if active_match else Messages.UNKNOWN_MESSAGE_INACTIVE

    try:
        await message.answer(
            text=fallback_text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    except Exception as exc:
        logger.error("Failed to send fallback message to user %s: %s", message.from_user.id, exc)