import re

file_path = 'bot/keyboards/inline.py'

with open(file_path, 'r') as f:
    content = f.read()

new_keyboard = """
def get_profile_edit_keyboard(is_vip: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="✏️ ویرایش پروفایل", callback_data="edit_profile")]
    ]
    if is_vip:
        buttons.extend([
            [InlineKeyboardButton(text="👀 بینندگان پروفایل", callback_data="vip_viewers")],
            [InlineKeyboardButton(text="👁 حالت مخفی", callback_data="vip_invisible")],
            [InlineKeyboardButton(text="🔁 مچ مجدد با نفر قبلی", callback_data="vip_rematch")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
"""

content = re.sub(r'def get_profile_edit_keyboard\(\) -> InlineKeyboardMarkup:.*?(?=\n|$)', new_keyboard.strip(), content, flags=re.DOTALL)

with open(file_path, 'w') as f:
    f.write(content)
print("Updated profile edit keyboard for VIP")
