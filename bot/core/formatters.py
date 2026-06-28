import json
import html
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
import pytz
import jdatetime

logger = logging.getLogger(__name__)

def _safe_format_var(value: str) -> str:
    """
    جلوگیری از خطاهای فرمت‌دهی رشته با اسکیپ کردن کرلی بریس‌ها {}
    و ایمن‌سازی متون برای نمایش در ساختار HTML تلگرام.
    """
    escaped = html.escape(str(value))
    return escaped.replace("{", "{{").replace("}", "}}")

def build_unified_profile_card(user, is_own_profile: bool = False,
                               compatibility: Optional[int] = None,
                               distance_km: Optional[float] = None) -> str:
    """
    تابع یکپارچه و بهینه برای ساخت کارت پروفایل از روی قالب JSON.
    همراه با حل مشکل ریجن و اختلاف زمانی آخرین بازدید کاربران بر اساس تایم‌زون ایران.
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
                "وضعیت تأهل: {marital_status}\n"
                "سن: {age}\n"
                "استان: {province}\n"
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
    # درصد تفاهم و فاصله جغرافیایی
    compatibility_text = ""
    if compatibility is not None:
        compatibility_text = f"💞 <b>میزان تفاهم:</b> {compatibility}%"
        if distance_km is not None and not is_own_profile:
            compatibility_text += f"\n📏 <b>فاصله از شما:</b> {distance_km} کیلومتر"
    elif distance_km is not None and not is_own_profile:
        compatibility_text = f"📏 <b>فاصله از شما:</b> {distance_km} کیلومتر"
    
    # اطلاعات خصوصی (مخصوص لایه کاربری خود شخص)
    private_info = ""
    if is_own_profile:
        coin_balance = getattr(user, 'coin_balance', 0)
        private_info = (
            f"\n<tg-emoji emoji-id=\"5379600444098093058\">🪙</tg-cookie> <b>موجودی سکه:</b> {coin_balance} سکه\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<tg-emoji emoji-id=\"5371017798065592581\">🔔</tg-emoji> تنظیم حالت سایلنت: /silent\n"
            f"<tg-emoji emoji-id=\"5465665476971471368\">❌</tg-emoji> حذف اکانت ربات: /delete_account\n"
            f"<tg-emoji emoji-id=\"5427009714745517609\">💡</tg-emoji> <i>شما در حال مشاهده پروفایل خودتان هستید.</i>"
        )
    
    # آخرین بازدید (محاسبه هوشمند بر اساس منطقه زمانی ایران و قالب شمسی)
    last_seen_text = ""
    if not is_own_profile and hasattr(user, 'last_active') and user.last_active:
        try:
            last_active_dt = user.last_active
            # فرونشاندن ماهیت خام دیتا عودتی از MySQL به ساختار آگاهِ UTC
            if last_active_dt.tzinfo is None:
                last_active_dt = last_active_dt.replace(tzinfo=timezone.utc)
                
            # انتقال جهت جغرافیایی زمان به آسیا/تهران
            tehran_tz = pytz.timezone('Asia/Tehran')
            local_time = last_active_dt.astimezone(tehran_tz)
            now_tehran = datetime.now(tehran_tz)
            
            diff = (now_tehran - local_time).total_seconds()
            
            if diff < 300:  # کمتر از ۵ دقیقه
                last_seen_text = "\n⏱ <b>آخرین بازدید:</b> آنلاین 🟢"
            elif diff < 3600:  # کمتر از ۱ ساعت
                mins = max(1, int(diff // 60))
                last_seen_text = f"\n⏱ <b>آخرین بازدید:</b> {mins} دقیقه پیش"
            elif diff < 86400 and now_tehran.date() == local_time.date():  # امروز
                last_seen_text = f"\n⏱ <b>آخرین بازدید:</b> امروز {local_time.strftime('%H:%M')}"
            elif diff < 172800 and (now_tehran.date() - local_time.date()).days == 1:  # دیروز
                last_seen_text = f"\n⏱ <b>آخرین بازدید:</b> دیروز {local_time.strftime('%H:%M')}"
            else:  # روزهای قبل به شمسی
                jalali_date = jdatetime.datetime.fromgregorian(datetime=local_time)
                last_seen_text = f"\n⏱ <b>آخرین بازدید:</b> {jalali_date.strftime('%Y/%m/%d')}"
        except Exception as e:
            logger.warning(f"Could not compute last seen timezone calibration: {e}")
    
    # ──── ۴. مونتاژ نهایی با محافظت در برابر خطاهای فرمت ────
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
        return formatted_card + last_seen_text
    except KeyError as ke:
        logger.error(f"Missing placeholder in profile template: {ke}", exc_info=True)
        return "⚠️ خطا در ساخت پروفایل (قالب ناقص). لطفاً با پشتیبانی تماس بگیرید."
    except Exception as e:
        logger.error(f"Error formatting profile string: {e}", exc_info=True)
        return "⚠️ خطا در اعمال مقادیر پروفایل."