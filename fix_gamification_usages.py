import os
import re

files_to_check = [
    "bot/handlers/interactions.py",
    "bot/handlers/anonymous_chat.py",
    "bot/handlers/discovery.py",
    "bot/handlers/matching.py"
]

for fpath in files_to_check:
    if not os.path.exists(fpath):
        continue
    with open(fpath, "r") as f:
        c = f.read()
    c = re.sub(r'from matching_bot_project\.services\.badge_service import .*?\n', '', c)
    c = re.sub(r'await check_and_award_badges.*?\n', '', c)
    with open(fpath, "w") as f:
        f.write(c)
