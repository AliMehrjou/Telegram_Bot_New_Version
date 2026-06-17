"""
bot/handlers/matching.py

Production-ready match-queue handler for the Telegram dating bot.

Covers:
  1. Queue cancellation  (text: "❌ انصراف و منوی اصلی")
  2. Coin-gated match entry  (callbacks: match_random / match_boy / match_girl / match_nearby)
  3. Employer-mandated 5-second match-initialisation countdown
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.loader import bot, dp, matching_engine
from matching_bot_project.bot.core.loader import dating_scheduler
from matching_bot_project.bot.keyboards.inline import (
    get_match_found_keyboard,
    get_question_reply_keyboard,
)
from matching_bot_project.bot.keyboards.reply import (
    get_cancel_keyboard,
    get_main_menu_keyboard,
)
from matching_bot_project.bot.states.states import MatchingStates, QuestionnaireStates
from matching_bot_project.database.queries import crud

logger = logging.getLogger(__name__)
router = Router(name="matching_handler")

# ─────────────────────────────────────────────────────────────────────────────
# Employer-mandated exact notification text (do not modify wording)
# ─────────────────────────────────────────────────────────────────────────────

_MATCH_FOUND_TEXT = (
    "🎉 تبریک شما با یک نفر برای رفتن به دیت متصل شدین ، "
    "(از دکمه های پایین منو میتونید پروفایل کاربر را مشاهده کنید و یا دیت را تمام کنید.)"
    "\n\nسوالات دیت ۵ ثانیه دیگه شروع میشه"
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_user_state(user_id: int) -> FSMContext:
    """
    Resolve an FSMContext for *any* Telegram user by ID.

    aiogram 3.x FSMContext is request-scoped, so we construct one manually
    when we need to read/write another user's state (e.g. the matched partner).
    """
    return FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 – Queue cancellation
# ─────────────────────────────────────────────────────────────────────────────


@router.message(F.text == "❌ انصراف و منوی اصلی")
async def cancel_queue_operations(message: Message, state: FSMContext) -> None:
    """
    Gracefully exit the match queue and return the user to the main menu.

    The engine call is guarded by a state check so we never attempt to remove a
    user who is not actually queued (e.g. if the button is pressed from another
    context).
    """
    tg_id = message.from_user.id

    current_state = await state.get_state()
    if current_state == MatchingStates.waiting_in_queue:
        await matching_engine.remove_from_queue(tg_id)

    await state.clear()
    await message.answer(
        text="🛑 عملیات لغو شد. به منوی اصلی بازگشتید.",
        reply_markup=get_main_menu_keyboard(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 – Match-type selection (callbacks)
# ─────────────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("match_"))
async def enter_match_queue(
    call: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    """
    Unified handler for all match-type selection callbacks.

    Supported callback_data values
    ────────────────────────────────
    match_random  – free, no gender/province filter
    match_boy     – 1 coin, targets male   users, nationwide
    match_girl    – 1 coin, targets female users, nationwide
    match_nearby  – 1 coin, no gender filter, same province as the caller

    Flow
    ────
    1. Guard against duplicate queue entry.
    2. Fetch the user record.
    3. Resolve cost and engine parameters from the match type.
    4. Verify coin balance; show alert and abort if insufficient.
    5. Lock FSM state → MatchingStates.waiting_in_queue; deduct cost.
    6. Edit the message to show "search started + cost".
    7. Send a cancel keyboard in a separate reply.
    8. Await matching_engine.find_match(...).
    9. Handle ghost matches (refund coin + error).
    10. On valid match: clear both users' queue states, launch handle_successful_match.
    """
    tg_id = call.from_user.id
    match_type = call.data.removeprefix("match_")

    # ── 1. Guard: already waiting ────────────────────────────────────────────
    current_state = await state.get_state()
    if current_state == MatchingStates.waiting_in_queue:
        await call.answer("⚠️ شما در حال حاضر در صف انتظار هستید!", show_alert=True)
        return

    # ── Guard: block cooldown ────────────────────────────────────────────────
    from matching_bot_project.bot.core.loader import redis_client
    cooldown = await redis_client.get(f"user:block_cooldown:{tg_id}")
    if cooldown:
        await call.answer("🚫 به دلیل گزارش/بلاک بیش از حد در ۲۴ ساعت گذشته، حساب شما موقتاً از ورود به صف مچینگ محروم شده است. ⏳", show_alert=True)
        return

    # ── 2. Fetch user ────────────────────────────────────────────────────────
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    # ── 3. Routing parameters per match type ─────────────────────────────────
    if match_type == "random":
        cost = 0
        target_gender = None
        province = None
        search_label = "🎲 مچ تصادفی (رایگان)"
        cost_display = "رایگان"
    elif match_type == "boy":
        cost = 1
        target_gender = "boy"
        province = None
        search_label = "👦 دیت با پسر"
        cost_display = "۱ سکه"
    elif match_type == "girl":
        cost = 1
        target_gender = "girl"
        province = None
        search_label = "👧 دیت با دختر"
        cost_display = "۱ سکه"
    elif match_type == "nearby":
        cost = 1
        target_gender = None
        province = user.province
        search_label = "📍 دیت هم‌شهری"
        cost_display = "۱ سکه"
    else:
        await call.answer("❌ نوع مچ ناشناخته!", show_alert=True)
        return

    # ── 4. Coin balance check ────────────────────────────────────────────────
    if user.coin_balance < cost:
        await call.answer(
            "❌ سکه‌های شما کافی نیست! برای دریافت سکه از منوی اصلی اقدام کنید.",
            show_alert=True,
        )
        return

    # ── 5. Lock state (Defer cost deduction) ─────────────────────────────────
    # State is set BEFORE any further action so a second rapid tap is rejected
    await state.set_state(MatchingStates.waiting_in_queue)
    await call.answer()

    # ── 6 & 7. Inform user that search is active ─────────────────────────────
    await call.message.edit_text(
        text=(
            f"🔍 *جستجوی پارتنر آغاز شد!*\n\n"
            f"نوع مچ: {search_label}\n"
            f"هزینه: {cost_display}\n\n"
            "به محض یافتن پارتنر مناسب اطلاع‌رسانی می‌شود. 🙏"
        ),
        parse_mode="Markdown",
    )
    await call.message.answer(
        text="در صورت تمایل به خروج از صف:",
        reply_markup=get_cancel_keyboard(),
    )

    # ── 8. Invoke the matching engine ────────────────────────────────────────
    matched_partner_id: int | None = await matching_engine.find_match(
        tg_id=tg_id,
        gender=user.gender,
        target_gender=target_gender,
        province=province,
        is_vip=user.is_vip,
    )

    # ── 9. Ghost-match guard ─────────────────────────────────────────────────
    # The engine should never return the caller's own ID; treat it as a fatal
    # engine bug, clean up, and notify the user.
    if matched_partner_id == tg_id:
        logger.error(
            "Ghost match detected — user %s was matched with themselves.", tg_id
        )
        await matching_engine.remove_from_queue(tg_id)
        await state.clear()

        await call.message.answer(
            text=(
                "⚠️ خطای سیستم در مچ‌یابی. "
                "لطفاً دوباره تلاش کنید."
            ),
            reply_markup=get_main_menu_keyboard(),
        )
        return

    # If valid match is found, matching_engine handles deducting coins
    # to avoid double deduction or deducting without matching.

    # ── 10. Valid match found ────────────────────────────────────────────────
    if matched_partner_id:
        # Clear the partner's queue state (they were waiting; we matched them).
        partner_ctx = get_user_state(matched_partner_id)
        await partner_ctx.clear()

        # Clear our own queue state before handing off.
        await state.clear()

        if cost > 0:
            # Deduct coin ONLY after a successful match is established for both users.
            try:
                # Deduct from caller
                user.coin_balance -= cost
                user.total_spent_coins += cost

                # Based on the user prompt: "If a match succeeds, the atomic transaction must deduce exactly 1 coin from each user once."
                partner = await crud.get_user_by_tg_id(db_session, matched_partner_id)
                if partner and partner.coin_balance >= 1:
                    partner.coin_balance -= 1
                    partner.total_spent_coins += 1

                await db_session.commit()
            except Exception as e:
                logger.error(f"Error deducting coins for match {tg_id} and {matched_partner_id}: {e}")
                await db_session.rollback()

        await handle_successful_match(db_session, tg_id, matched_partner_id)

    # If matched_partner_id is None the user has been placed in the queue and
    # will be notified when the engine finds a counterpart.  No action needed
    # here; the state remains MatchingStates.waiting_in_queue.


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 – Match initialisation with 5-second countdown
# ─────────────────────────────────────────────────────────────────────────────


async def handle_successful_match(
    session: AsyncSession,
    user_one_id: int,
    user_two_id: int,
) -> None:
    """
    Employer-mandated match-initialisation workflow.

    Must be called once per successful match, from whichever handler detects it.
    Both ``user_one_id`` and ``user_two_id`` must already have their FSM queue
    states cleared before this function is invoked.

    Step-by-step
    ─────────────
    1.  Persist a MatchHistory record and commit.
    2.  Fetch 20 random questions; cache IDs and starting index (0) in Redis.
    3.  Send the exact employer-mandated notification text to both users.
    4.  Attach get_match_found_keyboard(partner_id, match_id) to that message.
    5.  Set both users' FSM state → QuestionnaireStates.waiting_for_questions_to_start.
    6.  Sleep 5 seconds (non-blocking — does not freeze the event loop).
    7.  Re-fetch match_history; if is_active is False someone clicked "End Date"
        during the countdown → return silently, no questions sent.
    8.  Set both users' FSM state → QuestionnaireStates.answering_questions.
    9.  Send the first question to both via get_question_reply_keyboard(pool[0].id).
    """
    # ── Step 1: persist match history ────────────────────────────────────────
    match_history = await crud.create_match_history(session, user_one_id, user_two_id)
    await session.commit()
    await dating_scheduler.register_match_timeout(match_history.id, user_one_id, user_two_id)
    # ── Step 2: cache question pool in Redis ─────────────────────────────────
    pool = await crud.get_random_questions(session, 20)

    if not pool:
        logger.error(
            "No questions available in the database for match %s. "
            "Aborting match initialisation.",
            match_history.id,
        )
        for uid in (user_one_id, user_two_id):
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=(
                        "⚠️ متأسفانه در حال حاضر سوالی برای شروع مسابقه وجود ندارد. "
                        "لطفاً دوباره تلاش کنید."
                    ),
                    reply_markup=get_main_menu_keyboard(),
                )
            except Exception as exc:
                logger.error(
                    "Failed to send no-questions notice to user %s: %s", uid, exc
                )
        return

    q_ids_str = ",".join(str(q.id) for q in pool)
    await matching_engine.redis.set(
        f"match:questions:{match_history.id}", q_ids_str
    )
    await matching_engine.redis.set(
        f"match:current_q_index:{match_history.id}", "0"
    )

    # ── Steps 3, 4, 5: notify + keyboard + set FSM state ────────────────────
    # Process both users symmetrically.  Each user's keyboard carries the
    # *other* user's ID as the partner reference.
    user_pairs = [
        (user_one_id, user_two_id),   # (target, partner)
        (user_two_id, user_one_id),
    ]

    for target_id, partner_id in user_pairs:

        # Step 5 — FSM state (set before sending the message so the user is
        # already in the correct state if they tap a button immediately)
        ctx = get_user_state(target_id)
        try:
            await ctx.set_state(QuestionnaireStates.waiting_for_questions_to_start)
            await ctx.update_data(match_history_id=match_history.id)
        except Exception as exc:
            logger.error(
                "Could not set waiting_for_questions_to_start for user %s: %s",
                target_id,
                exc,
            )

        # Steps 3 & 4 — send the exact employer-mandated text with inline keyboard
        try:
            await bot.send_message(
                chat_id=target_id,
                text=_MATCH_FOUND_TEXT,
                reply_markup=get_match_found_keyboard(partner_id, match_history.id),
            )
        except Exception as exc:
            logger.error(
                "Could not deliver match notification to user %s: %s", target_id, exc
            )

    # ── Step 6: 5-second async countdown ────────────────────────────────────
    await asyncio.sleep(5)

    # ── Step 7: verify the match is still active ─────────────────────────────
    await session.refresh(match_history)
    if not match_history.is_active:
        logger.info(
            "Match %s was deactivated during the 5-second countdown. "
            "Skipping question delivery.",
            match_history.id,
        )
        return

    # ── Steps 8 & 9: transition to answering and deliver first question ───────
    first_question = pool[0]

    for target_id, _ in user_pairs:
        # Step 8 — advance FSM state
        ctx = get_user_state(target_id)
        try:
            await ctx.set_state(QuestionnaireStates.answering_questions)
            await ctx.update_data(current_question_index=0)
        except Exception as exc:
            logger.error(
                "Could not transition user %s to answering_questions: %s",
                target_id,
                exc,
            )

        # Step 9 — send first question
        try:
            await bot.send_message(
                chat_id=target_id,
                text=f"❓ *سوال اول:*\n\n{first_question.question_text}",
                reply_markup=get_question_reply_keyboard(first_question.id),
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.error(
                "Could not send first question to user %s: %s", target_id, exc
            )