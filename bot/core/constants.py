"""
bot/core/constants.py

Single source of truth for key UI strings, reply keyboard labels, 
inline keyboard labels, and system messages.

v3 CHANGES:
- Removed: RULES, BLOCKED_USERS (from main menu row), REFERRAL_VIP, MY_FRIENDS
- Renamed: REFERRAL_VIP → VIP_SUBSCRIPTION (was "زیرمجموعه‌گیری & VIP")
- Added: DISCOVER, COINS, GIFTS, COINS_FREE, HELP_MENU
- Added: VIP plan codes, gift codes, distance filter codes, tag categories
"""

# ═══════════════════════════════════════════════════════════════════════════
# VIP PLAN CODES (subscription model)
# ═══════════════════════════════════════════════════════════════════════════
class VIPPlan:
    WEEK_1 = "1w"   # 1 week
    WEEK_2 = "2w"   # 2 weeks
    MONTH_1 = "1m"  # 1 month

    LABELS = {
        WEEK_1:  "اشتراک ۱ هفته‌ای",
        WEEK_2:  "اشتراک ۲ هفته‌ای",
        MONTH_1: "اشتراک ۱ ماهه",
    }

    DURATION_DAYS = {
        WEEK_1:  7,
        WEEK_2:  14,
        MONTH_1: 30,
    }

    # Default prices in Tomans (admin can override via /set_vip_price)
    DEFAULT_PRICES_TOMAN = {
        WEEK_1:  30_000,
        WEEK_2:  55_000,
        MONTH_1: 95_000,
    }

def _pe(emoji_id: str, char: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{char}</tg-emoji>'

class PEmoji:
    WAVE = _pe("5472055112702629499", "👋")
    ROCKET = _pe("5445284980978621387", "🚀")
    CROWN = _pe("5467406098367521267", "👑")
    SWIRL = _pe("5469741319330996757", "💫")
    STAR = _pe("5458799228719472718", "🌟")
    SPARKLES = _pe("5472164874886846699", "✨")
    FIRE = _pe("5420315771991497307", "🔥")
    ROSE = _pe("5440911110838425969", "🌹")
    COIN = _pe("5379600444098093058", "🪙")
    MONEY_BAG = _pe("5375296873982604963", "💰")
    DIAMOND = _pe("5471952986970267163", "💎")
    GIFT = _pe("5199749070830197566", "🎁")
    CONFETTI = _pe("5435933711893797296", "🎊")
    PARTY_POPPER = _pe("5436040291507247633", "🎉")
    PARTY_FACE = _pe("5370870691140737817", "🥳")
    CHART_UP = _pe("5373001317042101552", "📈")
    CHART_DOWN = _pe("5361748661640372834", "📉")
    LINK = _pe("5375129357373165375", "🔗")
    CHECK = _pe("5427009714745517609", "✅")
    CROSS = _pe("5465665476971471368", "❌")
    CAKE = _pe("5370999492914976897", "🎂")
    POINT_DOWN = _pe("5470177992950946662", "👇")
    PEOPLE = _pe("5372926953978341366", "👥")
    FOLDED_HANDS = _pe("5472189549473963781", "🙏")
    PHONE = _pe("5467539229468793355", "📞")
    BOOK = _pe("5226512880362332956", "📖")
    COMPASS = _pe("5433825729060018456", "🧭")
    HOUSE = _pe("5465226866321268133", "🏠")
    LOCK = _pe("5472308992514464048", "🔐")
    

# ═══════════════════════════════════════════════════════════════════════════
# GIFT CODES
# ═══════════════════════════════════════════════════════════════════════════
class GiftCode:
    TEDDY    = "teddy"
    ROSE     = "rose"
    DIAMOND  = "diamond"
    RING     = "ring"
    CHOCOLATE = "chocolate"

    ALL = [TEDDY, ROSE, DIAMOND, RING, CHOCOLATE]

    LABELS = {
        TEDDY:     "تدی‌خرسی",
        ROSE:      "گل رز",
        DIAMOND:   "الماس",
        RING:      "انگشتر",
        CHOCOLATE: "شکلات",
    }

    EMOJIS = {
        TEDDY:     "🧸",
        ROSE:      "🌹",
        DIAMOND:   "💎",
        RING:      "💍",
        CHOCOLATE: "🍫",
    }

    # Default prices in coins (admin can override)
    DEFAULT_PRICES_COINS = {
        TEDDY:     5,
        ROSE:      3,
        DIAMOND:   50,
        RING:      30,
        CHOCOLATE: 4,
    }


# ═══════════════════════════════════════════════════════════════════════════
# DISTANCE FILTER (km ranges)
# ═══════════════════════════════════════════════════════════════════════════
class DistanceFilter:
    NEAR   = "0_50"      # 0-50 km
    MEDIUM = "50_100"    # 50-100 km
    FAR    = "100_200"   # 100-200 km
    ANY    = "any"       # no limit (default)

    LABELS = {
        NEAR:   "۰ تا ۵۰ کیلومتر",
        MEDIUM: "۵۰ تا ۱۰۰ کیلومتر",
        FAR:    "۱۰۰ تا ۲۰۰ کیلومتر",
        ANY:    "بدون محدودیت",
    }

    RANGES_KM = {
        NEAR:   (0, 50),
        MEDIUM: (50, 100),
        FAR:    (100, 200),
        ANY:    (0, 999999),
    }


# ═══════════════════════════════════════════════════════════════════════════
# TAG CATEGORIES
# ═══════════════════════════════════════════════════════════════════════════
class TagCategory:
    LIFESTYLE   = "lifestyle"     # سیگاریم، ورزشکار، گیاه‌خوار
    PHYSICAL    = "physical"      # قد بلند، چاق، لاغر
    INTEREST    = "interest"      # موسیقی، سینما، کتاب
    HABIT       = "habit"         # قهوه‌نوش، شب‌بیدار
    PERSONALITY = "personality"   # خجالتی، اجتماعی

    LABELS = {
        LIFESTYLE:   "سبک زندگی",
        PHYSICAL:    "ظاهری",
        INTEREST:    "علایق",
        HABIT:       "عادات",
        PERSONALITY: "شخصیتی",
    }


# ═══════════════════════════════════════════════════════════════════════════
# LIMITS
# ═══════════════════════════════════════════════════════════════════════════
class Limits:
    TAGS_NORMAL_USER = 3
    TAGS_VIP_USER    = 10

    LIKE_COOLDOWN_SECONDS = 60      # 1 like per minute
    LIKE_PER_MINUTE       = 1

    MATCH_QUEUE_TTL_SECONDS   = 300  # 5 minutes
    MATCH_INITIAL_LOCK_SECONDS = 5   # can't end chat/date for 5 sec

    REPORT_REWARD_COINS = 5         # reward for valid report (admin-approved)
    FALSE_REPORT_WARN_COUNT = 1     # warnings issued for false report
    MAX_WARNINGS_BEFORE_BAN = 3     # permanent ban after 3 warnings

    PROFILE_COMPLETION_REWARD = 10  # coins for completing profile

    REFERRAL_COMMISSION_PCT = 20    # 20% of referred user's coin purchases

    MAX_FORCE_JOIN_CHANNELS = 5


class ReplyBtn:
    # --- Main Menu Options (v3 redesign) ---
    START_DATE          = "شروع دیت ناشناس"
    MY_PROFILE          = "پروفایل من"
    DISCOVER            = "کشف کاربران"      # NEW: was SEARCH_USERS, now combined
    MY_COINS            = "سکه"               # renamed from "سکه‌های من"
    VIP_SUBSCRIPTION    = "اکانت VIP (پریمیوم)"  # renamed from "زیرمجموعه‌گیری & VIP"
    GIFTS               = "🎁 گیفت‌ها"
    SUPPORT             = "پشتیبانی"
    HELP                = "راهنما"
    LOOTBOX             = "🎁 لوت‌باکس و جوایز"

    # --- Legacy aliases (kept for backward compat with existing handlers) ---
    NEARBY              = "نزدیک من"           # legacy: still used in start.py
    REFERRAL_VIP        = "زیرمجموعه‌گیری & VIP"  # legacy alias for VIP_SUBSCRIPTION

    # --- Secondary (accessible from /qavanin and Discover sub-menus) ---
    SEARCH_USERS        = "جستجوی کاربران"   # kept as alias / sub-button
    MY_FRIENDS          = "دوستان من"
    BLOCKED_USERS       = "کاربران بلاک شده"
    RULES               = "قوانین"           # only via /qavanin

    # --- Cancellation & Interruption ---
    CANCEL              = "انصراف و منوی اصلی"
    CANCEL_SHORT        = "انصراف"
    BACK_TO_MENU        = "برگشت به منوی اصلی"

    # --- Active Date & Chat Phases ---
    PHASE_USER_PROFILE  = "پروفایل کاربر"
    END_DATE            = "اتمام دیت"
    END_CHAT            = "اتمام چت"

    # --- Terms acceptance ---
    ACCEPT_TERMS        = "قوانین را می‌پذیرم"
    SHOW_RULES          = "نمایش قوانین"
    
    DATE_PHASE_END_DATE = "اتمام دیت"
    CHAT_PHASE_END_CHAT = "اتمام چت"


class InlineBtn:
    # --- Onboarding / Gender ---
    GENDER_MALE   = "آقا"
    GENDER_FEMALE = "خانم"

    # --- Matching Type Options (v3: added "same_age") ---
    MATCH_RANDOM  = "دیت شانسی (رایگان)"
    MATCH_BOY     = "دیت با پسر (۱ سکه)"
    MATCH_GIRL    = "دیت با دختر (۱ سکه)"
    MATCH_NEARBY  = "دیت با افراد نزدیک (۱ سکه)"
    MATCH_SAME_AGE = "دیت با هم‌سن (۱ سکه)"  # NEW

    # --- Match Initialisation ---
    VIEW_PROFILE     = "مشاهده پروفایل کاربر"
    END_DATE_EARLY   = "اتمام دیت"

    # --- Questionnaire ---
    OPTION_A = "گزینه اول"
    OPTION_B = "گزینه دوم"
    OPTION_C = "گزینه سوم"
    OPTION_D = "گزینه چهارم"

    # --- Chat Approval ---
    APPROVE_CHAT_YES = "موافقم؛ شروع گفتگو ناشناس"
    APPROVE_CHAT_NO  = "خیر؛ لغو"

    # --- Active Chat Controls ---
    END_ACTIVE_CHAT = "پایان دادن به چت"
    REPORT_USER     = "گزارش کاربر"

    # --- Terms Acceptance (Inline version) ---
    TERMS_SHOW_INLINE    = "نمایش قوانین"
    TERMS_ACCEPT_INLINE  = "پذیرفتن قوانین"

    # --- Double Confirmation Dialogs ---
    CONFIRM_END_DATE_YES = "بله، دیت را پایان می‌دهم"
    CONFIRM_END_CHAT_YES = "بله، چت را پایان می‌دهم"
    CANCEL_RETURN        = "لغو و بازگشت"

    # --- VIP Panel Controls ---
    VIP_VIEWERS         = "بینندگان پروفایل"
    VIP_INVISIBLE_ON    = "حالت مخفی: روشن"
    VIP_INVISIBLE_OFF   = "حالت مخفی: خاموش"
    VIP_REMATCH         = "مچ مجدد با نفر قبلی"
    VIP_BUY_SUBSCRIPTION = "خرید اشتراک VIP"  # NEW

    # --- VIP Subscription Plans (NEW) ---
    VIP_PLAN_1W  = "۱ هفته - ۳۰٬۰۰۰ ت"
    VIP_PLAN_2W  = "۲ هفته - ۵۵٬۰۰۰ ت"
    VIP_PLAN_1M  = "۱ ماه - ۹۵٬۰۰۰ ت"

    # --- VIP Age Filter ---
    VIP_AGE_18_25 = "[۱۸-۲۵]"
    VIP_AGE_25_30 = "[۲۵-۳۰]"
    VIP_AGE_30_40 = "[۳۰-۴۰]"
    VIP_AGE_ALL   = "[هر سنی]"

    # --- Nearby Search ---
    NEARBY_FEMALE = "دخترها"
    NEARBY_MALE   = "پسرها"
    NEARBY_BOTH   = "هردو جنسیت"

    # --- Distance Filter (NEW) ---
    DIST_NEAR   = "۰ تا ۵۰ کیلومتر"
    DIST_MEDIUM = "۵۰ تا ۱۰۰ کیلومتر"
    DIST_FAR    = "۱۰۰ تا ۲۰۰ کیلومتر"
    DIST_ANY    = "بدون محدودیت"

    # --- Search Options ---
    SEARCH_ONLINE_MALE      = "کاربران آنلاین پسر"
    SEARCH_ONLINE_FEMALE    = "کاربران آنلاین دختر"
    SEARCH_SAME_PROVINCE    = "هم‌استانی‌ها"
    SEARCH_SAME_CITY        = "هم‌شهری‌ها"
    SEARCH_LIKED_ME         = "افراد لایک شده"     # NEW
    SEARCH_SAME_INTERESTS   = "هم‌علایق"            # NEW (was SEARCH_SAME_TAGS)
    SEARCH_NO_CHAT          = "افراد بدون دیت/چت"  # NEW (moved from SEARCH_NO_CHAT)
    SEARCH_BLOCKED          = "افراد بلاک شده"
    SEARCH_FRIENDS          = "دوستان من"

    # --- Coins Menu (NEW) ---
    COINS_HISTORY    = "تاریخچه تراکنش‌ها"
    COINS_PURCHASE   = "خرید سکه"
    COINS_FREE       = "سکه رایگان"
    COINS_TRANSFER   = "انتقال سکه"
    COINS_GIFT_TRANSFER = "ارسال گیفت"

    # --- Gifts Menu (NEW) ---
    GIFTS_BUY        = "خرید گیفت"
    GIFTS_SEND       = "ارسال گیفت"
    GIFTS_INVENTORY  = "گیفت‌های من"
    GIFT_TEDDY       = "🧸 تدی‌خرسی"
    GIFT_ROSE        = "🌹 گل رز"
    GIFT_DIAMOND     = "💎 الماس"
    GIFT_RING        = "💍 انگشتر"
    GIFT_CHOCOLATE   = "🍫 شکلات"

    # --- Referral Menu (NEW) ---
    REFERRAL_LINK     = "لینک اختصاصی من"
    REFERRAL_DASHBOARD = "آمار زیرمجموعه‌ها"

    # --- Help Menu (NEW) ---
    HELP_CHAT          = "/help_chat"
    HELP_CREDIT        = "/help_credit"
    HELP_GPS           = "/help_gps"
    HELP_PROFILE       = "/help_profile"
    HELP_PCHAT         = "/help_pchat"
    HELP_DIRECT        = "/help_direct"
    HELP_SHORTCUTS     = "/help_shortcuts"
    HELP_TERMS         = "/help_terms"
    HELP_ONW           = "/help_onw"
    HELP_CONTACTS      = "/help_contacts"
    HELP_SEARCH        = "/help_search"
    HELP_DELETE_MSG    = "/help_deleteMessage"
    HELP_SILENT        = "/help_silent"
    HELP_NASHENAS      = "/help_nashenas"
    HELP_CHW           = "/help_chw"
    HELP_DELACC        = "/help_delAcc"
    HELP_SEE_PROFILE   = "/help_seeProfile"
    HELP_VIP           = "/help_vip"
    HELP_GIFT          = "/help_gapogift"
    HELP_TAGS          = "/help_tags"

    # --- Direct Messages (NEW) ---
    DM_VIEW          = "مشاهده پیام دایرکت"
    DM_REPLY         = "پاسخ به دایرکت"
    DM_DELETE        = "حذف پیام دایرکت"

    # --- User Actions (used by get_user_action_keyboard) ---
    ACTION_ADD_FRIEND     = "➕ افزودن به دوستان"
    ACTION_BLOCK          = "🚫 بلاک"
    ACTION_UNBLOCK        = "✅ رفع بلاک"
    ACTION_LIKE           = "👍 لایک"
    ACTION_REPORT         = "🚩 گزارش"
    ACTION_REQ_DATE       = "💕 درخواست دیت"
    ACTION_REQ_CHAT       = "💬 درخواست چت"
    ACTION_REQ_DIRECT     = "📨 پیام دایرکت"
    ACTION_TRANSFER_COIN  = "🪙 انتقال سکه"

    # --- Report Reasons (used by get_report_reasons_keyboard) ---
    REPORT_INAPPROPRIATE_PHOTO = "📸 عکس نامناسب"
    REPORT_SCAMMER             = "💰 کلاهبردار"
    REPORT_HARASSMENT          = "⚠️ آزار و اذیت"
    REPORT_SPAM                = "📨 اسپم"
    REPORT_IMPERSONATION       = "🎭 جعل هویت"
    REPORT_SUSPICIOUS_LINK     = "🔗 لینک مشکوک"
    REPORT_ADULT_CONTENT       = "🔞 محتوای بزرگسال"
    REPORT_DRUGS               = "💊 مواد مخدر"
    REPORT_BOT_FAKE            = "🤖 ربات/فیک"
    REPORT_OTHER               = "❓ سایر"
    REPORT_CANCEL              = "❌ انصراف"

    # --- Discovery Age Filters ---
    DISC_AGE_18_25  = "[۱۸-۲۵]"
    DISC_AGE_25_30  = "[۲۵-۳۰]"
    DISC_AGE_30_40  = "[۳۰-۴۰]"
    DISC_AGE_40_50  = "[۴۰-۵۰]"
    DISC_AGE_ALL    = "[همه سنین]"
    DISC_CONFIRM    = "✅ تأیید و ادامه"

    # --- Discovery Interests ---
    INT_GAMING  = "🎮 گیمینگ"
    INT_MUSIC   = "🎵 موسیقی"
    INT_TRAVEL  = "✈️ سفر"
    INT_MOVIES  = "🎬 فیلم"
    INT_SPORTS  = "⚽ ورزش"
    INT_READING = "📚 مطالعه"
    INT_COOKING = "🍳 آشپزی"
    INT_ART     = "🎨 هنر"
    INT_TECH    = "💻 تکنولوژی"
    INT_NATURE  = "🌿 طبیعت"


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM MESSAGES (Persian)
# ═══════════════════════════════════════════════════════════════════════════
class Messages:
    # Fallback message when user sends an unrecognized message
    UNKNOWN_MESSAGE = (
        "عزیز دلم 🫠 متوجه نشدم :/\n\n"
        "چه کاری برات انجام بدم؟ از منوی پایین انتخاب کن 👇"
    )


    # When user starts searching while already in queue
    ALREADY_IN_QUEUE = (
        "عزیزم از قبل دارم برات می‌گردم صبر داشته باش تا ۵ دقیقه اگه پیدا نکردم دوباره سرچ کن"
    )

    # When no match found within 5 minutes
    NO_MATCH_FOUND = (
        "متاسفم عزیزم ☹️ کسی پیدا نشد ، دوباره از منو انتخاب کن و سرچ کن "
        "اینبار دقیق تر برات می‌گردم 😍"
    )

    # Match found — 5-second initial lock
    MATCH_FOUND_LOCK = (
        "🎉 مچ پیدا شد! تا ۵ ثانیه نمی‌توانید دیت/چت را قطع کنید."
    )

    # Like notification
    LIKE_RECEIVED = (
        "تبریک ✨ کاربر {user_tag} پروفایل شما را #لایک کرد."
    )

    # End of chat/date — sent to user A (the one who ended)
    CHAT_ENDED_BY_YOU = (
        "چت شما یا دیت شما با {user_tag} توسط شما قط شد\n\n"
        "🗑 (حذف پیام ها) /del_all_messages_{msg_token}\n\n"
        "برای گزارش عدم رعایت قوانین (/qavanin) می توانید با لمس "
        "《 گزارش کاربر 》 در پروفایل، کاربر را گزارش کنید و "
        "💰 سکه دریافت کنید 😍"
    )

    # End of chat/date — sent to user B (the partner)
    CHAT_ENDED_BY_PARTNER = (
        "چت شما یا دیت شما با {user_tag} توسط مخاطب شما قط شد\n\n"
        "🗑 (حذف پیام ها) /del_all_messages_{msg_token}\n\n"
        "برای گزارش عدم رعایت قوانین (/qavanin) می توانید با لمس "
        "《 گزارش کاربر 》 در پروفایل، کاربر را گزارش کنید و "
        "💰 سکه دریافت کنید 😍"
    )

    # Chat request sent
    CHAT_REQUEST_SENT = (
        "✅ درخواست چت شما برای {user_tag} ارسال شد.\n\n"
        "🚶 منتظر باش و اگه تا ۵ دقیقه تایید نکرد درخواست چت/دیت به این کاربر لغو می‌شه..."
    )

    # Re-engagement: user with coins
    REENGAGE_WITH_COINS = (
        "سلام عزیزم چند روزه ازت خبری نیست ، خواستم بگم توی این مدت که نبودی "
        "کلی دختر و پسر جدید اومدن که میتونی باهاشون دیت بری یا ناشناس چت کنی"
    )

    # Re-engagement: user without coins (+5 free coins)
    REENGAGE_NO_COINS = (
        "سلام عزیزم چند روزه ازت خبری نیست ، خواستم بگم توی این مدت که نبودی "
        "کلی دختر و پسر جدید اومدن که میتونی باهاشون دیت بری یا ناشناس چت کنی\n\n"
        "تازه بهت ۵ سکه رایگان هم تقدیمت کردم که مشکلی نداشته باشی ، "
        "دیگه چی میخوای عزیزم؟"
    )

    # Profile completion reminder (sent 2 min after registration, then daily)
    PROFILE_COMPLETION_REMINDER = (
        "{first_name} عزیز شما هنوز پروفایلت رو کامل نکردی!\n"
        "میدونستی اگه همین الان کامل کنی پروفایلت رو ۱۰ سکه رایگان بهت تعلق می‌گیره؟ 🎁\n\n"
        "برای تکمیل پروفایل روی دکمه «پروفایل من» بزن 👇"
    )

    # Silence in chat (30 min, then 24h, then 24h...)
    CHAT_SILENCE_REMINDER = (
        "شما در حال چت با {user_tag} هستید.\n\n"
        "💡 برای اتمام چت می تونی گزینه «قطع مکالمه» رو بزنی.\n\n"
        "هر وقت هر جای ربات گیر کردی میتونی گزینه /start رو بزنی.\n\n"
        "🆔 @datenashenas"
    )

    # Direct message notification (only shown if not in chat/date)
    DM_RECEIVED = (
        "🙍‍♀️ کاربر {user_tag} «برای شما یک پیام دایرکت ارسال کرده است»\n"
        "جهت دیدن پیام روی دکمه «مشاهده پیام دایرکت» بزنید."
    )

    # Location required for nearby
    LOCATION_REQUIRED = (
        "برای استفاده از «افراد نزدیک» باید ابتدا موقعیت مکانی خود را تعیین کنید.\n\n"
        "برای این کار به «پروفایل من» → «ویرایش پروفایل» → «ارسال لوکیشن» بروید."
    )

    # Profile completion success
    PROFILE_COMPLETED = (
        "🎉 تبریک می‌گم! پروفایل شما با موفقیت تکمیل شد.\n\n"
        "💎 {coins} سکه رایگان به شما تعلق گرفت!"
    )

    # VIP purchase success
    VIP_PURCHASE_SUCCESS = (
        "🎉 تبریک! اشتراک VIP شما با موفقیت فعال شد.\n\n"
        "⏰ مدت اعتبار: {duration}\n"
        "📅 تاریخ انقضا: {expires_at}\n\n"
        "از این پس تمام امکانات ربات برای شما رایگان است!\n"
        "• بدون مصرف سکه برای مچینگ\n"
        "• ۱۰ تگ به جای ۳ تگ\n"
        "• ستاره آبی ⭐ کنار نام شما\n"
        "• کامنت‌ها فقط برای VIP‌ها فعال\n"
        "• حالت مخفی + بازدیدکنندگان پروفایل"
    )

    # VIP badge for profile
    VIP_BADGE = "⭐"  # blue star emoji shown next to VIP users

    # Warning issued
    WARNING_ISSUED = (
        "⚠️ اخطار دریافت کردید!\n\n"
        "دلیل: {reason}\n"
        "تعداد اخطار فعلی شما: {count} از ۳\n\n"
        "پس از ۳ اخطار اکانت شما به‌طور دائمی مسدود خواهد شد."
    )

    BANNED_PERMANENT = (
        "🚫 اکانت شما به دلیل دریافت ۳ اخطار به‌طور دائمی مسدود شد.\n\n"
        "در صورت اعتراض می‌توانید به پشتیبانی مراجعه کنید."
    )

    REPORT_REWARDED = (
        "🙏 متشکریم! گزارش شما تأیید شد و {coins} سکه پاداش به شما تعلق گرفت."
    )

    BANNER_REWARD = (
        "🎉 تبریک! بنر شما تأیید شد و {coins} سکه رایگان دریافت کردید."
    )

    REFERRAL_CODE_INTRO = (
        "🔗 لینک اختصاصی شما برای دعوت دوستان:\n\n"
        "{referral_link}\n\n"
        "💎 هر بار که یکی از زیرمجموعه‌های شما سکه خریداری کند، "
        "{pct}٪ از خرید او به‌صورت سکه به شما تعلق می‌گیرد!"
    )
    # --- Questionnaire Messages ---
    WAITING_SUFFIX = "\n\n⏳ <i>منتظر انتخاب طرف مقابل...</i>"
    ANSWER_ACK_TOAST = "✅ انتخابت ثبت شد!"
    PARTNER_WAIT_ALERT = "⏳ دوست عزیز، هنوز طرف مقابل جوابی نداده. یکم بهش وقت بده!"

    # --- Date / Interactions Messages ---
    DATE_CANCELLED_TEXT = (
        "❌ دیت شما لغو شد.\n\n"
        "می‌تونی از منوی اصلی دوباره شانس خودت رو امتحان کنی!"
    )

class CompatibilityMsg:
    # < 30%
    TIER_LOW = "❄️ راستش رو بخوای سلیقه‌هاتون خیلی شبیه هم نیست، ولی خب می‌گن تضادها همدیگه رو جذب می‌کنن! امتحانش ضرر نداره 😉"
    
    # 30% to 50%
    TIER_MID_LOW = "🌱 یه شباهت‌هایی با هم دارید. شاید اگه یکم بیشتر گپ بزنید، نقطه‌های مشترک بیشتری پیدا کنید."
    
    # 50% to 70%
    TIER_MID_HIGH = "🔥 ایول! تفاهم خوبی با هم دارید. مطمئنم حرف کم نمیارید و حسابی بهتون خوش می‌گذره."
    
    # > 70%
    TIER_HIGH = "✨ واو! شما دو تا فوق‌العاده‌اید! این حجم از شباهت واقعاً کم‌پیداست، اصلاً این مچ رو از دست نده! 😍"