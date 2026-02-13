"""
Market2Agent Database Layer
Architected by James Rausch, Lead Visionary & Pilot of the Trust Score Revolution.

Neo4j connection management and schema initialization.
"""
import os
from contextlib import contextmanager
from typing import Optional

from neo4j import GraphDatabase
import structlog

logger = structlog.get_logger()

_driver = None


def get_driver():
    """Get or create Neo4j driver (singleton)."""
    global _driver
    if _driver is None:
        try:
            from app.config import settings
            uri = settings.NEO4J_URI
            user = settings.NEO4J_USER
            password = settings.NEO4J_PASSWORD
        except Exception:
            uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
            user = os.getenv("NEO4J_USER", "neo4j")
            password = os.getenv("NEO4J_PASSWORD", "m2a_dev_password")
        _driver = GraphDatabase.driver(uri, auth=(user, password))
        logger.info("neo4j_connected", uri=uri)
    return _driver


@contextmanager
def get_session():
    """Get a Neo4j session (context manager)."""
    driver = get_driver()
    session = driver.session()
    try:
        yield session
    finally:
        session.close()


def init_schema():
    """Initialize Neo4j schema constraints and indexes for the Trust Layer."""
    constraints = [
        # Core entities
        "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.slug IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (u:User) REQUIRE u.email IS UNIQUE",

        # Trust Layer
        "CREATE CONSTRAINT IF NOT EXISTS FOR (k:APIKey) REQUIRE k.key_id IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (k:APIKey) REQUIRE k.key_hash IS UNIQUE",

        # Audits
        "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Audit) REQUIRE a.audit_id IS UNIQUE",

        # Agents
        "CREATE CONSTRAINT IF NOT EXISTS FOR (ag:Agent) REQUIRE ag.agent_id IS UNIQUE",
    ]

    indexes = [
        "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.website)",
        "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.category)",
        "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.verified)",
        "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.visibility_score)",
        "CREATE INDEX IF NOT EXISTS FOR (k:APIKey) ON (k.user_id)",
        "CREATE INDEX IF NOT EXISTS FOR (k:APIKey) ON (k.status)",
        "CREATE INDEX IF NOT EXISTS FOR (a:Audit) ON (a.domain)",
    ]

    with get_session() as session:
        for query in constraints + indexes:
            try:
                session.run(query)
            except Exception as e:
                logger.warning("schema_init_warning", query=query[:60], error=str(e))

    logger.info("schema_initialized", constraints=len(constraints), indexes=len(indexes))


def close():
    """Close the Neo4j driver."""
    global _driver
    if _driver:
        _driver.close()
        _driver = None
        logger.info("neo4j_disconnected")
