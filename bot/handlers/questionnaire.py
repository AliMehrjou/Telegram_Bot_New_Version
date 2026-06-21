"""
bot/handlers/questionnaire.py

Manages the 20-question gamified compatibility survey that runs immediately
after two users are matched and the 5-second countdown completes.

Core contract
─────────────
Both participants MUST answer a question before the next one is delivered.
Synchronisation is enforced via a Redis INCR counter that is atomically
incremented by each user's answer.  The participant whose increment returns
``2`` drives the entire advancement for both users.  This avoids the need
for any locking beyond the atomic Redis operation itself.

Handler map
────────────────────────────────────────────────────────────────────────────
QuestionnaireStates.answering_questions + F.data.startswith("ans_")
    → register_question_response        (main answer → sync → advance flow)

QuestionnaireStates.waiting_for_partner_answer  (any callback)
    → ignore_input_on_wait_state        (reject all taps gracefully)

Internal helpers (not handlers)
────────────────────────────────────────────────────────────────────────────
_parse_answer_callback      Extract (option, question_id) from callback_data.
_fetch_question_pool        Read + validate the Redis question-ID list.
_deliver_next_question      Advance both users to the next question.
finalize_questionnaire_and_request_approval
                            Score answers, set approval state, notify both.
"""
from __future__ import annotations
 
import logging
 
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
 
from matching_bot_project.bot.core.loader import bot, dp, redis_client, dating_scheduler
from matching_bot_project.bot.keyboards.inline import (
    get_chat_approval_keyboard,
    get_question_reply_keyboard,
)
from matching_bot_project.bot.states.states import ChatStates, QuestionnaireStates
from matching_bot_project.database.models.models import MatchHistory, Question, UserAnswer
from matching_bot_project.database.queries import crud


logger = logging.getLogger(__name__)
router = Router(name="questionnaire_handler")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

TOTAL_QUESTIONS: int = 20

def build_progress_bar(current: int, total: int = TOTAL_QUESTIONS) -> str:
    """Creates a text-based progress bar."""
    filled_length = int(10 * current / total)
    bar = '▓' * filled_length + '░' * (10 - filled_length)
    return f"[{bar}] {current}/{total}\n\n"

# Safety TTL on sync keys prevents memory leaks when a match is abandoned
# mid-questionnaire and the key never reaches a count of 2.
_SYNC_KEY_TTL_SECONDS: int = 3600

# Appended to the question message text when the user locks in their answer.
# Using _WAITING_SUFFIX on the existing message avoids sending an extra
# message and keeps the conversation thread compact.
_WAITING_SUFFIX: str = "\n\n⏳ پاسخ شما ثبت شد. در انتظار پاسخ پارتنر..."

# Toast text shown in the Telegram callback-answer notification.
_ANSWER_ACK_TOAST: str = "✅ پاسخ ثبت شد"

# Alert shown whenever a user taps anything while waiting for their partner.
_PARTNER_WAIT_ALERT: str = (
    "⏳ لطفا شکیبا باشید، پارتنر شما هنوز پاسخ نداده است."
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_user_state(user_id: int) -> FSMContext:
    """
    Resolve an FSMContext for *any* Telegram user by ID.

    aiogram 3.x FSMContext is request-scoped; this helper manually constructs
    one from the dispatcher's storage so we can update another user's state
    (e.g. the matched partner's index and FSM state) from within a handler
    that belongs to the first user.
    """
    return FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
    )


def _parse_answer_callback(callback_data: str) -> tuple[str, int] | None:
    """
    Parse ``ans_a_{question_id}`` or ``ans_b_{question_id}`` into
    ``(option_uppercase, question_id)``.

    Returns ``None`` on any malformed input so callers can emit an alert
    without ever raising an unhandled exception.

    Token layout after ``split("_")``
    ───────────────────────────────────
    Index 0  → "ans"          (literal prefix, already guaranteed by the filter)
    Index 1  → "a" or "b"    (the selected option)
    Index 2  → str(int)       (the question primary key)

    The function deliberately rejects payloads with a part count other than 3
    since a valid integer question ID can never contain underscores.
    """
    parts = callback_data.split("_")

    if len(parts) != 3:
        return None

    if parts[1] not in ("a", "b"):
        return None

    try:
        question_id = int(parts[2])
    except ValueError:
        return None

    return parts[1].upper(), question_id  # option is 'A' or 'B'


async def _fetch_question_pool(match_history_id: int) -> list[int] | None:
    """
    Read the ordered list of question IDs from Redis and validate its format.

    The key ``match:questions:{match_history_id}`` is written as a
    comma-separated string of integers by ``handle_successful_match`` in
    matching.py at match creation time.

    Returns ``None`` when:
    - The key has been evicted (TTL expired or Redis restarted).
    - The stored value cannot be parsed as a comma-separated integer list.

    Both cases are logged at ERROR level because they indicate a data
    integrity problem that must be investigated.
    """
    try:
        raw: str | None = await redis_client.get(
            f"match:questions:{match_history_id}"
        )
    except Exception as exc:
        logger.error(
            "Redis GET failed for match:questions:%s: %s", match_history_id, exc
        )
        return None

    if not raw:
        logger.error(
            "Redis key match:questions:%s is absent. "
            "Was match initialisation (handle_successful_match) completed correctly?",
            match_history_id,
        )
        return None

    try:
        return [int(qid) for qid in raw.split(",")]
    except ValueError:
        logger.error(
            "Corrupt question pool in Redis for match %s: %r",
            match_history_id,
            raw,
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 + 2 + 3 – Main answer handler
# ─────────────────────────────────────────────────────────────────────────────


@router.callback_query(
    QuestionnaireStates.answering_questions,
    F.data.startswith("ans_"),
)
async def register_question_response(
    call: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    """
    Receive, validate, persist, and synchronise a question answer.

    Section 1 – Receive & lock
    ───────────────────────────
    1.  Parse option + question_id from callback_data.
    2.  Read match_history_id and current_question_index from FSM.
    3.  IMMEDIATELY lock FSM state to waiting_for_partner_answer before any
        I/O.  This is the spam-click guard: a duplicate tap from the same user
        fires AFTER the state flip and is caught by ignore_input_on_wait_state.
    4.  Edit the question message: append the waiting notice AND set
        reply_markup=None in a single API call to remove the option buttons.
    5.  Send a toast acknowledgement via call.answer().

    Section 2 – Persist & sync
    ───────────────────────────
    6.  Save the answer to the UserAnswer table via crud.save_user_answer.
    7.  Atomically increment the Redis sync counter.
        - count == 1 → first to answer; set safety TTL and return.
        - count == 2 → both users have answered; drive advancement.

    Section 3 – Advance
    ────────────────────
    8.  Compute next_q_index = current_question_index + 1.
    9.  Fetch the question pool from Redis and the MatchHistory from DB.
    10. If next_q_index < TOTAL_QUESTIONS → _deliver_next_question().
        If next_q_index == TOTAL_QUESTIONS → finalize_questionnaire_and_request_approval().

    Error recovery
    ──────────────
    DB failure (step 6): state is reverted to answering_questions and a new
    question message with the inline keyboard is sent so the user can retry.
    Redis failure (step 7): logged and silently aborted to avoid split-brain.
    """
    tg_id = call.from_user.id

    # ── Section 1.1 – Parse callback payload ─────────────────────────────────
    parsed = _parse_answer_callback(call.data)
    if not parsed:
        logger.warning(
            "Malformed answer callback from user %s: %r", tg_id, call.data
        )
        await call.answer("⚠️ خطای داخلی: داده نامعتبر.", show_alert=True)
        return

    selected_option, question_id = parsed

    # ── Section 1.2 – Read FSM data ───────────────────────────────────────────
    fsm_data: dict = await state.get_data()
    match_history_id: int | None = fsm_data.get("match_history_id")
    current_q_index: int = fsm_data.get("current_question_index", 0)

    if not match_history_id:
        logger.error(
            "User %s has no match_history_id in FSM state while answering "
            "question %s. State may be corrupt.",
            tg_id,
            question_id,
        )
        await call.answer(
            "⚠️ خطای داخلی: جلسه مچ یافت نشد. لطفاً با پشتیبانی تماس بگیرید.",
            show_alert=True,
        )
        return

    # ── Section 1.3 – Lock state immediately (spam-click guard) ──────────────
    # This must happen BEFORE any awaitable I/O so that a rapid second tap
    # arrives in the waiting_for_partner_answer state and is handled by
    # ignore_input_on_wait_state rather than creating a duplicate answer.
    await state.set_state(QuestionnaireStates.waiting_for_partner_answer)

    # ── Section 1.4 – Edit message: append waiting text + remove keyboard ─────
    # Combining both edits into a single edit_text call (with reply_markup=None)
    # is more efficient than calling edit_reply_markup then edit_text separately.
    # Passing entities=message.entities preserves the bold/italic formatting
    # of the original question text on the appended-to portion.
    try:
        new_text = (call.message.text or "") + _WAITING_SUFFIX
        await call.message.edit_text(
            text=new_text,
            reply_markup=None,
            entities=call.message.entities,
        )
    except Exception as exc:
        logger.warning(
            "Could not edit question message for user %s (non-fatal): %s",
            tg_id,
            exc,
        )
        # Fallback: at minimum remove the keyboard so the user cannot re-tap
        # an answer button.  If this also fails the message is probably too
        # old or already edited; we proceed regardless.
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    # ── Section 1.5 – Acknowledge callback (toast) ────────────────────────────
    await call.answer(_ANSWER_ACK_TOAST)

    # ── Section 2.1 – Persist answer to DB ───────────────────────────────────
    try:
        await crud.save_user_answer(
            session=db_session,
            user_id=tg_id,
            question_id=question_id,
            match_history_id=match_history_id,
            selected_option=selected_option,
        )
        await db_session.commit()
        await dating_scheduler.update_user_activity(match_history_id, tg_id)
        
    except Exception as exc:
        logger.error(
            "DB error saving answer for user %s, question %s, match %s: %s",
            tg_id,
            question_id,
            match_history_id,
            exc,
        )
        await db_session.rollback()

        # Revert FSM so the user can try again.
        await state.set_state(QuestionnaireStates.answering_questions)

        # Re-fetch the question to rebuild the keyboard for a retry message.
        # If this DB call also fails, send a generic error.
        try:
            retry_question: Question | None = await db_session.get(
                Question, question_id
            )
            if retry_question:
                retry_text = (
                    "⚠️ خطا در ثبت پاسخ. لطفاً دوباره انتخاب کنید:\n\n"
                    f"🅰️ گزینه اول: {retry_question.option_a}\n"
                    f"🅱️ گزینه دوم: {retry_question.option_b}"
                )
                await call.message.answer(
                    text=retry_text,
                    reply_markup=get_question_reply_keyboard(question_id),
                )
            else:
                await call.message.answer(
                    "⚠️ خطا در ثبت پاسخ. لطفاً دوباره امتحان کنید."
                )
        except Exception as inner_exc:
            logger.error(
                "Could not send retry question to user %s after DB error: %s",
                tg_id,
                inner_exc,
            )
        return

    # ── Section 2.2 – Redis atomic sync counter ───────────────────────────────
    sync_key = f"match:{match_history_id}:q:{question_id}:sync"

    try:
        sync_count: int = await redis_client.incr(sync_key)

        if sync_count == 1:
            # First to answer.  Set a safety TTL so abandoned matches do not
            # leak keys in Redis, then return — we are now waiting.
            await redis_client.expire(sync_key, _SYNC_KEY_TTL_SECONDS)
            return

        if sync_count > 2:
            # Guard against a third increment, which should be impossible given
            # state locking but is handled defensively.
            logger.warning(
                "sync_count=%s on key %r; expected 1 or 2. Ignoring.",
                sync_count,
                sync_key,
            )
            return

    except Exception as exc:
        logger.error(
            "Redis error on sync key %r for match %s: %s",
            sync_key,
            match_history_id,
            exc,
        )
        # If Redis is unavailable we cannot safely determine who has answered.
        # Abort rather than risk sending a duplicate question or skipping one.
        return

    # sync_count == 2: BOTH users have answered this question.
    # The current coroutine is responsible for driving the entire match forward.

    # ── Section 3.1 – Compute next question index ─────────────────────────────
    next_q_index: int = current_q_index + 1

    # ── Section 3.2 – Fetch question pool from Redis ─────────────────────────
    q_ids: list[int] | None = await _fetch_question_pool(match_history_id)
    if q_ids is None:
        logger.error(
            "Cannot advance match %s past question index %s: "
            "question pool unavailable from Redis.",
            match_history_id,
            current_q_index,
        )
        return

    # ── Section 3.3 – Fetch MatchHistory for participant IDs ─────────────────
    match_history: MatchHistory | None = await db_session.get(
        MatchHistory, match_history_id
    )
    if not match_history:
        logger.error(
            "MatchHistory %s not found in DB when advancing to index %s.",
            match_history_id,
            next_q_index,
        )
        return

    # ── Section 3.4 – Branch: next question or finalise ──────────────────────
    if next_q_index < TOTAL_QUESTIONS:
        await _deliver_next_question(
            match_history_id=match_history_id,
            next_q_index=next_q_index,
            q_ids=q_ids,
            user_one_id=match_history.user_one_id,
            user_two_id=match_history.user_two_id,
            db_session=db_session,
        )
    else:
        # next_q_index == TOTAL_QUESTIONS: all 20 questions answered.
        match_history.questionnaire_completed = True
        try:
            await db_session.commit()
        except Exception as exc:
            logger.error(
                "Failed to mark questionnaire_completed=True for match %s: %s",
                match_history_id,
                exc,
            )
            await db_session.rollback()
            # Non-fatal for UX: score calculation reads UserAnswer, not this flag.

        await finalize_questionnaire_and_request_approval(
            session=db_session,
            match_id=match_history_id,
            match_row=match_history,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 – Silent wait-state guard
# ─────────────────────────────────────────────────────────────────────────────


@router.callback_query(QuestionnaireStates.waiting_for_partner_answer)
async def ignore_input_on_wait_state(call: CallbackQuery) -> None:
    """
    Catch-all guard for the waiting state.

    Any callback (including stale ``ans_*`` duplicates from double-taps)
    received while a user is in ``waiting_for_partner_answer`` is silently
    rejected with an informational alert.  No state is mutated.

    Registration order note
    ────────────────────────
    This handler must be registered on the router AFTER
    ``register_question_response``.  aiogram 3.x resolves handlers in
    declaration order, but because the two handlers filter on distinct FSM
    states (``answering_questions`` vs ``waiting_for_partner_answer``), they
    are already mutually exclusive and order does not affect correctness here.
    The note is retained as documentation for future maintainers.
    """
    await call.answer(_PARTNER_WAIT_ALERT, show_alert=True)


# ─────────────────────────────────────────────────────────────────────────────
# Internal: advance both users to the next question
# ─────────────────────────────────────────────────────────────────────────────


async def _deliver_next_question(
    match_history_id: int,
    next_q_index: int,
    q_ids: list[int],
    user_one_id: int,
    user_two_id: int,
    db_session,  # AsyncSession
) -> None:
    """
    Fetch the next question, update both users' FSM, and deliver the message.
 
    FIX (باگ ۱):
    - update_data و set_state در یه try/except ترکیب شدن تا از split-state جلوگیری بشه.
    - لاگ‌گذاری بهتر برای شناسایی کدوم user fail کرده.
    - ادامه‌ی delivery حتی اگه FSM write fail بشه (رفتار قبلی حفظ شده).
    """
    next_question_id: int = q_ids[next_q_index]
 
    next_question = await db_session.get(Question, next_question_id)
    if not next_question:
        logger.error(
            "Question %s (index %s) not found in DB for match %s. "
            "Questionnaire is stalled — manual intervention required.",
            next_question_id,
            next_q_index,
            match_history_id,
        )
        return
 
    progress_bar = build_progress_bar(next_q_index + 1, TOTAL_QUESTIONS)
    question_text = (
        f"{progress_bar}"
        f"❓ *سوال {next_q_index + 1} از {TOTAL_QUESTIONS}:*\n\n"
        f"{next_question.question_text}\n\n"
        f"🅰️ گزینه اول: {next_question.option_a}\n"
        f"🅱️ گزینه دوم: {next_question.option_b}"
    )
    keyboard = get_question_reply_keyboard(next_question_id)
 
    for uid in (user_one_id, user_two_id):
        ctx = get_user_state(uid)
 
        # ── FSM: state + data رو با هم آپدیت کن ─────────────────────────────
        # FIX: update_data و set_state در یه try هستن تا از حالتی که
        # data آپدیت شده ولی state نه (یا برعکس) جلوگیری بشه.
        try:
            await ctx.update_data(current_question_index=next_q_index)
            await ctx.set_state(QuestionnaireStates.answering_questions)
        except Exception as exc:
            logger.error(
                "Failed to update FSM for user %s in match %s (index %s): %s",
                uid,
                match_history_id,
                next_q_index,
                exc,
            )
            # ادامه می‌دیم تا حداقل پیام سوال ارسال بشه
 
        # ── پیام سوال رو بفرست ───────────────────────────────────────────────
        try:
            await bot.send_message(
                chat_id=uid,
                text=question_text,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
        except TelegramForbiddenError:
            logger.warning(
                "User %s blocked the bot. Questionnaire stalled in match %s.",
                uid,
                match_history_id,
            )
        except TelegramAPIError as exc:
            logger.error(
                "Telegram API Error delivering question to %s in match %s: %s",
                uid,
                match_history_id,
                exc,
            )
        except Exception as exc:
            logger.error(
                "Failed to send question %s (index %s) to user %s in match %s: %s",
                next_question_id,
                next_q_index,
                uid,
                match_history_id,
                exc,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Internal: score the questionnaire and open the consent flow
# ─────────────────────────────────────────────────────────────────────────────


async def finalize_questionnaire_and_request_approval(
    session: AsyncSession,
    match_id: int,
    match_row: MatchHistory,
) -> None:
    """
    Calculate compatibility percentage and send the mutual-consent prompt.

    Called from ``register_question_response`` when all ``TOTAL_QUESTIONS``
    have been answered by both participants.

    Compatibility algorithm
    ──────────────────────────────────────────────────────────────────────────
    1.  Load every ``UserAnswer`` row for this ``match_id``.
    2.  Group rows by ``question_id`` → a list of (user_id, option) pairs.
    3.  For each question with exactly two answers (one per participant),
        compare the two selected options.
    4.  Percentage = round((identical_pairs / compared_questions) × 100).
        Defaults to 50 % when no complete pairs exist (guards against a data
        inconsistency without crashing).

    Consent flow
    ────────────
    Both users are placed into ``ChatStates.waiting_for_approval`` and
    receive the compatibility score together with ``get_chat_approval_keyboard``.
    The actual chat is opened only if both tap "موافقم" (handled by the
    chat-approval handler, not this file).

    Per-user error isolation
    ─────────────────────────
    FSM and send operations are wrapped per user so a single blocked-bot
    failure does not prevent the other participant from seeing their result.
    """
    # ── Step 1: Load all answers for this match ───────────────────────────────
    try:
        stmt = select(UserAnswer).where(UserAnswer.match_history_id == match_id)
        result = await session.execute(stmt)
        all_answers: list[UserAnswer] = list(result.scalars().all())
    except Exception as exc:
        logger.error(
            "Failed to fetch UserAnswer records for match %s: %s", match_id, exc
        )
        all_answers = []

    # ── Step 2 & 3: Group by question and count matching pairs ───────────────
    # per_question maps question_id → list of selected options (strings).
    per_question: dict[int, list[str]] = {}
    for ans in all_answers:
        per_question.setdefault(ans.question_id, []).append(ans.selected_option)

    identical_count: int = 0
    compared_count: int = 0

    for options in per_question.values():
        if len(options) == 2:
            compared_count += 1
            if options[0] == options[1]:
                identical_count += 1

    # ── Step 4: Compute score ─────────────────────────────────────────────────
    compatibility_pct: int = (
        round((identical_count / compared_count) * 100)
        if compared_count > 0
        else 50  # neutral fallback on data inconsistency
    )

    logger.info(
        "Match %s questionnaire complete — %s/%s identical answers → %s%%",
        match_id,
        identical_count,
        compared_count,
        compatibility_pct,
    )

    # ── Build consent-prompt message ─────────────────────────────────────────
    approval_text = (
        "🏁 *همکاری و آزمایش تفاهم به پایان رسید!*\n\n"
        f"📊 میزان تفاهم شما با پارتنر بر اساس پاسخ‌ها: *{compatibility_pct}%*\n\n"
        "در صورتی که مایل به شروع گفتگوی ناشناس عاطفی با این شخص هستید، "
        'موافقت خود را با زدن روی دکمه *"موافقم"* اعلام کنید 👇\n\n'
        "_(مکالمه تنها در صورت تایید هر دو طرف باز خواهد شد)_"
    )
    approval_keyboard = get_chat_approval_keyboard()

    # ── Update both users: FSM state + send approval prompt ──────────────────
    for uid in (match_row.user_one_id, match_row.user_two_id):

        ctx = get_user_state(uid)
        try:
            await ctx.update_data(compatibility_pct=compatibility_pct)
            await ctx.set_state(ChatStates.waiting_for_approval)
        except Exception as exc:
            logger.error(
                "Failed to set ChatStates.waiting_for_approval for user %s "
                "in match %s: %s",
                uid,
                match_id,
                exc,
            )

        try:
            await bot.send_message(
                chat_id=uid,
                text=approval_text,
                reply_markup=approval_keyboard,
                parse_mode="Markdown",
            )
        except TelegramForbiddenError:
            logger.warning(f"User {uid} blocked the bot before approval prompt in match {match_id}.")
        except TelegramAPIError as exc:
            logger.error(f"Telegram API error delivering approval prompt to {uid} in match {match_id}: {exc}")
        except Exception as exc:
            logger.error(
                "Failed to deliver approval prompt to user %s in match %s: %s",
                uid,
                match_id,
                exc,
            )