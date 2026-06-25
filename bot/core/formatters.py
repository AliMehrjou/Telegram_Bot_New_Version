import json
import html
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def build_unified_profile_card(user, is_own_profile: bool = False, compatibility: Optional[int] = None) -> str:
    """
    تابع یکپارچه برای ساخت کارت پروفایل از روی قالب JSON.
    """
    try:
        json_path = Path("json_files/profile_template.json")
        if not json_path.exists():
            # مسیر جایگزین برای محیط داکر
            json_path = Path("/app/json_files/profile_template.json")
        
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        template_lines = data.get("profile_text", [])
        template_str = "\n".join(template_lines)
    except Exception as e:
        logger.error(f"Error reading profile template: {e}")
        template_str = "⚠️ خطایی در بارگذاری قالب پروفایل رخ داد."

    # آماده‌سازی متغیرها
    profile_title = "شما" if is_own_profile else "کاربر"
    public_id = getattr(user, 'public_id', None) or "نامشخص"
    
    first_name = html.escape(str(user.first_name or "کاربر"))
    
    gender_raw = str(user.gender or "").lower()
    gender_txt = "آقا 🙋‍♂️" if gender_raw == "male" else "خانم 🙋‍♀️" if gender_raw == "female" else "نامشخص ❓"
    
    age = html.escape(str(user.age or "نامشخص"))
    province = html.escape(str(user.province or "نامشخص").replace("_", " "))
    city = html.escape(str(user.city or "نامشخص").replace("_", " "))
    
    bio = html.escape(str(user.bio or "تنظیم نشده"))
    interests = html.escape(str(user.interests or "تنظیم نشده"))
    
    is_vip = getattr(user, 'is_vip', False)
    vip_status = "👑 عضو VIP" if is_vip else "🏷️ عضو عادی"
    
    likes_count = getattr(user, 'likes_count', 0)
    
    compatibility_text = f"\n💞 <b>میزان تفاهم:</b> {compatibility}%" if compatibility is not None else ""
    
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

    
    try:
        return template_str.format(
            profile_title=profile_title,
            public_id=public_id,
            first_name=first_name,
            gender=gender_txt,
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
    except Exception as e:
        logger.error(f"Error formatting profile string: {e}")
        return "⚠️ خطا در اعمال مقادیر پروفایل."