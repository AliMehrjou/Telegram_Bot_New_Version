import json
import html
import logging
import functools
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
import pytz
import jdatetime
import re
from aiogram.types import InlineKeyboardButton

logger = logging.getLogger(__name__)

def _safe_format_var(value) -> str:
    """
    جلوگیری از خطاهای فرمت‌دهی رشته با اسکیپ کردن کرلی بریس‌ها {}
    و ایمن‌سازی متون برای نمایش در ساختار HTML تلگرام.
    """
    escaped = html.escape(str(value))
    return escaped.replace("{", "{{").replace("}", "}}")

_PROFILE_TEMPLATE_CACHE: Optional[str] = None

def _load_profile_template() -> str:
    global _PROFILE_TEMPLATE_CACHE
    if _PROFILE_TEMPLATE_CACHE is not None:
        return _PROFILE_TEMPLATE_CACHE
        
    json_path = Path("json_files/profile_template.json")
    if not json_path.exists():
        json_path = Path("/app/json_files/profile_template.json")
        
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            template_lines = data.get("profile_text", [])
            _PROFILE_TEMPLATE_CACHE = "\n".join(template_lines)
        except Exception as e:
            logger.error(f"Error reading profile template: {e}", exc_info=True)
            _PROFILE_TEMPLATE_CACHE = "{profile_title} {first_name}\n🆔 {public_id}\nجنسیت: {gender}\nوضعیت تأهل: {marital_status}\nسن: {age}\nاستان: {province}\nشهر: {city}\nبیو: {bio}\nعلایق: {interests}\nوضعیت: {vip_status}\nلایک: {likes_count}\n{compatibility_text}{private_info}"
    else:
        logger.warning("Profile template file not found, using fallback.")
        _PROFILE_TEMPLATE_CACHE = "{profile_title} {first_name}\n🆔 {public_id}\nجنسیت: {gender}\nوضعیت تأهل: {marital_status}\nسن: {age}\nاستان: {province}\nشهر: {city}\nبیو: {bio}\nعلایق: {interests}\nوضعیت: {vip_status}\nلایک: {likes_count}\n{compatibility_text}{private_info}"
        
    return _PROFILE_TEMPLATE_CACHE


def _is_valid_vip(user) -> bool:
    """بررسی دقیق و ایمن فعال بودن اشتراک VIP کاربر با در نظر گرفتن تاریخ انقضا"""
    if not getattr(user, 'is_vip', False): 
        return False
        
    expires = getattr(user, 'vip_expires_at', None)
    if not expires: 
        # اگر کاربر VIP است اما تاریخ انقضا تنظیم نشده (مثلا ادمین به صورت دستی VIP کرده)
        return True
        
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
        
    return expires > datetime.now(timezone.utc)

def build_unified_profile_card(user, is_own_profile: bool = False,
                               compatibility: Optional[int] = None,
                               distance_km: Optional[float] = None,
                               gifts_summary: Optional[dict] = None) -> str:
    """نسخه فوق لوکس با تیک آبی و ایموجی‌های پریمیوم سفارشی"""
    template_str = _load_profile_template()

    # 🌟 ایموجی‌های پریمیوم (تیک آبی، چشم، زنگوله، ضربدر، تیک سبز و نقطه آنلاین)
    BLUE_TICK = '<tg-emoji emoji-id="5852518859268951767">⭐</tg-emoji>'
    P_EYE = '<tg-emoji emoji-id="6037218073793007354">👀</tg-emoji>'
    P_BELL = '<tg-emoji emoji-id="6039712977345580805">🔔</tg-emoji>'
    P_CROSS = '<tg-emoji emoji-id="6037327204617030722">❌</tg-emoji>'
    P_CHECK = '<tg-emoji emoji-id="6037088297061191007">✔️</tg-emoji>'
    P_ONLINE = '<tg-emoji emoji-id="6039690609155903995">🟢</tg-emoji>'

    # اعمال تیک آبی VIP به نام و وضعیت
    is_vip = _is_valid_vip(user)
    vip_badge = f" {BLUE_TICK}" if is_vip else ""
    vip_status = f"{BLUE_TICK} <b>تایید شده (VIP)</b>" if is_vip else "عضو عادی"

    profile_title = (f"شما" if is_own_profile else f"کاربر")
    first_name = _safe_format_var(getattr(user, 'first_name', None) or "کاربر") + vip_badge
    
    public_id = _safe_format_var(getattr(user, 'public_id', None) or "نامشخص")
    gender_raw = str(getattr(user, 'gender', '') or "").lower()
    gender_txt = ("آقا 🙋‍♂️" if gender_raw == "male" else "خانم 🙋‍♀️" if gender_raw == "female" else "نامشخص ❓")

    marital_raw = getattr(user, 'marital_status', None)
    marital_status = ("مجرد 🙋" if marital_raw == "single" else "متأهل 💍" if marital_raw == "married" else "تنظیم نشده")

    age = _safe_format_var(getattr(user, 'age', 'نامشخص') or "نامشخص")
    province = _safe_format_var((getattr(user, 'province', 'نامشخص') or "نامشخص").replace("_", " "))
    city = _safe_format_var((getattr(user, 'city', 'نامشخص') or "نامشخص").replace("_", " "))

    # NOTE: bio is HTML-escaped once already at input time
    # (bot/handlers/profile_edit.py: safe_bio = html.escape(...) before saving).
    # Don't run it through _safe_format_var (which calls html.escape again) or
    # "&" in a bio becomes visible "&amp;amp;" to every viewer. Only the
    # curly-brace guard (for the later .format() call) is still needed here.
    bio_raw = getattr(user, 'bio', None) or "تنظیم نشده"
    bio = bio_raw.replace("{", "{{").replace("}", "}}")
    tags_str = getattr(user, 'tags', None) or getattr(user, 'interests', None) or "تنظیم نشده"
    interests = _safe_format_var(tags_str)
    likes_count = getattr(user, 'likes_count', 0)

    # محاسبه داینامیک گیفت‌ها
    gifts_text = ""
    summary = gifts_summary or getattr(user, '_gifts_summary', None)
    if summary:
        gifts_parts = [f"{emoji}×{qty}" for emoji, qty in summary.items() if qty > 0]
        if gifts_parts:
            gifts_text = "🎁 <b>گیفت‌ها:</b> " + " | ".join(gifts_parts) + "\n"

    compatibility_text = ""
    if compatibility is not None:
        compatibility_text = f"\n💞 <b>میزان تفاهم:</b> {compatibility}%"
        if distance_km is not None and not is_own_profile:
            compatibility_text += f"\n📏 <b>فاصله از شما:</b> {distance_km} کیلومتر"
    elif distance_km is not None and not is_own_profile:
        compatibility_text = f"\n📏 <b>فاصله از شما:</b> {distance_km} کیلومتر"

    # اطلاعات خصوصی (سکه و تنظیمات) با آیکون‌های پریمیوم
    private_info = ""
    if is_own_profile:
        coin_balance = getattr(user, 'coin_balance', 0)
        vip_info = f"\n💎 <b>وضعیت حساب:</b> ویژه (VIP) {BLUE_TICK}" if is_vip else ""
        private_info = (
            f"\n━━━━━━━━━━━━━━━━━━━━\n"
            f"{gifts_text}"
            f"<tg-emoji emoji-id=\"5379600444098093058\">🪙</tg-emoji> <b>موجودی سکه:</b> {coin_balance} سکه"
            f"{vip_info}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{P_BELL} تنظیم حالت سایلنت: /silent\n"
            f"{P_CROSS} حذف اکانت ربات: /delete_account\n"
            f"{P_CHECK} <i>شما در حال مشاهده پروفایل خودتان هستید.</i>"
        )
    elif gifts_text:
        private_info = f"\n━━━━━━━━━━━━━━━━━━━━\n{gifts_text}"

    # آخرین بازدید با چشم و نقطه آنلاین پریمیوم
    last_seen_text = ""
    if hasattr(user, 'last_active') and user.last_active:
        try:
            last_active_dt = user.last_active
            if last_active_dt.tzinfo is None:
                last_active_dt = last_active_dt.replace(tzinfo=timezone.utc)
                
            tehran_tz = pytz.timezone('Asia/Tehran')
            local_time = last_active_dt.astimezone(tehran_tz)
            now_tehran = datetime.now(timezone.utc).astimezone(tehran_tz)
            
            diff = max(0, (now_tehran - local_time).total_seconds())

            def to_persian_num(text):
                trans = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
                return str(text).translate(trans)

            time_str = to_persian_num(local_time.strftime('%H:%M'))
            prefix = f"\n{P_EYE} <b>آخرین بازدید:</b> "

            if diff < 300:
                last_seen_text = prefix + f"آنلاین {P_ONLINE}"
            elif diff < 3600:
                mins = max(1, int(diff // 60))
                last_seen_text = prefix + f"{to_persian_num(mins)} دقیقه پیش"
            elif diff < 86400 and now_tehran.date() == local_time.date():
                last_seen_text = prefix + f"امروز {time_str}"
            elif diff < 172800 and (now_tehran.date() - local_time.date()).days == 1:
                last_seen_text = prefix + f"دیروز {time_str}"
            else:
                jalali_date = jdatetime.datetime.fromgregorian(datetime=local_time)
                date_str = to_persian_num(jalali_date.strftime('%Y/%m/%d'))
                last_seen_text = prefix + f"{date_str}"
        except Exception as e:
            logger.warning(f"Could not compute last seen: {e}")

    try:
        formatted_card = template_str.format(
            profile_title=profile_title,
            public_id=public_id,
            first_name=first_name,
            gender=gender_txt,
            marital_status=marital_status,
            age=age,
            province=province,
            city=city,
            bio=bio,
            interests=interests,
            vip_status=vip_status,
            likes_count=likes_count,
            compatibility_text=compatibility_text,
            private_info=private_info
        )
        
        # 👑 بنر سلطنتی در بالاترین بخش کارت پروفایل
        if is_vip:
            formatted_card = f"{BLUE_TICK} <b>پروفایل تایید شده (VIP)</b> {BLUE_TICK}\n━━━━━━━━━━━━━━━━━━━━\n" + formatted_card
            
        return formatted_card + last_seen_text
    except KeyError as ke:
        logger.error(f"Missing placeholder in profile template: {ke}", exc_info=True)
        return "⚠️ خطا در ساخت پروفایل (قالب ناقص)."
    except Exception as e:
        logger.error(f"Error formatting profile string: {e}", exc_info=True)
        return "⚠️ خطا در اعمال مقادیر پروفایل."
    

def chunk_html_text(text: str, max_length: int = 950) -> list[str]:
    """
    متن را بدون به هم ریختن تگ‌های HTML به قطعات کوچکتر می‌شکند.
    """
    lines = text.split("\n")
    pages: list[str] = []
    current_page = ""
    open_tags: list[str] = []

    tag_pattern = re.compile(r'<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*?)(/?)>')

    def _close_all(suffix_target: list[str]) -> None:
        for t in reversed(open_tags):
            suffix_target.append(f"</{t}>")

    def _reopen(prefix_target: list[str]) -> None:
        for t in open_tags:
            prefix_target.append(f"<{t}>")

    for line in lines:
        if len(current_page) + len(line) + 1 > max_length:
            if not current_page:
                pages.append(line[:max_length])
                current_page = line[max_length:] + "\n"
                continue

            suffix: list[str] = []
            _close_all(suffix)
            pages.append(current_page.strip() + "".join(suffix))

            prefix: list[str] = []
            _reopen(prefix)
            current_page = "".join(prefix) + line + "\n"
        else:
            current_page += line + "\n"

        for match in tag_pattern.finditer(line):
            is_closing = match.group(1) == '/'
            tag_name = match.group(2).lower()
            self_closing = match.group(4) == '/'
            if self_closing:
                continue
            if not is_closing:
                open_tags.append(tag_name)
            else:
                if open_tags and open_tags[-1] == tag_name:
                    open_tags.pop()
                else:
                    logger.debug("chunk_html_text: mismatched closing tag </%s> (top of stack=%s)", tag_name, open_tags[-1] if open_tags else None)

    if current_page.strip():
        suffix: list[str] = []
        _close_all(suffix)
        pages.append(current_page.strip() + "".join(suffix))

    return pages


def get_pagination_row(target_id: int, current_page: int, total_pages: int, is_own: bool) -> list:
    """ردیف دکمه‌های شیشه‌ای برای صفحه‌بندی پروفایل"""
    nav_row = []
    is_own_int = 1 if is_own else 0

    if current_page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"prof_page:{target_id}:{current_page - 1}:{is_own_int}"))

    nav_row.append(InlineKeyboardButton(text=f"📄 {current_page + 1} از {total_pages}", callback_data="ignore"))

    if current_page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"prof_page:{target_id}:{current_page + 1}:{is_own_int}"))

    return nav_row