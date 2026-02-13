"""
Market2Agent Platform - Entity API

Endpoints for the Entity Registry.

Public endpoints:
    GET  /entities/:slug              - Public entity profile
    GET  /entities/search             - Search entities

Authenticated endpoints:
    POST /entities/claim              - Claim a new entity
    GET  /entities/mine               - List user's entities
    PUT  /entities/:id                - Update entity
    POST /entities/:id/verify         - Start verification
    GET  /entities/:id/verify/status  - Check verification status
    POST /entities/:id/competitors    - Add competitor tracking
"""
import secrets
import hashlib
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from pydantic import BaseModel, Field, HttpUrl
import structlog

from app.security import require_auth, get_current_user
from app.entities.model import (
    Entity, EntityStatus, VerificationMethod, CATEGORY_TAXONOMY,
    create_entity, get_entity_by_id, get_entity_by_slug, get_entity_by_domain,
    get_entities_for_user, get_tracked_entities, track_competitor,
    update_entity, verify_entity, search_entities, get_entities_in_category,
)

logger = structlog.get_logger()


# =============================================
# REQUEST/RESPONSE MODELS
# =============================================

class ClaimEntityRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    website: Optional[HttpUrl] = None
    category: Optional[str] = None


class UpdateEntityRequest(BaseModel):
    canonical_name: Optional[str] = None
    legal_name: Optional[str] = None
    description: Optional[str] = Field(None, max_length=2000)
    short_description: Optional[str] = Field(None, max_length=160)
    category: Optional[str] = None
    subcategories: Optional[List[str]] = None
    headquarters_city: Optional[str] = None
    headquarters_region: Optional[str] = None
    headquarters_country: Optional[str] = None
    service_areas: Optional[List[str]] = None
    founded_year: Optional[int] = None
    employee_count_range: Optional[str] = None
    revenue_range: Optional[str] = None
    company_type: Optional[str] = None
    website: Optional[str] = None
    logo_url: Optional[str] = None
    twitter_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    facebook_url: Optional[str] = None
    instagram_url: Optional[str] = None
    youtube_url: Optional[str] = None
    github_url: Optional[str] = None


class EntityResponse(BaseModel):
    entity_id: str
    slug: str
    canonical_name: str
    status: str
    verified: bool
    description: Optional[str]
    short_description: Optional[str]
    category: Optional[str]
    headquarters_city: Optional[str]
    headquarters_country: Optional[str]
    website: Optional[str]
    logo_url: Optional[str]
    visibility_score: Optional[float]
    visibility_trend: Optional[str]
    completeness_score: int
    
    # Links (for public profile)
    twitter_url: Optional[str]
    linkedin_url: Optional[str]
    
    # Knowledge graph
    wikidata_qid: Optional[str]
    wikipedia_url: Optional[str]


class EntityPublicResponse(BaseModel):
    """Public-facing entity profile (for unauthenticated access)."""
    slug: str
    name: str
    description: Optional[str]
    category: Optional[str]
    website: Optional[str]
    logo_url: Optional[str]
    verified: bool
    visibility_score: Optional[float]
    json_ld: dict  # Schema.org structured data


class StartVerificationResponse(BaseModel):
    method: str
    instructions: str
    verification_token: str
    expires_in_hours: int


class VerificationStatusResponse(BaseModel):
    entity_id: str
    verified: bool
    method: Optional[str]
    verified_at: Optional[str]


class AddCompetitorRequest(BaseModel):
    entity_id: str  # Entity ID of competitor to track


# =============================================
# PUBLIC ENDPOINTS
# =============================================

public_router = APIRouter(prefix="/entities", tags=["entities-public"])


@public_router.get("/{slug}", response_model=EntityPublicResponse)
async def get_public_entity(slug: str):
    """
    Get public entity profile.
    This page is SEO-optimized and crawlable.
    """
    entity = get_entity_by_slug(slug)
    
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Only show verified entities publicly (or all in early stage)
    # if not entity.verified:
    #     raise HTTPException(status_code=404, detail="Entity not found")
    
    return EntityPublicResponse(
        slug=entity.slug,
        name=entity.canonical_name,
        description=entity.description,
        category=entity.category,
        website=entity.website,
        logo_url=entity.logo_url,
        verified=entity.verified,
        visibility_score=entity.visibility_score,
        json_ld=entity.to_json_ld(),
    )


@public_router.get("/", response_model=List[EntityPublicResponse])
async def search_public_entities(
    q: str = Query(None, min_length=2, description="Search query"),
    category: str = Query(None, description="Filter by category"),
    limit: int = Query(20, le=100),
):
    """
    Search entities.
    Used for finding businesses to claim or track.
    """
    if not q and not category:
        raise HTTPException(status_code=400, detail="Provide search query or category")
    
    if q:
        entities = search_entities(query=q, category=category, limit=limit)
    else:
        entities = get_entities_in_category(category=category, limit=limit)
    
    return [
        EntityPublicResponse(
            slug=e.slug,
            name=e.canonical_name,
            description=e.description,
            category=e.category,
            website=e.website,
            logo_url=e.logo_url,
            verified=e.verified,
            visibility_score=e.visibility_score,
            json_ld=e.to_json_ld(),
        )
        for e in entities
    ]


@public_router.get("/categories/list")
async def list_categories():
    """Get available categories for entity registration."""
    return CATEGORY_TAXONOMY


# =============================================
# AUTHENTICATED ENDPOINTS
# =============================================

user_router = APIRouter(prefix="/v1/entities", tags=["entities"])


@user_router.post("/claim", response_model=EntityResponse)
async def claim_entity(
    data: ClaimEntityRequest,
    user: dict = Depends(require_auth),
):
    """
    Claim a new entity.
    Creates the entity and assigns ownership to current user.
    Entity starts unverified - must complete verification to unlock features.
    """
    # Check if entity already exists for this domain
    if data.website:
        from urllib.parse import urlparse
        domain = urlparse(str(data.website)).netloc.replace("www.", "")
        existing = get_entity_by_domain(domain)
        if existing and existing.verified:
            raise HTTPException(
                status_code=409,
                detail=f"Entity already claimed for domain {domain}"
            )
    
    # Validate category
    if data.category and data.category not in CATEGORY_TAXONOMY:
        raise HTTPException(status_code=400, detail=f"Invalid category: {data.category}")
    
    # Create entity
    entity = create_entity(
        name=data.name,
        owner_user_id=user["id"],
        website=str(data.website) if data.website else None,
        category=data.category,
    )
    
    logger.info("entity_claimed",
                entity_id=entity.entity_id,
                user_id=user["id"],
                name=data.name)
    
    return _entity_to_response(entity)


@user_router.get("/mine", response_model=List[EntityResponse])
async def get_my_entities(user: dict = Depends(require_auth)):
    """Get all entities owned by current user."""
    entities = get_entities_for_user(user["id"])
    return [_entity_to_response(e) for e in entities]


@user_router.get("/tracking", response_model=List[EntityResponse])
async def get_tracked_competitors(user: dict = Depends(require_auth)):
    """Get competitor entities the user is tracking."""
    entities = get_tracked_entities(user["id"])
    return [_entity_to_response(e) for e in entities]


@user_router.get("/{entity_id}", response_model=EntityResponse)
async def get_entity(
    entity_id: str,
    user: dict = Depends(require_auth),
):
    """Get entity details. Must be owner or tracking."""
    entity = get_entity_by_id(entity_id)
    
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Check ownership or tracking
    owned = get_entities_for_user(user["id"])
    tracked = get_tracked_entities(user["id"])
    
    owned_ids = {e.entity_id for e in owned}
    tracked_ids = {e.entity_id for e in tracked}
    
    if entity_id not in owned_ids and entity_id not in tracked_ids:
        raise HTTPException(status_code=403, detail="Not authorized to view this entity")
    
    return _entity_to_response(entity)


@user_router.put("/{entity_id}", response_model=EntityResponse)
async def update_entity_details(
    entity_id: str,
    data: UpdateEntityRequest,
    user: dict = Depends(require_auth),
):
    """
    Update entity details.
    Only owner can update.
    """
    entity = get_entity_by_id(entity_id)
    
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    if entity.owner_user_id != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can update entity")
    
    # Validate category if provided
    if data.category and data.category not in CATEGORY_TAXONOMY:
        raise HTTPException(status_code=400, detail=f"Invalid category: {data.category}")
    
    # Update
    updates = data.dict(exclude_unset=True, exclude_none=True)
    updated = update_entity(entity_id, updates)
    
    if not updated:
        raise HTTPException(status_code=500, detail="Update failed")
    
    logger.info("entity_updated", entity_id=entity_id, fields=list(updates.keys()))
    
    return _entity_to_response(updated)


@user_router.post("/{entity_id}/competitors", response_model=dict)
async def add_competitor_tracking(
    entity_id: str,
    data: AddCompetitorRequest,
    user: dict = Depends(require_auth),
):
    """
    Add a competitor to track.
    Both entities must exist.
    """
    # Verify user owns the source entity
    owned = get_entities_for_user(user["id"])
    owned_ids = {e.entity_id for e in owned}
    
    if entity_id not in owned_ids:
        raise HTTPException(status_code=403, detail="You must own the entity to add competitors")
    
    # Verify competitor exists
    competitor = get_entity_by_id(data.entity_id)
    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor entity not found")
    
    # Can't track yourself
    if entity_id == data.entity_id:
        raise HTTPException(status_code=400, detail="Cannot track yourself as competitor")
    
    # Add tracking
    track_competitor(user["id"], data.entity_id)
    
    logger.info("competitor_tracked",
                entity_id=entity_id,
                competitor_id=data.entity_id,
                user_id=user["id"])
    
    return {"message": f"Now tracking {competitor.canonical_name}"}


# =============================================
# VERIFICATION ENDPOINTS
# =============================================

# In-memory verification tokens (use Redis in production)
_verification_tokens = {}


@user_router.post("/{entity_id}/verify", response_model=StartVerificationResponse)
async def start_verification(
    entity_id: str,
    method: str = Query("domain_dns", description="Verification method"),
    user: dict = Depends(require_auth),
):
    """
    Start entity verification.
    
    Methods:
    - domain_dns: Add TXT record to domain
    - domain_file: Upload file to website
    - email: Verify email at domain
    """
    entity = get_entity_by_id(entity_id)
    
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    if entity.owner_user_id != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can verify")
    
    if entity.verified:
        raise HTTPException(status_code=400, detail="Entity already verified")
    
    if not entity.website:
        raise HTTPException(status_code=400, detail="Website required for verification")
    
    # Generate verification token
    token = f"m2a-verify-{secrets.token_hex(16)}"
    
    # Store token with expiry
    _verification_tokens[entity_id] = {
        "token": token,
        "method": method,
        "user_id": user["id"],
    }
    
    # Generate instructions based on method
    from urllib.parse import urlparse
    domain = urlparse(entity.website).netloc.replace("www.", "")
    
    if method == "domain_dns":
        instructions = f"""
Add a TXT record to your domain's DNS:

Host: _m2a-verify.{domain}
Type: TXT
Value: {token}

DNS changes can take up to 48 hours to propagate.
Once added, call the verify/complete endpoint.
"""
    elif method == "domain_file":
        instructions = f"""
Create a file at: {entity.website}/.well-known/m2a-verify.txt

The file should contain exactly this text:
{token}

Then call the verify/complete endpoint.
"""
    elif method == "email":
        instructions = f"""
We will send a verification email to admin@{domain} or webmaster@{domain}.

Click the link in the email to complete verification.
"""
    else:
        raise HTTPException(status_code=400, detail=f"Unknown verification method: {method}")
    
    logger.info("verification_started",
                entity_id=entity_id,
                method=method,
                user_id=user["id"])
    
    return StartVerificationResponse(
        method=method,
        instructions=instructions.strip(),
        verification_token=token,
        expires_in_hours=48,
    )


@user_router.post("/{entity_id}/verify/complete")
async def complete_verification(
    entity_id: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_auth),
):
    """
    Complete entity verification.
    Checks that verification requirements are met.
    """
    entity = get_entity_by_id(entity_id)
    
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    if entity.owner_user_id != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can verify")
    
    if entity.verified:
        return {"message": "Already verified", "verified": True}
    
    # Get pending verification
    pending = _verification_tokens.get(entity_id)
    if not pending:
        raise HTTPException(status_code=400, detail="No pending verification. Start verification first.")
    
    if pending["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Verification started by different user")
    
    method = pending["method"]
    token = pending["token"]
    
    from urllib.parse import urlparse
    domain = urlparse(entity.website).netloc.replace("www.", "")
    
    # Check verification based on method
    verified = False
    
    if method == "domain_dns":
        # Check DNS TXT record
        import dns.resolver
        try:
            answers = dns.resolver.resolve(f"_m2a-verify.{domain}", "TXT")
            for rdata in answers:
                if token in str(rdata):
                    verified = True
                    break
        except Exception as e:
            logger.warning("dns_check_failed", domain=domain, error=str(e))
    
    elif method == "domain_file":
        # Check file at well-known path
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{entity.website}/.well-known/m2a-verify.txt",
                    timeout=10,
                    follow_redirects=True,
                )
                if resp.status_code == 200 and token in resp.text:
                    verified = True
        except Exception as e:
            logger.warning("file_check_failed", website=entity.website, error=str(e))
    
    elif method == "email":
        # Email verification handled separately via callback
        raise HTTPException(status_code=400, detail="Email verification requires clicking link in email")
    
    if verified:
        verify_entity(entity_id, method)
        del _verification_tokens[entity_id]
        
        logger.info("entity_verified",
                    entity_id=entity_id,
                    method=method,
                    user_id=user["id"])
        
        # Trigger initial visibility indexing in background
        background_tasks.add_task(
            _run_initial_visibility_check,
            entity_id=entity_id,
        )
        
        return {"message": "Verification successful!", "verified": True}
    else:
        return {
            "message": "Verification not yet detected. Make sure you've completed the steps.",
            "verified": False,
            "method": method,
        }


@user_router.get("/{entity_id}/verify/status", response_model=VerificationStatusResponse)
async def get_verification_status(
    entity_id: str,
    user: dict = Depends(require_auth),
):
    """Check verification status of an entity."""
    entity = get_entity_by_id(entity_id)
    
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    return VerificationStatusResponse(
        entity_id=entity_id,
        verified=entity.verified,
        method=entity.verification_method,
        verified_at=entity.verified_at,
    )


# =============================================
# HELPERS
# =============================================

def _entity_to_response(entity: Entity) -> EntityResponse:
    """Convert Entity to API response."""
    return EntityResponse(
        entity_id=entity.entity_id,
        slug=entity.slug,
        canonical_name=entity.canonical_name,
        status=entity.status,
        verified=entity.verified,
        description=entity.description,
        short_description=entity.short_description,
        category=entity.category,
        headquarters_city=entity.headquarters_city,
        headquarters_country=entity.headquarters_country,
        website=entity.website,
        logo_url=entity.logo_url,
        visibility_score=entity.visibility_score,
        visibility_trend=entity.visibility_trend,
        completeness_score=entity.completeness_score,
        twitter_url=entity.twitter_url,
        linkedin_url=entity.linkedin_url,
        wikidata_qid=entity.wikidata_qid,
        wikipedia_url=entity.wikipedia_url,
    )


async def _run_initial_visibility_check(entity_id: str):
    """Background task to run initial visibility check after verification."""
    from app.visibility.monitor import index_entity_visibility
    from app.entities.model import get_entity_by_id
    
    entity = get_entity_by_id(entity_id)
    if not entity:
        return
    
    try:
        await index_entity_visibility(
            entity_id=entity_id,
            entity_name=entity.canonical_name,
            category=entity.category or "general",
            location=entity.headquarters_city,
        )
    except Exception as e:
        logger.error("initial_visibility_check_failed",
                     entity_id=entity_id,
                     error=str(e))
