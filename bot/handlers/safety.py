from aiogram.filters import StateFilter
from aiogram.types import Message # اطمینان از ایمپورت Message

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

    # به جای selecting_reason، استیت را روی درخواست مدرک قرار می‌دهیم
    await state.set_state(ReportStates.waiting_for_evidence_before_reason)
    await state.update_data(reported_id=target_id, match_id=match_id)

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data="report_cancel")]
    ])

    await call.message.answer(
        "لطفاً پیامی که از طرف کاربر خاطی نقض قانون را نشان می‌دهد را فوروارد کنید.\n"
        "(این مرحله اجباری است و بدون آن گزارش ثبت نمی‌شود)",
        reply_markup=cancel_kb
    )
    await call.answer()

@router.message(ReportStates.waiting_for_evidence_before_reason, F.forward_date)
async def handle_evidence_for_safety(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    match_id = data.get("match_id")
    
    if not match_id:
        await state.clear()
        return

    # ثبت اطلاعات پیام فوروارد شده در استیت
    await state.update_data(
        forward_chat_id=message.chat.id,
        forward_message_id=message.message_id
    )
    
    await state.set_state(ReportStates.selecting_reason)
    await message.answer(
        "مدرک دریافت شد. لطفاً دلیل گزارش خود را انتخاب کنید:",
        reply_markup=get_report_reasons_keyboard(match_id)
    )

@router.message(ReportStates.waiting_for_evidence_before_reason, F.text)
async def handle_evidence_text_warning(message: Message) -> None:
    await message.answer("⚠️ لطفاً پیام کاربر خاطی را فوروارد کنید، نه متن آزاد.")

# اصلاح استیت فیلتر برای دکمه لغو
@router.callback_query(StateFilter(ReportStates.selecting_reason, ReportStates.waiting_for_evidence_before_reason), F.data == "report_cancel")
async def cancel_report(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        await call.message.edit_text("❌ گزارش لغو شد.")
    await call.answer("گزارش لغو شد.")

@router.callback_query(ReportStates.selecting_reason, F.data.startswith("report_reason_"))
async def handle_report_reason(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    data = await state.get_data()
    reported_id = data.get("reported_id")
    forward_chat_id = data.get("forward_chat_id")
    forward_message_id = data.get("forward_message_id")
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

    reason_map = {
        "inappropriate": "محتوای نامناسب",
        "spam": "اسپم",
        "harassment": "آزار و اذیت",
        "fake": "ربات/فیک"
    }
    persian_reason = reason_map.get(reason, "نامشخص")

    await create_user_report(
        session=db_session,
        reporter_id=reporter_id,
        reported_id=reported_id,
        reason=persian_reason,
        match_history_id=match_id
    )

    await update_trust_score(db_session, reported_id)
    await db_session.flush()
    await execute_chat_termination(db_session, match_id, reporter_id)
    await execute_user_blocking(db_session, reporter_id, reported_id)

    admin_alert_text = (
        "🚨 <b>گزارش جدید:</b>\n"
        f"گزارش‌دهنده: <code>{reporter_id}</code>\n"
        f"گزارش‌شده: <code>{reported_id}</code>\n"
        f"دلیل: {persian_reason}\n"
        f"مچ: #{match_id}"
    )

    # فوروارد مدرک به ادمین‌ها پیش از ارسال نوتیفیکیشن
    if forward_chat_id and forward_message_id:
        for admin_id in settings.parsed_admin_ids:
            try:
                await bot.forward_message(
                    chat_id=admin_id,
                    from_chat_id=forward_chat_id,
                    message_id=forward_message_id
                )
            except Exception as e:
                logger.error(f"Failed to forward evidence to admin {admin_id}: {e}")

    worker = BroadcastWorker(bot=bot)
    worker.start_background_broadcast(user_ids=settings.parsed_admin_ids, text=admin_alert_text, delay_ms=40)

    await state.clear()
    await call.message.edit_text("✅ گزارش شما با موفقیت ثبت شد و کاربر مسدود و چت پایان یافت. با تشکر از همکاری شما.")
    await call.answer()