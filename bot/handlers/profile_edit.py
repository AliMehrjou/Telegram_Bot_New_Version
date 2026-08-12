import html
import logging
import json
import os
from typing import Dict, List
from pathlib import Path
from matching_bot_project.bot.core.loader import bot, profile_completion_service
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update 
from datetime import datetime, timezone
from matching_bot_project.bot.states.states import ProfileEditStates
from matching_bot_project.database.queries.crud import update_user_profile
from matching_bot_project.bot.keyboards.reply import get_main_menu_keyboard
from matching_bot_project.database.queries import crud
from matching_bot_project.database.models.models import User
from matching_bot_project.bot.core.loader import bot
from matching_bot_project.bot.core.constants import ReplyBtn

logger = logging.getLogger(__name__)
router = Router(name="profile_edit_handler")

try:
    json_path = Path("json_files/iran_data.json")
    if not json_path.exists():
        json_path = Path("/app/json_files/iran_data.json")

    with open(json_path, "r", encoding="utf-8") as f:
        IRAN_DATA = json.load(f)
    logger.info(f"Successfully loaded {len(IRAN_DATA)} provinces from json_files.")
except Exception as e:
    logger.error(f"Error loading iran_data.json from json_files: {e}")
    IRAN_DATA = {"تهران": ["تهران"], "اصفهان": ["اصفهان"]}


# 🎨 هر علاقه: (برچسب نمایشی، شناسه ایموجی پریمیوم) — دقیقاً هماهنگ با
# inline.py::get_discovery_interests_keyboard تا سراسر ربات یکدست باشه.
INTERESTS = {
    "gaming":  ("گیمینگ",   "5467583879948803288"),
    "music":   ("موزیک",    "5188621441926438751"),
    "travel":  ("سفر",      "5361600266225326825"),
    "movies":  ("فیلم",     "5375464961822695044"),
    "sports":  ("ورزش",     "5373101763442255191"),
    "reading": ("مطالعه",   "5373098009640836781"),
    "cooking": ("آشپزی",    "5388747006451655179"),
    "art":     ("هنر",      "5431456208487716895"),
    "tech":    ("تکنولوژی", "5431376038628171216"),
    "nature":  ("طبیعت",    "5449850741667668411"),
}

def get_interests_keyboard(selected_interests: List[str]) -> InlineKeyboardMarkup:
    keyboard = []
    keys = list(INTERESTS.keys())
    for i in range(0, len(keys), 2):
        row = []
        for j in range(2):
            if i + j < len(keys):
                key = keys[i + j]
                label, emoji_id = INTERESTS[key]
                text = f"{label} ✅" if key in selected_interests else label
                row.append(InlineKeyboardButton(
                    text=text,
                    callback_data=f"interest_{key}",
                    icon_custom_emoji_id=emoji_id,
                    style="success" if key in selected_interests else "primary",
                ))
        keyboard.append(row)
    keyboard.append([
        InlineKeyboardButton(
            text="تأیید و ذخیره",
            callback_data="save_interests",
            icon_custom_emoji_id="5427009714745517609",
            style="success",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_provinces_reply_keyboard() -> ReplyKeyboardMarkup:
    buttons = []
    provinces = list(IRAN_DATA.keys())
    for i in range(0, len(provinces), 2):
        row = [KeyboardButton(text=provinces[i])]
        if i + 1 < len(provinces):
            row.append(KeyboardButton(text=provinces[i+1]))
        buttons.append(row)
    buttons.append([KeyboardButton(text=ReplyBtn.BACK_TO_MENU)])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)


def get_cities_reply_keyboard(province_name: str) -> ReplyKeyboardMarkup:
    buttons = []
    cities = IRAN_DATA.get(province_name, [])
    for i in range(0, len(cities), 2):
        row = [KeyboardButton(text=cities[i])]
        if i + 1 < len(cities):
            row.append(KeyboardButton(text=cities[i+1]))
        buttons.append(row)
    buttons.append([KeyboardButton(text=ReplyBtn.BACK_TO_MENU)])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)


# ==================== هندلرهای مدیریت FSM ====================


@router.message(
    StateFilter(
        ProfileEditStates.editing_bio,
        ProfileEditStates.selecting_interests,
        ProfileEditStates.waiting_for_photo,
        ProfileEditStates.waiting_for_voice,
        ProfileEditStates.updating_province,
        ProfileEditStates.updating_city,
        ProfileEditStates.updating_age,
        ProfileEditStates.waiting_for_gps,
    ),
    F.text == ReplyBtn.BACK_TO_MENU,
)
async def cancel_profile_editing(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ عملیات ویرایش پروفایل لغو شد.", reply_markup=get_main_menu_keyboard())


@router.callback_query(F.data == "edit_profile_triggered")
async def show_edit_menu(call: CallbackQuery, state: FSMContext):
    await state.clear() 
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="تغییر نام", callback_data="change_name", icon_custom_emoji_id="5373012449597335010", style="primary"),
            InlineKeyboardButton(text="ویرایش بیوگرافی", callback_data="change_bio", icon_custom_emoji_id="5470060791883374114", style="primary")
        ],
        [
            InlineKeyboardButton(text="تغییر علایق", callback_data="change_interests", icon_custom_emoji_id="5467583879948803288", style="primary"),
            InlineKeyboardButton(text="تغییر عکس", callback_data="change_photo", icon_custom_emoji_id="5375074927252621134", style="primary")
        ],
        [
            InlineKeyboardButton(text="وضعیت تأهل", callback_data="change_marital", icon_custom_emoji_id="5402100905883488232", style="primary"),
            InlineKeyboardButton(text="تغییر آهنگ", callback_data="change_voice", icon_custom_emoji_id="5188621441926438751", style="primary")
        ],
        [
            InlineKeyboardButton(text="استان/شهر", callback_data="change_location", icon_custom_emoji_id="5399898266265475100", style="primary"),
            InlineKeyboardButton(text="لوکیشن دقیق", callback_data="change_gps", icon_custom_emoji_id="5433825729060018456", style="primary")
        ],
        [
            InlineKeyboardButton(text="تغییر سن", callback_data="change_age", icon_custom_emoji_id="5370999492914976897", style="primary"),
            InlineKeyboardButton(text="کامنت‌های من", callback_data=f"view_comments:{call.from_user.id}:0", icon_custom_emoji_id="5465300082628763143", style="primary")
        ],
    ])

    # --- بخش اضافه شده برای رفع باگ قفل شدن دکمه ---
    
    # متوقف کردن حالت لودینگ دکمه
    await call.answer()
    
    # از آنجایی که پیام قبلی ممکن است عکس باشد (کارت پروفایل)، بهتر است آن را حذف کرده و یک پیام جدید بفرستیم
    try:
        await call.message.delete()
    except Exception:
        pass
        
    await call.message.answer(
        "⚙️ <b>ویرایش پروفایل</b>\n\nلطفاً بخشی که می‌خواهید ویرایش کنید را انتخاب کنید:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "change_bio")
async def start_bio_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEditStates.editing_bio)
    cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=ReplyBtn.BACK_TO_MENU)]], resize_keyboard=True)
    await call.message.answer("✍️ لطفاً بیوگرافی خود را بنویسید (حداکثر ۱۵۰ کاراکتر):", reply_markup=cancel_kb)
    await call.answer()

@router.message(ProfileEditStates.editing_bio)
async def process_bio_input(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    bio_text = message.text or ""
    if bio_text == ReplyBtn.BACK_TO_MENU:
        await state.clear()
        await message.answer("❌ عملیات لغو شد.", reply_markup=get_main_menu_keyboard())
        return

    # 🌟 فیکس باگ کاراکترهای عجیب: جایگزینی کاراکتر تک‌کاراکتری سه‌نقطه با سه نقطه واقعی
    # و حذف فضاهای خالی اضافی
    bio_text = bio_text.replace("…", "...").strip()

    if len(bio_text) > 150:
        await message.answer("⚠️ متن بیوگرافی طولانی است. مجدداً بنویسید (حداکثر ۱۵۰ کاراکتر):")
        return

    # امن‌سازی متن
    safe_bio = html.escape(bio_text)
    
    # 🌟 فیکس باگ افزایش طول: اگر تبدیل به HTML Entity باعث شد طول رشته از 150 بیشتر شود،
    # آن را برش می‌زنیم اما حواسمان هست که کدهای HTML نصفه نمانند (مثل &am)
    if len(safe_bio) > 150:
        safe_bio = safe_bio[:150]
        if '&' in safe_bio[-5:]:
            safe_bio = safe_bio[:safe_bio.rfind('&')]

    tg_id = message.from_user.id
    
    success = await crud.update_user_profile(
        session=db_session, tg_id=tg_id, bio=safe_bio
    )
    if success:
        await db_session.commit()
        
        # --- Profile Completion Step ---
        await profile_completion_service.mark_step_done(db_session, tg_id, "bio")
        reward = await profile_completion_service.try_award_completion_reward(db_session, tg_id)
        # -------------------------------
        
        await message.answer("✅ بیوگرافی شما با موفقیت به‌روزرسانی شد.", reply_markup=get_main_menu_keyboard())
        
        if reward is not None:
            await message.answer(f"تبریک میگم! پروفایل شما تکمیل شد و {reward} تا سکه به حساب کاربریت اضافه شد.")
            
    await state.clear()


@router.callback_query(F.data == "change_interests")
async def start_interests_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEditStates.selecting_interests)
    await state.update_data(selected_interests=[])
    await call.message.answer("اکنون علایق خود را انتخاب کنید:", reply_markup=get_interests_keyboard([]))
    await call.answer()



@router.callback_query(ProfileEditStates.selecting_interests, F.data.startswith("interest_"))
async def toggle_interest(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    interest_key = call.data.removeprefix("interest_")
    data = await state.get_data()
    selected_interests = data.get("selected_interests", [])

    if interest_key in selected_interests:
        # اگر در لیست بود، آن را حذف کن (نیاز به چک کردن سقف نیست)
        selected_interests.remove(interest_key)
    else:
        # برای اضافه‌کردن آیتم جدید، اول وضعیت VIP و سقف را چک کن
        from datetime import datetime, timezone
        user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
        
        if not user:
            await call.answer("❌ اطلاعات کاربری یافت نشد.", show_alert=True)
            return

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        is_vip_active = bool(user.is_vip) and (not user.vip_expires_at or user.vip_expires_at > now_utc)
        
        max_limit = 10 if is_vip_active else 3

        if len(selected_interests) >= max_limit:
            if not is_vip_active:
                await call.answer(f"برای انتخاب بیش از {max_limit} علاقه، اشتراک VIP تهیه کنید 💎", show_alert=True)
            else:
                await call.answer(f"شما حداکثر {max_limit} علاقه را می‌توانید انتخاب کنید.", show_alert=True)
            return
            
        selected_interests.append(interest_key)

    await state.update_data(selected_interests=selected_interests)
    await call.message.edit_reply_markup(reply_markup=get_interests_keyboard(selected_interests))
    await call.answer()

@router.callback_query(ProfileEditStates.selecting_interests, F.data == "save_interests")
async def save_profile_changes(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    data = await state.get_data()
    selected_interests = data.get("selected_interests", [])
    tg_id = call.from_user.id

    # لایه‌ی دفاعی سمت سرور: جلوگیری از ذخیره‌ی دیتای دستکاری‌شده
    user = await crud.get_user_by_tg_id(db_session, tg_id)
    if user:
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        is_vip_active = bool(user.is_vip) and (not user.vip_expires_at or user.vip_expires_at > now_utc)
        max_limit = 10 if is_vip_active else 3

        # اگر بیشتر از سقف ارسال شده بود، فقط مجازها را ذخیره کن
        if len(selected_interests) > max_limit:
            selected_interests = selected_interests[:max_limit]

    interests_str = ",".join(selected_interests) if selected_interests else ""

    success = await crud.update_user_profile(
        session=db_session, tg_id=tg_id, interests=interests_str
    )
    if success:
        await db_session.commit() 
        
        # --- Profile Completion Step ---
        await profile_completion_service.mark_step_done(db_session, tg_id, "tags")
        reward = await profile_completion_service.try_award_completion_reward(db_session, tg_id)
        # -------------------------------
        
        await call.message.answer("✅ علایق شما با موفقیت بروزرسانی شد.", reply_markup=get_main_menu_keyboard())
        
        if reward is not None:
            await call.message.answer(f"تبریک میگم! پروفایل شما تکمیل شد و {reward} تا سکه به حساب کاربریت اضافه شد.")
            
    await state.clear()
    await call.answer()


@router.callback_query(F.data == "change_photo")
async def start_photo_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEditStates.waiting_for_photo)
    cancel_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=ReplyBtn.BACK_TO_MENU)]],
        resize_keyboard=True,
    )
    await call.message.answer("📸 لطفاً عکس جدید پروفایل خود را ارسال کنید:", reply_markup=cancel_kb)
    await call.answer()

@router.message(ProfileEditStates.waiting_for_photo, F.photo)
async def process_new_photo(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    photo_file_id = message.photo[-1].file_id
    tg_id = message.from_user.id

    try:
        from matching_bot_project.bot.core.photo_utils import strip_exif_and_reupload
        from matching_bot_project.bot.core.loader import bot
        clean_file_id = await strip_exif_and_reupload(bot, photo_file_id)
    except Exception:
        clean_file_id = photo_file_id

    await db_session.execute(
        update(User).where(User.tg_id == tg_id).values(profile_photo_file_id=clean_file_id)
    )
    await db_session.commit()

    # --- Profile Completion Step ---
    await profile_completion_service.mark_step_done(db_session, tg_id, "photo")
    reward = await profile_completion_service.try_award_completion_reward(db_session, tg_id)
    # -------------------------------

    await state.clear()
    await message.answer("✅ عکس پروفایل شما با موفقیت به‌روزرسانی شد.", reply_markup=get_main_menu_keyboard())
    
    if reward is not None:
        await message.answer(f"تبریک میگم! پروفایل شما تکمیل شد و {reward} تا سکه به حساب کاربریت اضافه شد.")


@router.message(ProfileEditStates.waiting_for_photo, F.document)
async def process_new_photo_document(message: Message) -> None:
    await message.answer("⚠️ لطفاً عکس را به صورت تصویری (Photo) ارسال کنید، نه به عنوان فایل!")


@router.message(ProfileEditStates.waiting_for_photo)
async def process_photo_invalid(message: Message, state: FSMContext) -> None:
    """Fallback: هر ورودی غیر از Photo/Document در این state — BACK_TO_MENU اینجا نمی‌رسه چون StateFilter بالاتر می‌گیره"""
    await message.answer("⚠️ لطفاً فقط یک تصویر (Photo) ارسال کنید یا از دکمه بازگشت استفاده کنید.")

# ==================== بخش مربوط به ویرایش سن ====================

@router.callback_query(F.data == "change_age")
async def start_age_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEditStates.updating_age)
    cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=ReplyBtn.BACK_TO_MENU)]], resize_keyboard=True)
    await call.message.answer("🎂 لطفاً سن جدید خود را به صورت عدد انگلیسی ارسال کنید:", reply_markup=cancel_kb)
    await call.answer()

@router.message(ProfileEditStates.updating_age)
async def process_new_age(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    age_text = message.text or ""
    if age_text == ReplyBtn.BACK_TO_MENU:
        await state.clear()
        await message.answer("❌ عملیات لغو شد.", reply_markup=get_main_menu_keyboard())
        return

    # FIX PHASE5-HIGH-51: normalize Persian/Arabic digits before parsing.
    # Previously "۲۵".isdigit() returns True (Python treats Persian digits as
    # digits), so the guard passed, then int("۲۵") raised unhandled ValueError
    # → user got no reply. Now we normalize first, then validate.
    from matching_bot_project.bot.core.normalizers import normalize_digits
    normalized_age = normalize_digits(age_text).strip()

    # FIX HIGH-13 (consistency): align range with start.py (18-75) to avoid confusion.
    if not normalized_age.isdigit() or not (18 <= int(normalized_age) <= 75):
        await message.answer("⚠️ لطفاً یک سن معتبر (عددی بین ۱۸ تا ۷۵) وارد کنید:")
        return

    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if user:
        user.age = int(normalized_age)
        await db_session.commit()
        await message.answer("✅ سن شما با موفقیت اصلاح شد.", reply_markup=get_main_menu_keyboard())
    
    await state.clear()


# ==================== بخش مربوط به ویرایش محل سکونت ====================

@router.callback_query(F.data == "change_location")
async def start_location_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEditStates.updating_province)
    await call.message.answer("📍 لطفاً استان جدید خود را از کیبورد زیر انتخاب کنید:", reply_markup=get_provinces_reply_keyboard())
    await call.answer()

@router.message(ProfileEditStates.updating_province)
async def process_edit_province(message: Message, state: FSMContext):
    selected_province = message.text or ""
    if selected_province == ReplyBtn.BACK_TO_MENU:
        await state.clear()
        await message.answer("❌ عملیات لغو شد.", reply_markup=get_main_menu_keyboard())
        return

    if selected_province not in IRAN_DATA:
        await message.answer("⚠️ لطفاً استان خود را فقط از روی کیبورد زیر انتخاب کنید:")
        return

    await state.update_data(province=selected_province)
    await state.set_state(ProfileEditStates.updating_city)
    await message.answer(f"✅ استان {selected_province} انتخاب شد.\n\nاکنون شهر خود را از کیبورد انتخاب کنید:", reply_markup=get_cities_reply_keyboard(selected_province))



@router.message(ProfileEditStates.updating_city)
async def process_edit_city(message: Message, state: FSMContext, db_session: AsyncSession):
    selected_city = message.text or ""
    if selected_city == ReplyBtn.BACK_TO_MENU:
        await state.clear()
        await message.answer("❌ عملیات لغو شد.", reply_markup=get_main_menu_keyboard())
        return

    data = await state.get_data()
    new_province = data.get("province")
    new_city = html.escape(selected_city.strip())

    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if user:
        user.province = new_province
        user.city = new_city
        await db_session.commit()
        await message.answer("🎉 محل سکونت شما با موفقیت اصلاح شد.", reply_markup=get_main_menu_keyboard())
    await state.clear()


# ==================== بخش مربوط به ویرایش وویس ====================

@router.callback_query(F.data == "change_voice")
async def start_voice_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEditStates.waiting_for_voice)
    cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=ReplyBtn.BACK_TO_MENU)]], resize_keyboard=True)
    
    text = (
        "🎙 <b>گرامافون پروفایل شما!</b>\n\n"
        "یک وویس کوتاه (مثلاً معرفی خودت) یا یک تیکه از آهنگ مورد علاقه‌ت رو برام بفرست تا بقیه وقتی پروفایلت رو می‌بینن بتونن گوشش بدن.\n\n"
        "⚠️ <i>لطفاً فقط یک فایل صوتی (Voice یا Audio) ارسال کن.</i>"
    )
    await call.message.answer(text, reply_markup=cancel_kb, parse_mode="HTML")
    await call.answer()

@router.message(ProfileEditStates.waiting_for_voice, F.voice | F.audio)
async def process_new_voice(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    
    if message.voice:
        file_id = message.voice.file_id
    else:
        file_id = message.audio.file_id

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


@router.message(ProfileEditStates.waiting_for_voice)
async def process_voice_invalid(message: Message, state: FSMContext):
    if message.text == ReplyBtn.BACK_TO_MENU:
        await state.clear()
        return await message.answer("❌ عملیات تغییر آهنگ لغو شد.", reply_markup=get_main_menu_keyboard())
        
    await message.answer("⚠️ لطفاً فقط یک فایل صوتی (Voice) یا آهنگ (Audio) ارسال کن!")
    
# ================== کدهای افزودنی ==================
# ---- وضعیت تأهل ----
@router.callback_query(F.data == "change_marital")
async def start_marital_edit(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="مجرد", callback_data="set_marital_single", icon_custom_emoji_id="5373012449597335010", style="primary"),
            InlineKeyboardButton(text="متأهل", callback_data="set_marital_married", icon_custom_emoji_id="5451609943092239685", style="success")
        ]
    ])
    await call.message.edit_text(
        "💍 <b>وضعیت تأهل</b>\n━━━━━━━━━━━━━━━━━━━━\nلطفاً وضعیت فعلی خودت رو انتخاب کن:",
        reply_markup=kb,
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("set_marital_"))
async def process_marital_edit(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = call.data.split("_")
    if len(parts) < 3:
        return await call.answer("⚠️ درخواست نامعتبر.", show_alert=True)
    status = parts[2]
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)

    if not user:
        return await call.answer("⚠️ کاربر یافت نشد.", show_alert=True)
        
    user.marital_status = status
    await db_session.commit()
    await call.answer("✅ وضعیت تأهل شما بروزرسانی شد.", show_alert=True)
    
    # 🌟 بازگشت نرم به منوی ویرایش پروفایل به جای دیلیت کردن پیام
    await show_edit_menu(call, state)
  
# ---- لوکیشن GPS ----
@router.callback_query(F.data == "change_gps")
async def start_gps_edit(call: CallbackQuery, state: FSMContext):
    await state.set_state(ProfileEditStates.waiting_for_gps)  # ← state اختصاصی
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📍 ارسال لوکیشن من", request_location=True)],
        [KeyboardButton(text=ReplyBtn.BACK_TO_MENU)]  # 👈 از متغیر ثابت استفاده شد
    ], resize_keyboard=True, one_time_keyboard=True)
    
    await call.message.delete()
    await call.message.answer(
        "🌍 برای اینکه بتونیم فاصله شما رو با بقیه محاسبه کنیم، لطفاً دکمه زیر را لمس کرده و لوکیشن خود را بفرستید.\n\n"
        "⚠️ حریم خصوصی: لوکیشن دقیق شما به هیچکس نمایش داده نخواهد شد.",
        reply_markup=kb
    )
    await call.answer()

@router.message(ProfileEditStates.waiting_for_gps, F.location)
async def process_gps_location(message: Message, state: FSMContext, db_session: AsyncSession):
    lat = message.location.latitude
    lng = message.location.longitude
    tg_id = message.from_user.id
    
    # 👈 فیکس: استفاده از crud.update_user_location برای ساخت هندسه مکانی در دیتابیس تا کاربر در رادار دیده شود
    success = await crud.update_user_location(db_session, tg_id, lat, lng)
    if success:
        # --- Profile Completion Step ---
        await profile_completion_service.mark_step_done(db_session, tg_id, "gps")
        reward = await profile_completion_service.try_award_completion_reward(db_session, tg_id)
        
        await message.answer("✅ لوکیشن شما با موفقیت روی نقشه ثبت شد.", reply_markup=get_main_menu_keyboard())
        
        if reward is not None:
            await message.answer(f"تبریک میگم! پروفایل شما تکمیل شد و {reward} تا سکه به حساب کاربریت اضافه شد.")
            
    await state.clear()


@router.message(ProfileEditStates.waiting_for_gps)  # ← گارد برای ورودی غیر لوکیشن
async def process_gps_invalid(message: Message, state: FSMContext):
    if message.text == ReplyBtn.BACK_TO_MENU:
        await state.clear()
        return await message.answer("❌ عملیات لغو شد.", reply_markup=get_main_menu_keyboard())
    await message.answer("⚠️ لطفاً فقط از دکمه «ارسال لوکیشن من» استفاده کنید.")

@router.callback_query(F.data == "change_name")
async def start_name_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEditStates.editing_name)
    cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=ReplyBtn.BACK_TO_MENU)]], resize_keyboard=True)
    await call.message.answer("✍️ لطفاً نام جدید خود را بنویسید (بین ۲ تا ۵۰ کاراکتر):", reply_markup=cancel_kb)
    await call.answer()

@router.message(ProfileEditStates.editing_name)
async def process_name_input(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    name_text = message.text or ""
    if name_text == ReplyBtn.BACK_TO_MENU:
        await state.clear()
        await message.answer("❌ عملیات لغو شد.", reply_markup=get_main_menu_keyboard())
        return

    name_text = name_text.strip()
    if not name_text or len(name_text) < 2 or len(name_text) > 50:
        await message.answer("⚠️ لطفاً یک نام معتبر (بین ۲ تا ۵۰ کاراکتر) وارد کنید:")
        return

    safe_name = html.escape(name_text)
    tg_id = message.from_user.id
    
    success = await crud.update_user_profile(
        session=db_session, tg_id=tg_id, first_name=safe_name
    )
    if success:
        await db_session.commit()
        await message.answer("✅ نام شما با موفقیت به‌روزرسانی شد.", reply_markup=get_main_menu_keyboard())
            
    await state.clear()