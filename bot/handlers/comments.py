"""
handlers/comments.py
────────────────────
سیستم کامنت پروفایل (Security-Hardened):
  - هر کاربر یه کامنت روی هر پروفایل (ویرایش‌پذیر)
  - صاحب پروفایل می‌تونه هر کامنتی رو پاک کنه
  - نویسنده می‌تونه کامنت خودش رو پاک کنه
  - صاحب پروفایل می‌تونه نویسنده‌ی هر کامنتی رو مستقیماً بلاک کنه
  - کاربری که توسط صاحب پروفایل بلاک شده، نمی‌تونه کامنت بگذاره
  - 🛡️ NEW: کاربر در حال چت/دیت ناشناس نمی‌تونه کامنت بگذاره
  - 🛡️ NEW: بلاک دوطرفه چک می‌شه (هر دو جهت)
  - 🛡️ NEW: استیت FSM به‌صورت امن ذخیره/بازیابی می‌شه (preserve/restore)
  - صاحب پروفایل می‌تونه کلاً امکان کامنت‌گذاری رو ببندد/باز کند
  - وقتی کامنت جدیدی ثبت می‌شه، صاحب پروفایل نوتیف می‌گیرد
  - pagination با ۳ کامنت در صفحه

نقاط ورود:
  callback_data="view_comments:{target_tg_id}:0"                    ← نمایش کامنت‌ها
  callback_data="add_comment:{target_tg_id}"                        ← شروع نوشتن کامنت
  callback_data="block_from_comment:{author_id}:{target_id}:{page}" ← بلاک نویسنده
  callback_data="toggle_comments:{target_tg_id}:{page}"             ← باز/بسته کردن کامنت‌گذاری
"""

import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.queries import crud
from matching_bot_project.bot.states.states import ProfileCommentStates, ChatStates
from matching_bot_project.bot.core.loader import bot, redis_client
from datetime import datetime, timezone
import pytz
import jdatetime

logger = logging.getLogger(__name__)
router = Router(name="comments_handler")

_PER_PAGE = 3

# ════════════════════════════════════════════════════════════════════════════
# Constants — FSM metadata keys
# ════════════════════════════════════════════════════════════════════════════

_CHAT_METADATA_KEYS = (
    "partner_id",
    "match_history_id",
    "partner_tg_id",
    "match_type",
    "chat_started_at",
    "chat_role",
)

_COMMENT_TEMP_KEYS = frozenset({
    "target_tg_id",
    "__comment_prev_state__",
    "__comment_chat_meta_snapshot__",
})


# ════════════════════════════════════════════════════════════════════════════
# Active-Session Detection
# ════════════════════════════════════════════════════════════════════════════

def to_persian_num(text: str) -> str:
    """تبدیل اعداد انگلیسی به فارسی"""
    trans = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
    return str(text).translate(trans)

def format_jalali(dt: datetime) -> str:
    """تبدیل تاریخ UTC دیتابیس به تاریخ و ساعت شمسی"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tehran_tz = pytz.timezone('Asia/Tehran')
    local_time = dt.astimezone(tehran_tz)
    jalali_date = jdatetime.datetime.fromgregorian(datetime=local_time)
    return to_persian_num(jalali_date.strftime('%Y/%m/%d - %H:%M'))

async def _is_in_active_session(state: FSMContext, db_session: AsyncSession, tg_id: int) -> bool:
    """بررسی مطمئن اینکه آیا کاربر در سشن فعال است یا خیر، همراه با خودترمیمی زامبی استیت‌ها"""
    
    # ۱. بررسی قطعی از روی دیتابیس (تگ یگانه حقیقت / Single Source of Truth)
    active_match = await crud.get_active_match(db_session, tg_id)
    if active_match:
        return True
        
    # ۲. اگر دیتابیس می‌گوید مچ فعالی وجود ندارد، هرچیزی که در FSM یا Redis باشد "زامبی" است!
    current_state = await state.get_state()
    is_zombie_state = current_state and any(phase in current_state.lower() for phase in ["chat", "matching", "questionnaire"])
    
    zombie_redis = False
    try:
        status = await redis_client.hget(f"user:state:{tg_id}", "status")
        if status is not None:
            status_str = status.decode() if isinstance(status, bytes) else status
            if status_str in ("chatting", "dating", "in_chat", "in_date"):
                zombie_redis = True
    except Exception:
        pass

    # ۳. نابود کردن زامبی‌ها در صورت وجود
    if is_zombie_state or zombie_redis:
        await state.set_state(None)
        await state.clear()
        try:
            await redis_client.delete(f"user:state:{tg_id}")
        except Exception:
            pass

    return False

# ════════════════════════════════════════════════════════════════════════════
# FSM State Preservation & Restoration (mirrors transfer.py pattern)
# ════════════════════════════════════════════════════════════════════════════

async def _preserve_current_state(state: FSMContext) -> None:
    """
    Snapshot current FSM state and chat metadata before entering the
    comment-writing flow.  The snapshot is stored inside FSM data under
    double-underscore keys.
    """
    current_state = await state.get_state()
    data = await state.get_data()
    chat_meta_snapshot = {
        k: data[k] for k in _CHAT_METADATA_KEYS if k in data
    }
    await state.update_data(
        __comment_prev_state__=current_state,
        __comment_chat_meta_snapshot__=chat_meta_snapshot,
    )

async def _restore_previous_state(state: FSMContext, db_session: AsyncSession, tg_id: int) -> str | None:
    """بازگردانی هوشمند و ضد زامبی FSM در کامنت‌ها"""
    data = await state.get_data()
    previous_state = data.pop("__comment_prev_state__", None)
    chat_meta_snapshot = data.pop("__comment_chat_meta_snapshot__", {})

    for key in _COMMENT_TEMP_KEYS:
        data.pop(key, None)

    for key, value in chat_meta_snapshot.items():
        if data.get(key) is None and value is not None:
            data[key] = value

    # 🛡️ سیستم خود ترمیمی
    active_match = await crud.get_active_match(db_session, tg_id)
    is_pipeline = previous_state and any(p in previous_state.lower() for p in ["chat", "matching", "questionnaire"])
    
    if is_pipeline and not active_match:
        await state.clear()
        return None

    if previous_state == ProfileCommentStates.waiting_for_comment_text.state:
        previous_state = None

    await state.set_data(data)
    
    if previous_state:
        await state.set_state(previous_state)
    else:
        if active_match:
            from matching_bot_project.bot.states.states import QuestionnaireStates, ChatStates
            if not active_match.questionnaire_completed:
                await state.set_state(QuestionnaireStates.answering_questions)
                previous_state = QuestionnaireStates.answering_questions.state
            else:
                await state.set_state(ChatStates.anonymous_chat_active)
                previous_state = ChatStates.anonymous_chat_active.state
        else:
            await state.clear()

    return previous_state

# ════════════════════════════════════════════════════════════════════════════
# Helper: display text
# ════════════════════════════════════════════════════════════════════════════
def _build_comments_text(comments, total: int = 0) -> str:
    header = '<tg-emoji emoji-id="5465300082628763143">💬</tg-emoji> <b>نظرات پروفایل</b>'

    if not comments:
        return (
            f"{header}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            '<tg-emoji emoji-id="5352896944496728039">📭</tg-emoji> '
            "<i>هنوز هیچ کامنتی ثبت نشده.</i>"
        )

    lines = [
        header,
        f"✦ <i>{to_persian_num(total)} نظر ثبت‌شده</i> ✦\n━━━━━━━━━━━━━━━━━━━━",
    ]
    for c in comments:
        author_label = html.escape(
            c.author.public_id if c.author and c.author.public_id else "کاربر"
        )
        
        is_edited = c.updated_at.replace(microsecond=0) > c.created_at.replace(microsecond=0)
        edited_text = ' <tg-emoji emoji-id="5470060791883374114">✍️</tg-emoji><i>(ویرایش‌شده)</i>' if is_edited else ""
        date_str = format_jalali(c.created_at)

        lines.append(f'<tg-emoji emoji-id="5373012449597335010">👤</tg-emoji> <b>{author_label}</b>{edited_text}')
        lines.append(f'<tg-emoji emoji-id="5370999492914976897">🕒</tg-emoji> <code>{date_str}</code>')
        lines.append(f"<blockquote>{c.text}</blockquote>")
        lines.append("─────────────────────")
        
    return "\n".join(lines)

async def _notify_new_comment(
    target_tg_id: int, author_label: str, comment_text: str, is_edit: bool
) -> None:
    action_text = "کامنت خودش رو ویرایش کرد" if is_edit else "یک کامنت جدید برات گذاشت"
    text = (
        f'<tg-emoji emoji-id="5465300082628763143">💬</tg-emoji> '
        f"<b>{html.escape(author_label)}</b> {action_text}:\n\n"
        f"<blockquote>{comment_text}</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="💬 مشاهده کامنت‌ها",
            callback_data=f"view_comments:{target_tg_id}:0",
        )
    ]])
    try:
        await bot.send_message(
            chat_id=target_tg_id, text=text, parse_mode="HTML", reply_markup=kb
        )
    except Exception as e:
        logger.info("Could not notify user %s about new comment: %s", target_tg_id, e)

# ════════════════════════════════════════════════════════════════════════════
# Keyboard
# ════════════════════════════════════════════════════════════════════════════

def _comments_keyboard(
    comments,
    target_tg_id: int,
    page: int,
    total: int,
    viewer_tg_id: int,
    is_own_profile: bool,
    comments_disabled: bool = False,
) -> InlineKeyboardMarkup:
    rows = []

    for c in comments:
        can_delete = is_own_profile or (c.author_tg_id == viewer_tg_id)
        is_others_comment = c.author_tg_id != viewer_tg_id

        row_1 = []
        if can_delete:
            row_1.append(InlineKeyboardButton(
                text=f"🗑 حذف #{c.id}",
                callback_data=f"del_comment:{c.id}:{target_tg_id}:{page}",
            ))
        if is_own_profile and is_others_comment:
            row_1.append(InlineKeyboardButton(
                text=f"🚫 بلاک #{c.id}",
                callback_data=f"block_from_comment:{c.author_tg_id}:{target_tg_id}:{page}",
            ))
        if row_1:
            rows.append(row_1)

        if is_own_profile and is_others_comment:
            rows.append([
                InlineKeyboardButton(
                    text=f"👤 پروفایل #{c.id}",
                    callback_data=f"view_profile_{c.author_tg_id}",
                ),
                InlineKeyboardButton(
                    text=f"🚩 گزارش #{c.id}",
                    callback_data=f"report_user_{c.author_tg_id}",
                ),
            ])

# Navigation
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️ قبلی",
            callback_data=f"view_comments:{target_tg_id}:{page - 1}",
        ))

    total_pages = max(1, -(-total // _PER_PAGE))
    nav.append(InlineKeyboardButton(
        text=f"📄 {to_persian_num(page + 1)} از {to_persian_num(total_pages)}",
        callback_data="noop",
    ))

    if (page + 1) * _PER_PAGE < total:
        nav.append(InlineKeyboardButton(
            text="بعدی ▶️",
            callback_data=f"view_comments:{target_tg_id}:{page + 1}",
        ))

    if nav:
        rows.append(nav)

    if is_own_profile:
        toggle_text = "🔓 باز کردن کامنت‌گذاری" if comments_disabled else "🔒 بستن کامنت‌گذاری"
        rows.append([InlineKeyboardButton(
            text=toggle_text,
            callback_data=f"toggle_comments:{target_tg_id}:{page}",
        )])
    else:
        if not comments_disabled:
            rows.append([InlineKeyboardButton(
                text="✏️ ثبت / ویرایش نظر من",
                callback_data=f"add_comment:{target_tg_id}",
            )])

    rows.append([InlineKeyboardButton(text="🔙 بستن بخش نظرات", callback_data="close_comments")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ════════════════════════════════════════════════════════════════════════════
# Display comments
# ════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("view_comments:"))
async def show_comments(call: CallbackQuery, db_session: AsyncSession):
    parts = call.data.split(":")
    try:
        target_tg_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        return await call.answer("⚠️ درخواست نامعتبر.", show_alert=True)

    viewer_tg_id = call.from_user.id
    is_own_profile = (viewer_tg_id == target_tg_id)

    comments, total = await crud.get_profile_comments(db_session, target_tg_id, page)
    comments_disabled = await crud.are_comments_disabled(db_session, target_tg_id)

    text = _build_comments_text(comments, total)
    if comments_disabled and not is_own_profile:
        text += (
            '\n\n<tg-emoji emoji-id="5472308992514464048">🔐</tg-emoji> '
            "<i>این کاربر کامنت‌گذاری روی پروفایلش را بسته است.</i>"
        )

    kb = _comments_keyboard(
        comments=comments,
        target_tg_id=target_tg_id,
        page=page,
        total=total,
        viewer_tg_id=viewer_tg_id,
        is_own_profile=is_own_profile,
        comments_disabled=comments_disabled,
    )

    # 🌟 فیکس باگ UX: تشخیص اینکه پیام فعلی پروفایل است یا منوی کامنت‌ها
    # اگر پیام فعلی شامل کلمه "نظرات پروفایل" باشد یعنی در حال صفحه‌بندی هستیم
    is_already_in_comments = call.message.text and "نظرات پروفایل" in call.message.text

    if is_already_in_comments:
        # در حال ورق زدن کامنت‌ها هستیم -> پیام را ویرایش کن
        try:
            await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    else:
        # بار اولی است که روی دکمه کلیک شده -> کامنت‌ها را در پیام جدیدی بفرست
        await call.message.answer(text, reply_markup=kb, parse_mode="HTML")

    await call.answer()


# ════════════════════════════════════════════════════════════════════════════
# Start writing / editing a comment  —  GUARDED ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("add_comment:"))
async def start_add_comment(
    call: CallbackQuery, state: FSMContext, db_session: AsyncSession
):
    try:
        target_tg_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        return await call.answer("⚠️ درخواست نامعتبر.", show_alert=True)
    author_tg_id = call.from_user.id

    # ── Guard 1: Can't comment on own profile ─────────────────────────
    if author_tg_id == target_tg_id:
        await call.answer(
            "⚠️ نمی‌توانید روی پروفایل خودتان کامنت بگذارید.",
            show_alert=True,
        )
        return

    # ── Guard 2: Active anonymous chat/date — BLOCK ───────────────────
    # Users in an active anonymous session must not post profile comments
    # to avoid moderation bypass, spam, and context-switch confusion.
    if await _is_in_active_session(state, db_session, author_tg_id):
        await call.answer(
            "🚫 در حین چت یا دیت ناشناس نمی‌توانید کامنت بگذارید. "
            "ابتدا از چت خارج شوید.",
            show_alert=True,
        )
        return

    # ── Guard 3: Comments disabled by profile owner ───────────────────
    if await crud.are_comments_disabled(db_session, target_tg_id):
        await call.answer(
            "🔒 این کاربر کامنت‌گذاری روی پروفایلش را بسته است.",
            show_alert=True,
        )
        return

    # ── Guard 4: Block list — bidirectional ───────────────────────────
    # 4a: Profile owner blocked the author
    if await crud.is_blocked(
        db_session, blocker_id=target_tg_id, blocked_id=author_tg_id
    ):
        await call.answer(
            "🚫 شما توسط این کاربر مسدود شده‌اید و نمی‌توانید کامنت بگذارید.",
            show_alert=True,
        )
        return
    # 4b: Author blocked the profile owner (mutual block enforcement)
    if await crud.is_blocked(
        db_session, blocker_id=author_tg_id, blocked_id=target_tg_id
    ):
        await call.answer(
            "🚫 شما این کاربر را مسدود کرده‌اید. "
            "برای کامنت‌گذاری ابتدا رفع بلاک کنید.",
            show_alert=True,
        )
        return

    # ── Check for existing comment (edit vs. new) ─────────────────────
    existing = await crud.get_my_comment_on_profile(
        db_session, author_tg_id, target_tg_id
    )

    # ── Preserve current state BEFORE entering comment flow ───────────
    await _preserve_current_state(state)

    await state.set_state(ProfileCommentStates.waiting_for_comment_text)
    await state.update_data(target_tg_id=target_tg_id)

    if existing:
        prompt = (
            '<tg-emoji emoji-id="5334673106202010226">✏️</tg-emoji> <b>ویرایش نظر</b>\n'
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"نظر فعلی شما:\n<blockquote>{existing.text}</blockquote>\n\n"
            "💬 متن جدید را بنویسید (حداکثر ۳۰۰ کاراکتر):\n"
            "یا /cancel برای انصراف"
        )
    else:
        prompt = (
            '<tg-emoji emoji-id="5470060791883374114">✍️</tg-emoji> <b>نظر جدید</b>\n'
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "💬 متن نظر خود را بنویسید (حداکثر ۳۰۰ کاراکتر):\n"
            "یا /cancel برای انصراف"
        )

    await call.message.answer(prompt, parse_mode="HTML")
    await call.answer()


# ════════════════════════════════════════════════════════════════════════════
# Process comment text — GUARDED SUBMISSION
# ════════════════════════════════════════════════════════════════════════════

@router.message(ProfileCommentStates.waiting_for_comment_text)
async def process_comment_text(
    message: Message, state: FSMContext, db_session: AsyncSession
):
    text = (message.text or "").strip()
    tg_id = message.from_user.id

    # ── Cancel ────────────────────────────────────────────────────────
    if text.lower() == "/cancel":
        await _is_in_active_session(state, db_session, author_tg_id)
        await message.answer("❌ عملیات لغو شد.")
        return

    if not text:
        await message.answer("⚠️ متن کامنت نمی‌تواند خالی باشد.")
        return

    if len(text) > 300:
        await message.answer(
            f"⚠️ کامنت حداکثر ۳۰۰ کاراکتر می‌تواند باشد. "
            f"({len(text)} کاراکتر وارد شده)"
        )
        return

    data = await state.get_data()
    target_tg_id = data.get("target_tg_id")
    author_tg_id = tg_id

    if not target_tg_id:
        await _restore_previous_state(state, db_session, author_tg_id)
        await message.answer(
            "⚠️ نشست شما منقضی شده است. لطفاً مجدداً تلاش کنید."
        )
        return

    # ── Defence-in-depth: re-check own-profile ────────────────────────
    if target_tg_id == author_tg_id:
        await _restore_previous_state(state, db_session, author_tg_id)
        await message.answer("⚠️ نمی‌توانید روی پروفایل خودتان کامنت بگذارید.")
        return

    # ── Re-check: active session (race condition protection) ──────────
    # Between start_add_comment and this submission, the user might have
    # entered an anonymous chat. Block it.
    if await _is_in_active_session(state, db_session, author_tg_id):
        await _restore_previous_state(state, db_session, author_tg_id)
        await message.answer(
            "🚫 در حین چت ناشناس نمی‌توانید کامنت بگذارید. "
            "کامنت شما ثبت نشد."
        )
        return

    # ── Re-check: comments disabled ───────────────────────────────────
    if await crud.are_comments_disabled(db_session, target_tg_id):
        await _restore_previous_state(state, db_session, author_tg_id)
        await message.answer(
            "🔒 این کاربر کامنت‌گذاری روی پروفایلش را بسته است. "
            "کامنت شما ثبت نشد."
        )
        return

    # ── Re-check: block list (bidirectional) ──────────────────────────
    if await crud.is_blocked(
        db_session, blocker_id=target_tg_id, blocked_id=author_tg_id
    ):
        await _restore_previous_state(state, db_session, author_tg_id)
        await message.answer(
            "🚫 شما توسط این کاربر مسدود شده‌اید و کامنت شما ثبت نشد."
        )
        return
    if await crud.is_blocked(
        db_session, blocker_id=author_tg_id, blocked_id=target_tg_id
    ):
        await _restore_previous_state(state, db_session, author_tg_id)
        await message.answer(
            "🚫 شما این کاربر را مسدود کرده‌اید. "
            "برای کامنت‌گذاری ابتدا رفع بلاک کنید."
        )
        return

    # ── Persist comment ───────────────────────────────────────────────
    safe_text = html.escape(text)
    try:
        comment = await crud.upsert_profile_comment(
            session=db_session,
            author_tg_id=author_tg_id,
            target_tg_id=target_tg_id,
            text=safe_text,
        )
        await db_session.commit()
    except Exception as e:
        await db_session.rollback()
        logger.error("Failed to upsert comment: %s", e, exc_info=True)
        await _restore_previous_state(state, db_session, author_tg_id)
        await message.answer("❌ خطایی رخ داد. لطفاً مجدداً تلاش کنید.")
        return

    # ── Restore previous FSM state (preserves chat metadata) ──────────
    await _restore_previous_state(state, db_session, author_tg_id)

    is_edit = comment.created_at != comment.updated_at
    action = "ویرایش" if is_edit else "ثبت"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="💬 مشاهده کامنت‌ها",
            callback_data=f"view_comments:{target_tg_id}:0",
        )
    ]])
    await message.answer(
        f'<tg-emoji emoji-id="5427009714745517609">✅</tg-emoji> '
        f"کامنت شما با موفقیت {action} شد.",
        reply_markup=kb,
        parse_mode="HTML",
    )

    # ── Notify profile owner ──────────────────────────────────────────
    author_user = await crud.get_user_by_tg_id(db_session, author_tg_id)
    author_label = (
        author_user.public_id
        if author_user and author_user.public_id
        else "کاربر"
    )
    await _notify_new_comment(target_tg_id, author_label, safe_text, is_edit)


# ════════════════════════════════════════════════════════════════════════════
# Delete comment
# ════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("del_comment:"))
async def delete_comment(call: CallbackQuery, db_session: AsyncSession):
    parts = call.data.split(":")
    try:
        comment_id   = int(parts[1])
        target_tg_id = int(parts[2])
        page         = int(parts[3])
    except (ValueError, IndexError):
        return await call.answer("⚠️ درخواست نامعتبر.", show_alert=True)

    deleted = await crud.delete_profile_comment(
        session=db_session,
        comment_id=comment_id,
        requester_tg_id=call.from_user.id,
    )

    if not deleted:
        await call.answer("⚠️ کامنت یافت نشد یا دسترسی ندارید.", show_alert=True)
        return

    await db_session.commit()
    await call.answer("🗑 کامنت حذف شد.")

    comments, total = await crud.get_profile_comments(db_session, target_tg_id, page)
    if not comments and page > 0:
        page -= 1
        comments, total = await crud.get_profile_comments(db_session, target_tg_id, page)

    is_own_profile = (call.from_user.id == target_tg_id)
    comments_disabled = await crud.are_comments_disabled(db_session, target_tg_id)

    text = _build_comments_text(comments, total)
    kb = _comments_keyboard(
        comments=comments,
        target_tg_id=target_tg_id,
        page=page,
        total=total,
        viewer_tg_id=call.from_user.id,
        is_own_profile=is_own_profile,
        comments_disabled=comments_disabled,
    )

    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
# Block comment author (by profile owner)
# ════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("block_from_comment:"))
async def block_comment_author(call: CallbackQuery, db_session: AsyncSession):
    parts = call.data.split(":")
    author_tg_id = int(parts[1])
    target_tg_id = int(parts[2])
    page         = int(parts[3])

    if call.from_user.id != target_tg_id:
        await call.answer("⚠️ دسترسی ندارید.", show_alert=True)
        return

    from matching_bot_project.bot.handlers.interactions import execute_user_blocking

    success, msg = await execute_user_blocking(
        db_session, blocker_id=target_tg_id, blocked_id=author_tg_id
    )
    await call.answer(msg, show_alert=True)

    if not success:
        return

    comments, total = await crud.get_profile_comments(db_session, target_tg_id, page)
    if not comments and page > 0:
        page -= 1
        comments, total = await crud.get_profile_comments(db_session, target_tg_id, page)

    comments_disabled = await crud.are_comments_disabled(db_session, target_tg_id)

    text = _build_comments_text(comments, total)
    kb = _comments_keyboard(
        comments=comments,
        target_tg_id=target_tg_id,
        page=page,
        total=total,
        viewer_tg_id=call.from_user.id,
        is_own_profile=True,
        comments_disabled=comments_disabled,
    )

    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
# Toggle comments on/off
# ════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("toggle_comments:"))
async def toggle_comments(call: CallbackQuery, db_session: AsyncSession):
    parts = call.data.split(":")
    target_tg_id = int(parts[1])
    page         = int(parts[2]) if len(parts) > 2 else 0

    if call.from_user.id != target_tg_id:
        await call.answer("⚠️ دسترسی ندارید.", show_alert=True)
        return

    new_state = await crud.toggle_comments_disabled(db_session, target_tg_id)
    if new_state is None:
        await call.answer("❌ حساب کاربری یافت نشد.", show_alert=True)
        return

    await db_session.commit()

    msg = (
        "🔒 کامنت‌گذاری روی پروفایل شما بسته شد."
        if new_state
        else "🔓 کامنت‌گذاری روی پروفایل شما باز شد."
    )
    await call.answer(msg, show_alert=True)

    comments, total = await crud.get_profile_comments(db_session, target_tg_id, page)
    text = _build_comments_text(comments, total)
    kb = _comments_keyboard(
        comments=comments,
        target_tg_id=target_tg_id,
        page=page,
        total=total,
        viewer_tg_id=call.from_user.id,
        is_own_profile=True,
        comments_disabled=new_state,
    )

    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
# Helper buttons
# ════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "close_comments")
async def close_comments(call: CallbackQuery):
    await call.message.delete()
    await call.answer()


@router.callback_query(F.data == "noop")
async def noop_handler(call: CallbackQuery):
    await call.answer()