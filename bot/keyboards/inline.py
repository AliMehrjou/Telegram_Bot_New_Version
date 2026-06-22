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
        ],
        [
            InlineKeyboardButton(text="🇨 گزینه سوم", callback_data=f"ans_c_{question_id}"),
            InlineKeyboardButton(text="🇩 گزینه چهارم", callback_data=f"ans_d_{question_id}")
        ]
    ])

def get_chat_approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ موافقم؛ شروع گفتگو ناشناس", callback_data="approve_chat_yes")],
        [InlineKeyboardButton(text="❌ خیر؛ لغو", callback_data="approve_chat_no")]
    ])

def get_active_chat_controls(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛑 پایان دادن به چت", callback_data="end_active_chat")],
        [InlineKeyboardButton(text="🚩 گزارش کاربر", callback_data=f"trigger_report_{target_id}")]
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

# --- VIP Panel ---
def get_vip_panel_keyboard(invisible_mode: bool) -> InlineKeyboardMarkup:
    invisible_text = "👁 حالت مخفی: روشن 🟢" if invisible_mode else "👁 حالت مخفی: خاموش 🔴"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👀 بینندگان پروفایل", callback_data="vip_viewers")],
        [InlineKeyboardButton(text=invisible_text, callback_data="vip_toggle_invisible")],
        [InlineKeyboardButton(text="🔁 مچ مجدد با نفر قبلی", callback_data="vip_rematch")]
    ])

def get_vip_age_filter_keyboard(match_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="[۱۸-۲۵]", callback_data=f"vip_age_filter_18_25_{match_type}")],
        [InlineKeyboardButton(text="[۲۵-۳۰]", callback_data=f"vip_age_filter_25_30_{match_type}")],
        [InlineKeyboardButton(text="[۳۰-۴۰]", callback_data=f"vip_age_filter_30_40_{match_type}")],
        [InlineKeyboardButton(text="[هر سنی]", callback_data=f"vip_age_filter_0_99_{match_type}")]
    ])

# ── Onboarding: terms acceptance ───────────────────────────────────────────
def get_terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 نمایش قوانین",    callback_data="terms_show")],
        [InlineKeyboardButton(text="✅ پذیرفتن قوانین",  callback_data="terms_accept")]
    ])

# ── Date termination double-confirm ────────────────────────────────────────
def get_end_date_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ بله، دیت را پایان می‌دهم", callback_data="confirm_end_date")],
        [InlineKeyboardButton(text="❌ لغو و بازگشت",              callback_data="cancel_end_date")]
    ])

# ── Chat termination double-confirm ────────────────────────────────────────
def get_end_chat_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ بله، چت را پایان می‌دهم", callback_data="confirm_end_chat")],
        [InlineKeyboardButton(text="❌ لغو و بازگشت",             callback_data="cancel_end_chat")]
    ])

# ── Other-user profile action keyboard ─────────────────────────────────────
def get_user_action_keyboard(
    target_tg_id: int,
    is_blocked: bool = False
) -> InlineKeyboardMarkup:
    block_text = "🔓 آنبلاک کاربر" if is_blocked else "🚫 بلاک کاربر"
    block_cb   = f"unblock_user_{target_tg_id}" if is_blocked else f"block_user_{target_tg_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💘 درخواست دیت",      callback_data=f"req_date_{target_tg_id}"),
            InlineKeyboardButton(text="💬 درخواست چت",       callback_data=f"req_chat_{target_tg_id}")
        ],
        [
            InlineKeyboardButton(text="✉️ ارسال دایرکت",     callback_data=f"req_direct_{target_tg_id}"),
            InlineKeyboardButton(text="🪙 انتقال سکه",       callback_data=f"transfer_coin_{target_tg_id}")
        ],
        [
            InlineKeyboardButton(text="👥 افزودن به دوستان", callback_data=f"add_friend_{target_tg_id}"),
            InlineKeyboardButton(text="❤️ لایک",              callback_data=f"like_user_{target_tg_id}")
        ],
        [InlineKeyboardButton(text=block_text, callback_data=block_cb)],
        [InlineKeyboardButton(text="🚩 گزارش تخلف",          callback_data=f"report_user_{target_tg_id}")]
    ])

# ── Report reasons (10 preset options, no text input) ──────────────────────
def get_report_reasons_keyboard(reported_tg_id: int) -> InlineKeyboardMarkup:
    reasons = [
        ("🔞 عکس نامناسب",       "inappropriate_photo"),
        ("💸 کلاهبردار",         "scammer"),
        ("🤬 توهین و فحاشی",    "harassment"),
        ("📢 اسپم/تبلیغات",     "spam"),
        ("👤 جعل هویت",          "impersonation"),
        ("🔗 ارسال لینک مشکوک",  "suspicious_link"),
        ("🔞 محتوای غیراخلاقی",  "adult_content"),
        ("💊 فروش مواد",         "drugs"),
        ("🤖 ربات/فیک",          "bot_fake"),
        ("⚠️ سایر موارد",        "other"),
    ]
    keyboard = [
        [InlineKeyboardButton(
            text=label,
            callback_data=f"report_reason_{reported_tg_id}_{code}"
        )]
        for label, code in reasons
    ]
    keyboard.append(
        [InlineKeyboardButton(text="❌ انصراف", callback_data="report_cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ── Discovery: age range selection ─────────────────────────────────────────
def get_discovery_age_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="۱۸ تا ۲۵ سال",      callback_data="disc_age_18_25")],
        [InlineKeyboardButton(text="۲۵ تا ۳۰ سال",      callback_data="disc_age_25_30")],
        [InlineKeyboardButton(text="۳۰ تا ۴۰ سال",      callback_data="disc_age_30_40")],
        [InlineKeyboardButton(text="۴۰ تا ۵۰ سال",      callback_data="disc_age_40_50")],
        [InlineKeyboardButton(text="بدون محدودیت سنی",  callback_data="disc_age_0_99")]
    ])

# ── Discovery: interests multi-select ──────────────────────────────────────
def get_discovery_interests_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    interests = {
        "gaming":  "🎮 گیمینگ",  "music":   "🎵 موزیک",
        "travel":  "✈️ سفر",     "movies":  "🎬 فیلم",
        "sports":  "⚽️ ورزش",   "reading": "📚 مطالعه",
        "cooking": "🍳 آشپزی",   "art":     "🎨 هنر",
        "tech":    "💻 تکنولوژی","nature":  "🌿 طبیعت",
    }
    keyboard = []
    keys = list(interests.keys())
    for i in range(0, len(keys), 2):
        row = []
        for j in range(2):
            if i + j < len(keys):
                k     = keys[i + j]
                label = interests[k] + (" ✅" if k in selected else "")
                row.append(InlineKeyboardButton(
                    text=label, callback_data=f"disc_int_{k}"
                ))
        keyboard.append(row)
    keyboard.append(
        [InlineKeyboardButton(text="✅ تأیید و جستجو", callback_data="disc_int_confirm")]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)