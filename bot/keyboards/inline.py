from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- Onboarding ---
def get_gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🙋‍♂️ آقا", callback_data="gender_male"),
            InlineKeyboardButton(text="🙋‍♀️ خانم", callback_data="gender_female")
        ]
    ])

# --- Matching Menu ---
def get_matching_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 دیت شانسی (رایگان)", callback_data="match_random")],
        [InlineKeyboardButton(text="👦 دیت با پسر (۱ سکه)", callback_data="match_boy")],
        [InlineKeyboardButton(text="👧 دیت با دختر (۱ سکه)", callback_data="match_girl")],
        [InlineKeyboardButton(text="📍 دیت با افراد نزدیک (۱ سکه)", callback_data="match_nearby")]
    ])

# --- Match Initialisation (5-Second Delay) ---
def get_match_found_keyboard(partner_id: int, match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 مشاهده پروفایل کاربر", callback_data=f"view_profile_{partner_id}")],
        [InlineKeyboardButton(text="❌ اتمام دیت", callback_data=f"end_date_early_{match_id}")]
    ])

# --- Questionnaire ---
def get_question_reply_keyboard(question_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🅰️ گزینه اول", callback_data=f"ans_a_{question_id}"),
            InlineKeyboardButton(text="🅱️ گزینه دوم", callback_data=f"ans_b_{question_id}")
        ]
    ])

def get_chat_approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ موافقم؛ شروع گفتگو ناشناس", callback_data="approve_chat_yes")],
        [InlineKeyboardButton(text="❌ خیر؛ لغو", callback_data="approve_chat_no")]
    ])

def get_active_chat_controls(partner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛑 پایان دادن به چت", callback_data="end_active_chat")],
        [InlineKeyboardButton(text="🚩 گزارش کاربر", callback_data=f"start_report_{partner_id}")]
    ])
# --- Main Menu Sub-menus (Search & Explore) ---
def get_nearby_options_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👧 دخترها", callback_data="nearby_female")],
        [InlineKeyboardButton(text="👦 پسرها", callback_data="nearby_male")],
        [InlineKeyboardButton(text="👫 هردو جنسیت", callback_data="nearby_both")]
    ])

def get_search_options_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 کاربران آنلاین پسر", callback_data="search_online_male")],
        [InlineKeyboardButton(text="🟢 کاربران آنلاین دختر", callback_data="search_online_female")],
        [InlineKeyboardButton(text="🗺️ هم‌استانی‌ها", callback_data="search_same_province")],
        [InlineKeyboardButton(text="📍 هم‌شهری‌ها", callback_data="search_same_city")],
        [InlineKeyboardButton(text="💬 کاربران بدون چت و دیت", callback_data="search_no_chat")]
    ])

def get_coins_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 تاریخچه تراکنش‌ها", callback_data="coins_history")],
        [InlineKeyboardButton(text="💎 خرید سکه", callback_data="coins_purchase")]
    ])
# --- Admin ---
def get_admin_stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 ثبت‌نام امروز", callback_data="admin_stats_today"),
            InlineKeyboardButton(text="📅 ثبت‌نام این هفته", callback_data="admin_stats_week")
        ],
        [
            InlineKeyboardButton(text="🔥 فعال‌ترین ساعات", callback_data="admin_stats_hours"),
            InlineKeyboardButton(text="🗺 برترین استان‌ها", callback_data="admin_stats_provinces")
        ],
        [
            InlineKeyboardButton(text="💬 نرخ تبدیل مچ→چت", callback_data="admin_stats_conversion"),
            InlineKeyboardButton(text="💎 تعداد VIP فعال", callback_data="admin_stats_vip")
        ],
        [
            InlineKeyboardButton(text="🔄 بروزرسانی", callback_data="admin_stats_refresh")
        ]
    ])

def get_profile_edit_keyboard(is_vip: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="✏️ ویرایش پروفایل", callback_data="edit_profile")]
    ]
    if is_vip:
        buttons.extend([
            [InlineKeyboardButton(text="👀 بینندگان پروفایل", callback_data="vip_viewers")],
            [InlineKeyboardButton(text="👁 حالت مخفی", callback_data="vip_invisible")],
            [InlineKeyboardButton(text="🔁 مچ مجدد با نفر قبلی", callback_data="vip_rematch")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ ویرایش پروفایل", callback_data="edit_profile")]
    ])

def get_discovery_keyboard(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❤️ لایک", callback_data=f"discovery_like_{target_id}"),
            InlineKeyboardButton(text="👎 پاس", callback_data=f"discovery_pass_{target_id}")
        ],
        [InlineKeyboardButton(text="👤 پروفایل کامل", callback_data=f"view_profile_{target_id}")]
    ])
