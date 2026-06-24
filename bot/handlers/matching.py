from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.bot.core.loader import bot, dp, matching_engine, redis_client
from matching_bot_project.bot.core.loader import dating_scheduler
from matching_bot_project.bot.keyboards.inline import (
    get_match_found_keyboard,
    get_question_reply_keyboard,
    get_vip_age_filter_keyboard,
)
from matching_bot_project.bot.keyboards.reply import (
    get_cancel_keyboard,
    get_date_phase_keyboard,          
    get_main_menu_keyboard,
)
from matching_bot_project.bot.states.states import MatchingStates, QuestionnaireStates, VIPStates
from matching_bot_project.database.queries import crud
from matching_bot_project.database.models.models import User

from matching_bot_project.bot.core.constants import SystemMsg
from matching_bot_project.bot.core.constants import ReplyBtn

logger = logging.getLogger(__name__)
router = Router(name="matching_handler")

# ─────────────────────────────────────────────────────────────────────────────
# Employer-mandated exact notification text (do not modify wording)
# ─────────────────────────────────────────────────────────────────────────────

_MATCH_FOUND_TEXT = SystemMsg.MATCH_FOUND_TEXT

_MATCH_TYPE_CONFIG = {
    "random": {
        "cost": 0,
        "target_gender": None,
        "uses_province": False,
        "label": "🎲 مچ تصادفی (رایگان)",
        "cost_display": "رایگان",
    },
    "boy": {
        "cost": 1,
        "target_gender": "Male",    # ← Fixed: Mapped to Model values
        "uses_province": False,
        "label": "👦 دیت با پسر",
        "cost_display": "۱ سکه",
    },
    "girl": {
        "cost": 1,
        "target_gender": "Female",  # ← Fixed: Mapped to Model values
        "uses_province": False,
        "label": "👧 دیت با دختر",
        "cost_display": "۱ سکه",
    },
    "nearby": {
        "cost": 1,
        "target_gender": None,
        "uses_province": True,
        "label": "📍 دیت هم‌شهری",
        "cost_display": "۱ سکه",
    },
}

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


def _resolve_match_params(match_type: str, user: User) -> Optional[dict]:
    """
    Resolves cost/engine routing parameters for a given match_type.
    Returns None if match_type is unknown.
    """
    config = _MATCH_TYPE_CONFIG.get(match_type)
    if not config:
        return None

    return {
        "cost": config["cost"],
        "target_gender": config["target_gender"],
        "province": user.province if config["uses_province"] else None,
        "search_label": config["label"],
        "cost_display": config["cost_display"],
    }


async def _settle_coins_after_match(
    db_session: AsyncSession,
    user: User,
    cost: int,
    matched_partner_id: int,
) -> None:
    """
    Deducts the caller's coin cost unconditionally, and deducts 1 coin from the
    partner only if they can afford it. If the partner can't afford it, the
    match still proceeds — the partner simply doesn't pay for this round.
    No refund logic applies since the caller's coin is only deducted here,
    after the match is already confirmed.
    """
    if cost <= 0:
        return

    try:
        user.coin_balance -= cost
        user.total_spent_coins += cost

        partner = await crud.get_user_by_tg_id(db_session, matched_partner_id)
        if partner and partner.coin_balance >= 1:
            partner.coin_balance -= 1
            partner.total_spent_coins += 1
        elif partner:
            logger.info(
                "Partner %s had insufficient coins; match with %s proceeds anyway.",
                matched_partner_id,
                user.tg_id,
            )

        await db_session.commit()
    except Exception as exc:
        logger.error(
            "Error deducting coins for match %s <-> %s: %s",
            user.tg_id,
            matched_partner_id,
            exc,
        )
        await db_session.rollback()


async def _start_search_ui(
    call: CallbackQuery,
    search_label: str,
    cost_display: str,
    age_range_text: Optional[str] = None,
) -> None:
    """
    Edits the callback message to show "search started" and sends the cancel
    keyboard. Both sends are best-effort: failures are logged but never abort
    the matching flow, since the user is already locked into the queue state.
    """
    age_line = f"محدوده سنی: {age_range_text}\n" if age_range_text else ""
    try:
        await call.message.edit_text(
            text=(
                f"🔍 *جستجوی پارتنر آغاز شد!*\n\n"
                f"نوع مچ: {search_label}\n"
                f"{age_line}"
                f"هزینه: {cost_display}\n\n"
                "به محض یافتن پارتنر مناسب اطلاع‌رسانی می‌شود. 🙏"
            ),
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.warning("Could not edit search-start message for user %s: %s", call.from_user.id, exc)

    try:
        await call.message.answer(
            text="در صورت تمایل به خروج از صف:",
            reply_markup=get_cancel_keyboard(),
        )
    except Exception as exc:
        logger.warning("Could not send cancel-keyboard message for user %s: %s", call.from_user.id, exc)


async def _handle_ghost_match(call: CallbackQuery, state: FSMContext, tg_id: int) -> None:
    """Cleans up and notifies the user when the engine returns a self-match (fatal bug guard)."""
    logger.error("Ghost match detected — user %s was matched with themselves.", tg_id)
    await matching_engine.remove_from_queue(tg_id)
    await state.clear()
    try:
        await call.message.answer(
            text="⚠️ خطای سیستم در مچ‌یابی. لطفاً دوباره تلاش کنید.",
            reply_markup=get_main_menu_keyboard(),
        )
    except Exception as exc:
        logger.warning("Could not deliver ghost-match notice to user %s: %s", tg_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 – Queue cancellation
# ─────────────────────────────────────────────────────────────────────────────


@router.message(F.text == ReplyBtn.CANCEL)
async def cancel_queue_operations(message: Message, state: FSMContext) -> None:
    """
    Gracefully exit the match queue and return the user to the main menu.

    The engine call is guarded by a state check so we never attempt to remove a
    user who is not actually queued (e.g. if the button is pressed from another
    context).
    """
    tg_id = message.from_user.id

    current_state = await state.get_state()
    if current_state in (MatchingStates.waiting_in_queue, VIPStates.waiting_for_age_filter):
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
    2. Guard against block-cooldown.
    3. Fetch the user record (guard against missing user / missing gender).
    4. Resolve cost and engine parameters from the match type.
    5. Verify coin balance; show alert and abort if insufficient.
    6. VIP users (except "nearby") are redirected to the age-filter step.
    7. Lock FSM state → MatchingStates.waiting_in_queue.
    8. Show "search started" UI (best-effort).
    9. Await matching_engine.find_match(...).
    10. Handle ghost matches.
    11. On valid match: clear both users' queue states, settle coins, launch
        handle_successful_match. If match initialisation fails, both users
        are returned to the main menu (see handle_successful_match).
    """
    tg_id = call.from_user.id
    match_type = call.data.removeprefix("match_")

    # ── 1. Guard: already waiting ────────────────────────────────────────────
    current_state = await state.get_state()
    if current_state == MatchingStates.waiting_in_queue:
        await call.answer("⚠️ شما در حال حاضر در صف انتظار هستید!", show_alert=True)
        return

    # ── 2. Guard: block cooldown ─────────────────────────────────────────────
    cooldown = await redis_client.get(f"user:block_cooldown:{tg_id}")
    if cooldown:
        await call.answer(
            "🚫 به دلیل گزارش/بلاک بیش از حد در ۲۴ ساعت گذشته، حساب شما موقتاً از ورود به صف مچینگ محروم شده است. ⏳",
            show_alert=True,
        )
        return

    # ── 3. Fetch user ─────────────────────────────────────────────────────────
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if user is None:
        logger.error("User %s not found in DB during match queue entry.", tg_id)
        await call.answer("❌ خطا در دریافت اطلاعات کاربری. لطفاً دوباره تلاش کنید.", show_alert=True)
        return
    if not user.gender:
        await call.answer(
            "❌ پروفایل شما کامل نیست (جنسیت ثبت نشده). لطفاً ابتدا پروفایل خود را تکمیل کنید.",
            show_alert=True,
        )
        return
    if match_type == "nearby" and not user.province:
        await call.answer(
            "❌ استان شما ثبت نشده است. لطفاً ابتدا پروفایل خود را تکمیل کنید.",
            show_alert=True,
        )
        return

    # ── 4. Routing parameters per match type ─────────────────────────────────
    params = _resolve_match_params(match_type, user)
    if params is None:
        await call.answer("❌ نوع مچ ناشناخته!", show_alert=True)
        return

    cost = params["cost"]
    target_gender = params["target_gender"]
    province = params["province"]
    search_label = params["search_label"]
    cost_display = params["cost_display"]

    # ── 5. Coin balance check ────────────────────────────────────────────────
    if user.coin_balance < cost:
        await call.answer(
            "❌ سکه‌های شما کافی نیست! برای دریافت سکه از منوی اصلی اقدام کنید.",
            show_alert=True,
        )
        return

    # ── 6. VIP Age Filter Interception ───────────────────────────────────────
    is_vip = user.is_vip or (user.vip_expires_at and user.vip_expires_at > datetime.utcnow())
    if is_vip and match_type != "nearby":
        await state.set_state(VIPStates.waiting_for_age_filter)
        await state.update_data(
            match_type=match_type,
            target_gender=target_gender,
            province=province,
            search_label=search_label,
            cost=cost,
            cost_display=cost_display,
        )
        try:
            await call.message.edit_text(
                "شما کاربر VIP هستید! 💎\nلطفاً محدوده سنی مورد نظر خود را برای مچ انتخاب کنید:",
                reply_markup=get_vip_age_filter_keyboard(match_type),
            )
        except Exception as exc:
            logger.warning("Could not show VIP age-filter prompt to user %s: %s", tg_id, exc)
        await call.answer()
        return

    # ── 7. Lock state ─────────────────────────────────────────────────────────
    # State is set BEFORE any further action so a second rapid tap is rejected.
    await state.set_state(MatchingStates.waiting_in_queue)
    await call.answer()

    # ── 8. Inform user that search is active (best-effort) ──────────────────
    await _start_search_ui(call, search_label, cost_display)

    # ── 9. Invoke the matching engine ────────────────────────────────────────
    matched_partner_id = await matching_engine.find_match(
        tg_id=tg_id,
        gender=user.gender,
        target_gender=target_gender,
        province=province,
        is_vip=False,
        caller_age=user.age,
        caller_min_age=0,
        caller_max_age=99,
        caller_interests_str=user.interests,
    )

    # ── 10. Ghost-match guard ─────────────────────────────────────────────────
    if matched_partner_id == tg_id:
        await _handle_ghost_match(call, state, tg_id)
        return

    # If no match was found, the user has been placed in the queue by the
    # engine and will be matched asynchronously by a future caller. FSM state
    # remains MatchingStates.waiting_in_queue; no further action here.
    if not matched_partner_id:
        return

    # ── 11. Valid match found ────────────────────────────────────────────────
    partner_ctx = get_user_state(matched_partner_id)
    await partner_ctx.clear()
    await state.clear()

    await _settle_coins_after_match(db_session, user, cost, matched_partner_id)

    await handle_successful_match(db_session, tg_id, matched_partner_id)


@router.callback_query(VIPStates.waiting_for_age_filter, F.data.startswith("vip_age_"))
async def process_vip_age_filter(
    call: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    """Processes the VIP age filter selection and delegates to the matching engine."""
    
    # حذف پیشوند مشترک برای استخراج تمیزتر دیتا (مثلاً خروجی: "18_25_boy" یا "all_girl")
    data_parts = call.data.removeprefix("vip_age_").split("_")
    
    if data_parts[0] == "all":
        min_age, max_age = 0, 99
        match_type = data_parts[1] if len(data_parts) > 1 else "random"
    else:
        min_age = int(data_parts[0])
        max_age = int(data_parts[1])
        match_type = data_parts[2] if len(data_parts) > 2 else "random"

    data = await state.get_data()
    target_gender = data.get("target_gender")
    province = data.get("province")
    search_label = data.get("search_label")
    cost = data.get("cost", 0)
    cost_display = data.get("cost_display")

    tg_id = call.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if user is None:
        logger.error("User %s not found in DB during VIP age-filter processing.", tg_id)
        await state.clear()
        await call.answer("❌ خطا در دریافت اطلاعات کاربری. لطفاً دوباره تلاش کنید.", show_alert=True)
        return
    if not user.gender:
        await state.clear()
        await call.answer(
            "❌ پروفایل شما کامل نیست (جنسیت ثبت نشده). لطفاً ابتدا پروفایل خود را تکمیل کنید.",
            show_alert=True,
        )
        return

    # Lock state
    await state.set_state(MatchingStates.waiting_in_queue)
    await call.answer()

    # Store filters for reference (engine receives them directly as well)
    await state.update_data(min_age_filter=min_age, max_age_filter=max_age)

    await _start_search_ui(
        call,
        search_label,
        cost_display,
        age_range_text=f"{min_age} تا {max_age} سال",
    )

    # Invoke engine
    matched_partner_id = await matching_engine.find_match(
        tg_id=tg_id,
        gender=user.gender,
        target_gender=target_gender,
        province=province,
        is_vip=True,
        caller_age=user.age,
        caller_min_age=min_age,
        caller_max_age=max_age,
        caller_interests_str=user.interests,
    )

    if matched_partner_id == tg_id:
        await _handle_ghost_match(call, state, tg_id)
        return

    if not matched_partner_id:
        return

    partner_ctx = get_user_state(matched_partner_id)
    await partner_ctx.clear()
    await state.clear()

    await _settle_coins_after_match(db_session, user, cost, matched_partner_id)

    await handle_successful_match(db_session, tg_id, matched_partner_id)


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 – Match initialisation with 5-second countdown
# ─────────────────────────────────────────────────────────────────────────────


async def _abort_match_initialisation(
    session: AsyncSession,
    match_history,
    user_one_id: int,
    user_two_id: int,
    reason: str,
) -> None:
    """
    Marks the match as inactive and returns both users to the main menu.
    Used when match notification delivery fails for either party before the
    5-second countdown begins, since the employer requires both sides to be
    notified successfully or the date does not proceed.
    """
    logger.error(
        "Aborting match %s initialisation (%s <-> %s): %s",
        match_history.id,
        user_one_id,
        user_two_id,
        reason,
    )
    match_history.is_active = False
    match_history.ended_at = datetime.utcnow()
    try:
        await session.commit()
    except Exception as exc:
        logger.error("Could not persist match-abort state for match %s: %s", match_history.id, exc)
        await session.rollback()

    for uid in (user_one_id, user_two_id):
        ctx = get_user_state(uid)
        try:
            await ctx.clear()
        except Exception as exc:
            logger.error("Could not clear FSM state for user %s during match abort: %s", uid, exc)

        try:
            await bot.send_message(
                chat_id=uid,
                text=(
                    "⚠️ متأسفانه ارسال اطلاع‌رسانی مچ به یکی از طرفین با خطا مواجه شد "
                    "و دیت لغو گردید. لطفاً دوباره تلاش کنید."
                ),
                reply_markup=get_main_menu_keyboard(),
            )
        except Exception as exc:
            logger.error("Could not deliver match-abort notice to user %s: %s", uid, exc)


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
        If delivery fails for either user, abort the whole match (both users
        return to the main menu) rather than proceeding one-sided.
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
        match_history.is_active = False
        match_history.ended_at = datetime.utcnow()
        try:
            await session.commit()
        except Exception as exc:
            logger.error("Could not persist no-questions abort for match %s: %s", match_history.id, exc)
            await session.rollback()

        for uid in (user_one_id, user_two_id):
            ctx = get_user_state(uid)
            try:
                await ctx.clear()
            except Exception as exc:
                logger.error("Could not clear FSM state for user %s: %s", uid, exc)
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

    # ── Steps 3, 4: notify + keyboard ────────────────────────────────────────
    # Each user's keyboard carries the *other* user's ID as the partner reference.
    user_pairs = [
        (user_one_id, user_two_id),   # (target, partner)
        (user_two_id, user_one_id),
    ]

# WHERE: Inside `handle_successful_match()`, down in Steps 3 & 4 (the user_pairs for-loop)

    delivery_failed_for = None
    for target_id, partner_id in user_pairs:
        try:
            await bot.send_message(
                chat_id=target_id,
                text=_MATCH_FOUND_TEXT,
                reply_markup=get_match_found_keyboard(partner_id, match_history.id),
            )
            
            # Send date-phase reply keyboard
            try:
                await bot.send_message(
                    chat_id=target_id,
                    text="کیبورد دیت شما آماده است 👇",
                    reply_markup=get_date_phase_keyboard(),
                )
            except Exception:
                pass

        except Exception as exc:
            logger.error(
                "Could not deliver match notification to user %s: %s", target_id, exc
            )
            delivery_failed_for = target_id
            break

    # If delivery failed for either side, abort the whole match — both users
    # return to the main menu rather than one side proceeding alone.
    if delivery_failed_for is not None:
        await _abort_match_initialisation(
            session,
            match_history,
            user_one_id,
            user_two_id,
            reason=f"notification delivery failed for user {delivery_failed_for}",
        )
        return

    # ── Step 5: set both users' FSM state ────────────────────────────────────
    for target_id in (user_one_id, user_two_id):
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