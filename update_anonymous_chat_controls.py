import re

file_path = 'bot/handlers/anonymous_chat.py'

with open(file_path, 'r') as f:
    content = f.read()

# get_active_chat_controls needs partner_id
# We find: reply_markup=get_active_chat_controls() and replace with reply_markup=get_active_chat_controls(partner_id) (or target_id depending on context)
# Looking at anonymous_chat.py, around line 249:
# await bot.send_message(
#                 chat_id=target_id,
#                 text="✅ گفتگو تایید شد. هم‌اکنون می‌توانید مستقیماً پیام دهید.\n...",
#                 reply_markup=get_active_chat_controls(),
#             )

content = content.replace("reply_markup=get_active_chat_controls()", "reply_markup=get_active_chat_controls(partner_id)")

# also replace check_and_award_badges
# we need to import check_and_award_badges and call it at the end of end_active_anonymous_chat
check_badges = """
    # Award badges check
    from matching_bot_project.services.badge_service import check_and_award_badges
    await check_and_award_badges(db_session, redis_client, bot, tg_id)
    if partner_id:
        await check_and_award_badges(db_session, redis_client, bot, partner_id)
"""
content = content.replace('    await _safe_send(\n        partner_id,\n        "🛑 پارتنر شما گفتگو را خاتمه داد. به منوی اصلی بازگشتید.",\n        with_main_menu=True,\n    )', '    await _safe_send(\n        partner_id,\n        "🛑 پارتنر شما گفتگو را خاتمه داد. به منوی اصلی بازگشتید.",\n        with_main_menu=True,\n    )\n' + check_badges)

with open(file_path, 'w') as f:
    f.write(content)
print("Updated anonymous_chat controls and badges")
