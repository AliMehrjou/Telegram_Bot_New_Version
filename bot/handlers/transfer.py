"""
bot/handlers/transfer.py
──────────────────────────────────────────────────────────────────────────────
Peer-to-peer coin transfer flow.
Entry point: callback `transfer_coin_{target_tg_id}` fired from
get_user_action_keyboard() in bot/keyboards/inline.py.
──────────────────────────────────────────────────────────────────────────────
"""
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.loader import bot
from matching_bot_project.bot.keyboards.reply import get_cancel_keyboard, get_main_menu_keyboard
from matching_bot_project.bot.states.states import CoinTransferStates
from matching_bot_project.database.queries.crud import (
    get_user_by_tg_id,
    transfer_coins,
)

logger = logging.getLogger(__name__)
router = Router(name="transfer_handler")

_MAX_TRANSFER = 1_000  # coins per transaction


# ── Helper ─────────────────────────────────────────────────────────────────

def _confirm_keyboard(target_id: int, amount: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"✅ تأیید انتقال {amount} سکه",
            callback_data=f"transfer_confirm_{target_id}_{amount}",
        )],
        [InlineKeyboardButton(text="❌ لغو", callback_data="transfer_cancel")],
    ])


# ── Step 1: entry point ────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("transfer_coin_"))
async def start_coin_transfer(
    call: CallbackQuery, state: FSMContext, db_session: AsyncSession
) -> None:
    target_id_str = call.data.removeprefix("transfer_coin_")
    if not target_id_str.isdigit():
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    target_id = int(target_id_str)
    caller_id = call.from_user.id

    if target_id == caller_id:
        await call.answer("نمی‌توانید به خودتان سکه منتقل کنید!", show_alert=True)
        return

    target_user = await get_user_by_tg_id(db_session, target_id)
    if not target_user:
        await call.answer("❌ کاربر مورد نظر یافت نشد.", show_alert=True)
        return

    caller_user = await get_user_by_tg_id(db_session, caller_id)
    if not caller_user:
        await call.answer("❌ خطای سیستم.", show_alert=True)
        return

    await state.set_state(CoinTransferStates.waiting_for_amount)
    await state.update_data(
        target_id=target_id,
        target_name=target_user.first_name or "کاربر",
    )
    await call.answer()
    await call.message.answer(
        f"🪙 <b>انتقال سکه به:</b> {target_user.first_name}\n\n"
        f"موجودی فعلی شما: <b>{caller_user.coin_balance}</b> سکه\n\n"
        f"چند سکه می‌خواهید منتقل کنید؟ (حداکثر {_MAX_TRANSFER})",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )


# ── Step 2: receive amount ─────────────────────────────────────────────────

@router.message(CoinTransferStates.waiting_for_amount)
async def receive_transfer_amount(
    message: Message, state: FSMContext, db_session: AsyncSession
) -> None:
    if message.text in ("❌ انصراف", "❌ انصراف و منوی اصلی"):
        await state.clear()
        await message.answer("لغو شد.", reply_markup=get_main_menu_keyboard())
        return

    if not message.text or not message.text.strip().isdigit():
        await message.reply("⚠️ لطفاً یک عدد صحیح وارد کنید.")
        return

    amount = int(message.text.strip())

    if amount <= 0:
        await message.reply("⚠️ مقدار باید بیشتر از صفر باشد.")
        return
    if amount > _MAX_TRANSFER:
        await message.reply(f"⚠️ حداکثر {_MAX_TRANSFER} سکه در هر بار مجاز است.")
        return

    caller_user = await get_user_by_tg_id(db_session, message.from_user.id)
    if not caller_user or caller_user.coin_balance < amount:
        balance = caller_user.coin_balance if caller_user else 0
        await message.reply(f"⚠️ موجودی کافی نیست. موجودی فعلی: {balance} سکه.")
        return

    data        = await state.get_data()
    target_id   = data["target_id"]
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


# ── Step 3: confirm ────────────────────────────────────────────────────────

@router.callback_query(
    CoinTransferStates.confirming,
    F.data.startswith("transfer_confirm_"),
)
async def confirm_transfer(
    call: CallbackQuery, state: FSMContext, db_session: AsyncSession
) -> None:
    suffix = call.data.removeprefix("transfer_confirm_")
    parts  = suffix.rsplit("_", 1)          # target_id may have underscores — unlikely but safe
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await call.answer("❌ درخواست نامعتبر.", show_alert=True)
        return

    target_id, amount = int(parts[0]), int(parts[1])
    success, msg      = await transfer_coins(db_session, call.from_user.id, target_id, amount)

    if success:
        await db_session.commit()
        try:
            await bot.send_message(
                chat_id=target_id,
                text=f"🎁 <b>{amount} سکه</b> از طرف یک کاربر به حساب شما واریز شد! 🪙",
                parse_mode="HTML",
            )
        except Exception:
            pass

    await state.clear()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.answer(msg, show_alert=True)
    await call.message.answer(msg, reply_markup=get_main_menu_keyboard())


# ── Cancel at any point ────────────────────────────────────────────────────

@router.callback_query(F.data == "transfer_cancel")
async def cancel_transfer(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("❌ انتقال لغو شد.")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass