import re

file_path = 'bot/handlers/matching.py'

with open(file_path, 'r') as f:
    content = f.read()

# Inside handle_successful_match
last_partner_logic = """
    # ── Step 1.5: store last partner for VIP rematch ─────────────────────────
    await matching_engine.redis.set(f"user:{user_one_id}:last_match_partner", str(user_two_id), ex=86400)
    await matching_engine.redis.set(f"user:{user_two_id}:last_match_partner", str(user_one_id), ex=86400)
"""

content = content.replace("    # ── Step 2: cache question pool in Redis ─────────────────────────────────", last_partner_logic + "\n    # ── Step 2: cache question pool in Redis ─────────────────────────────────")

with open(file_path, 'w') as f:
    f.write(content)
print("Added last match partner logic")
