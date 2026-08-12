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
    
    # 🛡️ رفع باگ: محاسبه بیس اکس‌پی برای لول فعلی کاربر
    base_xp = max((user.level - 1) * 100, 0)
    next_level_xp = max(user.level * 100, 100)
    
    # محاسبه مقدار پیشرفت کاربر *فقط* در لول فعلی
    current_progress_xp = max(user.xp_points - base_xp, 0)
    required_xp_for_level = next_level_xp - base_xp
    
    progress_bar_length = 10
    
    # جلوگیری از خطای تقسیم بر صفر
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
        texts.get("xp_text", "").format(bar=bar, xp=user.xp_points, max_xp=next_level_xp) +
        texts.get("lootbox_text", "").format(lootbox=user.lootbox_count) +
        texts.get("footer", "")
    )

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
    # 1️⃣ سیستم کول‌داون 2 ثانیه‌ای با ردیس برای جلوگیری از اسپم و فشار به دیتابیس
    try:
        from matching_bot_project.bot.core.loader import redis_client
        cooldown_key = f"gacha_cooldown:{call.from_user.id}"
        
        # اگر کلید در ردیس وجود داشت، یعنی کاربر زودتر از 2 ثانیه کلیک کرده
        if await redis_client.exists(cooldown_key):
            return await call.answer("⏳ لطفاً یک لحظه صبر کن...", show_alert=False)
        
        # ست کردن کول‌داون 2 ثانیه‌ای
        await redis_client.set(cooldown_key, "1", ex=2)
    except Exception:
        pass # در صورت قطع موقت ردیس، ربات متوقف نشود

    # 2️⃣ بررسی وجود کاربر
    user = await crud.get_user_by_tg_id(db_session, call.from_user.id)
    if not user:
        return await call.answer("⚠️ حساب کاربری یافت نشد.", show_alert=True)
        
    if user.lootbox_count <= 0:
        return await call.answer("📦 شما هیچ صندوقچه‌ای برای باز کردن ندارید!", show_alert=True)

    # 3️⃣ آپدیت اتمیک ایمن برای MySQL و PostgreSQL (جلوگیری از باز شدن همزمان 2 باکس)
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
    
    # 4️⃣ بارگذاری تنظیمات از فایل JSON
    config = load_gacha_config()
    rewards = config.get("rewards", [])
    
    if not rewards:
        return await call.answer("⚠️ خطا در بارگذاری جوایز.", show_alert=True)

    # 5️⃣ منطق گاچا (Gacha Drop Rates) - محاسبه شانس بر اساس مقادیر JSON
    # FIX PHASE5-HIGH-65: switched from `random.random()` (Mersenne Twister,
    # predictable from ~624 observed outputs) to `secrets.randbelow` (CSPRNG).
    # For a gacha/lootbox system, predictability is a fairness/security concern.
    _RNG_SCALE = 10**9
    rand_val = secrets.randbelow(_RNG_SCALE) / _RNG_SCALE
    cumulative_chance = 0.0
    selected_reward = None
    
    for reward in rewards:
        cumulative_chance += reward.get("chance", 0)
        if rand_val < cumulative_chance:
            selected_reward = reward
            break
            
    # فالبک امن: اگر مجموع شانس‌ها در JSON کمتر از 1 بود، آخرین جایزه رو بده
    if not selected_reward:
        selected_reward = rewards[-1]

    # 6️⃣ اعطای جایزه به کاربر بر اساس نوع (Type) که در JSON تعریف شده
    reward_type = selected_reward.get("type")
    amount = selected_reward.get("amount", 0)
    
    if reward_type == "vip_quota":
        # FIX HIGH-31: previously both a Core UPDATE and an ORM attribute mutation
        # were applied. On commit, the ORM flush would overwrite the Core UPDATE
        # with the stale in-memory value, causing lost updates under concurrency.
        # Now we use ONLY the atomic Core UPDATE and refresh the in-memory value.
        await db_session.execute(
            sa_update(User)
            .where(User.tg_id == user.tg_id)
            .values(vip_quota=User.vip_quota + amount)
        )
        # Refresh local object so the user-facing display matches the DB.
        await db_session.refresh(user, ["vip_quota"])

    elif reward_type == "coins":
        await crud.process_coin_transaction(db_session, user, amount, "جایزه لوت‌باکس (صندوقچه)")
        
    elif reward_type == "xp":
        await crud.add_xp_to_user(db_session, user.tg_id, amount)
        
    await db_session.commit()
    
    # 7️⃣ ساخت متن انیمیشن و جایزه با ایموجی‌های پریمیوم از طریق JSON
    animation_template = config.get("animations", {}).get("opening_text", "🎉 شما برنده شدید: {reward}")
    final_text = animation_template.format(reward=selected_reward["message"])
    
    btn_text = config.get("buttons", {}).get("back_to_menu", "🔙 بازگشت به منو")
    
    # 8️⃣ ویرایش پیام و نمایش دکمه بازگشت
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