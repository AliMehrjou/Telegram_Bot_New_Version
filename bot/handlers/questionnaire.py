from __future__ import annotations
 
import html
import logging
 
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.exceptions import TelegramBadRequest

from matching_bot_project.bot.core.loader import bot, dp, redis_client, dating_scheduler
from matching_bot_project.bot.keyboards.inline import (
    get_chat_approval_keyboard,
    get_question_reply_keyboard,
)
from matching_bot_project.bot.states.states import ChatStates, QuestionnaireStates
from matching_bot_project.database.models.models import MatchHistory, Question, UserAnswer
from matching_bot_project.database.queries import crud

# --- NEW CONSTANTS IMPORT ---
from matching_bot_project.bot.core.constants import Messages as SystemMsg
from matching_bot_project.bot.core.constants import CompatibilityMsg

from aiogram.types import CallbackQuery, Message
from aiogram.filters import StateFilter


logger = logging.getLogger(__name__)
router = Router(name="questionnaire_handler")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

TOTAL_QUESTIONS: int = 20

# ─────────────────────────────────────────────────────────────────────────────
# Premium (tg-emoji) icons for the question card — از پک اموجی پریمیوم پروژه
# ─────────────────────────────────────────────────────────────────────────────
_EMOJI_QUESTION = '<tg-emoji emoji-id="5467666648263564704">❓</tg-emoji>'
_EMOJI_OPT_A = '<tg-emoji emoji-id="5202123891392156888">👈</tg-emoji>'
_EMOJI_OPT_B = '<tg-emoji emoji-id="5202123891392156888">👈</tg-emoji>'
_EMOJI_OPT_C = '<tg-emoji emoji-id="5202123891392156888">👈</tg-emoji>'
_EMOJI_OPT_D = '<tg-emoji emoji-id="5202123891392156888">👈</tg-emoji>'
_EMOJI_TIP = '<tg-emoji emoji-id="5472146462362048818">💡</tg-emoji>'


def build_progress_bar(current: int, total: int = TOTAL_QUESTIONS) -> str:
    """Creates a text-based progress bar."""
    # FIX L-24: clamp to [0, 10] so a current > total doesn't render a bar wider than 10 cells.
    filled_length = max(0, min(10, int(10 * current / total))) if total > 0 else 0
    bar = '▓' * filled_length + '░' * (10 - filled_length)
    return f"[{bar}] {current}/{total}\n\n"

# Safety TTL on sync keys prevents memory leaks when a match is abandoned
# mid-questionnaire and the key never reaches a count of 2.
_SYNC_KEY_TTL_SECONDS: int = 3600

# Appended to the question message text when the user locks in their answer.
# Using _WAITING_SUFFIX on the existing message avoids sending an extra
# message and keeps the conversation thread compact.
_WAITING_SUFFIX: str = SystemMsg.WAITING_SUFFIX
_ANSWER_ACK_TOAST: str = SystemMsg.ANSWER_ACK_TOAST
_PARTNER_WAIT_ALERT: str = SystemMsg.PARTNER_WAIT_ALERT

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
    Parse ``ans_a_{question_id}`` ... ``ans_d_{question_id}`` into
    ``(option_uppercase, question_id)``.

    Returns ``None`` on any malformed input so callers can emit an alert
    without ever raising an unhandled exception.

    Token layout after ``split("_")``
    ───────────────────────────────────
    Index 0  → "ans"                   (literal prefix)
    Index 1  → "a", "b", "c", or "d"  (selected option)
    Index 2  → str(int)                (question primary key)
    """
    parts = callback_data.split("_")

    if len(parts) != 3:
        return None

    if parts[1] not in ("a", "b", "c", "d"):
        return None

    try:
        question_id = int(parts[2])
    except ValueError:
        return None

    return parts[1].upper(), question_id  # 'A', 'B', 'C', or 'D'


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
    await state.set_state(QuestionnaireStates.waiting_for_partner_answer)

# ── Section 1.4 – Edit message: append waiting text + remove keyboard ─────
    try:
        # ۱. اول کیبورد شیشه‌ای رو قطعی از بین می‌بریم تا روی صفحه گیر نکنه
        await call.message.edit_reply_markup(reply_markup=None)
        
        # ۲. متن ساده رو بدون درگیری با تگ‌ها و Entities آپدیت می‌کنیم که تلگرام ارور نده
        safe_text = call.message.text if call.message.text else "پاسخ ثبت شد."
        await call.message.edit_text(
            text=safe_text + "\n\n" + _WAITING_SUFFIX,
            reply_markup=None
        )
    except Exception as exc:
        logger.warning(
            "Could not edit question message for user %s (non-fatal): %s",
            tg_id,
            exc,
        )
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    # ── Section 1.5 – Acknowledge callback (toast) ────────────────────────────
    try:
        await call.answer(_ANSWER_ACK_TOAST)
    except TelegramBadRequest:
        pass

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

        try:
            retry_question: Question | None = await db_session.get(
                Question, question_id
            )
            if retry_question:
                retry_opt_c = getattr(retry_question, 'option_c', None)
                retry_opt_d = getattr(retry_question, 'option_d', None)
                retry_is_four_choice = bool(retry_opt_c and retry_opt_d)

                retry_text = (
                    "⚠️ <b>خطا در ثبت پاسخ. لطفاً دوباره انتخاب کنید:</b>\n\n"
                    f"<blockquote>{_EMOJI_OPT_A} <b>گزینه اول</b> ▫️ <i>{html.escape(retry_question.option_a or '')}</i>\n"
                    f"{_EMOJI_OPT_B} <b>گزینه دوم</b> ▫️ <i>{html.escape(retry_question.option_b or '')}</i>"
                )
                if retry_is_four_choice:
                    retry_text += (
                        f"\n{_EMOJI_OPT_C} <b>گزینه سوم</b> ▫️ <i>{html.escape(retry_opt_c or '')}</i>"
                        f"\n{_EMOJI_OPT_D} <b>گزینه چهارم</b> ▫️ <i>{html.escape(retry_opt_d or '')}</i>"
                    )
                retry_text += "</blockquote>"

                await call.message.answer(
                    text=retry_text,
                    reply_markup=get_question_reply_keyboard(question_id, is_four_choice=retry_is_four_choice),
                    parse_mode="HTML",
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
        return  # این Return به درستی در داخل بلوک except قرار دارد تا در صورت خطا خارج شود

    # ── Section 2.2 – Redis atomic sync using Set (Anti-Spam & Safe) ───────────
    # باگ ۹ فیکس شد: این بخش با ۴ فاصله (Indentation سطح متد) هم‌تراز شد
    sync_key = f"match:{match_history_id}:q:{question_id}:sync"

    try:
        current_user_id = call.from_user.id
        added: int = await redis_client.sadd(sync_key, current_user_id)

        if added == 0:
            await call.answer()
            return

        sync_count: int = await redis_client.scard(sync_key)

        if sync_count == 1:
            await redis_client.expire(sync_key, _SYNC_KEY_TTL_SECONDS)
            return

        if sync_count > 2:
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
        await state.set_state(QuestionnaireStates.answering_questions)
        await call.answer("⚠️ خطای موقت در سرور. لطفا مجدداً گزینه را انتخاب کنید.", show_alert=True)
        return

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

        await finalize_questionnaire_and_request_approval(
            session=db_session,
            match_id=match_history_id,
            match_row=match_history,
        )



# ─────────────────────────────────────────────────────────────────────────────
# Section 4 – Silent wait-state guard
# ─────────────────────────────────────────────────────────────────────────────


@router.callback_query(QuestionnaireStates.waiting_for_partner_answer, F.data.startswith("ans_"))
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

    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

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
    """
    if next_q_index < 0 or next_q_index >= len(q_ids):
        logger.error(
            "Question index %s out of range (q_ids has %s items) for match %s. "
            "Finalizing questionnaire with the questions we have.",
            next_q_index, len(q_ids), match_history_id,
        )
        try:
            from matching_bot_project.bot.handlers.matching import finalize_questionnaire_and_request_approval
            await finalize_questionnaire_and_request_approval(db_session, match_history_id)
        except Exception as exc:
            logger.exception("Fallback finalize failed for match %s: %s", match_history_id, exc)
        return
        
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

    opt_c = getattr(next_question, 'option_c', None)
    opt_d = getattr(next_question, 'option_d', None)
    is_four_choice = bool(opt_c and opt_d)

    q_text_safe = html.escape(next_question.question_text or "")
    opt_a_safe = html.escape(next_question.option_a or "")
    opt_b_safe = html.escape(next_question.option_b or "")
    opt_c_safe = html.escape(opt_c or "")
    opt_d_safe = html.escape(opt_d or "")

    question_text = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{_EMOJI_QUESTION} <b>سؤال {next_q_index + 1} از {TOTAL_QUESTIONS}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<code>{progress_bar.strip()}</code>\n\n"
        f"<i>{q_text_safe}</i>\n\n"
        f"<blockquote>{_EMOJI_OPT_A} <b>گزینه اول</b> ▫️ <i>{opt_a_safe}</i>\n"
        f"{_EMOJI_OPT_B} <b>گزینه دوم</b> ▫️ <i>{opt_b_safe}</i>"
    )
    if is_four_choice:
        question_text += (
            f"\n{_EMOJI_OPT_C} <b>گزینه سوم</b> ▫️ <i>{opt_c_safe}</i>"
            f"\n{_EMOJI_OPT_D} <b>گزینه چهارم</b> ▫️ <i>{opt_d_safe}</i>"
        )
    question_text += (
        f"</blockquote>\n\n"
        f"{_EMOJI_TIP} <i>یکی از گزینه‌ها را انتخاب کنید</i>"
    )

    keyboard = get_question_reply_keyboard(next_question_id, is_four_choice=is_four_choice)
 
    for uid in (user_one_id, user_two_id):
        ctx = get_user_state(uid)
 
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
 
        try:
            await bot.send_message(
                chat_id=uid,
                text=question_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except TelegramForbiddenError:
            logger.warning(
                "User %s blocked the bot. Questionnaire stalled in match %s. Aborting.",
                uid, match_history_id
            )
            
            # --- بخش اضافه‌شده: انحلال مچ و پاکسازی زامبی ---
            partner_id = user_two_id if uid == user_one_id else user_one_id
            
            match_row = await db_session.get(MatchHistory, match_history_id)
            if match_row and match_row.is_active:
                match_row.is_active = False
                from datetime import datetime, timezone
                match_row.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await db_session.commit()
                
            # پاک کردن استیت هر دو نفر
            for target in (uid, partner_id):
                target_ctx = get_user_state(target)
                await target_ctx.set_state(None)
                await target_ctx.clear()
                
            # اطلاع‌رسانی به پارتنر آنلاین
            try:
                from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
                await bot.send_message(
                    chat_id=partner_id,
                    text="⚠️ <b>دیت متوقف شد!</b>\nپارتنر شما ربات را مسدود کرده یا از آن خارج شده است.",
                    parse_mode="HTML",
                    reply_markup=get_main_menu_keyboard()
                )
            except Exception:
                pass
            return  # توقف کامل حلقه و تابع
            
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
    try:
        stmt = select(UserAnswer).where(UserAnswer.match_history_id == match_id)
        result = await session.execute(stmt)
        all_answers: list[UserAnswer] = list(result.scalars().all())
    except Exception as exc:
        logger.error("Failed to fetch UserAnswer records for match %s: %s", match_id, exc)
        all_answers = []

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
    
    compatibility_pct: int = (
        round((identical_count / compared_count) * 100) if compared_count > 0 else 50
    )

    if compatibility_pct < 30:
        tier_msg = CompatibilityMsg.TIER_LOW
    elif 30 <= compatibility_pct < 50:
        tier_msg = CompatibilityMsg.TIER_MID_LOW
    elif 50 <= compatibility_pct < 70:
        tier_msg = CompatibilityMsg.TIER_MID_HIGH
    else:
        tier_msg = CompatibilityMsg.TIER_HIGH

    # ── Update both users ──────────────────────────────────────────────
    failed_users = []
    
    for target_uid, partner_uid in [
        (match_row.user_one_id, match_row.user_two_id),
        (match_row.user_two_id, match_row.user_one_id)
    ]:
        ctx = get_user_state(target_uid)
        try:
            await ctx.update_data(compatibility_pct=compatibility_pct)
            await ctx.set_state(ChatStates.waiting_for_approval)
        except Exception:
            pass

        partner_summary = await build_partner_answer_summary(session, match_id, partner_uid)

        approval_text = (
            f"📊 درصد شباهت پاسخ‌ها: {compatibility_pct}%\n"
            f"🤝 تعداد پاسخ‌های مشترک: {identical_count} از {compared_count} سؤال\n"
            f"💬 خلاصه‌ای از دیدگاه فرد مقابل:\n"
            f"{partner_summary}\n"
            f"━━━━━━━━━━━━━━\n"
            f"{tier_msg}\n\n"
            "آیا مایل به شروع گفتگوی ناشناس عاطفی با این شخص هستید؟ 👇\n"
            "<i>(مکالمه تنها در صورت تایید هر دو طرف باز خواهد شد)</i>"
        )

        try:
            await bot.send_message(
                chat_id=target_uid,
                text=approval_text,
                reply_markup=get_chat_approval_keyboard(),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error(f"Failed to deliver approval prompt to {target_uid}: {exc}")
            failed_users.append(target_uid)
            
    # --- بخش اضافه‌شده: انحلال مچ در صورت قطع شدن یکی از طرفین ---
    if failed_users:
        match_row.is_active = False
        await session.commit()
        
        for target_uid, partner_uid in [
            (match_row.user_one_id, match_row.user_two_id),
            (match_row.user_two_id, match_row.user_one_id)
        ]:
            ctx = get_user_state(target_uid)
            await ctx.set_state(None)
            await ctx.clear()
            
            if target_uid not in failed_users:
                try:
                    from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
                    await bot.send_message(
                        chat_id=target_uid,
                        text="⚠️ <b>خطا در پردازش دیت!</b>\nارتباط پارتنر شما با ربات قطع شده است. لطفاً مجدداً دیت جدیدی را آغاز کنید.",
                        parse_mode="HTML",
                        reply_markup=get_main_menu_keyboard()
                    )
                except Exception:
                    pass



# ================== کدهای افزودنی ==================
async def build_partner_answer_summary(session: AsyncSession, match_id: int, partner_id: int) -> str:
    """خلاصه‌ای از پاسخ‌های کاربر مقابل را برای نمایش می‌سازد (محدود به ۵ مورد)"""
    stmt = select(UserAnswer, Question).join(Question, UserAnswer.question_id == Question.id).where(
        UserAnswer.match_history_id == match_id,
        UserAnswer.user_id == partner_id
    ).order_by(UserAnswer.id)

    result = await session.execute(stmt)
    all_results = result.all()

    lines = []
    max_display = 5

    _opt_map = {
        'A': 'option_a',
        'B': 'option_b',
        'C': 'option_c',
        'D': 'option_d',
    }

    for idx, (ans, q) in enumerate(all_results[:max_display], 1):
        import html as _html
        q_text = getattr(q, 'short_label', None)
        if not q_text:
            # FIX HIGH-07: guard against None question_text.
            _qt = q.question_text or ""
            q_text = (_qt[:30] + "...") if len(_qt) > 30 else _qt
        # FIX HIGH-08: html.escape dynamic content to prevent HTML injection in parse_mode="HTML".
        q_text = _html.escape(q_text)

        opt_attr = _opt_map.get(ans.selected_option, 'option_a')
        opt_text = getattr(q, opt_attr, None) or q.option_a
        opt_text = _html.escape(opt_text)
        lines.append(f"سؤال {idx} (کد {q.id}): {q_text} ⬅️ {opt_text}")

    remaining = len(all_results) - max_display
    if remaining > 0:
        lines.append(f"و {remaining} مورد دیگر...")

    return "\n".join(lines)

from aiogram.filters import StateFilter
from matching_bot_project.bot.core.constants import ReplyBtn

@router.message(
    StateFilter(
        QuestionnaireStates.waiting_for_questions_to_start,
        QuestionnaireStates.answering_questions, 
        QuestionnaireStates.waiting_for_partner_answer
    ),
    ~F.text.in_({
        ReplyBtn.END_DATE, 
        ReplyBtn.END_CHAT, 
        ReplyBtn.PHASE_USER_PROFILE,
        "🎁 ارسال گیفت"   # 👈 دکمه ارسال گیفت به لیست مجاز اضافه شد
    })
)
async def guard_questionnaire_text_input(message: Message):
    """
    Guard to prevent user text inputs from breaking the FSM state during the questionnaire and 5-second wait.
    """
    try:
        await message.delete()
    except Exception:
        pass
    
    await message.answer("رفیق بی زحمت فقط از دکمه های شیشه ای استفاده کن :)")

