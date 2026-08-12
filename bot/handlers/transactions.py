"""
bot/handlers/transactions.py
──────────────────────────────────────────────────────────────────────────────
Transaction history handler with category filtering and pagination.
──────────────────────────────────────────────────────────────────────────────
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional
from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.queries import crud
from matching_bot_project.database.models.models import CoinTransaction

logger = logging.getLogger(__name__)
router = Router(name="transactions_handler")

_PER_PAGE = 5

_EMOJI = {
    "chart": "5431577498364158238",       # 📊
    "clipboard": "5431736674147114227",   # 🗂 (جایگزین 📋 که در پک موجود نیست)
    "clock": "5413704112220949842",       # ⏰
    "memo": "5334882760735598374",        # 📝
    "id_card": "5422683699130933153",     # 🪪
    "empty": "5352896944496728039",       # 📭
    "plus": "5226945370684140473",        # ➕
    "minus": "5229113891081956317",       # ➖
    "cart": "5431499171045581032",        # 🛒
    "gift": "5199749070830197566",        # 🎁
    "wings": "5472030678633684592",       # 💸
    "check": "5427009714745517609",       # ✅
    "cross": "5465665476971471368",       # ❌
    "arrow_right": "5471978009449731768", # 👉
    "arrow_left": "5469735272017043817",  # 👈
    "page": "5472404950673791399",        # 🧮 (شمارش صفحات؛ در پک، ایموجی مستقل 📄 وجود ندارد)
}

_CATEGORY_META = {
    "all":      {"label": "همه تراکنش‌ها", "icon": "🗂", "emoji_key": "clipboard"},
    "purchase": {"label": "خریدهای من",    "icon": "🛒", "emoji_key": "cart"},
    "received": {"label": "دریافتی‌ها",    "icon": "🎁", "emoji_key": "gift"},
    "spent":    {"label": "پرداختی‌ها",    "icon": "💸", "emoji_key": "wings"},
}

_CATEGORIES = {key: meta["label"] for key, meta in _CATEGORY_META.items()}

_TEHRAN_TZ = ZoneInfo("Asia/Tehran")


def _pe(emoji_id: str, fallback: str) -> str:
    """ساخت تگ اموجی پریمیوم با توجه به آیدی (فقط برای متن پیام)"""
    return f'<a href="tg://emoji?id={emoji_id}">{fallback}</a>'


def _category_icon(category: str) -> str:
    """اموجی پریمیوم متناظر با دسته‌بندی فعال، برای استفاده در هدر پیام"""
    meta = _CATEGORY_META.get(category, _CATEGORY_META["all"])
    return _pe(_EMOJI[meta["emoji_key"]], meta["icon"])


# ── Helpers ─────────────────────────────────────────────────────────────────

def _get_tx_date(tx: CoinTransaction) -> Optional[datetime]:
    return getattr(tx, "created_at", None)


def _format_date(dt: Optional[datetime]) -> str:
    """
    تبدیل زمان UTC به زمان تهران با استفاده از zoneinfo.
    این روش به‌صورت خودکار تغییرات تابستانی (DST) را مدیریت می‌کند.
    """
    if dt is None:
        return "نامشخص"
    
    # اطمینان از اینکه تاریخ دارای timezone است (در صورت نبودن، UTC در نظر گرفته می‌شود)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        
    tehran_dt = dt.astimezone(_TEHRAN_TZ)
    return tehran_dt.strftime("%Y/%m/%d %H:%M")


def _get_tx_icon(tx: CoinTransaction, category: str) -> str:
    if category == "purchase":
        return _pe(_EMOJI["cart"], "🛒")
    if category == "received":
        return _pe(_EMOJI["gift"], "🎁")
    if category == "spent":
        return _pe(_EMOJI["wings"], "💸")

    if tx.amount >= 0:
        return _pe(_EMOJI["plus"], "➕")
    return _pe(_EMOJI["minus"], "➖")


def _format_tx(tx: CoinTransaction, index: int, category: str) -> str:
    icon = _get_tx_icon(tx, category)
    amount_str = f"+{tx.amount}" if tx.amount >= 0 else str(tx.amount)

    desc = tx.description or "بدون توضیحات"
    date_str = _format_date(_get_tx_date(tx))
    
    # ⭐ REFACTORED: Read directly from the structured database column
    # instead of parsing natural language text via regex.
    if tx.reference_id is not None:
        identifier_label = f"{_pe(_EMOJI['id_card'], '🪪')} شناسه مرجع"
        identifier_val = tx.reference_id
    else:
        identifier_label = f"{_pe(_EMOJI['id_card'], '🪪')} شناسه"
        identifier_val = getattr(tx, "id", "نامشخص")

    return (
        f"<blockquote>"
        f"<b>{index}.</b> {icon} <b>{amount_str}</b> سکه\n"
        f"┣ {_pe(_EMOJI['memo'], '📝')} {desc}\n"
        f"┣ {_pe(_EMOJI['clock'], '⏰')} {date_str}\n"
        f"┗ {identifier_label}: <code>{identifier_val}</code>"
        f"</blockquote>"
    )


def _build_tx_keyboard(active_category: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = []

    cat_row = []
    for key, meta in _CATEGORY_META.items():
        is_active = active_category == key
        cat_row.append(InlineKeyboardButton(
            text=meta["label"],
            callback_data=f"tx_cat_{key}",
            icon_custom_emoji_id=_EMOJI[meta["emoji_key"]],
            style="success" if is_active else "primary",
        ))
    rows.append(cat_row[:2])
    rows.append(cat_row[2:])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(
            text="قبلی",
            callback_data=f"tx_page_{active_category}_{page - 1}",
            icon_custom_emoji_id=_EMOJI["arrow_right"],
            style="primary",
        ))
    nav_row.append(InlineKeyboardButton(
        text=f"{page + 1}/{total_pages}",
        callback_data="tx_noop",
        icon_custom_emoji_id=_EMOJI["page"],
        style="primary",
    ))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(
            text="بعدی",
            callback_data=f"tx_page_{active_category}_{page + 1}",
            icon_custom_emoji_id=_EMOJI["arrow_left"],
            style="primary",
        ))
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton(
        text="بستن",
        callback_data="tx_close",
        icon_custom_emoji_id=_EMOJI["cross"],
        style="danger",
    )])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_tx_text(
    transactions: list,
    total: int,
    category: str,
    page: int,
    total_pages: int
) -> str:
    """ساخت متن تراکنش‌ها"""
    cat_title = _CATEGORY_META.get(category, {}).get("label", "تراکنش‌ها")
    header_icon = _category_icon(category)

    if not transactions:
        text = (
            f"{header_icon} <b>{cat_title}</b>\n\n"
            f"<blockquote>{_pe(_EMOJI['empty'], '📭')} شما در این بخش هیچ تراکنشی ندارید.</blockquote>"
        )
        return text

    text = (
        f"{header_icon} <b>{cat_title}</b>\n\n"
        f"<blockquote>"
        f"{_pe(_EMOJI['clipboard'], '🗂')} مجموع: <b>{total}</b> تراکنش\n"
        f"{_pe(_EMOJI['page'], '🧮')} صفحه <b>{page + 1}</b> از <b>{total_pages}</b>"
        f"</blockquote>\n\n"
    )

    for i, tx in enumerate(transactions, start=1):
        text += _format_tx(tx, i, category) + "\n\n"

    return text.rstrip()


# ── Core display functions ──────────────────────────────────────────────────

async def _show_transactions_from_callback(
    call: CallbackQuery,
    db_session: AsyncSession,
    category: str = "all",
    page: int = 0
) -> None:
    """نمایش تاریخچه تراکنش‌ها از طریق callback query (ادیت پیام)"""
    tg_id = call.from_user.id

    transactions, total = await crud.get_user_transactions(
        db_session, tg_id, category=category, limit=_PER_PAGE, offset=page * _PER_PAGE
    )

    total_pages = max((total + _PER_PAGE - 1) // _PER_PAGE, 1)
    if page >= total_pages:
        page = max(total_pages - 1, 0)

    text = _build_tx_text(transactions, total, category, page, total_pages)
    kb = _build_tx_keyboard(category, page, total_pages)

    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer(text, reply_markup=kb, parse_mode="HTML")


async def _show_transactions_from_message(
    message: Message,
    db_session: AsyncSession,
    category: str = "all",
    page: int = 0
) -> None:
    """نمایش تاریخچه تراکنش‌ها از طریق message (ارسال پیام جدید)"""
    tg_id = message.from_user.id

    transactions, total = await crud.get_user_transactions(
        db_session, tg_id, category=category, limit=_PER_PAGE, offset=page * _PER_PAGE
    )

    total_pages = max((total + _PER_PAGE - 1) // _PER_PAGE, 1)
    if page >= total_pages:
        page = max(total_pages - 1, 0)

    text = _build_tx_text(transactions, total, category, page, total_pages)
    kb = _build_tx_keyboard(category, page, total_pages)

    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ── Handlers ────────────────────────────────────────────────────────────────

@router.message(F.text == "💰 تاریخچه تراکنش")
async def show_tx_history_from_reply(message: Message, db_session: AsyncSession):
    """ورود به منوی تاریخچه از طریق دکمه ریپلی کیبورد"""
    await _show_transactions_from_message(message, db_session, category="all", page=0)


@router.callback_query(F.data == "coins_history")
async def show_tx_history(call: CallbackQuery, db_session: AsyncSession):
    """ورود به منوی تاریخچه (پیش‌فرض: همه تراکنش‌ها)"""
    await _show_transactions_from_callback(call, db_session, category="all", page=0)
    await call.answer()


@router.callback_query(F.data.startswith("tx_cat_"))
async def tx_change_category(call: CallbackQuery, db_session: AsyncSession):
    """تغییر دسته‌بندی تراکنش‌ها"""
    category = call.data.removeprefix("tx_cat_")
    if category not in _CATEGORY_META:
        category = "all"
    await _show_transactions_from_callback(call, db_session, category=category, page=0)
    await call.answer()


@router.callback_query(F.data.startswith("tx_page_"))
async def tx_history_pagination(call: CallbackQuery, db_session: AsyncSession):
    """صفحه‌بندی داخل یک دسته‌بندی مشخص"""
    parts = call.data.removeprefix("tx_page_").rsplit("_", 1)
    if len(parts) != 2:
        await call.answer()
        return

    category = parts[0]
    try:
        page = int(parts[1])
    except ValueError:
        page = 0

    await _show_transactions_from_callback(call, db_session, category=category, page=page)
    await call.answer()


@router.callback_query(F.data == "tx_noop")
async def tx_noop(call: CallbackQuery):
    """جلوگیری از لودینگ دکمه شماره صفحه."""
    await call.answer()


@router.callback_query(F.data == "tx_close")
async def tx_close(call: CallbackQuery, db_session: AsyncSession):
    """بستن پنجره تاریخچه تراکنش‌ها و بازگشت به منوی سکه."""
    from matching_bot_project.database.models.models import User
    from sqlalchemy import select
    from matching_bot_project.bot.keyboards.inline import get_coins_main_menu_keyboard
    
    user_obj = await db_session.execute(select(User).where(User.tg_id == call.from_user.id))
    user = user_obj.scalar_one_or_none()
    if not user:
        return await call.answer("خطا در بارگذاری.")
        
    text = (
        f"💰 <b>منوی سکه</b>\n\n"
        f"موجودی فعلی شما: <b>{user.coin_balance}</b> سکه\n\n"
        f"از منوی زیر انتخاب کنید:"
    )
    
    # 🌟 بازگشت روان به منوی سکه
    try:
        await call.message.edit_text(text, reply_markup=get_coins_main_menu_keyboard(), parse_mode="HTML")
    except TelegramBadRequest:
        pass
        
    await call.answer("بسته شد.")