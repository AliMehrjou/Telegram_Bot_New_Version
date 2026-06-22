from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Returns the primary Persian reply keyboard overlay."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚡️ شروع دیت ناشناس")],
            [KeyboardButton(text="🪬 پروفایل من"), KeyboardButton(text="📍 نزدیک من")],
            [KeyboardButton(text="🔍 جستجوی کاربران"), KeyboardButton(text="👥 دوستان من")],
            [KeyboardButton(text="💘 کشف کاربران"),KeyboardButton(text="🎁 زیرمجموعه‌گیری & VIP")],
            [KeyboardButton(text="📜 قوانین"), KeyboardButton(text="📞 پشتیبانی"), KeyboardButton(text="❔ راهنما")]
        ],
        resize_keyboard=True,
        input_field_placeholder="انتخاب کنید..."
    )

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Standard operation interruption Reply overlay."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ انصراف و منوی اصلی")]
        ],
        resize_keyboard=True,
        input_field_placeholder="لغو عملیات..."
    )

# ── Date phase keyboard (shown when match starts) ──────────────────────────
def get_date_phase_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 پروفایل کاربر")],
            [KeyboardButton(text="🛑 اتمام دیت")]
        ],
        resize_keyboard=True,
        input_field_placeholder="در حال دیت..."
    )

# ── Chat phase keyboard (shown when anonymous chat opens) ──────────────────
def get_chat_phase_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 پروفایل کاربر")],
            [KeyboardButton(text="🛑 اتمام چت")]
        ],
        resize_keyboard=True,
        input_field_placeholder="در حال چت ناشناس..."
    )

# ── Terms acceptance keyboard (onboarding step 0) ─────────────────────────
def get_terms_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ قوانین را می‌پذیرم")],
            [KeyboardButton(text="📜 نمایش قوانین")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )