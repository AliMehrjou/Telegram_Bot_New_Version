import re

file_path = 'services/matching_engine.py'

with open(file_path, 'r') as f:
    content = f.read()

# Update add_to_queue signature
content = content.replace("async def add_to_queue(\n        self, \n        tg_id: int, \n        gender: str, \n        target_gender: Optional[str] = None, \n        province: Optional[str] = None\n    ) -> bool:", "async def add_to_queue(\n        self, \n        tg_id: int, \n        gender: str, \n        target_gender: Optional[str] = None, \n        province: Optional[str] = None,\n        interests: Optional[str] = None\n    ) -> bool:")

# Update pipe.hset in add_to_queue
content = content.replace('"province": province or "",\n                "queue_key": queue_key,', '"province": province or "",\n                "interests": interests or "",\n                "queue_key": queue_key,')

# Update find_match signature
content = content.replace("async def find_match(\n        self, \n        tg_id: int, \n        gender: str, \n        target_gender: Optional[str] = None, \n        province: Optional[str] = None\n    ) -> Optional[int]:", "async def find_match(\n        self, \n        tg_id: int, \n        gender: str, \n        target_gender: Optional[str] = None, \n        province: Optional[str] = None,\n        interests: Optional[str] = None\n    ) -> Optional[int]:")

# Update logic inside find_match
# Add interest intersection check before pipe.multi()
interest_check = """
                    # VIP interest filter (optional strictness or just priority)
                    # If caller has interests, prioritize. We implement a simple filter: if interests provided, must intersect at least 1.
                    if interests:
                        candidate_interests = await pipe.hget(candidate_state_key, "interests")
                        if candidate_interests:
                            caller_ints = set(interests.split(","))
                            cand_ints = set(candidate_interests.split(","))
                            if not caller_ints.intersection(cand_ints):
                                await pipe.reset()
                                # Reject this candidate and put them back
                                await self.redis.lpush(target_queue_key, candidate_id_str)
                                continue
"""
content = content.replace('# Begin atomic transaction', interest_check + '\n                    # Begin atomic transaction')

# Update caller hset in find_match
content = content.replace('"province": province or "",\n                        "queue_key": caller_queue_key,', '"province": province or "",\n                        "interests": interests or "",\n                        "queue_key": caller_queue_key,')

# Update fallback add_to_queue call at bottom of find_match
content = content.replace('await self.add_to_queue(tg_id, gender, target_gender, province)', 'await self.add_to_queue(tg_id, gender, target_gender, province, interests)')


with open(file_path, 'w') as f:
    f.write(content)
print("Updated matching engine with interests filter")
