import secrets
import json
import os
from pathlib import Path
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update as sa_update

from matching_bot_project.database.queries import crud
from matching_bot_project.database.models.models import User

gacha_router = Router(name="gacha_handler")

# FIX L-22: resolve path relative to this file so the handler works regardless of CWD.
# Falls back to /app/json_files/... for the Docker image.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GACHA_CONFIG_PATH = _PROJECT_ROOT / "json_files" / "gacha_config.json"
if not GACHA_CONFIG_PATH.exists():
    GACHA_CONFIG_PATH = Path("/app/json_files/gacha_config.json")

def load_gacha_config():
    """بارگذاری تنظیمات گاچا از فایل JSON"""
    if not os.path.exists(GACHA_CONFIG_PATH):
        return {}
    with open(GACHA_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _generate_gacha_text(user: User, config: dict) -> str:
    """تابع کمکی برای ساخت متن منو با استفاده از ایموجی‌های پریمیوم"""
    
    # 🛡 رفع باگ فریز شدن XP: 
    # از آنجایی که در دیتابیس بعد از لول‌آپ، اکس‌پی کسر و ریست می‌شود، 
    # user.xp_points همان پیشرفت خالص در لول فعلی است.
    required_xp_for_level = user.level * 100
    current_progress_xp = user.xp_points
    
    progress_bar_length = 10
    
    if required_xp_for_level > 0:
        progress_ratio = min(current_progress_xp / required_xp_for_level, 1.0)
    else:
        progress_ratio = 1.0
        
    filled_blocks = int(progress_ratio * progress_bar_length)
    bar = "🟩" * filled_blocks + "⬜️" * (progress_bar_length - filled_blocks)

    texts = config.get("menu_texts", {})
    return (
        texts.get("title", "") +
        texts.get("level_text", "").format(level=user.level) +
        texts.get("xp_text", "").format(bar=bar, xp=current_progress_xp, max_xp=required_xp_for_level) +
        texts.get("lootbox_text", "").format(lootbox=user.lootbox_count) +
        texts.get("footer", "")
    )


@gacha_router.callback_query(F.data == "open_lootbox")
async def process_open_lootbox(call: CallbackQuery, db_session: AsyncSession):
    # 1️⃣ سیستم کول‌داون 2 ثانیه‌ای با ردیس برای جلوگیری از اسپم
    try:
        from matching_bot_project.bot.core.loader import redis_client
        cooldown_key = f"gacha_cooldown:{call.from_user.id}"
        
        if await redis_client.exists(cooldown_key):
            return await call.answer("⏳ لطفاً یک لحظه صبر کن...", show_alert=False)
        
        await redis_client.set(cooldown_key, "1", ex=2)
    except Exception:
        pass 

    # 2️⃣ بررسی وجود کاربر
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user:
        return await call.answer("⚠️ حساب کاربری یافت نشد.", show_alert=True)
        
    if user.lootbox_count <= 0:
        return await call.answer("📦 شما هیچ صندوقچه‌ای برای باز کردن ندارید!", show_alert=True)

    # 3️⃣ آپدیت اتمیک ایمن برای کسر لوت‌باکس
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
    
    config = load_gacha_config()
    rewards = config.get("rewards", [])
    
    if not rewards:
        return await call.answer("⚠️ خطا در بارگذاری جوایز.", show_alert=True)

    # 5️⃣ منطق گاچا (Gacha Drop Rates)
    _RNG_SCALE = 10**9
    rand_val = secrets.randbelow(_RNG_SCALE) / _RNG_SCALE
    cumulative_chance = 0.0
    selected_reward = None
    
    for reward in rewards:
        cumulative_chance += reward.get("chance", 0)
        if rand_val < cumulative_chance:
            selected_reward = reward
            break
            
    if not selected_reward:
        selected_reward = rewards[-1]


    # 6️⃣ اعطای جایزه به کاربر 
    reward_type = selected_reward.get("type")
    amount = selected_reward.get("amount", 0)
    
    # 🌟 بررسی ایونت فعال برای نمایش داینامیک به کاربر (رفع باگ سایلنت بودن ایونت)
    display_amount = amount
    if reward_type == "coins":
        try:
            from matching_bot_project.bot.core.loader import redis_client
            active_multiplier_str = await redis_client.get("bot:active_event_multiplier")
            if active_multiplier_str:
                multiplier = float(active_multiplier_str)
                display_amount = int(amount * multiplier)
        except Exception:
            pass
            
        await crud.process_coin_transaction(db_session, user, amount, "جایزه لوت‌باکس (صندوقچه)")
        
    elif reward_type == "vip_quota":
        await db_session.execute(
            sa_update(User)
            .where(User.tg_id == user.tg_id)
            .values(vip_quota=User.vip_quota + amount)
        )
        await db_session.refresh(user, ["vip_quota"])

    elif reward_type == "xp":
        await crud.add_xp_to_user(db_session, user.tg_id, amount)
        
    await db_session.commit()
    
    # 7️⃣ ساخت متن انیمیشن و جایزه
    msg = selected_reward["message"]
    
    # اگر ایونت فعال است، متنِ جایزه را به روز کن تا کاربر ببیند!
    if reward_type == "coins" and display_amount != amount:
        msg = msg.replace(str(amount), str(display_amount)) + " 🎁 (تأثیر ایونت!)"
        
    animation_template = config.get("animations", {}).get("opening_text", "🎉 شما برنده شدید: {reward}")
    final_text = animation_template.format(reward=msg)
    
    btn_text = config.get("buttons", {}).get("back_to_menu", "🔙 بازگشت به منو")
    
    # 8️⃣ ویرایش پیام
    await call.message.edit_text(
        final_text, 
        parse_mode="HTML", 
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=btn_text, callback_data="back_to_gacha")]]
        )
    )
    await call.answer("صندوقچه باز شد!", show_alert=False)

@gacha_router.message(F.text == "🎁 لوت‌باکس و جوایز")
async def show_gacha_panel(message: Message, db_session: AsyncSession):
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    
    if not user:
        return await message.answer("⚠️ حساب کاربری شما یافت نشد. لطفاً ابتدا /start را ارسال کنید.")
    
    config = load_gacha_config()
    text = _generate_gacha_text(user, config)
    
    kb = []
    if user.lootbox_count > 0:
        btn_text = config.get("buttons", {}).get("open_lootbox", "🔓 باز کردن یک صندوقچه")
        kb.append([InlineKeyboardButton(text=btn_text, callback_data="open_lootbox")])
        
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")


@gacha_router.callback_query(F.data == "open_lootbox")
async def process_open_lootbox(call: CallbackQuery, db_session: AsyncSession):
    # 1️⃣ سیستم کول‌داون 2 ثانیه‌ای با ردیس برای جلوگیری از اسپم
    try:
        from matching_bot_project.bot.core.loader import redis_client
        cooldown_key = f"gacha_cooldown:{call.from_user.id}"
        
        if await redis_client.exists(cooldown_key):
            return await call.answer("⏳ لطفاً یک لحظه صبر کن...", show_alert=False)
        
        await redis_client.set(cooldown_key, "1", ex=2)
    except Exception:
        pass 

    # 2️⃣ بررسی وجود کاربر
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user:
        return await call.answer("⚠️ حساب کاربری یافت نشد.", show_alert=True)
        
    if user.lootbox_count <= 0:
        return await call.answer("📦 شما هیچ صندوقچه‌ای برای باز کردن ندارید!", show_alert=True)

    # 3️⃣ آپدیت اتمیک ایمن برای کسر لوت‌باکس
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
    
    config = load_gacha_config()
    rewards = config.get("rewards", [])
    
    if not rewards:
        return await call.answer("⚠️ خطا در بارگذاری جوایز.", show_alert=True)

    # 5️⃣ منطق گاچا (Gacha Drop Rates)
    # استفاده از secrets برای تولید عدد تصادفی ایمن و غیرقابل پیش‌بینی (فاز 5)
    _RNG_SCALE = 10**9
    rand_val = secrets.randbelow(_RNG_SCALE) / _RNG_SCALE
    cumulative_chance = 0.0
    selected_reward = None
    
    for reward in rewards:
        cumulative_chance += reward.get("chance", 0)
        if rand_val < cumulative_chance:
            selected_reward = reward
            break
            
    if not selected_reward:
        selected_reward = rewards[-1]

    # 6️⃣ اعطای جایزه به کاربر 
    reward_type = selected_reward.get("type")
    amount = selected_reward.get("amount", 0)
    display_amount = amount
    
    if reward_type == "coins":
        # 🌟 بررسی ایونت فعال برای نمایش داینامیک به کاربر (منطق یکپارچه شده)
        try:
            from matching_bot_project.bot.core.loader import redis_client
            active_multiplier_str = await redis_client.get("bot:active_event_multiplier")
            if active_multiplier_str:
                multiplier = float(active_multiplier_str)
                display_amount = int(amount * multiplier)
        except Exception:
            pass
            
        await crud.process_coin_transaction(db_session, user, amount, "جایزه لوت‌باکس (صندوقچه)")
        
    elif reward_type == "vip_quota":
        await db_session.execute(
            sa_update(User)
            .where(User.tg_id == user.tg_id)
            .values(vip_quota=User.vip_quota + amount)
        )
        await db_session.refresh(user, ["vip_quota"])

    elif reward_type == "xp":
        await crud.add_xp_to_user(db_session, user.tg_id, amount)
        
    await db_session.commit()
    
    # 7️⃣ ساخت متن انیمیشن و جایزه
    msg = selected_reward["message"]
    
    # اگر ایونت فعال است، متنِ جایزه را به روز کن تا کاربر ببیند!
    if reward_type == "coins" and display_amount != amount:
        msg = msg.replace(str(amount), str(display_amount)) + " 🎁 (تأثیر ایونت!)"
        
    animation_template = config.get("animations", {}).get("opening_text", "🎉 شما برنده شدید: {reward}")
    final_text = animation_template.format(reward=msg)
    
    btn_text = config.get("buttons", {}).get("back_to_menu", "🔙 بازگشت به منو")
    
    # 8️⃣ ویرایش پیام
    await call.message.edit_text(
        final_text, 
        parse_mode="HTML", 
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=btn_text, callback_data="back_to_gacha")]]
        )
    )
    await call.answer("صندوقچه باز شد!", show_alert=False)

@gacha_router.callback_query(F.data == "back_to_gacha")
async def back_to_gacha_handler(call: CallbackQuery, db_session: AsyncSession):
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user:
        return await call.answer("خطا در بارگذاری.", show_alert=True)
        
    config = load_gacha_config()
    text = _generate_gacha_text(user, config)
    
    kb = []
    if user.lootbox_count > 0:
        btn_text = config.get("buttons", {}).get("open_lootbox", "🔓 باز کردن یک صندوقچه")
        kb.append([InlineKeyboardButton(text=btn_text, callback_data="open_lootbox")])
        
    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception:
        # 🌟 اگر متن تغییر نکرده باشد حداقل تلگرام را از حالت لودینگ خارج می‌کنیم
        pass
    
    await call.answer()