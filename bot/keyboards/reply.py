from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Returns the primary Persian reply keyboard overlay with animated emojis."""
    # Row 1: [⚡️ شروع دیت ناشناس]              ← full width, CTA
    # Row 2: [🪬 پروفایل من]  [📍 نزدیک من]
    # Row 3: [🔍 جستجو]       [👥 دوستان]
    # Row 4: [🪙 سکه‌ها]      [🏆 آمار من]     ← NEW: آمار من
    # Row 5: [📜 قوانین]      [📞 پشتیبانی]
    # And Discovery: [💘 کشف کاربران] - Let's put this in Row 1 or 2 as it's a main feature
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚡️ شروع دیت ناشناس")],
            [KeyboardButton(text="🪬 پروفایل من"), KeyboardButton(text="📍 نزدیک من")],
            [KeyboardButton(text="💘 کشف کاربران"), KeyboardButton(text="🔍 جستجو")],
            [KeyboardButton(text="👥 دوستان"), KeyboardButton(text="🪙 سکه‌ها")],
            [KeyboardButton(text="🏆 آمار من"), KeyboardButton(text="📜 قوانین")],
            [KeyboardButton(text="📞 پشتیبانی")]
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
