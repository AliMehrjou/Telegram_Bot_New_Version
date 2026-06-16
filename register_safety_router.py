import re

file_path = 'run.py'

with open(file_path, 'r') as f:
    content = f.read()

if "dp.include_router(safety.router)" not in content:
    content = content.replace("from matching_bot_project.bot.handlers import start, profile, matching, questionnaire, anonymous_chat, explore, interactions, admin, discovery, profile_edit, gamification", "from matching_bot_project.bot.handlers import start, profile, matching, questionnaire, anonymous_chat, explore, interactions, admin, discovery, profile_edit, gamification, safety")

    content = content.replace("dp.include_router(gamification.router)", "dp.include_router(safety.router)\n    dp.include_router(gamification.router)")

    with open(file_path, 'w') as f:
        f.write(content)
    print("Registered safety router in run.py")
else:
    print("safety router already registered")
