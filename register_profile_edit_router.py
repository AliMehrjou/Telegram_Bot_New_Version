import re

file_path = 'run.py'

with open(file_path, 'r') as f:
    content = f.read()

if "dp.include_router(profile_edit.router)" not in content:
    content = content.replace("from matching_bot_project.bot.handlers import start, profile, matching, questionnaire, anonymous_chat, explore, interactions, admin, discovery", "from matching_bot_project.bot.handlers import start, profile, matching, questionnaire, anonymous_chat, explore, interactions, admin, discovery, profile_edit")

    content = content.replace("dp.include_router(discovery.router)", "dp.include_router(profile_edit.router)\n    dp.include_router(discovery.router)")

    with open(file_path, 'w') as f:
        f.write(content)
    print("Registered profile_edit router in run.py")
else:
    print("profile_edit router already registered")
