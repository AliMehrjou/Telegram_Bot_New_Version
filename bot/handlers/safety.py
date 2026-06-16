import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from matching_bot_project.bot.core.loader import bot, redis_client
from matching_bot_project.bot.core.config import settings
from matching_bot_project.database.queries import crud
from matching_bot_project.database.models.models import UserReport, User, MatchHistory
from matching_bot_project.bot.states.states import ChatStates
from aiogram.fsm.context import FSMContext

logger = logging.getLogger(__name__)
router = Router(name="safety_handler")

def get_report_reason_keyboard(target_id: int, match_id: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔞 محتوای نامناسب", callback_data=f"report_{target_id}_{match_id}_inappropriate")],
        [InlineKeyboardButton(text="💬 اسپم", callback_data=f"report_{target_id}_{match_id}_spam")],
        [InlineKeyboardButton(text="😠 آزار و اذیت", callback_data=f"report_{target_id}_{match_id}_harassment")],
        [InlineKeyboardButton(text="🤖 ربات/فیک", callback_data=f"report_{target_id}_{match_id}_fake")],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="report_cancel")]
    ])

# Added to active chat controls in a later script update

@router.callback_query(F.data.startswith("start_report_"))
async def start_report_flow(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    # Expected format: start_report_targetId
    parts = call.data.split("_")
    if len(parts) != 3:
        await call.answer("خطا در یافتن اطلاعات کاربر.", show_alert=True)
        return

    target_id = int(parts[2])

    # Try to find current match if any
    match_id = 0
    fsm_data = await state.get_data()
    if fsm_data.get("match_history_id"):
        match_id = fsm_data.get("match_history_id")

    await call.message.edit_reply_markup(reply_markup=get_report_reason_keyboard(target_id, match_id))
    await call.answer()

@router.callback_query(F.data == "report_cancel")
async def cancel_report_flow(call: CallbackQuery):
    # Simplest way: edit message to say report cancelled or remove keyboard
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("گزارش لغو شد.")
    await call.answer()

@router.callback_query(F.data.startswith("report_"))
async def process_report(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    # report_{target_id}_{match_id}_{reason}
    parts = call.data.split("_")
    if len(parts) != 4:
        return

    target_id = int(parts[1])
    match_id = int(parts[2])
    reason = parts[3]
    reporter_id = call.from_user.id

    # Save report
    report = UserReport(
        reporter_id=reporter_id,
        reported_id=target_id,
        reason=reason,
        match_history_id=match_id if match_id > 0 else None
    )
    db_session.add(report)

    # Update Trust Score
    user = await crud.get_user_by_tg_id(db_session, target_id)
    if user:
        user.report_count += 1
        user.trust_score = max(0, user.trust_score - 10)

        if user.report_count >= 5 and not getattr(user, 'is_banned', False):
            user.is_banned = True
            try:
                await bot.send_message(
                    chat_id=target_id,
                    text="🚫 حساب شما به دلیل دریافت گزارش‌های مکرر از طرف کاربران تعلیق شد. برای اعتراض با پشتیبانی تماس بگیرید."
                )
            except Exception:
                pass

            # Notify admins of ban
            from matching_bot_project.services.broadcast_worker import BroadcastWorker
            worker = BroadcastWorker(bot=bot)
            worker.start_background_broadcast(
                user_ids=settings.parsed_admin_ids,
                text=f"🚨 سیستم کاربری را به طور خودکار مسدود کرد:\nکاربر: {target_id}\nبه دلیل دریافت ۵ گزارش.",
                delay_ms=40
            )

    # Auto block logic (just like when user manually blocks)
    from matching_bot_project.database.models.models import BlockList
    from sqlalchemy.exc import IntegrityError
    db_session.add(BlockList(blocker_id=reporter_id, blocked_id=target_id))

    try:
        await db_session.commit()
        await redis_client.sadd(f"user:{reporter_id}:blocks", str(target_id))
    except IntegrityError:
        await db_session.rollback()
        # Already blocked, that's fine

    # Alert Admins of new report
    admin_msg = f"🚨 گزارش جدید:\nگزارش‌دهنده: {reporter_id}\nگزارش‌شده: {target_id}\nدلیل: {reason}\nمچ: #{match_id}"
    for admin_id in settings.parsed_admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=admin_msg)
        except Exception:
            pass

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("✅ گزارش شما ثبت شد و کاربر مسدود گردید. با تشکر از همکاری شما در حفظ امنیت.")

    # End match if it was active
    from matching_bot_project.bot.handlers.interactions import end_date_early
    # Construct a mock callback to reuse end date logic
    class MockCall:
        def __init__(self, from_user_id, match_id_val):
            self.from_user = type('obj', (object,), {'id': from_user_id})
            self.data = f"end_date_early_{match_id_val}"
        async def answer(self, *args, **kwargs): pass

    if match_id > 0:
        await end_date_early(MockCall(reporter_id, match_id), db_session)
