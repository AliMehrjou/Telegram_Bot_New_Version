"""
bot/core/constants.py

Single source of truth for key UI strings, reply keyboard labels, 
inline keyboard labels, and system messages.
"""

class ReplyBtn:
    # --- Main Menu Options ---
    START_DATE = "شروع دیت ناشناس"
    MY_PROFILE = "پروفایل من"
    NEARBY = "نزدیک من"
    SEARCH_USERS = "جستجوی کاربران"
    MY_FRIENDS = "دوستان من"
    DISCOVER = "کشف کاربران"
    REFERRAL_VIP = "زیرمجموعه‌گیری & VIP"
    RULES = "قوانین"
    SUPPORT = "پشتیبانی"
    HELP = "راهنما"
    MY_COINS = "سکه‌های من"

    # --- Cancellation & Interruption ---
    CANCEL = "انصراف و منوی اصلی"
    CANCEL_SHORT = "انصراف"
    BACK_TO_MENU = "برگشت به منوی اصلی"

    # --- Active Date & Chat Phases ---
    PHASE_USER_PROFILE = "پروفایل کاربر"
    END_DATE = "اتمام دیت"
    END_CHAT = "اتمام چت"

    # --- Terms acceptance ---
    ACCEPT_TERMS = "قوانین را می‌پذیرم"
    SHOW_RULES = "نمایش قوانین"
    
    PHASE_USER_PROFILE = "پروفایل کاربر"
    DATE_PHASE_END_DATE = "اتمام دیت"
    CHAT_PHASE_END_CHAT = "اتمام چت"


class InlineBtn:
    # --- Onboarding / Gender ---
    GENDER_MALE = "آقا"
    GENDER_FEMALE = "خانم"

    # --- Matching Type Options ---
    MATCH_RANDOM = "دیت شانسی (رایگان)"
    MATCH_BOY = "دیت با پسر (۱ سکه)"
    MATCH_GIRL = "دیت با دختر (۱ سکه)"
    MATCH_NEARBY = "دیت با افراد نزدیک (۱ سکه)"

    # --- Match Initialisation ---
    VIEW_PROFILE = "مشاهده پروفایل کاربر"
    END_DATE_EARLY = "اتمام دیت"

    # --- Questionnaire ---
    OPTION_A = "گزینه اول"
    OPTION_B = "گزینه دوم"
    OPTION_C = "گزینه سوم"
    OPTION_D = "گزینه چهارم"

    # --- Chat Approval ---
    APPROVE_CHAT_YES = "موافقم؛ شروع گفتگو ناشناس"
    APPROVE_CHAT_NO = "خیر؛ لغو"

    # --- Active Chat Controls ---
    END_ACTIVE_CHAT = "پایان دادن به چت"
    REPORT_USER = "گزارش کاربر"

    # --- Terms Acceptance (Inline version) ---
    TERMS_SHOW_INLINE = "نمایش قوانین"
    TERMS_ACCEPT_INLINE = "پذیرفتن قوانین"

    # --- Double Confirmation Dialogs ---
    CONFIRM_END_DATE_YES = "بله، دیت را پایان می‌دهم"
    CONFIRM_END_CHAT_YES = "بله، چت را پایان می‌دهم"
    CANCEL_RETURN = "لغو و بازگشت"

    # --- VIP Panel Controls ---
    VIP_VIEWERS = "بینندگان پروفایل"
    VIP_INVISIBLE_ON = "حالت مخفی: روشن"
    VIP_INVISIBLE_OFF = "حالت مخفی: خاموش"
    VIP_REMATCH = "مچ مجدد با نفر قبلی"

    # --- VIP Age Filter ---
    VIP_AGE_18_25 = "[۱۸-۲۵]"
    VIP_AGE_25_30 = "[۲۵-۳۰]"
    VIP_AGE_30_40 = "[۳۰-۴۰]"
    VIP_AGE_ALL = "[هر سنی]"

    # --- Nearby Search ---
    NEARBY_FEMALE = "دخترها"
    NEARBY_MALE = "پسرها"
    NEARBY_BOTH = "هردو جنسیت"

    # --- Search Options ---
    SEARCH_ONLINE_MALE = "کاربران آنلاین پسر"
    SEARCH_ONLINE_FEMALE = "کاربران آنلاین دختر"
    SEARCH_SAME_PROVINCE = "هم‌استانی‌ها"
    SEARCH_SAME_CITY = "هم‌شهری‌ها"
    SEARCH_NO_CHAT = "کاربران بدون چت و دیت"

    # --- Coins Menu ---
    COINS_HISTORY = "تاریخچه تراکنش‌ها"
    COINS_PURCHASE = "خرید سکه"

    # --- User Actions ---
    ACTION_UNBLOCK = "آنبلاک کاربر"
    ACTION_BLOCK = "بلاک کاربر"
    ACTION_REQ_DATE = "درخواست دیت"
    ACTION_REQ_CHAT = "درخواست چت"
    ACTION_REQ_DIRECT = "ارسال دایرکت"
    ACTION_TRANSFER_COIN = "انتقال سکه"
    ACTION_ADD_FRIEND = "افزودن به دوستان"
    ACTION_LIKE = "لایک"
    ACTION_REPORT = "گزارش تخلف"

    # --- Report Reasons ---
    REPORT_INAPPROPRIATE_PHOTO = "عکس نامناسب"
    REPORT_SCAMMER = "کلاهبردار"
    REPORT_HARASSMENT = "توهین و فحاشی"
    REPORT_SPAM = "اسپم/تبلیغات"
    REPORT_IMPERSONATION = "جعل هویت"
    REPORT_SUSPICIOUS_LINK = "ارسال لینک مشکوک"
    REPORT_ADULT_CONTENT = "محتوای غیراخلاقی"
    REPORT_DRUGS = "فروش مواد"
    REPORT_BOT_FAKE = "ربات/فیک"
    REPORT_OTHER = "سایر موارد"
    REPORT_CANCEL = "انصراف"

    # --- Discovery Age ---
    DISC_AGE_18_25 = "۱۸ تا ۲۵ سال"
    DISC_AGE_25_30 = "۲۵ تا ۳۰ سال"
    DISC_AGE_30_40 = "۳۰ تا ۴۰ سال"
    DISC_AGE_40_50 = "۴۰ تا ۵۰ سال"
    DISC_AGE_ALL = "بدون محدودیت سنی"

    # --- Discovery Interests ---
    INT_GAMING = "گیمینگ"
    INT_MUSIC = "موزیک"
    INT_TRAVEL = "سفر"
    INT_MOVIES = "فیلم"
    INT_SPORTS = "ورزش"
    INT_READING = "مطالعه"
    INT_COOKING = "آشپزی"
    INT_ART = "هنر"
    INT_TECH = "تکنولوژی"
    INT_NATURE = "طبیعت"
    DISC_CONFIRM = "تأیید و جستجو"

class SystemMsg:
    MATCH_FOUND_TEXT = (
        "تبریک شما با یک نفر برای رفتن به دیت متصل شدین ، "
        "(از دکمه های پایین منو میتونید پروفایل کاربر را مشاهده کنید و یا دیت را تمام کنید.)"
        "\n\nسوالات دیت ۵ ثانیه دیگه شروع میشه"
    )
    WAITING_SUFFIX = "\n\nپاسخ شما ثبت شد. در انتظار پاسخ پارتنر..."
    ANSWER_ACK_TOAST = "پاسخ ثبت شد"
    PARTNER_WAIT_ALERT = "لطفا شکیبا باشید، پارتنر شما هنوز پاسخ نداده است."
    DATE_CANCELLED_TEXT = "دیت توسط یکی از طرفین لغو شد و به منوی اصلی بازگشتید."