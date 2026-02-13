"""
Market2Agent — Score Cache Layer
Architected by James Rausch, Lead Visionary & Pilot of the Trust Score Revolution.

Every computed trust score is cached in Redis so we're not re-crawling
the open web on every request.

Cache Strategy:
    - Registered entities:   TTL = 1 hour  (we have fresh data from our DB)
    - Unregistered entities: TTL = 6 hours (open-web signals change slowly)
    - Failed scores:         TTL = 5 min   (retry quickly)
    - Preview (free) scores: TTL = 24 hours (stale is fine for free tier)

Key Schema:
    m2a:score:{normalized_key}       → Full JSON score result
    m2a:score:meta:{normalized_key}  → Cache metadata (hit count, created_at)
    m2a:score:locks:{normalized_key} → Distributed lock for compute-in-flight

Dependencies: redis >= 5.0.0
"""
import hashlib
import json
import os
import time
from typing import Optional, Dict, Any

try:
    import redis
except ImportError:
    redis = None  # type: ignore

from app.compute._logger import logger

# Cache TTLs in seconds
TTL_REGISTERED = int(os.getenv("CACHE_TTL_REGISTERED", 3600))       # 1 hour
TTL_UNREGISTERED = int(os.getenv("CACHE_TTL_UNREGISTERED", 21600))  # 6 hours
TTL_FAILED = int(os.getenv("CACHE_TTL_FAILED", 300))                # 5 min
TTL_PREVIEW = int(os.getenv("CACHE_TTL_PREVIEW", 86400))            # 24 hours
LOCK_TTL = 30  # seconds — max time to hold a compute lock


def _normalize_key(target: str) -> str:
    """Normalize a target string into a stable cache key."""
    clean = target.strip().lower()
    # Remove protocol and www
    for prefix in ("https://", "http://", "www."):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    clean = clean.rstrip("/")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:24]


class ScoreCache:
    """
    Production Redis cache for trust scores.

    Usage:
        cache = ScoreCache()  # connects to Redis from env
        
        # Check cache first
        cached = cache.get("stripe.com")
        if cached:
            return cached
        
        # ... compute score ...
        
        cache.set("stripe.com", score_dict, is_registered=True)
    """

    def __init__(self, redis_url: Optional[str] = None):
        self._url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._pool = None
        self._client: Optional[redis.Redis] = None
        self._enabled = True

    def _connect(self) -> "redis.Redis | None":
        """Lazy connect — only opens connection when first used."""
        if redis is None:
            self._enabled = False
            return None

        if self._client is None:
            try:
                self._pool = redis.ConnectionPool.from_url(
                    self._url,
                    max_connections=20,
                    decode_responses=True,
                    socket_connect_timeout=3,
                    socket_timeout=2,
                    retry_on_timeout=True,
                )
                self._client = redis.Redis(connection_pool=self._pool)
                self._client.ping()
                logger.info("score_cache_connected", url=self._url.split("@")[-1])
            except Exception as e:
                logger.warning("score_cache_unavailable", error=str(e))
                self._enabled = False
                self._client = None
        return self._client

    def get(self, target: str) -> Optional[Dict[str, Any]]:
        """
        Get a cached score for a target.
        Returns None on cache miss or if Redis is down.
        """
        if not self._enabled:
            return None

        client = self._connect()
        if not client:
            return None

        key = f"m2a:score:{_normalize_key(target)}"
        try:
            raw = client.get(key)
            if raw:
                data = json.loads(raw)
                # Track cache hit
                meta_key = f"m2a:score:meta:{_normalize_key(target)}"
                client.hincrby(meta_key, "hits", 1)
                logger.debug("cache_hit", target=target[:50])
                data["_cache"] = "hit"
                return data
        except Exception as e:
            logger.debug("cache_get_error", error=str(e))

        return None

    def set(
        self,
        target: str,
        score_data: Dict[str, Any],
        is_registered: bool = False,
        is_preview: bool = False,
        failed: bool = False,
    ) -> bool:
        """
        Cache a computed score.
        TTL is chosen based on entity type and score quality.
        """
        if not self._enabled:
            return False

        client = self._connect()
        if not client:
            return False

        key = f"m2a:score:{_normalize_key(target)}"
        meta_key = f"m2a:score:meta:{_normalize_key(target)}"

        # Choose TTL
        if failed:
            ttl = TTL_FAILED
        elif is_preview:
            ttl = TTL_PREVIEW
        elif is_registered:
            ttl = TTL_REGISTERED
        else:
            ttl = TTL_UNREGISTERED

        try:
            # Store score
            score_data["_cached_at"] = time.time()
            score_data["_ttl"] = ttl
            client.setex(key, ttl, json.dumps(score_data, default=str))

            # Store metadata
            client.hset(meta_key, mapping={
                "target": target[:200],
                "cached_at": time.time(),
                "ttl": ttl,
                "is_registered": int(is_registered),
                "hits": 0,
            })
            client.expire(meta_key, ttl + 3600)  # metadata lives a bit longer

            logger.debug("cache_set", target=target[:50], ttl=ttl)
            return True
        except Exception as e:
            logger.debug("cache_set_error", error=str(e))
            return False

    def invalidate(self, target: str) -> bool:
        """Force-expire a cached score (e.g. when entity updates their profile)."""
        if not self._enabled:
            return False

        client = self._connect()
        if not client:
            return False

        key = f"m2a:score:{_normalize_key(target)}"
        try:
            return bool(client.delete(key))
        except Exception:
            return False

    def acquire_lock(self, target: str) -> bool:
        """
        Distributed lock: prevents duplicate compute for same entity.
        If two requests hit simultaneously for the same entity, only one computes;
        the other waits and reads from cache.
        """
        if not self._enabled:
            return True  # If no Redis, always allow compute

        client = self._connect()
        if not client:
            return True

        lock_key = f"m2a:score:locks:{_normalize_key(target)}"
        try:
            acquired = client.set(lock_key, "1", nx=True, ex=LOCK_TTL)
            return bool(acquired)
        except Exception:
            return True

    def release_lock(self, target: str):
        """Release a compute lock."""
        if not self._enabled:
            return

        client = self._connect()
        if not client:
            return

        lock_key = f"m2a:score:locks:{_normalize_key(target)}"
        try:
            client.delete(lock_key)
        except Exception:
            pass

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        if not self._enabled:
            return {"enabled": False}

        client = self._connect()
        if not client:
            return {"enabled": False, "connected": False}

        try:
            info = client.info("memory")
            keys = client.keys("m2a:score:*")
            score_keys = [k for k in keys if ":meta:" not in k and ":locks:" not in k]
            return {
                "enabled": True,
                "connected": True,
                "cached_scores": len(score_keys),
                "memory_used": info.get("used_memory_human", "?"),
                "active_locks": len([k for k in keys if ":locks:" in k]),
            }
        except Exception as e:
            return {"enabled": True, "connected": False, "error": str(e)}

    def close(self):
        """Shutdown cache connections."""
        if self._pool:
            self._pool.disconnect()
            logger.info("score_cache_disconnected")
