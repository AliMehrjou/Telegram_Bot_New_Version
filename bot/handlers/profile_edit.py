import html
import logging
import json
import os
from typing import Dict, List
from pathlib import Path

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update 

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


INTERESTS = {
    "gaming": "🎮 گیمینگ",
    "music": "🎵 موزیک",
    "travel": "✈️ سفر",
    "movies": "🎬 فیلم",
    "sports": "⚽️ ورزش",
    "reading": "📚 مطالعه",
    "cooking": "🍳 آشپزی",
    "art": "🎨 هنر",
    "tech": "💻 تکنولوژی",
    "nature": "🌿 طبیعت",
}

def get_interests_keyboard(selected_interests: List[str]) -> InlineKeyboardMarkup:
    keyboard = []
    keys = list(INTERESTS.keys())
    for i in range(0, len(keys), 2):
        row = []
        for j in range(2):
            if i + j < len(keys):
                key = keys[i + j]
                label = INTERESTS[key]
                if key in selected_interests:
                    label += " ✅"
                row.append(InlineKeyboardButton(text=label, callback_data=f"interest_{key}"))
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="✅ تایید و ذخیره", callback_data="save_interests")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_provinces_reply_keyboard() -> ReplyKeyboardMarkup:
    buttons = []
    provinces = list(IRAN_DATA.keys())
    for i in range(0, len(provinces), 2):
        row = [KeyboardButton(text=provinces[i])]
        if i + 1 < len(provinces):
            row.append(KeyboardButton(text=provinces[i+1]))
        buttons.append(row)
    buttons.append([KeyboardButton(text="🔙 برگشت به منوی اصلی")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)


def get_cities_reply_keyboard(province_name: str) -> ReplyKeyboardMarkup:
    buttons = []
    cities = IRAN_DATA.get(province_name, [])
    for i in range(0, len(cities), 2):
        row = [KeyboardButton(text=cities[i])]
        if i + 1 < len(cities):
            row.append(KeyboardButton(text=cities[i+1]))
        buttons.append(row)
    buttons.append([KeyboardButton(text="🔙 برگشت به منوی اصلی")])
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
            [InlineKeyboardButton(text="✍️ ویرایش بیوگرافی", callback_data="change_bio")],
            [
                InlineKeyboardButton(text="🎮 تغییر علایق", callback_data="change_interests"),
                InlineKeyboardButton(text="📸 تغییر عکس", callback_data="change_photo")
            ],
            [
                InlineKeyboardButton(text="💍 تغییر وضعیت تأهل", callback_data="change_marital"), # جدید
                InlineKeyboardButton(text="🎵 تغییر آهنگ", callback_data="change_voice")
            ],
            [
                InlineKeyboardButton(text="📍 استان/شهر", callback_data="change_location"),
                InlineKeyboardButton(text="🌍 ثبت لوکیشن دقیق", callback_data="change_gps") # جدید
            ],
            [InlineKeyboardButton(text="🎂 تغییر سن", callback_data="change_age")],
            [InlineKeyboardButton(text="💬 کامنت‌های پروفایل من", callback_data=f"view_comments:{call.from_user.id}:0")]
        ])
    
    text_content = "⚙️ <b>کدام بخش از پروفایل خود را می‌خواهید ویرایش کنید؟</b>"
    
    if call.message.photo:
        await call.message.delete()
        await call.message.answer(text_content, reply_markup=keyboard, parse_mode="HTML")
    else:
        try:
            await call.message.edit_text(text_content, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            await call.message.answer(text_content, reply_markup=keyboard, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "change_bio")
async def start_bio_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEditStates.editing_bio)
    cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 برگشت به منوی اصلی")]], resize_keyboard=True)
    await call.message.answer("✍️ لطفاً بیوگرافی خود را بنویسید (حداکثر ۱۵۰ کاراکتر):", reply_markup=cancel_kb)
    await call.answer()


@router.message(ProfileEditStates.editing_bio)
async def process_bio_input(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    bio_text = message.text or ""
    if bio_text == ReplyBtn.BACK_TO_MENU:
        await state.clear()
        await message.answer("❌ عملیات لغو شد.", reply_markup=get_main_menu_keyboard())
        return

    if len(bio_text) > 150:
        await message.answer("⚠️ متن بیوگرافی طولانی است. مجدداً بنویسید:")
        return

    safe_bio = html.escape(bio_text.strip())
    
    success = await update_user_profile(
        session=db_session, tg_id=message.from_user.id, bio=safe_bio
    )
    if success:
        await db_session.commit()
        await message.answer("✅ بیوگرافی شما با موفقیت به‌روزرسانی شد.", reply_markup=get_main_menu_keyboard())
    await state.clear()


@router.callback_query(F.data == "change_interests")
async def start_interests_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEditStates.selecting_interests)
    await state.update_data(selected_interests=[])
    await call.message.answer("اکنون علایق خود را انتخاب کنید:", reply_markup=get_interests_keyboard([]))
    await call.answer()


@router.callback_query(ProfileEditStates.selecting_interests, F.data.startswith("interest_"))
async def toggle_interest(call: CallbackQuery, state: FSMContext) -> None:
    interest_key = call.data.removeprefix("interest_")
    data = await state.get_data()
    selected_interests = data.get("selected_interests", [])

    if interest_key in selected_interests:
        selected_interests.remove(interest_key)
    else:
        selected_interests.append(interest_key)

    await state.update_data(selected_interests=selected_interests)
    await call.message.edit_reply_markup(reply_markup=get_interests_keyboard(selected_interests))
    await call.answer()


@router.callback_query(ProfileEditStates.selecting_interests, F.data == "save_interests")
async def save_profile_changes(call: CallbackQuery, state: FSMContext, db_session: AsyncSession) -> None:
    data = await state.get_data()
    selected_interests = data.get("selected_interests", [])
    interests_str = ",".join(selected_interests) if selected_interests else ""

    success = await update_user_profile(
        session=db_session, tg_id=call.from_user.id, interests=interests_str
    )
    if success:
        await db_session.commit() 
        await call.message.answer("✅ علایق شما با موفقیت بروزرسانی شد.", reply_markup=get_main_menu_keyboard())
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

    await db_session.execute(
        update(User).where(User.tg_id == tg_id).values(profile_photo_file_id=photo_file_id)
    )
    await db_session.commit()

    # اول state رو clear کن، بعد answer بفرست — جلوگیری از آویزون موندن state
    await state.clear()
    await message.answer("✅ عکس پروفایل شما با موفقیت به‌روزرسانی شد.", reply_markup=get_main_menu_keyboard())


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
    cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 برگشت به منوی اصلی")]], resize_keyboard=True)
    await call.message.answer("🎂 لطفاً سن جدید خود را به صورت عدد انگلیسی ارسال کنید:", reply_markup=cancel_kb)
    await call.answer()

@router.message(ProfileEditStates.updating_age)
async def process_new_age(message: Message, state: FSMContext, db_session: AsyncSession) -> None:
    age_text = message.text or ""
    if age_text == ReplyBtn.BACK_TO_MENU:
        await state.clear()
        await message.answer("❌ عملیات لغو شد.", reply_markup=get_main_menu_keyboard())
        return

    if not age_text.isdigit() or not (18 <= int(age_text) <= 99):
        await message.answer("⚠️ لطفاً یک سن معتبر (عددی بین ۱۸ تا ۹۹) وارد کنید:")
        return
        
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if user:
        user.age = int(age_text)
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
    cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 برگشت به منوی اصلی")]], resize_keyboard=True)
    
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
    
    await message.answer("✅ آهنگ/وویس پروفایل شما با موفقیت ثبت شد!", reply_markup=get_main_menu_keyboard())
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
            InlineKeyboardButton(text="مجرد 🙋", callback_data="set_marital_single"),
            InlineKeyboardButton(text="متأهل 💍", callback_data="set_marital_married")
        ]
    ])
    await call.message.edit_text("💍 لطفاً وضعیت تأهل خود را انتخاب کنید:", reply_markup=kb)

@router.callback_query(F.data.startswith("set_marital_"))
async def process_marital_edit(call: CallbackQuery, db_session: AsyncSession):
    status = call.data.split("_")[2]
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if user:
        user.marital_status = status
        await db_session.commit()
        await call.answer("✅ وضعیت تأهل شما بروزرسانی شد.", show_alert=True)
        await call.message.delete()
        await bot.send_message(
            chat_id=call.from_user.id, 
            text="به منوی اصلی بازگشتید.", 
            reply_markup=get_main_menu_keyboard()
        )
        
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

@router.message(ProfileEditStates.waiting_for_gps, F.location)  # ← state filter اضافه شد
async def process_gps_location(message: Message, state: FSMContext, db_session: AsyncSession):
    lat = message.location.latitude
    lng = message.location.longitude
    
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    if user:
        user.location_lat = lat
        user.location_lng = lng
        await db_session.commit()
        await message.answer("✅ لوکیشن شما با موفقیت روی نقشه ثبت شد.", reply_markup=get_main_menu_keyboard())
    await state.clear()

@router.message(ProfileEditStates.waiting_for_gps)  # ← گارد برای ورودی غیر لوکیشن
async def process_gps_invalid(message: Message, state: FSMContext):
    if message.text == ReplyBtn.BACK_TO_MENU:
        await state.clear()
        return await message.answer("❌ عملیات لغو شد.", reply_markup=get_main_menu_keyboard())
    await message.answer("⚠️ لطفاً فقط از دکمه «ارسال لوکیشن من» استفاده کنید.")