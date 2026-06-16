import logging
import html
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from matching_bot_project.bot.core.loader import bot, redis_client
from matching_bot_project.database.queries import crud
from matching_bot_project.database.models.models import User, BlockList, UserLike
from matching_bot_project.bot.keyboards.inline import get_discovery_keyboard
from matching_bot_project.bot.handlers.interactions import _build_profile_card
from sqlalchemy import func

logger = logging.getLogger(__name__)
router = Router(name="discovery_handler")

# MySQL uses RAND(); swap to func.random() if migrating to PostgreSQL/SQLite.
_RANDOM_FUNC = func.rand()

async def _query_discovery_user(db_session: AsyncSession, caller_tg_id: int) -> User | None:
    # Subqueries for blocks
    blocked_by_caller_sq = select(BlockList.blocked_id).where(BlockList.blocker_id == caller_tg_id).correlate(False).scalar_subquery()
    blockers_of_caller_sq = select(BlockList.blocker_id).where(BlockList.blocked_id == caller_tg_id).correlate(False).scalar_subquery()

    # Subquery for already seen
    already_seen_sq = select(UserLike.liked_id).where(UserLike.liker_id == caller_tg_id).correlate(False).scalar_subquery()

    stmt = select(User).where(
        User.tg_id != caller_tg_id,
        User.completed_registration.is_(True),
        User.tg_id.not_in(blocked_by_caller_sq),
        User.tg_id.not_in(blockers_of_caller_sq),
        User.tg_id.not_in(already_seen_sq)
    )

    # Exclude invisible users
    if hasattr(User, 'invisible_mode'):
        stmt = stmt.where(User.invisible_mode.is_(False))

    stmt = stmt.order_by(_RANDOM_FUNC).limit(1)
    result = await db_session.execute(stmt)
    return result.scalar_one_or_none()

async def show_next_discovery(message_or_call, db_session: AsyncSession, caller_tg_id: int):
    # Check limit first
    user = await crud.get_user_by_tg_id(db_session, caller_tg_id)
    if not getattr(user, 'is_vip', False):
        daily_likes = await crud.get_daily_like_count(redis_client, caller_tg_id)
        if daily_likes >= 30:
            msg = "⚡️ سقف لایک روزانه‌ات تموم شد! فردا دوباره برمیگرده یا با VIP شدن بی‌نهایت لایک بزن 💎"
            if isinstance(message_or_call, Message):
                await message_or_call.answer(msg)
            else:
                await message_or_call.message.answer(msg)
            return

    target_user = await _query_discovery_user(db_session, caller_tg_id)
    if not target_user:
        msg = "کاربر جدیدی برای نمایش وجود ندارد."
        if isinstance(message_or_call, Message):
            await message_or_call.answer(msg)
        else:
            await message_or_call.message.answer(msg)
        return

    profile_card = _build_profile_card(target_user)
    keyboard = get_discovery_keyboard(target_user.tg_id)

    if isinstance(message_or_call, Message):
        await message_or_call.answer(profile_card, reply_markup=keyboard)
    else:
        await message_or_call.message.edit_text(profile_card, reply_markup=keyboard)


@router.message(F.text == "💘 کشف کاربران")
async def discovery_entry(message: Message, db_session: AsyncSession):
    await show_next_discovery(message, db_session, message.from_user.id)


@router.callback_query(F.data.startswith("discovery_"))
async def discovery_action(call: CallbackQuery, db_session: AsyncSession):
    action, target_id_str = call.data.removeprefix("discovery_").split("_")
    target_id = int(target_id_str)
    caller_tg_id = call.from_user.id

    is_pass = (action == "pass")

    if not is_pass:
        # Check limit again for like
        user = await crud.get_user_by_tg_id(db_session, caller_tg_id)
        if not getattr(user, 'is_vip', False):
            daily_likes = await crud.get_daily_like_count(redis_client, caller_tg_id)
            if daily_likes >= 30:
                await call.answer("⚡️ سقف لایک روزانه‌ات تموم شد! فردا دوباره برمیگرده یا با VIP شدن بی‌نهایت لایک بزن 💎", show_alert=True)
                return

        await crud.increment_daily_like_count(redis_client, caller_tg_id)

    # Save action
    await crud.save_like(db_session, caller_tg_id, target_id, is_pass)
    await db_session.commit()

    if not is_pass:
        # Check mutual
        mutual = await crud.check_mutual_like(db_session, caller_tg_id, target_id)
        if mutual:
            target_user = await crud.get_user_by_tg_id(db_session, target_id)
            caller_user = await crud.get_user_by_tg_id(db_session, caller_tg_id)

            # Send notification
            await call.message.answer(f"💘 شما و {target_user.first_name} همدیگرو لایک کردید! در حال اتصال...")
            try:
                await bot.send_message(chat_id=target_id, text=f"💘 شما و {caller_user.first_name} همدیگرو لایک کردید! در حال اتصال...")
            except Exception:
                pass

            # Trigger instant match
            from matching_bot_project.bot.handlers.matching import handle_successful_match
            from matching_bot_project.bot.core.loader import matching_engine
            # Remove from queues if they were in any
            await matching_engine.remove_from_queue(caller_tg_id)
            await matching_engine.remove_from_queue(target_id)

            # Clear FSM contexts
            from matching_bot_project.bot.handlers.matching import get_user_state
            await get_user_state(caller_tg_id).clear()
            await get_user_state(target_id).clear()

            await handle_successful_match(db_session, caller_tg_id, target_id)
            await call.answer("Mutual Like!")
            return

    # Show next user
    await show_next_discovery(call, db_session, caller_tg_id)
    await call.answer()
