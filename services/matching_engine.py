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
        """
        Computes the queue key where the current user will WAIT.
        Format: match:queue:want_{target}:{gender}
        """
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
        Computes the target queue key where the current user should LOOK for candidates.
        Format: match:queue:want_{gender}:{target}
        
        Logic Proof: 
        If User A (female seeking male) is waiting, her queue_key is "want_male:female".
        If User B (male seeking female) is searching, his target_queue_key resolves to
        want_{B_gender}:{B_target} -> want_male:female.
        This perfectly matches User A's queue_key. The symmetry is mathematically sound.
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
        """
        await self.connect()
        
        user_state_key = f"user:state:{tg_id}"
        target_queue_key = self._get_target_queue_key(gender, target_gender, province)

        caller_interests = set(caller_interests_str.split(",")) if caller_interests_str else set()
        caller_age = int(caller_age or 0)
        caller_min_age = int(caller_min_age or 0)
        caller_max_age = int(caller_max_age or 99)

        # استخراج سریع ۱۵ کاندیدای قدیمی‌تر از انتهای صف با یک درخواست (O(1))
        candidates_batch = await self.redis.lrange(target_queue_key, -15, -1)
        
        if not candidates_batch:
            # صف خالی است، کاربر مستقیم وارد صف انتظار می‌شود
            await self.add_to_queue(
                tg_id, gender, target_gender, province,
                interests=caller_interests_str, age=caller_age,
                min_age_filter=caller_min_age, max_age_filter=caller_max_age
            )
            return None

        # مرتب‌سازی برای بررسی از قدیمی‌ترین به جدیدترین
        candidates_batch.reverse()

        for candidate_id_str in candidates_batch:
            candidate_id = int(candidate_id_str)
            
            if candidate_id == tg_id:
                continue

            candidate_state_key = f"user:state:{candidate_id}"
            peeked_state = await self.redis.hgetall(candidate_state_key)
            
            # اگر وضعیت کاندیدا تغییر کرده یا لغو کرده، رد می‌شویم
            if not peeked_state or peeked_state.get("status") != "queuing":
                continue

            candidate_age = int(peeked_state.get("age", 0))
            candidate_min_age = int(peeked_state.get("min_age_filter", 0))
            candidate_max_age = int(peeked_state.get("max_age_filter", 99))

            # بررسی فیلترهای سنی دو طرفه
            if not (caller_min_age <= candidate_age <= caller_max_age):
                continue
            if not (candidate_min_age <= caller_age <= candidate_max_age):
                continue

            # بررسی علایق مشترک برای VIP
            if is_vip and caller_interests:
                peeked_interests_str = peeked_state.get("interests", "")
                peeked_interests = set(peeked_interests_str.split(",")) if peeked_interests_str else set()
                if not caller_interests.intersection(peeked_interests):
                    continue

            # بررسی بلاک بودن قبل از باز کردن تراکنش
            is_candidate_blocked_by_user = await self.redis.sismember(f"user:{tg_id}:blocks", str(candidate_id))
            is_user_blocked_by_candidate = await self.redis.sismember(f"user:{candidate_id}:blocks", str(tg_id))

            if is_candidate_blocked_by_user or is_user_blocked_by_candidate:
                continue

            # 🔴 آغاز تراکنش اتمیک ایمن
            try:
                async with self.redis.pipeline() as pipe:
                    # قفل کردن وضعیت کاندیدا (جلوگیری از تغییر توسط پروسه دیگر)
                    await pipe.watch(candidate_state_key)
                    candidate_status = await pipe.hget(candidate_state_key, "status")

                    if candidate_status != "queuing":
                        await pipe.reset()
                        continue

                    pipe.multi()

                    # آپدیت وضعیت کاربر درخواست‌دهنده
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

                    # آپدیت وضعیت کاندیدا
                    pipe.hset(candidate_state_key, mapping={
                        "status": "matched",
                        "matched_with": str(tg_id)
                    })
                    pipe.expire(candidate_state_key, _USER_STATE_TTL_SECONDS)

                    # 🔴 حذف ایمن از صف: اگر تراکنش फेल شود، کاندیدا در صف باقی می‌ماند
                    pipe.lrem(target_queue_key, 1, candidate_id_str)

                    # اجرای تراکنش
                    await pipe.execute()

                logger.info("Redis Matchmaking succeeded: %s <-> %s", tg_id, candidate_id)
                return candidate_id

            except WatchError:
                # تداخل رخ داد: شخص دیگری همزمان این کاندیدا را مچ کرد.
                # مشکلی نیست، کاندیدای بعدی در لیست را چک می‌کنیم.
                logger.debug("WatchError on candidate %s during match attempt, skipping.", candidate_id)
                continue

        # اگر هیچ کاندیدای مناسبی پیدا نشد، وارد صف می‌شویم
        await self.add_to_queue(
            tg_id, gender, target_gender, province,
            interests=caller_interests_str, age=caller_age,
            min_age_filter=caller_min_age, max_age_filter=caller_max_age
        )
        return None

    async def get_user_match_partner(self, tg_id: int) -> Optional[int]:
        """Utility to retrieve the active partner ID of a matched user."""
        await self.connect()
        partner = await self.redis.hget(f"user:state:{tg_id}", "matched_with")
        return int(partner) if partner else None