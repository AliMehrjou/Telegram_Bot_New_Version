"""
bot/handlers/anonymous_chat.py

Implements the three operational stages of an anonymous dating session:

1.  **Consent Phase** – processes ``approve_chat_yes`` / ``approve_chat_no``
    callbacks while both participants are in ``ChatStates.waiting_for_approval``
    (set by the questionnaire finaliser in ``questionnaire.py`` before it
    dispatches the approval keyboard).

2.  **Live-Chat Phase** – routes every inbound message from one participant to
    their partner while both are in ``ChatStates.anonymous_chat_active``,
    applying real-time privacy filters (username handles, URLs, phone numbers)
    and unconditionally blocking content types that can expose real-world
    identity or location.

3.  **Termination Phase** – processes the ``end_active_chat`` callback to
    cleanly shut the session down, update the database, and return both
    participants to the main menu.

Dependency note
---------------
``ChatStates.waiting_for_approval`` must be declared in
``bot/states/states.py`` and set by ``finalize_questionnaire_and_request_approval``
(``questionnaire.py``) **before** the consent keyboard is dispatched to both
participants.  The current ``ChatStates`` only defines ``anonymous_chat_active``
and must be extended:

    class ChatStates(StatesGroup):
        waiting_for_approval   = State()   # ← add this
        anonymous_chat_active  = State()
"""

import re
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.enums import ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.loader import bot, dp, redis_client
from bot.states.states import ChatStates
from bot.keyboards.inline import get_active_chat_controls
from bot.keyboards.reply import get_main_menu_keyboard
from database.models.models import MatchHistory
from database.queries import crud  # noqa: F401 – available for callers / future use

logger = logging.getLogger(__name__)
router = Router(name="anonymous_chat_handler")


# ─────────────────────────────────────────────────────────────────────────────
# Security filter configuration
# ─────────────────────────────────────────────────────────────────────────────

# Telegram username handles – e.g. @username
USERNAME_REGEX: re.Pattern = re.compile(r"@[a-zA-Z0-9_]{3,32}")

# Web URLs in various common formats
URL_REGEX: re.Pattern = re.compile(
    r"(https?://\S+|www\.\S+|\S+\.(com|ir|org|net|info|me|co)\b)"
)

# Iranian mobile phone numbers (with or without the +98 / 0 prefix)
PHONE_REGEX: re.Pattern = re.compile(r"(\+98|0)?9\d{9}")

# These content types can reveal the sender's real identity or physical location
# and must never be forwarded inside an anonymous session.
FORBIDDEN_CONTENT_TYPES: frozenset[ContentType] = frozenset({
    ContentType.CONTACT,
    ContentType.LOCATION,
    ContentType.VENUE,
    ContentType.POLL,
    ContentType.DICE,
    ContentType.STORY,
})


# ─────────────────────────────────────────────────────────────────────────────
# Internal utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def apply_security_filters(text: str) -> tuple[str, bool]:
    """
    Scans *text* and replaces any Telegram username handles, web URLs, or
    Iranian phone numbers with the generic redaction placeholder
    ``"[⚠️ فیلتر شد]"``.

    This function is **pure** – it has no side-effects and relies on no
    external state, making it straightforward to unit-test in isolation.

    Args:
        text: The raw string to sanitise.

    Returns:
        ``(sanitised_text, was_modified)`` where ``was_modified`` is ``True``
        when at least one substitution was performed.  Callers can branch on
        this flag to issue a single conditional notice to the sender without
        repeating the regex passes.
    """
    was_filtered = False

    if USERNAME_REGEX.search(text):
        text = USERNAME_REGEX.sub("[⚠️ فیلتر شد]", text)
        was_filtered = True

    if URL_REGEX.search(text):
        text = URL_REGEX.sub("[⚠️ فیلتر شد]", text)
        was_filtered = True

    if PHONE_REGEX.search(text):
        text = PHONE_REGEX.sub("[⚠️ فیلتر شد]", text)
        was_filtered = True

    return text, was_filtered


def _resolve_partner_fsm(partner_tg_id: int) -> FSMContext:
    """
    Constructs an :class:`~aiogram.fsm.context.FSMContext` for a user who is
    **not** the sender of the current Telegram update.

    This lets handlers read or mutate another participant's FSM state from
    within a handler that was triggered by a different user's event.

    ``bot.id`` is derived from the numeric prefix of the bot token at class
    construction time and requires no API call; it is always available after
    the :class:`~aiogram.Bot` object is instantiated.

    Args:
        partner_tg_id: Telegram user ID of the target participant.

    Returns:
        A fully functional :class:`~aiogram.fsm.context.FSMContext` backed by
        the shared Redis storage instance.
    """
    return FSMContext(
        storage=dp.storage,
        key=StorageKey(
            bot_id=bot.id,
            chat_id=partner_tg_id,
            user_id=partner_tg_id,
        ),
    )


async def _safe_send(
    tg_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
    with_main_menu: bool = False,
) -> None:
    """
    Sends a message to *tg_id*, swallowing any delivery error so callers never
    crash because the partner has blocked the bot or deleted their account.

    Args:
        tg_id: Telegram user ID of the recipient.
        text: Message body to deliver.
        parse_mode: Optional Telegram parse mode (e.g. ``"Markdown"``).
        with_main_menu: When ``True``, the main reply keyboard is attached.
    """
    try:
        await bot.send_message(
            chat_id=tg_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=get_main_menu_keyboard() if with_main_menu else None,
        )
    except Exception as exc:
        logger.error("Could not deliver message to user %d: %s", tg_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Handler 1 – Consent phase (approve / reject the anonymous chat channel)
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(
    ChatStates.waiting_for_approval,
    F.data.in_({"approve_chat_yes", "approve_chat_no"}),
)
async def register_chat_consent(
    call: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    """
    Processes one participant's consent decision after the questionnaire ends.

    This handler is guarded by ``ChatStates.waiting_for_approval`` which must
    be set by ``finalize_questionnaire_and_request_approval`` (in
    ``questionnaire.py``) before the approval keyboard is dispatched.

    Rejection flow
    ~~~~~~~~~~~~~~
    * Sets ``match_history.is_active = False`` and commits.
    * Clears FSM for **both** the caller and the partner.
    * Returns both to the main menu with localised Persian notices.

    Approval flow
    ~~~~~~~~~~~~~
    * Persists the caller's ``user_one_approved`` or ``user_two_approved``
      flag and commits to the database.
    * Immediately calls ``db_session.refresh`` to capture a concurrent approval
      that the partner may have committed within the same millisecond (race
      condition prevention).
    * **If both flags are now ``True``:**

      - Sets ``match_history.chat_approved = True`` and commits.
      - Updates Redis tracking keys for both users to ``"chatting"``.
      - Transitions **both** users to ``ChatStates.anonymous_chat_active`` and
        stores ``(match_history_id, partner_id)`` in each user's FSM data.
      - Sends the chat-activation notice with ``get_active_chat_controls()``
        to both participants.

    * **If only this party has approved so far:**

      - The approval keyboard was already stripped earlier in this handler.
      - Edits the caller's message to a "waiting for partner" notice.
    """
    await call.answer()

    tg_id: int = call.from_user.id
    fsm_data: dict = await state.get_data()
    match_history_id: int | None = fsm_data.get("match_history_id")

    # ── Guard: FSM must carry a valid match reference ─────────────────────── #
    if not match_history_id:
        logger.error(
            "User %d invoked consent handler with no match_history_id in FSM.",
            tg_id,
        )
        try:
            await call.message.edit_text(
                "⚠️ خطا: اطلاعات دیت یافت نشد. لطفاً /start را مجدداً ارسال کنید."
            )
        except Exception:
            pass
        await state.clear()
        return

    # ── Fetch and validate the MatchHistory record ────────────────────────── #
    match_history: MatchHistory | None = await db_session.get(
        MatchHistory, match_history_id
    )

    if not match_history or not match_history.is_active:
        logger.warning(
            "User %d tried to consent on an inactive / missing match (ID %d).",
            tg_id,
            match_history_id,
        )
        try:
            await call.message.edit_text(
                "⚠️ این دیت دیگر فعال نیست یا قبلاً پایان یافته است."
            )
        except Exception:
            pass
        await state.clear()
        return

    # ── Identify the caller's role and the partner's Telegram ID ─────────── #
    # Note: user_one_id / user_two_id store Telegram user IDs in this
    # codebase; the FK to users.id is intentionally overloaded this way
    # throughout the project.
    is_user_one: bool = match_history.user_one_id == tg_id
    partner_id: int = (
        match_history.user_two_id if is_user_one else match_history.user_one_id
    )
    partner_ctx: FSMContext = _resolve_partner_fsm(partner_id)

    # ── Strip the inline keyboard immediately to prevent double-submissions ── #
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.warning(
            "Could not remove approval keyboard for user %d: %s", tg_id, exc
        )

    # ════════════════════════════════════════════════════════════════════════ #
    # REJECTION PATH                                                          #
    # ════════════════════════════════════════════════════════════════════════ #

    if call.data == "approve_chat_no":
        match_history.is_active = False
        try:
            await db_session.commit()
        except Exception as exc:
            logger.error(
                "DB commit failed when deactivating match %d after rejection: %s",
                match_history_id,
                exc,
            )
            await db_session.rollback()

        # Inform and clean up the caller.
        await state.clear()
        await _safe_send(
            tg_id,
            "❌ گفتگو رد شد. به منوی اصلی بازگشتید.",
            with_main_menu=True,
        )

        # Inform and clean up the partner.
        try:
            await partner_ctx.clear()
        except Exception as exc:
            logger.warning(
                "Could not clear FSM for partner %d after rejection: %s",
                partner_id,
                exc,
            )

        await _safe_send(
            partner_id,
            "⚠️ متاسفانه پارتنر شما با برقراری چت موافقت نکرد. دیت پایان یافت.",
            with_main_menu=True,
        )
        return

    # ════════════════════════════════════════════════════════════════════════ #
    # APPROVAL PATH                                                           #
    # ════════════════════════════════════════════════════════════════════════ #

    if is_user_one:
        match_history.user_one_approved = True
    else:
        match_history.user_two_approved = True

    try:
        await db_session.commit()
        # Critical: refresh immediately to capture a concurrent approval
        # the partner may have committed within the same millisecond.
        await db_session.refresh(match_history)
    except Exception as exc:
        logger.error(
            "DB commit/refresh failed for consent on match %d: %s",
            match_history_id,
            exc,
        )
        await db_session.rollback()
        try:
            await call.message.answer(
                "⚠️ خطایی در ثبت موافقت رخ داد. لطفاً دوباره امتحان کنید."
            )
        except Exception:
            pass
        return

    both_approved: bool = (
        match_history.user_one_approved and match_history.user_two_approved
    )

    # ── Both parties have consented → open the anonymous channel ─────────── #
    if both_approved:
        match_history.chat_approved = True
        try:
            await db_session.commit()
        except Exception as exc:
            logger.error(
                "DB commit failed when setting chat_approved on match %d: %s",
                match_history_id,
                exc,
            )
            await db_session.rollback()
            return

        # Inform external services (e.g. timeout scheduler) that both users
        # have transitioned out of the questionnaire / consent phase.
        try:
            await redis_client.hset(f"user:state:{tg_id}", "status", "chatting")
            await redis_client.hset(f"user:state:{partner_id}", "status", "chatting")
        except Exception as exc:
            logger.error(
                "Redis status update failed for match %d: %s", match_history_id, exc
            )

        activation_text = (
            "🗣️ *اتصال با موفقیت برقرار شد! گفتگو آغاز گردید.*\n\n"
            "🔒 امنیت شما محفوظ است. هویت پارتنر کاملاً پنهان نگه داشته می‌شود.\n"
            "🚫 آیدی تلگرام، شماره تلفن و لینک‌های وب به صورت خودکار فیلتر می‌شوند.\n\n"
            "برای پایان دادن به گفتگو دکمه زیر را فشار دهید 👇"
        )

        # Set FSM state and dispatch the activation notice for both users.
        for uid, peer_id in ((tg_id, partner_id), (partner_id, tg_id)):
            ctx = _resolve_partner_fsm(uid)
            await ctx.set_state(ChatStates.anonymous_chat_active)
            await ctx.update_data(
                match_history_id=match_history.id,
                partner_id=peer_id,
            )
            try:
                partner_for_uid = user_one_id if uid == user_two_id else user_two_id
                await bot.send_message(
                    chat_id=uid,
                    text=activation_text,
                    reply_markup=get_active_chat_controls(partner_for_uid),
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.error(
                    "Failed to deliver chat-activation message to user %d: %s",
                    uid,
                    exc,
                )

    # ── Only this party has approved – ask the caller to wait ────────────── #
    else:
        # The inline keyboard was already stripped above; just update the text.
        try:
            await call.message.edit_text(
                "⏳ موافقت شما ثبت شد. منتظر تایید طرف مقابل بمانید..."
            )
        except Exception as exc:
            logger.error(
                "Failed to edit waiting-confirmation for user %d: %s", tg_id, exc
            )


# ─────────────────────────────────────────────────────────────────────────────
# Handler 2 – Live anonymous message routing
# ─────────────────────────────────────────────────────────────────────────────

@router.message(ChatStates.anonymous_chat_active)
async def route_anonymous_chat_message(message: Message, state: FSMContext) -> None:
    """
    Intercepts **every** inbound message from an active participant and relays
    it to their partner after applying all privacy and security filters.

    Content-type blocking
    ~~~~~~~~~~~~~~~~~~~~~
    Contacts, locations, venues, polls, dice rolls, and stories are rejected
    with an explanatory reply and are **never** forwarded.  These types can
    reveal the sender's real-world identity or physical whereabouts.

    Text filtering
    ~~~~~~~~~~~~~~
    All text bodies and media captions are scanned with three compiled regular
    expressions.  Matching substrings are replaced with ``"[⚠️ فیلتر شد]"``:

    * Telegram username handles (``@username``)
    * Web URLs (``https://…``, ``www.…``, bare TLD links)
    * Iranian phone numbers (leading ``09`` or ``+98``)

    The sender receives a single notice if any substitution was performed.

    Relay strategy
    ~~~~~~~~~~~~~~
    * *Text messages* – forwarded via ``bot.send_message`` prefixed with
      ``"💬: "`` to visually differentiate peer messages from system notices.
    * *Media messages* – forwarded via ``bot.copy_message``.  The caption
      (if present) is filtered before forwarding, and ``reply_markup=None``
      is always passed to strip any inline keyboard from the original message,
      preventing callback-payload injection by a malicious sender.
    """
    tg_id: int = message.from_user.id
    fsm_data: dict = await state.get_data()
    partner_id: int | None = fsm_data.get("partner_id")

    # Guard: partner_id must be present; absence indicates a broken session.
    if not partner_id:
        logger.error(
            "User %d is in anonymous_chat_active but has no partner_id in FSM.",
            tg_id,
        )
        await message.answer(
            "⚠️ مکالمه به اتمام رسیده است یا خطایی رخ داد.",
            reply_markup=get_main_menu_keyboard(),
        )
        await state.clear()
        return

    # ── Block content types that could expose identity or location ────────── #
    if message.content_type in FORBIDDEN_CONTENT_TYPES:
        await message.reply(
            "⚠️ ارسال مخاطب، موقعیت مکانی، نظرسنجی و محتوای مشابه "
            "در چت ناشناس مجاز نیست و مسدود شد."
        )
        return

    # ── Text messages ─────────────────────────────────────────────────────── #
    if message.text:
        filtered_text, was_filtered = apply_security_filters(message.text)

        if was_filtered:
            await message.reply(
                "⚠️ پیام شما حاوی اطلاعات شخصی بود. "
                "موارد ممنوع حذف شده و پیام ارسال گردید."
            )

        from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError
        try:
            await bot.send_message(
                chat_id=partner_id,
                text=f"💬: {filtered_text}",
            )
        except TelegramForbiddenError:
            logger.warning(f"Partner {partner_id} blocked the bot during chat with {tg_id}")
            await message.reply("⚠️ پارتنر ربات را بلاک کرده است و اتصال قطع شد.")
            await state.clear()
        except TelegramAPIError as exc:
            logger.error(f"Telegram API Error relaying text from {tg_id} to {partner_id}: {exc}")
            await message.reply("⚠️ خطای تلگرام در ارسال پیام.")
        except Exception as exc:
            logger.error(
                "Failed to relay text from user %d to partner %d: %s",
                tg_id,
                partner_id,
                exc,
            )
            await message.reply(
                "⚠️ پیام به پارتنر تحویل داده نشد. "
                "احتمالاً ربات را بلاک کرده است."
            )
        return

    # ── Media messages (photo, video, audio, document, sticker, voice, …) ── #
    raw_caption: str = message.caption or ""
    sanitized_caption: str | None = None

    if raw_caption:
        sanitized_caption, caption_was_filtered = apply_security_filters(raw_caption)
        if caption_was_filtered:
            await message.reply(
                "⚠️ کپشن پیام شما حاوی اطلاعات ممنوع بود. "
                "پس از پاکسازی ارسال شد."
            )

    try:
        await bot.copy_message(
            chat_id=partner_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            # Provide the sanitised caption only when the original had one.
            # Passing None for caption-less media (stickers, GIFs, etc.)
            # lets Telegram preserve the natural absence of a caption.
            caption=sanitized_caption if raw_caption else None,
            # ⚠️ CRITICAL: Always strip inline keyboards from forwarded media
            # to prevent malicious callback-payload injection.
            reply_markup=None,
        )
    except TelegramForbiddenError:
        logger.warning(f"Partner {partner_id} blocked the bot during chat with {tg_id}")
        await message.reply("⚠️ پارتنر ربات را بلاک کرده است و اتصال قطع شد.")
        await state.clear()
    except TelegramAPIError as exc:
        logger.error(f"Telegram API Error relaying media from {tg_id} to {partner_id}: {exc}")
        await message.reply("⚠️ خطای تلگرام در ارسال پیام.")
    except Exception as exc:
        logger.error(
            "Failed to forward media from user %d to partner %d: %s",
            tg_id,
            partner_id,
            exc,
        )
        await message.reply(
            "⚠️ ارسال این فایل پشتیبانی نمی‌شود "
            "یا پارتنر ربات را بلاک کرده است."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Handler 3 – Voluntary chat termination
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(ChatStates.anonymous_chat_active, F.data == "end_active_chat")
async def end_active_anonymous_chat(
    call: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    """
    Gracefully tears down the active anonymous chat session when a participant
    presses the end-chat control.

    Teardown sequence
    ~~~~~~~~~~~~~~~~~
    1. Marks ``MatchHistory.is_active = False`` in the database and commits.
    2. Deletes both users' Redis tracking keys (``user:state:{id}``).
    3. Clears both users' FSM states.
    4. Edits the caller's chat-controls message to ``"🛑 گفتگو را پایان دادید."``.
    5. Sends the caller a new message with the main reply keyboard.
    6. Sends the partner ``"🛑 پارتنر شما گفتگو را خاتمه داد."`` with the
       main reply keyboard.

    All Telegram delivery calls are wrapped in ``try/except`` so the handler
    never crashes if the partner has blocked the bot.
    """
    await call.answer()

    tg_id: int = call.from_user.id
    fsm_data: dict = await state.get_data()
    partner_id: int | None = fsm_data.get("partner_id")
    match_history_id: int | None = fsm_data.get("match_history_id")

    # ── Deactivate the match record in the database ───────────────────────── #
    if match_history_id:
        match_row: MatchHistory | None = await db_session.get(
            MatchHistory, match_history_id
        )
        if match_row and match_row.is_active:
            match_row.is_active = False
            try:
                await db_session.commit()
            except Exception as exc:
                logger.error(
                    "DB commit failed when closing match %d: %s",
                    match_history_id,
                    exc,
                )
                await db_session.rollback()
    else:
        logger.warning(
            "User %d ended an anonymous chat with no match_history_id in FSM.",
            tg_id,
        )

    # ── Clean up the caller's Redis key and FSM state ────────────────────── #
    try:
        await redis_client.delete(f"user:state:{tg_id}")
    except Exception as exc:
        logger.error("Redis delete failed for caller %d: %s", tg_id, exc)

    await state.clear()

    # Acknowledge the termination by editing the chat-controls message.
    try:
        await call.message.edit_text("🛑 گفتگو را پایان دادید.")
    except Exception as exc:
        logger.warning(
            "Could not edit end-chat message for caller %d: %s", tg_id, exc
        )

    # Send the caller back to the main menu via a new message.
    try:
        await call.message.answer(
            "به منوی اصلی بازگشتید 👇",
            reply_markup=get_main_menu_keyboard(),
        )
    except Exception as exc:
        logger.error("Failed to send main menu to caller %d: %s", tg_id, exc)

    # ── Clean up the partner's Redis key and FSM state ───────────────────── #
    if not partner_id:
        logger.warning(
            "User %d ended chat with no partner_id in FSM; partner not notified.",
            tg_id,
        )
        return

    try:
        await redis_client.delete(f"user:state:{partner_id}")
    except Exception as exc:
        logger.error("Redis delete failed for partner %d: %s", partner_id, exc)

    partner_ctx: FSMContext = _resolve_partner_fsm(partner_id)

    try:
        await partner_ctx.clear()
    except Exception as exc:
        logger.warning(
            "Failed to clear FSM for partner %d after end-chat: %s", partner_id, exc
        )

    await _safe_send(
        partner_id,
        "🛑 پارتنر شما گفتگو را خاتمه داد. به منوی اصلی بازگشتید.",
        with_main_menu=True,
    )