"""
handlers/comments.py
────────────────────
سیستم کامنت پروفایل:
  - هر کاربر یه کامنت روی هر پروفایل (ویرایش‌پذیر)
  - صاحب پروفایل می‌تونه هر کامنتی رو پاک کنه
  - نویسنده می‌تونه کامنت خودش رو پاک کنه
  - صاحب پروفایل می‌تونه نویسنده‌ی هر کامنت رو مستقیماً از همین‌جا بلاک کنه
  - کاربری که توسط صاحب پروفایل بلاک شده، نمی‌تونه کامنت بگذاره
  - pagination با ۳ کامنت در صفحه

نقاط ورود:
  callback_data="view_comments:{target_tg_id}:0"              ← نمایش کامنت‌ها (از profile.py / profile_edit.py)
  callback_data="add_comment:{target_tg_id}"                  ← شروع نوشتن کامنت
  callback_data="block_from_comment:{author_id}:{target_id}:{page}" ← بلاک نویسنده‌ی کامنت توسط صاحب پروفایل
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
from aiogram.filters import StateFilter
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.queries import crud
from matching_bot_project.bot.states.states import ProfileCommentStates

logger = logging.getLogger(__name__)
router = Router(name="comments_handler")

_PER_PAGE = 3


# ══════════════════════════════════════════════════════════════
# کمکی‌های نمایش متن
# ══════════════════════════════════════════════════════════════

def _build_comments_text(comments) -> str:
    if not comments:
        return "💬 <b>کامنت‌های پروفایل</b>\n\n📭 هنوز هیچ کامنتی ثبت نشده."

    lines = ["💬 <b>کامنت‌های پروفایل</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for c in comments:
        author_name = html.escape(
            c.author.first_name if c.author and c.author.first_name else "کاربر"
        )
        edited = " <i>(ویرایش‌شده)</i>" if c.updated_at != c.created_at else ""
        lines.append(
            f"👤 <b>{author_name}</b>{edited}\n"
            f"<code>#{c.id}</code>  {html.escape(c.text)}"
        )
        lines.append("─────────────────────")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# کیبوردها
# ══════════════════════════════════════════════════════════════

def _comments_keyboard(
    comments,
    target_tg_id: int,
    page: int,
    total: int,
    viewer_tg_id: int,
    is_own_profile: bool,
) -> InlineKeyboardMarkup:
    rows = []

    # دکمه‌های هر کامنت: حذف (صاحب پروفایل یا نویسنده) + بلاک نویسنده (فقط صاحب پروفایل)
    for c in comments:
        can_delete = is_own_profile or (c.author_tg_id == viewer_tg_id)
        comment_row = []
        if can_delete:
            comment_row.append(
                InlineKeyboardButton(
                    text=f"🗑 حذف #{c.id}",
                    callback_data=f"del_comment:{c.id}:{target_tg_id}:{page}",
                )
            )
        # صاحب پروفایل می‌تونه نویسنده‌ی مزاحم رو همینجا بلاک کنه
        if is_own_profile and c.author_tg_id != viewer_tg_id:
            comment_row.append(
                InlineKeyboardButton(
                    text=f"🚫 بلاک نویسنده #{c.id}",
                    callback_data=f"block_from_comment:{c.author_tg_id}:{target_tg_id}:{page}",
                )
            )
        if comment_row:
            rows.append(comment_row)

    # navigation
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️ قبلی",
            callback_data=f"view_comments:{target_tg_id}:{page - 1}",
        ))

    total_pages = max(1, -(-total // _PER_PAGE))  # ceil division
    nav.append(InlineKeyboardButton(
        text=f"📄 {page + 1}/{total_pages}",
        callback_data="noop",
    ))

    if (page + 1) * _PER_PAGE < total:
        nav.append(InlineKeyboardButton(
            text="بعدی ▶️",
            callback_data=f"view_comments:{target_tg_id}:{page + 1}",
        ))

    if nav:
        rows.append(nav)

    # دکمه نوشتن / ویرایش کامنت (برای پروفایل دیگران)
    if not is_own_profile:
        rows.append([
            InlineKeyboardButton(
                text="✏️ کامنت من",
                callback_data=f"add_comment:{target_tg_id}",
            )
        ])

    rows.append([
        InlineKeyboardButton(text="🔙 بستن", callback_data="close_comments")
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ══════════════════════════════════════════════════════════════
# نمایش کامنت‌ها
# ══════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("view_comments:"))
async def show_comments(call: CallbackQuery, db_session: AsyncSession):
    parts = call.data.split(":")
    target_tg_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    viewer_tg_id = call.from_user.id
    is_own_profile = (viewer_tg_id == target_tg_id)

    comments, total = await crud.get_profile_comments(db_session, target_tg_id, page)

    text = _build_comments_text(comments)

    kb = _comments_keyboard(
        comments=comments,
        target_tg_id=target_tg_id,
        page=page,
        total=total,
        viewer_tg_id=viewer_tg_id,
        is_own_profile=is_own_profile,
    )

    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await call.message.answer(text, reply_markup=kb, parse_mode="HTML")

    await call.answer()


# ══════════════════════════════════════════════════════════════
# نوشتن / ویرایش کامنت
# ══════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("add_comment:"))
async def start_add_comment(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    target_tg_id = int(call.data.split(":")[1])
    author_tg_id = call.from_user.id

    if author_tg_id == target_tg_id:
        await call.answer("⚠️ نمی‌توانید روی پروفایل خودتان کامنت بگذارید.", show_alert=True)
        return

    # اگه صاحب پروفایل قبلاً این کاربر رو بلاک کرده باشه، اجازه‌ی کامنت گذاشتن نداره
    if await crud.is_blocked(db_session, blocker_id=target_tg_id, blocked_id=author_tg_id):
        await call.answer("🚫 شما توسط این کاربر مسدود شده‌اید و نمی‌توانید کامنت بگذارید.", show_alert=True)
        return

    # بررسی کامنت قبلی
    existing = await crud.get_my_comment_on_profile(
        db_session, author_tg_id, target_tg_id
    )

    await state.set_state(ProfileCommentStates.waiting_for_comment_text)
    await state.update_data(target_tg_id=target_tg_id)

    if existing:
        prompt = (
            f"✏️ <b>ویرایش کامنت</b>\n\n"
            f"کامنت فعلی شما:\n<i>{html.escape(existing.text)}</i>\n\n"
            "متن جدید را بنویسید (حداکثر ۳۰۰ کاراکتر):\n"
            "یا /cancel برای انصراف"
        )
    else:
        prompt = (
            "✍️ <b>کامنت جدید</b>\n\n"
            "متن کامنت خود را بنویسید (حداکثر ۳۰۰ کاراکتر):\n"
            "یا /cancel برای انصراف"
        )

    await call.message.answer(prompt, parse_mode="HTML")
    await call.answer()


@router.message(ProfileCommentStates.waiting_for_comment_text)
async def process_comment_text(message: Message, state: FSMContext, db_session: AsyncSession):
    text = (message.text or "").strip()

    if text.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    if not text:
        await message.answer("⚠️ متن کامنت نمی‌تواند خالی باشد.")
        return

    if len(text) > 300:
        await message.answer(f"⚠️ کامنت حداکثر ۳۰۰ کاراکتر می‌تواند باشد. ({len(text)} کاراکتر وارد شده)")
        return

    data = await state.get_data()
    target_tg_id = data["target_tg_id"]
    author_tg_id = message.from_user.id

    # چک مجدد بلاک — ممکنه کاربر بین شروع و ارسال متن بلاک شده باشه
    if await crud.is_blocked(db_session, blocker_id=target_tg_id, blocked_id=author_tg_id):
        await state.clear()
        await message.answer("🚫 شما توسط این کاربر مسدود شده‌اید و کامنت شما ثبت نشد.")
        return

    safe_text = html.escape(text)
    comment = await crud.upsert_profile_comment(
        session=db_session,
        author_tg_id=author_tg_id,
        target_tg_id=target_tg_id,
        text=safe_text,
    )
    await db_session.commit()
    await state.clear()

    is_edit = comment.created_at != comment.updated_at
    action = "ویرایش" if is_edit else "ثبت"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="💬 مشاهده کامنت‌ها",
            callback_data=f"view_comments:{target_tg_id}:0",
        )
    ]])
    await message.answer(
        f"✅ کامنت شما با موفقیت {action} شد.",
        reply_markup=kb,
    )


# ══════════════════════════════════════════════════════════════
# حذف کامنت
# ══════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("del_comment:"))
async def delete_comment(call: CallbackQuery, db_session: AsyncSession):
    # del_comment:{comment_id}:{target_tg_id}:{page}
    parts = call.data.split(":")
    comment_id   = int(parts[1])
    target_tg_id = int(parts[2])
    page         = int(parts[3])

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

    # برگشت به همون صفحه (اگه خالی شد، صفحه قبلی)
    comments, total = await crud.get_profile_comments(db_session, target_tg_id, page)
    if not comments and page > 0:
        page -= 1
        comments, total = await crud.get_profile_comments(db_session, target_tg_id, page)

    is_own_profile = (call.from_user.id == target_tg_id)

    text = _build_comments_text(comments)

    kb = _comments_keyboard(
        comments=comments,
        target_tg_id=target_tg_id,
        page=page,
        total=total,
        viewer_tg_id=call.from_user.id,
        is_own_profile=is_own_profile,
    )

    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# بلاک کردن نویسنده‌ی کامنت (توسط صاحب پروفایل)
# ══════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("block_from_comment:"))
async def block_comment_author(call: CallbackQuery, db_session: AsyncSession):
    # block_from_comment:{author_tg_id}:{target_tg_id}:{page}
    parts = call.data.split(":")
    author_tg_id = int(parts[1])
    target_tg_id = int(parts[2])
    page         = int(parts[3])

    # فقط صاحب پروفایل می‌تونه از این مسیر بلاک کنه
    if call.from_user.id != target_tg_id:
        await call.answer("⚠️ دسترسی ندارید.", show_alert=True)
        return

    # lazy import برای پیشگیری از circular import بین comments.py و interactions.py
    from matching_bot_project.bot.handlers.interactions import execute_user_blocking

    success, msg = await execute_user_blocking(db_session, blocker_id=target_tg_id, blocked_id=author_tg_id)
    await call.answer(msg, show_alert=True)

    if not success:
        return

    # رفرش لیست کامنت‌ها (کامنت‌های همین کاربر بلاک‌شده هنوز نمایش داده می‌شن، چون
    # تاریخچه‌ی کامنت حذف نمی‌شود — فقط امکان کامنت جدید گرفته می‌شود)
    comments, total = await crud.get_profile_comments(db_session, target_tg_id, page)
    if not comments and page > 0:
        page -= 1
        comments, total = await crud.get_profile_comments(db_session, target_tg_id, page)

    text = _build_comments_text(comments)
    kb = _comments_keyboard(
        comments=comments,
        target_tg_id=target_tg_id,
        page=page,
        total=total,
        viewer_tg_id=call.from_user.id,
        is_own_profile=True,
    )

    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# دکمه‌های کمکی
# ══════════════════════════════════════════════════════════════

@router.callback_query(F.data == "close_comments")
async def close_comments(call: CallbackQuery):
    await call.message.delete()
    await call.answer()


@router.callback_query(F.data == "noop")
async def noop_handler(call: CallbackQuery):
    """دکمه شماره صفحه — هیچ کاری نمی‌کنه"""
    await call.answer()