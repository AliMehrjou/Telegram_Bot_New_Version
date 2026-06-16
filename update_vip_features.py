import re

file_path = 'bot/handlers/profile_edit.py'

with open(file_path, 'r') as f:
    content = f.read()

new_vip_handlers = """
@router.callback_query(F.data == "vip_viewers")
async def vip_viewers(call: CallbackQuery, db_session: AsyncSession):
    from matching_bot_project.bot.core.loader import redis_client

    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user or not user.is_vip:
        await call.answer("این ویژگی مخصوص اعضای VIP است 💎", show_alert=True)
        return

    viewers_key = f"user:{call.from_user.id}:viewers"
    viewers = await redis_client.zrevrange(viewers_key, 0, 19, withscores=True)

    if not viewers:
        await call.answer("اخیراً کسی از پروفایل شما بازدید نکرده است.", show_alert=True)
        return

    from datetime import datetime
    text = "👀 بازدیدکنندگان اخیر پروفایل:\\n"
    for viewer_id_b, score in viewers:
        viewer_id = viewer_id_b.decode() if isinstance(viewer_id_b, bytes) else viewer_id_b
        # Get user
        v_user = await crud.get_user_by_tg_id(db_session, int(viewer_id))
        name = v_user.first_name[:2] + "***" if v_user and v_user.first_name else "کاربر***"

        # Simple time format
        dt = datetime.fromtimestamp(score)
        time_str = dt.strftime("%Y-%m-%d %H:%M")

        text += f"• {name} — {time_str}\\n"

    await call.message.answer(text)
    await call.answer()

@router.callback_query(F.data == "vip_invisible")
async def vip_invisible(call: CallbackQuery, db_session: AsyncSession):
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user or not user.is_vip:
        await call.answer("این ویژگی مخصوص اعضای VIP است 💎", show_alert=True)
        return

    # Toggle
    user.invisible_mode = not user.invisible_mode
    await db_session.commit()

    status = "روشن" if user.invisible_mode else "خاموش"
    await call.message.answer(f"👁 حالت مخفی {status} شد.")
    await call.answer()

@router.callback_query(F.data == "vip_rematch")
async def vip_rematch(call: CallbackQuery, db_session: AsyncSession):
    from matching_bot_project.bot.core.loader import redis_client

    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user or not user.is_vip:
        await call.answer("این ویژگی مخصوص اعضای VIP است 💎", show_alert=True)
        return

    partner_id_b = await redis_client.get(f"user:{call.from_user.id}:last_match_partner")
    if not partner_id_b:
        await call.answer("❌ کاربر قبلی در دسترس نیست", show_alert=True)
        return

    partner_id = int(partner_id_b.decode() if isinstance(partner_id_b, bytes) else partner_id_b)

    # Generic unavailable message (covers offline, blocked, active match, invisible)
    generic_unavailable = "❌ کاربر قبلی در دسترس نیست"

    partner = await crud.get_user_by_tg_id(db_session, partner_id)
    if not partner:
        await call.answer(generic_unavailable, show_alert=True)
        return

    # Check invisible
    if getattr(partner, 'invisible_mode', False):
        await call.answer(generic_unavailable, show_alert=True)
        return

    # Check block
    is_blocked = await redis_client.sismember(f"user:{partner_id}:blocks", str(call.from_user.id))
    if is_blocked:
        await call.answer(generic_unavailable, show_alert=True)
        return

    # Check active match
    active = await crud.get_active_match(db_session, partner_id)
    if active:
        await call.answer(generic_unavailable, show_alert=True)
        return

    # Check queue (simplified, we just pull them if they are queuing)
    from matching_bot_project.bot.handlers.matching import handle_successful_match
    from matching_bot_project.bot.core.loader import matching_engine

    await matching_engine.remove_from_queue(call.from_user.id)
    await matching_engine.remove_from_queue(partner_id)

    from matching_bot_project.bot.handlers.matching import get_user_state
    await get_user_state(call.from_user.id).clear()
    await get_user_state(partner_id).clear()

    await handle_successful_match(db_session, call.from_user.id, partner_id)
    await call.answer("مچ مجدد برقرار شد!")
"""

if "vip_viewers" not in content:
    with open(file_path, 'a') as f:
        f.write(new_vip_handlers)
    print("Added VIP handlers to profile_edit.py")
