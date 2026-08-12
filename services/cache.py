"""
services/cache.py

v3.1 SCALING: Redis-based caching layer for read-heavy data.

Why?
- At 200K users, profile views, catalog reads, and admin channel checks
  generate thousands of identical SELECT queries per second.
- Caching these in Redis with TTL reduces DB load by 60-80%.

Cached items:
- User profiles (CACHE_USER_PROFILE_TTL = 5 min)
- Tag catalog (CACHE_TAG_CATALOG_TTL = 1 hour)
- Gift catalog (CACHE_GIFT_CATALOG_TTL = 1 hour)
- VIP plans (CACHE_VIP_PLANS_TTL = 1 hour)
- Admin channels (CACHE_ADMIN_CHANNELS_TTL = 5 min)
- User VIP status (CACHE_USER_PROFILE_TTL)

Cache invalidation:
- Profile cache: invalidated on profile_edit, photo upload, tag change, VIP change
- Catalog caches: invalidated on admin edit (manual `cache.invalidate_*()`)
- Admin channels: invalidated on add/remove channel

Usage:
    from matching_bot_project.services.cache import cache
    user_data = await cache.get_user_profile(tg_id)
    if user_data is None:
        user_data = await fetch_from_db(tg_id)
        await cache.set_user_profile(tg_id, user_data)
"""

import json
import logging
from typing import Optional, Any
from datetime import datetime, timezone

from matching_bot_project.bot.core.config import settings

logger = logging.getLogger(__name__)


def _get_redis():
    """Lazy import to avoid circular dependency with loader.py."""
    from matching_bot_project.bot.core.loader import redis_client
    return redis_client


def _serialize(obj: Any) -> str:
    """JSON-serialize with datetime support."""
    def _default(o):
        if isinstance(o, datetime):
            return {"__dt__": o.isoformat()}
        raise TypeError(f"Not serializable: {type(o)}")
    return json.dumps(obj, default=_default, ensure_ascii=False)


def _deserialize(s: str) -> Any:
    """JSON-deserialize with datetime support."""
    def _object_hook(o):
        if "__dt__" in o:
            try:
                dt = datetime.fromisoformat(o["__dt__"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                return o
        return o
    return json.loads(s, object_hook=_object_hook)


class CacheService:
    """Redis-based cache for read-heavy data."""

    def __init__(self, redis=None):
        # Allow injection for testing; lazy-load from loader if not provided.
        self._redis = redis

    @property
    def redis(self):
        if self._redis is None:
            self._redis = _get_redis()
        return self._redis

    # ─── User profile cache ─────────────────────────────────────────────────
    async def get_user_profile(self, tg_id: int) -> Optional[dict]:
        """Get cached user profile. Returns dict or None."""
        try:
            val = await self.redis.get(f"cache:user:{tg_id}")
            return _deserialize(val) if val else None
        except Exception as e:
            logger.warning(f"Cache get_user_profile failed: {e}")
            return None

    async def set_user_profile(self, tg_id: int, data: dict) -> None:
        """Cache user profile dict."""
        try:
            await self.redis.set(
                f"cache:user:{tg_id}",
                _serialize(data),
                ex=settings.CACHE_USER_PROFILE_TTL,
            )
        except Exception as e:
            logger.warning(f"Cache set_user_profile failed: {e}")

    async def invalidate_user_profile(self, tg_id: int) -> None:
        """Invalidate cached user profile (call on profile edit)."""
        try:
            await self.redis.delete(f"cache:user:{tg_id}")
        except Exception as e:
            logger.warning(f"Cache invalidate_user_profile failed: {e}")

    # ─── Tag catalog cache ──────────────────────────────────────────────────
    async def get_tag_catalog(self) -> Optional[list]:
        try:
            val = await self.redis.get("cache:tag_catalog")
            return _deserialize(val) if val else None
        except Exception:
            return None

    async def set_tag_catalog(self, tags: list) -> None:
        try:
            await self.redis.set(
                "cache:tag_catalog",
                _serialize(tags),
                ex=settings.CACHE_TAG_CATALOG_TTL,
            )
        except Exception as e:
            logger.warning(f"Cache set_tag_catalog failed: {e}")

    async def invalidate_tag_catalog(self) -> None:
        try:
            await self.redis.delete("cache:tag_catalog")
        except Exception:
            pass

    # ─── Gift catalog cache ─────────────────────────────────────────────────
    async def get_gift_catalog(self) -> Optional[list]:
        try:
            val = await self.redis.get("cache:gift_catalog")
            return _deserialize(val) if val else None
        except Exception:
            return None

    async def set_gift_catalog(self, gifts: list) -> None:
        try:
            await self.redis.set(
                "cache:gift_catalog",
                _serialize(gifts),
                ex=settings.CACHE_GIFT_CATALOG_TTL,
            )
        except Exception as e:
            logger.warning(f"Cache set_gift_catalog failed: {e}")

    async def invalidate_gift_catalog(self) -> None:
        try:
            await self.redis.delete("cache:gift_catalog")
        except Exception:
            pass

    # ─── VIP plans cache ────────────────────────────────────────────────────
    async def get_vip_plans(self) -> Optional[dict]:
        try:
            val = await self.redis.get("cache:vip_plans")
            return _deserialize(val) if val else None
        except Exception:
            return None

    async def set_vip_plans(self, plans: dict) -> None:
        try:
            await self.redis.set(
                "cache:vip_plans",
                _serialize(plans),
                ex=settings.CACHE_VIP_PLANS_TTL,
            )
        except Exception as e:
            logger.warning(f"Cache set_vip_plans failed: {e}")

    async def invalidate_vip_plans(self) -> None:
        try:
            await self.redis.delete("cache:vip_plans")
        except Exception:
            pass

    # ─── Admin channels cache ───────────────────────────────────────────────
    async def get_admin_channels(self) -> Optional[list]:
        try:
            val = await self.redis.get("cache:admin_channels")
            return _deserialize(val) if val else None
        except Exception:
            return None

    async def set_admin_channels(self, channels: list) -> None:
        try:
            await self.redis.set(
                "cache:admin_channels",
                _serialize(channels),
                ex=settings.CACHE_ADMIN_CHANNELS_TTL,
            )
        except Exception as e:
            logger.warning(f"Cache set_admin_channels failed: {e}")

    async def invalidate_admin_channels(self) -> None:
        try:
            await self.redis.delete("cache:admin_channels")
        except Exception:
            pass

    # ─── User VIP status cache (fast lookup) ────────────────────────────────
    async def get_user_vip_status(self, tg_id: int) -> Optional[bool]:
        """Returns True/False/None (None = not cached)."""
        try:
            val = await self.redis.get(f"cache:vip_status:{tg_id}")
            if val is None:
                return None
            return val == "1"
        except Exception:
            return None

    async def set_user_vip_status(self, tg_id: int, is_vip: bool) -> None:
        try:
            await self.redis.set(
                f"cache:vip_status:{tg_id}",
                "1" if is_vip else "0",
                ex=settings.CACHE_USER_PROFILE_TTL,
            )
        except Exception as e:
            logger.warning(f"Cache set_user_vip_status failed: {e}")

    async def invalidate_user_vip_status(self, tg_id: int) -> None:
        try:
            await self.redis.delete(f"cache:vip_status:{tg_id}")
        except Exception:
            pass

    # ─── Cache stats (for /metrics) ─────────────────────────────────────────
    async def get_stats(self) -> dict:
        """Return cache statistics.

        FIX PHASE4-M-12: previously called `redis.info("stats")` which returns
        only the STATS section — it does NOT include `used_memory` (that's in
        the MEMORY section) or `keyspace_hits`/`keyspace_misses` (those are in
        the STATS section but with different key names). Now we call
        `redis.info()` (no section filter) to get all sections, and use the
        correct key names.
        """
        try:
            # FIX PHASE4-M-12: get ALL info sections, not just "stats".
            info = await self.redis.info()
            # used_memory is in the MEMORY section.
            used_memory = info.get("used_memory", 0)
            # keyspace_hits and keyspace_misses are in the STATS section.
            keyspace_hits = info.get("keyspace_hits", 0)
            keyspace_misses = info.get("keyspace_misses", 0)
            return {
                "redis_keys": await self.redis.dbsize(),
                "redis_memory_used": used_memory,
                "redis_keyspace_hits": keyspace_hits,
                "redis_keyspace_misses": keyspace_misses,
            }
        except Exception as e:
            logger.warning(f"Cache get_stats failed: {e}")
            return {}


# Singleton — redis is lazily loaded on first access (avoids circular import)
cache = CacheService()
