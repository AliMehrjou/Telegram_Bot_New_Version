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
        [InlineKeyboardButton(text=InlineBtn.MATCH_SAME_AGE, callback_data="match_same_age", icon_custom_emoji_id="5451732530048802485", style="primary")], # دکمه اضافه شده
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
def get_question_reply_keyboard(question_id: int, is_four_choice: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text=InlineBtn.OPTION_A, callback_data=f"ans_a_{question_id}", icon_custom_emoji_id="5472146462362048818"),
            InlineKeyboardButton(text=InlineBtn.OPTION_B, callback_data=f"ans_b_{question_id}", icon_custom_emoji_id="5472146462362048818")
        ]
    ]
    # ✅ FIX: پارامتر گم‌شده که questionnaire.py صداش می‌زد ولی اینجا تعریف نشده بود
    if is_four_choice:
        keyboard.append([
            InlineKeyboardButton(text=InlineBtn.OPTION_C, callback_data=f"ans_c_{question_id}", icon_custom_emoji_id="5472146462362048818"),
            InlineKeyboardButton(text=InlineBtn.OPTION_D, callback_data=f"ans_d_{question_id}", icon_custom_emoji_id="5472146462362048818")
        ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_chat_approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.APPROVE_CHAT_YES, callback_data="approve_chat_yes", icon_custom_emoji_id="5427009714745517609", style="success")],
        [InlineKeyboardButton(text=InlineBtn.APPROVE_CHAT_NO, callback_data="approve_chat_no", icon_custom_emoji_id="5465665476971471368", style="danger")]
    ])

def get_active_chat_controls(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.END_ACTIVE_CHAT, callback_data="end_active_chat", icon_custom_emoji_id="5465665476971471368", style="danger")],
        [InlineKeyboardButton(text=InlineBtn.REPORT_USER, callback_data=f"report_user_{target_id}", icon_custom_emoji_id="5411175424455613715", style="danger")]
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

# --- Coin Store (payments.py) ─────────────────────────────────────────────
# ✅ FIX: این سه تابع قبلاً توی این فایل تعریف نشده بودن ولی payments.py
# ایمپورتشون می‌کرد → کل روتر payments_handler موقع ایمپورت کرش می‌کرد.
def get_coin_packages_keyboard(packages: list) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(
            text=f"💰 {package.coin_amount} سکه — {package.price_toman:,} تومان",
            callback_data=f"buy_package_{package.id}",
            icon_custom_emoji_id="5471952986970267163",
            style="primary",
        )]
        for package in packages
    ]
    keyboard.append(
        [InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_payment", icon_custom_emoji_id="5465665476971471368", style="danger")]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_payment_method_keyboard(gateway_enabled: bool) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="💳 کارت به کارت", callback_data="pay_method_card", icon_custom_emoji_id="5472019095106886003", style="primary")]
    ]
    if gateway_enabled:
        keyboard.append(
            [InlineKeyboardButton(text="🔗 پرداخت آنلاین (درگاه)", callback_data="pay_method_gateway", icon_custom_emoji_id="5471952986970267163", style="primary")]
        )
    keyboard.append(
        [InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_payment", icon_custom_emoji_id="5465665476971471368", style="danger")]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_admin_receipt_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تأیید", callback_data=f"verify_receipt_{order_id}", icon_custom_emoji_id="5427009714745517609", style="success"),
            InlineKeyboardButton(text="❌ رد", callback_data=f"reject_receipt_{order_id}", icon_custom_emoji_id="5465665476971471368", style="danger"),
        ]
    ])

# --- VIP Panel ---
def get_vip_panel_keyboard(invisible_mode: bool) -> InlineKeyboardMarkup:
    invisible_text = InlineBtn.VIP_INVISIBLE_ON if invisible_mode else InlineBtn.VIP_INVISIBLE_OFF
    status_icon_id = "5427009714745517609" if invisible_mode else "5465665476971471368"
    status_style = "success" if invisible_mode else "danger"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.VIP_VIEWERS, callback_data="vip_viewers", icon_custom_emoji_id="5424885441100782420", style="primary")],
        [InlineKeyboardButton(text=invisible_text, callback_data="vip_toggle_invisible", icon_custom_emoji_id=status_icon_id, style=status_style)],
        [InlineKeyboardButton(text=InlineBtn.VIP_REMATCH, callback_data="vip_rematch", icon_custom_emoji_id="5264727218734524899", style="primary")],
        [InlineKeyboardButton(text="❌ بستن", callback_data="close_vip_panel")]
    ])

def get_vip_age_filter_keyboard(match_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_18_25, callback_data=f"vip_age_filter_18_25_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_25_30, callback_data=f"vip_age_filter_25_30_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_30_40, callback_data=f"vip_age_filter_30_40_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_ALL, callback_data=f"vip_age_filter_0_99_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")],
        # 👈 فیکس: تغییر callback_data برای مدیریت صحیح لغو
        [InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_vip_filter", icon_custom_emoji_id="5465665476971471368", style="danger")]
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
def get_user_action_keyboard(
    target_tg_id: int,
    is_blocked: bool = False,
    is_friend: bool = False,
    in_active_match: bool = False,
) -> InlineKeyboardMarkup:
    
    block_text = InlineBtn.ACTION_UNBLOCK if is_blocked else InlineBtn.ACTION_BLOCK
    block_cb   = f"unblock_user_{target_tg_id}" if is_blocked else f"block_user_{target_tg_id}"
    block_emoji_id = "5330115548900501467" if is_blocked else "5472308992514464048"
    
    if is_friend:
        friend_button = InlineKeyboardButton(text="حذف از دوستان", callback_data=f"remove_friend_{target_tg_id}", icon_custom_emoji_id="5465665476971471368", style="danger")
    else:
        friend_button = InlineKeyboardButton(text=InlineBtn.ACTION_ADD_FRIEND, callback_data=f"add_friend_{target_tg_id}", icon_custom_emoji_id="5372926953978341366", style="primary")

    rows: list[list[InlineKeyboardButton]] = []

    if not in_active_match:
        rows.append([
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_DATE, callback_data=f"req_date_{target_tg_id}", icon_custom_emoji_id="5452140079495518256", style="success"),
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_CHAT, callback_data=f"req_chat_{target_tg_id}", icon_custom_emoji_id="5465300082628763143", style="success")
        ])
        # FIX BUGS-1: Added "🪙 انتقال سکه" button to the same row as Direct Message and Send Gift
        rows.append([
            InlineKeyboardButton(text=InlineBtn.ACTION_REQ_DIRECT, callback_data=f"req_direct_{target_tg_id}", icon_custom_emoji_id="5472019095106886003", style="primary"),
            InlineKeyboardButton(text="🎁 ارسال گیفت", callback_data=f"gift_send_direct_{target_tg_id}", icon_custom_emoji_id="5199749070830197566", style="success"),
            InlineKeyboardButton(text="🪙 انتقال سکه", callback_data=f"transfer_coin_{target_tg_id}", icon_custom_emoji_id="5471899089425667918", style="primary")
        ])

    rows.append([
        friend_button,
        InlineKeyboardButton(text=InlineBtn.ACTION_LIKE, callback_data=f"like_user_{target_tg_id}", icon_custom_emoji_id="5449505950283078474", style="primary")
    ])
    rows.append([
        InlineKeyboardButton(text="💬 مشاهده نظرات", callback_data=f"view_comments:{target_tg_id}:0")
    ])
    rows.append([InlineKeyboardButton(text=block_text, callback_data=block_cb, icon_custom_emoji_id=block_emoji_id, style="danger")])
    rows.append([InlineKeyboardButton(text=InlineBtn.ACTION_REPORT, callback_data=f"report_user_{target_tg_id}", icon_custom_emoji_id="5411175424455613715", style="danger")])

    return InlineKeyboardMarkup(inline_keyboard=rows)

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

# ═══════════════════════════════════════════════════════════════════════════
# v3 NEW: VIP subscription, Gifts, Distance filter, Coins menu, Help, Referral,
#         Direct messages, Profile completion, Tag selection
# ═══════════════════════════════════════════════════════════════════════════

def get_vip_subscription_plans_keyboard(plans: dict) -> InlineKeyboardMarkup:
    keyboard = []
    for code, plan in plans.items():
        # ساخت برچسب دکمه به صورت داینامیک: مثلاً "👑 1 ماهه - 100,000 ت"
        label = f"👑 {plan.get('label', code)} - {plan.get('price_toman', 0):,} ت"
        keyboard.append([
            InlineKeyboardButton(
                text=label, 
                callback_data=f"vip_buy_{code}",
                icon_custom_emoji_id="5467406098367521267", 
                style="primary"
            )
        ])
        
    keyboard.append([
        InlineKeyboardButton(
            text="❌ انصراف", 
            callback_data="vip_buy_cancel",
            icon_custom_emoji_id="5465665476971471368", 
            style="danger"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ── Distance filter ─────────────────────────────────────────────────────────
def get_distance_filter_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.DIST_NEAR, callback_data="dist_0_50",
                              icon_custom_emoji_id="5415803062738504079", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.DIST_MEDIUM, callback_data="dist_50_100",
                              icon_custom_emoji_id="5415803062738504079", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.DIST_FAR, callback_data="dist_100_200",
                              icon_custom_emoji_id="5415803062738504079", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.DIST_ANY, callback_data="dist_any",
                              icon_custom_emoji_id="5469741319330996757", style="success")],
        # 🌟 دکمه رفع بن‌بست
        [InlineKeyboardButton(text="🔙 بازگشت به منوی کشف", callback_data="open_discovery_menu",
                              icon_custom_emoji_id="5465665476971471368", style="danger")],
    ])
# ── Coins main menu ─────────────────────────────────────────────────────────
def get_coins_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.COINS_PURCHASE, callback_data="coins_buy",
                              icon_custom_emoji_id="5471952986970267163", style="success")],
        [InlineKeyboardButton(text=InlineBtn.COINS_FREE, callback_data="coins_free",
                              icon_custom_emoji_id="5379600444098093058", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.COINS_HISTORY, callback_data="coins_history",
                              icon_custom_emoji_id="5334882760735598374", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.COINS_TRANSFER, callback_data="coins_transfer",
                              icon_custom_emoji_id="5471899089425667918", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.COINS_GIFT_TRANSFER, callback_data="coins_gift_transfer",
                              icon_custom_emoji_id="5451732530048802485", style="primary")],
    ])


def get_gifts_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.GIFTS_BUY, callback_data="gift_buy",
                              icon_custom_emoji_id="5451732530048802485", style="success")],
        [InlineKeyboardButton(text=InlineBtn.GIFTS_SEND, callback_data="gift_send",
                              icon_custom_emoji_id="5472019095106886003", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.GIFTS_INVENTORY, callback_data="gift_inventory",
                              icon_custom_emoji_id="5373012449597335010", style="primary")],
        # استایل دکمه‌ی زیر از warning به primary تغییر یافت تا ارور برطرف شود
        [InlineKeyboardButton(text="🛍 فروش گیفت‌های من", callback_data="gift_sell_start",
                              icon_custom_emoji_id="5472030678633684592", style="primary")],
    ])


def get_gift_sell_picker_keyboard(inventory: list) -> InlineKeyboardMarkup:
    """ساخت کیبورد از گیفت‌هایی که کاربر در موجودی خود دارد"""
    btns = []
    for ug, gt in inventory:
        if ug.quantity > 0:
            # محاسبه قیمت فروش (۲۰ درصد ارزان‌تر) با استفاده از تقسیم صحیح
            sell_price = (gt.price_coins * 80) // 100
            btns.append([
                InlineKeyboardButton(
                    text=f"{gt.emoji} {gt.display_name} (دارید: {ug.quantity}) — فروش: {sell_price} سکه",
                    callback_data=f"gift_sell_pick_{gt.code}",
                    style="primary"
                )
            ])
    btns.append([
        InlineKeyboardButton(text="❌ انصراف", callback_data="gift_cancel",
                             icon_custom_emoji_id="5465665476971471368", style="danger")
    ])
    return InlineKeyboardMarkup(inline_keyboard=btns)


def get_gift_picker_keyboard(prices: dict, owned: dict = None) -> InlineKeyboardMarkup:
    """
    prices: {gift_code: price_in_coins}
    owned:  {gift_code: quantity_in_inventory} — optional, for showing owned badge
    """
    owned = owned or {}
    btns = []
    # Use InlineBtn.GIFT_TEDDY etc for display text
    code_to_label = {
        "teddy":     InlineBtn.GIFT_TEDDY,
        "rose":      InlineBtn.GIFT_ROSE,
        "diamond":   InlineBtn.GIFT_DIAMOND,
        "ring":      InlineBtn.GIFT_RING,
        "chocolate": InlineBtn.GIFT_CHOCOLATE,
    }
    for code, label in code_to_label.items():
        price = prices.get(code, 0)
        own_qty = owned.get(code, 0)
        suffix = f" ({own_qty} دارید)" if own_qty > 0 else f" — {price} سکه"
        btns.append([
            InlineKeyboardButton(
                text=f"{label}{suffix}",
                callback_data=f"gift_pick_{code}",
                icon_custom_emoji_id="5451732530048802485",
                style="primary"
            )
        ])
    btns.append([
        InlineKeyboardButton(text="❌ انصراف", callback_data="gift_cancel",
                             icon_custom_emoji_id="5465665476971471368", style="danger")
    ])
    return InlineKeyboardMarkup(inline_keyboard=btns)


def get_gift_quantity_keyboard(gift_code: str, max_qty: int = 10) -> InlineKeyboardMarkup:
    """Picker for quantity 1-5 plus a custom button."""
    btns = []
    row = []
    for n in [1, 2, 3, 5, 10]:
        if n <= max_qty:
            row.append(InlineKeyboardButton(text=f"×{n}", callback_data=f"gift_qty_{gift_code}_{n}",
                                            style="primary"))
    btns.append(row)
    btns.append([InlineKeyboardButton(text="❌ انصراف", callback_data="gift_cancel",
                                       icon_custom_emoji_id="5465665476971471368", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def get_help_main_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for /qavanin help menu (Grid Layout) - Fully Persian & Standardized"""
    
    # فرمت: (متن فارسی دکمه, کلید انگلیسی استاندارد)
    items = [
        ("💬 چت ناشناس", "anonymous_chat"),
        ("🪙 سکه و اعتبار", "credit_coin"),
        ("📍 افراد نزدیک", "nearby_users"),
        ("👤 پروفایل من", "profile"),
        ("📨 درخواست چت", "chat_request"),
        ("✉️ پیام دایرکت", "direct_message"),
        ("⌨️ میان‌برها", "shortcuts"),
        ("📜 قوانین ربات", "terms_of_use"),
        ("🔔 هشدار آنلاین", "online_alert"),
        ("👥 دوستان و مخاطبین", "contacts"),
        ("🔍 جستجوی پیشرفته", "advanced_search"),
        ("🗑 حذف پیام‌ها", "delete_message"),
        ("🔕 حالت بی‌صدا", "silent_mode"),
        ("🔗 لینک ناشناس", "anonymous_link"),
        ("📢 اعلان پایان چت", "chat_end_alert"),
        ("❌ حذف اکانت", "delete_account"),
        ("👀 بازدید پروفایل", "profile_visitors"),
        ("⭐ اشتراک VIP", "vip_subscription"),
        ("🎁 گیفت‌ها", "gifts"),
        ("🏷 تگ‌ها", "tags"),
    ]
    
    rows = []
    # حرکت به صورت دوتا دوتا در لیست برای ایجاد ساختار شبکه‌ای (Grid)
    for i in range(0, len(items), 2):
        row = []
        
        # دکمه اول (راست)
        label1, cb1 = items[i]
        # باگ برطرف شد: حالا از cb1 استفاده می‌شود نه label1
        row.append(InlineKeyboardButton(text=label1, callback_data=f"help_topic_{cb1}"))
        
        # دکمه دوم (چپ) اگر وجود داشت
        if i + 1 < len(items):
            label2, cb2 = items[i+1]
            # باگ برطرف شد: حالا از cb2 استفاده می‌شود نه label2
            row.append(InlineKeyboardButton(text=label2, callback_data=f"help_topic_{cb2}"))
            
        rows.append(row)
        
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_referral_dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.REFERRAL_LINK, callback_data="referral_show_link",
                              icon_custom_emoji_id="5467406098367521267", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.REFERRAL_DASHBOARD, callback_data="referral_show_stats",
                              icon_custom_emoji_id="5334882760735598374", style="primary")],
        [InlineKeyboardButton(text="🔗 بنرهای دعوت اختصاصی", callback_data="referral_banners")]
    ])


# ── Direct message inbox ────────────────────────────────────────────────────
def get_dm_inbox_keyboard(messages: list) -> InlineKeyboardMarkup:
    """
    messages: list of (id, sender_public_id, unread_marker, sent_str)
    """
    rows = []
    for mid, sender_pid, unread_marker, sent in messages[:10]:
        # 🚀 فیکس: حذف متن پیام از روی دکمه برای جلوگیری از بهم ریختگی RTL/LTR
        rows.append([
            InlineKeyboardButton(
                text=f"{unread_marker} {sender_pid} ⏳ {sent[-5:]}",
                callback_data=f"dm_view_{mid}",
                style="primary"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_dm_message_keyboard(message_id: int, sender_tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.DM_REPLY, callback_data=f"dm_reply_{message_id}_{sender_tg_id}",
                              icon_custom_emoji_id="5472019095106886003", style="success")],
        [InlineKeyboardButton(text=InlineBtn.DM_DELETE, callback_data=f"dm_delete_{message_id}",
                              icon_custom_emoji_id="5465665476971471368", style="danger")],
        # 🌟 دکمه رفع بن‌بست
        [InlineKeyboardButton(text="🔙 بازگشت به صندوق", callback_data="dm_inbox", style="primary")]
    ])

def get_distance_filter_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.DIST_NEAR, callback_data="dist_0_50",
                              icon_custom_emoji_id="5415803062738504079", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.DIST_MEDIUM, callback_data="dist_50_100",
                              icon_custom_emoji_id="5415803062738504079", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.DIST_FAR, callback_data="dist_100_200",
                              icon_custom_emoji_id="5415803062738504079", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.DIST_ANY, callback_data="dist_any",
                              icon_custom_emoji_id="5469741319330996757", style="success")],
        # 🌟 دکمه رفع بن‌بست
        [InlineKeyboardButton(text="🔙 بازگشت به منوی کشف", callback_data="open_discovery_menu",
                              icon_custom_emoji_id="5465665476971471368", style="danger")],
    ])

def get_vip_age_filter_keyboard(match_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_18_25, callback_data=f"vip_age_filter_18_25_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_25_30, callback_data=f"vip_age_filter_25_30_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_30_40, callback_data=f"vip_age_filter_30_40_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")],
        [InlineKeyboardButton(text=InlineBtn.VIP_AGE_ALL, callback_data=f"vip_age_filter_0_99_{match_type}", icon_custom_emoji_id="5451732530048802485", style="primary")],
        # 🌟 دکمه رفع بن‌بست
        [InlineKeyboardButton(text="❌ انصراف", callback_data="close_menu", icon_custom_emoji_id="5465665476971471368", style="danger")]
    ])

def get_matching_type_keyboard_v3() -> InlineKeyboardMarkup:
    """نسخه لوکس با چیدمان شبکه‌ای و ایموجی‌های پریمیوم"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎯 دیت رندوم", callback_data="match_random",
                                 icon_custom_emoji_id="6037220740967697584"), 
            InlineKeyboardButton(text="⏳ دیت هم‌سن", callback_data="match_same_age",
                                 icon_custom_emoji_id="5451732530048802485")
        ],
        [
            InlineKeyboardButton(text="🙋‍♂️ دیت با پسر", callback_data="match_boy",
                                 icon_custom_emoji_id="5429564911048992647"),
            InlineKeyboardButton(text="🙋‍♀️ دیت با دختر", callback_data="match_girl",
                                 icon_custom_emoji_id="5429474729620677471")
        ],
        [
            # 🌟 متن دکمه اصلاح شد تا با منطق استان‌محور موتور مچینگ همخوانی داشته باشد
            InlineKeyboardButton(text="📍 دیت با هم‌شهری / هم‌استان", callback_data="match_nearby",
                                 icon_custom_emoji_id="6037516707164064818") 
        ],
        [
            InlineKeyboardButton(text="👑 ورود به پنل VIP", callback_data="vip_panel",
                                 icon_custom_emoji_id="5852518859268951767") 
        ]
    ])
def get_discovery_main_menu_keyboard() -> InlineKeyboardMarkup:
    """v3 redesigned discovery menu (Grid Layout)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        # ردیف ۱
        [
            InlineKeyboardButton(text="📍 افراد نزدیک", callback_data="disc_nearby",
                                 icon_custom_emoji_id="6037579284837567462"), # پین پریمیوم
            InlineKeyboardButton(text="❤️ کیا منو لایک کردن؟", callback_data="disc_liked_me",
                                 icon_custom_emoji_id="5449505950283078474")
        ],
        # ردیف ۲
        [
            InlineKeyboardButton(text="🤝 هم‌سلیقه", callback_data="disc_same_interests",
                                 icon_custom_emoji_id="6037628664076570524"),
            InlineKeyboardButton(text="🏙 هم‌شهر", callback_data="disc_same_city",
                                 icon_custom_emoji_id="5264733042710181045")
        ],
        # ردیف ۳
        [
            InlineKeyboardButton(text="🗺 هم‌استان", callback_data="disc_same_province",
                                 icon_custom_emoji_id="5415803062738504079"),
            InlineKeyboardButton(text="💬 بدون چت قبلی", callback_data="disc_no_chat",
                                 icon_custom_emoji_id="6039520378127126241")
        ],
        # ردیف ۴ (لیست‌ها)
        [
            InlineKeyboardButton(text="👥 دوستان من", callback_data="disc_friends",
                                 icon_custom_emoji_id="5372926953978341366"),
            InlineKeyboardButton(text="🚫 مسدود شده‌ها", callback_data="disc_blocked",
                                 icon_custom_emoji_id="6039591820613127611") # علامت ممنوع پریمیوم
        ]
    ])
    
# ── Tag selection (profile edit) ────────────────────────────────────────────
def get_tag_selection_keyboard(tags_by_cat: dict, selected: set, max_tags: int) -> InlineKeyboardMarkup:
    """
    tags_by_cat: {category_code: [(tag_code, display_name, emoji), ...]}
    selected: set of tag_code strings
    max_tags: 3 for normal users, 10 for VIP
    """
    rows = []
    for cat_code, tags in tags_by_cat.items():
        rows.append([InlineKeyboardButton(text=f"— {cat_code} —", callback_data="tag_cat_noop",
                                          style="primary")])
        for code, name, emoji in tags:
            mark = "✅ " if code in selected else ""
            rows.append([InlineKeyboardButton(
                text=f"{mark}{emoji} {name}",
                callback_data=f"tag_toggle_{code}",
                style="primary" if code not in selected else "success"
            )])
    rows.append([InlineKeyboardButton(
        text=f"✅ تأیید ({len(selected)}/{max_tags})",
        callback_data="tag_confirm",
        icon_custom_emoji_id="5427009714745517609",
        style="success"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Profile completion flow ─────────────────────────────────────────────────
def get_profile_completion_keyboard(current_step: str) -> InlineKeyboardMarkup:
    """Steps: city → photo → gps → tags → bio → voice → complete"""
    steps_map = {
        "city":  ("🏙 شهر خود را وارد کنید", "pc_set_city"),
        "photo": ("📷 عکس پروفایل", "pc_set_photo"),
        "gps":   ("📍 لوکیشن (GPS)", "pc_set_gps"),
        "tags":  ("🏷 تگ‌ها", "pc_set_tags"),
        "bio":   ("📝 بیوگرافی", "pc_set_bio"),
        "voice": ("🎙 ویس پروفایل", "pc_set_voice"),
    }
    rows = []
    if current_step in steps_map:
        label, cb = steps_map[current_step]
        rows.append([InlineKeyboardButton(text=label, callback_data=cb, style="primary")])
    rows.append([InlineKeyboardButton(text="⏭ رد کردن این مرحله", callback_data="pc_skip",
                                       icon_custom_emoji_id="5465665476971471368", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Banner forward (free coins) ─────────────────────────────────────────────
def get_free_coin_banner_keyboard(campaign_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 فوروارد بنر به دوستان", callback_data=f"banner_fwd_{campaign_id}",
                              icon_custom_emoji_id="5467406098367521267", style="success")],
        [InlineKeyboardButton(text="❌ بستن", callback_data="banner_close",
                              icon_custom_emoji_id="5465665476971471368", style="danger")],
    ])


# ── Admin: pin broadcast option ─────────────────────────────────────────────
def get_admin_broadcast_pin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📌 ارسال و پین شود", callback_data="admin_bc_pin",
                              icon_custom_emoji_id="5467406098367521267", style="success")],
        [InlineKeyboardButton(text="📨 ارسال بدون پین", callback_data="admin_bc_nopin",
                              icon_custom_emoji_id="5472019095106886003", style="primary")],
        [InlineKeyboardButton(text="❌ لغو", callback_data="admin_bc_cancel",
                              icon_custom_emoji_id="5465665476971471368", style="danger")],
    ])


# ── Admin: report review ────────────────────────────────────────────────────
def get_admin_report_review_keyboard(report_id: int) -> InlineKeyboardMarkup:
    """For 3-strike system: approve = reward reporter + warn reported user; reject = warn reporter."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تأیید گزارش (پاداش به گزارش‌دهنده + اخطار به متخلف)",
                              callback_data=f"admin_report_approve_{report_id}",
                              icon_custom_emoji_id="5427009714745517609", style="success")],
        [InlineKeyboardButton(text="❌ رد گزارش (اخطار به گزارش‌دهنده)",
                              callback_data=f"admin_report_reject_{report_id}",
                              icon_custom_emoji_id="5465665476971471368", style="danger")],
    ])


# ── Admin: banner forward review ────────────────────────────────────────────
def get_admin_banner_review_keyboard(forward_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تأیید و اعطای سکه",
                              callback_data=f"admin_banner_approve_{forward_id}",
                              icon_custom_emoji_id="5427009714745517609", style="success")],
        [InlineKeyboardButton(text="❌ رد",
                              callback_data=f"admin_banner_reject_{forward_id}",
                              icon_custom_emoji_id="5465665476971471368", style="danger")],
    ])

def get_gift_sell_quantity_keyboard(gift_code: str, max_qty: int) -> InlineKeyboardMarkup:
    """کیبورد انتخاب تعداد برای فروش (با محدودیت موجودی کاربر)"""
    btns = []
    row = []
    # فقط اعدادی را نشان می‌دهیم که از موجودی کاربر بیشتر نباشند
    for n in [1, 2, 3, 5, 10]:
        if n <= max_qty:
            row.append(InlineKeyboardButton(
                text=f"×{n}", 
                callback_data=f"gift_sell_qty_{gift_code}_{n}",
                style="primary"
            ))
    
    if row:
        btns.append(row)
        
    btns.append([
        InlineKeyboardButton(text="❌ انصراف", callback_data="gift_cancel",
                             icon_custom_emoji_id="5465665476971471368", style="danger")
    ])
    return InlineKeyboardMarkup(inline_keyboard=btns)