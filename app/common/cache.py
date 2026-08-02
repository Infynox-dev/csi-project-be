"""Shared API response cache with TTL and pattern-based invalidation.

Uses Redis when REDIS_URL is set (required for multi-worker Coolify deploys);
falls back to an in-process dict for local development.
"""

from __future__ import annotations

import logging
import pickle
import threading
import time
from functools import lru_cache, wraps
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_cache: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()

KEY_PREFIX = "csi:cache:"

# Long-lived TTLs for reference / mostly-static data
TTL_DASHBOARD = 300        # 5 min
TTL_UNITS_LIST = 300       # 5 min
TTL_PAYMENTS = 120         # 2 min
TTL_MEMBER_ADD_REQ = 120   # 2 min
TTL_SITE_SETTINGS = 600    # 10 min
TTL_MASTER_DATA = 3600     # 1 hour (countries / states / cities rarely change)
TTL_KALAMELA = 300         # 5 min
TTL_DISTRICT_DATA = 300    # 5 min


@lru_cache(maxsize=1)
def _redis_client():
    """Lazy Redis client; returns None if REDIS_URL unset or connect fails."""
    from app.common.config import get_settings

    url = get_settings().redis_url
    if not url:
        return None
    try:
        import redis

        client = redis.Redis.from_url(
            url,
            decode_responses=False,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        logger.info("Cache backend: Redis (%s)", url.split("@")[-1])
        return client
    except Exception as exc:
        logger.warning("Redis unavailable (%s); falling back to in-memory cache", exc)
        return None


def get_cache(key: str) -> Optional[Any]:
    """Return cached value if still fresh, else None."""
    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(KEY_PREFIX + key)
            if raw is None:
                return None
            return pickle.loads(raw)
        except Exception as exc:
            logger.warning("Redis get failed for %s: %s", key, exc)
            return None

    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        if time.monotonic() < entry["expires_at"]:
            return entry["value"]
        del _cache[key]
        return None


def set_cache(key: str, value: Any, ttl_seconds: int = 300) -> None:
    """Store *value* under *key* for *ttl_seconds* seconds."""
    client = _redis_client()
    if client is not None:
        try:
            client.setex(KEY_PREFIX + key, ttl_seconds, pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))
            return
        except Exception as exc:
            logger.warning("Redis set failed for %s: %s", key, exc)
            return

    with _lock:
        _cache[key] = {
            "value": value,
            "expires_at": time.monotonic() + ttl_seconds,
        }


def clear_cache(pattern: Optional[str] = None) -> None:
    """Delete cache entries whose key contains *pattern* (or all if None)."""
    client = _redis_client()
    if client is not None:
        try:
            match = KEY_PREFIX + (f"*{pattern}*" if pattern else "*")
            deleted = 0
            for redis_key in client.scan_iter(match=match, count=200):
                client.delete(redis_key)
                deleted += 1
            if deleted:
                logger.info("Cleared %d Redis cache keys (pattern=%s)", deleted, pattern)
            return
        except Exception as exc:
            logger.warning("Redis clear failed (pattern=%s): %s", pattern, exc)
            return

    with _lock:
        if pattern is None:
            _cache.clear()
        else:
            to_delete = [k for k in _cache if pattern in k]
            for key in to_delete:
                del _cache[key]


def cache_size() -> int:
    """Return number of live (non-expired) entries."""
    client = _redis_client()
    if client is not None:
        try:
            return sum(1 for _ in client.scan_iter(match=KEY_PREFIX + "*", count=200))
        except Exception:
            return 0

    now = time.monotonic()
    with _lock:
        return sum(1 for e in _cache.values() if now < e["expires_at"])


def cached(ttl_seconds: int = 300, key_prefix: str = ""):
    """Decorator: cache async or sync function result by function name.

    The cache key is ``{key_prefix}{func.__name__}``.  For functions that
    accept arguments which should vary the key, use get_cache / set_cache
    directly.
    """
    def decorator(func: Callable) -> Callable:
        import asyncio

        cache_key = f"{key_prefix}{func.__name__}"

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            cached_value = get_cache(cache_key)
            if cached_value is not None:
                return cached_value
            result = await func(*args, **kwargs)
            set_cache(cache_key, result, ttl_seconds)
            return result

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            cached_value = get_cache(cache_key)
            if cached_value is not None:
                return cached_value
            result = func(*args, **kwargs)
            set_cache(cache_key, result, ttl_seconds)
            return result

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator
