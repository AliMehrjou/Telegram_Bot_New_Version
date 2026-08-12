# bot/handlers/explore.py
from __future__ import annotations

import logging
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.loader import redis_client
from matching_bot_project.database.queries import crud
from matching_bot_project.bot.core.formatters import build_unified_profile_card
from matching_bot_project.database.queries.crud import calculate_distance_km

logger = logging.getLogger(__name__)
router = Router(name="explore_handler")

_NO_RESULT_TEXT = "کاربری با این مشخصات یافت نشد."
_RESULT_HEADER_TEXT = "🔍 یک کاربر یافت شد!"

# ── Redis Cache Helpers ──────────────────────────────────────────────────────
_EXPLORE_VIEWED_SET_PREFIX = "explore:viewed"
_EXPLORE_VIEWED_SET_TTL = 60 * 60 * 6  # اعتبار ۶ ساعته برای کش جستجو

def _explore_viewed_set_key(tg_id: int) -> str:
    return f"{_EXPLORE_VIEWED_SET_PREFIX}:{tg_id}"

async def _get_explore_viewed_ids(tg_id: int, limit: int = 500) -> list[int]:
    """
    دریافت آیدی‌های دیده شده. 
    لیمیت به 500 افزایش یافت تا از نمایش تکراری افراد در یک نشست طولانی جلوگیری شود.
    """
    raw = await redis_client.smembers(_explore_viewed_set_key(tg_id))
    return [int(x) for x in raw][:limit] if raw else []


async def _add_explore_viewed_id(tg_id: int, candidate_id: int) -> None:
    key = _explore_viewed_set_key(tg_id)
    await redis_client.sadd(key, candidate_id)
    await redis_client.expire(key, _EXPLORE_VIEWED_SET_TTL)

async def _clear_explore_viewed_ids(tg_id: int) -> None:
    await redis_client.delete(_explore_viewed_set_key(tg_id))
# ─────────────────────────────────────────────────────────────────────────────

def _build_result_keyboard(found_user_tg_id: int, rerun_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💬 دایرکت (۱ سکه)", callback_data=f"req_direct_{found_user_tg_id}"),
            InlineKeyboardButton(text="👤 پروفایل کامل", callback_data=f"view_profile_{found_user_tg_id}")
        ],
        [
            InlineKeyboardButton(text="🔄 جستجوی یک نفر دیگه", callback_data=rerun_callback)
        ],
        [
            InlineKeyboardButton(text="🔙 بازگشت به منوی کشف", callback_data="open_discovery_menu")
        ]
    ])

@router.callback_query(F.data.startswith("search_") | F.data.startswith("nearby_"))
async def execute_search(call: CallbackQuery, db_read_session: AsyncSession) -> None:
    caller_tg_id = call.from_user.id

    try:
        caller = await crud.get_user_by_tg_id(db_read_session, caller_tg_id)
    except Exception as exc:
        logger.error("DB error while fetching caller %s during search: %s", caller_tg_id, exc)
        return await call.answer("❌ خطای سرور. لطفاً دوباره تلاش کنید.", show_alert=True)

    if not caller:
        return await call.answer("❌ حساب کاربری شما یافت نشد.", show_alert=True)

    is_nearby = call.data.startswith("nearby_")
    suffix = call.data.replace("nearby_", "").replace("search_", "")
    tokens = set(suffix.split("_")) if suffix else set()

    gender_filter = None
    if "female" in tokens:
        gender_filter = "female"
    elif "male" in tokens:
        gender_filter = "male"

    online_only = "online" in tokens
    same_province = "province" in tokens

    if same_province and not caller.province:
        return await call.answer("⚠️ برای این جستجو ابتدا باید استان خود را ثبت کنید!", show_alert=True)

    if is_nearby and (not caller.location_lat or not caller.location_lng):
        await call.answer("📍 لوکیشن ثبت نشده!", show_alert=True)
        
        # 🛡️ رفع باگ محدودیت ۴۸ ساعته حذف پیام تلگرام
        try:
            await call.message.delete()
        except TelegramBadRequest:
            try:
                await call.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            
        return await call.message.answer(
            "⚠️ <b>موقعیت مکانی شما ثبت نشده است!</b>\n\n"
            "برای پیدا کردن افراد نزدیک، باید لوکیشن (GPS) خود را ثبت کنید.\n"
            "لطفاً از منوی اصلی به بخش <b>«👤 پروفایل من»</b> رفته و با کلیک روی <b>«ویرایش پروفایل»</b>، موقعیت مکانی خود را بفرستید.",
            parse_mode="HTML"
        )

    viewed_ids = await _get_explore_viewed_ids(caller_tg_id, limit=500)

    try:
        candidates = await crud.get_filtered_discovery_candidates(
            session=db_read_session,
            caller_tg_id=caller_tg_id,
            province=caller.province if same_province else None,
            exclude_ids=viewed_ids,
            gender_filter=gender_filter,
            online_only=online_only,
            distance_filter="0_50" if is_nearby else None,
            limit=1,
            pool_size=100
        )
    except Exception as exc:
        logger.error("Search query failed for user %s: %s", caller_tg_id, exc)
        return await call.answer("❌ خطای سرور در جستجو. لطفاً دوباره تلاش کنید.", show_alert=True)

    found_user = candidates[0] if candidates else None

    if not found_user:
        if viewed_ids:
            await _clear_explore_viewed_ids(caller_tg_id)
            return await call.answer("تمام کاربران فیلتر شده را دیدید. جستجو از ابتدا آغاز می‌شود. دوباره کلیک کنید.", show_alert=True)
        return await call.answer(_NO_RESULT_TEXT, show_alert=True)

    await call.answer()
    await _add_explore_viewed_id(caller_tg_id, found_user.tg_id)

    # 🚀 استفاده از تابع سبک خود دیتابیس به جای سرویس سنگین پایتون
    distance_km = None
    if caller.location_lat and caller.location_lng and found_user.location_lat and found_user.location_lng:
        distance_km = calculate_distance_km(
            caller.location_lat, caller.location_lng,
            found_user.location_lat, found_user.location_lng
        )
        
    profile_card = build_unified_profile_card(found_user, distance_km=distance_km)
    result_text = f"{_RESULT_HEADER_TEXT}\n\n{profile_card}"

    try:
        await call.message.edit_text(
            text=result_text,
            reply_markup=_build_result_keyboard(found_user.tg_id, call.data),
            parse_mode="HTML"
        )
    except TelegramBadRequest: 
        await call.message.answer(
            text=result_text,
            reply_markup=_build_result_keyboard(found_user.tg_id, call.data),
            parse_mode="HTML"
        )