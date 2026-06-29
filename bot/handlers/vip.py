import logging
from datetime import datetime
import time
from aiogram.exceptions import TelegramBadRequest
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import timezone
from matching_bot_project.bot.core.loader import redis_client
from matching_bot_project.database.queries.crud import get_user_by_tg_id
from matching_bot_project.database.models.models import User
from matching_bot_project.bot.keyboards.inline import get_vip_panel_keyboard

logger = logging.getLogger(__name__)
router = Router(name="vip_handler")


def _is_vip_active(user: User) -> bool:
    """
    بررسی فعال بودن VIP روی یک آبجکت User که از قبل از دیتابیس خوانده شده.
    هیچ کوئری جدیدی به دیتابیس نمی‌زند.
    """
    if not user:
        return False
    if user.is_vip:
        return True

    if user.vip_expires_at:
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        expires = user.vip_expires_at.replace(tzinfo=None) if user.vip_expires_at.tzinfo else user.vip_expires_at
        if expires > now_naive:
            return True

    return False


async def is_vip(db_session: AsyncSession, tg_id: int) -> bool:
    """
    برای حفظ سازگاری با کدهای قدیمی نگه داشته شده.
    در هندلرهایی که از قبل آبجکت user را fetch کرده‌اند،
    به‌جای این تابع از _is_vip_active(user) استفاده کنید.
    """
    user = await get_user_by_tg_id(db_session, tg_id)
    return _is_vip_active(user)



@router.callback_query(F.data == "vip_panel")
async def open_vip_panel(call: CallbackQuery, db_session: AsyncSession):
    tg_id = call.from_user.id
    user = await get_user_by_tg_id(db_session, tg_id)
    if not _is_vip_active(user):
        await call.answer("این بخش مخصوص کاربران VIP است! 💎", show_alert=True)
        return

    vip_text = "💎 <b>پنل مدیریت VIP</b>\n\nاز امکانات زیر برای مدیریت حساب ویژه خود استفاده کنید:"
    kb = get_vip_panel_keyboard(user.invisible_mode)

    # 💡 اصلاحیه: اگر پیام قبلی عکس/صدا داشت، پیام قبلی رو پاک می‌کنیم تا کیبورد اضافه نماند
    if call.message.photo or call.message.voice or call.message.audio:
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass
        await call.message.answer(text=vip_text, reply_markup=kb, parse_mode="HTML")
    else:
        try:
            await call.message.edit_text(text=vip_text, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest:
            await call.message.answer(text=vip_text, reply_markup=kb, parse_mode="HTML")

    await call.answer()

@router.callback_query(F.data == "vip_viewers")
async def show_profile_viewers(call: CallbackQuery, db_session: AsyncSession):
    tg_id = call.from_user.id
    user = await get_user_by_tg_id(db_session, tg_id)
    if not _is_vip_active(user):
        await call.answer("دسترسی غیرمجاز.", show_alert=True)
        return

    key = f"user:{tg_id}:viewers"
    viewers = await redis_client.zrevrange(key, 0, 19, withscores=True)

    if not viewers:
        await call.answer("هیچ بازدیدکننده‌ای ثبت نشده است.", show_alert=True)
        return

    text_lines = ["👀 <b>بازدیدکنندگان اخیر پروفایل شما:</b>\n"]
    now = time.time()

    for member, score in viewers:
        viewer_id = int(member)
        viewer = await get_user_by_tg_id(db_session, viewer_id)
        if viewer:
            name = viewer.first_name or "کاربر"
            anon_name = name[:2] + "***" if len(name) >= 2 else name + "***"

            diff = now - float(score)
            if diff < 3600:
                time_str = f"{int(diff/60)} دقیقه پیش"
            elif diff < 86400:
                time_str = f"{int(diff/3600)} ساعت پیش"
            else:
                time_str = f"{int(diff/86400)} روز پیش"

            gender = "پسر" if viewer.gender in ["Male", "boy"] else "دختر" if viewer.gender in ["Female", "girl"] else "?"
            text_lines.append(f"👤 {anon_name} ({gender}) - {time_str}")

    await call.message.answer("\n".join(text_lines), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "vip_toggle_inv")
async def toggle_invisible_mode(call: CallbackQuery, db_session: AsyncSession):
    tg_id = call.from_user.id
    user = await get_user_by_tg_id(db_session, tg_id)
    if not _is_vip_active(user):
        await call.answer("دسترسی غیرمجاز.", show_alert=True)
        return

    user.invisible_mode = not user.invisible_mode
    await db_session.commit()

    status = "روشن 🟢" if user.invisible_mode else "خاموش 🔴"
    await call.answer(f"حالت مخفی {status} شد.", show_alert=True)

    try:
        await call.message.edit_reply_markup(reply_markup=get_vip_panel_keyboard(user.invisible_mode))
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "vip_rematch")
async def rematch_previous_partner(call: CallbackQuery, db_session: AsyncSession):
    tg_id = call.from_user.id
    user = await get_user_by_tg_id(db_session, tg_id)
    if not _is_vip_active(user):
        await call.answer("دسترسی غیرمجاز.", show_alert=True)
        return

    REMATCH_COST = 1
    if user.coin_balance < REMATCH_COST:
        await call.answer(
            f"❌ موجودی سکه شما برای درخواست اتصال مجدد ({REMATCH_COST} سکه) کافی نیست.",
            show_alert=True
        )
        return

    last_partner_id_str = await redis_client.get(f"user:{tg_id}:last_match_partner")
    if not last_partner_id_str:
        await call.answer("هیچ پارتنر قبلی یافت نشد.", show_alert=True)
        return

    partner_id = int(last_partner_id_str)
    partner = await get_user_by_tg_id(db_session, partner_id)

    if not partner or partner.is_banned:
        await call.answer("❌ کاربر قبلی در حال حاضر در دسترس نیست.", show_alert=True)
        return

    partner_state = await redis_client.hget(f"user:state:{partner_id}", "status")
    if partner_state in ["matched", "chatting", b"matched", b"chatting"]:
        await call.answer("❌ کاربر قبلی در حال حاضر در حال چت یا مچ شدن است.", show_alert=True)
        return

    is_blocked = await redis_client.sismember(f"user:{partner_id}:blocks", str(tg_id))
    if is_blocked:
        await call.answer("❌ کاربر قبلی در دسترس نیست.", show_alert=True)
        return

    from matching_bot_project.bot.handlers.matching import (
        handle_successful_match,
        _settle_coins_after_match
    )
    from matching_bot_project.bot.core.loader import dp, bot
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey

    def _get_user_state(user_tg_id: int) -> FSMContext:
        return FSMContext(
            storage=dp.storage,
            key=StorageKey(bot_id=bot.id, chat_id=user_tg_id, user_id=user_tg_id)
        )

    from matching_bot_project.bot.core.loader import matching_engine
    await matching_engine.remove_from_queue(tg_id)
    await matching_engine.remove_from_queue(partner_id)

    caller_ctx = _get_user_state(tg_id)
    await caller_ctx.clear()

    partner_ctx = _get_user_state(partner_id)
    await partner_ctx.clear()

    await call.message.answer(f"🔁 در حال اتصال مجدد به پارتنر قبلی... (هزینه: {REMATCH_COST} سکه در صورت موفقیت)")

    match_success = await handle_successful_match(db_session, tg_id, partner_id)
    if match_success:
        await _settle_coins_after_match(db_session, user, REMATCH_COST, partner_id)

    await call.answer()