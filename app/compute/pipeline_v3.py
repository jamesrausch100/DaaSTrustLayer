"""
Market2Agent — Pipeline v3
Bridges the v3 scoring engine into the existing API.

This replaces the old compute_trust_score() with the new
evidence accumulation model while maintaining API compatibility.
"""
import asyncio
import time
from typing import Dict, Any, Optional

import structlog

from app.compute.cache import ScoreCache

logger = structlog.get_logger()

_cache: Optional[ScoreCache] = None


def get_cache() -> ScoreCache:
    global _cache
    if _cache is None:
        _cache = ScoreCache()
    return _cache


async def score_entity(target: str, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Score any entity using the v3 engine.
    Returns a dict ready for API response.

    Flow:
        1. Check cache
        2. Collect all signals in parallel (Layer A)
        3. Run scoring algorithm (Layer B)
        4. Compute confidence (Layer C)
        5. Cache result
        6. Return
    """
    cache = get_cache()

    # Cache check
    if not force_refresh:
        cached = cache.get(target)
        if cached:
            cached["_source"] = "cache"
            return cached

    # Lock to prevent duplicate computation
    lock = cache.acquire_lock(target)
    if not lock:
        await asyncio.sleep(1.5)
        cached = cache.get(target)
        if cached:
            return cached

    try:
        # Layer A — observe entity (sensors + chain recording)
        from app.compute.sensors import observe_entity
        signals = await observe_entity(target)

        # Layer B + C — score
        from app.trust.engine_v3 import compute_score
        result = compute_score(signals)

        # Build API-compatible response
        response = result.to_full()
        response["_source"] = "computed"
        response["collection_time_ms"] = signals.collection_time_ms
        response["sources_queried"] = signals.sources_queried
        response["sources_responded"] = signals.sources_responded
        response["collection_errors"] = signals.collection_errors

        # Cache it
        cache.set(target, response)

        return response

    except Exception as e:
        logger.error("score_entity_failed", target=target, error=str(e))
        # Return a minimal safe response
        return {
            "target": target,
            "score": 0,
            "grade": "D",
            "recommendation": "REJECT",
            "confidence": 0.0,
            "confidence_label": "error",
            "is_verified": False,
            "is_registered": False,
            "engine_version": "3.0.0",
            "error": str(e),
        }

    finally:
        cache.release_lock(target)
