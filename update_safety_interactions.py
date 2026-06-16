import re

file_path = 'bot/handlers/interactions.py'

with open(file_path, 'r') as f:
    content = f.read()

# Add block cooldown logic in block_user
cooldown_logic = """
    try:
        await db_session.commit()
        # Add to Redis Sets for atomic evaluation in matching engine
        await redis_client.sadd(f"user:{caller_id}:blocks", str(target_id))

        # Cooldown logic
        block_count_key = f"user:block_count_today:{caller_id}"
        count = await redis_client.incr(block_count_key)
        if count == 1:
            await redis_client.expire(block_count_key, 86400)

        if count >= 3:
            await redis_client.set(f"user:block_cooldown:{caller_id}", "1", ex=86400)

    except IntegrityError:
"""

content = content.replace("    try:\n        await db_session.commit()\n        # Add to Redis Sets for atomic evaluation in matching engine\n        await redis_client.sadd(f\"user:{caller_id}:blocks\", str(target_id))\n    except IntegrityError:", cooldown_logic)

with open(file_path, 'w') as f:
    f.write(content)
print("Added block cooldown logic to interactions.py")
