import re

file_path = 'bot/handlers/matching.py'

with open(file_path, 'r') as f:
    content = f.read()

progress_bar_func = """
def build_progress_bar(current: int, total: int = 20) -> str:
    if total == 0:
        return ""
    filled = round((current / total) * 10)
    bar = "▓" * filled + "░" * (10 - filled)
    return f"[{bar}] {current}/{total}\\n\\n"
"""

if "def build_progress_bar" not in content:
    content = content.replace("def get_user_state(user_id: int) -> FSMContext:", progress_bar_func + "\n\ndef get_user_state(user_id: int) -> FSMContext:")

    new_text = """
        progress = build_progress_bar(1, 20)
        try:
            await bot.send_message(
                chat_id=target_id,
                text=f"{progress}❓ *سوال اول:*\\n\\n{first_question.question_text}",
                reply_markup=get_question_reply_keyboard(first_question.id),
                parse_mode="Markdown",
            )"""

    content = re.sub(r'try:\s+await bot.send_message\(\s+chat_id=target_id,\s+text=f"❓ \*سوال اول:\*\\n\\n\{first_question.question_text\}",\s+reply_markup=get_question_reply_keyboard\(first_question.id\),\s+parse_mode="Markdown",\s+\)', new_text, content)

    with open(file_path, 'w') as f:
        f.write(content)
    print("Added progress bar to first question in matching.py")
