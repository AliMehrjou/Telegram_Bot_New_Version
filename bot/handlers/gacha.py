import random
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update as sa_update

from matching_bot_project.database.queries import crud
from matching_bot_project.database.models.models import User
# در صورتی که دکمه گاچا را در ReplyBtn اضافه کردید، می‌توانید متن هاردکد را جایگزین کنید

gacha_router = Router(name="gacha_handler")

def _generate_gacha_text(user: User) -> str:
    """تابع کمکی برای جلوگیری از تکرار کد ساخت متن و نوار پیشرفت"""
    # جلوگیری از خطای تقسیم بر صفر در صورتی که لول کاربر 0 باشد
    next_level_xp = max(user.level * 100, 100)
    
    progress_bar_length = 10
    # جلوگیری از Overflow در صورت بیشتر بودن موقت XP از سقف لول
    progress_ratio = min(user.xp_points / next_level_xp, 1.0)
    
    filled_blocks = int(progress_ratio * progress_bar_length)
    bar = "🟩" * filled_blocks + "⬜️" * (progress_bar_length - filled_blocks)

    return (
        "🌟 <b>سیستم پاداش و گاچا بلایند دیت</b>\n\n"
        f"🎖 سطح (Level): <b>{user.level}</b>\n"
        f"✨ نوار تجربه: {bar} ({user.xp_points}/{next_level_xp} XP)\n"
        f"📦 صندوقچه‌های باز نشده: <b>{user.lootbox_count} عدد</b>\n\n"
        "💡 <i>با فعالیت در ربات (مچ شدن، لایک کردن و...) XP بگیر تا لول‌آپ بشی و صندوقچه جایزه بگیری!</i>"
    )

@gacha_router.message(F.text == "🎁 لوت‌باکس و جوایز")
async def show_gacha_panel(message: Message, db_session: AsyncSession):
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    
    if not user:
        return await message.answer("⚠️ حساب کاربری شما یافت نشد. لطفاً ابتدا /start را ارسال کنید.")
    
    text = _generate_gacha_text(user)
    
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

    # آپدیت اتمیک ایمن برای MySQL و PostgreSQL (استفاده از rowcount به جای returning)
    result = await db_session.execute(
        sa_update(User)
        .where(User.tg_id == user.tg_id, User.lootbox_count > 0)
        .values(lootbox_count=User.lootbox_count - 1)
    )
    
    if result.rowcount == 0:
        await db_session.rollback()
        return await call.answer("📦 صندوقچه‌ای برای باز کردن وجود ندارد!", show_alert=True)
        
    await db_session.commit()
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


@gacha_router.callback_query(F.data == "back_to_gacha")
async def back_to_gacha_handler(call: CallbackQuery, db_session: AsyncSession):
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user:
        return await call.answer("خطا در بارگذاری.", show_alert=True)
        
    text = _generate_gacha_text(user)
    
    kb = []
    if user.lootbox_count > 0:
        kb.append([InlineKeyboardButton(text="🔓 باز کردن یک صندوقچه", callback_data="open_lootbox")])
        
    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception:
        pass
    await call.answer()