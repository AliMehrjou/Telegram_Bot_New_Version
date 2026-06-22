# در فایلی مثل gacha.py یا پنل کاربری

import random
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

from matching_bot_project.database.queries import crud

gacha_router = Router(name="gacha_handler")

@gacha_router.message(F.text == "🎁 لوت‌باکس و جوایز")
async def show_gacha_panel(message: Message, db_session: AsyncSession):
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    
    next_level_xp = user.level * 100
    progress_bar_length = 10
    filled_blocks = int((user.xp_points / next_level_xp) * progress_bar_length)
    bar = "🟩" * filled_blocks + "⬜️" * (progress_bar_length - filled_blocks)

    text = (
        "🌟 <b>سیستم پاداش و گاچا بلایند دیت</b>\n\n"
        f"🎖 سطح (Level): <b>{user.level}</b>\n"
        f"✨ نوار تجربه: {bar} ({user.xp_points}/{next_level_xp} XP)\n"
        f"📦 صندوقچه‌های باز نشده: <b>{user.lootbox_count} عدد</b>\n\n"
        "💡 <i>با فعالیت در ربات (مچ شدن، لایک کردن و...) XP بگیر تا لول‌آپ بشی و صندوقچه جایزه بگیری!</i>"
    )
    
    kb = []
    if user.lootbox_count > 0:
        kb.append([InlineKeyboardButton(text="🔓 باز کردن یک صندوقچه", callback_data="open_lootbox")])
        
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@gacha_router.callback_query(F.data == "open_lootbox")
async def process_open_lootbox(call: CallbackQuery, db_session: AsyncSession):
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    
    if user.lootbox_count <= 0:
        await call.answer("📦 شما هیچ صندوقچه‌ای برای باز کردن ندارید!", show_alert=True)
        return
        
    # کم کردن یکی از صندوق‌ها
    user.lootbox_count -= 1
    
    # منطق گاچا (Gacha Drop Rates)
    rand_val = random.random()
    
    if rand_val < 0.05: # 5% شانس
        reward = "👑 1 عدد سهمیه مچ VIP!"
        user.vip_quota += 1
    elif rand_val < 0.25: # 20% شانس
        reward = "🪙 5 عدد سکه طلایی!"
        user.coin_balance += 5
    elif rand_val < 0.60: # 35% شانس
        reward = "🪙 2 عدد سکه طلایی!"
        user.coin_balance += 2
    else: # 40% شانس
        reward = "✨ 50 امتیاز XP ویژه!"
        user.xp_points += 50
        
    await db_session.commit()
    
    animation_text = (
        "🎉 <b>صندوقچه در حال باز شدن است...</b>\n\n"
        "✨ ✨ ✨\n\n"
        f"🎁 تبریک! شما برنده شدید:\n<b>{reward}</b>"
    )
    
    await call.message.edit_text(animation_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="back_to_gacha")]]
    ))
    await call.answer("صندوقچه باز شد!", show_alert=False)