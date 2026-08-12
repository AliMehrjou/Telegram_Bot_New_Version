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

from matching_bot_project.database.queries import crud
from matching_bot_project.database.models.models import MatchHistory
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.enums import ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from matching_bot_project.bot.core.constants import ReplyBtn, Messages as SystemMsg
from matching_bot_project.bot.core.loader import bot, dp, redis_client
from matching_bot_project.bot.states.states import ChatStates
from matching_bot_project.bot.keyboards.inline import get_active_chat_controls
from matching_bot_project.bot.keyboards.reply import (
    get_main_menu_keyboard,
    get_chat_phase_keyboard,           # ← NEW
)
from matching_bot_project.database.models.models import MatchHistory
from matching_bot_project.database.queries import crud
from matching_bot_project.bot.core.constants import ReplyBtn

logger = logging.getLogger(__name__)
router = Router(name="anonymous_chat_handler")


async def _recover_chat_session(tg_id: int, state: FSMContext) -> tuple[int | None, int | None]:
    """
    وقتی کاربر در ChatStates.anonymous_chat_active هست ولی FSM data ناقصه
    (مثلاً بعد از رفتن به پروفایل و برگشتن)، اطلاعات چت رو از Redis بازیابی
    می‌کنه و دوباره در FSM ذخیره می‌کنه تا چت ادامه پیدا کنه.

    Returns:
        (partner_id, match_history_id)
    """
    try:
        redis_state = await redis_client.hgetall(f"user:state:{tg_id}")
        matched_with = redis_state.get("matched_with") if redis_state else None
        partner_id = int(matched_with) if matched_with else None

        fsm_data = await state.get_data()
        match_history_id = fsm_data.get("match_history_id")

        if partner_id:
            await state.update_data(partner_id=partner_id)
            logger.info(
                "Recovered partner_id=%d for user %d from Redis (FSM data was stale).",
                partner_id, tg_id,
            )

        return partner_id, match_history_id

    except Exception as exc:
        logger.error("Failed to recover chat session for user %d: %s", tg_id, exc)
        return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Security filter configuration
# ─────────────────────────────────────────────────────────────────────────────
FORBIDDEN_CONTENT_TYPES: frozenset[ContentType] = frozenset({
    ContentType.CONTACT,
    ContentType.LOCATION,
    ContentType.VENUE,
    ContentType.POLL,
    ContentType.DICE,
    ContentType.STORY,
    ContentType.INVOICE,            
    ContentType.SUCCESSFUL_PAYMENT,        
    
})


# ─────────────────────────────────────────────────────────────────────────────
# Internal utility helpers
# ─────────────────────────────────────────────────────────────────────────────

# Telegram username handles – e.g. @username
USERNAME_REGEX: re.Pattern = re.compile(r"@[a-zA-Z0-9_]{5,32}")

# Web URLs in various common formats (comprehensive TLD list)
URL_REGEX: re.Pattern = re.compile(
    r"(https?://\S+|www\.\S+|\b[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\."
    r"(?:com|ir|org|net|info|me|co|io|app|dev|xyz|click|page|link|site|online|"
    r"store|shop|tech|cloud|fun|world|life|live|news|blog|agency|digital|media|"
    r"network|solutions|systems|academy|consulting|engineering|ventures|partners|"
    r"capital|holdings|group|company|limited|international|global|worldwide|"
    r"pro|biz|name|tv|cc|ws|us|uk|ca|au|de|fr|jp|cn|in|br|ru|kr|tw|hk|sg|my|"
    r"th|vn|id|ph|nz|ie|es|it|nl|be|se|no|fi|dk|pl|cz|hu|gr|pt|ro|bg|hr|si|"
    r"sk|lt|lv|ee|cy|lu|mt|is|li|mc|ad|sm|va|tr|il|ae|sa|qa|kw|bh|om|jo|lb|"
    r"iq|sy|ye|eg|ly|tn|dz|ma|sd|so|dj|km|sc|mu|re|yt|mayotte)\b)",
    re.IGNORECASE
)

# Iranian mobile phone numbers (with or without the +98 / 0 prefix)
PHONE_REGEX: re.Pattern = re.compile(r"(\+98|0)?9\d{9}")


def apply_security_filters(text: str) -> tuple[str, bool]:
    """
    Scans *text* and replaces any Telegram username handles, web URLs, or
    Iranian phone numbers with the generic redaction placeholder
    ``"[⚠️ فیلتر شد]"``.
    """
    was_filtered = False

    if USERNAME_REGEX.search(text):
        text = USERNAME_REGEX.sub("فیلتر شد :(", text)
        was_filtered = True

    if URL_REGEX.search(text):
        text = URL_REGEX.sub("فیلتر شد :(", text)
        was_filtered = True

    if PHONE_REGEX.search(text):
        text = PHONE_REGEX.sub("فیلتر شد :(", text)
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

async def activate_anonymous_chat_session(
    db_session: AsyncSession,
    user_one_id: int,
    user_two_id: int,
    match_history: MatchHistory | None = None,
) -> MatchHistory:
    """
    باز کردن کانال چت ناشناس بین دو کاربر و قرار دادن هر دو در
    ChatStates.anonymous_chat_active.
    """
    if match_history is None:
        match_history = await crud.create_match_history(db_session, user_one_id, user_two_id)

    match_history.chat_approved = True
    match_history.questionnaire_completed = True
    match_history.user_one_approved = True
    match_history.user_two_approved = True

    try:
        await db_session.commit()
    except Exception as exc:
        logger.error(
            "DB commit failed when activating anonymous chat session %s <-> %s: %s",
            user_one_id, user_two_id, exc,
        )
        await db_session.rollback()
        raise

    # خلع سلاح تایمر دیت
    try:
        await redis_client.delete(f"date:timeout:{match_history.id}")
    except Exception as exc:
        logger.error("Failed to disarm timeout timer for match %d: %s", match_history.id, exc)

    try:
        await redis_client.hset(f"user:state:{user_one_id}", mapping={"status": "chatting", "matched_with": str(user_two_id)})
        await redis_client.hset(f"user:state:{user_two_id}", mapping={"status": "chatting", "matched_with": str(user_one_id)})
        
        # --- اعمال قفل ۵ ثانیه‌ای در استارت چت ---
        await redis_client.setex(f"anti_skip_lock:{match_history.id}", 5, "1")
        # ------------------------------------------------
    except Exception as exc:
        logger.error(
            "Redis status update failed for match %d: %s", match_history.id, exc
        )

    activation_text = (
        "🗣️ *اتصال با موفقیت برقرار شد! گفتگو آغاز گردید.*\n\n"
        "🔒 امنیت شما محفوظ است. هویت پارتنر کاملاً پنهان نگه داشته می‌شود.\n"
        "🚫 آیدی تلگرام، شماره تلفن و لینک‌های وب به صورت خودکار فیلتر می‌شوند.\n\n"
        "برای پایان دادن به گفتگو دکمه زیر را فشار دهید 👇"
    )

    for uid, peer_id in ((user_one_id, user_two_id), (user_two_id, user_one_id)):
        ctx = _resolve_partner_fsm(uid)
        await ctx.set_state(ChatStates.anonymous_chat_active)
        await ctx.update_data(
            match_history_id=match_history.id,
            partner_id=peer_id,
        )

        try:
            await bot.send_message(
                chat_id=uid,
                text=activation_text,
                reply_markup=get_active_chat_controls(peer_id),
                parse_mode="Markdown",
            )
            try:
                await bot.send_message(
                    chat_id=uid,
                    text="کیبورد چت ناشناس شما آماده است 👇",
                    reply_markup=get_chat_phase_keyboard(),
                )
            except Exception:
                pass
        except Exception as exc:
            logger.error(
                "Failed to deliver chat-activation message to user %d: %s",
                uid,
                exc,
            )

    return match_history

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
    await call.answer()
 
    tg_id: int = call.from_user.id
    fsm_data: dict = await state.get_data()
    match_history_id: int | None = fsm_data.get("match_history_id")
 
    if not match_history_id:
        logger.error("User %d invoked consent handler with no match_history_id in FSM.", tg_id)
        try:
            await call.message.edit_text("⚠️ خطا: اطلاعات دیت یافت نشد. لطفاً /start را مجدداً ارسال کنید.")
        except Exception:
            pass
        await state.clear()
        return
 
    match_history: MatchHistory | None = await db_session.get(MatchHistory, match_history_id)
 
    if not match_history or not match_history.is_active:
        logger.warning("User %d tried to consent on inactive/missing match (ID %d).", tg_id, match_history_id)
        try:
            await call.message.edit_text("⚠️ این دیت دیگر فعال نیست یا قبلاً پایان یافته است.")
        except Exception:
            pass
        await state.clear()
        return
 
    # FIX (باگ ۴): guard رو قبل از هر چیز چک کن
    if match_history.chat_approved:
        # یه coroutine دیگه قبلاً activation رو انجام داده
        return
 
    is_user_one: bool = match_history.user_one_id == tg_id
    partner_id: int = (
        match_history.user_two_id if is_user_one else match_history.user_one_id
    )
    partner_ctx: FSMContext = _resolve_partner_fsm(partner_id)
 
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.warning("Could not remove approval keyboard for user %d: %s", tg_id, exc)
 
    # ════════════════════════════════════════════════════════════════════════ #
    # REJECTION PATH                                                          #
    # ════════════════════════════════════════════════════════════════════════ #
    if call.data == "approve_chat_no":
        match_history.is_active = False
        try:
            await db_session.commit()
        except Exception as exc:
            logger.error("DB commit failed when deactivating match %d: %s", match_history_id, exc)
            await db_session.rollback()
 
        await state.clear()
        await _safe_send(tg_id, "❌ گفتگو رد شد. به منوی اصلی بازگشتید.", with_main_menu=True)
 
        # FIX (باگ ۲): partner ممکنه keyboard داشته باشه، اول text رو edit کن
        try:
            partner_fsm_data = await partner_ctx.get_data()
            # Partner رو از waiting_for_approval خارج کن
            await partner_ctx.set_state(None)  # FIX: set_state(None) قبل از clear() ضروری است
            await partner_ctx.clear()
        except Exception as exc:
            logger.warning("Could not clear FSM for partner %d after rejection: %s", partner_id, exc)
 
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
        try:
            await activate_anonymous_chat_session(
                db_session, match_history.user_one_id, match_history.user_two_id, match_history
            )
        except Exception:
            try:
                await call.message.answer(
                    "⚠️ خطایی در فعال‌سازی گفتگو رخ داد. لطفاً دوباره امتحان کنید."
                )
            except Exception:
                pass

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

@router.message(
    ChatStates.anonymous_chat_active,
    ~F.text.startswith("/"),  # جلوگیری قطعی از روت شدن تمام کامندها
    ~F.text.in_({
        ReplyBtn.PHASE_USER_PROFILE, 
        ReplyBtn.CHAT_PHASE_END_CHAT, 
        ReplyBtn.DATE_PHASE_END_DATE, 
        ReplyBtn.END_CHAT, 
        ReplyBtn.END_DATE,
        "🎁 ارسال گیفت",
        "📬 صندوق پیام‌ها"  # اضافه شدن دکمه صندوق به لیست سیاه
    })
)
async def route_anonymous_chat_message(message: Message, state: FSMContext) -> None:
    tg_id: int = message.from_user.id
    fsm_data: dict = await state.get_data()
    partner_id: int | None = fsm_data.get("partner_id")
    match_history_id: int | None = fsm_data.get("match_history_id")

    if not partner_id:
        partner_id, match_history_id_recovered = await _recover_chat_session(tg_id, state)
        if not match_history_id and match_history_id_recovered:
            match_history_id = match_history_id_recovered

    if not partner_id:
        logger.error(
            "User %d is in anonymous_chat_active but partner_id not found in FSM or Redis. Closing session.",
            tg_id,
        )
        await message.answer(
            "⚠️ مکالمه به اتمام رسیده است یا خطایی رخ داد.",
            reply_markup=get_main_menu_keyboard(),
        )
        await state.set_state(None)
        await state.clear()
        return

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
            await message.reply("⛔️ پیام شما حاوی لینک یا آیدی تلگرام بود و ارسال نشد.")
            return

        filtered_text = filtered_text[:4090]
        try:
            sent_msg = await bot.send_message(
                chat_id=partner_id,
                text=filtered_text,
            )
            if match_history_id:
                key = f"match:{match_history_id}:msgs:{partner_id}"
                await redis_client.sadd(key, sent_msg.message_id)
                await redis_client.expire(key, 172800)
                
        except TelegramForbiddenError:
            logger.warning(f"Partner {partner_id} blocked the bot during chat with {tg_id}")
            await message.reply("⚠️ پارتنر ربات را بلاک کرده است و اتصال قطع شد.")
            await state.set_state(None)
            await state.clear()
            if match_history_id:
                # Import required for the db_session lookup if not handled at router level
                from matching_bot_project.database.session import async_session_factory
                async with async_session_factory() as db_session:
                    match_row = await db_session.get(MatchHistory, match_history_id)
                    if match_row and match_row.is_active:
                        match_row.is_active = False
                        await db_session.commit()
        except TelegramAPIError as exc:
            logger.error(f"Telegram API Error relaying text from {tg_id} to {partner_id}: {exc}")
            await message.reply("⚠️ خطای تلگرام در ارسال پیام.")
        except Exception as exc:
            logger.error(
                "Failed to relay text from user %d to partner %d: %s",
                tg_id, partner_id, exc,
            )
            await message.reply("⚠️ پیام به پارتنر تحویل داده نشد.")
        return
    
    # ── Media messages ────────────────────────────────────────────────────── #
    raw_caption: str = message.caption or ""
    sanitized_caption: str | None = None

    if raw_caption:
        sanitized_caption, caption_was_filtered = apply_security_filters(raw_caption)
        if caption_was_filtered:
            await message.reply(
                "⛔️ کپشن پیام شما حاوی لینک یا آیدی تلگرام بود و ارسال نشد."
            )
            return
        sanitized_caption = sanitized_caption[:1024]

    try:
        sent_msg = await bot.copy_message(
            chat_id=partner_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            caption=sanitized_caption if raw_caption else None,
            reply_markup=None,
        )
        if match_history_id:
            key = f"match:{match_history_id}:msgs:{partner_id}"
            await redis_client.sadd(key, sent_msg.message_id)
            await redis_client.expire(key, 172800)
            
    except TelegramForbiddenError:
        logger.warning(f"Partner {partner_id} blocked the bot during chat with {tg_id}")
        await message.reply("⚠️ پارتنر ربات را بلاک کرده است و اتصال قطع شد.")
        await state.set_state(None)
        await state.clear()
        if match_history_id:
            from matching_bot_project.database.session import async_session_factory
            async with async_session_factory() as db_session:
                match_row = await db_session.get(MatchHistory, match_history_id)
                if match_row and match_row.is_active:
                    match_row.is_active = False
                    await db_session.commit()
    except TelegramAPIError as exc:
        logger.error(f"Telegram API Error relaying media from {tg_id} to {partner_id}: {exc}")
        await message.reply("⚠️ خطای تلگرام در ارسال پیام.")
    except Exception as exc:
        logger.error(
            "Failed to forward media from user %d to partner %d: %s",
            tg_id, partner_id, exc,
        )
        await message.reply("⚠️ ارسال این فایل پشتیبانی نمی‌شود یا پارتنر ربات را بلاک کرده است.")


# ─────────────────────────────────────────────────────────────────────────────
# Handler 2.5 – REMOVED (was: profile view during chat)
# ─────────────────────────────────────────────────────────────────────────────
# 🛡️ FIX (باگ اصلی گزارش‌شده): این فایل و interactions.py هر دو یک هندلر برای
# `F.text == ReplyBtn.PHASE_USER_PROFILE` داشتند:
#   - اینجا: با فیلتر state=ChatStates.anonymous_chat_active
#   - interactions.py::view_partner_profile_from_reply_btn: بدون فیلتر state
#
# در aiogram وقتی دو هندلر روی یک آپدیت match می‌کنند، فقط همون هندلری که زودتر
# در روتر رجیستر شده اجرا میشه و باقی رو "می‌بلعه". یعنی بسته به ترتیب include
# روترها در main.py، یا:
#   (الف) این هندلر برنده می‌شد → پروفایل واقعی پارتنر هیچ‌وقت در حین چت نشون
#         داده نمی‌شد (فقط همین پیام جنریک «از دکمه‌های زیر استفاده کنید» تکرار
#         می‌شد) و کیبورد محدودشده‌ی in_active_match=True در interactions.py که
#         دقیقاً برای همین حالت طراحی شده بود، هیچ‌وقت اجرا نمی‌شد.
#   (ب) یا اون یکی برنده می‌شد → منطق ذخیره‌سازی matched_with در Redis اینجا
#         هیچ‌وقت اجرا نمی‌شد.
#
# رفع نهایی:
#   1. matched_with حالا در خودِ activate_anonymous_chat_session (بالاتر در همین
#      فایل) و برای هر دو نفر همزمان با شروع چت ست می‌شود؛ دیگر وابسته به این‌که
#      کاربر دکمه‌ی پروفایل را بزند نیست، پس نیازی به این هندلر جداگانه نمانده.
#   2. interactions.py::view_partner_profile_from_reply_btn اکنون تنها و بدون
#      ابهام، هندلر این دکمه است (چه در چت، چه در دیت)، مستقیماً از دیتابیس
#      پارتنر فعال را می‌خواند (get_active_match) و ربطی به FSM ندارد، پس با
#      حذف این هندلر هیچ عملکردی از دست نمی‌رود.


# ─────────────────────────────────────────────────────────────────────────────
# Handler 3 – Voluntary chat termination
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(ChatStates.anonymous_chat_active, F.data == "end_active_chat")
async def end_active_anonymous_chat(
    call: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    tg_id: int = call.from_user.id
    fsm_data: dict = await state.get_data()
    partner_id: int | None = fsm_data.get("partner_id")
    match_history_id: int | None = fsm_data.get("match_history_id")

    # 🛡️ بازیابی هوشمند سطح ۱ (Redis) و سطح ۲ (MySQL)
    if not partner_id or not match_history_id:
        recovered_partner, recovered_match = await _recover_chat_session(tg_id, state)
        partner_id = partner_id or recovered_partner
        match_history_id = match_history_id or recovered_match

        # اگر ردیس هم خالی بود، مستقیما از دیتابیس می‌خونیم
        if not partner_id:
            active_match = await crud.get_active_match(db_session, tg_id)
            if active_match:
                partner_id = active_match.user_two_id if active_match.user_one_id == tg_id else active_match.user_one_id
                match_history_id = match_history_id or active_match.id

    # --- بخش ۲: بررسی قفل ۵ ثانیه‌ای ---
    if match_history_id:
        is_locked = await redis_client.exists(f"anti_skip_lock:{match_history_id}")
        if is_locked:
            await call.answer("یه کوچولو صبر کن عزیزم، تازه رسیدی! 😉", show_alert=False)
            return

    await call.answer()

    # ── DB: match رو deactivate کن ────────────────────────────────────────── #
    if match_history_id:
        match_row: MatchHistory | None = await db_session.get(MatchHistory, match_history_id)
        if match_row and match_row.is_active:
            match_row.is_active = False
            try:
                await db_session.commit()
            except Exception as exc:
                logger.error("DB commit failed when closing match %d: %s", match_history_id, exc)
                await db_session.rollback()
    else:
        logger.warning("User %d ended anonymous chat with no match_history_id anywhere.", tg_id)

    # ── Caller: Redis + FSM + UI ──────────────────────────────────────────── #
    try:
        await redis_client.delete(f"user:state:{tg_id}")
        if partner_id:
            await redis_client.setex(f"user:{tg_id}:last_match_partner", 86400, str(partner_id))
        if match_history_id:
            await redis_client.delete(f"date:timeout:{match_history_id}")
    except Exception as exc:
        logger.error("Redis operation failed for caller %d: %s", tg_id, exc)

    # 🛡️ پاکسازی قطعی زامبی استیت برای فراخواننده خروج
    await state.set_state(None)
    await state.clear()

    try:
        await call.message.edit_text("🛑 چت رو تموم کردی. خسته نباشی! ☕️")
    except Exception:
        pass

    # استخراج تگ‌های هر دو کاربر از دیتابیس
    caller = await crud.get_user_by_tg_id(db_session, tg_id)
    caller_tag = f"<code>{caller.public_id}</code>" if caller and caller.public_id else "ناشناس"

    partner_tag = "ناشناس"
    if partner_id:
        partner = await crud.get_user_by_tg_id(db_session, partner_id)
        if partner and partner.public_id:
            partner_tag = f"<code>{partner.public_id}</code>"

    # ارسال پیام پایان به فراخواننده (شخصی که دکمه خروج را زده)
    caller_text = SystemMsg.CHAT_ENDED_BY_YOU.format(user_tag=partner_tag, msg_token=match_history_id or "")
    try:
        await call.message.answer(caller_text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    except Exception as exc:
        logger.error("Failed to send main menu to caller %d: %s", tg_id, exc)

    # 🛡️ آخرین لایه محافظتی: اگر واقعاً هیچ نشانی از پارتنر نبود
    if not partner_id:
        logger.critical("User %d ended chat but partner_id is entirely lost. Partner might become a zombie.", tg_id)
        # 🌟 در صورت نبود پارتنر، فقط برای فراخواننده یادآوری می‌کنیم
        await crud.notify_missed_messages(db_session, tg_id)
        return

    # ── Partner: Redis + FSM + اطلاع‌رسانی ───────────────────────────────── #
    try:
        await redis_client.delete(f"user:state:{partner_id}")
        await redis_client.setex(f"user:{partner_id}:last_match_partner", 86400, str(tg_id))
    except Exception as exc:
        logger.error("Redis delete failed for partner %d: %s", partner_id, exc)

    partner_ctx: FSMContext = _resolve_partner_fsm(partner_id)

    try:
        await partner_ctx.set_state(None)
        await partner_ctx.clear()
    except Exception as exc:
        logger.warning("Failed to clear FSM for partner %d: %s", partner_id, exc)

    # ارسال پیام پایان به پارتنر
    partner_text = SystemMsg.CHAT_ENDED_BY_PARTNER.format(user_tag=caller_tag, msg_token=match_history_id or "")
    await _safe_send(
        partner_id,
        partner_text,
        with_main_menu=True,
        parse_mode="HTML"
    )

    # 🌟 بررسی و ارسال یادآوری پیام‌های خوانده‌نشده حین چت برای هر دو نفر
    await crud.notify_missed_messages(db_session, tg_id)
    if partner_id:
        await crud.notify_missed_messages(db_session, partner_id)



import asyncio
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
import re

@router.message(F.text.regexp(r"^/del_all_messages_(\d+)$"))
async def cmd_delete_match_history(message: Message, db_session: AsyncSession):
    # گرفتن شماره دیت از دستور ارسال شده
    match_id_str = re.match(r"^/del_all_messages_(\d+)$", message.text).group(1)
    match_id = int(match_id_str)
    tg_id = message.from_user.id
    
    # اطمینان از اینکه خود این شخص توی این دیت حضور داشته
    match_row = await db_session.get(MatchHistory, match_id)
    if not match_row or tg_id not in (match_row.user_one_id, match_row.user_two_id):
        return await message.answer("⚠️ تاریخچه این چت پیدا نشد یا شما در آن حضور نداشتید.")
        
    partner_id = match_row.user_two_id if match_row.user_one_id == tg_id else match_row.user_one_id
    
    # کلیدهای Redis
    my_msgs_key = f"match:{match_id}:msgs:{tg_id}"
    partner_msgs_key = f"match:{match_id}:msgs:{partner_id}"
    
    my_msgs = await redis_client.smembers(my_msgs_key)
    partner_msgs = await redis_client.smembers(partner_msgs_key)
    
    if not my_msgs and not partner_msgs:
        return await message.answer("⚠️ پیامی برای حذف یافت نشد! (ممکن است مهلت ۴۸ ساعته تمام شده باشد یا قبلاً پیام‌ها را پاک کرده‌اید)")

    await message.answer("⏳ در حال پاکسازی دوطرفه پیام‌ها... لطفاً صبور باشید.")
    
    # یک تابع کمکی برای پاک کردن امن پیام‌ها (بدون بلاک شدن توسط تلگرام)
    async def delete_messages_for_user(user_chat_id, msg_ids_set):
        deleted_count = 0
        for msg_id_bytes in msg_ids_set:
            # تبدیل بایت به عدد
            msg_id = int(msg_id_bytes.decode('utf-8') if isinstance(msg_id_bytes, bytes) else msg_id_bytes)
            try:
                await bot.delete_message(chat_id=user_chat_id, message_id=msg_id)
                deleted_count += 1
            except TelegramRetryAfter as e:
                # اگه تلگرام بخاطر سرعت زیاد ارور داد، صبر می‌کنیم
                await asyncio.sleep(e.retry_after)
                await bot.delete_message(chat_id=user_chat_id, message_id=msg_id)
                deleted_count += 1
            except TelegramBadRequest:
                pass # این یعنی پیام خیلی قدیمیه یا قبلاً دستی پاک شده
            except Exception as e:
                pass
            
            # توقف‌های میلی‌ثانیه‌ای برای دور زدن لیمیتِ تلگرام
            await asyncio.sleep(0.05)
        return deleted_count

    # پاک کردن پیام‌های خودش و پارتنرش
    await delete_messages_for_user(tg_id, my_msgs)
    await delete_messages_for_user(partner_id, partner_msgs)
    
    # حذف کلیدها از Redis برای جلوگیری از اسپم شدن ربات
    await redis_client.delete(my_msgs_key, partner_msgs_key)
    
    await message.answer("✅ تمام پیام‌های رد و بدل شده در این سشن به صورت دوطرفه پاک شدند.")
    
    # اطلاع‌رسانی به پارتنر
    try:
        await bot.send_message(
            partner_id, 
            "🗑 پارتنر قبلی شما درخواست داد تا پیام‌های چت اخیر پاک شوند. تمام پیام‌های فوروارد شده بین شما دو نفر حذف گردید."
        )
    except Exception:
        pass