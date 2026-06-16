from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Returns the primary Persian reply keyboard overlay."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎯 شروع دیت ناشناس")],
            [KeyboardButton(text="👤 پروفایل من"), KeyboardButton(text="📍 افراد نزدیک من")],
            [KeyboardButton(text="🔍 جستجوی کاربران"), KeyboardButton(text="👥 دوستان من")],
            [KeyboardButton(text="🪙 سکه‌های من"), KeyboardButton(text="📜 قوانین")],
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