import re

def fix_string(filepath):
    with open(filepath, "r") as f:
        c = f.read()

    # Interactions profile card
    c = c.replace('"╔══════════════════════╗\n"', '"╔══════════════════════╗\\n"')
    c = c.replace('"║   👤 پروفایل کاربر   ║\n"', '"║   👤 پروفایل کاربر   ║\\n"')
    c = c.replace('"╠══════════════════════╣\n"', '"╠══════════════════════╣\\n"')
    c = c.replace('f"║ 📝 نام: {name}\n"', 'f"║ 📝 نام: {name}\\n"')
    c = c.replace('f"║ ⚧  جنسیت: {gender}\n"', 'f"║ ⚧  جنسیت: {gender}\\n"')
    c = c.replace('f"║ 🎂 سن: {age} سال\n"', 'f"║ 🎂 سن: {age} سال\\n"')
    c = c.replace('f"║ 🗺  استان: {province}\n"', 'f"║ 🗺  استان: {province}\\n"')
    c = c.replace('f"║ 🏙  شهر: {city}\n"', 'f"║ 🏙  شهر: {city}\\n"')
    c = c.replace('f"║ 💼 بیو: {bio}\n"', 'f"║ 💼 بیو: {bio}\\n"')
    c = c.replace('f"║ 🏷  علایق: {interests}\n"', 'f"║ 🏷  علایق: {interests}\\n"')
    c = c.replace('f"║ 🏆 مچ‌ها: مخفی | ❤️ تفاهم بهترین: مخفی\n"', 'f"║ 🏆 مچ‌ها: مخفی | ❤️ تفاهم بهترین: مخفی\\n"')

    # Questionnaire and match matching
    c = c.replace('f"❓ *سوال {next_q_index + 1} از {TOTAL_QUESTIONS}:*\n\n"', 'f"❓ *سوال {next_q_index + 1} از {TOTAL_QUESTIONS}:*\\n\\n"')
    c = c.replace('f"{next_question.question_text}\n\n"', 'f"{next_question.question_text}\\n\\n"')
    c = c.replace('f"🅰️ گزینه اول: {next_question.option_a}\n"', 'f"🅰️ گزینه اول: {next_question.option_a}\\n"')

    with open(filepath, "w") as f:
        f.write(c)

fix_string("bot/handlers/interactions.py")
fix_string("bot/handlers/questionnaire.py")
