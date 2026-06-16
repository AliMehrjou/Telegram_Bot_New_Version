import re

file_path = 'run.py'

with open(file_path, 'r') as f:
    content = f.read()

if "dp.include_router(admin.router)" not in content:
    # Add import
    content = content.replace("from matching_bot_project.bot.handlers import start, profile, matching, questionnaire, anonymous_chat, explore, interactions", "from matching_bot_project.bot.handlers import start, profile, matching, questionnaire, anonymous_chat, explore, interactions, admin")

    # Add router
    content = content.replace("dp.include_router(start.router)", "dp.include_router(admin.router)\n    dp.include_router(start.router)")

    with open(file_path, 'w') as f:
        f.write(content)
    print("Registered admin router in run.py")
else:
    print("Admin router already registered")
