"""
Market2Agent Metering & Rate Limiting
Architected by James Rausch, Lead Visionary & Pilot of the Trust Score Revolution.

Every Trust API call is metered. This is how we make money.

Architecture:
    - Redis sorted sets for sliding window rate limits
    - Redis counters for daily/monthly usage
    - Redis hash for real-time key status cache

Keys:
    m2a:rate:{key_hash}:min   -> Sliding window (requests per minute)
    m2a:rate:{key_hash}:day   -> Daily counter (resets at midnight UTC)
    m2a:usage:{key_hash}:YYYY-MM  -> Monthly usage counter
    m2a:key_cache:{key_hash}  -> Cached key metadata (TTL 5 min)
"""
import time
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

import redis
import structlog

logger = structlog.get_logger()


class MeteringError(Exception):
    pass


class RateLimitExceeded(MeteringError):
    def __init__(self, limit_type: str, limit: int, reset_seconds: int):
        self.limit_type = limit_type
        self.limit = limit
        self.reset_seconds = reset_seconds
        super().__init__(f"Rate limit exceeded: {limit_type} ({limit}/window). Resets in {reset_seconds}s")


class QuotaExceeded(MeteringError):
    def __init__(self, quota: int, used: int):
        self.quota = quota
        self.used = used
        super().__init__(f"Monthly quota exceeded: {used}/{quota}")


class Meter:
    """
    Handles rate limiting and usage metering for API keys.
    All state is in Redis. Stateless otherwise.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self._prefix = "m2a"

    def _key(self, *parts) -> str:
        return ":".join([self._prefix] + list(parts))

    # =============================================
    # RATE LIMITING (sliding window)
    # =============================================

    def check_rate_limit(
        self,
        key_hash: str,
        limit_per_minute: int = 60,
        limit_per_day: int = 10000,
    ) -> Dict[str, Any]:
        """
        Check if the request is within rate limits.
        Returns remaining counts.
        Raises RateLimitExceeded if over limit.
        """
        now = time.time()
        pipe = self.redis.pipeline()

        # Per-minute sliding window
        minute_key = self._key("rate", key_hash, "min")
        window_start = now - 60

        # Remove old entries and count current
        pipe.zremrangebyscore(minute_key, 0, window_start)
        pipe.zcard(minute_key)
        pipe.zadd(minute_key, {str(now): now})
        pipe.expire(minute_key, 120)

        # Per-day counter
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        day_key = self._key("rate", key_hash, "day", today)
        pipe.incr(day_key)
        pipe.expire(day_key, 86400 * 2)  # Keep for 2 days

        results = pipe.execute()

        minute_count = results[1]  # zcard result
        day_count = results[4]     # incr result

        # Check minute limit
        if minute_count >= limit_per_minute:
            raise RateLimitExceeded(
                limit_type="per_minute",
                limit=limit_per_minute,
                reset_seconds=60,
            )

        # Check daily limit
        if day_count > limit_per_day:
            # Calculate seconds until midnight UTC
            now_dt = datetime.now(timezone.utc)
            midnight = now_dt.replace(hour=0, minute=0, second=0) + __import__('datetime').timedelta(days=1)
            reset = int((midnight - now_dt).total_seconds())
            raise RateLimitExceeded(
                limit_type="per_day",
                limit=limit_per_day,
                reset_seconds=reset,
            )

        return {
            "minute_remaining": limit_per_minute - minute_count,
            "day_remaining": limit_per_day - day_count,
            "minute_limit": limit_per_minute,
            "day_limit": limit_per_day,
        }

    # =============================================
    # USAGE METERING (monthly counters)
    # =============================================

    def record_usage(self, key_hash: str, user_id: str, endpoint: str):
        """
        Record a billable API call.
        Increments monthly counter and logs the event.
        """
        now = datetime.now(timezone.utc)
        month_key = self._key("usage", key_hash, now.strftime("%Y-%m"))
        global_key = self._key("usage", "global", now.strftime("%Y-%m"))
        user_key = self._key("usage", "user", user_id, now.strftime("%Y-%m"))

        pipe = self.redis.pipeline()
        pipe.incr(month_key)
        pipe.expire(month_key, 86400 * 62)  # Keep for ~2 months
        pipe.incr(global_key)
        pipe.expire(global_key, 86400 * 62)
        pipe.incr(user_key)
        pipe.expire(user_key, 86400 * 62)

        # Daily breakdown for analytics
        day_key = self._key("usage", "daily", now.strftime("%Y-%m-%d"))
        pipe.incr(day_key)
        pipe.expire(day_key, 86400 * 90)

        pipe.execute()

    def check_quota(self, key_hash: str, monthly_quota: int) -> Dict[str, Any]:
        """
        Check if the key is within its monthly quota.
        Raises QuotaExceeded if over.
        """
        now = datetime.now(timezone.utc)
        month_key = self._key("usage", key_hash, now.strftime("%Y-%m"))
        used = int(self.redis.get(month_key) or 0)

        if monthly_quota > 0 and used >= monthly_quota:
            raise QuotaExceeded(quota=monthly_quota, used=used)

        return {
            "used": used,
            "quota": monthly_quota,
            "remaining": monthly_quota - used if monthly_quota > 0 else -1,
            "period": now.strftime("%Y-%m"),
        }

    def get_usage_for_key(self, key_hash: str) -> Dict[str, int]:
        """Get usage stats for a specific key."""
        now = datetime.now(timezone.utc)
        month_key = self._key("usage", key_hash, now.strftime("%Y-%m"))
        today = now.strftime("%Y-%m-%d")
        day_key = self._key("rate", key_hash, "day", today)

        pipe = self.redis.pipeline()
        pipe.get(month_key)
        pipe.get(day_key)
        results = pipe.execute()

        return {
            "month": int(results[0] or 0),
            "today": int(results[1] or 0),
        }

    def get_usage_for_user(self, user_id: str) -> Dict[str, int]:
        """Get aggregated usage for a user (across all keys)."""
        now = datetime.now(timezone.utc)
        user_key = self._key("usage", "user", user_id, now.strftime("%Y-%m"))
        return {
            "month": int(self.redis.get(user_key) or 0),
            "period": now.strftime("%Y-%m"),
        }

    # =============================================
    # KEY CACHE (avoid Neo4j on every request)
    # =============================================

    def cache_key_metadata(self, key_hash: str, metadata: Dict[str, Any], ttl: int = 300):
        """Cache API key metadata in Redis (5 min TTL)."""
        cache_key = self._key("key_cache", key_hash)
        self.redis.setex(cache_key, ttl, json.dumps(metadata, default=str))

    def get_cached_key(self, key_hash: str) -> Optional[Dict[str, Any]]:
        """Get cached key metadata."""
        cache_key = self._key("key_cache", key_hash)
        data = self.redis.get(cache_key)
        return json.loads(data) if data else None

    # =============================================
    # ADMIN STATS
    # =============================================

    def get_global_stats(self) -> Dict[str, Any]:
        """Get platform-wide usage stats."""
        now = datetime.now(timezone.utc)
        global_key = self._key("usage", "global", now.strftime("%Y-%m"))
        today_key = self._key("usage", "daily", now.strftime("%Y-%m-%d"))

        pipe = self.redis.pipeline()
        pipe.get(global_key)
        pipe.get(today_key)
        results = pipe.execute()

        return {
            "total_calls_this_month": int(results[0] or 0),
            "total_calls_today": int(results[1] or 0),
            "period": now.strftime("%Y-%m"),
        }


# Singleton
_meter: Optional[Meter] = None


def get_meter() -> Meter:
    global _meter
    if _meter is None:
        import os
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        _meter = Meter(redis_url=redis_url)
    return _meter
