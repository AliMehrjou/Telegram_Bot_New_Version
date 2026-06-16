import re

file_path = 'services/matching_engine.py'

with open(file_path, 'r') as f:
    content = f.read()

# Update add_to_queue signature
content = content.replace("interests: Optional[str] = None", "interests: Optional[str] = None,\n        min_age: Optional[int] = None,\n        max_age: Optional[int] = None,\n        caller_age: Optional[int] = None")

content = content.replace('"interests": interests or "",\n                "queue_key": queue_key,', '"interests": interests or "",\n                "min_age": min_age or "",\n                "max_age": max_age or "",\n                "age": caller_age or "",\n                "queue_key": queue_key,')

# Update find_match signature
content = content.replace("interests: Optional[str] = None", "interests: Optional[str] = None,\n        min_age: Optional[int] = None,\n        max_age: Optional[int] = None,\n        caller_age: Optional[int] = None")

age_check = """
                    # VIP Age filter (bilateral)
                    candidate_min_age = await pipe.hget(candidate_state_key, "min_age")
                    candidate_max_age = await pipe.hget(candidate_state_key, "max_age")
                    candidate_age_str = await pipe.hget(candidate_state_key, "age")
                    candidate_age = int(candidate_age_str) if candidate_age_str else None

                    if min_age and max_age and candidate_age:
                        if not (min_age <= candidate_age <= max_age):
                            await pipe.reset()
                            await self.redis.lpush(target_queue_key, candidate_id_str)
                            continue

                    if candidate_min_age and candidate_max_age and caller_age:
                        if not (int(candidate_min_age) <= caller_age <= int(candidate_max_age)):
                            await pipe.reset()
                            await self.redis.lpush(target_queue_key, candidate_id_str)
                            continue
"""

content = content.replace('# Begin atomic transaction', age_check + '\n                    # Begin atomic transaction')

content = content.replace('"interests": interests or "",\n                        "queue_key": caller_queue_key,', '"interests": interests or "",\n                        "min_age": min_age or "",\n                        "max_age": max_age or "",\n                        "age": caller_age or "",\n                        "queue_key": caller_queue_key,')

content = content.replace('await self.add_to_queue(tg_id, gender, target_gender, province, interests)', 'await self.add_to_queue(tg_id, gender, target_gender, province, interests, min_age, max_age, caller_age)')

with open(file_path, 'w') as f:
    f.write(content)
print("Updated matching engine with age filters")
