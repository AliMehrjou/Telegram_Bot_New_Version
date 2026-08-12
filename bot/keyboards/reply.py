from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from matching_bot_project.bot.core.constants import ReplyBtn

def get_main_menu_keyboard(is_vip: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            # ردیف ۱
            [KeyboardButton(text=ReplyBtn.START_DATE, icon_custom_emoji_id="5445284980978621387", style="success")],
            # ردیف ۲
            [
                KeyboardButton(text=ReplyBtn.MY_PROFILE, icon_custom_emoji_id="5373012449597335010", style="primary"),
                KeyboardButton(text=ReplyBtn.DISCOVER, icon_custom_emoji_id="5469741319330996757", style="primary")
            ],
            # ردیف ۳
            [
                KeyboardButton(text=ReplyBtn.MY_COINS, icon_custom_emoji_id="5379600444098093058", style="success"),
                KeyboardButton(text=ReplyBtn.VIP_SUBSCRIPTION, icon_custom_emoji_id="5467406098367521267", style="primary")
            ],
            # ردیف ۴
            [
                KeyboardButton(text=ReplyBtn.GIFTS, icon_custom_emoji_id="5451732530048802485", style="success"),
                KeyboardButton(text="📬 صندوق پیام‌ها", icon_custom_emoji_id="5375129357373165375", style="primary") # 👈 دکمه جدید
            ],
            # ردیف ۵
            [
                KeyboardButton(text=ReplyBtn.LOOTBOX, icon_custom_emoji_id="5451732530048802485", style="success"),
                KeyboardButton(text=ReplyBtn.SUPPORT, icon_custom_emoji_id="5467539229468793355", style="primary")
            ],
            # ردیف ۶
            [
                KeyboardButton(text=ReplyBtn.REFERRAL_VIP, icon_custom_emoji_id="5373012449597335010", style="success"),
                KeyboardButton(text=ReplyBtn.HELP, icon_custom_emoji_id="5467666648263564704", style="primary")
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="انتخاب کن... (ریست ربات: /reset) 🔄"
    )
def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.CANCEL, icon_custom_emoji_id="5465665476971471368", style="danger")]
        ],
        resize_keyboard=True,
        input_field_placeholder="لغو عملیات (یا ارسال /cancel) 🛑" # 👈 اضافه شدن راهنما
    )

def get_date_phase_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=ReplyBtn.PHASE_USER_PROFILE, icon_custom_emoji_id="5373012449597335010", style="primary"),
                KeyboardButton(text="🎁 ارسال گیفت", icon_custom_emoji_id="5199749070830197566", style="success")
            ],
            [KeyboardButton(text=ReplyBtn.END_DATE, icon_custom_emoji_id="5465665476971471368", style="danger")]
        ],
        resize_keyboard=True,
        input_field_placeholder="در حال دیت... (خروج اضطراری: /reset) 🔄" # 👈 اضافه شدن راهنما
    )


def get_chat_phase_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=ReplyBtn.PHASE_USER_PROFILE, icon_custom_emoji_id="5373012449597335010", style="primary"),
                KeyboardButton(text="🎁 ارسال گیفت", icon_custom_emoji_id="5199749070830197566", style="success")
            ],
            [KeyboardButton(text=ReplyBtn.END_CHAT, icon_custom_emoji_id="5465665476971471368", style="danger")]
        ],
        resize_keyboard=True,
        input_field_placeholder="در حال چت ناشناس... (خروج اضطراری: /reset) 🔄" # 👈 اضافه شدن راهنما
    )


def get_terms_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.ACCEPT_TERMS, icon_custom_emoji_id="5427009714745517609", style="success")],
            [KeyboardButton(text=ReplyBtn.SHOW_RULES, icon_custom_emoji_id="5334882760735598374", style="primary")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def get_back_to_menu_keyboard() -> ReplyKeyboardMarkup:
    """Simple keyboard with only 'back to main menu' button."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.BACK_TO_MENU, icon_custom_emoji_id="5465665476971471368", style="primary")]
        ],
        resize_keyboard=True,
        input_field_placeholder="برگشت به منوی اصلی..."
    )
