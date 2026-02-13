"""
Market2Agent Platform - Entity Model

The Entity is the core primitive. Everything else builds on it.

An Entity represents a business, organization, or brand that:
- Has a verified owner (user who claimed it)
- Has structured data (name, description, category, locations, etc.)
- Has visibility metrics (how AI systems perceive it)
- Can have an AI agent representing it

Schema:
    (:User)-[:OWNS]->(:Entity)
    (:User)-[:TRACKS]->(:Entity)  // Competitor tracking
    (:Entity)-[:IN_CATEGORY]->(:Category)
    (:Entity)-[:COMPETES_WITH]->(:Entity)
    (:Entity)-[:HAS_VISIBILITY_RECORD]->(:VisibilityRecord)
"""
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum

import structlog

logger = structlog.get_logger()


class EntityStatus(str, Enum):
    CLAIMED = "claimed"          # User started claiming
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"        # Domain/email verified
    ENRICHED = "enriched"        # Data enriched from external sources
    SUSPENDED = "suspended"      # Flagged for review


class VerificationMethod(str, Enum):
    DOMAIN_DNS = "domain_dns"        # TXT record on domain
    DOMAIN_FILE = "domain_file"      # File at /.well-known/m2a-verify.txt
    EMAIL = "email"                  # Email to domain admin
    MANUAL = "manual"                # Admin manual verification


CATEGORY_TAXONOMY = {
    "technology": {
        "name": "Technology",
        "subcategories": [
            "software", "saas", "hardware", "cloud-services", 
            "cybersecurity", "ai-ml", "devtools", "fintech"
        ]
    },
    "professional-services": {
        "name": "Professional Services",
        "subcategories": [
            "consulting", "legal", "accounting", "marketing-agency",
            "design-agency", "recruiting", "real-estate"
        ]
    },
    "retail": {
        "name": "Retail & E-commerce",
        "subcategories": [
            "fashion", "electronics", "home-garden", "food-beverage",
            "health-beauty", "sports-outdoors"
        ]
    },
    "local-services": {
        "name": "Local Services",
        "subcategories": [
            "hvac", "plumbing", "electrical", "landscaping",
            "cleaning", "auto-repair", "home-improvement"
        ]
    },
    "healthcare": {
        "name": "Healthcare",
        "subcategories": [
            "medical-practice", "dental", "mental-health", 
            "physical-therapy", "pharmacy", "medical-devices"
        ]
    },
    "hospitality": {
        "name": "Hospitality & Travel",
        "subcategories": [
            "restaurant", "hotel", "travel-agency", "events",
            "entertainment", "fitness"
        ]
    },
    "finance": {
        "name": "Finance",
        "subcategories": [
            "banking", "insurance", "investment", "lending",
            "payments", "crypto"
        ]
    },
    "education": {
        "name": "Education",
        "subcategories": [
            "k12", "higher-ed", "online-learning", "tutoring",
            "professional-training", "language-learning"
        ]
    },
}


@dataclass
class Entity:
    entity_id: str
    slug: str
    canonical_name: str
    
    # Status & verification
    status: str = EntityStatus.CLAIMED
    verified: bool = False
    verification_method: Optional[str] = None
    verified_at: Optional[str] = None
    owner_user_id: Optional[str] = None
    
    # Core info
    legal_name: Optional[str] = None
    description: Optional[str] = None
    short_description: Optional[str] = None  # 160 chars for meta
    
    # Categorization
    category: Optional[str] = None
    subcategories: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    
    # Location
    headquarters_city: Optional[str] = None
    headquarters_region: Optional[str] = None
    headquarters_country: Optional[str] = None
    service_areas: List[str] = field(default_factory=list)  # For local businesses
    
    # Company info
    founded_year: Optional[int] = None
    employee_count_range: Optional[str] = None  # "1-10", "11-50", etc.
    revenue_range: Optional[str] = None
    company_type: Optional[str] = None  # "private", "public", "nonprofit"
    
    # Web presence
    website: Optional[str] = None
    domains: List[str] = field(default_factory=list)
    logo_url: Optional[str] = None
    
    # Social links
    twitter_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    facebook_url: Optional[str] = None
    instagram_url: Optional[str] = None
    youtube_url: Optional[str] = None
    github_url: Optional[str] = None
    
    # Knowledge graph links
    wikidata_qid: Optional[str] = None
    wikipedia_url: Optional[str] = None
    crunchbase_url: Optional[str] = None
    
    # AI Visibility (denormalized for speed)
    visibility_score: Optional[float] = None
    visibility_trend: Optional[str] = None  # "up", "down", "stable"
    visibility_updated_at: Optional[str] = None
    
    # Metadata
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    claimed_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values for Neo4j."""
        return {k: v for k, v in self.__dict__.items() if v is not None}
    
    @staticmethod
    def from_record(record: dict) -> "Entity":
        """Create Entity from Neo4j record."""
        return Entity(
            entity_id=record.get("entity_id", ""),
            slug=record.get("slug", ""),
            canonical_name=record.get("canonical_name", ""),
            status=record.get("status", EntityStatus.CLAIMED),
            verified=record.get("verified", False),
            verification_method=record.get("verification_method"),
            verified_at=_to_iso(record.get("verified_at")),
            owner_user_id=record.get("owner_user_id"),
            legal_name=record.get("legal_name"),
            description=record.get("description"),
            short_description=record.get("short_description"),
            category=record.get("category"),
            subcategories=record.get("subcategories", []),
            tags=record.get("tags", []),
            headquarters_city=record.get("headquarters_city"),
            headquarters_region=record.get("headquarters_region"),
            headquarters_country=record.get("headquarters_country"),
            service_areas=record.get("service_areas", []),
            founded_year=record.get("founded_year"),
            employee_count_range=record.get("employee_count_range"),
            revenue_range=record.get("revenue_range"),
            company_type=record.get("company_type"),
            website=record.get("website"),
            domains=record.get("domains", []),
            logo_url=record.get("logo_url"),
            twitter_url=record.get("twitter_url"),
            linkedin_url=record.get("linkedin_url"),
            facebook_url=record.get("facebook_url"),
            instagram_url=record.get("instagram_url"),
            youtube_url=record.get("youtube_url"),
            github_url=record.get("github_url"),
            wikidata_qid=record.get("wikidata_qid"),
            wikipedia_url=record.get("wikipedia_url"),
            crunchbase_url=record.get("crunchbase_url"),
            visibility_score=record.get("visibility_score"),
            visibility_trend=record.get("visibility_trend"),
            visibility_updated_at=_to_iso(record.get("visibility_updated_at")),
            created_at=_to_iso(record.get("created_at")),
            updated_at=_to_iso(record.get("updated_at")),
            claimed_at=_to_iso(record.get("claimed_at")),
        )
    
    def to_json_ld(self) -> dict:
        """Export as Schema.org JSON-LD for public profile page."""
        ld = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "@id": f"https://market2agent.ai/entity/{self.slug}",
            "name": self.canonical_name,
            "url": self.website,
        }
        
        if self.description:
            ld["description"] = self.description
        
        if self.logo_url:
            ld["logo"] = self.logo_url
        
        if self.founded_year:
            ld["foundingDate"] = str(self.founded_year)
        
        if self.headquarters_city:
            ld["address"] = {
                "@type": "PostalAddress",
                "addressLocality": self.headquarters_city,
                "addressRegion": self.headquarters_region,
                "addressCountry": self.headquarters_country,
            }
        
        same_as = []
        for url in [self.twitter_url, self.linkedin_url, self.facebook_url, 
                    self.instagram_url, self.youtube_url, self.github_url,
                    self.wikipedia_url, self.crunchbase_url]:
            if url:
                same_as.append(url)
        if same_as:
            ld["sameAs"] = same_as
        
        return ld
    
    @property
    def is_claimable(self) -> bool:
        """Can this entity still be claimed by a new user?"""
        return self.status == EntityStatus.CLAIMED and not self.verified
    
    @property
    def completeness_score(self) -> int:
        """Calculate profile completeness (0-100)."""
        fields = [
            self.description,
            self.category,
            self.website,
            self.founded_year,
            self.headquarters_city,
            self.employee_count_range,
            self.logo_url,
            self.twitter_url or self.linkedin_url,  # At least one social
            self.short_description,
        ]
        filled = sum(1 for f in fields if f)
        return int((filled / len(fields)) * 100)


def _to_iso(val) -> Optional[str]:
    """Convert Neo4j DateTime to ISO string."""
    if val is None:
        return None
    if hasattr(val, "to_native"):
        return val.to_native().isoformat()
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def generate_slug(name: str, existing_slugs: List[str] = None) -> str:
    """Generate a URL-safe slug from entity name."""
    # Lowercase, replace spaces with hyphens
    slug = name.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)  # Remove special chars
    slug = re.sub(r'[\s_]+', '-', slug)   # Replace spaces with hyphens
    slug = re.sub(r'-+', '-', slug)       # Collapse multiple hyphens
    slug = slug.strip('-')
    
    # Ensure uniqueness
    if existing_slugs and slug in existing_slugs:
        i = 2
        while f"{slug}-{i}" in existing_slugs:
            i += 1
        slug = f"{slug}-{i}"
    
    return slug


# =============================================
# NEO4J QUERIES
# =============================================

def _get_session():
    from app.db.neo4j import get_session
    return get_session()


def create_entity(
    name: str,
    owner_user_id: str,
    website: Optional[str] = None,
    category: Optional[str] = None,
) -> Entity:
    """
    Create a new entity and assign ownership.
    Called when a user claims a business.
    """
    entity_id = str(uuid.uuid4())
    
    # Generate unique slug
    with _get_session() as session:
        result = session.run("MATCH (e:Entity) RETURN e.slug as slug")
        existing = [r["slug"] for r in result]
    
    slug = generate_slug(name, existing)
    
    with _get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $owner_user_id})
            CREATE (e:Entity {
                entity_id: $entity_id,
                slug: $slug,
                canonical_name: $name,
                status: 'claimed',
                verified: false,
                owner_user_id: $owner_user_id,
                website: $website,
                category: $category,
                created_at: datetime(),
                claimed_at: datetime()
            })
            CREATE (u)-[:OWNS]->(e)
            RETURN e {.*} as entity
        """, 
            entity_id=entity_id,
            slug=slug,
            name=name,
            owner_user_id=owner_user_id,
            website=website,
            category=category,
        )
        
        record = result.single()
        if not record:
            raise RuntimeError(f"Failed to create entity for user {owner_user_id}")
        
        logger.info("entity_created", entity_id=entity_id, slug=slug, owner=owner_user_id)
        return Entity.from_record(dict(record["entity"]))


def get_entity_by_id(entity_id: str) -> Optional[Entity]:
    """Get entity by ID."""
    with _get_session() as session:
        result = session.run("""
            MATCH (e:Entity {entity_id: $entity_id})
            RETURN e {.*} as entity
        """, entity_id=entity_id)
        
        record = result.single()
        return Entity.from_record(dict(record["entity"])) if record else None


def get_entity_by_slug(slug: str) -> Optional[Entity]:
    """Get entity by slug (for public profile pages)."""
    with _get_session() as session:
        result = session.run("""
            MATCH (e:Entity {slug: $slug})
            RETURN e {.*} as entity
        """, slug=slug.lower())
        
        record = result.single()
        return Entity.from_record(dict(record["entity"])) if record else None


def get_entity_by_domain(domain: str) -> Optional[Entity]:
    """Get entity by domain (for claiming)."""
    domain = domain.lower().replace("www.", "")
    
    with _get_session() as session:
        result = session.run("""
            MATCH (e:Entity)
            WHERE e.website CONTAINS $domain OR $domain IN e.domains
            RETURN e {.*} as entity
            LIMIT 1
        """, domain=domain)
        
        record = result.single()
        return Entity.from_record(dict(record["entity"])) if record else None


def get_entities_for_user(user_id: str) -> List[Entity]:
    """Get all entities owned by a user."""
    with _get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})-[:OWNS]->(e:Entity)
            RETURN e {.*} as entity
            ORDER BY e.created_at DESC
        """, user_id=user_id)
        
        return [Entity.from_record(dict(r["entity"])) for r in result]


def get_tracked_entities(user_id: str) -> List[Entity]:
    """Get competitor entities a user is tracking."""
    with _get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})-[:TRACKS]->(e:Entity)
            RETURN e {.*} as entity
            ORDER BY e.visibility_score DESC
        """, user_id=user_id)
        
        return [Entity.from_record(dict(r["entity"])) for r in result]


def track_competitor(user_id: str, entity_id: str) -> bool:
    """Start tracking a competitor entity."""
    with _get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})
            MATCH (e:Entity {entity_id: $entity_id})
            MERGE (u)-[:TRACKS]->(e)
            RETURN e.entity_id as tracked
        """, user_id=user_id, entity_id=entity_id)
        
        return result.single() is not None


def update_entity(entity_id: str, updates: dict) -> Optional[Entity]:
    """Update entity fields."""
    # Sanitize updates - only allow certain fields
    allowed = {
        "canonical_name", "legal_name", "description", "short_description",
        "category", "subcategories", "tags",
        "headquarters_city", "headquarters_region", "headquarters_country",
        "service_areas", "founded_year", "employee_count_range", 
        "revenue_range", "company_type", "website", "domains", "logo_url",
        "twitter_url", "linkedin_url", "facebook_url", "instagram_url",
        "youtube_url", "github_url", "wikidata_qid", "wikipedia_url",
        "crunchbase_url",
    }
    
    updates = {k: v for k, v in updates.items() if k in allowed}
    
    if not updates:
        return get_entity_by_id(entity_id)
    
    # Build SET clause
    set_clauses = ", ".join([f"e.{k} = ${k}" for k in updates.keys()])
    set_clauses += ", e.updated_at = datetime()"
    
    with _get_session() as session:
        result = session.run(f"""
            MATCH (e:Entity {{entity_id: $entity_id}})
            SET {set_clauses}
            RETURN e {{.*}} as entity
        """, entity_id=entity_id, **updates)
        
        record = result.single()
        return Entity.from_record(dict(record["entity"])) if record else None


def verify_entity(entity_id: str, method: str) -> bool:
    """Mark entity as verified."""
    with _get_session() as session:
        session.run("""
            MATCH (e:Entity {entity_id: $entity_id})
            SET e.verified = true,
                e.verification_method = $method,
                e.verified_at = datetime(),
                e.status = 'verified',
                e.updated_at = datetime()
        """, entity_id=entity_id, method=method)
        
        logger.info("entity_verified", entity_id=entity_id, method=method)
        return True


def search_entities(
    query: str,
    category: Optional[str] = None,
    verified_only: bool = False,
    limit: int = 20,
) -> List[Entity]:
    """Search entities by name or description."""
    with _get_session() as session:
        # Build WHERE clause
        where = "WHERE e.canonical_name CONTAINS $query OR e.description CONTAINS $query"
        if category:
            where += " AND e.category = $category"
        if verified_only:
            where += " AND e.verified = true"
        
        result = session.run(f"""
            MATCH (e:Entity)
            {where}
            RETURN e {{.*}} as entity
            ORDER BY e.visibility_score DESC, e.verified DESC
            LIMIT $limit
        """, query=query.lower(), category=category, limit=limit)
        
        return [Entity.from_record(dict(r["entity"])) for r in result]


def get_entities_in_category(
    category: str,
    limit: int = 50,
    min_visibility: float = 0,
) -> List[Entity]:
    """Get top entities in a category by visibility score."""
    with _get_session() as session:
        result = session.run("""
            MATCH (e:Entity {category: $category})
            WHERE e.verified = true 
              AND coalesce(e.visibility_score, 0) >= $min_visibility
            RETURN e {.*} as entity
            ORDER BY e.visibility_score DESC
            LIMIT $limit
        """, category=category, min_visibility=min_visibility, limit=limit)
        
        return [Entity.from_record(dict(r["entity"])) for r in result]


def update_visibility_score(entity_id: str, score: float, trend: str):
    """Update entity's visibility score (called by visibility indexer)."""
    with _get_session() as session:
        session.run("""
            MATCH (e:Entity {entity_id: $entity_id})
            SET e.visibility_score = $score,
                e.visibility_trend = $trend,
                e.visibility_updated_at = datetime()
        """, entity_id=entity_id, score=score, trend=trend)
