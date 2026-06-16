import re

file_path = 'bot/handlers/interactions.py'

with open(file_path, 'r') as f:
    content = f.read()

# When view_profile fires, log it to redis
redis_log = """
    # VIP: Log profile view
    from matching_bot_project.bot.core.loader import redis_client
    import time
    try:
        now = int(time.time())
        # ZADD user:{viewed_id}:viewers {timestamp} {viewer_id}
        await redis_client.zadd(f"user:{target_id}:viewers", {str(call.from_user.id): now})
        await redis_client.expire(f"user:{target_id}:viewers", 86400 * 7) # 7 days TTL
    except Exception as e:
        logger.warning(f"Could not log profile view: {e}")
"""

content = content.replace("    if not user:\n        await call.answer(\"❌ پروفایل کاربر یافت نشد.\", show_alert=True)\n        return\n", "    if not user:\n        await call.answer(\"❌ پروفایل کاربر یافت نشد.\", show_alert=True)\n        return\n" + redis_log)

with open(file_path, 'w') as f:
    f.write(content)
print("Added profile view logging to interactions.py")
