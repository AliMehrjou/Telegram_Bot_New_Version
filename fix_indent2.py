import re
with open("bot/handlers/anonymous_chat.py", "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.strip() == "# Award badges check" or line.strip() == "if partner_id:":
        continue
    new_lines.append(line)

with open("bot/handlers/anonymous_chat.py", "w") as f:
    f.writelines(new_lines)
