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
        
        # Random Date allows any gender matching without strict filters.
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
        
        # Random Date allows any gender matching without strict filters.
        return "match:queue:random"

    async def add_to_queue(
        self, 
        tg_id: int, 
        gender: str, 
        target_gender: Optional[str] = None, 
        province: Optional[str] = None,
        interests: Optional[str] = None,
        age: Optional[int] = None,
        min_age_filter: Optional[int] = None,
        max_age_filter: Optional[int] = None
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
                "interests": interests or "",
                "age": age or 0,
                "min_age_filter": min_age_filter or 0,
                "max_age_filter": max_age_filter or 99,
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
        province: Optional[str] = None,
        is_vip: bool = False,
        caller_age: Optional[int] = 0,
        caller_min_age: Optional[int] = 0,
        caller_max_age: Optional[int] = 99,
        caller_interests_str: Optional[str] = ""
    ) -> Optional[int]:
        """
        Attempts to match an active user with an opposing queue participant atomically.
        If no match is found, adds the user to the queue to wait.
        """
        await self.connect()
        
        user_state_key = f"user:state:{tg_id}"
        target_queue_key = self._get_target_queue_key(gender, target_gender, province)

        # Use passed parameters for VIP matching
        caller_interests = set(caller_interests_str.split(",")) if caller_interests_str else set()

        caller_age = int(caller_age or 0)
        caller_min_age = int(caller_min_age or 0)
        caller_max_age = int(caller_max_age or 99)

        max_attempts = 50
        attempts = 0

        while attempts < max_attempts:
            attempts += 1
            
            # We peek at the queue to find a match that satisfies bilateral age filters and shared interests for VIPs
            candidate_id_str = None
            queue_length = await self.redis.llen(target_queue_key)

            # Check up to 15 candidates in the queue to find a valid match
            for i in range(min(15, queue_length)):
                # LINDEX counts from head (left). We want to check from the tail (right) which is next to pop.
                idx = -(i + 1)
                peeked_id_str = await self.redis.lindex(target_queue_key, idx)
                if not peeked_id_str:
                    continue

                peeked_state_key = f"user:state:{peeked_id_str}"
                peeked_state = await self.redis.hgetall(peeked_state_key)
                if not peeked_state:
                    continue

                candidate_age = int(peeked_state.get("age", 0))
                candidate_min_age = int(peeked_state.get("min_age_filter", 0))
                candidate_max_age = int(peeked_state.get("max_age_filter", 99))

                # Check bilateral age constraints
                if not (caller_min_age <= candidate_age <= caller_max_age):
                    continue
                if not (candidate_min_age <= caller_age <= candidate_max_age):
                    continue

                # If VIP, try to match interests if both have them
                if is_vip and caller_interests:
                    peeked_interests_str = peeked_state.get("interests", "")
                    peeked_interests = set(peeked_interests_str.split(",")) if peeked_interests_str else set()

                    if not caller_interests.intersection(peeked_interests):
                        # Skip if no shared interests for VIP
                        continue

                # Found a valid candidate, pull them out of the list
                await self.redis.lrem(target_queue_key, 1, peeked_id_str)
                candidate_id_str = peeked_id_str
                break

            # If no candidate found after peeking constraints, break
            if not candidate_id_str:
                break

            candidate_id = int(candidate_id_str)

            # Prevent self-matching
            if candidate_id == tg_id:
                continue

            # Check if users blocked each other BEFORE pipeline
            # SISMEMBER user:{id}:blocks {target_id}
            is_candidate_blocked_by_user = await self.redis.sismember(f"user:{tg_id}:blocks", str(candidate_id))
            is_user_blocked_by_candidate = await self.redis.sismember(f"user:{candidate_id}:blocks", str(tg_id))

            if is_candidate_blocked_by_user or is_user_blocked_by_candidate:
                logger.debug(f"Block collision detected between {tg_id} and {candidate_id}. Discarding match.")
                # LPUSH candidate back to queue
                await self.redis.lpush(target_queue_key, candidate_id_str)
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

        # No valid candidate found after all attempts; add self to queue.
        # Ensure we fetch existing state data to preserve them when adding to queue
        await self.add_to_queue(
            tg_id,
            gender,
            target_gender,
            province,
            interests=caller_interests_str,
            age=caller_age,
            min_age_filter=caller_min_age,
            max_age_filter=caller_max_age
        )
        return None

    async def get_user_match_partner(self, tg_id: int) -> Optional[int]:
        """Utility to retrieve the active partner ID of a matched user."""
        await self.connect()
        partner = await self.redis.hget(f"user:state:{tg_id}", "matched_with")
        return int(partner) if partner else None