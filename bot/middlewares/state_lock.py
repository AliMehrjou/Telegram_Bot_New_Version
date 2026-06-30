import logging
import time
from typing import Any, Callable, Dict, Awaitable, Set, Optional

from aiogram import BaseMiddleware
from aiogram.types import (
    TelegramObject, Message, CallbackQuery, Update,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
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
    SupportStates,
    AdminStates,
    EventStates,
    PBroadcastStates,
    QuestionAddStates,
    ProfileCommentStates,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# دکمه‌های ریپلی کیبورد (منو اصلی)
# ─────────────────────────────────────────────────────────────────────────────
REPLY_BTN_VALUES: Set[str] = {
    getattr(ReplyBtn, attr)
    for attr in dir(ReplyBtn)
    if not attr.startswith("_") and isinstance(getattr(ReplyBtn, attr), str)
}

GLOBAL_ALLOWED_TEXTS: Set[str] = {
    ReplyBtn.CANCEL,
    ReplyBtn.BACK_TO_MENU,
}

GLOBAL_ALLOWED_CALLBACK_PREFIXES: Set[str] = {
    "view_profile_",
    "prof_page:",
    "ignore",
    "req_direct_",
    "block_user_",
    "unblock_user_",
    "report_user_",
    "add_friend_",
    "remove_friend_",
    "transfer_coin_",
    "view_comments:",
}

# ─────────────────────────────────────────────────────────────────────────────
# سقف زمانی برای حالت‌های قفل‌شده (auto-expire عمومی)
# ─────────────────────────────────────────────────────────────────────────────
# اگر کاربر بیش از این مدت در یک LOCKED_STATE بماند (مثلاً به‌خاطر کرش یک
# هندلر یا خطایی که state را پاک نکرده)، خودِ middleware آن را آزاد می‌کند.
# این جدا و مکمل auto-heal اختصاصی REAL_MATCH_STATES است: auto-heal فقط برای
# حالت‌های مچ/چت/کوییز با چک دیتابیس کار می‌کند، اما این TTL برای *همه‌ی*
# ۴۰+ حالت داخل LOCKED_STATES اعمال می‌شود (ادیت پروفایل، دیسکاوری، پرداخت و غیره)
# که هیچ معیار دیتابیسی برای auto-heal ندارند.
_LOCKED_STATE_TTL_SECONDS = 15 * 60  # ۱۵ دقیقه
_LOCK_ENTERED_AT_KEY = "_state_lock_entered_at"
_LOCK_STATE_NAME_KEY = "_state_lock_state_name"

# ─────────────────────────────────────────────────────────────────────────────
# استیت‌هایی که نشانه مچ/چت واقعی هستند — برای auto-heal
# ─────────────────────────────────────────────────────────────────────────────
REAL_MATCH_STATES: Set[str] = {
    ChatStates.anonymous_chat_active.state,
    ChatStates.waiting_for_approval.state,
    MatchingStates.waiting_in_queue.state,
    QuestionnaireStates.answering_questions.state,
    QuestionnaireStates.waiting_for_partner_answer.state,
    QuestionnaireStates.waiting_for_questions_to_start.state,
}

# ─────────────────────────────────────────────────────────────────────────────
# استیت‌های قفل‌شده
# ─────────────────────────────────────────────────────────────────────────────
LOCKED_STATES: Set[str] = {
    ChatStates.anonymous_chat_active.state,
    ChatStates.waiting_for_approval.state,
    ChatStates.typing_direct_message.state,
    MatchingStates.waiting_in_queue.state,
    QuestionnaireStates.answering_questions.state,
    QuestionnaireStates.waiting_for_partner_answer.state,
    ReportStates.waiting_for_report_description.state,
    ReportStates.waiting_for_evidence_before_reason.state,
    ReportStates.selecting_reason.state,
    VIPStates.waiting_for_age_filter.state,
    ProfileEditStates.editing_bio.state,
    ProfileEditStates.selecting_interests.state,
    ProfileEditStates.waiting_for_photo.state,
    ProfileEditStates.editing_name.state,
    ProfileEditStates.updating_age.state,
    ProfileEditStates.updating_province.state,
    ProfileEditStates.updating_city.state,
    ProfileEditStates.waiting_for_voice.state,
    ProfileEditStates.waiting_for_gps.state,
    DiscoveryStates.choosing_province.state,
    DiscoveryStates.choosing_interests.state,
    DiscoveryStates.choosing_age_range.state,
    DiscoveryStates.showing_results.state,
    DiscoveryStates.navigating.state,
    CoinTransferStates.waiting_for_amount.state,
    CoinTransferStates.confirming.state,
    TransferCoinStates.waiting_for_amount.state,
    PaymentStates.choosing_package.state,
    PaymentStates.choosing_method.state,
    PaymentStates.waiting_for_receipt_photo.state,
    AdminStates.waiting_for_support_reply.state,
    AdminStates.waiting_for_broadcast_message.state,
    EventStates.waiting_for_name.state,
    EventStates.waiting_for_description.state,
    EventStates.waiting_for_duration.state,
    EventStates.waiting_for_multiplier.state,
    EventStates.confirming.state,
    PBroadcastStates.waiting_for_filter.state,
    PBroadcastStates.waiting_for_message.state,
    PBroadcastStates.confirming.state,
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
    ProfileCommentStates.waiting_for_comment_text.state,
    SupportStates.waiting_for_support_message.state,
}

# ─────────────────────────────────────────────────────────────────────────────
# اکشن‌های مجاز به ازای هر استیت
# ─────────────────────────────────────────────────────────────────────────────
STATE_ALLOWED_ACTIONS: Dict[str, Dict[str, Any]] = {
    ChatStates.anonymous_chat_active.state: {
        "allowed_texts": {ReplyBtn.PHASE_USER_PROFILE, ReplyBtn.END_CHAT, ReplyBtn.END_DATE},
        "allowed_callback_prefixes": {
            "end_active_chat", "confirm_end_chat", "cancel_end_chat",
            "confirm_end_date", "cancel_end_date",
            "trigger_report_", "safety_reason_", "report_cancel",
        },
        "allow_other_texts": True,
        "allow_media": True,
    },
    ChatStates.waiting_for_approval.state: {
        "allowed_callback_prefixes": {"approve_chat_yes", "approve_chat_no"},
        "allow_other_texts": False,
    },
    ChatStates.typing_direct_message.state: {
        "allow_other_texts": True,
        "allow_media": False,
    },
    MatchingStates.waiting_in_queue.state: {"allow_other_texts": False},
    QuestionnaireStates.answering_questions.state: {
        "allowed_callback_prefixes": {"ans_"},
        "allow_other_texts": False,
    },
    QuestionnaireStates.waiting_for_partner_answer.state: {
        "allowed_callback_prefixes": {"ans_"},
        "allow_other_texts": False,
    },
    ReportStates.waiting_for_report_description.state: {
        "allowed_callback_prefixes": {"report_cancel"},
        "allow_other_texts": True,
        "allow_media": True,
    },
    ReportStates.waiting_for_evidence_before_reason.state: {
        "allowed_callback_prefixes": {"report_cancel"},
        "allow_other_texts": True,
        "allow_media": False,
    },
    ReportStates.selecting_reason.state: {
        "allowed_callback_prefixes": {"safety_reason_", "report_cancel"},
        "allow_other_texts": False,
    },
    VIPStates.waiting_for_age_filter.state: {
        "allowed_texts": {ReplyBtn.BACK_TO_MENU},
        "allowed_callback_prefixes": {"vip_age_"},
        "allow_other_texts": False,
    },
    ProfileEditStates.editing_bio.state: {"allow_other_texts": True},
    ProfileEditStates.selecting_interests.state: {
        "allowed_callback_prefixes": {"interest_", "save_interests"},
        "allow_other_texts": True,
    },
    ProfileEditStates.waiting_for_photo.state: {"allow_media": True, "allow_other_texts": False},
    ProfileEditStates.editing_name.state: {"allow_other_texts": True},
    ProfileEditStates.updating_age.state: {"allow_other_texts": True},
    ProfileEditStates.updating_province.state: {"allow_other_texts": True},
    ProfileEditStates.updating_city.state: {"allow_other_texts": True},
    ProfileEditStates.waiting_for_voice.state: {"allow_media": True, "allow_other_texts": False},
    ProfileEditStates.waiting_for_gps.state: {"allow_media": True, "allow_other_texts": False},
    DiscoveryStates.choosing_province.state: {
        "allowed_callback_prefixes": {"disc_cancel"},
        "allow_other_texts": True,
    },
    DiscoveryStates.choosing_interests.state: {
        "allowed_callback_prefixes": {"disc_cancel", "disc_int_"},
        "allow_other_texts": True,
    },
DiscoveryStates.choosing_age_range.state: {
        "allowed_callback_prefixes": {"disc_cancel", "disc_age_"},
        "allow_other_texts": True,
    },
    DiscoveryStates.showing_results.state: {
        "allowed_texts": {ReplyBtn.MY_PROFILE},  # 👈 اضافه شد تا دکمه ریپلی پروفایل من قفل نشود
        "allowed_callback_prefixes": {"disc_", "view_profile_", "like_", "pass_"},
        "allow_other_texts": False,
    },
    DiscoveryStates.navigating.state: {
        "allowed_texts": {ReplyBtn.MY_PROFILE},  # 👈 اضافه شد
        "allowed_callback_prefixes": {"disc_", "view_profile_", "like_", "pass_"},
        "allow_other_texts": False,
    },
    
    CoinTransferStates.waiting_for_amount.state: {"allow_other_texts": True},
    CoinTransferStates.confirming.state: {
        "allowed_callback_prefixes": {"transfer_confirm_", "transfer_cancel"},
        "allow_other_texts": False,
    },
    TransferCoinStates.waiting_for_amount.state: {"allow_other_texts": True},
    PaymentStates.choosing_package.state: {
        "allowed_callback_prefixes": {"buy_package_", "cancel_payment"},
        "allow_other_texts": False,
    },
    PaymentStates.choosing_method.state: {
        "allowed_callback_prefixes": {"pay_method_", "cancel_payment"},
        "allow_other_texts": False,
    },
    PaymentStates.waiting_for_receipt_photo.state: {
        "allowed_callback_prefixes": {"cancel_payment"},
        "allow_media": True,
        "allow_other_texts": True,
    },
    AdminStates.waiting_for_support_reply.state: {
        "allowed_callback_prefixes": {"cancel_admin_reply"},
        "allow_other_texts": True,
        "allow_media": True,
    },
    AdminStates.waiting_for_broadcast_message.state: {
        "allowed_callback_prefixes": {"cancel_broadcast"},
        "allow_other_texts": True,
        "allow_media": True,
    },
    EventStates.waiting_for_name.state: {
        "allowed_callback_prefixes": {"cancel_event"},
        "allow_other_texts": True,
    },
    EventStates.waiting_for_description.state: {
        "allowed_callback_prefixes": {"cancel_event"},
        "allow_other_texts": True,
    },
    EventStates.waiting_for_duration.state: {
        "allowed_callback_prefixes": {"cancel_event"},
        "allow_other_texts": True,
    },
    EventStates.waiting_for_multiplier.state: {
        "allowed_callback_prefixes": {"cancel_event"},
        "allow_other_texts": True,
    },
    EventStates.confirming.state: {
        "allowed_callback_prefixes": {"event_confirm", "cancel_event"},
        "allow_other_texts": False,
    },
    PBroadcastStates.waiting_for_filter.state: {
        "allowed_callback_prefixes": {"cancel_pbroadcast"},
        "allow_other_texts": True,
    },
    PBroadcastStates.waiting_for_message.state: {
        "allowed_callback_prefixes": {"cancel_pbroadcast"},
        "allow_other_texts": True,
        "allow_media": True,
    },
    PBroadcastStates.confirming.state: {
        "allowed_callback_prefixes": {"pbroadcast_confirm", "cancel_pbroadcast"},
        "allow_other_texts": False,
    },
QuestionAddStates.choosing_type.state: {
        "allowed_callback_prefixes": {"qtype:"},
        "allow_other_texts": True
    },
    QuestionAddStates.entering_text.state: {"allow_other_texts": True},
    QuestionAddStates.entering_option_a.state: {"allow_other_texts": True},
    QuestionAddStates.entering_option_b.state: {"allow_other_texts": True},
    QuestionAddStates.entering_option_c.state: {"allow_other_texts": True},
    QuestionAddStates.entering_option_d.state: {"allow_other_texts": True},
    QuestionAddStates.entering_category.state: {"allow_other_texts": True},
    QuestionAddStates.confirming.state: {
        "allowed_callback_prefixes": {"qconfirm:"},
        "allow_other_texts": False,
    },
QuestionAddStates.waiting_for_excel.state: {"allow_media": True, "allow_other_texts": False},
    QuestionAddStates.confirming_bulk.state: {
        "allowed_callback_prefixes": {"qbulk:"},
        "allow_other_texts": False,
    },
    ProfileCommentStates.waiting_for_comment_text.state: {"allow_other_texts": True},
    SupportStates.waiting_for_support_message.state: {"allow_other_texts": True},
}


# ─────────────────────────────────────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────────────────────────────────────
class StateLockMiddleware(BaseMiddleware):
    """
    قفل منوی اصلی در زمان فرآیندهای فعال.

    ترتیب بررسی:
      ۱. force_exit_state → مستقیماً اینجا اجرا می‌شود (بدون router/handler)
      ۲. auto-heal ghost state → اگر DB مچ ندارد، state پاک می‌شود
      ۳. بررسی مجاز بودن پیام/callback
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        state: FSMContext = data.get("state")
        if not state:
            return await handler(event, data)

        current_state = await state.get_state()

        # ── unwrap Update ────────────────────────────────────────────────────
        user_event = event
        if isinstance(event, Update):
            if event.message:
                user_event = event.message
            elif event.callback_query:
                user_event = event.callback_query
            else:
                return await handler(event, data)

        # ── ۱. force_exit_state: اول از همه، قبل از هر چیز ─────────────────
        # اینجا handle می‌شود تا به هیچ router یا register جداگانه‌ای وابسته نباشد.
        if isinstance(user_event, CallbackQuery) and user_event.data == "force_exit_state":
            await self._handle_force_exit(user_event, state)
            return  # بدون صدا زدن handler — ما خودمان جواب دادیم

        # اگر state قفل نیست، عبور کن
        if current_state is None or current_state not in LOCKED_STATES:
            return await handler(event, data)

        # ── شناسایی tg_id ────────────────────────────────────────────────────
        tg_id: Optional[int] = None
        if isinstance(user_event, Message) and user_event.from_user:
            tg_id = user_event.from_user.id
        elif isinstance(user_event, CallbackQuery) and user_event.from_user:
            tg_id = user_event.from_user.id

        # ── ۲أ. سقف زمانی عمومی برای همه‌ی LOCKED_STATES ───────────────────────
        # این مستقل از auto-heal دیتابیسی است و برای حالت‌هایی که هیچ معیار
        # دیتابیسی ندارند (ادیت پروفایل، دیسکاوری، پرداخت و ...) هم کار می‌کند.
        # تایمر هر بار که کاربر به state قفل‌شده‌ی متفاوتی منتقل شود (یعنی در
        # حال پیشرفت در یک فرآیند چندمرحله‌ای است، نه گیر کرده) ریست می‌شود.
        fsm_data = await state.get_data()
        entered_at = fsm_data.get(_LOCK_ENTERED_AT_KEY)
        tracked_state = fsm_data.get(_LOCK_STATE_NAME_KEY)
        now_ts = time.time()

        if entered_at is None or tracked_state != current_state:
            # اولین بار در این state خاص دیده می‌شود — تایمر را (ری)ست کن
            await state.update_data({
                _LOCK_ENTERED_AT_KEY: now_ts,
                _LOCK_STATE_NAME_KEY: current_state,
            })
        elif (now_ts - entered_at) > _LOCKED_STATE_TTL_SECONDS:
            logger.warning(
                "State '%s' for user %s exceeded TTL (%ds) — auto-expiring.",
                current_state, tg_id, _LOCKED_STATE_TTL_SECONDS,
            )
            if tg_id is not None:
                try:
                    from matching_bot_project.bot.core.loader import matching_engine, redis_client
                    await matching_engine.remove_from_queue(tg_id)
                    await redis_client.delete(f"user:state:{tg_id}")
                except Exception as e:
                    logger.warning("TTL auto-expire cleanup failed for user %s: %s", tg_id, e)
            await state.set_state(None)
            await state.clear()
            return await handler(event, data)

        # ── ۲ب. auto-heal ghost state (فقط برای حالت‌های مچ/چت/کوییز) ─────────
        if tg_id is not None and current_state in REAL_MATCH_STATES:
            healed = await self._try_auto_heal(current_state, state, tg_id, data.get("db_session"))
            if healed:
                return await handler(event, data)

        # ── ۳. بررسی مجاز بودن ──────────────────────────────────────────────
        actions = STATE_ALLOWED_ACTIONS.get(current_state, {})
        allowed_texts: Set[str] = actions.get("allowed_texts", set()) | GLOBAL_ALLOWED_TEXTS
        allowed_callback_prefixes: Set[str] = (
            actions.get("allowed_callback_prefixes", set()) | GLOBAL_ALLOWED_CALLBACK_PREFIXES
        )
        allow_other_texts: bool = actions.get("allow_other_texts", False)
        allow_media: bool = actions.get("allow_media", False)

        async def _allow():
            # هر بار که کاربر یک اکشن مجاز انجام می‌دهد (نه فقط هنگام ورود
            # به state)، تایمر TTL را ریست کن. این یعنی سقف زمانی بر اساس
            # «آخرین فعالیت معتبر» است، نه صرفاً «اولین ورود» — کاربری که
            # فعالانه در حال پیشروی در یک ویزارد چندمرحله‌ای است هیچ‌وقت
            # وسط کار قطع نمی‌شود.
            await state.update_data({_LOCK_ENTERED_AT_KEY: time.time()})
            return await handler(event, data)

        if isinstance(user_event, Message):
            if user_event.text and user_event.text.startswith("/"):
                return await handler(event, data)
            if not user_event.text:
                if allow_media:
                    return await _allow()
                await self._reply_blocked(user_event)
                return
            if user_event.text in allowed_texts:
                return await _allow()
            if user_event.text in REPLY_BTN_VALUES:
                await self._reply_blocked(user_event)
                return
            if allow_other_texts:
                return await _allow()
            await self._reply_blocked(user_event)
            return

        elif isinstance(user_event, CallbackQuery):
            cb = user_event.data or ""
            for prefix in allowed_callback_prefixes:
                if cb.startswith(prefix):
                    return await _allow()
            await self._block_callback(user_event)
            return

        return await handler(event, data)

    # ─────────────────────────────────────────────────────────────────────────
    # force_exit: مستقیم داخل middleware اجرا می‌شود
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def _handle_force_exit(call: CallbackQuery, state: FSMContext) -> None:
        from matching_bot_project.bot.core.loader import matching_engine, redis_client
        from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard

        tg_id = call.from_user.id
        current = await state.get_state()
        logger.info("force_exit_state: user=%s was in state='%s'", tg_id, current)

        try:
            await matching_engine.remove_from_queue(tg_id)
        except Exception as e:
            logger.warning("force_exit remove_from_queue failed for user %s: %s", tg_id, e)

        try:
            await redis_client.delete(f"user:state:{tg_id}")
        except Exception as e:
            logger.warning("force_exit redis delete failed for user %s: %s", tg_id, e)

        await state.set_state(None)
        await state.clear()

        try:
            await call.answer("✅ وضعیت شما ریست شد.", show_alert=False)
        except Exception:
            pass

        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await call.message.answer(
            "✅ از فرآیند قبلی خارج شدید.\nمی‌توانید از منوی اصلی ادامه دهید 👇",
            reply_markup=get_main_menu_keyboard(),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # auto-heal ghost state
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def _try_auto_heal(
        current_state: str,
        state: FSMContext,
        tg_id: int,
        db_session: Optional[Any],
    ) -> bool:
        if db_session is None:
            return False
        try:
            from matching_bot_project.database.queries import crud
            from matching_bot_project.bot.core.loader import matching_engine, redis_client

            active_match = await crud.get_active_match(db_session, tg_id)
            if active_match:
                return False

            logger.warning("Ghost state '%s' for user %s — auto-healing.", current_state, tg_id)
            try:
                await matching_engine.remove_from_queue(tg_id)
            except Exception:
                pass
            try:
                await redis_client.delete(f"user:state:{tg_id}")
            except Exception:
                pass
            await state.set_state(None)
            await state.clear()
            logger.info("Auto-heal done for user %s.", tg_id)
            return True
        except Exception as e:
            logger.error("Auto-heal error for user %s: %s", tg_id, e)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # پیام‌های block
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _make_force_exit_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚪 خروج اجباری از فرآیند", callback_data="force_exit_state")
        ]])

    @staticmethod
    async def _reply_blocked(message: Message) -> None:
        try:
            await message.reply(
                "⚠️ شما در حال حاضر داخل یک فرآیند فعال هستید.\n"
                "لطفاً ابتدا آن را تکمیل یا لغو کنید.\n\n"
                "اگر گیر کرده‌اید، دکمه زیر را بزنید 👇",
                reply_markup=StateLockMiddleware._make_force_exit_keyboard(),
            )
        except Exception as e:
            logger.error("_reply_blocked failed: %s", e)

    @staticmethod
    async def _block_callback(call: CallbackQuery) -> None:
        try:
            await call.answer(
                "⚠️ شما در حال حاضر داخل یک فرآیند فعال هستید.\n"
                "ابتدا آن را پایان دهید یا از دکمه «خروج اجباری» استفاده کنید.",
                show_alert=True,
            )
        except Exception as e:
            logger.error("_block_callback failed: %s", e)