from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from matching_bot_project.bot.core.constants import InlineBtn
from matching_bot_project.database.models.models import CoinPackage

# ================== کدهای افزودنی ==================
def get_coin_packages_keyboard(packages: list[CoinPackage]) -> InlineKeyboardMarkup:
    kb = []
    for pkg in packages:
        # فرمت نمایش: 50 سکه — 20,000 تومان
        text = f"🪙 {pkg.coin_amount} سکه — {pkg.price_toman:,} تومان"
        kb.append([InlineKeyboardButton(text=text, callback_data=f"buy_package_{pkg.id}")])
    kb.append([InlineKeyboardButton(text="🔙 انصراف", callback_data="close_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_payment_method_keyboard(gateway_enabled: bool) -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(text="💳 کارت به کارت (آفلاین)", callback_data="pay_method_card")]]
    if gateway_enabled:
        kb.append([InlineKeyboardButton(text="🏦 پرداخت آنلاین (درگاه)", callback_data="pay_method_gateway")])
    kb.append([InlineKeyboardButton(text="🔙 انصراف", callback_data="cancel_payment")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_admin_receipt_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تأیید واریز", callback_data=f"verify_receipt_{order_id}"),
            InlineKeyboardButton(text="❌ رد کردن", callback_data=f"reject_receipt_{order_id}")
        ]
    ])
# --- Onboarding ---
def get_gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=InlineBtn.GENDER_MALE,
                callback_data="set_gender_male", # 🚹
            ),
            InlineKeyboardButton(
                text=InlineBtn.GENDER_FEMALE,
                callback_data="set_gender_female", # 🚺
            ),
        ]
    ])

# --- Matching Menu ---
def get_matching_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=InlineBtn.MATCH_RANDOM,
            callback_data="match_random", # 💫
        )],
        [InlineKeyboardButton(
            text=InlineBtn.MATCH_BOY,
            callback_data="match_boy",# 🚹
        ),
        InlineKeyboardButton(
            text=InlineBtn.MATCH_GIRL,
            callback_data="match_girl",# 🚺
        )],
        [InlineKeyboardButton(
            text=InlineBtn.MATCH_NEARBY,
            callback_data="match_nearby",# 🗺
        )],
    ])

# --- Match Initialisation ---
def get_match_found_keyboard(partner_id: int, match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=InlineBtn.VIEW_PROFILE,
            callback_data=f"view_profile_{partner_id}", # 👤
        )],
        [InlineKeyboardButton(
            text=InlineBtn.END_DATE_EARLY,
            callback_data=f"end_date_{match_id}", # ❌
        )],
    ])

# --- Questionnaire ---
def get_question_reply_keyboard(question_id: int, is_four_choice: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=InlineBtn.OPTION_A, callback_data=f"ans_a_{question_id}"),
            InlineKeyboardButton(text=InlineBtn.OPTION_B, callback_data=f"ans_b_{question_id}"),
        ]
    ]
    if is_four_choice:
        rows.append([
            InlineKeyboardButton(text=InlineBtn.OPTION_C, callback_data=f"ans_c_{question_id}"),
            InlineKeyboardButton(text=InlineBtn.OPTION_D, callback_data=f"ans_d_{question_id}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_chat_approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=InlineBtn.APPROVE_CHAT_YES,
            callback_data="approve_chat_yes", # ✅
        )],
        [InlineKeyboardButton(
            text=InlineBtn.APPROVE_CHAT_NO,
            callback_data="approve_chat_no", # ❌
        )],
    ])

def get_active_chat_controls(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=InlineBtn.END_ACTIVE_CHAT,
            callback_data="end_active_chat", # ❌
        )],
        [InlineKeyboardButton(
            text=InlineBtn.REPORT_USER,
            # اصلاح شد: از trigger_report_ به report_user_ تغییر یافت
            callback_data=f"report_user_{target_id}" # ❗️
        )],
    ])
# --- Main Menu Sub-menus ---
def get_nearby_options_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.NEARBY_FEMALE, callback_data="nearby_female")], # 🚺
        [InlineKeyboardButton(text=InlineBtn.NEARBY_MALE, callback_data="nearby_male")], # 🚹
        [InlineKeyboardButton(text=InlineBtn.NEARBY_BOTH, callback_data="nearby_both")] # 👥
    ])

def get_search_options_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=InlineBtn.SEARCH_ONLINE_MALE, callback_data="search_online_male"),
            InlineKeyboardButton(text=InlineBtn.SEARCH_ONLINE_FEMALE, callback_data="search_online_female")
        ],
        [
            InlineKeyboardButton(text=InlineBtn.SEARCH_SAME_PROVINCE, callback_data="search_same_prov"), # 🏢
            InlineKeyboardButton(text=InlineBtn.SEARCH_SAME_CITY, callback_data="search_same_city") # 🏠
        ],
        [
            InlineKeyboardButton(text=InlineBtn.SEARCH_NO_CHAT, callback_data="search_no_chat") # 💬
        ],
    ])


def get_coins_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.COINS_HISTORY, callback_data="coins_history")], # ⏳
        [InlineKeyboardButton(text=InlineBtn.COINS_PURCHASE, callback_data="coins_purchase")], # 🪙
    ])

# --- VIP Panel ---
def get_vip_panel_keyboard(invisible_mode: bool) -> InlineKeyboardMarkup:
    invisible_text  = InlineBtn.VIP_INVISIBLE_ON if invisible_mode else InlineBtn.VIP_INVISIBLE_OFF
    invisible_style = "success" if invisible_mode else "danger"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.VIP_VIEWERS, callback_data="vip_viewers")], # 👀
        [InlineKeyboardButton(text=invisible_text, callback_data="vip_toggle_inv")], # 👻
        [InlineKeyboardButton(text=InlineBtn.VIP_REMATCH, callback_data="vip_rematch")], # 🔄
    ])

def get_vip_age_filter_keyboard(match_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_18_25, callback_data=f"vip_age_18_25_{match_type}")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_25_30, callback_data=f"vip_age_25_30_{match_type}")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_30_40, callback_data=f"vip_age_30_40_{match_type}")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_ALL, callback_data=f"vip_age_all_{match_type}")],
    ])

# ── Onboarding: terms acceptance ──
def get_terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.TERMS_SHOW_INLINE, callback_data="terms_show")], # 📖
        [InlineKeyboardButton(text=InlineBtn.TERMS_ACCEPT_INLINE, callback_data="terms_accept")] # ✅
    ])

# ── Double-confirms ──
def get_end_date_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.CONFIRM_END_DATE_YES, callback_data="confirm_end_date")], # ❌
        [InlineKeyboardButton(text=InlineBtn.CANCEL_RETURN, callback_data="cancel_end_date")] # ✅
    ])

def get_end_chat_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.CONFIRM_END_CHAT_YES, callback_data="confirm_end_chat")], # ❌
        [InlineKeyboardButton(text=InlineBtn.CANCEL_RETURN, callback_data="cancel_end_chat")] # ✅
    ])

# ── Other-user profile action keyboard ──
def get_user_action_keyboard(target_tg_id: int, is_blocked: bool = False, is_friend: bool = False) -> InlineKeyboardMarkup:
    block_text = InlineBtn.ACTION_UNBLOCK if is_blocked else InlineBtn.ACTION_BLOCK
    block_style = "success" if is_blocked else "danger"
    block_callback = f"unblock_user_{target_tg_id}" if is_blocked else f"block_user_{target_tg_id}"
    
    friend_text = "حذف از دوستان" if is_friend else InlineBtn.ACTION_ADD_FRIEND
    friend_style = "danger" if is_friend else "primary"
    friend_callback = f"remove_friend_{target_tg_id}" if is_friend else f"add_friend_{target_tg_id}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_DATE, callback_data=f"req_date_{target_tg_id}"), # ☕️
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_CHAT, callback_data=f"req_chat_{target_tg_id}") # 💬
        ],
        [
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_DIRECT, callback_data=f"req_direct_{target_tg_id}"), # 💌
            InlineKeyboardButton(text=InlineBtn.ACTION_TRANSFER_COIN, callback_data=f"transfer_coin_{target_tg_id}") # 💸
        ],
        [
            InlineKeyboardButton(text=friend_text, callback_data=friend_callback), # 🫂
            InlineKeyboardButton(text=InlineBtn.ACTION_LIKE, callback_data=f"like_user_{target_tg_id}") # ❤️
        ],
        [InlineKeyboardButton(text="💬 کامنت‌ها", callback_data=f"view_comments:{target_tg_id}:0")], # 💬
        [InlineKeyboardButton(text=block_text, callback_data=block_callback)], # 🔒
        [InlineKeyboardButton(text=InlineBtn.ACTION_REPORT, callback_data=f"report_user_{target_tg_id}")] # ❗️
    ])


# ── Report reasons ──
def get_report_reasons_keyboard(reported_tg_id: int) -> InlineKeyboardMarkup:
    reasons = [
        (InlineBtn.REPORT_INAPPROPRIATE_PHOTO, "inappropriate_photo"),
        (InlineBtn.REPORT_SCAMMER, "scammer"),
        (InlineBtn.REPORT_HARASSMENT, "harassment"),
        (InlineBtn.REPORT_SPAM, "spam"),
        (InlineBtn.REPORT_IMPERSONATION, "impersonation"),
        (InlineBtn.REPORT_SUSPICIOUS_LINK, "suspicious_link"),
        (InlineBtn.REPORT_ADULT_CONTENT, "adult_content"),
        (InlineBtn.REPORT_DRUGS, "drugs"),
        (InlineBtn.REPORT_BOT_FAKE, "bot_fake"),
        (InlineBtn.REPORT_OTHER, "other"),
    ]
    
    # 🟢 اطمینان از استفاده از پیشوند دقیق report_reason_
    keyboard = [
        [InlineKeyboardButton(text=label, callback_data=f"report_reason_{reported_tg_id}_{code}")] 
        for label, code in reasons
    ]
    keyboard.append([InlineKeyboardButton(text=InlineBtn.REPORT_CANCEL, callback_data="report_cancel")]) 
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ── Discovery ──
def get_discovery_age_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_18_25, callback_data="disc_age_18_25")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_25_30, callback_data="disc_age_25_30")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_30_40, callback_data="disc_age_30_40")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_40_50, callback_data="disc_age_40_50")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_ALL, callback_data="disc_age_all")]
    ])

def get_discovery_interests_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    interests = {
        "gaming": (InlineBtn.INT_GAMING, "5467583879948803288"),   # 🎮
        "music": (InlineBtn.INT_MUSIC, "5188621441926438751"),     # 🎵
        "travel": (InlineBtn.INT_TRAVEL, "5361600266225326825"),   # ✈️
        "movies": (InlineBtn.INT_MOVIES, "5375464961822695044"),   # 🎬
        "sports": (InlineBtn.INT_SPORTS, "5373101763442255191"),   # ⚽️
        "reading": (InlineBtn.INT_READING, "5373098009640836781"), # 📚
        "cooking": (InlineBtn.INT_COOKING, "5388747006451655179"), # 🍳
        "art": (InlineBtn.INT_ART, "5431456208487716895"),         # 🎨
        "tech": (InlineBtn.INT_TECH, "5431376038628171216"),       # 💻
        "nature": (InlineBtn.INT_NATURE, "5449523005598210324"),   # 🌲
    }
    
    keyboard = []
    keys = list(interests.keys())
    for i in range(0, len(keys), 2):
        row = []
        for j in range(2):
            if i + j < len(keys):
                k = keys[i + j]
                label_text, emoji_id = interests[k]
                label = f"{label_text} ✅" if k in selected else label_text
                style = "success" if k in selected else "primary"
                row.append(InlineKeyboardButton(text=label, callback_data=f"disc_int_{k}"))
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton(text=InlineBtn.DISC_CONFIRM, callback_data="disc_int_confirm")]) # ✅
    return InlineKeyboardMarkup(inline_keyboard=keyboard)