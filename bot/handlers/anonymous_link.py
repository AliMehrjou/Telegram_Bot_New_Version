import html
import logging
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.dialects.mysql import insert
from sqlalchemy import and_

from matching_bot_project.bot.core.loader import bot
from matching_bot_project.bot.states.states import AnonymousLinkStates, OnboardingStates
from matching_bot_project.database.queries import crud
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
from matching_bot_project.bot.keyboards.inline import get_terms_keyboard
from matching_bot_project.bot.handlers.anonymous_chat import apply_security_filters
from matching_bot_project.database.models.models import BlockList

logger = logging.getLogger(__name__)
router = Router(name="anonymous_link_handler")

@router.message(F.text == "🔗 لینک ناشناس من")
async def generate_my_anon_link(message: Message, db_session: AsyncSession):
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if not user or not user.completed_registration:
        return await message.answer("رفیق، برای گرفتن لینک اختصاصی اول باید ثبت‌نامت رو تموم کنی! 🚀")

    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=anon_{user.public_id}"
    
    caption_text = (
        "سلام! 👋\n"
        "بدون اینکه بشناسمت، هر حرفی تو دلت هست رو از لینک زیر کاملاً ناشناس بهم بگو:\n"
        f"{link}"
    )
    
    await message.answer(
        "🎉 <b>تادااا! اینم لینک اختصاصی تو:</b>\n\n"
        "متن زیر رو کپی کن و بذار تو بیو اینستاگرام یا تلگرامت تا بقیه بتونن بهت پیام ناشناس بدن 😎👇\n\n"
        "🎁 <b>یک خبر خوب:</b> به ازای <b>اولین پیامی</b> که هر فرد جدید از طریق این لینک برات بفرسته، <b>۳ سکه رایگان</b> به عنوان پاداش می‌گیری! 🪙\n\n"
        f"<code>{caption_text}</code>",
        parse_mode="HTML"
    )

@router.message(AnonymousLinkStates.waiting_for_message)
async def receive_anonymous_message(message: Message, state: FSMContext, db_session: AsyncSession):
    sender_id = message.from_user.id
    sender_user = await crud.get_user_by_tg_id(db_session, sender_id)
    is_registered = sender_user and sender_user.completed_registration
    markup = get_main_menu_keyboard() if is_registered else ReplyKeyboardRemove()

    if message.text and message.text.startswith("/"):
        await state.clear()
        return await message.answer("عملیات لغو شد! 🔙 به منوی اصلی برگشتیم.", reply_markup=markup)

    if message.sticker or message.animation or message.contact or message.location or message.dice or message.poll:
        return await message.reply("⚠️ ارسال استیکر، گیف، موقعیت مکانی و مخاطب پشتیبانی نمیشه. لطفاً فقط متن، عکس، ویس یا ویدیو بفرست.")

    data = await state.get_data()
    target_id = data.get("target_anon_id")
    
    if not target_id:
        await state.clear()
        return await message.answer("عجیبه! گیرنده پیام پیدا نشد.", reply_markup=markup)

    # بررسی بلاک بودن فرستنده توسط گیرنده
    if await crud.is_blocked(db_session, blocker_id=target_id, blocked_id=sender_id):
        await state.clear()
        return await message.answer("🚫 <b>متأسفانه شما توسط صاحب این لینک مسدود شده‌اید.</b>", parse_mode="HTML", reply_markup=markup)

    text_to_send = message.text or message.caption or ""
    filtered_text, was_filtered = apply_security_filters(text_to_send)
    
    if was_filtered:
         await state.clear()
         return await message.reply("اوه اوه! ⛔️ خط قرمز ربات! ارسال آیدی تلگرام یا لینک وب‌سایت در پیام ناشناس ممنوعه.", reply_markup=markup)

    is_media = bool(message.photo or message.video or message.voice or message.document)
    file_id = None
    media_type = None
    if is_media:
        if message.photo: file_id, media_type = message.photo[-1].file_id, "photo"
        elif message.video: file_id, media_type = message.video.file_id, "video"
        elif message.voice: file_id, media_type = message.voice.file_id, "voice"
        elif message.document: file_id, media_type = message.document.file_id, "document"

    # ذخیره پیام در دیتابیس صندوق پیام‌های ناشناس
    try:
        await crud.save_anonymous_message(
            session=db_session, sender_id=sender_id, target_id=target_id,
            text=filtered_text, is_media=is_media, media_type=media_type, file_id=file_id
        )
    except Exception as e:
        logger.error(f"DB save error for anon message: {e}")
        await state.clear()
        return await message.answer("متأسفانه مشکلی در ثبت پیام پیش آمد.", reply_markup=markup)

    # 🟢 ایمپورت کردن کلاینت ردیس برای جلوگیری از پاداش تکراری
    from matching_bot_project.bot.core.loader import redis_client, bot
    
    reward_amount = 3
    # یک کلید یکتا برای هر جفت فرستنده و گیرنده
    reward_key = f"rewarded:anon:{target_id}:{sender_id}"
    already_rewarded = await redis_client.get(reward_key)
    
    # 🟢 بررسی اینکه آیا این شخص قبلاً پیام داده یا جدید است
    if not already_rewarded:
        target_user_obj = await crud.get_user_by_tg_id(db_session, target_id)
        if target_user_obj:
            try:
                await crud.process_coin_transaction(
                    db_session, 
                    target_user_obj, 
                    reward_amount, 
                    "پاداش پیام ناشناس از کاربر جدید"
                )
                await db_session.commit()
                # علامت‌گذاری این فرستنده برای این گیرنده
                await redis_client.set(reward_key, "1")
                
                notification_text = (
                    f"💌 <b>یه پیام ناشناس جدید داری!</b> 👀\n\n"
                    f"یکی یه چیزی تو دلش بوده که خواسته بهت بگه.\n"
                    f"🎁 چون اولین باره که این شخص بهت پیام میده، <b>{reward_amount} سکه</b> هم پاداش گرفتی!\n\n"
                    f"برای خوندنش کافیه روی دستور /inbox کلیک کنی."
                )
            except Exception as e:
                logger.error(f"Failed to reward user {target_id} for anon message: {e}")
                notification_text = "💌 <b>یه پیام ناشناس جدید داری!</b> 👀\n\nیکی یه چیزی تو دلش بوده که خواسته بهت بگه. برای خوندنش کافیه روی دستور /inbox کلیک کنی."
    else:
        # کاربری است که قبلاً هم پیام داده، پس پاداشی تعلق نمی‌گیرد
        notification_text = "💌 <b>یه پیام ناشناس جدید داری!</b> 👀\n\nیکی یه چیزی تو دلش بوده که خواسته بهت بگه. برای خوندنش کافیه روی دستور /inbox کلیک کنی."

    # 🛡️ بررسی اینکه آیا گیرنده در دیت است یا خیر
    in_chat = await crud.get_active_match(db_session, target_id)
    
    if not in_chat:
        # 🚀 ارسال نوتیفیکیشن به گیرنده فقط در صورتی که آزاد باشد
        try:
            from aiogram.exceptions import TelegramForbiddenError
            await bot.send_message(target_id, text=notification_text, parse_mode="HTML")
        except TelegramForbiddenError:
            pass # گیرنده ربات را بلاک کرده است
        except Exception as e:
            logger.error(f"Failed to send notification to {target_id}: {e}")

    await state.clear()

    # بازخورد به فرستنده
    if not is_registered:
        reg_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📝 تکمیل ثبت‌نام", callback_data="start_registration")]])
        await message.answer("مرسی ازت! 🌸 پیامت با موفقیت به صندوق پیام‌های ناشناسش رفت.\n\nراستی! اگه خواستی ربات رو امتحان کن. پشیمون نمیشی :)", reply_markup=reg_kb)
    else:
        await message.answer("ایول! 🚀 پیامت با موفقیت به صندوق پیام‌های ناشناسش رفت.", reply_markup=markup)


# ── ۳. صندوق دریافت پیام‌های ناشناس (Inbox) ──
from aiogram.filters import StateFilter
from aiogram.exceptions import TelegramBadRequest

# ── ۳. صندوق دریافت پیام‌های ناشناس (Inbox) ──
@router.message(F.text == "/inbox", StateFilter("*"))
async def open_anonymous_inbox(message: Message, db_session: AsyncSession):
    target_id = message.from_user.id

    # 🛡️ گارد دیت فعال: جلوگیری از خواندن پیام‌ها وسط دیت
    active_match = await crud.get_active_match(db_session, target_id)
    if active_match:
        try:
            await message.delete()  # پاک کردن کامند کاربر برای تمیز ماندن چت
        except TelegramBadRequest:
            pass
        return await message.answer("رفیق، تو الان وسط یه چت و دیت جذابی! 😅 برای اینکه تمرکزت به هم نریزه، صندوق پیام‌های ناشناست فعلاً قفله. هر وقت این چتت تموم شد، سر فرصت بیا پیام‌های ناشناست رو بخون.")

    # واکشی پیام‌های خوانده‌نشده
    unread_messages = await crud.get_unread_anonymous_messages(db_session, target_id)
    
    if not unread_messages:
        return await message.answer("📭 صندوق پیام‌های ناشناس شما در حال حاضر خالیه!")

    await message.answer(f"📬 شما <b>{len(unread_messages)}</b> پیام خوانده‌نشده دارید. در حال دریافت...\n", parse_mode="HTML")

    for msg in unread_messages:
        action_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="↩️ پاسخ دادن", callback_data=f"anon_reply_{msg.sender_tg_id}"),
                InlineKeyboardButton(text="🚫 مسدود کردن", callback_data=f"anon_block_{msg.sender_tg_id}")
            ]
        ])

        target_text = f"👤 <b>فرستنده ناشناس:</b>\n\n{msg.text}" if msg.text else "👤 <b>فرستنده ناشناس (رسانه):</b>"

        try:
            if msg.is_media:
                if msg.media_type == "photo": await message.answer_photo(photo=msg.file_id, caption=target_text, reply_markup=action_kb, parse_mode="HTML")
                elif msg.media_type == "video": await message.answer_video(video=msg.file_id, caption=target_text, reply_markup=action_kb, parse_mode="HTML")
                elif msg.media_type == "voice": await message.answer_voice(voice=msg.file_id, caption=target_text, reply_markup=action_kb, parse_mode="HTML")
                elif msg.media_type == "document": await message.answer_document(document=msg.file_id, caption=target_text, reply_markup=action_kb, parse_mode="HTML")
            else:
                await message.answer(text=target_text, reply_markup=action_kb, parse_mode="HTML")
            
            # مارک کردن پیام به عنوان خوانده‌شده پس از ارسال موفق
            await crud.mark_anonymous_message_as_read(db_session, msg.id)
            
        except Exception as e:
            logger.error(f"Failed to render inbox message {msg.id}: {e}")

# ── ۴. مسدود کردن فرستنده مزاحم ──
@router.callback_query(F.data.startswith("anon_block_"))
async def process_anon_block(call: CallbackQuery, db_session: AsyncSession):
    try: blocked_id = int(call.data.replace("anon_block_", ""))
    except ValueError: return await call.answer("شناسه نامعتبر است.", show_alert=True)
        
    blocker_id = call.from_user.id
    if blocked_id == blocker_id:
        return await call.answer("شما نمی‌توانید خودتان را بلاک کنید!", show_alert=True)

    stmt = insert(BlockList).values(
        blocker_id=blocker_id, blocked_id=blocked_id
    ).on_duplicate_key_update(created_at=BlockList.created_at) #[cite: 1]
    await db_session.execute(stmt)
    await db_session.commit()
    
    new_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="مسدود شده 🚫", callback_data="ignore_action")]
    ])
    try: await call.message.edit_reply_markup(reply_markup=new_kb)
    except Exception: pass
        
    await call.answer("✅ این شخص برای همیشه بلاک شد.", show_alert=True)


# ── ۵. پاسخ دادن به پیام ناشناس ──
@router.callback_query(F.data.startswith("anon_reply_"))
async def process_anon_reply(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    # 🛡️ گارد دیت فعال: جلوگیری از پاسخ‌دهی در زمان دیت
    active_match = await crud.get_active_match(db_session, call.from_user.id) #[cite: 2]
    if active_match:
        return await call.answer("رفیق، تو الان وسط یه چت و دیت جذابی! 😅 اول این دیت رو تموم کن بعد سر فرصت جواب بده.", show_alert=True)

    try: 
        target_id = int(call.data.replace("anon_reply_", ""))
    except ValueError: 
        return await call.answer("شناسه فرستنده نامعتبر است.", show_alert=True)
        
    await state.set_state(AnonymousLinkStates.waiting_for_message)
    await state.update_data(target_anon_id=target_id)
    
    await call.message.answer(
        "✍️ <b>در حال پاسخ دادن به پیام ناشناس...</b>\n\n"
        "پیامت رو بنویس (متن، عکس، ویس و...). پیام شما هم کاملاً ناشناس به دستش می‌رسه! 🤫\n\n"
        "<i>(برای لغو، کافیه دستور /start را بفرستی)</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )
    await call.answer()


# ── هندلرهای کمکی ──
@router.callback_query(F.data == "ignore_action")
async def ignore_action_callback(call: CallbackQuery):
    await call.answer("این کاربر بلاک شده است.", show_alert=False)

@router.callback_query(F.data == "start_registration")
async def start_registration_flow(call: CallbackQuery, state: FSMContext):
    from matching_bot_project.bot.handlers.start import _build_welcome_and_terms_text
    await call.message.answer(
        _build_welcome_and_terms_text(call.from_user.first_name),
        reply_markup=get_terms_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(OnboardingStates.waiting_for_terms_acceptance)
    await call.answer()