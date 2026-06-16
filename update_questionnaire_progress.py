import re

file_path = 'bot/handlers/questionnaire.py'

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
    # Insert helper
    content = content.replace("def get_user_state(user_id: int) -> FSMContext:", progress_bar_func + "\n\ndef get_user_state(user_id: int) -> FSMContext:")

    # Modify _deliver_next_question
    # question_text = (
    #     f"❓ *سوال {next_q_index + 1} از {TOTAL_QUESTIONS}:*\n\n"
    #     f"{next_question.question_text}\n\n"
    #     f"🅰️ گزینه اول: {next_question.option_a}\n"
    #     f"🅱️ گزینه دوم: {next_question.option_b}"
    # )
    new_q_text = """
    progress = build_progress_bar(next_q_index + 1, TOTAL_QUESTIONS)
    question_text = (
        f"{progress}"
        f"❓ *سوال {next_q_index + 1} از {TOTAL_QUESTIONS}:*\\n\\n"
        f"{next_question.question_text}\\n\\n"
        f"🅰️ گزینه اول: {next_question.option_a}\\n"
        f"🅱️ گزینه دوم: {next_question.option_b}"
    )"""
    content = re.sub(r'question_text = \([^)]+\)', new_q_text.strip(), content, count=1, flags=re.MULTILINE)

    # For the first question (in handle_successful_match from matching.py) we also need to update it

    with open(file_path, 'w') as f:
        f.write(content)
    print("Added progress bar to questionnaire")
