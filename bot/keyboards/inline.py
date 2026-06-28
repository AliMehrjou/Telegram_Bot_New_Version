from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from matching_bot_project.bot.core.constants import InlineBtn
from matching_bot_project.database.models.models import CoinPackage

def get_coin_packages_keyboard(packages: list[CoinPackage]) -> InlineKeyboardMarkup:
    kb = []
    for pkg in packages:
        text = f"{pkg.coin_amount} سکه — {pkg.price_toman:,} تومان"
        kb.append([InlineKeyboardButton(text=text, callback_data=f"buy_package_{pkg.id}", icon_custom_emoji_id="5379600444098093058", style="primary")])
    kb.append([InlineKeyboardButton(text="انصراف", callback_data="close_menu", icon_custom_emoji_id="5465665476971471368", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_payment_method_keyboard(gateway_enabled: bool) -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(text="کارت به کارت (آفلاین)", callback_data="pay_method_card", icon_custom_emoji_id="5472030678633684592", style="primary")]]
    if gateway_enabled:
        kb.append([InlineKeyboardButton(text="پرداخت آنلاین (درگاه)", callback_data="pay_method_gateway", icon_custom_emoji_id="5375129357373165375", style="primary")])
    kb.append([InlineKeyboardButton(text="انصراف", callback_data="cancel_payment", icon_custom_emoji_id="5465665476971471368", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_admin_receipt_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="تأیید واریز", callback_data=f"verify_receipt_{order_id}", icon_custom_emoji_id="5427009714745517609", style="success"),
            InlineKeyboardButton(text="رد کردن", callback_data=f"reject_receipt_{order_id}", icon_custom_emoji_id="5465665476971471368", style="danger")
        ]
    ])

def get_gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=InlineBtn.GENDER_MALE, callback_data="set_gender_male", icon_custom_emoji_id="5429564911048992647", style="primary"),
            InlineKeyboardButton(text=InlineBtn.GENDER_FEMALE, callback_data="set_gender_female", icon_custom_emoji_id="5429474729620677471", style="primary"),
        ]
    ])

def get_matching_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.MATCH_RANDOM, callback_data="match_random", icon_custom_emoji_id="5469741319330996757", style="primary")],
        [
            InlineKeyboardButton(text=InlineBtn.MATCH_BOY, callback_data="match_boy", icon_custom_emoji_id="5429564911048992647", style="primary"),
            InlineKeyboardButton(text=InlineBtn.MATCH_GIRL, callback_data="match_girl", icon_custom_emoji_id="5429474729620677471", style="primary")
        ],
        [InlineKeyboardButton(text=InlineBtn.MATCH_NEARBY, callback_data="match_nearby", icon_custom_emoji_id="5415803062738504079", style="primary")],
    ])

def get_match_found_keyboard(partner_id: int, match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.VIEW_PROFILE, callback_data=f"view_profile_{partner_id}", icon_custom_emoji_id="5373012449597335010", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.END_DATE_EARLY, callback_data=f"end_date_{match_id}", icon_custom_emoji_id="5465665476971471368", style="danger")],
    ])

def get_question_reply_keyboard(question_id: int, is_four_choice: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=InlineBtn.OPTION_A, callback_data=f"ans_a_{question_id}", icon_custom_emoji_id="5188216731453103384", style="primary"),
            InlineKeyboardButton(text=InlineBtn.OPTION_B, callback_data=f"ans_b_{question_id}", icon_custom_emoji_id="5188216731453103384", style="primary"),
        ]
    ]
    if is_four_choice:
        rows.append([
            InlineKeyboardButton(text=InlineBtn.OPTION_C, callback_data=f"ans_c_{question_id}", icon_custom_emoji_id="5188216731453103384", style="primary"),
            InlineKeyboardButton(text=InlineBtn.OPTION_D, callback_data=f"ans_d_{question_id}", icon_custom_emoji_id="5188216731453103384", style="primary"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_chat_approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.APPROVE_CHAT_YES, callback_data="approve_chat_yes", icon_custom_emoji_id="5427009714745517609", style="success")],
        [InlineKeyboardButton(text=InlineBtn.APPROVE_CHAT_NO, callback_data="approve_chat_no", icon_custom_emoji_id="5465665476971471368", style="danger")],
    ])

def get_active_chat_controls(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.END_ACTIVE_CHAT, callback_data="end_active_chat", icon_custom_emoji_id="5465665476971471368", style="danger")],
        [InlineKeyboardButton(text=InlineBtn.REPORT_USER, callback_data=f"report_user_{target_id}", icon_custom_emoji_id="5467928559664242360", style="danger")],
    ])

def get_nearby_options_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.NEARBY_FEMALE, callback_data="nearby_female", icon_custom_emoji_id="5429474729620677471", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.NEARBY_MALE, callback_data="nearby_male", icon_custom_emoji_id="5429564911048992647", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.NEARBY_BOTH, callback_data="nearby_both", icon_custom_emoji_id="5372926953978341366", style="primary")]
    ])

def get_search_options_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=InlineBtn.SEARCH_ONLINE_MALE, callback_data="search_online_male", icon_custom_emoji_id="5429564911048992647", style="primary"),
            InlineKeyboardButton(text=InlineBtn.SEARCH_ONLINE_FEMALE, callback_data="search_online_female", icon_custom_emoji_id="5429474729620677471", style="primary")
        ],
        [
            InlineKeyboardButton(text=InlineBtn.SEARCH_SAME_PROVINCE, callback_data="search_same_prov", icon_custom_emoji_id="5264733042710181045", style="primary"),
            InlineKeyboardButton(text=InlineBtn.SEARCH_SAME_CITY, callback_data="search_same_city", icon_custom_emoji_id="5465226866321268133", style="primary")
        ],
        [
            InlineKeyboardButton(text=InlineBtn.SEARCH_NO_CHAT, callback_data="search_no_chat", icon_custom_emoji_id="5465300082628763143", style="primary")
        ],
    ])

def get_coins_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.COINS_HISTORY, callback_data="coins_history", icon_custom_emoji_id="5451732530048802485", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.COINS_PURCHASE, callback_data="coins_purchase", icon_custom_emoji_id="5379600444098093058", style="success")],
    ])

def get_vip_panel_keyboard(invisible_mode: bool) -> InlineKeyboardMarkup:
    invisible_text  = InlineBtn.VIP_INVISIBLE_ON if invisible_mode else InlineBtn.VIP_INVISIBLE_OFF
    invisible_style = "success" if invisible_mode else "danger"
    invisible_emoji = "5371017798065592581"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.VIP_VIEWERS, callback_data="vip_viewers", icon_custom_emoji_id="5424885441100782420", style="primary")],
        [InlineKeyboardButton(text=invisible_text, callback_data="vip_toggle_inv", icon_custom_emoji_id=invisible_emoji, style=invisible_style)],
        [InlineKeyboardButton(text=InlineBtn.VIP_REMATCH, callback_data="vip_rematch", icon_custom_emoji_id="5264727218734524899", style="primary")],
    ])

def get_vip_age_filter_keyboard(match_type: str) -> InlineKeyboardMarkup:
    calendar_emoji = "5431897022456145283"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_18_25, callback_data=f"vip_age_18_25_{match_type}", icon_custom_emoji_id=calendar_emoji, style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_25_30, callback_data=f"vip_age_25_30_{match_type}", icon_custom_emoji_id=calendar_emoji, style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_30_40, callback_data=f"vip_age_30_40_{match_type}", icon_custom_emoji_id=calendar_emoji, style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_ALL, callback_data=f"vip_age_all_{match_type}", icon_custom_emoji_id=calendar_emoji, style="primary")],
    ])

def get_terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.TERMS_SHOW_INLINE, callback_data="terms_show", icon_custom_emoji_id="5226512880362332956", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.TERMS_ACCEPT_INLINE, callback_data="terms_accept", icon_custom_emoji_id="5427009714745517609", style="success")]
    ])

def get_end_date_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.CONFIRM_END_DATE_YES, callback_data="confirm_end_date", icon_custom_emoji_id="5465665476971471368", style="danger")],
        [InlineKeyboardButton(text=InlineBtn.CANCEL_RETURN, callback_data="cancel_end_date", icon_custom_emoji_id="5427009714745517609", style="success")]
    ])

def get_end_chat_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.CONFIRM_END_CHAT_YES, callback_data="confirm_end_chat", icon_custom_emoji_id="5465665476971471368", style="danger")],
        [InlineKeyboardButton(text=InlineBtn.CANCEL_RETURN, callback_data="cancel_end_chat", icon_custom_emoji_id="5427009714745517609", style="success")]
    ])

def get_user_action_keyboard(target_tg_id: int, is_blocked: bool = False, is_friend: bool = False) -> InlineKeyboardMarkup:
    block_text = InlineBtn.ACTION_UNBLOCK if is_blocked else InlineBtn.ACTION_BLOCK
    block_style = "success" if is_blocked else "danger"
    block_callback = f"unblock_user_{target_tg_id}" if is_blocked else f"block_user_{target_tg_id}"
    
    friend_text = "حذف از دوستان" if is_friend else InlineBtn.ACTION_ADD_FRIEND
    friend_style = "danger" if is_friend else "primary"
    friend_callback = f"remove_friend_{target_tg_id}" if is_friend else f"add_friend_{target_tg_id}"
    friend_emoji = "5471954395719539651" if is_friend else "5370867268051806190"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_DATE, callback_data=f"req_date_{target_tg_id}", icon_custom_emoji_id="5359370246190801956", style="primary"),
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_CHAT, callback_data=f"req_chat_{target_tg_id}", icon_custom_emoji_id="5465300082628763143", style="primary")
        ],
        [
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_DIRECT, callback_data=f"req_direct_{target_tg_id}", icon_custom_emoji_id="5472019095106886003", style="primary"),
            InlineKeyboardButton(text=InlineBtn.ACTION_TRANSFER_COIN, callback_data=f"transfer_coin_{target_tg_id}", icon_custom_emoji_id="5472030678633684592", style="primary")
        ],
        [
            InlineKeyboardButton(text=friend_text, callback_data=friend_callback, icon_custom_emoji_id=friend_emoji, style=friend_style),
            InlineKeyboardButton(text=InlineBtn.ACTION_LIKE, callback_data=f"like_user_{target_tg_id}", icon_custom_emoji_id="5449505950283078474", style="primary")
        ],
        [InlineKeyboardButton(text="کامنت‌ها", callback_data=f"view_comments:{target_tg_id}:0", icon_custom_emoji_id="5465300082628763143", style="primary")],
        [InlineKeyboardButton(text=block_text, callback_data=block_callback, icon_custom_emoji_id="5472308992514464048", style=block_style)],
        [InlineKeyboardButton(text=InlineBtn.ACTION_REPORT, callback_data=f"report_user_{target_tg_id}", icon_custom_emoji_id="5467928559664242360", style="danger")]
    ])

def get_report_reasons_keyboard(reported_tg_id: int) -> InlineKeyboardMarkup:
    reasons = [
        (InlineBtn.REPORT_INAPPROPRIATE_PHOTO, "inappropriate_photo", "5422542669584800702"),
        (InlineBtn.REPORT_SCAMMER, "scammer", "5373069598432172355"),
        (InlineBtn.REPORT_HARASSMENT, "harassment", "5373123633415723713"),
        (InlineBtn.REPORT_SPAM, "spam", "5264727218734524899"),
        (InlineBtn.REPORT_IMPERSONATION, "impersonation", "5359441070201513074"),
        (InlineBtn.REPORT_SUSPICIOUS_LINK, "suspicious_link", "5375129357373165375"),
        (InlineBtn.REPORT_ADULT_CONTENT, "adult_content", "5422542669584800702"),
        (InlineBtn.REPORT_DRUGS, "drugs", "5433635625217563352"),
        (InlineBtn.REPORT_BOT_FAKE, "bot_fake", "5372981976804366741"),
        (InlineBtn.REPORT_OTHER, "other", "5467666648263564704"),
    ]
    
    keyboard = [
        [InlineKeyboardButton(text=label, callback_data=f"report_reason_{reported_tg_id}_{code}", icon_custom_emoji_id=emoji_id, style="primary")] 
        for label, code, emoji_id in reasons
    ]
    keyboard.append([InlineKeyboardButton(text=InlineBtn.REPORT_CANCEL, callback_data="report_cancel", icon_custom_emoji_id="5465665476971471368", style="danger")]) 
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_discovery_age_keyboard() -> InlineKeyboardMarkup:
    calendar_emoji = "5431897022456145283"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_18_25, callback_data="disc_age_18_25", icon_custom_emoji_id=calendar_emoji, style="primary")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_25_30, callback_data="disc_age_25_30", icon_custom_emoji_id=calendar_emoji, style="primary")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_30_40, callback_data="disc_age_30_40", icon_custom_emoji_id=calendar_emoji, style="primary")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_40_50, callback_data="disc_age_40_50", icon_custom_emoji_id=calendar_emoji, style="primary")],
        [InlineKeyboardButton(text=InlineBtn.DISC_AGE_ALL, callback_data="disc_age_all", icon_custom_emoji_id=calendar_emoji, style="primary")]
    ])

def get_discovery_interests_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    interests = {
        "gaming": (InlineBtn.INT_GAMING, "5467583879948803288"),
        "music": (InlineBtn.INT_MUSIC, "5188621441926438751"),
        "travel": (InlineBtn.INT_TRAVEL, "5361600266225326825"),
        "movies": (InlineBtn.INT_MOVIES, "5375464961822695044"),
        "sports": (InlineBtn.INT_SPORTS, "5373101763442255191"),
        "reading": (InlineBtn.INT_READING, "5373098009640836781"),
        "cooking": (InlineBtn.INT_COOKING, "5388747006451655179"),
        "art": (InlineBtn.INT_ART, "5431456208487716895"),
        "tech": (InlineBtn.INT_TECH, "5431376038628171216"),
        "nature": (InlineBtn.INT_NATURE, "5449523005598210324"),
    }
    
    keyboard = []
    keys = list(interests.keys())
    for i in range(0, len(keys), 2):
        row = []
        for j in range(2):
            if i + j < len(keys):
                k = keys[i + j]
                label_text, emoji_id = interests[k]
                label = f"{label_text}"
                style = "success" if k in selected else "primary"
                row.append(InlineKeyboardButton(text=label, callback_data=f"disc_int_{k}", icon_custom_emoji_id=emoji_id, style=style))
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton(text=InlineBtn.DISC_CONFIRM, callback_data="disc_int_confirm", icon_custom_emoji_id="5427009714745517609", style="success")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)