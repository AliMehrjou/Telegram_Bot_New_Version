import html
import logging
from typing import Dict, List

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.states.states import ProfileEditStates
from matching_bot_project.database.queries.crud import update_user_profile
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard

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

def get_interests_keyboard(selected_interests: List[str]) -> InlineKeyboardMarkup:
    """Builds a dynamic multi-select keyboard for interests."""
    keyboard = []
    # Build buttons in pairs (2 per row)
    keys = list(INTERESTS.keys())
    for i in range(0, len(keys), 2):
        row = []
        for j in range(2):
            if i + j < len(keys):
                key = keys[i + j]
                label = INTERESTS[key]
                if key in selected_interests:
                    label += " ✅"
                row.append(InlineKeyboardButton(text=label, callback_data=f"interest_{key}"))
        keyboard.append(row)

    # Add save button at the bottom
    keyboard.append([InlineKeyboardButton(text="✅ تایید و ذخیره", callback_data="save_interests")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.callback_query(F.data == "edit_profile_triggered")
async def start_profile_edit(call: CallbackQuery, state: FSMContext) -> None:
    """Handles the edit profile callback from the profile view."""
    await state.set_state(ProfileEditStates.editing_bio)
    await state.update_data(selected_interests=[])

    await call.message.answer(
        "لطفاً بیوگرافی خود را بنویسید (حداکثر ۱۵۰ کاراکتر):"
    )
    await call.answer()


@router.message(ProfileEditStates.editing_bio)
async def process_bio_input(message: Message, state: FSMContext) -> None:
    """Handles the bio input text."""
    bio_text = message.text or ""
    if len(bio_text) > 150:
        await message.answer("⚠️ متن بیوگرافی طولانی است. لطفاً حداکثر در ۱۵۰ کاراکتر بنویسید:")
        return

    safe_bio = html.escape(bio_text.strip())
    await state.update_data(bio=safe_bio)

    await state.set_state(ProfileEditStates.selecting_interests)

    data = await state.get_data()
    selected_interests = data.get("selected_interests", [])

    await message.answer(
        "✅ بیوگرافی ذخیره شد.\n\n"
        "اکنون علایق خود را انتخاب کنید (می‌توانید چند مورد را انتخاب کنید):",
        reply_markup=get_interests_keyboard(selected_interests)
    )


@router.callback_query(ProfileEditStates.selecting_interests, F.data.startswith("interest_"))
async def toggle_interest(call: CallbackQuery, state: FSMContext) -> None:
    """Toggles an interest selection and updates the keyboard."""
    interest_key = call.data.removeprefix("interest_")

    if interest_key not in INTERESTS:
        await call.answer("⚠️ داده نامعتبر.", show_alert=True)
        return

    data = await state.get_data()
    selected_interests = data.get("selected_interests", [])

    if interest_key in selected_interests:
        selected_interests.remove(interest_key)
    else:
        selected_interests.append(interest_key)

    await state.update_data(selected_interests=selected_interests)

    await call.message.edit_reply_markup(
        reply_markup=get_interests_keyboard(selected_interests)
    )
    await call.answer()


@router.callback_query(ProfileEditStates.selecting_interests, F.data == "save_interests")
async def save_profile_changes(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    """Saves the bio and selected interests to the database."""
    data = await state.get_data()
    bio = data.get("bio", "")
    selected_interests = data.get("selected_interests", [])

    interests_str = ",".join(selected_interests) if selected_interests else ""

    success = await update_user_profile(
        session=db_session,
        tg_id=call.from_user.id,
        bio=bio,
        interests=interests_str
    )

    if success:
        await call.message.edit_text("✅ پروفایل شما با موفقیت بروزرسانی شد.")
    else:
        await call.message.edit_text("⚠️ خطایی در بروزرسانی پروفایل رخ داد. لطفاً دوباره تلاش کنید.")

    await state.clear()
    await call.answer()
