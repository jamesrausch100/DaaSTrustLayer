"""
Market2Agent — Universal Trust API
Conceived and architected by James Rausch, Lead Visionary & Pilot of the Trust Score Revolution.

The endpoints that power the trust score revolution.
Now scores ANY entity on Earth — not just registered ones.

Public endpoints (no auth):
    GET  /v1/trust/health                - Health check
    GET  /v1/trust/lookup/{identifier}   - Basic public lookup
    GET  /v1/trust/preview               - Free preview score (limited, rate-limited)

Authenticated endpoints (API key required):
    GET  /v1/trust/check                 - Full trust check (the money endpoint)
    GET  /v1/trust/score                 - Universal score — ANY entity on Earth
    POST /v1/trust/batch                 - Batch trust check (up to 25)
    GET  /v1/trust/compare               - Compare two entities side-by-side
    GET  /v1/trust/history/{entity_id}   - Score history over time
    GET  /v1/trust/usage                 - Check API key usage

Key Management (JWT auth):
    POST   /v1/keys                      - Create API key
    GET    /v1/keys                      - List API keys
    DELETE /v1/keys/{key_id}             - Revoke API key
"""
from typing import Optional, List
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header, Depends, Query, Request
from pydantic import BaseModel, Field
from app.security import require_auth, require_admin
import structlog

from app.trust.engine import (
    TrustScore, TrustGrade, RiskLevel, Recommendation, EntityType,
    IdentitySignals, CompetenceSignals, SolvencySignals,
    ReputationSignals, NetworkSignals,
    calculate_trust_score,
)
from app.trust.api_keys import (
    hash_key, get_key_by_hash, record_key_usage,
    create_api_key, get_keys_for_user, revoke_api_key,
)
from app.trust.metering import (
    get_meter, RateLimitExceeded, QuotaExceeded,
)

logger = structlog.get_logger()


# =============================================
# REQUEST/RESPONSE MODELS
# =============================================

class TrustCheckResponse(BaseModel):
    """Full trust check response — the money response."""
    target: str
    score: int
    grade: str
    risk_level: str
    recommendation: str
    is_verified: bool
    is_registered: bool
    entity_type: str
    identity_score: float
    competence_score: float
    solvency_score: float
    reputation_score: float
    network_score: float
    confidence: float
    data_freshness: str
    data_sources: List[str]
    signal_count: int
    calculated_at: str
    engine_version: str = "2.0.0"
    engine_author: str = "Market2Agent"


class TrustPreviewResponse(BaseModel):
    """Free preview — limited data, encourages upgrade."""
    target: str
    score: int
    grade: str
    recommendation: str
    is_verified: bool
    is_registered: bool
    confidence: float
    message: str = "Full breakdown available with an API key. Get yours at market2agent.ai"
    engine_author: str = "Market2Agent"


class TrustCheckCompactResponse(BaseModel):
    target: str
    score: int
    grade: str
    recommendation: str
    is_verified: bool
    confidence: float


class BatchTrustRequest(BaseModel):
    targets: List[str] = Field(..., min_length=1, max_length=25)


class BatchTrustResponse(BaseModel):
    results: List[TrustCheckCompactResponse]
    total: int
    checked: int
    engine_author: str = "Market2Agent"


class CompareRequest(BaseModel):
    entity_a: str
    entity_b: str


class CompareResponse(BaseModel):
    entity_a: TrustCheckCompactResponse
    entity_b: TrustCheckCompactResponse
    recommendation: str
    safer_entity: str
    engine_author: str = "Market2Agent"


class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    environment: str = Field("live", pattern="^(live|test)$")


class CreateKeyResponse(BaseModel):
    key: str
    key_id: str
    name: str
    prefix: str
    environment: str
    monthly_quota: int
    message: str = "Save this key now. It will not be shown again."


class KeyListResponse(BaseModel):
    key_id: str
    name: str
    prefix: str
    environment: str
    status: str
    created_at: Optional[str]
    last_used_at: Optional[str]
    usage_total: int = 0


class UsageResponse(BaseModel):
    calls_today: int
    calls_this_month: int
    monthly_quota: int
    remaining: int
    period: str


# =============================================
# AUTH DEPENDENCY
# =============================================

async def require_api_key(
    x_api_key: str = Header(None, alias="X-API-Key"),
    authorization: str = Header(None),
) -> dict:
    """Authenticate via API key."""
    raw_key = None
    if x_api_key:
        raw_key = x_api_key
    elif authorization and authorization.startswith("Bearer m2a_"):
        raw_key = authorization.replace("Bearer ", "")

    if not raw_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Pass via X-API-Key header or Bearer token. Get yours at market2agent.ai",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not raw_key.startswith("m2a_"):
        raise HTTPException(status_code=401, detail="Invalid API key format.")

    key_hash_val = hash_key(raw_key)
    meter = get_meter()

    key_data = meter.get_cached_key(key_hash_val)
    if not key_data:
        key_data = get_key_by_hash(key_hash_val)
        if key_data:
            meter.cache_key_metadata(key_hash_val, key_data)

    if not key_data:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key.")

    if key_data.get("status") != "active":
        raise HTTPException(status_code=403, detail=f"API key is {key_data.get('status')}.")

    try:
        rate_info = meter.check_rate_limit(
            key_hash=key_hash_val,
            limit_per_minute=key_data.get("rate_limit_per_minute", 60),
            limit_per_day=key_data.get("rate_limit_per_day", 10000),
        )
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "limit_type": e.limit_type,
                "limit": e.limit,
                "reset_seconds": e.reset_seconds,
            },
            headers={"Retry-After": str(e.reset_seconds)},
        )

    try:
        quota_info = meter.check_quota(
            key_hash=key_hash_val,
            monthly_quota=key_data.get("monthly_quota", 10000),
        )
    except QuotaExceeded as e:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "used": e.used,
                "quota": e.quota,
                "message": "Monthly quota exceeded. Upgrade at market2agent.ai/pricing",
            },
        )

    key_data["_hash"] = key_hash_val
    key_data["_rate_info"] = rate_info
    key_data["_quota_info"] = quota_info
    return key_data


# =============================================
# INTERNAL: Build scores from Neo4j + Open Web
# =============================================

def _build_trust_score_for_entity(entity_id: str) -> Optional[TrustScore]:
    """Build trust score for a REGISTERED entity (from Neo4j)."""
    try:
        from app.db.neo4j import get_session
    except ImportError:
        logger.warning("neo4j_not_available")
        return None

    with get_session() as session:
        result = session.run("""
            MATCH (e:Entity {entity_id: $eid})
            OPTIONAL MATCH (e)<-[:OWNS]-(u:User)
            OPTIONAL MATCH (u)-[:HAS_SUBSCRIPTION]->(s:Subscription)
            RETURN e {.*} as entity,
                   u {.id, .email, .stripe_customer_id} as owner,
                   s {.status, .tier} as subscription
        """, eid=entity_id)
        record = result.single()
        if not record:
            return None

        entity = dict(record["entity"])
        owner = dict(record["owner"]) if record["owner"] else {}
        sub = dict(record["subscription"]) if record["subscription"] else {}

        identity = IdentitySignals(
            domain_verified=entity.get("verified", False),
            dns_txt_verified=entity.get("verification_method") == "domain_dns",
            file_verified=entity.get("verification_method") == "domain_file",
            email_verified=entity.get("verification_method") == "email",
            has_structured_data=bool(entity.get("website")),
            has_organization_schema=entity.get("verified", False),
            has_wikidata_entry=bool(entity.get("wikidata_qid")),
            has_wikipedia_page=bool(entity.get("wikipedia_url")),
            has_crunchbase=bool(entity.get("crunchbase_url")),
            social_profiles_count=sum(1 for k in [
                "twitter_url", "linkedin_url", "facebook_url",
                "instagram_url", "youtube_url", "github_url",
            ] if entity.get(k)),
            ssl_valid=True if entity.get("website", "").startswith("https") else False,
            geo_score=entity.get("visibility_score", 0) or 0,
        )

        competence = CompetenceSignals(
            visibility_score=entity.get("visibility_score", 0) or 0,
        )

        solvency = SolvencySignals(
            has_payment_method=bool(owner.get("stripe_customer_id")),
            stripe_verified=bool(owner.get("stripe_customer_id")),
            subscription_active=sub.get("status") == "active",
            subscription_tier=sub.get("tier", "free"),
        )

        reputation = ReputationSignals()
        network = NetworkSignals()

        return calculate_trust_score(
            entity_id=entity_id,
            entity_name=entity.get("canonical_name", "Unknown"),
            entity_type=entity.get("category", "unknown"),
            is_verified=entity.get("verified", False),
            is_registered=True,
            identity=identity,
            competence=competence,
            solvency=solvency,
            reputation=reputation,
            network=network,
        )


async def _score_universal(target: str, force_refresh: bool = False, is_preview: bool = False) -> TrustScore:
    """
    Score ANY entity — registered or not.
    Now backed by the full compute pipeline:
        Cache → Collect (parallel, circuit-breaked) → Score → Persist → Return

    James Rausch's universal scoring: the open web is our database.
    """
    try:
        from app.compute.pipeline import compute_trust_score as pipeline_score
        result = await pipeline_score(target, force_refresh=force_refresh, is_preview=is_preview)

        # Convert pipeline dict → TrustScore for Pydantic response models
        return TrustScore(
            score=result.get("score", 0),
            grade=TrustGrade(result.get("grade", "D")),
            risk_level=RiskLevel(result.get("risk_level", "critical")),
            recommendation=Recommendation(result.get("recommendation", "reject")),
            identity_score=result.get("identity_score", 0),
            competence_score=result.get("competence_score", 0),
            solvency_score=result.get("solvency_score", 0),
            reputation_score=result.get("reputation_score", 0),
            network_score=result.get("network_score", 0),
            identity_signals=result.get("signals", {}).get("identity", result.get("identity_signals", {})),
            competence_signals=result.get("signals", {}).get("competence", result.get("competence_signals", {})),
            solvency_signals=result.get("signals", {}).get("solvency", result.get("solvency_signals", {})),
            reputation_signals=result.get("signals", {}).get("reputation", result.get("reputation_signals", {})),
            network_signals=result.get("signals", {}).get("network", result.get("network_signals", {})),
            entity_id=result.get("entity_id", f"unknown:{target}"),
            entity_name=result.get("entity_name", target),
            entity_type=result.get("entity_type", "unknown"),
            is_verified=result.get("is_verified", False),
            is_registered=result.get("is_registered", False),
            calculated_at=result.get("calculated_at", ""),
            confidence=result.get("confidence", 0.0),
            data_freshness=result.get("data_freshness", "unknown"),
            data_sources=result.get("data_sources", []),
            signal_count=result.get("signal_count", 0),
        )

    except ImportError:
        # Fallback: compute pipeline not installed — use direct collector path
        logger.warning("compute_pipeline_not_available_falling_back_to_direct")
        return await _score_universal_fallback(target)
    except Exception as e:
        logger.error("pipeline_score_failed", target=target, error=str(e))
        return calculate_trust_score(
            entity_id=f"error:{target}",
            entity_name=target,
            entity_type="unknown",
            is_verified=False,
            is_registered=False,
            data_freshness="failed",
            data_sources=["none"],
        )


async def _score_universal_fallback(target: str) -> TrustScore:
    """Direct scoring fallback when compute pipeline is unavailable."""
    registered_data = _lookup_entity(target)

    if registered_data:
        trust = _build_trust_score_for_entity(registered_data["entity_id"])
        if trust:
            return trust

    try:
        from app.collectors.open_web import score_any_entity
        result = await score_any_entity(target, registered_data)
        return TrustScore(
            score=result["score"],
            grade=TrustGrade(result["grade"]),
            risk_level=RiskLevel(result["risk_level"]),
            recommendation=Recommendation(result["recommendation"]),
            identity_score=result["identity_score"],
            competence_score=result["competence_score"],
            solvency_score=result["solvency_score"],
            reputation_score=result["reputation_score"],
            network_score=result["network_score"],
            entity_id=result["entity_id"],
            entity_name=result["entity_name"],
            entity_type=result.get("entity_type", "unknown"),
            is_verified=result["is_verified"],
            is_registered=result["is_registered"],
            calculated_at=result["calculated_at"],
            confidence=result["confidence"],
            data_freshness=result["data_freshness"],
            data_sources=result.get("data_sources", ["public_web"]),
            signal_count=result.get("signal_count", 0),
        )
    except Exception as e:
        logger.error("fallback_scoring_failed", target=target, error=str(e))
        return calculate_trust_score(
            entity_id=f"unknown:{target}",
            entity_name=target,
            entity_type="unknown",
            is_verified=False,
            is_registered=False,
            data_freshness="failed",
            data_sources=["none"],
        )


def _lookup_entity(identifier: str) -> Optional[dict]:
    """Look up entity by ID, slug, or domain."""
    try:
        from app.db.neo4j import get_session
    except ImportError:
        return None

    with get_session() as session:
        result = session.run("""
            MATCH (e:Entity)
            WHERE e.entity_id = $id
               OR e.slug = $id
               OR e.website = $id
               OR e.domain = $id
            RETURN e {.*} as entity
            LIMIT 1
        """, id=identifier)
        record = result.single()
        return dict(record["entity"]) if record else None


# =============================================
# TRUST API ROUTES
# =============================================

trust_router = APIRouter(prefix="/v1/trust", tags=["trust"])


@trust_router.get("/health")
async def trust_health():
    """Health check for the Trust API."""
    return {
        "status": "healthy",
        "service": "market2agent-universal-trust-api",
        "version": "2.0.0",
        "author": "James Rausch — Lead Visionary & Pilot of the Trust Score Revolution",
        "capabilities": [
            "universal_scoring",      # Score any entity on Earth
            "registered_scoring",     # Enhanced scoring for registered entities
            "batch_scoring",          # Score up to 25 at once
            "comparison",             # Compare two entities side-by-side
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@trust_router.get("/lookup/{identifier}")
async def public_lookup(identifier: str):
    """
    Public entity lookup. Returns basic info, no trust score.
    Free — no API key needed.
    """
    entity = _lookup_entity(identifier)
    if not entity:
        return {
            "identifier": identifier,
            "registered": False,
            "message": "Entity not found in registry. You can still score it with /v1/trust/score — we'll check the open web.",
            "score_url": f"/v1/trust/score?target={identifier}",
        }

    return {
        "entity_id": entity.get("entity_id"),
        "name": entity.get("canonical_name"),
        "slug": entity.get("slug"),
        "verified": entity.get("verified", False),
        "category": entity.get("category"),
        "website": entity.get("website"),
        "registered": True,
    }


@trust_router.get("/preview", response_model=TrustPreviewResponse)
async def trust_preview(
    request: Request,
    target: str = Query(..., description="Any identifier: domain, URL, name, email, agent ID"),
):
    """
    Free trust score preview — powered by v3 evidence accumulation engine.
    Returns score and grade but NOT the full breakdown.
    Rate-limited to 10 per minute per IP via Redis sliding window.
    """
    from app.rate_limit import rate_limit_preview
    await rate_limit_preview(request)

    # v3 engine — real data from real sources
    from app.compute.pipeline_v3 import score_entity
    result = await score_entity(target)

    return TrustPreviewResponse(
        target=target,
        score=result.get("score", 0),
        grade=result.get("grade", "D"),
        recommendation=result.get("recommendation", "REJECT"),
        is_verified=result.get("is_verified", False),
        is_registered=result.get("is_registered", False),
        confidence=result.get("confidence", 0.0),
    )


# ── TrustChain Endpoints (the product) ───────────

@trust_router.get("/chain/{entity_id}/verify")
async def verify_chain(entity_id: str):
    """
    Verify the integrity of an entity's observation chain.
    Returns cryptographic proof that no data has been tampered with.
    This is the audit endpoint. This is what makes the data trustworthy.
    """
    from app.chain.trustchain import get_chain
    chain = get_chain()
    result = chain.verify_chain(entity_id)
    result["entity_id"] = entity_id
    return result


@trust_router.get("/chain/{entity_id}/history")
async def chain_history(entity_id: str, limit: int = Query(default=20, le=100)):
    """
    Get the observation history for an entity.
    Every block is a timestamped, hashed observation from an independent sensor.
    """
    from app.chain.trustchain import get_chain
    chain = get_chain()
    blocks = chain.get_entity_history(entity_id, limit=limit)
    return {
        "entity_id": entity_id,
        "blocks": blocks,
        "total_blocks": chain.get_block_count(entity_id),
        "chain_verified": chain.verify_chain(entity_id, limit=limit)["verified"],
    }


@trust_router.get("/chain/stats")
async def chain_stats():
    """
    Global chain statistics. How big is the data lake?
    """
    from app.chain.trustchain import get_chain
    chain = get_chain()
    return {
        "total_entities": chain.get_entity_count(),
        "engine_version": "4.0.0",
        "storage": "trustchain",
    }


@trust_router.get("/check", response_model=TrustCheckResponse)
async def trust_check(
    target: str = Query(..., description="Entity ID, slug, or domain"),
    key_data: dict = Depends(require_api_key),
):
    """
    Full trust check — the money endpoint.
    Works for BOTH registered and unregistered entities.

    Registered entities get richer scores from internal data.
    Unregistered entities are scored from the open web.
    Every entity on Earth gets a score. That's the James Rausch promise.

    Usage:
        GET /v1/trust/check?target=stripe.com
        Headers: X-API-Key: m2a_live_...
    """
    trust = await _score_universal(target)

    # Record billable usage
    meter = get_meter()
    meter.record_usage(
        key_hash=key_data["_hash"],
        user_id=key_data.get("user_id", "unknown"),
        endpoint="trust_check",
    )
    record_key_usage(key_data["_hash"])

    logger.info("trust_check",
                target=target,
                score=trust.score,
                grade=trust.grade.value,
                registered=trust.is_registered,
                key_prefix=key_data.get("prefix"))

    return TrustCheckResponse(
        target=target,
        score=trust.score,
        grade=trust.grade.value,
        risk_level=trust.risk_level.value,
        recommendation=trust.recommendation.value,
        is_verified=trust.is_verified,
        is_registered=trust.is_registered,
        entity_type=trust.entity_type,
        identity_score=trust.identity_score,
        competence_score=trust.competence_score,
        solvency_score=trust.solvency_score,
        reputation_score=trust.reputation_score,
        network_score=trust.network_score,
        confidence=trust.confidence,
        data_freshness=trust.data_freshness,
        data_sources=trust.data_sources,
        signal_count=trust.signal_count,
        calculated_at=trust.calculated_at,
    )


@trust_router.get("/score", response_model=TrustCheckResponse)
async def universal_score(
    target: str = Query(..., description="ANYTHING: domain, URL, company name, email, agent ID, @handle"),
    key_data: dict = Depends(require_api_key),
):
    """
    Universal Trust Score — James Rausch's flagship endpoint.

    Score ANY entity on the planet. Pass in anything:
        - stripe.com          → Scores Stripe from web + registry
        - @elonmusk           → Scores the social handle
        - openai              → Scores by name lookup
        - 0x1234...           → Scores a smart contract
        - random-new-bot.ai   → Scores an unknown domain from DNS/web signals

    Every query returns a score. Confidence reflects data availability.
    """
    trust = await _score_universal(target)

    # Billable
    meter = get_meter()
    meter.record_usage(
        key_hash=key_data["_hash"],
        user_id=key_data.get("user_id", "unknown"),
        endpoint="universal_score",
    )
    record_key_usage(key_data["_hash"])

    logger.info("universal_score",
                target=target,
                score=trust.score,
                grade=trust.grade.value,
                confidence=trust.confidence,
                registered=trust.is_registered)

    return TrustCheckResponse(
        target=target,
        score=trust.score,
        grade=trust.grade.value,
        risk_level=trust.risk_level.value,
        recommendation=trust.recommendation.value,
        is_verified=trust.is_verified,
        is_registered=trust.is_registered,
        entity_type=trust.entity_type,
        identity_score=trust.identity_score,
        competence_score=trust.competence_score,
        solvency_score=trust.solvency_score,
        reputation_score=trust.reputation_score,
        network_score=trust.network_score,
        confidence=trust.confidence,
        data_freshness=trust.data_freshness,
        data_sources=trust.data_sources,
        signal_count=trust.signal_count,
        calculated_at=trust.calculated_at,
    )


@trust_router.post("/batch", response_model=BatchTrustResponse)
async def batch_trust_check(
    request: BatchTrustRequest,
    key_data: dict = Depends(require_api_key),
):
    """
    Batch trust check — up to 25 entities at once.
    Works for ANY entity: registered, unregistered, domains, names, whatever.
    Each entity in the batch counts as one API call.
    """
    results = []
    meter = get_meter()

    for target in request.targets:
        try:
            trust = await _score_universal(target)
            results.append(TrustCheckCompactResponse(
                target=target,
                score=trust.score,
                grade=trust.grade.value,
                recommendation=trust.recommendation.value,
                is_verified=trust.is_verified,
                confidence=trust.confidence,
            ))
            meter.record_usage(
                key_hash=key_data["_hash"],
                user_id=key_data.get("user_id", "unknown"),
                endpoint="trust_check_batch",
            )
        except Exception as e:
            logger.warning("batch_item_failed", target=target, error=str(e))
            results.append(TrustCheckCompactResponse(
                target=target,
                score=0,
                grade="D",
                recommendation="MANUAL_REVIEW",
                is_verified=False,
                confidence=0.0,
            ))

    record_key_usage(key_data["_hash"])

    return BatchTrustResponse(
        results=results,
        total=len(request.targets),
        checked=len([r for r in results if r.score > 0]),
    )


@trust_router.get("/compare")
async def compare_entities(
    entity_a: str = Query(..., description="First entity"),
    entity_b: str = Query(..., description="Second entity"),
    key_data: dict = Depends(require_api_key),
):
    """
    Compare two entities side-by-side.
    Useful for agent-to-agent trust decisions.
    Counts as 2 API calls.
    """
    trust_a = await _score_universal(entity_a)
    trust_b = await _score_universal(entity_b)

    meter = get_meter()
    meter.record_usage(key_hash=key_data["_hash"], user_id=key_data.get("user_id", "unknown"), endpoint="compare")
    meter.record_usage(key_hash=key_data["_hash"], user_id=key_data.get("user_id", "unknown"), endpoint="compare")
    record_key_usage(key_data["_hash"])

    safer = entity_a if trust_a.score >= trust_b.score else entity_b

    if trust_a.score >= 700 and trust_b.score >= 700:
        rec = "Both entities are trustworthy. Proceed with either."
    elif trust_a.score >= 700 or trust_b.score >= 700:
        rec = f"Prefer {safer} — significantly higher trust score."
    else:
        rec = "Both entities have low trust scores. Exercise caution with both."

    return {
        "entity_a": {
            "target": entity_a,
            "score": trust_a.score,
            "grade": trust_a.grade.value,
            "recommendation": trust_a.recommendation.value,
            "is_verified": trust_a.is_verified,
            "confidence": trust_a.confidence,
        },
        "entity_b": {
            "target": entity_b,
            "score": trust_b.score,
            "grade": trust_b.grade.value,
            "recommendation": trust_b.recommendation.value,
            "is_verified": trust_b.is_verified,
            "confidence": trust_b.confidence,
        },
        "safer_entity": safer,
        "recommendation": rec,
        "engine_author": "James Rausch — Lead Visionary, Market2Agent",
    }


@trust_router.get("/usage", response_model=UsageResponse)
async def get_usage(key_data: dict = Depends(require_api_key)):
    """Check your API key usage and remaining quota."""
    meter = get_meter()
    usage = meter.get_usage_for_key(key_data["_hash"])
    quota = key_data.get("monthly_quota", 10000)

    return UsageResponse(
        calls_today=usage["today"],
        calls_this_month=usage["month"],
        monthly_quota=quota,
        remaining=max(0, quota - usage["month"]),
        period=datetime.now(timezone.utc).strftime("%Y-%m"),
    )


# =============================================
# KEY MANAGEMENT ROUTES
# =============================================

keys_router = APIRouter(prefix="/v1/keys", tags=["api-keys"])


@keys_router.post("/", response_model=CreateKeyResponse)
async def create_key(
    data: CreateKeyRequest,
    user: dict = Depends(require_auth),
):
    """Create a new API key. The full key is returned ONCE."""
    user_id = user.get("id", user.get("user_id"))
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    full_key, key_data = create_api_key(
        user_id=user_id,
        name=data.name,
        environment=data.environment,
    )

    return CreateKeyResponse(
        key=full_key,
        key_id=key_data.get("key_id", ""),
        name=data.name,
        prefix=key_data.get("prefix", ""),
        environment=data.environment,
        monthly_quota=key_data.get("monthly_quota", 10000),
    )


@keys_router.get("/", response_model=List[KeyListResponse])
async def list_keys(user: dict = Depends(require_auth)):
    """List all API keys for the current user."""
    user_id = user.get("id", user.get("user_id"))
    keys = get_keys_for_user(user_id)
    return [
        KeyListResponse(
            key_id=k.get("key_id", ""),
            name=k.get("name", ""),
            prefix=k.get("prefix", ""),
            environment=k.get("environment", "live"),
            status=k.get("status", "active"),
            created_at=str(k.get("created_at", "")),
            last_used_at=str(k.get("last_used_at", "")) if k.get("last_used_at") else None,
            usage_total=k.get("usage_total", 0),
        )
        for k in keys
    ]


@keys_router.delete("/{key_id}")
async def delete_key(key_id: str, user: dict = Depends(require_auth)):
    """Revoke an API key. Permanent."""
    user_id = user.get("id", user.get("user_id"))
    success = revoke_api_key(key_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found or already revoked.")
    return {"message": "API key revoked.", "key_id": key_id}


# =============================================
# ADMIN ROUTES
# =============================================

admin_trust_router = APIRouter(prefix="/v1/admin/trust", tags=["admin-trust"])


@admin_trust_router.get("/stats")
async def admin_trust_stats(user: dict = Depends(require_admin)):
    """Admin: Platform-wide trust API stats. Requires admin auth."""
    meter = get_meter()
    stats = meter.get_global_stats()

    # Include pipeline status
    pipeline_info = {}
    try:
        from app.compute.pipeline import pipeline_status
        pipeline_info = pipeline_status()
    except ImportError:
        pipeline_info = {"available": False}

    return {
        "platform_stats": stats,
        "pipeline": pipeline_info,
        "engine_version": "2.0.0",
        "engine_author": "James Rausch — Lead Visionary & Pilot of the Trust Score Revolution",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
