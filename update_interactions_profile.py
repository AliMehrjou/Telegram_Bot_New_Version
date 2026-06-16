import re

file_path = 'bot/handlers/interactions.py'

with open(file_path, 'r') as f:
    content = f.read()

new_build_card = """
def _build_profile_card(user) -> str:
    \"\"\"
    Render a clean, HTML-safe profile card from a User ORM object.
    \"\"\"
    name = html.escape(str(user.first_name or "نامشخص"))
    gender_raw = str(user.gender or "").lower()
    gender = html.escape(_GENDER_DISPLAY.get(gender_raw, html.escape(str(user.gender or "نامشخص"))))
    age = html.escape(str(user.age or "نامشخص"))
    province = html.escape(str(user.province or "نامشخص").replace("_", " "))
    city = html.escape(str(user.city or "نامشخص").replace("_", " "))

    bio = html.escape(str(getattr(user, "bio", None) or "—"))
    interests_raw = getattr(user, "interests", "")
    if interests_raw:
        # Assuming interests are stored as comma-separated keys or values
        interests = html.escape(interests_raw)
    else:
        interests = "—"

    matches = 0 # This would be ideally queried, but we just show placeholder if not provided directly. Or we could remove it from here if we can't query it. But let's leave it as a general format.

    return (
        "╔══════════════════════╗\\n"
        "║   👤 پروفایل کاربر   ║\\n"
        "╠══════════════════════╣\\n"
        f"║ 📝 نام: {name}\\n"
        f"║ ⚧  جنسیت: {gender}\\n"
        f"║ 🎂 سن: {age} سال\\n"
        f"║ 🗺  استان: {province}\\n"
        f"║ 🏙  شهر: {city}\\n"
        f"║ 💼 بیو: {bio}\\n"
        f"║ 🏷  علایق: {interests}\\n"
        "╠══════════════════════╣\\n"
        f"║ 🏆 مچ‌ها: مخفی | ❤️ تفاهم بهترین: مخفی\\n"
        "╚══════════════════════╝"
    )
"""

content = re.sub(r'def _build_profile_card\(user\) -> str:.*?(?=\n\ndef _parse_int_suffix)', new_build_card.strip(), content, flags=re.DOTALL)

with open(file_path, 'w') as f:
    f.write(content)
print("Updated profile card in interactions.py")
