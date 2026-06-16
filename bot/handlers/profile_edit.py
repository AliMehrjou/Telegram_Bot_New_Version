import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from matching_bot_project.bot.states.states import ProfileEditStates
from matching_bot_project.database.queries import crud
from matching_bot_project.bot.keyboards.reply import get_cancel_keyboard, get_main_menu_keyboard

logger = logging.getLogger(__name__)
router = Router(name="profile_edit_handler")

INTERESTS = {
    "gaming": "🎮 گیمینگ",
    "music": "🎵 موزیک",
    "travel": "✈️ سفر",
    "movies": "🎬 فیلم",
    "sports": "⚽️ ورزش",
    "reading": "📚 مطالعه",
    "cooking": "🍳 آشپزی",
    "art": "🎨 هنر",
    "tech": "💻 تکنولوژی",
    "nature": "🌿 طبیعت",
}

def get_interest_keyboard(selected_keys: list[str]) -> InlineKeyboardMarkup:
    buttons = []
    # Layout in pairs
    row = []
    for key, label in INTERESTS.items():
        text = f"✅ {label}" if key in selected_keys else label
        row.append(InlineKeyboardButton(text=text, callback_data=f"interest_{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text="✅ تایید و ذخیره", callback_data="interest_confirm")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "edit_profile")
async def start_profile_edit(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    await call.message.answer("لطفاً یک بیوگرافی کوتاه برای خود بنویسید (حداکثر ۱۵۰ کاراکتر):", reply_markup=get_cancel_keyboard())
    await state.set_state(ProfileEditStates.editing_bio)
    await call.answer()


@router.message(ProfileEditStates.editing_bio)
async def process_bio(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text == "❌ انصراف و منوی اصلی":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=get_main_menu_keyboard())
        return

    bio_text = message.text[:150]
    await state.update_data(bio=bio_text, selected_interests=[])

    await message.answer("✅ بیو ثبت شد.\nحالا علایق خود را انتخاب کنید:", reply_markup=get_interest_keyboard([]))
    await state.set_state(ProfileEditStates.selecting_interests)


@router.callback_query(ProfileEditStates.selecting_interests, F.data.startswith("interest_"))
async def process_interest_selection(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    data = await state.get_data()
    selected = data.get("selected_interests", [])

    action = call.data.removeprefix("interest_")

    if action == "confirm":
        # Save to DB
        bio = data.get("bio")
        interests_str = ",".join(selected)

        user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
        if user:
            user.bio = bio
            user.interests = interests_str
            await db_session.commit()

            # Use import inside method to avoid circular deps
            from matching_bot_project.bot.handlers.start import view_user_profile

            await call.message.edit_text("✅ پروفایل شما با موفقیت بروزرسانی شد.")
            await state.clear()

            # Show updated profile
            await view_user_profile(call.message, db_session)
        else:
            await call.answer("خطا در یافتن کاربر.")
        return

    # Toggle selection
    if action in selected:
        selected.remove(action)
    else:
        if len(selected) >= 5:
            await call.answer("حداکثر ۵ علاقه می‌توانید انتخاب کنید.", show_alert=True)
            return
        selected.append(action)

    await state.update_data(selected_interests=selected)
    await call.message.edit_reply_markup(reply_markup=get_interest_keyboard(selected))
    await call.answer()


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
    text = "👀 بازدیدکنندگان اخیر پروفایل:\n"
    for viewer_id_b, score in viewers:
        viewer_id = viewer_id_b.decode() if isinstance(viewer_id_b, bytes) else viewer_id_b
        # Get user
        v_user = await crud.get_user_by_tg_id(db_session, int(viewer_id))
        name = v_user.first_name[:2] + "***" if v_user and v_user.first_name else "کاربر***"

        # Simple time format
        dt = datetime.fromtimestamp(score)
        time_str = dt.strftime("%Y-%m-%d %H:%M")

        text += f"• {name} — {time_str}\n"

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
