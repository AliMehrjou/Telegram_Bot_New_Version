import logging
from datetime import datetime
import time
from aiogram.exceptions import TelegramBadRequest
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from matching_bot_project.bot.core.loader import redis_client
from matching_bot_project.database.queries.crud import get_user_by_tg_id
from matching_bot_project.database.models.models import User
from matching_bot_project.bot.keyboards.inline import get_vip_panel_keyboard

logger = logging.getLogger(__name__)
router = Router(name="vip_handler")


async def is_vip(db_session: AsyncSession, tg_id: int) -> bool:
    user = await get_user_by_tg_id(db_session, tg_id)
    if not user:
        return False
    if user.is_vip:
        return True
    if user.vip_expires_at and user.vip_expires_at > datetime.utcnow():
        return True
    return False


@router.callback_query(F.data == "vip_panel")
async def open_vip_panel(call: CallbackQuery, db_session: AsyncSession):
    tg_id = call.from_user.id
    if not await is_vip(db_session, tg_id):
        await call.answer("این بخش مخصوص کاربران VIP است! 💎", show_alert=True)
        return

    user = await get_user_by_tg_id(db_session, tg_id)

    try:
        await call.message.edit_reply_markup(reply_markup=get_vip_panel_keyboard(user.invisible_mode))
    except TelegramBadRequest:
        pass 
    await call.answer()


@router.callback_query(F.data == "vip_viewers")
async def show_profile_viewers(call: CallbackQuery, db_session: AsyncSession):
    tg_id = call.from_user.id
    if not await is_vip(db_session, tg_id):
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
    if not await is_vip(db_session, tg_id):
        await call.answer("دسترسی غیرمجاز.", show_alert=True)
        return

    user = await get_user_by_tg_id(db_session, tg_id)
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
    if not await is_vip(db_session, tg_id):
        await call.answer("دسترسی غیرمجاز.", show_alert=True)
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

    # Check if partner is queuing or matched
    partner_state = await redis_client.hget(f"user:state:{partner_id}", "status")
    if partner_state in ["matched", "chatting", b"matched", b"chatting"]:
        await call.answer("❌ کاربر قبلی در حال حاضر در حال چت یا مچ شدن است.", show_alert=True)
        return

    # Check if they blocked us
    is_blocked = await redis_client.sismember(f"user:{partner_id}:blocks", str(tg_id))
    if is_blocked:
        await call.answer("❌ کاربر قبلی در دسترس نیست.", show_alert=True)
        return

    # Trigger instant match!
    from matching_bot_project.bot.handlers.matching import handle_successful_match, get_user_state

    # Remove both from any queue if they are in it
    from matching_bot_project.bot.core.loader import matching_engine
    await matching_engine.remove_from_queue(tg_id)
    await matching_engine.remove_from_queue(partner_id)

    # Clear FSM states to prevent trapping users in previous states
    caller_ctx = get_user_state(tg_id)
    await caller_ctx.clear()
    
    partner_ctx = get_user_state(partner_id)
    await partner_ctx.clear()

    await call.message.answer("🔁 در حال اتصال مجدد به پارتنر قبلی...")
    await handle_successful_match(db_session, tg_id, partner_id)
    await call.answer()
