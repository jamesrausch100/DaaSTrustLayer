"""
Market2Agent Platform - Visibility API

Endpoints for AI Visibility Index.

Authenticated endpoints:
    GET  /v1/visibility/:entity_id          - Get visibility score
    GET  /v1/visibility/:entity_id/history  - Get historical visibility
    GET  /v1/visibility/:entity_id/compare  - Compare vs competitors
    POST /v1/visibility/:entity_id/refresh  - Trigger manual refresh
"""
from typing import List, Optional
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from pydantic import BaseModel
import structlog

from app.security import require_auth, require_subscription
from app.entities.model import get_entity_by_id, get_entities_for_user, get_tracked_entities
from app.visibility.monitor import (
    VisibilityScore, index_entity_visibility,
    AISystem, PromptCategory,
)

logger = structlog.get_logger()


# =============================================
# RESPONSE MODELS
# =============================================

class VisibilityResponse(BaseModel):
    entity_id: str
    entity_name: str
    
    overall_score: float
    trend: str
    trend_delta: float
    
    mention_rate: float
    sentiment_score: float
    position_score: float
    recommendation_rate: float
    
    system_scores: dict
    category_scores: dict
    
    last_updated: Optional[str]


class VisibilityHistoryPoint(BaseModel):
    date: str
    score: float
    mention_rate: float
    sentiment_score: float


class VisibilityHistoryResponse(BaseModel):
    entity_id: str
    period_days: int
    data_points: List[VisibilityHistoryPoint]


class CompetitorComparison(BaseModel):
    entity_id: str
    entity_name: str
    visibility_score: float
    trend: str
    is_owned: bool


class VisibilityCompareResponse(BaseModel):
    your_entity: CompetitorComparison
    competitors: List[CompetitorComparison]
    your_rank: int
    total_compared: int


# =============================================
# ENDPOINTS
# =============================================

router = APIRouter(prefix="/v1/visibility", tags=["visibility"])


@router.get("/{entity_id}", response_model=VisibilityResponse)
async def get_visibility(
    entity_id: str,
    user: dict = Depends(require_auth),
):
    """
    Get current visibility score for an entity.
    User must own or be tracking the entity.
    """
    entity = get_entity_by_id(entity_id)
    
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Check authorization
    owned = {e.entity_id for e in get_entities_for_user(user["id"])}
    tracked = {e.entity_id for e in get_tracked_entities(user["id"])}
    
    if entity_id not in owned and entity_id not in tracked:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Return current visibility data
    # In production, this would pull from stored VisibilityRecord
    # For now, return what's denormalized on the entity
    
    return VisibilityResponse(
        entity_id=entity_id,
        entity_name=entity.canonical_name,
        overall_score=entity.visibility_score or 0,
        trend=entity.visibility_trend or "new",
        trend_delta=0,  # Would come from history comparison
        mention_rate=0,  # Would come from latest record
        sentiment_score=50,
        position_score=0,
        recommendation_rate=0,
        system_scores={},
        category_scores={},
        last_updated=entity.visibility_updated_at,
    )


@router.get("/{entity_id}/history", response_model=VisibilityHistoryResponse)
async def get_visibility_history(
    entity_id: str,
    days: int = Query(30, ge=7, le=365),
    user: dict = Depends(require_subscription),  # Requires paid tier
):
    """
    Get historical visibility data.
    Paid feature - requires active subscription.
    """
    entity = get_entity_by_id(entity_id)
    
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Check authorization
    owned = {e.entity_id for e in get_entities_for_user(user["id"])}
    tracked = {e.entity_id for e in get_tracked_entities(user["id"])}
    
    if entity_id not in owned and entity_id not in tracked:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # In production, query from Postgres time-series table
    # For now, return placeholder
    
    return VisibilityHistoryResponse(
        entity_id=entity_id,
        period_days=days,
        data_points=[],  # Would be populated from visibility_records table
    )


@router.get("/{entity_id}/compare", response_model=VisibilityCompareResponse)
async def compare_visibility(
    entity_id: str,
    user: dict = Depends(require_subscription),  # Requires paid tier
):
    """
    Compare visibility against tracked competitors.
    Paid feature - requires active subscription.
    """
    entity = get_entity_by_id(entity_id)
    
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Must own this entity
    owned = get_entities_for_user(user["id"])
    owned_ids = {e.entity_id for e in owned}
    
    if entity_id not in owned_ids:
        raise HTTPException(status_code=403, detail="You must own this entity to compare")
    
    # Get tracked competitors
    tracked = get_tracked_entities(user["id"])
    
    # Build comparison
    all_entities = [entity] + tracked
    ranked = sorted(all_entities, key=lambda e: e.visibility_score or 0, reverse=True)
    
    your_rank = 1
    for i, e in enumerate(ranked):
        if e.entity_id == entity_id:
            your_rank = i + 1
            break
    
    competitors = [
        CompetitorComparison(
            entity_id=e.entity_id,
            entity_name=e.canonical_name,
            visibility_score=e.visibility_score or 0,
            trend=e.visibility_trend or "stable",
            is_owned=e.entity_id in owned_ids,
        )
        for e in tracked
    ]
    
    return VisibilityCompareResponse(
        your_entity=CompetitorComparison(
            entity_id=entity_id,
            entity_name=entity.canonical_name,
            visibility_score=entity.visibility_score or 0,
            trend=entity.visibility_trend or "new",
            is_owned=True,
        ),
        competitors=competitors,
        your_rank=your_rank,
        total_compared=len(all_entities),
    )


@router.post("/{entity_id}/refresh")
async def refresh_visibility(
    entity_id: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_subscription),  # Requires paid tier
):
    """
    Trigger a manual visibility refresh.
    Paid feature with rate limiting.
    """
    entity = get_entity_by_id(entity_id)
    
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Must own this entity
    owned = {e.entity_id for e in get_entities_for_user(user["id"])}
    
    if entity_id not in owned:
        raise HTTPException(status_code=403, detail="You must own this entity to refresh")
    
    # Check rate limit (max 1 refresh per day)
    if entity.visibility_updated_at:
        try:
            last_update = datetime.fromisoformat(entity.visibility_updated_at)
            if datetime.now(timezone.utc) - last_update.replace(tzinfo=timezone.utc) < timedelta(hours=24):
                raise HTTPException(
                    status_code=429,
                    detail="Can only refresh once per 24 hours"
                )
        except (ValueError, TypeError):
            pass
    
    # Queue background task
    background_tasks.add_task(
        _run_visibility_refresh,
        entity_id=entity_id,
        entity_name=entity.canonical_name,
        category=entity.category or "general",
        location=entity.headquarters_city,
    )
    
    logger.info("visibility_refresh_requested",
                entity_id=entity_id,
                user_id=user["id"])
    
    return {
        "message": "Visibility refresh queued",
        "entity_id": entity_id,
        "estimated_time_minutes": 5,
    }


async def _run_visibility_refresh(
    entity_id: str,
    entity_name: str,
    category: str,
    location: Optional[str],
):
    """Background task to refresh visibility."""
    try:
        # Get competitors for comparison
        from app.entities.model import get_entity_by_id
        entity = get_entity_by_id(entity_id)
        
        # In production, get actual tracked competitors
        competitors = []
        
        await index_entity_visibility(
            entity_id=entity_id,
            entity_name=entity_name,
            category=category,
            location=location,
            competitors=competitors,
        )
    except Exception as e:
        logger.error("visibility_refresh_failed",
                     entity_id=entity_id,
                     error=str(e))
