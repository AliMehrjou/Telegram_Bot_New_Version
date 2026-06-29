import random
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from matching_bot_project.database.queries import crud

gacha_router = Router(name="gacha_handler")

@gacha_router.message(F.text == "🎁 لوت‌باکس و جوایز")
async def show_gacha_panel(message: Message, db_session: AsyncSession):
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    
    # جلوگیری از کرش اگر کاربر ثبت‌نام نکرده باشد
    if not user:
        return await message.answer("⚠️ حساب کاربری شما یافت نشد. لطفاً ابتدا /start را ارسال کنید.")
    
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
    
    if not user:
        return await call.answer("⚠️ حساب کاربری یافت نشد.", show_alert=True)
        
    if user.lootbox_count <= 0:
        return await call.answer("📦 شما هیچ صندوقچه‌ای برای باز کردن ندارید!", show_alert=True)

    # FIX: atomic check-and-decrement — جلوگیری از race condition دوبار کلیک
    from sqlalchemy import update as sa_update
    result = await db_session.execute(
        sa_update(type(user))
        .where(type(user).tg_id == user.tg_id, type(user).lootbox_count > 0)
        .values(lootbox_count=type(user).lootbox_count - 1)
        .returning(type(user).lootbox_count)
    )
    updated = result.fetchone()
    if not updated:
        await db_session.rollback()
        return await call.answer("📦 صندوقچه‌ای برای باز کردن وجود ندارد!", show_alert=True)
    await db_session.commit()

    # refresh برای خواندن مقدار جدید
    await db_session.refresh(user)
    
    # منطق گاچا (Gacha Drop Rates)
    rand_val = random.random()
    
    if rand_val < 0.05: # 5% شانس
        reward = "👑 1 عدد سهمیه مچ VIP!"
        user.vip_quota += 1
    elif rand_val < 0.25: # 20% شانس
        reward = "🪙 5 عدد سکه طلایی!"
        await crud.process_coin_transaction(db_session, user, 5, "جایزه لوت‌باکس (صندوقچه)")
    elif rand_val < 0.60: # 35% شانس
        reward = "🪙 2 عدد سکه طلایی!"
        await crud.process_coin_transaction(db_session, user, 2, "جایزه لوت‌باکس (صندوقچه)")
    else: # 40% شانس
        reward = "✨ 50 امتیاز XP ویژه!"
        # استفاده از تابع استاندارد برای بررسی لول‌آپ و اعطای خودکار صندوقچه در صورت رد شدن از 100
        await crud.add_xp_to_user(db_session, user.tg_id, 50)
        
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


# اضافه شدن هندلر برای دکمه بازگشت (تا منو دوباره رفرش بشه)
@gacha_router.callback_query(F.data == "back_to_gacha")
async def back_to_gacha_handler(call: CallbackQuery, db_session: AsyncSession):
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user:
        return await call.answer("خطا در بارگذاری.", show_alert=True)
        
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
        
    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception:
        pass
    await call.answer()