"""
Market2Agent API Key Management
Architected by James Rausch, Lead Visionary & Pilot of the Trust Score Revolution.

API keys are how agents (and their developers) authenticate.
Every trust check costs money. Every key is metered.

Key format: m2a_live_<32 hex chars> (production)
            m2a_test_<32 hex chars> (sandbox)

Storage: Neo4j for key metadata, Redis for rate limiting + usage counters.
"""
import secrets
import hashlib
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from enum import Enum

import structlog

logger = structlog.get_logger()


class KeyStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    RATE_LIMITED = "rate_limited"
    EXPIRED = "expired"


class KeyEnvironment(str, Enum):
    LIVE = "live"
    TEST = "test"


@dataclass
class APIKey:
    key_id: str
    user_id: str
    prefix: str             # First 8 chars (for display: m2a_live_a1b2c3d4...)
    key_hash: str           # SHA-256 of full key
    name: str               # User-given label
    environment: str        # "live" or "test"
    status: str
    created_at: str
    last_used_at: Optional[str] = None
    expires_at: Optional[str] = None

    # Limits
    rate_limit_per_minute: int = 60
    rate_limit_per_day: int = 10000
    monthly_quota: int = 10000

    # Usage (populated at query time from Redis)
    usage_today: int = 0
    usage_this_month: int = 0


def generate_api_key(environment: str = "live") -> tuple:
    """
    Generate a new API key.
    Returns (full_key, key_hash, prefix).
    The full key is shown ONCE to the user. We only store the hash.
    """
    raw = secrets.token_hex(32)
    full_key = f"m2a_{environment}_{raw}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    prefix = f"m2a_{environment}_{raw[:8]}..."
    return full_key, key_hash, prefix


def hash_key(full_key: str) -> str:
    """Hash a full API key for lookup."""
    return hashlib.sha256(full_key.encode()).hexdigest()


# =============================================
# NEO4J QUERIES
# =============================================

def _get_session():
    from app.db.neo4j import get_session
    return get_session()


def create_api_key(
    user_id: str,
    name: str,
    environment: str = "live",
    monthly_quota: int = 10000,
    rate_limit_per_minute: int = 60,
) -> tuple:
    """
    Create a new API key for a user.
    Returns (full_key, key_metadata).
    Full key is returned ONCE; we store only the hash.
    """
    import uuid
    key_id = str(uuid.uuid4())
    full_key, key_hash, prefix = generate_api_key(environment)

    with _get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})
            CREATE (k:APIKey {
                key_id: $key_id,
                user_id: $user_id,
                prefix: $prefix,
                key_hash: $key_hash,
                name: $name,
                environment: $environment,
                status: 'active',
                rate_limit_per_minute: $rate_limit_per_minute,
                rate_limit_per_day: $rate_limit_per_day,
                monthly_quota: $monthly_quota,
                created_at: datetime(),
                usage_total: 0
            })
            CREATE (u)-[:HAS_KEY]->(k)
            RETURN k {.*} as key_data
        """,
            key_id=key_id,
            user_id=user_id,
            prefix=prefix,
            key_hash=key_hash,
            name=name,
            environment=environment,
            rate_limit_per_minute=rate_limit_per_minute,
            rate_limit_per_day=10000,
            monthly_quota=monthly_quota,
        )
        record = result.single()
        if not record:
            raise RuntimeError("Failed to create API key")

        logger.info("api_key_created",
                     key_id=key_id,
                     user_id=user_id,
                     environment=environment)

        return full_key, record["key_data"]


def get_key_by_hash(key_hash: str) -> Optional[Dict[str, Any]]:
    """Look up an API key by its hash. Used on every API call."""
    with _get_session() as session:
        result = session.run("""
            MATCH (k:APIKey {key_hash: $key_hash})
            WHERE k.status = 'active'
            RETURN k {.*} as key_data
        """, key_hash=key_hash)
        record = result.single()
        return dict(record["key_data"]) if record else None


def get_keys_for_user(user_id: str) -> List[Dict[str, Any]]:
    """List all API keys for a user."""
    with _get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})-[:HAS_KEY]->(k:APIKey)
            RETURN k {.*} as key_data
            ORDER BY k.created_at DESC
        """, user_id=user_id)
        return [dict(r["key_data"]) for r in result]


def revoke_api_key(key_id: str, user_id: str) -> bool:
    """Revoke an API key. Only the owner can revoke."""
    with _get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})-[:HAS_KEY]->(k:APIKey {key_id: $key_id})
            SET k.status = 'revoked', k.revoked_at = datetime()
            RETURN k.key_id as revoked
        """, key_id=key_id, user_id=user_id)
        record = result.single()

        if record:
            logger.info("api_key_revoked", key_id=key_id, user_id=user_id)
        return record is not None


def record_key_usage(key_hash: str):
    """Record that a key was used (update last_used_at and total count)."""
    with _get_session() as session:
        session.run("""
            MATCH (k:APIKey {key_hash: $key_hash})
            SET k.last_used_at = datetime(),
                k.usage_total = coalesce(k.usage_total, 0) + 1
        """, key_hash=key_hash)


# Admin
def get_all_keys_admin() -> List[Dict[str, Any]]:
    """Admin: list all API keys across all users."""
    with _get_session() as session:
        result = session.run("""
            MATCH (u:User)-[:HAS_KEY]->(k:APIKey)
            RETURN k {.*, owner_email: u.email, owner_name: u.name} as key_data
            ORDER BY k.created_at DESC
            LIMIT 100
        """)
        return [dict(r["key_data"]) for r in result]
