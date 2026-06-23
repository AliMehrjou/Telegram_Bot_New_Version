from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from matching_bot_project.bot.core.constants import ReplyBtn

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Returns the primary Persian reply keyboard overlay."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.MAIN_MENU_START_ANON_DATE)],
            [KeyboardButton(text=ReplyBtn.MAIN_MENU_MY_PROFILE), KeyboardButton(text=ReplyBtn.MAIN_MENU_NEARBY)],
            [KeyboardButton(text=ReplyBtn.MAIN_MENU_SEARCH), KeyboardButton(text=ReplyBtn.MAIN_MENU_FRIENDS)],
            [KeyboardButton(text=ReplyBtn.MAIN_MENU_DISCOVER), KeyboardButton(text=ReplyBtn.MAIN_MENU_VIP_REFERRAL)],
            [KeyboardButton(text=ReplyBtn.MAIN_MENU_TERMS), KeyboardButton(text=ReplyBtn.MAIN_MENU_SUPPORT), KeyboardButton(text=ReplyBtn.MAIN_MENU_HELP)]
        ],
        resize_keyboard=True,
        input_field_placeholder="انتخاب کنید..."
    )

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Standard operation interruption Reply overlay."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.CANCEL_TO_MAIN_MENU)]
        ],
        resize_keyboard=True,
        input_field_placeholder="لغو عملیات..."
    )

# ── Date phase keyboard (shown when match starts) ──────────────────────────
def get_date_phase_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.PHASE_USER_PROFILE)],
            [KeyboardButton(text=ReplyBtn.DATE_PHASE_END_DATE)]
        ],
        resize_keyboard=True,
        input_field_placeholder="در حال دیت..."
    )

# ── Chat phase keyboard (shown when anonymous chat opens) ──────────────────
def get_chat_phase_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.PHASE_USER_PROFILE)],
            [KeyboardButton(text=ReplyBtn.CHAT_PHASE_END_CHAT)]
        ],
        resize_keyboard=True,
        input_field_placeholder="در حال چت ناشناس..."
    )

# ── Terms acceptance keyboard (onboarding step 0) ─────────────────────────
def get_terms_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.TERMS_ACCEPT)],
            [KeyboardButton(text=ReplyBtn.TERMS_SHOW)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )