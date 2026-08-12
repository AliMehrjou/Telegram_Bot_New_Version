import asyncio
import logging
from typing import Optional

import redis.asyncio as aioredis
from redis.exceptions import WatchError
import math
logger = logging.getLogger(__name__)

# FIX PHASE4-HIGH-13: wire metrics so /metrics endpoint reports real match data.
try:
    from matching_bot_project.services.metrics import metrics as _metrics
except Exception:
    _metrics = None

# FIX M-03: align with scheduler.CLOSE_AFTER_SECONDS (24h) so a long-running
# match does not silently lose its `user:state:` key mid-conversation.
_USER_STATE_TTL_SECONDS = 86400  # 24 hours
_QUEUE_TTL_SECONDS = 300  # 5 minutes


class MatchingEngine:
    """
    High-performance Matchmaking Engine powered by Redis.
    Supports Random, Gender-Targeted, and Province-Based matchmaking.
    """

    def __init__(self, redis_host: str, redis_port: int, redis_password: str):
        self.redis_url = f"redis://:{redis_password}@{redis_host}:{redis_port}/0"
        self.redis: Optional[aioredis.Redis] = None
        # FIX M-06: protect `connect()` against concurrent first-call races.
        self._connect_lock = asyncio.Lock()

    async def connect(self):
        """Initializes the async Redis connection pool."""
        # FIX M-06: serialize the first-time initialization so concurrent
        # coroutines don't end up creating multiple pools.
        if self.redis:
            return
        async with self._connect_lock:
            if self.redis:
                return
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
            # FIX L-13: aclose() is the non-deprecated close method in redis-py 5.x.
            await self.redis.aclose()
            self.redis = None
            logger.info("Disconnected from Redis Matchmaking engine.")

    def _get_queue_key(self, gender: str, target_gender: Optional[str] = None, province: Optional[str] = None) -> str:
        """تولید کلید یکپارچه صف برای کاربر بر اساس جنسیت خودش و چیزی که میخواد"""
        norm_gender = gender.strip().lower() if gender else "unknown"
        norm_target = target_gender.strip().lower() if target_gender else "any"
        norm_province = province.strip().lower().replace(" ", "_") if province else "global"
        
        return f"match:queue:{norm_province}:{norm_gender}:wants_{norm_target}"
    
    def _get_target_queue_keys(self, gender: str, target_gender: Optional[str] = None, province: Optional[str] = None) -> list[str]:
        """تولید لیستی از صف‌هایی که کاربر باید در آن‌ها به دنبال پارتنر بگردد (ادغام صف شانسی با هدف‌دار)"""
        norm_gender = gender.strip().lower() if gender else "unknown"
        norm_target = target_gender.strip().lower() if target_gender else "any"
        norm_province = province.strip().lower().replace(" ", "_") if province else "global"

        # اگر جنسیت خاصی مدنظرشه، فقط همون رو بگرد. اگر شانسیه، هر دو جنسیت رو بگرد.
        target_genders = [norm_target] if norm_target != "any" else ["male", "female"]
        
        # طرف مقابل باید یا مشخصاً جنسیت این کاربر رو بخواد، یا زده باشه "شانسی" (any)
        allowed_wants = [norm_gender, "any"]

        provinces_to_search = [norm_province]
        if norm_province != "global":
            # 🌟 اضافه کردن صف سراسری برای مچ شدن با هم‌شهری‌هایی که "مچ تصادفی (رایگان)" زده‌اند
            provinces_to_search.append("global")

        keys = []
        for p in provinces_to_search:
            for tg in target_genders:
                for w in allowed_wants:
                    keys.append(f"match:queue:{p}:{tg}:wants_{w}")
                
        return keys
    
    import asyncio
    import logging
    import math
    from typing import Optional

    import redis.asyncio as aioredis
    from redis.exceptions import WatchError

    logger = logging.getLogger(__name__)

    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """محاسبه سریع فاصله جغرافیایی (کیلومتر) در داخل موتور مچینگ"""
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return 99999.0
        r = 6371.0 # شعاع زمین
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
        return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


    async def remove_from_queue(self, tg_id: int) -> bool:
        """Removes user from their specific Redis queue and deletes their state atomically."""
        await self.connect()
        user_state_key = f"user:state:{tg_id}"

        state = await self.redis.hgetall(user_state_key)
        if not state:
            return False

        queue_key = state.get("queue_key")

        async with self.redis.pipeline(transaction=True) as pipe:
            if queue_key:
                pipe.lrem(queue_key, 0, str(tg_id))
            pipe.delete(user_state_key)
            await pipe.execute()

        return True

    async def set_queue_ttl(self, tg_id: int, ttl_seconds: int = _QUEUE_TTL_SECONDS) -> None:
        """Set a TTL marker for a user's queue entry (5 min default)."""
        await self.connect()
        key = f"match:queue_started:{tg_id}"
        await self.redis.set(key, str(int(asyncio.get_event_loop().time())), ex=ttl_seconds)

    async def get_expired_queue_users(self, limit: int = 100) -> list:
        """
        Find users whose queue TTL has expired (their match:queue_started:* key
        no longer exists but their user:state:* still shows status='queuing').
        """
        await self.connect()
        expired = []
        cursor = "0"
        while True:
            cursor, keys = await self.redis.scan(cursor=cursor, match="user:state:*", count=200)
            if not keys:
                if cursor == "0" or cursor == 0:
                    break
                continue
            for key in keys:
                try:
                    state = await self.redis.hgetall(key)
                    if not state or state.get("status") != "queuing":
                        continue
                    tg_id_str = key.split(":")[-1]
                    still_in_ttl = await self.redis.exists(f"match:queue_started:{tg_id_str}")
                    if not still_in_ttl:
                        expired.append(int(tg_id_str))
                        if len(expired) >= limit:
                            return expired
                except Exception:
                    continue
            if cursor == "0" or cursor == 0:
                break
        return expired

    async def add_to_queue(
        self,
        tg_id: int,
        gender: str,
        target_gender: Optional[str] = None,
        province: Optional[str] = None,
        interests: Optional[str] = None,
        age: Optional[int] = None,
        min_age_filter: Optional[int] = None,
        max_age_filter: Optional[int] = None,
        lat: Optional[float] = None,  # 🌟 پارامتر جدید
        lng: Optional[float] = None   # 🌟 پارامتر جدید
    ) -> bool:
        await self.connect()
        await self.remove_from_queue(tg_id)

        user_state_key = f"user:state:{tg_id}"
        queue_key = self._get_queue_key(gender, target_gender, province)

        norm_target = target_gender.strip().lower() if target_gender else ""
        norm_province = province.strip().lower().replace(" ", "_") if province else ""

        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.lrem(queue_key, 0, str(tg_id))
            pipe.hset(user_state_key, mapping={
                "gender": gender,
                "target_gender": norm_target,
                "province": norm_province,
                "interests": interests or "",
                "age": age or 0,
                "min_age_filter": min_age_filter or 0,
                "max_age_filter": max_age_filter or 99,
                "lat": str(lat) if lat is not None else "", # 🌟 ذخیره عرض جغرافیایی
                "lng": str(lng) if lng is not None else "", # 🌟 ذخیره طول جغرافیایی
                "queue_key": queue_key,
                "status": "queuing"
            })
            pipe.expire(user_state_key, _USER_STATE_TTL_SECONDS)
            pipe.lpush(queue_key, str(tg_id))
            await pipe.execute()

        try:
            await self.set_queue_ttl(tg_id)
        except Exception as e:
            logger.warning("Failed to set queue TTL for %s: %s", tg_id, e)
            
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
        caller_interests_str: Optional[str] = "",
        caller_lat: Optional[float] = None,     # 🌟 پارامتر جدید
        caller_lng: Optional[float] = None,     # 🌟 پارامتر جدید
        is_nearby_search: bool = False          # 🌟 پارامتر جدید
    ) -> Optional[int]:
        await self.connect()

        user_state_key = f"user:state:{tg_id}"
        target_keys = self._get_target_queue_keys(gender, target_gender, province)

        caller_interests = set(caller_interests_str.split(",")) if caller_interests_str else set()
        caller_age = int(caller_age or 0)
        caller_min_age = int(caller_min_age or 0)
        caller_max_age = int(caller_max_age or 99)

        for target_queue_key in target_keys:
            queue_len = await self.redis.llen(target_queue_key)
            if queue_len == 0:
                continue

            MAX_SCAN_LIMIT = min(queue_len, 100)
            BATCH_SIZE = 20

            for offset in range(0, MAX_SCAN_LIMIT, BATCH_SIZE):
                end_idx = queue_len - offset - 1
                start_idx = max(0, queue_len - offset - BATCH_SIZE)

                if end_idx < 0:
                    break

                candidates_batch = await self.redis.lrange(target_queue_key, start_idx, end_idx)
                if not candidates_batch:
                    continue

                candidates_batch.reverse()

                for candidate_id_str in candidates_batch:
                    candidate_id = int(candidate_id_str)

                    if candidate_id == tg_id:
                        await self.redis.lrem(target_queue_key, 0, str(tg_id))
                        continue

                    candidate_state_key = f"user:state:{candidate_id}"
                    peeked_state = await self.redis.hgetall(candidate_state_key)

                    if not peeked_state or peeked_state.get("status") != "queuing":
                        await self.redis.lrem(target_queue_key, 0, candidate_id_str)
                        continue

                    # 🌟 [شروع منطق فیلتر مسافت با GPS]
                    if is_nearby_search and caller_lat is not None and caller_lng is not None:
                        cand_lat_str = peeked_state.get("lat", "")
                        cand_lng_str = peeked_state.get("lng", "")
                        
                        if not cand_lat_str or not cand_lng_str:
                            continue  # کاربر مقابل لوکیشن ندارد
                            
                        try:
                            cand_lat = float(cand_lat_str)
                            cand_lng = float(cand_lng_str)
                            distance = _haversine_distance(caller_lat, caller_lng, cand_lat, cand_lng)
                            
                            if distance > 50.0:  # شعاع ۵۰ کیلومتری برای مچینگ
                                continue
                        except ValueError:
                            continue
                    # 🌟 [پایان منطق مسافت]

                    candidate_age = int(peeked_state.get("age", 0))
                    candidate_min_age = int(peeked_state.get("min_age_filter", 0))
                    candidate_max_age = int(peeked_state.get("max_age_filter", 99))

                    if not (caller_min_age <= candidate_age <= caller_max_age):
                        continue
                    if not (candidate_min_age <= caller_age <= candidate_max_age):
                        continue

                    if is_vip and caller_interests:
                        peeked_interests_str = peeked_state.get("interests", "")
                        peeked_interests = set(peeked_interests_str.split(",")) if peeked_interests_str else set()
                        if not caller_interests.intersection(peeked_interests):
                            continue

                    caller_blocks_key = f"user:{tg_id}:blocks"
                    candidate_blocks_key = f"user:{candidate_id}:blocks"
                    is_candidate_blocked_by_user = await self.redis.sismember(caller_blocks_key, str(candidate_id))
                    is_user_blocked_by_candidate = await self.redis.sismember(candidate_blocks_key, str(tg_id))

                    if is_candidate_blocked_by_user or is_user_blocked_by_candidate:
                        continue

                    try:
                        async with self.redis.pipeline(transaction=True) as pipe:
                            await pipe.watch(candidate_state_key, caller_blocks_key, candidate_blocks_key)
                            candidate_status = await pipe.hget(candidate_state_key, "status")

                            if candidate_status != "queuing":
                                await pipe.reset()
                                continue

                            is_candidate_blocked_recheck = await pipe.sismember(caller_blocks_key, str(candidate_id))
                            is_user_blocked_recheck = await pipe.sismember(candidate_blocks_key, str(tg_id))

                            if is_candidate_blocked_recheck or is_user_blocked_recheck:
                                await pipe.reset()
                                continue

                            pipe.multi()

                            caller_queue_key = self._get_queue_key(gender, target_gender, province)
                            norm_caller_target = target_gender.strip().lower() if target_gender else "any"
                            norm_caller_province = province.strip().lower().replace(" ", "_") if province else "global"

                            pipe.hset(user_state_key, mapping={
                                "gender": gender,
                                "target_gender": norm_caller_target,
                                "province": norm_caller_province,
                                "queue_key": caller_queue_key,
                                "status": "matched",
                                "matched_with": str(candidate_id)
                            })
                            pipe.expire(user_state_key, _USER_STATE_TTL_SECONDS)

                            pipe.hset(candidate_state_key, mapping={
                                "status": "matched",
                                "matched_with": str(tg_id)
                            })
                            pipe.expire(candidate_state_key, _USER_STATE_TTL_SECONDS)

                            pipe.lrem(target_queue_key, 1, candidate_id_str)
                            pipe.lrem(caller_queue_key, 0, str(tg_id))

                            await pipe.execute()

                        return candidate_id

                    except WatchError:
                        continue

        # پاس دادن متغیرهای جدید در زمان بازگشت به صف
        await self.add_to_queue(
            tg_id, gender, target_gender, province,
            interests=caller_interests_str, age=caller_age,
            min_age_filter=caller_min_age, max_age_filter=caller_max_age,
            lat=caller_lat, lng=caller_lng # 🌟
        )
        return None
    
    async def get_user_match_partner(self, tg_id: int) -> Optional[int]:
        """Utility to retrieve the active partner ID of a matched user."""
        await self.connect()
        partner = await self.redis.hget(f"user:state:{tg_id}", "matched_with")
        return int(partner) if partner else None