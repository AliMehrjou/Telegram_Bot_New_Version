import re

file_path = 'run.py'

with open(file_path, 'r') as f:
    content = f.read()

if "dp.include_router(gamification.router)" not in content:
    content = content.replace("from matching_bot_project.bot.handlers import start, profile, matching, questionnaire, anonymous_chat, explore, interactions, admin, discovery, profile_edit", "from matching_bot_project.bot.handlers import start, profile, matching, questionnaire, anonymous_chat, explore, interactions, admin, discovery, profile_edit, gamification")

    content = content.replace("dp.include_router(profile_edit.router)", "dp.include_router(gamification.router)\n    dp.include_router(profile_edit.router)")

    with open(file_path, 'w') as f:
        f.write(content)
    print("Registered gamification router in run.py")
else:
    print("gamification router already registered")
