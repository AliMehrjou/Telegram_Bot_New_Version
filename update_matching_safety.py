import re

file_path = 'bot/handlers/matching.py'

with open(file_path, 'r') as f:
    content = f.read()

# Add cooldown check in enter_match_queue
cooldown_check = """
    # ── Check block cooldown ──────────────────────────────────────────────────
    cooldown_active = await matching_engine.redis.get(f"user:block_cooldown:{tg_id}")
    if cooldown_active:
        await call.answer("⚠️ شما به دلیل مسدود کردن بیش از حد کاربران، فعلاً نمی‌توانید وارد صف مچ شوید. فردا تلاش کنید.", show_alert=True)
        return

    # ── 2. Fetch user ────────────────────────────────────────────────────────
"""

content = content.replace("    # ── 2. Fetch user ────────────────────────────────────────────────────────", cooldown_check)

with open(file_path, 'w') as f:
    f.write(content)
print("Added block cooldown check to matching.py")
