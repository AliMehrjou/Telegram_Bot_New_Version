import re

file_path = 'bot/handlers/start.py'

with open(file_path, 'r') as f:
    content = f.read()

new_view_profile = """
@router.message(F.text == "🪬 پروفایل من")
async def view_user_profile(message: Message, db_session: AsyncSession) -> None:
    \"\"\"
    Renders the authenticated user's profile card in HTML.
    \"\"\"
    tg_id = message.from_user.id
    user = await crud.get_user_by_tg_id(db_session, tg_id)

    if not user or not user.completed_registration:
        await message.answer(
            "⚠️ شما هنوز ثبت‌نام نکرده‌اید!\\n"
            "لطفاً دستور /start را ارسال کنید."
        )
        return

    gender_label = GENDER_LABELS.get(user.gender or "", "نامشخص")
    vip_badge = "👑 عضو VIP" if user.is_vip else "🏷️ عضو عادی"

    safe_name = html.escape(user.first_name or "کاربر")
    safe_city = html.escape((user.city or "").replace("_", " "))
    safe_province = html.escape(
        (getattr(user, "province", None) or "").replace("_", " ")
    ) or "—"

    bio = html.escape(str(getattr(user, "bio", None) or "—"))
    interests = html.escape(str(getattr(user, "interests", None) or "—"))

    coin_balance: int = getattr(user, "coin_balance", user.vip_quota)

    profile_card = (
        "╔══════════════════════╗\\n"
        "║   👤 پروفایل کاربر   ║\\n"
        "╠══════════════════════╣\\n"
        f"║ 📝 نام: {safe_name}\\n"
        f"║ ⚧  جنسیت: {gender_label}\\n"
        f"║ 🎂 سن: {user.age} سال\\n"
        f"║ 🗺  استان: {safe_province}\\n"
        f"║ 🏙  شهر: {safe_city}\\n"
        f"║ 💼 بیو: {bio}\\n"
        f"║ 🏷  علایق: {interests}\\n"
        "╠══════════════════════╣\\n"
        f"║ ⚡ وضعیت اشتراک: {vip_badge}\\n"
        f"║ 🪙 موجودی سکه: {coin_balance} سکه\\n"
        "╚══════════════════════╝"
    )

    from matching_bot_project.bot.keyboards.inline import get_profile_edit_keyboard

    try:
        markup = get_profile_edit_keyboard()
    except Exception:
        markup = None

    await message.answer(profile_card, reply_markup=markup)
"""

content = re.sub(r'@router\.message\(F\.text == "👤 پروفایل من"\).*?(?=\n# ═══════════════════════════════════════════════════════════════════════════════\n#  Main Menu — 📍 افراد نزدیک من)', new_view_profile.strip(), content, flags=re.DOTALL)

# Update other references to main menu buttons in start.py
content = content.replace('F.text == "🎯 شروع دیت ناشناس"', 'F.text == "⚡️ شروع دیت ناشناس"')
content = content.replace('F.text == "🔍 جستجوی کاربران"', 'F.text == "🔍 جستجو"')
content = content.replace('F.text == "👥 دوستان من"', 'F.text == "👥 دوستان"')
content = content.replace('F.text == "🪙 سکه‌های من"', 'F.text == "🪙 سکه‌ها"')
content = content.replace('F.text == "📍 افراد نزدیک من"', 'F.text == "📍 نزدیک من"')


with open(file_path, 'w') as f:
    f.write(content)
print("Updated profile card in start.py")
