"""
Market2Agent - User Dashboard API
Manage tracked domains and view audit history.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, field_validator
from typing import List, Optional
import re

from app.db import get_session
from app.auth import require_auth, require_subscription

router = APIRouter(prefix="/v1/user", tags=["dashboard"])


# ===========================================
# Models
# ===========================================

class DomainAdd(BaseModel):
    domain: str
    
    @field_validator('domain')
    @classmethod
    def validate_domain(cls, v: str) -> str:
        v = v.strip().lower()
        v = re.sub(r'^https?://', '', v)
        v = v.rstrip('/')
        v = re.sub(r'^www\.', '', v)
        
        pattern = r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z]{2,})+$'
        if not re.match(pattern, v):
            raise ValueError('Invalid domain format')
        return v


class DomainResponse(BaseModel):
    id: str
    domain: str
    current_score: float
    last_audited: Optional[str]


class AuditHistoryItem(BaseModel):
    audit_id: str
    domain: str
    score: float
    grade: str
    created_at: str


class DashboardStats(BaseModel):
    total_domains: int
    average_score: float
    total_audits: int
    subscription_status: str


# ===========================================
# Endpoints
# ===========================================

@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(user: dict = Depends(require_auth)):
    """Get dashboard statistics for current user."""
    
    
    with get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})
            OPTIONAL MATCH (u)-[:OWNS]->(d:Domain)
            OPTIONAL MATCH (d)-[:HAS_AUDIT]->(a:Audit)
            RETURN 
                count(DISTINCT d) as total_domains,
                avg(d.current_score) as avg_score,
                count(a) as total_audits
        """, user_id=user["id"])
        
        record = result.single()
        
        return DashboardStats(
            total_domains=record["total_domains"] or 0,
            average_score=round(record["avg_score"] or 0, 1),
            total_audits=record["total_audits"] or 0,
            subscription_status=user.get("subscription_status", "free")
        )


@router.get("/domains", response_model=List[DomainResponse])
async def list_domains(user: dict = Depends(require_auth)):
    """List all domains tracked by current user."""
    
    
    with get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})-[:OWNS]->(d:Domain)
            RETURN d
            ORDER BY d.current_score DESC
        """, user_id=user["id"])
        
        domains = []
        for record in result:
            d = record["d"]
            last_audited = d.get("last_audited")
            if last_audited:
                last_audited = last_audited.to_native().isoformat()
            
            domains.append(DomainResponse(
                id=d.get("id", d["name"]),
                domain=d["name"],
                current_score=d.get("current_score", 0),
                last_audited=last_audited
            ))
        
        return domains


@router.post("/domains", response_model=DomainResponse)
async def add_domain(data: DomainAdd, user: dict = Depends(require_auth)):
    """Add a domain to track."""
    
    # Check subscription limits
    tier = user.get("subscription_tier", "free")
    
    
    
    with get_session() as session:
        # Count existing domains
        count_result = session.run("""
            MATCH (u:User {id: $user_id})-[:OWNS]->(d:Domain)
            RETURN count(d) as count
        """, user_id=user["id"])
        
        current_count = count_result.single()["count"]
        
        # Limits
        limits = {"free": 1, "pro": 5, "enterprise": 100}
        max_domains = limits.get(tier, 1)
        
        if current_count >= max_domains:
            raise HTTPException(
                status_code=403, 
                detail=f"Domain limit reached ({max_domains}). Upgrade to add more."
            )
        
        # Create domain and link to user
        result = session.run("""
            MATCH (u:User {id: $user_id})
            MERGE (d:Domain {name: $domain})
            ON CREATE SET
                d.id = randomUUID(),
                d.first_seen = datetime(),
                d.current_score = 0,
                d.tier = 'tracked'
            MERGE (u)-[:OWNS]->(d)
            RETURN d
        """, user_id=user["id"], domain=data.domain)
        
        record = result.single()
        d = record["d"]
        
        return DomainResponse(
            id=d.get("id", d["name"]),
            domain=d["name"],
            current_score=d.get("current_score", 0),
            last_audited=None
        )


@router.delete("/domains/{domain}")
async def remove_domain(domain: str, user: dict = Depends(require_auth)):
    """Remove a domain from tracking."""
    
    
    with get_session() as session:
        # Remove ownership relationship (don't delete domain, others might use it)
        result = session.run("""
            MATCH (u:User {id: $user_id})-[r:OWNS]->(d:Domain {name: $domain})
            DELETE r
            RETURN d
        """, user_id=user["id"], domain=domain.lower())
        
        if not result.single():
            raise HTTPException(status_code=404, detail="Domain not found")
        
        return {"message": f"Domain {domain} removed"}


@router.get("/audits", response_model=List[AuditHistoryItem])
async def list_audits(
    limit: int = 20,
    user: dict = Depends(require_auth)
):
    """List recent audits for user's domains."""
    
    
    with get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})-[:OWNS]->(d:Domain)-[:HAS_AUDIT]->(a:Audit)
            WHERE a.status = 'complete'
            RETURN a, d.name as domain
            ORDER BY a.created_at DESC
            LIMIT $limit
        """, user_id=user["id"], limit=limit)
        
        audits = []
        for record in result:
            a = record["a"]
            created_at = a.get("created_at")
            if created_at:
                created_at = created_at.to_native().isoformat()
            
            # Get grade from raw_data if not directly available
            grade = a.get("grade", "")
            if not grade:
                import json
                raw = a.get("raw_data", "{}")
                try:
                    data = json.loads(raw)
                    grade = data.get("grade", "?")
                except (ValueError, KeyError):
                    grade = "?"
            
            audits.append(AuditHistoryItem(
                audit_id=a["audit_id"],
                domain=record["domain"],
                score=a.get("overall_score", 0),
                grade=grade,
                created_at=created_at or ""
            ))
        
        return audits


@router.post("/domains/{domain}/audit")
async def trigger_audit(domain: str, user: dict = Depends(require_auth)):
    """Manually trigger an audit for a domain."""
    
    # Verify user owns this domain
    
    
    with get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})-[:OWNS]->(d:Domain {name: $domain})
            RETURN d
        """, user_id=user["id"], domain=domain.lower())
        
        if not result.single():
            raise HTTPException(status_code=404, detail="Domain not found or not owned by you")
    
    # Trigger audit via background worker (if available)
    import uuid
    audit_id = str(uuid.uuid4())

    try:
        from app.workers.agent_runner import enqueue_audit
        await enqueue_audit(audit_id, domain.lower())
        return {"audit_id": audit_id, "status": "queued"}
    except ImportError:
        # Worker module not available — run inline or return pending
        logger.warning("worker_unavailable", msg="enqueue_audit not found, audit queued only")
        return {"audit_id": audit_id, "status": "pending", "message": "Audit worker not configured. Contact support."}
