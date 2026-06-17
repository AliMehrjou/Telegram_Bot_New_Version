import logging
import asyncio
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.states.states import DiscoveryStates
from matching_bot_project.database.queries.crud import (
    get_user_by_tg_id,
    get_discovery_candidate,
    save_like,
    check_mutual_like
)
from matching_bot_project.bot.handlers.interactions import _build_profile_card
from matching_bot_project.bot.core.loader import redis_client, bot

logger = logging.getLogger(__name__)
router = Router(name="discovery_handler")

DAILY_LIKE_LIMIT = 30

def get_discovery_keyboard(target_id: int) -> InlineKeyboardMarkup:
    """Builds the inline keyboard for a discovery candidate."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❤️ لایک", callback_data=f"like_{target_id}"),
                InlineKeyboardButton(text="👎 پاس", callback_data=f"pass_{target_id}")
            ],
            [
                InlineKeyboardButton(text="👤 پروفایل کامل", callback_data=f"view_profile_{target_id}")
            ]
        ]
    )


async def send_next_candidate(message_or_call, db_session: AsyncSession, state: FSMContext, user_tg_id: int):
    """Fetches and sends the next discovery candidate, or a fallback message if none found."""
    user = await get_user_by_tg_id(db_session, user_tg_id)
    if not user or not user.gender:
        text = "⚠️ لطفاً ابتدا ثبت‌نام خود را از طریق منوی اصلی کامل کنید."
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.edit_text(text)
        else:
            await message_or_call.answer(text)
        return

    candidate = await get_discovery_candidate(db_session, user.tg_id, user.gender)

    if not candidate:
        text = "✨ در حال حاضر کاربر جدیدی متناسب با معیارهای شما پیدا نشد! بعداً دوباره سر بزن یا پروفایلت رو کامل‌تر کن. 🧭"
        await state.clear()
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.edit_text(text)
        else:
            await message_or_call.answer(text)
        return

    profile_card = _build_profile_card(candidate)
    keyboard = get_discovery_keyboard(candidate.tg_id)

    await state.set_state(DiscoveryStates.navigating)

    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.edit_text(profile_card, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message_or_call.answer(profile_card, reply_markup=keyboard, parse_mode="HTML")


@router.message(F.text == "💘 کشف کاربران")
async def start_discovery(message: Message, state: FSMContext, db_session: AsyncSession):
    """Triggers the discovery flow."""
    tg_id = message.from_user.id

    limit_key = f"user:{tg_id}:likes_today"
    likes_count_str = await redis_client.get(limit_key)
    likes_count = int(likes_count_str) if likes_count_str else 0

    if likes_count >= DAILY_LIKE_LIMIT:
        await message.answer("🚫 سهمیه لایک روزانه شما (۳۰ از ۳۰) به پایان رسیده است. فردا دوباره تلاش کنید! ⏰")
        return

    await send_next_candidate(message, db_session, state, tg_id)


@router.callback_query(DiscoveryStates.navigating, F.data.startswith("pass_"))
async def handle_pass(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """Handles passing a profile."""
    target_id_str = call.data.removeprefix("pass_")
    if not target_id_str.isdigit():
        await call.answer("خطا در پردازش درخواست.")
        return

    target_id = int(target_id_str)
    caller_id = call.from_user.id

    await save_like(db_session, caller_id, target_id, is_pass=True)
    await db_session.commit()

    await call.answer()
    await send_next_candidate(call, db_session, state, caller_id)


@router.callback_query(DiscoveryStates.navigating, F.data.startswith("like_"))
async def handle_like(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """Handles liking a profile, checking limits, and processing mutual likes."""
    target_id_str = call.data.removeprefix("like_")
    if not target_id_str.isdigit():
        await call.answer("خطا در پردازش درخواست.")
        return

    target_id = int(target_id_str)
    caller_id = call.from_user.id

    limit_key = f"user:{caller_id}:likes_today"
    likes_count_str = await redis_client.get(limit_key)
    likes_count = int(likes_count_str) if likes_count_str else 0

    if likes_count >= DAILY_LIKE_LIMIT:
        await call.answer("🚫 سهمیه لایک روزانه شما به پایان رسیده است.", show_alert=True)
        await state.clear()
        await call.message.edit_text("🚫 سهمیه لایک روزانه شما (۳۰ از ۳۰) به پایان رسیده است. فردا دوباره تلاش کنید! ⏰")
        return

    # Increment Redis counter
    pipe = redis_client.pipeline()
    pipe.incr(limit_key)
    if likes_count == 0:
        # Calculate seconds until next midnight to set TTL
        now = datetime.utcnow()
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_to_midnight = int((midnight - now).total_seconds())
        pipe.expire(limit_key, seconds_to_midnight)
    await pipe.execute()

    # Save the like
    await save_like(db_session, caller_id, target_id, is_pass=False)
    await db_session.commit()

    # Check for mutual like
    is_mutual = await check_mutual_like(db_session, caller_id, target_id)

    if is_mutual:
        # Mutual Like! Trigger Instant Match
        await state.clear()

        # Clear target's discovery state if they are in it to prevent collision
        from matching_bot_project.bot.handlers.matching import get_user_state
        partner_ctx = get_user_state(target_id)
        current_partner_state = await partner_ctx.get_state()
        if current_partner_state == DiscoveryStates.navigating.state:
            await partner_ctx.clear()

        caller = await get_user_by_tg_id(db_session, caller_id)
        target = await get_user_by_tg_id(db_session, target_id)

        caller_name = caller.first_name if caller else "کاربر"
        target_name = target.first_name if target else "کاربر"

        from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
        try:
            await call.message.edit_text(f"💘 شما و {target_name} همدیگرو لایک کردید! در حال اتصال... 🚀")
        except (TelegramAPIError, TelegramForbiddenError):
            pass

        try:
            await bot.send_message(target_id, f"💘 شما و {caller_name} همدیگرو لایک کردید! در حال اتصال... 🚀")
        except (TelegramAPIError, TelegramForbiddenError):
            pass

        # Call handle_successful_match locally
        from matching_bot_project.bot.handlers.matching import handle_successful_match
        await handle_successful_match(db_session, caller_id, target_id)

    else:
        # Not mutual yet, load next profile
        await call.answer()
        await send_next_candidate(call, db_session, state, caller_id)
