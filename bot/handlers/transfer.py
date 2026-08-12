"""
bot/handlers/transfer.py
──────────────────────────────────────────────────────────────────────────────
Peer-to-peer coin transfer flow with security hardening.

Security guarantees:
  1. Strict input validation — rejects zero, negative, overflow, and
     non-ASCII digit amounts.
  2. Atomic balance mutation using SELECT … FOR UPDATE row locks on
     both sender and receiver rows (deadlock-free via ascending-id
     lock ordering).
  3. Block-list re-check inside the locked transaction (defense-in-depth).
  4. FSM state restoration that never drops anonymous-chat metadata
     (partner_id, match_history_id) — no zombie states.
  5. Privacy Protection: The receiver is NOT notified of the sender's 
     real name. They only get an anonymous button to view the profile.

Entry point: callback `transfer_coin_{target_tg_id}` fired from
get_user_action_keyboard() in bot/keyboards/inline.py.
──────────────────────────────────────────────────────────────────────────────
"""
import html
import logging

from matching_bot_project.database.queries import crud
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.loader import bot
from matching_bot_project.bot.keyboards.reply import (
    get_cancel_keyboard,
    get_main_menu_keyboard,
    get_chat_phase_keyboard,
)
from matching_bot_project.bot.states.states import ChatStates, CoinTransferStates, ManualTransferStates
from matching_bot_project.database.queries.crud import get_user_by_tg_id
from matching_bot_project.database.models.models import User, BlockList
from matching_bot_project.bot.core.constants import ReplyBtn

logger = logging.getLogger(__name__)
router = Router(name="transfer_handler")

# ════════════════════════════════════════════════════════════════════════════
# Constants
# ════════════════════════════════════════════════════════════════════════════

_MAX_TRANSFER = 1_000          # per-transaction cap (business rule)
_DB_BIGINT_MAX = (2**63) - 1   # PostgreSQL BIGINT maximum (overflow guard)

# Keys that must survive the transfer flow if user was in an anonymous chat
_CHAT_METADATA_KEYS = (
    "partner_id",
    "match_history_id",
    "partner_tg_id",
    "match_type",
    "chat_started_at",
    "chat_role",
)

# Temporary keys used only during the transfer flow — cleaned up on restore
_TRANSFER_TEMP_KEYS = frozenset({
    "target_id",
    "target_name",
    "amount",
    "sender_tg_id",
    "__transfer_prev_state__",
    "__transfer_chat_meta_snapshot__",
})

# FSM states that should never be restored-to (prevents zombie transfer loops)
_TRANSFER_STATES = frozenset({
    CoinTransferStates.waiting_for_amount.state,
    CoinTransferStates.confirming.state,
})

# Persian/Arabic digit → ASCII translation table
_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_DIGIT_TRANS = str.maketrans(
    _PERSIAN_DIGITS + _ARABIC_DIGITS,
    "0123456789" + "0123456789",
)


# ════════════════════════════════════════════════════════════════════════════
# FSM State Preservation & Restoration
# ════════════════════════════════════════════════════════════════════════════

async def _preserve_current_state(state: FSMContext) -> None:
    """
    Snapshot the current FSM state and all anonymous-chat metadata before
    entering the transfer flow.
    """
    current_state = await state.get_state()
    data = await state.get_data()
    chat_meta_snapshot = {
        k: data[k] for k in _CHAT_METADATA_KEYS if k in data
    }
    await state.update_data(
        __transfer_prev_state__=current_state,
        __transfer_chat_meta_snapshot__=chat_meta_snapshot,
    )

async def _restore_previous_state(state: FSMContext, db_session: AsyncSession, tg_id: int) -> str | None:
    """
    Restore the FSM to its pre-transfer state, guaranteeing that anonymous-chat
    metadata (partner_id, match_history_id, …) is intact.
    """
    data = await state.get_data()
    previous_state = data.pop("__transfer_prev_state__", None)
    chat_meta_snapshot = data.pop("__transfer_chat_meta_snapshot__", {})

    for key in _TRANSFER_TEMP_KEYS:
        data.pop(key, None)

    for key, value in chat_meta_snapshot.items():
        if data.get(key) is None and value is not None:
            data[key] = value

    # --- گارد امنیتی ضد زامبی ---
    is_pipeline = previous_state and any(p in previous_state.lower() for p in ["chat", "matching", "questionnaire"])
    if is_pipeline:
        from matching_bot_project.database.queries import crud
        active_match = await crud.get_active_match(db_session, tg_id)
        if not active_match:
            await state.clear()
            return None
    # ----------------------------------------

    if previous_state in _TRANSFER_STATES:
        previous_state = None

    await state.set_data(data)
    
    # 👇 کلید حل مشکل قفل شدن استیت
    if previous_state:
        await state.set_state(previous_state)
    else:
        await state.set_state(None) # 👈 خروج کامل از استیت انتقال سکه

    return previous_state

async def _was_in_anonymous_chat(
    prev_state: str | None, state: FSMContext
) -> bool:
    """
    Determine whether the user was in an active anonymous chat before the
    transfer, so we can show the chat keyboard instead of the main menu.
    """
    if prev_state == ChatStates.anonymous_chat_active.state:
        return True
    data = await state.get_data()
    return bool(data.get("partner_id") or data.get("match_history_id"))


# ════════════════════════════════════════════════════════════════════════════
# Atomic Transfer Execution (SELECT … FOR UPDATE)
# ════════════════════════════════════════════════════════════════════════════

async def _execute_atomic_transfer(
    db_session: AsyncSession,
    sender_tg_id: int,
    receiver_tg_id: int,
    amount: int,
) -> tuple[bool, str]:
    """
    Execute an atomic coin transfer using SELECT … FOR UPDATE row locks.
    """
    if sender_tg_id == receiver_tg_id:
        return False, "❌ نمی‌توان به خود سکه منتقل کرد."
    if amount <= 0:
        return False, "⚠️ مقدار انتقال باید مثبت باشد."
    if amount > _DB_BIGINT_MAX:
        return False, "⚠️ مقدار انتقال بیش از حد مجاز است."

    first_id, second_id = sorted([sender_tg_id, receiver_tg_id])

    lock_stmt = (
        select(User)
        .where(User.tg_id.in_([first_id, second_id]))
        .order_by(User.tg_id)
        .with_for_update()
    )
    result = await db_session.execute(lock_stmt)
    locked_users: dict[int, User] = {
        u.tg_id: u for u in result.scalars().all()
    }

    sender = locked_users.get(sender_tg_id)
    receiver = locked_users.get(receiver_tg_id)

    if sender is None:
        return False, "❌ حساب فرستنده یافت نشد."
    if receiver is None:
        return False, "❌ حساب گیرنده یافت نشد."

    block_stmt = select(BlockList).where(
        or_(
            and_(
                BlockList.blocker_id == sender_tg_id,
                BlockList.blocked_id == receiver_tg_id,
            ),
            and_(
                BlockList.blocker_id == receiver_tg_id,
                BlockList.blocked_id == sender_tg_id,
            ),
        )
    )
    block_result = await db_session.execute(block_stmt)
    if block_result.scalar_one_or_none() is not None:
        return False, "🚫 امکان انتقال سکه به این کاربر وجود ندارد."

    if sender.coin_balance < amount:
        return False, (
            f"⚠️ موجودی کافی نیست. موجودی فعلی: {sender.coin_balance} سکه."
        )

    new_sender_balance = sender.coin_balance - amount
    new_receiver_balance = receiver.coin_balance + amount

    if new_sender_balance < 0:
        return False, "⚠️ خطای محاسباتی: موجودی منفی."
    if new_receiver_balance > _DB_BIGINT_MAX:
        return False, "⚠️ ظرفیت کیف پول گیرنده تکمیل است."

    sender.coin_balance = new_sender_balance
    receiver.coin_balance = new_receiver_balance

    logger.info(
        "Atomic transfer executed: sender=%s receiver=%s amount=%s "
        "sender_balance=%s receiver_balance=%s",
        sender_tg_id, receiver_tg_id, amount,
        new_sender_balance, new_receiver_balance,
    )

    return True, f"✅ {amount} سکه با موفقیت منتقل شد."


# ════════════════════════════════════════════════════════════════════════════
# Input Validation
# ════════════════════════════════════════════════════════════════════════════

def _parse_and_validate_amount(raw_text: str | None) -> tuple[int | None, str | None]:
    """
    Parse and strictly validate a transfer amount.
    """
    if not raw_text or not raw_text.strip():
        return None, "⚠️ لطفاً یک عدد صحیح وارد کنید."

    normalized = raw_text.strip().translate(_DIGIT_TRANS)

    if not normalized.isdigit():
        return None, "⚠️ لطفاً یک عدد صحیح مثبت وارد کنید (بدون علامت یا اعشار)."

    if len(normalized) > 15:
        return None, (
            f"⚠️ عدد وارد شده بیش از حد بزرگ است. "
            f"حداکثر {_MAX_TRANSFER} سکه در هر تراکنش مجاز است."
        )

    try:
        amount = int(normalized)
    except ValueError:
        return None, "⚠️ عدد وارد شده نامعتبر است."

    if amount <= 0:
        return None, "⚠️ مقدار باید بیشتر از صفر باشد."
    if amount > _MAX_TRANSFER:
        return None, f"⚠️ حداکثر {_MAX_TRANSFER} سکه در هر بار مجاز است."

    return amount, None


# ════════════════════════════════════════════════════════════════════════════
# Helper: confirmation keyboard
# ════════════════════════════════════════════════════════════════════════════

def _confirm_keyboard(target_id: int, amount: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"✅ تأیید انتقال {amount} سکه",
            callback_data=f"transfer_confirm_{target_id}_{amount}",
        )],
        [InlineKeyboardButton(text="❌ لغو", callback_data="transfer_cancel")],
    ])


# ════════════════════════════════════════════════════════════════════════════
# Step 1: Entry point — `transfer_coin_{target_tg_id}`
# ════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("transfer_coin_"))
async def start_coin_transfer(
    call: CallbackQuery, state: FSMContext, db_session: AsyncSession
) -> None:
    # 👇 🛡️ گارد امنیتی ضد زامبی: جلوگیری از کلیک وسط دیت
    from matching_bot_project.database.queries import crud
    active_match = await crud.get_active_match(db_session, call.from_user.id)
    if active_match:
        return await call.answer("⚠️ شما در حال حاضر در یک چت/دیت فعال هستید و نمی‌توانید انتقال سکه انجام دهید.", show_alert=True)
    # 👆 پایان گارد امنیتی

    target_id_str = call.data.removeprefix("transfer_coin_")
    if not target_id_str.isdigit():
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    target_id = int(target_id_str)
    caller_id = call.from_user.id

    if target_id == caller_id:
        await call.answer("نمی‌توانید به خودتان سکه منتقل کنید!", show_alert=True)
        return

    block_check = await db_session.execute(
        select(BlockList).where(
            or_(
                and_(BlockList.blocker_id == caller_id, BlockList.blocked_id == target_id),
                and_(BlockList.blocker_id == target_id,  BlockList.blocked_id == caller_id),
            )
        )
    )
    if block_check.scalar_one_or_none():
        await call.answer(
            "🚫 امکان انتقال سکه به این کاربر وجود ندارد.",
            show_alert=True,
        )
        return

    target_user = await get_user_by_tg_id(db_session, target_id)
    if not target_user:
        await call.answer("❌ کاربر مورد نظر یافت نشد.", show_alert=True)
        return

    caller_user = await get_user_by_tg_id(db_session, caller_id)
    if not caller_user:
        await call.answer("❌ خطای سیستم.", show_alert=True)
        return

    await _preserve_current_state(state)

    await state.set_state(CoinTransferStates.waiting_for_amount)
    await state.update_data(
        target_id=target_id,
        target_name=target_user.first_name or "کاربر",
        sender_tg_id=caller_id,
    )

    await call.answer()
    await call.message.answer(
        f"🪙 <b>انتقال سکه به:</b> {target_user.first_name}\n\n"
        f"موجودی فعلی شما: <b>{caller_user.coin_balance}</b> سکه\n\n"
        f"چند سکه می‌خواهید منتقل کنید؟ (حداکثر {_MAX_TRANSFER})",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )

# ════════════════════════════════════════════════════════════════════════════
# Step 2: Receive amount
# ════════════════════════════════════════════════════════════════════════════

@router.message(CoinTransferStates.waiting_for_amount)
async def receive_transfer_amount(
    message: Message, state: FSMContext, db_session: AsyncSession
) -> None:
    if message.text == ReplyBtn.CANCEL:
        # آپدیت شده:
        prev = await _restore_previous_state(state, db_session, message.from_user.id)
        if await _was_in_anonymous_chat(prev, state):
            await message.answer(
                "❌ انتقال لغو شد. شما به چت ناشناس برگشتید. 🟢",
                reply_markup=get_chat_phase_keyboard(),
            )
        else:
            await message.answer(
                "❌ انتقال لغو شد.",
                reply_markup=get_main_menu_keyboard(),
            )
        return

    amount, error = _parse_and_validate_amount(message.text)
    if error:
        await message.reply(error)
        return

    assert amount is not None

    caller_user = await get_user_by_tg_id(db_session, message.from_user.id)
    if not caller_user or caller_user.coin_balance < amount:
        balance = caller_user.coin_balance if caller_user else 0
        await message.reply(f"⚠️ موجودی کافی نیست. موجودی فعلی: {balance} سکه.")
        return

    data = await state.get_data()
    target_id = data.get("target_id")
    if not target_id:
        await _restore_previous_state(state)
        await message.reply("⚠️ سشن شما منقضی شده است. لطفاً مجدداً تلاش کنید.")
        return

    target_name = data.get("target_name", "کاربر")

    await state.update_data(amount=amount)
    await state.set_state(CoinTransferStates.confirming)

    await message.answer(
        f"⚠️ <b>تأیید انتقال:</b>\n\n"
        f"گیرنده: <b>{target_name}</b>\n"
        f"مقدار: <b>{amount}</b> سکه\n\n"
        "آیا اطمینان دارید؟",
        reply_markup=_confirm_keyboard(target_id, amount),
        parse_mode="HTML",
    )


# ════════════════════════════════════════════════════════════════════════════
# Step 3: Final confirmation — `transfer_confirm_{target_id}_{amount}`
# ════════════════════════════════════════════════════════════════════════════
@router.callback_query(CoinTransferStates.confirming, F.data.startswith("transfer_confirm_"))
async def confirm_transfer(
    call: CallbackQuery, state: FSMContext, db_session: AsyncSession
) -> None:
    data = await state.get_data()
    target_id: int | None = data.get("target_id")
    amount: int | None = data.get("amount")
    sender_tg_id: int | None = data.get("sender_tg_id")

    if not target_id or not amount or not sender_tg_id:
        await _restore_previous_state(state, db_session, call.from_user.id)
        await call.answer("⚠️ نشست شما منقضی شده یا نامعتبر است.", show_alert=True)
        return

    if sender_tg_id != call.from_user.id:
        await call.answer("⚠️ دسترسی غیرمجاز.", show_alert=True)
        return

    try:
        success, msg = await _execute_atomic_transfer(
            db_session,
            sender_tg_id=call.from_user.id,
            receiver_tg_id=target_id,
            amount=amount,
        )
        if success:
            await db_session.commit()
            
            import secrets as _secrets
            from matching_bot_project.bot.core.loader import redis_client as _redis
            opaque_token = _secrets.token_urlsafe(12)
            try:
                await _redis.setex(
                    f"transfer:view_profile:{opaque_token}",
                    3600,
                    str(sender_tg_id),
                )
            except Exception as e:
                logger.warning("transfer: failed to store view-profile token: %s", e)
                opaque_token = ""
                
            profile_kb = InlineKeyboardMarkup(inline_keyboard=[])
            if opaque_token:
                profile_kb.inline_keyboard.append([
                    InlineKeyboardButton(
                        text="👤 مشاهده پروفایل فرستنده",
                        callback_data=f"sender_profile_token_{opaque_token}"
                    )
                ])
            
            try:
                from matching_bot_project.bot.core.loader import bot
                await bot.send_message(
                    chat_id=target_id,
                    text=(
                        f"🎁 <b>{amount} سکه</b> از طرف یک کاربر به حساب شما واریز شد! 🪙\n\n"
                        "برای مشاهده پروفایل فرستنده می‌توانید روی دکمه زیر بزنید:"
                    ),
                    parse_mode="HTML",
                    reply_markup=profile_kb
                )
            except Exception:
                logger.info("Could not notify receiver %s about incoming transfer.", target_id)
        else:
            await db_session.rollback()
    except Exception as e:
        await db_session.rollback()
        logger.error("Transfer execution failed: %s", e, exc_info=True)
        success, msg = False, "❌ خطای سیستمی رخ داد. لطفاً مجدداً تلاش کنید."

    
    prev_state = await _restore_previous_state(state, db_session, call.from_user.id)
    in_chat = await _was_in_anonymous_chat(prev_state, state)

    await call.answer(msg, show_alert=True)
    
    # 🌟 استفاده از bot.send_message برای اطمینان از جایگزینی کیبورد
    from matching_bot_project.bot.core.loader import bot
    if in_chat:
        await bot.send_message(
            chat_id=call.from_user.id,
            text="🟢 عملیات پایان یافت. به چت برگشتید.", 
            reply_markup=get_chat_phase_keyboard()
        )
    else:
        await bot.send_message(
            chat_id=call.from_user.id,
            text="🔙 عملیات پایان یافت. به منوی اصلی بازگشتید.", 
            reply_markup=get_main_menu_keyboard()
        )

    # 🌟 حذف پیام شیشه‌ای پس از ارسال کیبورد جدید
    try:
        await call.message.delete()
    except Exception:
        pass

# ════════════════════════════════════════════════════════════════════════════
# Cancel handlers
# ════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "transfer_cancel")
async def cancel_transfer(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    prev_state = await _restore_previous_state(state, db_session, call.from_user.id)
    in_chat = await _was_in_anonymous_chat(prev_state, state)

    await call.answer("❌ انتقال لغو شد.")
    
    # 🌟 ارسال پیام جدید برای پاک کردن قطعی کیبورد قرمز
    from matching_bot_project.bot.core.loader import bot
    if in_chat:
        try:
            await bot.send_message(
                chat_id=call.from_user.id,
                text="🟢 عملیات لغو شد. شما به چت ناشناس برگشتید.",
                reply_markup=get_chat_phase_keyboard(),
            )
        except Exception:
            pass
    else:
        try:
            await bot.send_message(
                chat_id=call.from_user.id,
                text="🔙 عملیات لغو شد. به منوی اصلی بازگشتید.",
                reply_markup=get_main_menu_keyboard(),
            )
        except Exception:
            pass

    # حذف پیام شیشه‌ای در انتها
    try:
        await call.message.delete()
    except Exception:
        pass


@router.message(CoinTransferStates.confirming, F.text == ReplyBtn.CANCEL)
async def cancel_transfer_message(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    prev_state = await _restore_previous_state(state, db_session, message.from_user.id)
    in_chat = await _was_in_anonymous_chat(prev_state, state)

    if in_chat:
        await message.answer(
            "❌ انتقال لغو شد. شما به چت ناشناس برگشتید. 🟢",
            reply_markup=get_chat_phase_keyboard(),
        )
    else:
        await message.answer("❌ انتقال لغو شد.", reply_markup=get_main_menu_keyboard())


@router.callback_query(F.data.startswith("sender_profile_token_"))
async def view_profile_by_transfer_token(call: CallbackQuery, db_session):
    token = call.data.replace("sender_profile_token_", "")
    await call.answer("در حال دریافت اطلاعات...", cache_time=2)
    
    if not token:
        return await call.answer("درخواست نامعتبر.", show_alert=True)

    from matching_bot_project.bot.core.loader import redis_client
    from matching_bot_project.database.queries import crud
    from matching_bot_project.bot.core.formatters import build_unified_profile_card

    try:
        sender_tg_id_str = await redis_client.get(f"transfer:view_profile:{token}")
    except Exception as e:
        logger.warning("view_profile_token: Redis get failed: %s", e)
        return await call.message.answer("خطای موقت. دوباره تلاش کنید.")

    if not sender_tg_id_str:
        return await call.message.answer("این لینک منقضی شده است. لینک‌های مشاهده پروفایل ۱ ساعت اعتبار دارند.")

    try:
        sender_tg_id = int(sender_tg_id_str)
    except (ValueError, TypeError):
        return await call.message.answer("توکن نامعتبر است.")

    sender = await crud.get_user_by_tg_id(db_session, sender_tg_id)
    if not sender or not sender.completed_registration:
        return await call.message.answer("پروفایل فرستنده یافت نشد.")

    profile_card = build_unified_profile_card(sender, is_own_profile=False)

    from matching_bot_project.bot.keyboards.inline import get_user_action_keyboard
    from matching_bot_project.database.models.models import BlockList
    from sqlalchemy import select as sa_select

    block_result = await db_session.execute(
        sa_select(BlockList).where(
            BlockList.blocker_id == call.from_user.id,
            BlockList.blocked_id == sender_tg_id,
        )
    )
    is_blocked = block_result.scalar_one_or_none() is not None

    try:
        already_friend = await crud.is_friend(db_session, call.from_user.id, sender_tg_id)
    except Exception:
        already_friend = False

    # 🛡️ بررسی اینکه آیا کاربرِ کلیک‌کننده در دیت قرار دارد یا خیر
    caller_active_match = await crud.get_active_match(db_session, call.from_user.id)

    markup = get_user_action_keyboard(
        target_tg_id=sender_tg_id,
        is_blocked=is_blocked,
        is_friend=already_friend,
        in_active_match=(caller_active_match is not None),
    )

    try:
        await call.message.delete()
    except Exception:
        pass

    photo_id = getattr(sender, 'profile_photo_file_id', None)
    if photo_id:
        try:
            await call.message.answer_photo(
                photo=photo_id,
                caption=profile_card[:1024],
                parse_mode="HTML",
                reply_markup=markup,
            )
            return
        except Exception as e:
            logger.warning("view_profile_token: photo send failed: %s", e)

    await call.message.answer(
        text=profile_card,
        parse_mode="HTML",
        reply_markup=markup,
    )
    
@router.callback_query(F.data == "coins_transfer")
async def manual_transfer_start(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """شروع فرایند انتقال دستی با شناسه اختصاصی ربات"""
    # 👇 🛡️ گارد امنیتی ضد زامبی
    from matching_bot_project.database.queries import crud
    active_match = await crud.get_active_match(db_session, call.from_user.id)
    if active_match:
        return await call.answer("⚠️ شما در حال حاضر در یک چت/دیت فعال هستید و نمی‌توانید این کار را انجام دهید.", show_alert=True)
    # 👆 پایان گارد امنیتی
    
    await _preserve_current_state(state)
    await state.set_state(ManualTransferStates.waiting_for_target_id)
    
    await call.message.answer(
        "💸 <b>رفیق، می‌خوای به کی سکه هدیه بدی؟</b>\n\n"
        "آیدی اختصاصی شخص مورد نظر رو اینجا برام بفرست. 🎁\n\n"
        "<i>(مثال: اگه آیدی توی پروفایل <code>/user_ABC12</code> هست، می‌تونی دقیقاً همون رو با اسلش کپی کنی یا فقط بنویسی <code>user_ABC12</code> 🔍)</i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()

@router.message(ManualTransferStates.waiting_for_target_id)
async def manual_transfer_receive_id(message: Message, state: FSMContext, db_session: AsyncSession):
    """دریافت و اعتبارسنجی شناسه اختصاصی (public_id) مقصد"""
    # 💡 اضافه شدن ایمپورت‌های ضروری برای جلوگیری از کرش کردن
    from matching_bot_project.database.queries import crud
    from matching_bot_project.bot.core.constants import ReplyBtn
    from matching_bot_project.bot.keyboards.reply import get_chat_phase_keyboard, get_main_menu_keyboard
    from matching_bot_project.bot.handlers.transfer import _restore_previous_state, _was_in_anonymous_chat, CoinTransferStates, _MAX_TRANSFER
    from sqlalchemy import select, or_, and_
    from matching_bot_project.database.models.models import BlockList

    if message.text == ReplyBtn.CANCEL:
        prev = await _restore_previous_state(state, db_session, message.from_user.id)
        if await _was_in_anonymous_chat(prev, state):
            await message.answer("باشه، برگشتی به چت ناشناس 🟢💬", reply_markup=get_chat_phase_keyboard())
        else:
            await message.answer("عملیات انتقال کنسل شد. 🔙", reply_markup=get_main_menu_keyboard())
        return

    # تمیز کردن هوشمند ورودی کاربر (حذف اسلش و هشتگ)
    raw_input = (message.text or "").strip()
    if raw_input.startswith("/"):
        raw_input = raw_input[1:]
    
    clean_public_id = raw_input.replace("#", "").strip()

    if not clean_public_id:
        return await message.reply("😅 شناسه رو خالی فرستادی! لطفاً آیدی رو بنویس.")

    caller_id = message.from_user.id
    caller_user = await crud.get_user_by_tg_id(db_session, caller_id)

    # تلاش برای پیدا کردن کاربر 
    target_user = await crud.get_user_by_public_id(db_session, clean_public_id)
    
    # اگر کاربر با user_ پیدا نشد، قسمت user_ رو پاک می‌کنیم و دوباره می‌گردیم
    if not target_user and clean_public_id.startswith("user_"):
        fallback_id = clean_public_id.replace("user_", "")
        target_user = await crud.get_user_by_public_id(db_session, fallback_id)
        
    # اگر کاربر بدون user_ وارد کرده بود، این پیشوند رو بهش می‌چسبونیم
    if not target_user and not clean_public_id.startswith("user_"):
        fallback_id = f"user_{clean_public_id}"
        target_user = await crud.get_user_by_public_id(db_session, fallback_id)

    if not target_user:
        return await message.reply("🧐 هرچی گشتم کاربری با این شناسه پیدا نکردم! (مثال معتبر: <code>/user_ABC12</code>)")

    if target_user.tg_id == caller_id:
        return await message.reply("😂 بامزه بود! نمی‌تونی به خودت سکه بفرستی.")

    # بررسی مسدود بودن
    block_check = await db_session.execute(
        select(BlockList).where(
            or_(
                and_(BlockList.blocker_id == caller_id, BlockList.blocked_id == target_user.tg_id),
                and_(BlockList.blocker_id == target_user.tg_id,  BlockList.blocked_id == caller_id),
            )
        )
    )
    if block_check.scalar_one_or_none():
        return await message.reply("🚫 متأسفانه امکان انتقال سکه به این شخص وجود ندارد.")

    # رفتن به مرحله گرفتن مقدار سکه
    await state.set_state(CoinTransferStates.waiting_for_amount)
    await state.update_data(
        target_id=target_user.tg_id,
        target_name=target_user.first_name or "دوستت",
        sender_tg_id=caller_id,
    )

    await message.answer(
        f"ایول! قراره به <b>{target_user.first_name}</b> سکه انتقال بدی 🎁\n\n"
        f"💰 موجودی شما: <b>{caller_user.coin_balance}</b> سکه\n\n"
        f"چند تا سکه می‌خوای بفرستی؟ (حداکثر {_MAX_TRANSFER})",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )