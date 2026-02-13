"""
Market2Agent — Compute Pipeline
Architected by James Rausch, Lead Visionary & Pilot of the Trust Score Revolution.

This is the central nervous system.
Every trust score request flows through this pipeline:

    Request → Cache Check → [Collect Signals] → Score → Cache → Persist → Response

The pipeline handles:
    - Cache-first strategy (don't recompute what you already know)
    - Distributed locking (prevent duplicate computation)
    - Parallel signal collection with circuit breakers
    - Score computation via the 5-pillar engine
    - Async persistence (don't block the response on DB writes)
    - Graceful degradation (every component can fail independently)

Dependencies: All components (cache, persistence, collectors) are optional.
    If Redis is down → compute every time.
    If Neo4j is down → don't persist, still return scores.
    If a collector fails → score with partial data.
"""
import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

from app.compute._logger import logger

from app.compute.cache import ScoreCache
from app.compute.persistence import ScorePersistence

# Singleton instances (initialized on first use)
_cache: Optional[ScoreCache] = None
_persistence: Optional[ScorePersistence] = None


def get_cache() -> ScoreCache:
    global _cache
    if _cache is None:
        _cache = ScoreCache()
    return _cache


def get_persistence() -> ScorePersistence:
    global _persistence
    if _persistence is None:
        _persistence = ScorePersistence()
    return _persistence


# =============================================
# CIRCUIT BREAKER
# =============================================

class CircuitBreaker:
    """
    Prevents repeated calls to a failing external service.
    After `threshold` failures, the circuit opens and skips calls
    for `recovery_timeout` seconds before trying again.
    """
    def __init__(self, name: str, threshold: int = 3, recovery_timeout: int = 60):
        self.name = name
        self.threshold = threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = "closed"  # closed = healthy, open = failing, half-open = testing

    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            # Check if recovery period has passed
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "half-open"
                return True
            return False
        # half-open: allow one test request
        return True

    def record_success(self):
        self.failures = 0
        self.state = "closed"

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.threshold:
            self.state = "open"
            logger.warning("circuit_breaker_opened", collector=self.name, failures=self.failures)

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "failures": self.failures,
            "threshold": self.threshold,
        }


# One circuit breaker per collector
_breakers: Dict[str, CircuitBreaker] = {
    "dns": CircuitBreaker("dns", threshold=5, recovery_timeout=120),
    "web": CircuitBreaker("web", threshold=3, recovery_timeout=60),
    "social": CircuitBreaker("social", threshold=5, recovery_timeout=120),
    "knowledge": CircuitBreaker("knowledge", threshold=3, recovery_timeout=60),
    "github": CircuitBreaker("github", threshold=5, recovery_timeout=180),
    "reputation": CircuitBreaker("reputation", threshold=3, recovery_timeout=60),
    "blocklist": CircuitBreaker("blocklist", threshold=3, recovery_timeout=60),
}


# =============================================
# THE PIPELINE
# =============================================

async def compute_trust_score(
    target: str,
    force_refresh: bool = False,
    is_preview: bool = False,
) -> Dict[str, Any]:
    """
    The main entry point for all trust score computation.
    James Rausch's universal scoring pipeline.

    Flow:
        1. Normalize target
        2. Check cache (skip if force_refresh)
        3. Acquire distributed lock (prevent duplicate compute)
        4. Look up registered data in Neo4j
        5. Collect open-web signals in parallel (with circuit breakers)
        6. Run the 5-pillar scoring engine
        7. Cache the result
        8. Persist to Neo4j (async, non-blocking)
        9. Return

    Args:
        target: Any identifier — domain, URL, name, email, etc.
        force_refresh: Skip cache and recompute
        is_preview: Free-tier preview (lighter collection, longer cache)

    Returns:
        Complete trust score dict ready for API response.
    """
    pipeline_start = time.time()
    cache = get_cache()
    persistence = get_persistence()

    # ── Step 1: Check cache ──────────────────────────────
    if not force_refresh:
        cached = cache.get(target)
        if cached:
            cached["_pipeline"] = {
                "source": "cache",
                "pipeline_time_ms": round((time.time() - pipeline_start) * 1000, 2),
            }
            return cached

    # ── Step 2: Acquire lock ─────────────────────────────
    lock_acquired = cache.acquire_lock(target)
    if not lock_acquired:
        # Another request is computing this right now — wait briefly and check cache
        await asyncio.sleep(1.5)
        cached = cache.get(target)
        if cached:
            cached["_pipeline"] = {"source": "cache_after_lock_wait"}
            return cached
        # Still nothing — compute anyway (lock may have expired)

    try:
        # ── Step 3: Registry lookup ──────────────────────
        registered_data = persistence.get_registered_data(target)
        is_registered = registered_data is not None

        # ── Step 4: Collect signals ──────────────────────
        identity, competence, solvency, reputation, network, metadata = \
            await _collect_with_breakers(target, registered_data, is_preview)

        # ── Step 5: Score ────────────────────────────────
        from app.trust.engine import calculate_trust_score

        entity_id = (
            registered_data.get("entity_id") if registered_data
            else f"open:{hashlib.sha256(target.lower().encode()).hexdigest()[:16]}"
        )
        entity_name = (
            registered_data.get("canonical_name") if registered_data
            else metadata.get("name", target)
        )

        trust_score = calculate_trust_score(
            entity_id=entity_id,
            entity_name=entity_name,
            entity_type=metadata.get("entity_type", "unknown"),
            is_verified=registered_data.get("verified", False) if registered_data else False,
            is_registered=is_registered,
            identity=identity,
            competence=competence,
            solvency=solvency,
            reputation=reputation,
            network=network,
            data_freshness="live",
            data_sources=metadata.get("data_sources", []),
        )

        result = trust_score.to_full()
        result["collection_metadata"] = {
            "collection_time_ms": metadata["collection_time_ms"],
            "data_sources_queried": metadata["data_sources"],
            "collectors_skipped": metadata.get("skipped", []),
            "errors": metadata["errors"],
        }

        # ── Step 6: Cache ────────────────────────────────
        cache.set(
            target, result,
            is_registered=is_registered,
            is_preview=is_preview,
        )

        # ── Step 7: Persist (fire-and-forget) ────────────
        # We don't await this — it runs in the background
        asyncio.get_event_loop().call_soon(
            lambda: persistence.save_score(result)
        )

        # ── Done ─────────────────────────────────────────
        pipeline_ms = round((time.time() - pipeline_start) * 1000, 2)
        result["_pipeline"] = {
            "source": "computed",
            "pipeline_time_ms": pipeline_ms,
            "is_registered": is_registered,
            "collectors_run": len(metadata["data_sources"]),
        }

        logger.info(
            "score_computed",
            target=target[:80],
            score=result["score"],
            grade=result["grade"],
            confidence=result["confidence"],
            pipeline_ms=pipeline_ms,
            cached=True,
        )

        return result

    except Exception as e:
        logger.error("pipeline_failed", target=target[:80], error=str(e))

        # Return a degraded score rather than an error
        from app.trust.engine import calculate_trust_score
        fallback = calculate_trust_score(
            entity_id=f"error:{hashlib.sha256(target.encode()).hexdigest()[:16]}",
            entity_name=target,
            entity_type="unknown",
            is_verified=False,
            is_registered=False,
            data_freshness="failed",
            data_sources=["none"],
        )
        result = fallback.to_full()
        result["_pipeline"] = {"source": "fallback", "error": str(e)}

        # Cache the failure briefly so we don't hammer external services
        cache.set(target, result, failed=True)
        return result

    finally:
        cache.release_lock(target)


async def _collect_with_breakers(
    target: str,
    registered_data: Optional[Dict[str, Any]],
    is_preview: bool,
) -> Tuple:
    """
    Run signal collectors in parallel with circuit breakers.
    Each collector is independently protected — if GitHub is down,
    we still get DNS, social, and everything else.
    """
    from app.compute.open_web import (
        EntityIdentifier,
        collect_dns_signals,
        collect_web_presence_signals,
        collect_social_signals,
        collect_knowledge_graph_signals,
        collect_github_signals,
        collect_reputation_signals,
        collect_blocklist_signals,
    )
    from app.trust.engine import (
        IdentitySignals, CompetenceSignals, SolvencySignals,
        ReputationSignals, NetworkSignals,
    )
    import httpx

    eid = EntityIdentifier.from_query(target)
    entity_name = eid.name or (eid.domain.split(".")[0] if eid.domain else target)

    metadata = {
        "entity_type": eid.entity_type.value,
        "domain": eid.domain,
        "name": entity_name,
        "data_sources": [],
        "skipped": [],
        "errors": [],
        "collection_time_ms": 0,
    }

    collect_start = time.time()

    # ── Build collector tasks ────────────────────────────
    async def _guarded(name: str, coro):
        """Run a collector with circuit breaker protection."""
        breaker = _breakers.get(name)
        if breaker and not breaker.can_execute():
            metadata["skipped"].append(name)
            return name, {}

        try:
            result = await asyncio.wait_for(coro, timeout=12.0)
            if breaker:
                breaker.record_success()
            metadata["data_sources"].append(name)
            return name, result
        except asyncio.TimeoutError:
            if breaker:
                breaker.record_failure()
            metadata["errors"].append(f"{name}: timeout")
            return name, {}
        except Exception as e:
            if breaker:
                breaker.record_failure()
            metadata["errors"].append(f"{name}: {str(e)[:100]}")
            return name, {}

    # ── Execute all collectors in TRUE parallel ──────────
    async with httpx.AsyncClient(
        headers={"User-Agent": "Market2Agent TrustBot/2.0 (+https://market2agent.ai/bot)"},
        follow_redirects=True,
        verify=True,
        timeout=httpx.Timeout(12.0, connect=5.0),
    ) as client:

        tasks = []

        if eid.domain:
            tasks.append(_guarded("dns", collect_dns_signals(eid.domain, client)))
            tasks.append(_guarded("web", collect_web_presence_signals(eid.domain, client)))
            tasks.append(_guarded("blocklist", collect_blocklist_signals(eid.domain, client)))

        tasks.append(_guarded("social", collect_social_signals(entity_name, eid.domain or "", client)))
        tasks.append(_guarded("knowledge", collect_knowledge_graph_signals(entity_name, eid.domain or "", client)))

        # Always run all collectors — even in preview mode.
        if True:  # Never skip collectors — a wrong score kills the demo
            tasks.append(_guarded("github", collect_github_signals(entity_name, client)))
            tasks.append(_guarded("reputation", collect_reputation_signals(entity_name, eid.domain or "", client)))

        # TRUE PARALLEL — all tasks fire simultaneously
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

    # ── Merge results ────────────────────────────────────
    results = {}
    for item in results_list:
        if isinstance(item, tuple):
            name, data = item
            results[name] = data
        else:
            logger.warning("collector_exception", error=str(item))

    dns_data = results.get("dns", {})
    web_data = results.get("web", {})
    social_data = results.get("social", {})
    kg_data = results.get("knowledge", {})
    github_data = results.get("github", {})
    rep_data = results.get("reputation", {})
    bl_data = results.get("blocklist", {})
    reg = registered_data or {}

    # ── Build signal objects ─────────────────────────────
    identity = IdentitySignals(
        domain_verified=reg.get("verified", False) or dns_data.get("dns_txt_verified", False),
        dns_txt_verified=dns_data.get("dns_txt_verified", False),
        file_verified=reg.get("verification_method") == "domain_file",
        email_verified=reg.get("verification_method") == "email",
        domain_age_days=dns_data.get("domain_age_days", 0),
        ssl_valid=dns_data.get("ssl_valid", False),
        ssl_org_match=dns_data.get("ssl_org_match", False),
        dns_has_spf=dns_data.get("dns_has_spf", False),
        dns_has_dmarc=dns_data.get("dns_has_dmarc", False),
        dns_has_dkim=dns_data.get("dns_has_dkim", False),
        has_structured_data=web_data.get("has_structured_data", False),
        has_organization_schema=web_data.get("has_organization_schema", False),
        has_product_schema=web_data.get("has_product_schema", False),
        has_faq_schema=web_data.get("has_faq_schema", False),
        has_wikidata_entry=kg_data.get("has_wikidata_entry", False),
        has_wikipedia_page=kg_data.get("has_wikipedia_page", False),
        has_crunchbase=kg_data.get("has_crunchbase", False),
        has_linkedin_company=kg_data.get("has_linkedin_company", False),
        has_google_knowledge_panel=kg_data.get("has_google_knowledge_panel", False),
        has_business_registration=reg.get("has_business_registration", False),
        social_profiles=social_data.get("social_profiles", {}),
        social_profiles_count=social_data.get("social_profiles_count", 0),
        has_agent_card=reg.get("has_agent_card", False),
        has_model_card=reg.get("has_model_card", False),
        has_api_documentation=web_data.get("has_api_documentation", False),
        geo_score=reg.get("visibility_score", 0) or 0,
    )

    competence = CompetenceSignals(
        total_transactions=reg.get("total_transactions", 0),
        successful_transactions=reg.get("successful_transactions", 0),
        failed_transactions=reg.get("failed_transactions", 0),
        uptime_pct=reg.get("uptime_pct", 0),
        has_status_page=web_data.get("has_status_page", False),
        github_stars=github_data.get("github_stars", 0),
        github_last_commit_days=github_data.get("github_last_commit_days", 0),
        has_public_changelog=web_data.get("has_public_changelog", False),
        has_public_roadmap=web_data.get("has_public_roadmap", False),
        visibility_score=reg.get("visibility_score", 0) or 0,
    )

    solvency = SolvencySignals(
        has_payment_method=bool(reg.get("stripe_customer_id")),
        stripe_verified=bool(reg.get("stripe_customer_id")),
        subscription_active=reg.get("subscription_status") == "active",
        subscription_tier=reg.get("subscription_tier", "free"),
        account_age_days=reg.get("account_age_days", 0),
    )

    reputation = ReputationSignals(
        overall_sentiment=rep_data.get("overall_sentiment", 0.0),
        sentiment_sample_size=rep_data.get("sentiment_sample_size", 0),
        sentiment_trend=rep_data.get("sentiment_trend", "stable"),
        google_reviews_count=rep_data.get("google_reviews_count", 0),
        google_reviews_rating=rep_data.get("google_reviews_rating", 0.0),
        news_mentions_30d=rep_data.get("news_mentions_30d", 0),
        has_positive_press=rep_data.get("has_positive_press", False),
        has_negative_press=rep_data.get("has_negative_press", False),
        twitter_followers=social_data.get("twitter_followers", 0),
        on_spam_blocklist=bl_data.get("on_spam_blocklist", False),
        on_fraud_blocklist=bl_data.get("on_fraud_blocklist", False),
        on_sanctions_list=bl_data.get("on_sanctions_list", False),
        has_soc2=rep_data.get("has_soc2", False),
        has_iso27001=rep_data.get("has_iso27001", False),
    )

    network = NetworkSignals(
        high_trust_connections=reg.get("high_trust_connections", 0),
        verified_partners_count=reg.get("verified_partners_count", 0),
        endorsements_received=reg.get("endorsements_received", 0),
        integration_partners=reg.get("integration_partners", 0),
    )

    metadata["collection_time_ms"] = round((time.time() - collect_start) * 1000, 2)

    return identity, competence, solvency, reputation, network, metadata


# =============================================
# BATCH PIPELINE
# =============================================

async def compute_batch(
    targets: list[str],
    max_concurrent: int = 5,
) -> list[Dict[str, Any]]:
    """
    Score multiple entities with controlled concurrency.
    Uses a semaphore to avoid overwhelming external APIs.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _score_one(target: str) -> Dict[str, Any]:
        async with semaphore:
            return await compute_trust_score(target)

    results = await asyncio.gather(
        *[_score_one(t) for t in targets],
        return_exceptions=True,
    )

    return [
        r if isinstance(r, dict) else {"error": str(r), "target": targets[i]}
        for i, r in enumerate(results)
    ]


# =============================================
# COMPARE PIPELINE
# =============================================

async def compute_comparison(entity_a: str, entity_b: str) -> Dict[str, Any]:
    """Score two entities side-by-side."""
    score_a, score_b = await asyncio.gather(
        compute_trust_score(entity_a),
        compute_trust_score(entity_b),
    )

    winner = "a" if score_a.get("score", 0) > score_b.get("score", 0) else "b"
    diff = abs(score_a.get("score", 0) - score_b.get("score", 0))

    return {
        "entity_a": score_a,
        "entity_b": score_b,
        "winner": winner,
        "score_difference": diff,
        "analysis": {
            "identity": {
                "a": score_a.get("identity_score", 0),
                "b": score_b.get("identity_score", 0),
                "winner": "a" if score_a.get("identity_score", 0) > score_b.get("identity_score", 0) else "b",
            },
            "competence": {
                "a": score_a.get("competence_score", 0),
                "b": score_b.get("competence_score", 0),
                "winner": "a" if score_a.get("competence_score", 0) > score_b.get("competence_score", 0) else "b",
            },
            "reputation": {
                "a": score_a.get("reputation_score", 0),
                "b": score_b.get("reputation_score", 0),
                "winner": "a" if score_a.get("reputation_score", 0) > score_b.get("reputation_score", 0) else "b",
            },
            "solvency": {
                "a": score_a.get("solvency_score", 0),
                "b": score_b.get("solvency_score", 0),
                "winner": "a" if score_a.get("solvency_score", 0) > score_b.get("solvency_score", 0) else "b",
            },
            "network": {
                "a": score_a.get("network_score", 0),
                "b": score_b.get("network_score", 0),
                "winner": "a" if score_a.get("network_score", 0) > score_b.get("network_score", 0) else "b",
            },
        },
    }


# =============================================
# PIPELINE STATUS
# =============================================

def pipeline_status() -> Dict[str, Any]:
    """Return health/status of the entire compute pipeline."""
    return {
        "cache": get_cache().stats(),
        "persistence": {"available": get_persistence()._available},
        "circuit_breakers": {
            name: breaker.status()
            for name, breaker in _breakers.items()
        },
    }


def shutdown():
    """Clean shutdown of pipeline resources."""
    global _cache, _persistence
    if _cache:
        _cache.close()
    _cache = None
    _persistence = None
    logger.info("pipeline_shutdown")
