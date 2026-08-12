import logging
import html
import os
import json
import string
import random
from pathlib import Path
from datetime import datetime, timedelta, timezone
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update
from matching_bot_project.bot.core.loader import gift_engine
from matching_bot_project.database.queries import crud
from matching_bot_project.bot.core.config import settings

from matching_bot_project.bot.core.constants import ReplyBtn
from matching_bot_project.bot.core.formatters import build_unified_profile_card, chunk_html_text, get_pagination_row
from matching_bot_project.bot.keyboards.inline import get_user_action_keyboard
from matching_bot_project.database.models.models import BlockList
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from matching_bot_project.bot.core.loader import dp, bot, redis_client
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
from matching_bot_project.bot.states.states import ProfileEditStates
from sqlalchemy import select
from aiogram.dispatcher.event.bases import SkipHandler
from matching_bot_project.bot.handlers.vip import _is_vip_active
logger = logging.getLogger(__name__)
router = Router(name="profile_handler")

# --- Premium Emojis ---
BLUE_TICK_HTML = '<tg-emoji emoji-id="5852518859268951767">⭐</tg-emoji>'
P_CHECK_HTML = '<tg-emoji emoji-id="6037088297061191007">✔️</tg-emoji>'
P_CROSS_HTML = '<tg-emoji emoji-id="6037327204617030722">❌</tg-emoji>'
P_WARN_HTML = '<tg-emoji emoji-id="6037255895275015803">⚠️</tg-emoji>'
P_BELL_HTML = '<tg-emoji emoji-id="6039712977345580805">🔔</tg-emoji>'
P_BELL_ID = "6039712977345580805"
P_CHECK_ID = "6037088297061191007"
P_CROSS_ID = "6037327204617030722"
P_BACK_ID = "6037271103754211155" # آیدی فلش بازگشت ➡️


def generate_public_id(length=6):
    # FIX L-12: use secrets.choice instead of random.choice so public_id is not
    # cryptographically predictable (random is a Mersenne-Twister PRNG).
    import secrets
    characters = string.ascii_letters + string.digits
    return f"user_{''.join(secrets.choice(characters) for _ in range(length))}"

@router.message(F.text == ReplyBtn.MY_PROFILE)
async def view_user_profile(message: Message, db_session: AsyncSession, state: FSMContext):
    current_state = await state.get_state()
    data = await state.get_data()
    prev_state = data.get("__gift_prev_state__", "")

    # 🛡️ چک کردن اینکه آیا کاربر الان تو چت/مچینگ هست، یا اینکه تو منوی گیفته ولی قبلاً تو چت بوده!
    is_active_process = current_state and any(x in current_state.lower() for x in ["chat", "matching", "questionnaire"])
    was_active_process = current_state and "gift" in current_state.lower() and prev_state and any(x in prev_state.lower() for x in ["chat", "matching", "questionnaire"])

    if is_active_process or was_active_process:
        return await message.answer("⚠️ شما در حال حاضر در یک فرآیند فعال (چت یا مچینگ) هستید. لطفاً اول آن را پایان دهید.")

    # 🛡️ جلوگیری از پاک شدن استیت گیفت
    if current_state and any(x in current_state.lower() for x in ["discovery", "gift"]):
        logger.info(f"User {message.from_user.id} viewed profile during discovery/gift. Preserving state.")
    else:
        await state.clear()

    try:
        tg_id = message.from_user.id
        user = await crud.get_user_by_tg_id(db_session, tg_id)

        if not user or not user.completed_registration:
            await message.answer("⚠️ رفیق هنوز ثبت‌نامت کامل نشده! /start رو بفرست تا شروع کنیم.")
            return

        await db_session.refresh(user)

        if not getattr(user, 'public_id', None):
            user.public_id = generate_public_id(6)
            await db_session.commit()
            await db_session.refresh(user)

        # ✅ استخراج گیفت‌ها و ساخت کارت پروفایل اصلی
        gifts_summary = await gift_engine.get_user_gifts_summary(db_session, user.tg_id)
        profile_card = build_unified_profile_card(user, is_own_profile=True, gifts_summary=gifts_summary)
        pages = chunk_html_text(profile_card, max_length=950)

        now_utc = datetime.now(timezone.utc)
        expires = user.vip_expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
            
        is_user_vip = bool(user.is_vip) and (not expires or expires > now_utc)
        
        inline_rows = [
            [InlineKeyboardButton(text="ویرایش پروفایل", callback_data="edit_profile_triggered", icon_custom_emoji_id="5334882760735598374", style="primary")]
        ]
        
        if is_user_vip:
            inline_rows.append([InlineKeyboardButton(text="بخش ویژه VIP", callback_data="vip_panel", icon_custom_emoji_id="5471952986970267163", style="success")])
            
        if len(pages) > 1:
            nav_row = get_pagination_row(target_id=user.tg_id, current_page=0, total_pages=len(pages), is_own=True)
            inline_rows.insert(0, nav_row)
            
        inline_kb = InlineKeyboardMarkup(inline_keyboard=inline_rows)

        photo_id = getattr(user, 'profile_photo_file_id', None)
        photo_sent = False
        
        if photo_id:
            try:
                await message.answer_photo(
                    photo=photo_id, 
                    caption=pages[0], 
                    parse_mode=ParseMode.HTML, 
                    reply_markup=inline_kb
                )
                photo_sent = True
            except Exception as photo_err:
                err_str = str(photo_err)
                if "DOCUMENT_INVALID" in err_str or "wrong file identifier" in err_str:
                    logger.warning(f"Invalid Photo ID for user {tg_id}. Clearing from DB.")
                    user.profile_photo_file_id = None
                    await db_session.commit()
                else:
                    logger.warning(f"Photo failed for unknown reason: {photo_err}")

        if not photo_sent:
            await message.answer(
                text=pages[0], 
                parse_mode=ParseMode.HTML, 
                reply_markup=inline_kb
            )

        voice_id = getattr(user, 'profile_voice_file_id', None)
        if voice_id:
            try:
                await message.answer_voice(voice=voice_id, caption="🎵 <b>صدای پروفایل شما</b>", parse_mode=ParseMode.HTML)
            except Exception as voice_err:
                err_str = str(voice_err)
                if "DOCUMENT_INVALID" in err_str or "wrong file identifier" in err_str:
                    logger.warning(f"Invalid Voice ID for user {tg_id}. Clearing from DB.")
                    user.profile_voice_file_id = None
                    await db_session.commit()
                else:
                    logger.warning(f"Voice failed for user {tg_id}: {voice_err}")

    except Exception as e:
        err_str = str(e)
        if "DOCUMENT_INVALID" in err_str or "wrong file identifier" in err_str:
            if 'user' in locals():
                user.profile_photo_file_id = None
                user.profile_voice_file_id = None
                await db_session.commit()
            await message.answer("⚠️ یکی از فایل‌های پروفایل شما (عکس یا وویس) نامعتبر بود و توسط سیستم امنیتی پاک شد. لطفاً دوباره روی «پروفایل من» کلیک کن.")
        else:
            logger.error(f"Error in view_user_profile: {e}", exc_info=True)
            await message.answer("⚠️ یه مشکلی پیش اومد! لطفاً دوباره تلاش کنید.")

# ==========================================
# سیستم سایلنت مود
# ==========================================

def get_silent_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="غیر فعال کردن سایلنت", callback_data="silent_off", icon_custom_emoji_id=P_BELL_ID)],
        [
            InlineKeyboardButton(text="تا ۱ ساعت", callback_data="silent_1h"),
            InlineKeyboardButton(text="تا ۱ روز", callback_data="silent_1d")
        ],
        [
            InlineKeyboardButton(text="تا ۱ هفته", callback_data="silent_1w"),
            InlineKeyboardButton(text="همیشه سایلنت", callback_data="silent_forever")
        ],
        [InlineKeyboardButton(text="بازگشت", callback_data="close_menu", icon_custom_emoji_id=P_BACK_ID)]
    ])

@router.message(Command("silent"))
async def silent_mode_command(message: Message, db_session: AsyncSession):
    # گرفتن اطلاعات کاربر از دیتابیس
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if not user:
        await message.answer("⚠️ حساب کاربری یافت نشد.")
        return

    # بررسی وضعیت سایلنت کاربر
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if user.silent_until and user.silent_until > now_utc:
        status_text = "فعال 🔕"
    else:
        status_text = "غیرفعال 🔔"

    text = (
        f"🔻 حالت سایلنت: <b>{status_text}</b>\n"
        "───────────────────\n"
        "💡 با فعال شدن حالت سایلنت، درخواست چت یا دیت دریافت نخواهید کرد."
    )
    await message.answer(text, reply_markup=get_silent_keyboard(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("silent_"))
async def handle_silent_options(call: CallbackQuery, db_session: AsyncSession):
    action = call.data.split("_")[1]
    now = datetime.now(timezone.utc).replace(tzinfo=None) 
    
    if action == "off":
        silent_until = None
        msg = f"{P_BELL_HTML} حالت سایلنت با موفقیت غیرفعال شد."
    elif action == "1h":
        silent_until = now + timedelta(hours=1)
        msg = "🔕 درخواست‌های چت و دیت تا ۱ ساعت برای شما ارسال نخواهد شد."
    elif action == "1d":
        silent_until = now + timedelta(days=1)
        msg = "🔕 درخواست‌های چت و دیت تا ۱ روز برای شما ارسال نخواهد شد."
    elif action == "1w":
        silent_until = now + timedelta(weeks=1)
        msg = "🔕 درخواست‌های چت و دیت تا ۱ هفته برای شما ارسال نخواهد شد."
    elif action == "forever":
        silent_until = now + timedelta(days=3650)
        msg = "🔕 درخواست‌های چت و دیت دیگر برای شما ارسال نخواهد شد."
    else:
        await call.answer("گزینه نامعتبر.", show_alert=True)
        return
    
    await crud.update_silent_mode(db_session, call.from_user.id, silent_until)
    await db_session.commit()
    
    await call.answer("تنظیمات ذخیره شد.", show_alert=False)
    await call.message.edit_text(msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data="close_menu", icon_custom_emoji_id=P_BACK_ID)]]
    ))

    
# ==========================================
# سیستم حذف اکانت
# ==========================================

@router.message(Command("delete_account"))
async def delete_account_command(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="بله، اکانتم حذف شود", callback_data="confirm_delete_account", icon_custom_emoji_id=P_CROSS_ID)],
        [InlineKeyboardButton(text="انصراف", callback_data="close_menu", icon_custom_emoji_id=P_BACK_ID)]
    ])
    await message.answer(f"{P_WARN_HTML} <b>آیا از حذف اکانت خود مطمئن هستید؟</b>\nتمام اطلاعات، مچ‌ها و امتیازات شما برای همیشه پاک خواهد شد.", reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "confirm_delete_account")
async def confirm_delete_account_handler(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    
    if not user:
        # اگر کاربر پیدا نشد همون اول خارج می‌شیم تا کد الکی تو در تو نشه (Early Return)
        return await call.answer("⚠️ حساب کاربری شما یافت نشد یا قبلاً حذف شده است.", show_alert=True)

    try:
        # --- ۱. متوقف کردن دیت/چت فعال ---
        active_match = await crud.get_active_match(db_session, user.tg_id)
        if active_match:
            active_match.is_active = False
            active_match.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
            
            partner_id = active_match.user_two_id if active_match.user_one_id == user.tg_id else active_match.user_one_id
            
            # پاکسازی استیت پارتنر
            partner_ctx = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=partner_id, user_id=partner_id))
            await partner_ctx.set_state(None)
            await partner_ctx.clear()
            
            try:
                await redis_client.delete(f"user:state:{partner_id}")
                await bot.send_message(
                    chat_id=partner_id,
                    text="⚠️ <b>دیت متوقف شد!</b>\nپارتنر شما اکانت خود را در ربات حذف کرد.",
                    parse_mode="HTML",
                    reply_markup=get_main_menu_keyboard()
                )
            except Exception as e:
                logger.warning(f"Could not notify partner {partner_id} about account deletion: {e}")
        # --------------------------------------

        # --- ۲. پاکسازی کامل کاربر از صف‌های ردیس و ماشین حالت (FSM) ---
        try:
            from matching_bot_project.bot.core.loader import matching_engine
            await matching_engine.remove_from_queue(user.tg_id)
            await redis_client.delete(f"user:state:{user.tg_id}")
            await state.clear()
        except Exception as e:
            logger.error(f"Error clearing Redis/FSM for deleted user {user.tg_id}: {e}")
        # --------------------------------------

        # --- ۳. حذف از دیتابیس ---
        await crud.mark_account_deleted(db_session, user.tg_id)
        await db_session.delete(user)
        await db_session.commit()

        # پیام موفقیت‌آمیز بودن حذف
        await call.message.edit_text(
            f"{P_CHECK_HTML} <b>اکانت شما و تمامی اطلاعاتتان با موفقیت حذف شد.</b>\n"
            "برای استفاده مجدد /start را بفرستید.", 
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        await db_session.rollback()
        logger.error(f"Error during account deletion for {call.from_user.id}: {e}", exc_info=True)
        await call.answer("خطایی در حذف اکانت رخ داد. لطفاً دوباره تلاش کنید.", show_alert=True)
        

@router.callback_query(F.data == "close_menu")
async def close_menu_handler(call: CallbackQuery):
    await call.message.delete()


@router.message(F.text == ReplyBtn.HELP)
async def view_help_panel(message: Message):
    """خواند داینامیک متن راهنمای ربات از فایل JSON موجود در json_files"""
    try:
        # تنظیم مسیر فایل راهنما
        json_path = Path("json_files/help.json")

        if not json_path.exists():
            # مسیر بک‌آپ برای محیط داخل کانتینر داکر
            json_path = Path("/app/json_files/help.json")

        if not json_path.exists():
            return await message.answer("⚠️ فایل راهنمای ربات یافت نشد!")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        help_data = data.get("help_text", "متنی یافت نشد.")
        
        # چسباندن خطوط آرایه با کاراکتر خط بعد
        if isinstance(help_data, list):
            help_text = "\n".join(help_data)
        else:
            help_text = help_data

        await message.answer(help_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error reading help.json: {e}", exc_info=True)
        await message.answer("❌ خطایی در بازخوانی اطلاعات راهنما رخ داد.")

@router.message(F.text.startswith("/user_"))
async def view_profile_by_public_id(message: Message, db_session: AsyncSession, state: FSMContext):
    current_state = await state.get_state()
    data = await state.get_data()
    prev_state = data.get("__gift_prev_state__", "")
    
    if current_state == "ManualTransferStates:waiting_for_target_id":
        raise SkipHandler()
        
    # 🛡️ مسدودسازی هوشمند تماشای پروفایل دیگران در حین ارسال گیفت وسط چت
    is_active_process = current_state and any(x in current_state.lower() for x in ["chat", "matching", "questionnaire"])
    was_active_process = current_state and "gift" in current_state.lower() and prev_state and any(x in prev_state.lower() for x in ["chat", "matching", "questionnaire"])

    if is_active_process or was_active_process:
        return await message.answer("⚠️ شما در حال حاضر در یک فرآیند فعال (چت یا مچینگ) هستید. لطفاً اول آن را پایان دهید.")
        
    command_text = message.text.strip()
    public_id = command_text[1:]

    target_user = await crud.get_user_by_public_id(db_session, public_id)

    if not target_user or not target_user.completed_registration:
        await message.answer("⚠️ کاربری با این آیدی یافت نشد یا پروفایلش تکمیل نیست.")
        return

    is_own_profile = (message.from_user.id == target_user.tg_id)
    if target_user.invisible_mode and not is_own_profile:
        from datetime import datetime, timezone
        from matching_bot_project.database.queries.crud import get_user_by_tg_id
        viewer = await get_user_by_tg_id(db_session, message.from_user.id)
        viewer_is_vip = viewer and (
            viewer.is_vip or (
                viewer.vip_expires_at
                and viewer.vip_expires_at > datetime.now(timezone.utc).replace(tzinfo=None)
            )
        )
        if not viewer_is_vip:
            await message.answer("🔒 این کاربر در حالت مخفی است و پروفایلش قابل مشاهده نیست.")
            return
            
    # ✅ استخراج گیفت‌ها و ساخت پروفایل
    gifts_summary = await gift_engine.get_user_gifts_summary(db_session, target_user.tg_id)
    profile_card = build_unified_profile_card(target_user, is_own_profile=is_own_profile, gifts_summary=gifts_summary)

    if is_own_profile:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ویرایش پروفایل", callback_data="edit_profile_triggered", icon_custom_emoji_id="5334882760735598374", style="primary")],
            [InlineKeyboardButton(text="کامنت‌های پروفایل من", callback_data=f"view_comments:{target_user.tg_id}:0", icon_custom_emoji_id="5465300082628763143", style="primary")],
        ])
        profile_card += "\n💡 <i>شما در حال مشاهده پروفایل خودتان هستید.</i>"
    else:
        block_result = await db_session.execute(
            select(BlockList).where(
                BlockList.blocker_id == message.from_user.id,
                BlockList.blocked_id == target_user.tg_id,
            )
        )
        is_blocked = block_result.scalar_one_or_none() is not None
        
        try:
            already_friend = await crud.is_friend(db_session, message.from_user.id, target_user.tg_id)
        except Exception:
            already_friend = False

        caller_active_match = await crud.get_active_match(db_session, message.from_user.id)
        markup = get_user_action_keyboard(
            target_tg_id=target_user.tg_id,
            is_blocked=is_blocked,
            is_friend=already_friend,
            in_active_match=(caller_active_match is not None),
        )

    pages = chunk_html_text(profile_card, max_length=950)
    
    inline_rows = list(markup.inline_keyboard) if markup else []
    if len(pages) > 1:
        nav_row = get_pagination_row(target_id=target_user.tg_id, current_page=0, total_pages=len(pages), is_own=is_own_profile)
        inline_rows.insert(0, nav_row)
        
    final_markup = InlineKeyboardMarkup(inline_keyboard=inline_rows)

    photo_id = getattr(target_user, 'profile_photo_file_id', None)
    photo_sent = False
    
    if photo_id:
        try:
            await message.answer_photo(
                photo=photo_id,
                caption=pages[0],
                parse_mode=ParseMode.HTML,
                reply_markup=final_markup
            )
            photo_sent = True
        except Exception as e:
            logger.error(f"Failed to send profile photo for public id: {e}")

    if not photo_sent:
        await message.answer(
            text=pages[0],
            parse_mode=ParseMode.HTML,
            reply_markup=final_markup
        )

    profile_voice = getattr(target_user, 'profile_voice_file_id', None)
    if profile_voice:
        try:
            await message.answer_voice(
                voice=profile_voice,
                caption="🎵 <b>آهنگ/وویس پروفایل</b>",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send profile voice: {e}")

@router.callback_query(F.data.startswith("prof_page:"))
async def handle_profile_pagination(call: CallbackQuery, db_session: AsyncSession):
    parts = call.data.split(":")
    target_id = int(parts[1])
    page_index = int(parts[2])
    is_own = bool(int(parts[3]))
    
    target_user = await crud.get_user_by_tg_id(db_session, target_id)
    if not target_user:
        return await call.answer("❌ پروفایل این کاربر یافت نشد.", show_alert=True)
        
    # ✅ استخراج گیفت‌ها و ساخت پروفایل
    gifts_summary = await gift_engine.get_user_gifts_summary(db_session, target_user.tg_id)
    profile_card = build_unified_profile_card(target_user, is_own_profile=is_own, gifts_summary=gifts_summary)
    pages = chunk_html_text(profile_card, max_length=950)
    
    if page_index >= len(pages):
        page_index = len(pages) - 1
        
    if is_own:
        is_user_vip = _is_vip_active(target_user)
        inline_rows = [[InlineKeyboardButton(text="ویرایش پروفایل", callback_data="edit_profile_triggered", icon_custom_emoji_id="5334882760735598374", style="primary")]]
        if is_user_vip:
            inline_rows.append([InlineKeyboardButton(text="بخش ویژه VIP", callback_data="vip_panel", icon_custom_emoji_id="5471952986970267163", style="success")])
    else:
        block_result = await db_session.execute(
            select(BlockList).where(BlockList.blocker_id == call.from_user.id, BlockList.blocked_id == target_user.tg_id)
        )
        is_blocked = block_result.scalar_one_or_none() is not None
        try:
            already_friend = await crud.is_friend(db_session, call.from_user.id, target_user.tg_id)
        except Exception:
            already_friend = False

        in_active_match = await crud.is_active_match_partner(db_session, call.from_user.id, target_user.tg_id)

        from matching_bot_project.bot.keyboards.inline import get_user_action_keyboard
        base_kb = get_user_action_keyboard(target_user.tg_id, is_blocked=is_blocked, is_friend=already_friend, in_active_match=in_active_match)
        inline_rows = list(base_kb.inline_keyboard)
        
    if len(pages) > 1:
        from matching_bot_project.bot.core.formatters import get_pagination_row
        nav_row = get_pagination_row(target_id, page_index, len(pages), is_own)
        inline_rows.insert(0, nav_row)
        
    new_kb = InlineKeyboardMarkup(inline_keyboard=inline_rows)
    
    try:
        if call.message.photo or call.message.document:
            await call.message.edit_caption(caption=pages[page_index], parse_mode=ParseMode.HTML, reply_markup=new_kb)
        else:
            await call.message.edit_text(text=pages[page_index], parse_mode=ParseMode.HTML, reply_markup=new_kb)
    except TelegramBadRequest as e:
        if "is not modified" not in str(e).lower():
            logger.error(f"Error editing profile page: {e}")
            
    await call.answer()

@router.callback_query(F.data == "ignore")
async def ignore_callback(call: CallbackQuery):
    """جلوگیری از لودینگ چرخان روی دکمه شماره صفحه"""
    await call.answer()

@router.message(ProfileEditStates.waiting_for_voice, F.voice | F.audio)
async def process_new_voice(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    
    # استخراج اطلاعات فایل صوتی
    if message.voice:
        file_id = message.voice.file_id
        file_size = message.voice.file_size
        duration = message.voice.duration
    else:
        file_id = message.audio.file_id
        file_size = message.audio.file_size
        duration = message.audio.duration

    # 🛡️ گارد محافظتی ۱: محدودیت حجم (حداکثر ۵ مگابایت)
    MAX_SIZE_BYTES = 5 * 1024 * 1024
    if file_size and file_size > MAX_SIZE_BYTES:
        return await message.answer("⚠️ حجم فایل صوتی شما خیلی زیاد است! لطفاً فایلی با حجم کمتر از ۵ مگابایت ارسال کنید.")

    # 🛡️ گارد محافظتی ۲: محدودیت زمان (حداکثر ۶۰ ثانیه)
    MAX_DURATION_SECONDS = 60
    if duration and duration > MAX_DURATION_SECONDS:
        return await message.answer("⚠️ زمان فایل صوتی شما خیلی طولانی است! لطفاً یک ویس یا تکه‌ای از یک آهنگ (زیر ۶۰ ثانیه) ارسال کنید.")

    tg_id = message.from_user.id
    
    await db_session.execute(
        update(User).where(User.tg_id == tg_id).values(profile_voice_file_id=file_id)
    )
    await db_session.commit()
    
    # --- Profile Completion Step ---
    await profile_completion_service.mark_step_done(db_session, tg_id, "voice")
    reward = await profile_completion_service.try_award_completion_reward(db_session, tg_id)
    # -------------------------------
    
    await message.answer("✅ آهنگ/وویس پروفایل شما با موفقیت ثبت شد!", reply_markup=get_main_menu_keyboard())
    
    if reward is not None:
        await message.answer(f"تبریک میگم! پروفایل شما تکمیل شد و {reward} تا سکه به حساب کاربریت اضافه شد.")
        
    await state.clear()