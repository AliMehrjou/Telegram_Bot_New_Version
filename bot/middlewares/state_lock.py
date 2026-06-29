import logging
from typing import Any, Callable, Dict, Awaitable, Set, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery, Update
from aiogram.fsm.context import FSMContext

from matching_bot_project.bot.core.constants import ReplyBtn
from matching_bot_project.bot.states.states import (
    ChatStates,
    MatchingStates,
    QuestionnaireStates,
    ReportStates,
    VIPStates,
    ProfileEditStates,
    DiscoveryStates,
    CoinTransferStates,
    TransferCoinStates,
    PaymentStates,
    OnboardingStates,
    SupportStates,
    AdminStates,
    EventStates,
    PBroadcastStates,
    QuestionAddStates,
    ProfileCommentStates,
)

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Collect all main menu button texts (to block them)
# ----------------------------------------------------------------------
REPLY_BTN_VALUES: Set[str] = {
    getattr(ReplyBtn, attr)
    for attr in dir(ReplyBtn)
    if not attr.startswith("_") and isinstance(getattr(ReplyBtn, attr), str)
}

# Global allowed texts for all locked states (e.g., cancel buttons, commands)
GLOBAL_ALLOWED_TEXTS: Set[str] = {
    ReplyBtn.CANCEL,
    ReplyBtn.BACK_TO_MENU,
}

# Global allowed callback prefixes (safe actions that can be performed anywhere)
GLOBAL_ALLOWED_CALLBACK_PREFIXES: Set[str] = {
    "view_profile_",    # viewing any profile is safe and does not alter state
}


# ----------------------------------------------------------------------
# Define which states lock the menu and what is allowed in each
# ----------------------------------------------------------------------

# States that should block most menu interactions
LOCKED_STATES: Set[str] = {
    # Anonymous chat
    ChatStates.anonymous_chat_active.state,
    ChatStates.waiting_for_approval.state,
    ChatStates.typing_direct_message.state,

    # Matching queue
    MatchingStates.waiting_in_queue.state,

    # Questionnaire
    QuestionnaireStates.answering_questions.state,
    QuestionnaireStates.waiting_for_partner_answer.state,

    # Report flows
    ReportStates.waiting_for_report_description.state,
    ReportStates.waiting_for_evidence_before_reason.state,
    ReportStates.selecting_reason.state,

    # VIP filter
    VIPStates.waiting_for_age_filter.state,

    # Profile edit flows
    ProfileEditStates.editing_bio.state,
    ProfileEditStates.selecting_interests.state,
    ProfileEditStates.waiting_for_photo.state,
    ProfileEditStates.editing_name.state,
    ProfileEditStates.updating_age.state,
    ProfileEditStates.updating_province.state,
    ProfileEditStates.updating_city.state,
    ProfileEditStates.waiting_for_voice.state,
    ProfileEditStates.waiting_for_gps.state,

    # Discovery
    DiscoveryStates.choosing_province.state,
    DiscoveryStates.choosing_interests.state,
    DiscoveryStates.choosing_age_range.state,
    DiscoveryStates.showing_results.state,
    DiscoveryStates.navigating.state,

    # Coin transfers
    CoinTransferStates.waiting_for_amount.state,
    CoinTransferStates.confirming.state,
    TransferCoinStates.waiting_for_amount.state,

    # Payments
    PaymentStates.choosing_package.state,
    PaymentStates.choosing_method.state,
    PaymentStates.waiting_for_receipt_photo.state,

    # Admin flows (optional, but good to block menu)
    AdminStates.waiting_for_support_reply.state,
    AdminStates.waiting_for_broadcast_message.state,

    # Event creation
    EventStates.waiting_for_name.state,
    EventStates.waiting_for_description.state,
    EventStates.waiting_for_duration.state,
    EventStates.waiting_for_multiplier.state,
    EventStates.confirming.state,

    # Broadcast
    PBroadcastStates.waiting_for_filter.state,
    PBroadcastStates.waiting_for_message.state,
    PBroadcastStates.confirming.state,

    # Question add
    QuestionAddStates.choosing_type.state,
    QuestionAddStates.entering_text.state,
    QuestionAddStates.entering_option_a.state,
    QuestionAddStates.entering_option_b.state,
    QuestionAddStates.entering_option_c.state,
    QuestionAddStates.entering_option_d.state,
    QuestionAddStates.entering_category.state,
    QuestionAddStates.confirming.state,
    QuestionAddStates.waiting_for_excel.state,
    QuestionAddStates.confirming_bulk.state,

    # Profile comment
    ProfileCommentStates.waiting_for_comment_text.state,

    # Support
    SupportStates.waiting_for_support_message.state,
}


# Per‑state allowed actions
STATE_ALLOWED_ACTIONS: Dict[str, Dict[str, Any]] = {
    # ---------- Anonymous chat ----------
    ChatStates.anonymous_chat_active.state: {
        "allowed_texts": {
            ReplyBtn.PHASE_USER_PROFILE,
            ReplyBtn.END_CHAT,
            # END_DATE is not used in chat, but keep for completeness
            ReplyBtn.END_DATE,
        },
        "allowed_callback_prefixes": {
            "end_active_chat",
            "trigger_report_",
            "safety_reason_",
            "report_cancel",
        },
        "allow_other_texts": True,   # normal chat messages (text)
        "allow_media": True,         # photos, videos, etc.
    },

    ChatStates.waiting_for_approval.state: {
        "allowed_callback_prefixes": {"approve_chat_yes", "approve_chat_no"},
        "allow_other_texts": False,
    },

    ChatStates.typing_direct_message.state: {
        "allow_other_texts": True,   # user is typing a direct message
        "allow_media": False,        # only text is allowed for DM
    },

    # ---------- Matching queue ----------
    MatchingStates.waiting_in_queue.state: {
        "allow_other_texts": False,
    },

    # ---------- Questionnaire ----------
    QuestionnaireStates.answering_questions.state: {
        "allowed_callback_prefixes": {"option_a", "option_b", "option_c", "option_d"},
        "allow_other_texts": False,
    },
    QuestionnaireStates.waiting_for_partner_answer.state: {
        "allow_other_texts": False,
    },

    # ---------- Report flows ----------
    ReportStates.waiting_for_report_description.state: {
        "allow_other_texts": True,    # user can send text, photo, etc. as evidence
        "allow_media": True,
    },
    ReportStates.waiting_for_evidence_before_reason.state: {
        "allow_other_texts": True,    # user can forward a message
        "allow_media": False,         # only forward (message with forward_date)
    },
    ReportStates.selecting_reason.state: {
        "allowed_callback_prefixes": {"safety_reason_", "report_cancel"},
        "allow_other_texts": False,
    },

    # ---------- VIP age filter ----------
    VIPStates.waiting_for_age_filter.state: {
        "allowed_callback_prefixes": {"vip_age_filter_"},
        "allow_other_texts": False,
    },

    # ---------- Profile edit ----------
    # Most edit states only accept specific inputs (text or media) – we block menu buttons
    # but allow the actual input. Since we block all ReplyBtn texts, the user can still
    # send the required content (e.g., age number, city name, photo, voice).
    # We set allow_other_texts=True so that any non-menu text passes.
    ProfileEditStates.editing_bio.state: {"allow_other_texts": True},
    ProfileEditStates.selecting_interests.state: {"allow_other_texts": True},
    ProfileEditStates.waiting_for_photo.state: {"allow_media": True, "allow_other_texts": False},
    ProfileEditStates.editing_name.state: {"allow_other_texts": True},
    ProfileEditStates.updating_age.state: {"allow_other_texts": True},
    ProfileEditStates.updating_province.state: {"allow_other_texts": True},
    ProfileEditStates.updating_city.state: {"allow_other_texts": True},
    ProfileEditStates.waiting_for_voice.state: {"allow_media": True, "allow_other_texts": False},
    ProfileEditStates.waiting_for_gps.state: {"allow_media": True, "allow_other_texts": False},

    # ---------- Discovery ----------
    DiscoveryStates.choosing_province.state: {"allow_other_texts": True},
    DiscoveryStates.choosing_interests.state: {"allow_other_texts": True},
    DiscoveryStates.choosing_age_range.state: {"allow_other_texts": True},
    DiscoveryStates.showing_results.state: {
        "allowed_callback_prefixes": {"disc_", "view_profile_"},
        "allow_other_texts": False,
    },
    DiscoveryStates.navigating.state: {
        "allowed_callback_prefixes": {"disc_", "view_profile_"},
        "allow_other_texts": False,
    },

    # ---------- Coin transfers ----------
    CoinTransferStates.waiting_for_amount.state: {"allow_other_texts": True},
    CoinTransferStates.confirming.state: {
        "allowed_callback_prefixes": {"confirm_transfer", "cancel_transfer"},
        "allow_other_texts": False,
    },
    TransferCoinStates.waiting_for_amount.state: {"allow_other_texts": True},

    # ---------- Payments ----------
    PaymentStates.choosing_package.state: {
        "allowed_callback_prefixes": {"pay_package_"},
        "allow_other_texts": False,
    },
    PaymentStates.choosing_method.state: {
        "allowed_callback_prefixes": {"pay_method_"},
        "allow_other_texts": False,
    },
    PaymentStates.waiting_for_receipt_photo.state: {
        "allow_media": True,
        "allow_other_texts": True,  # user may send caption
    },

    # ---------- Admin flows ----------
    AdminStates.waiting_for_support_reply.state: {"allow_other_texts": True},
    AdminStates.waiting_for_broadcast_message.state: {"allow_other_texts": True},

    # ---------- Event creation ----------
    EventStates.waiting_for_name.state: {"allow_other_texts": True},
    EventStates.waiting_for_description.state: {"allow_other_texts": True},
    EventStates.waiting_for_duration.state: {"allow_other_texts": True},
    EventStates.waiting_for_multiplier.state: {"allow_other_texts": True},
    EventStates.confirming.state: {
        "allowed_callback_prefixes": {"confirm_event", "cancel_event"},
        "allow_other_texts": False,
    },

    # ---------- Broadcast ----------
    PBroadcastStates.waiting_for_filter.state: {"allow_other_texts": True},
    PBroadcastStates.waiting_for_message.state: {"allow_other_texts": True},
    PBroadcastStates.confirming.state: {
        "allowed_callback_prefixes": {"confirm_broadcast", "cancel_broadcast"},
        "allow_other_texts": False,
    },

    # ---------- Question add ----------
    QuestionAddStates.choosing_type.state: {"allow_other_texts": True},
    QuestionAddStates.entering_text.state: {"allow_other_texts": True},
    QuestionAddStates.entering_option_a.state: {"allow_other_texts": True},
    QuestionAddStates.entering_option_b.state: {"allow_other_texts": True},
    QuestionAddStates.entering_option_c.state: {"allow_other_texts": True},
    QuestionAddStates.entering_option_d.state: {"allow_other_texts": True},
    QuestionAddStates.entering_category.state: {"allow_other_texts": True},
    QuestionAddStates.confirming.state: {
        "allowed_callback_prefixes": {"confirm_question", "cancel_question"},
        "allow_other_texts": False,
    },
    QuestionAddStates.waiting_for_excel.state: {"allow_media": True, "allow_other_texts": False},
    QuestionAddStates.confirming_bulk.state: {
        "allowed_callback_prefixes": {"confirm_bulk", "cancel_bulk"},
        "allow_other_texts": False,
    },

    # ---------- Profile comment ----------
    ProfileCommentStates.waiting_for_comment_text.state: {"allow_other_texts": True},

    # ---------- Support ----------
    SupportStates.waiting_for_support_message.state: {"allow_other_texts": True},
}


# Default allowed actions for any locked state not explicitly listed
DEFAULT_ALLOWED_ACTIONS = {
    "allowed_texts": set(),
    "allowed_callback_prefixes": set(),
    "allow_other_texts": False,
    "allow_media": False,
}


class StateLockMiddleware(BaseMiddleware):
    """
    Prevents users in active sessions (chat, matching, questionnaire, etc.)
    from accidentally triggering main menu handlers that would corrupt their state.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Get current FSM state
        state: FSMContext = data.get("state")
        if not state:
            return await handler(event, data)

        current_state = await state.get_state()
        if current_state is None or current_state not in LOCKED_STATES:
            return await handler(event, data)

        # Resolve actual event (Message or CallbackQuery)
        user_event = event
        if isinstance(event, Update):
            if event.message:
                user_event = event.message
            elif event.callback_query:
                user_event = event.callback_query
            else:
                return await handler(event, data)

        # Determine allowed actions for this state
        actions = STATE_ALLOWED_ACTIONS.get(current_state, DEFAULT_ALLOWED_ACTIONS)
        allowed_texts = actions.get("allowed_texts", set()) | GLOBAL_ALLOWED_TEXTS
        allowed_callback_prefixes = actions.get("allowed_callback_prefixes", set()) | GLOBAL_ALLOWED_CALLBACK_PREFIXES
        allow_other_texts = actions.get("allow_other_texts", False)
        allow_media = actions.get("allow_media", False)

        # --------------------- Message handling ---------------------
        if isinstance(user_event, Message):
            # Commands are always allowed (e.g., /start)
            if user_event.text and user_event.text.startswith("/"):
                return await handler(event, data)

            # If it's a media message
            if not user_event.text:
                if allow_media:
                    return await handler(event, data)
                else:
                    await self._reply_blocked(user_event, "media")
                    return

            # Check if the text is an allowed exact match
            if user_event.text in allowed_texts:
                return await handler(event, data)

            # If it's a menu button (ReplyBtn), block it
            if user_event.text in REPLY_BTN_VALUES:
                await self._reply_blocked(user_event, "menu")
                return

            # Otherwise, allow only if the state permits arbitrary text
            if allow_other_texts:
                return await handler(event, data)
            else:
                await self._reply_blocked(user_event, "other")
                return

        # --------------------- CallbackQuery handling ---------------------
        elif isinstance(user_event, CallbackQuery):
            callback_data = user_event.data or ""

            # Check if data starts with any allowed prefix
            for prefix in allowed_callback_prefixes:
                if callback_data.startswith(prefix):
                    return await handler(event, data)

            # Block all other callbacks
            await self._block_callback(user_event)
            return

        # Fallback: allow other types (should not happen)
        return await handler(event, data)

    @staticmethod
    async def _reply_blocked(message: Message, reason: str) -> None:
        """Send a warning message to the user and stop propagation."""
        try:
            await message.reply(
                "⚠️ شما در حال حاضر داخل یک فرآیند فعال (چت، مچ‌یابی، پرسشنامه یا ویرایش) هستید.\n"
                "لطفاً ابتدا آن را تکمیل یا لغو کنید تا بتوانید از منوی اصلی استفاده کنید."
            )
        except Exception as e:
            logger.error(f"Failed to send block warning to user {message.from_user.id}: {e}")

    @staticmethod
    async def _block_callback(call: CallbackQuery) -> None:
        """Show an alert for blocked callback and stop propagation."""
        try:
            await call.answer(
                "⚠️ شما در حال حاضر داخل یک چت یا فرآیند فعال هستید.\n"
                "ابتدا آن را پایان دهید.",
                show_alert=True,
            )
        except Exception as e:
            logger.error(f"Failed to answer blocked callback for user {call.from_user.id}: {e}")