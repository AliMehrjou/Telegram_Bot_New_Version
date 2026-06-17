import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from matching_bot_project.bot.states.states import ReportStates
from matching_bot_project.database.queries.crud import create_user_report, get_user_by_tg_id
from matching_bot_project.bot.handlers.interactions import execute_chat_termination, execute_user_blocking
from matching_bot_project.bot.core.loader import bot
from matching_bot_project.services.broadcast_worker import BroadcastWorker
from matching_bot_project.bot.core.config import settings
from matching_bot_project.database.models.models import MatchHistory

logger = logging.getLogger(__name__)
router = Router(name="safety_handler")

def get_report_reasons_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔞 محتوای نامناسب", callback_data=f"report_reason_inappropriate_{match_id}")],
            [InlineKeyboardButton(text="💬 اسپم", callback_data=f"report_reason_spam_{match_id}")],
            [InlineKeyboardButton(text="😠 آزار و اذیت", callback_data=f"report_reason_harassment_{match_id}")],
            [InlineKeyboardButton(text="🤖 ربات/فیک", callback_data=f"report_reason_fake_{match_id}")],
            [InlineKeyboardButton(text="❌ انصراف", callback_data="report_cancel")]
        ]
    )

async def update_trust_score(session: AsyncSession, reported_id: int) -> None:
    """Updates the user's trust score and automatically bans if they receive 5 or more reports."""
    user = await get_user_by_tg_id(session, reported_id)
    if not user:
        return

    user.trust_score = max(0, user.trust_score - 10)

    if user.report_count >= 5:
        user.is_banned = True
        logger.info(f"User {reported_id} has been auto-banned due to exceeding report limits.")


@router.callback_query(F.data.startswith("trigger_report_"))
async def prompt_report_reasons(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    target_id_str = call.data.removeprefix("trigger_report_")
    if not target_id_str.isdigit():
        await call.answer("درخواست نامعتبر.", show_alert=True)
        return

    target_id = int(target_id_str)

    # We need the match_id to associate with the report.
    # Check if they have an active match together
    stmt = select(MatchHistory.id).where(
        MatchHistory.is_active == True,
        (
            ((MatchHistory.user_one_id == call.from_user.id) & (MatchHistory.user_two_id == target_id)) |
            ((MatchHistory.user_two_id == call.from_user.id) & (MatchHistory.user_one_id == target_id))
        )
    )
    res = await db_session.execute(stmt)
    match_id = res.scalar_one_or_none()

    if not match_id:
        await call.answer("هیچ چت فعالی با این کاربر یافت نشد.", show_alert=True)
        return

    # Set FSM to keep context of the reported user
    await state.set_state(ReportStates.selecting_reason)
    await state.update_data(reported_id=target_id)

    await call.message.answer(
        "لطفاً دلیل گزارش خود را انتخاب کنید:",
        reply_markup=get_report_reasons_keyboard(match_id)
    )
    await call.answer()


@router.callback_query(ReportStates.selecting_reason, F.data == "report_cancel")
async def cancel_report(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.delete()
    await call.answer("گزارش لغو شد.")


@router.callback_query(ReportStates.selecting_reason, F.data.startswith("report_reason_"))
async def handle_report_reason(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    data = await state.get_data()
    reported_id = data.get("reported_id")
    reporter_id = call.from_user.id

    if not reported_id:
        await state.clear()
        await call.answer("خطا در پردازش. لطفاً دوباره تلاش کنید.", show_alert=True)
        return

    parts = call.data.split("_")
    reason = parts[2]
    match_id_str = parts[3]

    if not match_id_str.isdigit():
        await state.clear()
        await call.answer("خطا در پردازش.", show_alert=True)
        return

    match_id = int(match_id_str)

    # Map raw reason string to a nice Persian string
    reason_map = {
        "inappropriate": "محتوای نامناسب",
        "spam": "اسپم",
        "harassment": "آزار و اذیت",
        "fake": "ربات/فیک"
    }
    persian_reason = reason_map.get(reason, "نامشخص")

    # 1. Save the report (this will increment report_count inside crud.py)
    await create_user_report(
        session=db_session,
        reporter_id=reporter_id,
        reported_id=reported_id,
        reason=persian_reason,
        match_history_id=match_id
    )

    # 2. Update trust score & check for auto-ban
    await update_trust_score(db_session, reported_id)

    # Flush explicitly just in case, before termination logic commits
    await db_session.flush()

    # 3. Terminate the active chat (this handles the commit internally)
    await execute_chat_termination(db_session, match_id, reporter_id)

    # 4. Automatically block the user (this handles the commit internally)
    await execute_user_blocking(db_session, reporter_id, reported_id)

    # Note: execute_chat_termination and execute_user_blocking manage their own transactions.
    # It is safe because we passed the same db_session and they execute sequential commits.

    # 5. Alert the admins via BroadcastWorker
    admin_alert_text = (
        "🚨 *گزارش جدید:*\n"
        f"گزارش‌دهنده: `{reporter_id}`\n"
        f"گزارش‌شده: `{reported_id}`\n"
        f"دلیل: {persian_reason}\n"
        f"مچ: #{match_id}"
    )

    worker = BroadcastWorker(bot=bot)
    # Broadcast asynchronously to admins with 40ms delay
    worker.start_background_broadcast(user_ids=settings.parsed_admin_ids, text=admin_alert_text, delay_ms=40)

    await state.clear()
    await call.message.edit_text("✅ گزارش شما با موفقیت ثبت شد و کاربر مسدود و چت پایان یافت. با تشکر از همکاری شما.")
    await call.answer()
