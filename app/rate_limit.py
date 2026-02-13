"""
Market2Agent — Rate Limiting
Redis-backed sliding window rate limiter.
"""
import time
import hashlib
from typing import Optional
from fastapi import Request, HTTPException
import structlog

logger = structlog.get_logger()

_redis = None


def _get_redis():
    """Lazy Redis connection."""
    global _redis
    if _redis is not None:
        return _redis
    try:
        import redis
        from app.config import settings
        _redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        _redis.ping()
        logger.info("rate_limiter_redis_connected")
        return _redis
    except Exception as e:
        logger.warning("rate_limiter_redis_unavailable", error=str(e))
        return None


def _client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For from nginx."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _rate_key(ip: str, endpoint: str) -> str:
    """Build a Redis key for rate limiting."""
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
    return f"rl:{endpoint}:{ip_hash}"


async def check_rate_limit(
    request: Request,
    endpoint: str = "preview",
    max_requests: int = 10,
    window_seconds: int = 60,
) -> None:
    """
    Sliding window rate limiter using Redis.
    Raises 429 if limit exceeded.
    Falls through silently if Redis is unavailable (fail-open).
    """
    r = _get_redis()
    if r is None:
        return  # fail-open: if Redis is down, allow requests

    ip = _client_ip(request)
    key = _rate_key(ip, endpoint)
    now = time.time()
    window_start = now - window_seconds

    pipe = r.pipeline()
    try:
        # Remove old entries outside the window
        pipe.zremrangebyscore(key, 0, window_start)
        # Count current requests in window
        pipe.zcard(key)
        # Add current request
        pipe.zadd(key, {str(now): now})
        # Set expiry on the key
        pipe.expire(key, window_seconds + 1)
        results = pipe.execute()

        current_count = results[1]

        if current_count >= max_requests:
            # Calculate retry-after
            oldest = r.zrange(key, 0, 0, withscores=True)
            retry_after = int(window_seconds - (now - oldest[0][1])) + 1 if oldest else window_seconds

            logger.warning("rate_limit_exceeded",
                           ip=ip[:8] + "...", endpoint=endpoint,
                           count=current_count, limit=max_requests)

            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Max {max_requests} requests per {window_seconds}s.",
                headers={"Retry-After": str(retry_after)},
            )
    except HTTPException:
        raise
    except Exception as e:
        # Redis error — fail open
        logger.warning("rate_limit_check_failed", error=str(e))


async def rate_limit_preview(request: Request) -> None:
    """Rate limit for the free preview endpoint: 10 per minute per IP."""
    await check_rate_limit(request, endpoint="preview", max_requests=10, window_seconds=60)


async def rate_limit_free(request: Request) -> None:
    """Rate limit for free tier: 100 per day per IP."""
    await check_rate_limit(request, endpoint="free", max_requests=100, window_seconds=86400)
