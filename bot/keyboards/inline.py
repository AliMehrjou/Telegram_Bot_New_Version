from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from matching_bot_project.bot.core.constants import InlineBtn

# --- Onboarding ---
def get_gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=InlineBtn.GENDER_MALE, callback_data="gender_male", icon_custom_emoji_id="5429564911048992647", style="primary"),
            InlineKeyboardButton(text=InlineBtn.GENDER_FEMALE, callback_data="gender_female", icon_custom_emoji_id="5429474729620677471", style="primary")
        ]
    ])

# --- Matching Menu ---
def get_matching_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.MATCH_RANDOM, callback_data="match_random", icon_custom_emoji_id="5361837567463399422", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.MATCH_BOY, callback_data="match_boy", icon_custom_emoji_id="5429564911048992647", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.MATCH_GIRL, callback_data="match_girl", icon_custom_emoji_id="5429474729620677471", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.MATCH_NEARBY, callback_data="match_nearby", icon_custom_emoji_id="5415803062738504079", style="primary")]
    ])

# --- Match Initialisation (5-Second Delay) ---
def get_match_found_keyboard(partner_id: int, match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.VIEW_PROFILE, callback_data=f"view_profile_{partner_id}", icon_custom_emoji_id="5373012449597335010", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.END_DATE_EARLY, callback_data=f"end_date_early_{match_id}", icon_custom_emoji_id="5465665476971471368", style="danger")]
    ])

# --- Questionnaire ---
def get_question_reply_keyboard(question_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=InlineBtn.OPTION_A, callback_data=f"ans_a_{question_id}", icon_custom_emoji_id="5472146462362048818"),
            InlineKeyboardButton(text=InlineBtn.OPTION_B, callback_data=f"ans_b_{question_id}", icon_custom_emoji_id="5472146462362048818")
        ],
        [
            InlineKeyboardButton(text=InlineBtn.OPTION_C, callback_data=f"ans_c_{question_id}", icon_custom_emoji_id="5472146462362048818"),
            InlineKeyboardButton(text=InlineBtn.OPTION_D, callback_data=f"ans_d_{question_id}", icon_custom_emoji_id="5472146462362048818")
        ]
    ])

def get_chat_approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.APPROVE_CHAT_YES, callback_data="approve_chat_yes", icon_custom_emoji_id="5427009714745517609", style="success")],
        [InlineKeyboardButton(text=InlineBtn.APPROVE_CHAT_NO, callback_data="approve_chat_no", icon_custom_emoji_id="5465665476971471368", style="danger")]
    ])

def get_active_chat_controls(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.END_ACTIVE_CHAT, callback_data="end_active_chat", icon_custom_emoji_id="5465665476971471368", style="danger")],
        [InlineKeyboardButton(text=InlineBtn.REPORT_USER, callback_data=f"trigger_report_{target_id}", icon_custom_emoji_id="5411175424455613715", style="danger")]
    ])

# --- Main Menu Sub-menus (Search & Explore) ---
def get_nearby_options_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.NEARBY_FEMALE, callback_data="nearby_female", icon_custom_emoji_id="5429474729620677471", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.NEARBY_MALE, callback_data="nearby_male", icon_custom_emoji_id="5429564911048992647", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.NEARBY_BOTH, callback_data="nearby_both", icon_custom_emoji_id="5372926953978341366", style="primary")]
    ])

def get_search_options_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.SEARCH_ONLINE_MALE, callback_data="search_online_male", icon_custom_emoji_id="5429564911048992647", style="success")],
        [InlineKeyboardButton(text=InlineBtn.SEARCH_ONLINE_FEMALE, callback_data="search_online_female", icon_custom_emoji_id="5429474729620677471", style="success")],
        [InlineKeyboardButton(text=InlineBtn.SEARCH_SAME_PROVINCE, callback_data="search_same_province", icon_custom_emoji_id="5415803062738504079", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.SEARCH_SAME_CITY, callback_data="search_same_city", icon_custom_emoji_id="5264733042710181045", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.SEARCH_NO_CHAT, callback_data="search_no_chat", icon_custom_emoji_id="5465300082628763143", style="primary")]
    ])

def get_coins_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.COINS_HISTORY, callback_data="coins_history", icon_custom_emoji_id="5334882760735598374")],
        [InlineKeyboardButton(text=InlineBtn.COINS_PURCHASE, callback_data="coins_purchase", icon_custom_emoji_id="5471952986970267163", style="primary")]
    ])

# --- VIP Panel ---
def get_vip_panel_keyboard(invisible_mode: bool) -> InlineKeyboardMarkup:
    invisible_text = InlineBtn.VIP_INVISIBLE_ON if invisible_mode else InlineBtn.VIP_INVISIBLE_OFF
    status_icon_id = "5427009714745517609" if invisible_mode else "5465665476971471368"
    status_style = "success" if invisible_mode else "danger"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.VIP_VIEWERS, callback_data="vip_viewers", icon_custom_emoji_id="5424885441100782420", style="primary")],
        [InlineKeyboardButton(text=invisible_text, callback_data="vip_toggle_invisible", icon_custom_emoji_id=status_icon_id, style=status_style)],
        [InlineKeyboardButton(text=InlineBtn.VIP_REMATCH, callback_data="vip_rematch", icon_custom_emoji_id="5264727218734524899", style="primary")]
    ])

def get_vip_age_filter_keyboard(match_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_18_25, callback_data=f"vip_age_filter_18_25_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_25_30, callback_data=f"vip_age_filter_25_30_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_30_40, callback_data=f"vip_age_filter_30_40_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_ALL, callback_data=f"vip_age_filter_0_99_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")]
    ])

# ── Onboarding: terms acceptance ───────────────────────────────────────────
def get_terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.TERMS_SHOW_INLINE, callback_data="terms_show", icon_custom_emoji_id="5334882760735598374")],
        [InlineKeyboardButton(text=InlineBtn.TERMS_ACCEPT_INLINE, callback_data="terms_accept", icon_custom_emoji_id="5427009714745517609", style="success")]
    ])

# ── Double-confirms ────────────────────────────────────────
def get_end_date_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.CONFIRM_END_DATE_YES, callback_data="confirm_end_date", icon_custom_emoji_id="5427009714745517609", style="success")],
        [InlineKeyboardButton(text=InlineBtn.CANCEL_RETURN, callback_data="cancel_end_date", icon_custom_emoji_id="5465665476971471368", style="danger")]
    ])

def get_end_chat_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.CONFIRM_END_CHAT_YES, callback_data="confirm_end_chat", icon_custom_emoji_id="5427009714745517609", style="success")],
        [InlineKeyboardButton(text=InlineBtn.CANCEL_RETURN, callback_data="cancel_end_chat", icon_custom_emoji_id="5465665476971471368", style="danger")]
    ])

# ── Other-user profile action keyboard ─────────────────────────────────────
def get_user_action_keyboard(target_tg_id: int, is_blocked: bool = False, is_friend: bool = False) -> InlineKeyboardMarkup:
    block_text = InlineBtn.ACTION_UNBLOCK if is_blocked else InlineBtn.ACTION_BLOCK
    block_cb   = f"unblock_user_{target_tg_id}" if is_blocked else f"block_user_{target_tg_id}"
    block_emoji_id = "5330115548900501467" if is_blocked else "5472308992514464048"
    
    if is_friend:
        friend_button = InlineKeyboardButton(text="حذف از دوستان", callback_data=f"remove_friend_{target_tg_id}", icon_custom_emoji_id="5465665476971471368", style="danger")
    else:
        friend_button = InlineKeyboardButton(text=InlineBtn.ACTION_ADD_FRIEND, callback_data=f"add_friend_{target_tg_id}", icon_custom_emoji_id="5372926953978341366", style="primary")

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_DATE, callback_data=f"req_date_{target_tg_id}", icon_custom_emoji_id="5452140079495518256", style="success"),
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_CHAT, callback_data=f"req_chat_{target_tg_id}", icon_custom_emoji_id="5465300082628763143", style="success")
        ],
        [
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_DIRECT, callback_data=f"req_direct_{target_tg_id}", icon_custom_emoji_id="5472019095106886003", style="primary"),
            InlineKeyboardButton(text=InlineBtn.ACTION_TRANSFER_COIN, callback_data=f"transfer_coin_{target_tg_id}", icon_custom_emoji_id="5471899089425667918", style="primary")
        ],
        [
            friend_button,
            InlineKeyboardButton(text=InlineBtn.ACTION_LIKE, callback_data=f"like_user_{target_tg_id}", icon_custom_emoji_id="5449505950283078474", style="primary")
        ],
        [InlineKeyboardButton(text=block_text, callback_data=block_cb, icon_custom_emoji_id=block_emoji_id, style="danger")],
        [InlineKeyboardButton(text=InlineBtn.ACTION_REPORT, callback_data=f"report_user_{target_tg_id}", icon_custom_emoji_id="5411175424455613715", style="danger")]
    ])

# ── Report reasons ──────────────────────
def get_report_reasons_keyboard(reported_tg_id: int) -> InlineKeyboardMarkup:
    reasons = [
        (InlineBtn.REPORT_INAPPROPRIATE_PHOTO, "inappropriate_photo", "5375074927252621134"),
        (InlineBtn.REPORT_SCAMMER,             "scammer", "5472030678633684592"),
        (InlineBtn.REPORT_HARASSMENT,          "harassment", "5373123633415723713"),
        (InlineBtn.REPORT_SPAM,                "spam", "5469903029144657419"),
        (InlineBtn.REPORT_IMPERSONATION,       "impersonation", "5373012449597335010"),
        (InlineBtn.REPORT_SUSPICIOUS_LINK,     "suspicious_link", "5375129357373165375"),
        (InlineBtn.REPORT_ADULT_CONTENT,       "adult_content", "5422542669584800702"),
        (InlineBtn.REPORT_DRUGS,               "drugs", "5433635625217563352"),
        (InlineBtn.REPORT_BOT_FAKE,            "bot_fake", "5372981976804366741"),
        (InlineBtn.REPORT_OTHER,               "other", "5467666648263564704"),
    ]
    keyboard = [
        [InlineKeyboardButton(
            text=label,
            callback_data=f"report_reason_{reported_tg_id}_{code}",
            icon_custom_emoji_id=eid
        )]
        for label, code, eid in reasons
    ]
    keyboard.append(
        [InlineKeyboardButton(text=InlineBtn.REPORT_CANCEL, callback_data="report_cancel", icon_custom_emoji_id="5465665476971471368", style="danger")]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ── Discovery ─────────────────────────────────────────
def get_discovery_age_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_18_25, callback_data="disc_age_18_25", icon_custom_emoji_id="5451732530048802485")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_25_30, callback_data="disc_age_25_30", icon_custom_emoji_id="5451732530048802485")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_30_40, callback_data="disc_age_30_40", icon_custom_emoji_id="5451732530048802485")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_40_50, callback_data="disc_age_40_50", icon_custom_emoji_id="5451732530048802485")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_ALL, callback_data="disc_age_0_99", icon_custom_emoji_id="5451732530048802485")]
    ])

def get_discovery_interests_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    interests = {
        "gaming":  (InlineBtn.INT_GAMING, "5467583879948803288"),  
        "music":   (InlineBtn.INT_MUSIC, "5188621441926438751"),
        "travel":  (InlineBtn.INT_TRAVEL, "5361600266225326825"),  
        "movies":  (InlineBtn.INT_MOVIES, "5375464961822695044"),
        "sports":  (InlineBtn.INT_SPORTS, "5373101763442255191"),  
        "reading": (InlineBtn.INT_READING, "5373098009640836781"),
        "cooking": (InlineBtn.INT_COOKING, "5388747006451655179"), 
        "art":     (InlineBtn.INT_ART, "5431456208487716895"),
        "tech":    (InlineBtn.INT_TECH, "5431376038628171216"),    
        "nature":  (InlineBtn.INT_NATURE, "5449850741667668411"),
    }
    keyboard = []
    keys = list(interests.keys())
    for i in range(0, len(keys), 2):
        row = []
        for j in range(2):
            if i + j < len(keys):
                k = keys[i + j]
                label_text, e_id = interests[k]
                label = label_text + (" ✅" if k in selected else "")
                row.append(InlineKeyboardButton(text=label, callback_data=f"disc_int_{k}", icon_custom_emoji_id=e_id))
        keyboard.append(row)
    keyboard.append(
        [InlineKeyboardButton(text=InlineBtn.DISC_CONFIRM, callback_data="disc_int_confirm", icon_custom_emoji_id="5427009714745517609", style="success")]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)