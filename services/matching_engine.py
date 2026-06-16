import logging
from typing import Optional

import redis.asyncio as aioredis
from redis.exceptions import WatchError

logger = logging.getLogger(__name__)

# TTL for user state keys in Redis. Ensures stale "queuing" or "matched" states 
# expire automatically if the application crashes or cleanup is missed.
_USER_STATE_TTL_SECONDS = 3600  # 1 hour


class MatchingEngine:
    """
    High-performance Matchmaking Engine powered by Redis.
    Supports Random, Gender-Targeted, and Province-Based matchmaking.
    """

    def __init__(self, redis_host: str, redis_port: int, redis_password: str):
        self.redis_url = f"redis://:{redis_password}@{redis_host}:{redis_port}/0"
        self.redis: Optional[aioredis.Redis] = None

    async def connect(self):
        """Initializes the async Redis connection pool."""
        if not self.redis:
            self.redis = aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=50
            )
            logger.info("Connected to Redis Matchmaking engine successfully.")

    async def disconnect(self):
        """Closes the connection pool gracefully."""
        if self.redis:
            await self.redis.close()
            logger.info("Disconnected from Redis Matchmaking engine.")

    def _get_queue_key(self, gender: str, target_gender: Optional[str] = None, province: Optional[str] = None) -> str:
        """Computes the queue key for the current user."""
        if province:
            normalized_province = province.strip().lower().replace(" ", "_")
            return f"match:queue:province:{normalized_province}"
        elif target_gender:
            normalized_target = target_gender.strip().lower()
            normalized_gender = gender.strip().lower()
            return f"match:queue:want_{normalized_target}:{normalized_gender}"
        
        return "match:queue:random"

    def _get_target_queue_key(self, gender: str, target_gender: Optional[str] = None, province: Optional[str] = None) -> str:
        """
        Computes the target queue key where the user should look for candidates.
        Inverts the gender requirements for targeted matching.
        """
        if province:
            normalized_province = province.strip().lower().replace(" ", "_")
            return f"match:queue:province:{normalized_province}"
        elif target_gender:
            normalized_target = target_gender.strip().lower()
            normalized_gender = gender.strip().lower()
            # If I am X looking for Y, I must pop from the queue of Y looking for X
            return f"match:queue:want_{normalized_gender}:{normalized_target}"
        
        return "match:queue:random"

    async def add_to_queue(
        self, 
        tg_id: int, 
        gender: str, 
        target_gender: Optional[str] = None, 
        province: Optional[str] = None
    ) -> bool:
        """
        Registers a user in the matching pool. Removes them from any prior queues 
        and atomically sets their state and pushes them to the correct List.
        """
        await self.connect()
        await self.remove_from_queue(tg_id)

        user_state_key = f"user:state:{tg_id}"
        queue_key = self._get_queue_key(gender, target_gender, province)

        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(user_state_key, mapping={
                "gender": gender,
                "target_gender": target_gender or "",
                "province": province or "",
                "queue_key": queue_key,
                "status": "queuing"
            })
            pipe.expire(user_state_key, _USER_STATE_TTL_SECONDS)
            pipe.lpush(queue_key, str(tg_id))
            await pipe.execute()

        logger.debug(f"User {tg_id} added to queue: {queue_key}")
        return True

    async def remove_from_queue(self, tg_id: int) -> bool:
        """Removes user from their specific Redis queue and deletes their state."""
        await self.connect()
        user_state_key = f"user:state:{tg_id}"
        state = await self.redis.hgetall(user_state_key)

        if not state:
            return False

        queue_key = state.get("queue_key")
        if queue_key:
            await self.redis.lrem(queue_key, 0, str(tg_id))

        await self.redis.delete(user_state_key)
        return True

    async def find_match(
        self, 
        tg_id: int, 
        gender: str, 
        target_gender: Optional[str] = None, 
        province: Optional[str] = None
    ) -> Optional[int]:
        """
        Attempts to match an active user with an opposing queue participant atomically.
        If no match is found, adds the user to the queue to wait.
        """
        await self.connect()
        
        user_state_key = f"user:state:{tg_id}"
        target_queue_key = self._get_target_queue_key(gender, target_gender, province)

        max_attempts = 50
        attempts = 0

        while attempts < max_attempts:
            attempts += 1
            
            # Pop a candidate from the target queue
            candidate_id_str = await self.redis.rpop(target_queue_key)
            if not candidate_id_str:
                break  # Queue is empty

            candidate_id = int(candidate_id_str)

            # Prevent self-matching (edge case if queue configuration overlaps)
            if candidate_id == tg_id:
                continue

            candidate_state_key = f"user:state:{candidate_id}"

            try:
                async with self.redis.pipeline() as pipe:
                    # WATCH the candidate's state to prevent race conditions
                    await pipe.watch(candidate_state_key)
                    candidate_status = await pipe.hget(candidate_state_key, "status")

                    # If candidate cancelled or was already matched, abort and retry
                    if candidate_status != "queuing":
                        await pipe.reset()
                        continue

                    # Begin atomic transaction
                    pipe.multi()

                    # 1. Initialize the FULL state for the current caller as "matched"
                    caller_queue_key = self._get_queue_key(gender, target_gender, province)
                    pipe.hset(user_state_key, mapping={
                        "gender": gender,
                        "target_gender": target_gender or "",
                        "province": province or "",
                        "queue_key": caller_queue_key,
                        "status": "matched",
                        "matched_with": str(candidate_id)
                    })
                    pipe.expire(user_state_key, _USER_STATE_TTL_SECONDS)

                    # 2. Update the candidate's state as "matched"
                    pipe.hset(candidate_state_key, mapping={
                        "status": "matched",
                        "matched_with": str(tg_id)
                    })
                    pipe.expire(candidate_state_key, _USER_STATE_TTL_SECONDS)

                    # Execute transaction
                    await pipe.execute()

                logger.info("Redis Matchmaking succeeded: %s <-> %s", tg_id, candidate_id)
                return candidate_id

            except WatchError:
                # Candidate's state was modified by another process.
                # Safe to retry picking another candidate.
                logger.debug("WatchError on candidate %s during match attempt, skipping.", candidate_id)
                continue

        # No valid candidate found after all attempts; add self to queue
        await self.add_to_queue(tg_id, gender, target_gender, province)
        return None

    async def get_user_match_partner(self, tg_id: int) -> Optional[int]:
        """Utility to retrieve the active partner ID of a matched user."""
        await self.connect()
        partner = await self.redis.hget(f"user:state:{tg_id}", "matched_with")
        return int(partner) if partner else None