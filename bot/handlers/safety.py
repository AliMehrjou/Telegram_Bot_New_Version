"""
bot/handlers/safety.py
──────────────────────────────────────────────────────────────────────────────
Isolated safety report handler for active live chat sessions.
Requires forwarded message proof before selecting report reasons.
──────────────────────────────────────────────────────────────────────────────
"""
import logging
from typing import Optional
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from matching_bot_project.bot.handlers.interactions import execute_chat_termination_no_commit, execute_user_blocking_no_commit

from matching_bot_project.bot.core.config import settings
from matching_bot_project.bot.core.loader import bot
from matching_bot_project.bot.states.states import ReportStates
from matching_bot_project.database.models.models import MatchHistory
from matching_bot_project.database.queries.crud import create_user_report
from matching_bot_project.bot.handlers.interactions import execute_chat_termination, execute_user_blocking
from matching_bot_project.services.broadcast_worker import BroadcastWorker

logger = logging.getLogger(__name__)
router = Router(name="safety_handler")

def get_safety_report_reasons_keyboard(reported_id: int, match_id: int) -> InlineKeyboardMarkup:
    """ساخت کیبورد اختصاصی گزارش داخل چت ناشناس جهت عدم تداخل با گزارش پروفایل عمومی"""
    reasons = [
        ("عکس نامناسب 📸", "inappropriate"),
        ("اسپم و تبلیغات 📢", "spam"),
        ("آزار و اذیت و فحاشی 🤬", "harassment"),
        ("ربات یا حساب فیک 🤖", "fake")
    ]
    keyboard = [
        [InlineKeyboardButton(text=label, callback_data=f"safety_reason_{reported_id}_{match_id}_{code}")]
        for label, code in reasons
    ]
    keyboard.append([InlineKeyboardButton(text="❌ انصراف", callback_data="report_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.callback_query(F.data.startswith("trigger_report_"))
async def prompt_report_reasons(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    target_id_str = call.data.removeprefix("trigger_report_")
    if not target_id_str.isdigit():
        await call.answer("درخواست نامعتبر.", show_alert=True)
        return

    target_id = int(target_id_str)

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

    await state.set_state(ReportStates.waiting_for_evidence_before_reason)
    await state.update_data(reported_id=target_id, match_id=match_id)

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data="report_cancel")]
    ])

    await call.message.answer(
        "🚨 <b>ثبت گزارش تخلف در چت</b>\n\n"
        "لطفاً پیامی که از طرف کاربر خاطی نقض قانون را نشان می‌دهد را روی این پیام <b>فوروارد (Forward)</b> کنید.\n"
        "<i>(این مرحله جهت تایید گزارش توسط مدیریت الزامی است)</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML"
    )
    await call.answer()


@router.message(ReportStates.waiting_for_evidence_before_reason, F.forward_date)
async def handle_evidence_for_safety(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    match_id = data.get("match_id")
    reported_id = data.get("reported_id")
    
    if not match_id or not reported_id:
        await state.clear()
        return

    await state.update_data(
        forward_chat_id=message.chat.id,
        forward_message_id=message.message_id
    )
    
    await state.set_state(ReportStates.selecting_reason)
    await message.answer(
        "✅ مدرک با موفقیت دریافت شد.\n\nلطفاً دلیل اصلی گزارش خود را از منوی زیر انتخاب کنید:",
        reply_markup=get_safety_report_reasons_keyboard(reported_id, match_id)
    )


@router.message(ReportStates.waiting_for_evidence_before_reason, F.text)
async def handle_evidence_text_warning(message: Message) -> None:
    await message.answer("⚠️ لطفاً پیام حریف خاطی را فوروارد کنید، نوشتن متن آزاد به عنوان مدرک معتبر نیست.")


@router.callback_query(StateFilter(ReportStates.selecting_reason, ReportStates.waiting_for_evidence_before_reason), F.data == "report_cancel")
async def cancel_report(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        await call.message.edit_text("❌ عملیات گزارش لغو شد.")
    await call.answer("گزارش لغو شد.")


@router.callback_query(ReportStates.selecting_reason, F.data.startswith("safety_reason_"))
async def handle_report_reason(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    data = await state.get_data()
    reported_id = data.get("reported_id")
    forward_chat_id = data.get("forward_chat_id")
    forward_message_id = data.get("forward_message_id")
    reporter_id = call.from_user.id

    # ─────────────────────────────────────────────────────────────────────────
    # BUG 9 FIX: Safe callback data parsing
    # ─────────────────────────────────────────────────────────────────────────
    remainder = call.data.removeprefix("safety_reason_")
    try:
        first_sep = remainder.index("_")
        second_sep = remainder.index("_", first_sep + 1)
        
        reported_id_str = remainder[:first_sep]
        match_id_str = remainder[first_sep+1:second_sep]
        reason = remainder[second_sep+1:]
        
        match_id = int(match_id_str)
    except (ValueError, IndexError):
        await call.answer("خطا در پردازش اطلاعات.", show_alert=True)
        return

    reason_map = {
        "inappropriate": "محتوای نامناسب",
        "spam": "اسپم",
        "harassment": "آزار و اذیت و فحاشی",
        "fake": "ربات/فیک",
        "bot_fake": "ربات/فیک" # Added to support extended reason codes
    }
    persian_reason = reason_map.get(reason, "نامشخص")

    # ذخیره گزارش در دیتابیس
    await create_user_report(
        session=db_session,
        reporter_id=reporter_id,
        reported_id=reported_id,
        reason=persian_reason,
        match_history_id=match_id
    )

    # ─────────────────────────────────────────────────────────────────────────
    # BUG 10 FIX: Single commit point for transactional safety
    # ─────────────────────────────────────────────────────────────────────────
    await execute_chat_termination_no_commit(db_session, match_id, reporter_id)
    await execute_user_blocking_no_commit(db_session, reporter_id, reported_id)
    await db_session.commit()

    admin_alert_text = (
        "🚨 <b>گزارش تخلف جدید (داخل چت ناشناس)</b>\n\n"
        f"👤 شاکی: <code>{reporter_id}</code>\n"
        f"🎯 متخلف: <code>{reported_id}</code>\n"
        f"⚠️ دلیل: {persian_reason}\n"
        f"🆔 شناسه مچ: #{match_id}\n\n"
        f"👆 مدرک فوروارد شده بالا ضمیمه این گزارش است."
    )

    # ─────────────────────────────────────────────────────────────────────────
    # BUG 11 FIX: Bypass forward privacy restrictions
    # ─────────────────────────────────────────────────────────────────────────
    if forward_chat_id and forward_message_id:
        for admin_id in settings.parsed_admin_ids:
            try:
                await bot.copy_message(
                    chat_id=admin_id,
                    from_chat_id=forward_chat_id,
                    message_id=forward_message_id
                )
            except Exception as e:
                logger.error(f"Failed to copy evidence to admin {admin_id}: {e}")

    worker = BroadcastWorker(bot=bot)
    worker.start_background_broadcast(user_ids=settings.parsed_admin_ids, text=admin_alert_text, delay_ms=40)

    await state.clear()
    await call.message.edit_text("✅ گزارش شما با موفقیت ثبت شد. گفتگو پایان یافت و کاربر خاطی برای همیشه مسدود شد.")
    await call.answer()

