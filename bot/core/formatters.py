import json
import html
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

def _safe_format_var(value: str) -> str:
    """
    Escape curly braces to prevent formatting errors,
    and also keep the string HTML-safe.
    """
    # اول HTML escape
    escaped = html.escape(str(value))
    # بعد replace { و } با نسخه‌ی دوتایی برای format
    return escaped.replace("{", "{{").replace("}", "}}")

def build_unified_profile_card(user, is_own_profile: bool = False,
                               compatibility: Optional[int] = None,
                               distance_km: Optional[float] = None) -> str:
    """
    تابع یکپارچه برای ساخت کارت پروفایل از روی قالب JSON.
    """
    try:
        # ──── ۱. بارگذاری قالب ────
        json_path = Path("json_files/profile_template.json")
        if not json_path.exists():
            json_path = Path("/app/json_files/profile_template.json")

        if json_path.exists():
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            template_lines = data.get("profile_text", [])
            template_str = "\n".join(template_lines)
        else:
            logger.warning("Profile template file not found, using fallback.")
            template_str = (
                "{profile_title} {first_name}\n"
                "🆔 {public_id}\n"
                "جنسیت: {gender}\n"
                "سن: {age}\n"
                "شهر: {city}\n"
                "بیو: {bio}\n"
                "علایق: {interests}\n"
                "وضعیت: {vip_status}\n"
                "لایک: {likes_count}\n"
                "{compatibility_text}"
                "{private_info}"
            )
    except Exception as e:
        logger.error(f"Error reading profile template: {e}", exc_info=True)
        return "⚠️ خطایی در بارگذاری قالب پروفایل رخ داد."

    # ──── ۲. آماده‌سازی متغیرها (با محافظت کامل) ────
    profile_title = "👤 شما" if is_own_profile else "👤 کاربر"
    
    # اطلاعات هویتی
    public_id = getattr(user, 'public_id', None) or "نامشخص"
    first_name = _safe_format_var(getattr(user, 'first_name', None) or "کاربر")
    gender_raw = str(getattr(user, 'gender', '') or "").lower()
    gender_txt = ("آقا 🙋‍♂️" if gender_raw == "male" 
                  else "خانم 🙋‍♀️" if gender_raw == "female" 
                  else "نامشخص ❓")
    
    marital_raw = getattr(user, 'marital_status', None)
    marital_status = ("مجرد 🙋" if marital_raw == "single" 
                      else "متأهل 💍" if marital_raw == "married" 
                      else "تنظیم نشده")
    
    # اطلاعات شخصی
    age = _safe_format_var(getattr(user, 'age', 'نامشخص') or "نامشخص")
    province = _safe_format_var((getattr(user, 'province', 'نامشخص') or "نامشخص").replace("_", " "))
    city = _safe_format_var((getattr(user, 'city', 'نامشخص') or "نامشخص").replace("_", " "))
    
    # متن‌های طولانی (بیو، علایق)
    bio = _safe_format_var(getattr(user, 'bio', 'تنظیم نشده') or "تنظیم نشده")
    interests = _safe_format_var(getattr(user, 'interests', 'تنظیم نشده') or "تنظیم نشده")
    
    # وضعیت VIP و امتیازات
    is_vip = getattr(user, 'is_vip', False)
    vip_status = "👑 عضو VIP" if is_vip else "🏷️ عضو عادی"
    likes_count = getattr(user, 'likes_count', 0)
    
    # ──── ۳. محاسبه‌های داینامیک ────
    # درصد تفاهم و فاصله
    compatibility_text = ""
    if compatibility is not None:
        compatibility_text = f"💞 <b>میزان تفاهم:</b> {compatibility}%"
        if distance_km is not None and not is_own_profile:
            compatibility_text += f"\n📏 <b>فاصله از شما:</b> {distance_km} کیلومتر"
    
    # اطلاعات خصوصی (مخصوص خود کاربر)
    private_info = ""
    if is_own_profile:
        coin_balance = getattr(user, 'coin_balance', 0)
        private_info = (
            f"\n<tg-emoji emoji-id=\"5379600444098093058\">🪙</tg-emoji> <b>موجودی سکه:</b> {coin_balance} سکه\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<tg-emoji emoji-id=\"5371017798065592581\">🔔</tg-emoji> تنظیم حالت سایلنت: /silent\n"
            f"<tg-emoji emoji-id=\"5465665476971471368\">❌</tg-emoji> حذف اکانت ربات: /delete_account\n"
            f"<tg-emoji emoji-id=\"5427009714745517609\">💡</tg-emoji> <i>شما در حال مشاهده پروفایل خودتان هستید.</i>"
        )
    
    # آخرین بازدید (فقط برای پروفایل دیگران)
    last_seen_text = ""
    if not is_own_profile and hasattr(user, 'last_active') and user.last_active:
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            diff = (now - user.last_active).total_seconds()
            if diff < 3600:
                mins = max(1, int(diff // 60))
                last_seen_text = f"\n⏱ <b>آخرین بازدید:</b> {mins} دقیقه پیش"
            elif diff < 86400:
                hrs = int(diff // 3600)
                last_seen_text = f"\n⏱ <b>آخرین بازدید:</b> {hrs} ساعت پیش"
            else:
                days = int(diff // 86400)
                last_seen_text = f"\n⏱ <b>آخرین بازدید:</b> {days} روز پیش"
        except Exception as e:
            logger.warning(f"Could not compute last seen: {e}")
    
    # ──── ۴. مونتاژ نهایی با محافظت در برابر خطاهای فرمت ────
    try:
        # همه‌ی متغیرها را به صورت safe (بدون { و } آزاد) پاس می‌دهیم
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
        return formatted_card + last_seen_text
    except KeyError as ke:
        logger.error(f"Missing placeholder in profile template: {ke}", exc_info=True)
        return "⚠️ خطا در ساخت پروفایل (قالب ناقص). لطفاً با پشتیبانی تماس بگیرید."
    except Exception as e:
        logger.error(f"Error formatting profile string: {e}", exc_info=True)
        return "⚠️ خطا در اعمال مقادیر پروفایل."