import re

file_path = 'bot/keyboards/inline.py'

with open(file_path, 'r') as f:
    content = f.read()

# Update get_active_chat_controls
new_chat_controls = """
def get_active_chat_controls(partner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛑 پایان دادن به چت", callback_data="end_active_chat")],
        [InlineKeyboardButton(text="🚩 گزارش کاربر", callback_data=f"start_report_{partner_id}")]
    ])
"""

content = re.sub(r'def get_active_chat_controls\(\) -> InlineKeyboardMarkup:.*?(?=\n# --- Main Menu)', new_chat_controls.strip(), content, flags=re.DOTALL)

with open(file_path, 'w') as f:
    f.write(content)
print("Updated active chat controls keyboard")
