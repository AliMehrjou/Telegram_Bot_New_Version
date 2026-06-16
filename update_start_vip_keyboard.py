import re

file_path = 'bot/handlers/start.py'

with open(file_path, 'r') as f:
    content = f.read()

content = content.replace("markup = get_profile_edit_keyboard()", "markup = get_profile_edit_keyboard(user.is_vip)")

with open(file_path, 'w') as f:
    f.write(content)
print("Updated start.py to pass is_vip to keyboard")
