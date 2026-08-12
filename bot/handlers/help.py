"""
bot/handlers/help.py

v3 NEW: Help system handler with sub-commands.

Routes:
- /qavanin                      → show main help menu (with all sub-topics)
- /help_anonymous_chat          → how to chat anonymously
- /help_credit_coin             → what is coin/credit
- /help_nearby_users            → how to see nearby users
- /help_profile                 → what is profile
- /help_chat_request            → how to send chat request
- /help_direct_message          → what is direct message
- /help_shortcuts               → how to use shortcuts
- /help_terms_of_use            → terms of use
- /help_online_alert            → online notification
- /help_contacts                → what is contacts list
- /help_advanced_search         → advanced search
- /help_delete_message          → how to delete messages
- /help_silent_mode             → silent mode
- /help_anonymous_link          → anonymous message link
- /help_chat_end_alert          → chat end notification
- /help_delete_account          → how to delete account
- /help_profile_visitors        → who viewed my profile
- /help_vip_subscription        → VIP subscription
- /help_gifts                   → gifts
- /help_tags                    → tags

The main menu is a clickable inline keyboard that triggers the corresponding sub-command.
"""

import logging
import json
from pathlib import Path
from typing import Callable, Awaitable

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandObject

from matching_bot_project.bot.keyboards.inline import get_help_main_keyboard
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)
router = Router(name="help_router")


# Cache help content loaded from json_files/help.json
_HELP_CACHE: dict | None = None


def _load_help_content() -> dict:
    global _HELP_CACHE
    if _HELP_CACHE is not None:
        return _HELP_CACHE
    json_path = Path("json_files/help.json")
    if not json_path.exists():
        json_path = Path("/app/json_files/help.json")
    if not json_path.exists():
        _HELP_CACHE = {}
        return _HELP_CACHE
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            _HELP_CACHE = json.load(f)
    except Exception as e:
        logger.error("Error loading help.json: %s", e)
        _HELP_CACHE = {}
    return _HELP_CACHE


# Map slash commands to help topic keys (Cleaned and Standardized)
_HELP_TOPICS = {
    "anonymous_chat":      "chat",
    "credit_coin":         "credit",
    "nearby_users":        "gps",
    "profile":             "profile",
    "chat_request":        "pchat",
    "direct_message":      "direct",
    "shortcuts":           "shortcuts",
    "terms_of_use":        "terms",
    "online_alert":        "onw",
    "contacts":            "contacts",
    "advanced_search":     "search",
    "delete_message":      "deleteMessage",
    "silent_mode":         "silent",
    "anonymous_link":      "nashenas",
    "chat_end_alert":      "chw",
    "delete_account":      "delAcc",
    "profile_visitors":    "seeProfile",
    "vip_subscription":    "vip",
    "gifts":               "gapogift",
    "tags":                "tags",
}


@router.message(Command("qavanin"))
async def cmd_qavanin(message: Message):
    """Main /qavanin help menu with inline command links."""
    text = (
        "📚 <b>مرکز راهنما و پشتیبانی ربات</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "برای دسترسی سریع می‌توانید روی دستورات زیر کلیک کنید:\n\n"
        "🔹 چت ناشناس: /help_anonymous_chat\n"
        "🔹 سکه و اعتبار: /help_credit_coin\n"
        "🔹 افراد نزدیک: /help_nearby_users\n"
        "🔹 پروفایل کاربری: /help_profile\n"
        "🔹 درخواست چت: /help_chat_request\n"
        "🔹 پیام دایرکت: /help_direct_message\n"
        "🔹 میان‌برها: /help_shortcuts\n"
        "🔹 قوانین ربات: /help_terms_of_use\n"
        "🔹 هشدار آنلاین: /help_online_alert\n"
        "🔹 دوستان من: /help_contacts\n"
        "🔹 جستجوی پیشرفته: /help_advanced_search\n"
        "🔹 حذف پیام‌ها: /help_delete_message\n"
        "🔹 حالت بی‌صدا: /help_silent_mode\n"
        "🔹 لینک ناشناس: /help_anonymous_link\n"
        "🔹 اعلان پایان چت: /help_chat_end_alert\n"
        "🔹 دیلیت اکانت: /help_delete_account\n"
        "🔹 بازدیدکنندگان: /help_profile_visitors\n"
        "🔹 اشتراک VIP ویژه: /help_vip_subscription\n"
        "🔹 گیفت‌ها: /help_gifts\n"
        "🔹 تگ‌های پروفایل: /help_tags\n\n"
        "یا از منوی زیر موضوع مورد نظر خود را انتخاب کنید 👇"
    )
    await message.answer(text, reply_markup=get_help_main_keyboard(), parse_mode="HTML")


# کیبورد دکمه بازگشت
def get_help_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت به لیست راهنما", callback_data="help_back_to_main")]
    ])


# ── تابع کمکی برای پردازش مشترک محتوای راهنما ──
async def _process_help_topic(
    topic_cmd: str, 
    send_function: Callable[[str, InlineKeyboardMarkup], Awaitable[None]]
) -> bool:
    """Finds the content and executes the provided send_function. Returns True if valid."""
    content_key = _HELP_TOPICS.get(topic_cmd)
    
    if not content_key:
        return False

    content = _load_help_content()
    topic_content = content.get(content_key, "راهنمایی برای این بخش هنوز آماده نشده است.")
    
    # زیباسازی متن خروجی
    formatted_text = f"{topic_content}\n\n━━━━━━━━━━━━━━━━━━━━"
    
    # اجرای تابع ارسالی از سمت کال‌بک یا کامند
    await send_function(formatted_text, get_help_back_keyboard())
    return True


# ── ۱. هندلر برای دکمه‌های شیشه‌ای (Callback) ──
@router.callback_query(F.data.startswith("help_topic_"))
async def help_topic_callback(call: CallbackQuery):
    """User tapped a help topic in the inline keyboard."""
    topic_cmd = call.data.replace("help_topic_", "")
    
    async def _send_method(text: str, reply_markup: InlineKeyboardMarkup):
        try:
            await call.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
            await call.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")

    success = await _process_help_topic(topic_cmd, _send_method)
    
    if not success:
        await call.answer("موضوع نامعتبر.", show_alert=True)
        return
        
    await call.answer()


# ── ۲. هندلر برای دستورات اسلشِ (Commands) اختصاصی راهنما ──
# به صورت داینامیک تمام ۲۰ دستور را از روی کلیدهای دیکشنری به هندلر متصل می‌کنیم
@router.message(Command(*[f"help_{cmd}" for cmd in _HELP_TOPICS.keys()]))
async def help_topic_command(message: Message, command: CommandObject):
    """User sent a specific /help_... command directly."""
    # استخراج نام دستور بدون پیشوند (مثلاً /help_gifts -> gifts)
    topic_cmd = command.command.replace("help_", "", 1)
    
    async def _send_method(text: str, reply_markup: InlineKeyboardMarkup):
        await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
        
    success = await _process_help_topic(topic_cmd, _send_method)
    
    if not success:
        await message.answer("موضوع نامعتبر. لطفاً از طریق منوی /qavanin اقدام کنید.")


@router.callback_query(F.data == "help_back_to_main")
async def help_back_to_main_callback(call: CallbackQuery):
    """User tapped back button to return to main help menu."""
    text = (
        "📚 <b>مرکز راهنما و پشتیبانی ربات</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "برای دسترسی سریع می‌توانید روی دستورات زیر کلیک کنید:\n\n"
        "🔹 چت ناشناس: /help_anonymous_chat\n"
        "🔹 سکه و اعتبار: /help_credit_coin\n"
        "🔹 افراد نزدیک: /help_nearby_users\n"
        "🔹 پروفایل کاربری: /help_profile\n"
        "🔹 درخواست چت: /help_chat_request\n"
        "🔹 پیام دایرکت: /help_direct_message\n"
        "🔹 میان‌برها: /help_shortcuts\n"
        "🔹 قوانین ربات: /help_terms_of_use\n"
        "🔹 هشدار آنلاین: /help_online_alert\n"
        "🔹 دوستان من: /help_contacts\n"
        "🔹 جستجوی پیشرفته: /help_advanced_search\n"
        "🔹 حذف پیام‌ها: /help_delete_message\n"
        "🔹 حالت بی‌صدا: /help_silent_mode\n"
        "🔹 لینک ناشناس: /help_anonymous_link\n"
        "🔹 اعلان پایان چت: /help_chat_end_alert\n"
        "🔹 دیلیت اکانت: /help_delete_account\n"
        "🔹 بازدیدکنندگان: /help_profile_visitors\n"
        "🔹 اشتراک VIP ویژه: /help_vip_subscription\n"
        "🔹 گیفت‌ها: /help_gifts\n"
        "🔹 تگ‌های پروفایل: /help_tags\n\n"
        "یا از منوی زیر موضوع مورد نظر خود را انتخاب کنید 👇"
    )
    try:
        await call.message.edit_text(text, reply_markup=get_help_main_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    await call.answer()