from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from matching_bot_project.bot.core.constants import ReplyBtn

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Returns the primary Persian reply keyboard overlay."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.START_DATE, icon_custom_emoji_id="5445284980978621387")], # 🚀
            [
                KeyboardButton(text=ReplyBtn.MY_PROFILE, icon_custom_emoji_id="5373012449597335010"), # 👤
                KeyboardButton(text=ReplyBtn.NEARBY, icon_custom_emoji_id="5415803062738504079") # 🗺
            ],
            [
                KeyboardButton(text=ReplyBtn.SEARCH_USERS, icon_custom_emoji_id="5188217332748527444"), # 🔍
                KeyboardButton(text=ReplyBtn.MY_FRIENDS, icon_custom_emoji_id="5372926953978341366") # 👥
            ],
            [
                KeyboardButton(text=ReplyBtn.DISCOVER, icon_custom_emoji_id="5469741319330996757"), # 💫
                KeyboardButton(text=ReplyBtn.REFERRAL_VIP, icon_custom_emoji_id="5467406098367521267") # 👑
            ],
            [
                KeyboardButton(text=ReplyBtn.RULES, icon_custom_emoji_id="5334882760735598374"), # 📝
                KeyboardButton(text=ReplyBtn.SUPPORT, icon_custom_emoji_id="5467539229468793355"), # 📞
                KeyboardButton(text=ReplyBtn.HELP, icon_custom_emoji_id="5467666648263564704") # ❓
            ],
            [KeyboardButton(text=ReplyBtn.MY_COINS, icon_custom_emoji_id="5379600444098093058")] # 🪙
        ],
        resize_keyboard=True,
        input_field_placeholder="انتخاب کنید..."
    )

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Standard operation interruption Reply overlay."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.CANCEL, icon_custom_emoji_id="5465665476971471368")] # ❌
        ],
        resize_keyboard=True,
        input_field_placeholder="لغو عملیات..."
    )

# ── Date phase keyboard (shown when match starts) ──────────────────────────
def get_date_phase_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.PHASE_USER_PROFILE, icon_custom_emoji_id="5373012449597335010")], # 👤
            [KeyboardButton(text=ReplyBtn.DATE_PHASE_END_DATE, icon_custom_emoji_id="5465665476971471368")] # ❌
        ],
        resize_keyboard=True,
        input_field_placeholder="در حال دیت..."
    )

# ── Chat phase keyboard (shown when anonymous chat opens) ──────────────────
def get_chat_phase_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.PHASE_USER_PROFILE, icon_custom_emoji_id="5373012449597335010")], # 👤
            [KeyboardButton(text=ReplyBtn.CHAT_PHASE_END_CHAT, icon_custom_emoji_id="5465665476971471368")] # ❌
        ],
        resize_keyboard=True,
        input_field_placeholder="در حال چت ناشناس..."
    )

# ── Terms acceptance keyboard (onboarding step 0) ─────────────────────────
def get_terms_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ReplyBtn.ACCEPT_TERMS, icon_custom_emoji_id="5427009714745517609")], # ✅
            [KeyboardButton(text=ReplyBtn.SHOW_RULES, icon_custom_emoji_id="5334882760735598374")] # 📝
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )