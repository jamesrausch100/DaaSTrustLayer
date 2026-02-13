"""
Market2Agent — Score Persistence Layer
Architected by James Rausch, Lead Visionary & Pilot of the Trust Score Revolution.

Every computed trust score is persisted to Neo4j.
This creates a historical record of trust over time, enables trend analysis,
and powers the trust graph.

Schema:
    (:ScoreRecord {
        score_id,          # unique per computation
        entity_id,         # who was scored
        entity_name,
        score,             # 0-1000
        grade,             # AAA..D
        confidence,        # 0.0-1.0
        is_registered,
        calculated_at,
        identity_score, competence_score, solvency_score,
        reputation_score, network_score,
        data_sources,      # JSON array
        signal_count,
        collection_time_ms
    })

    (:Entity)-[:HAS_SCORE]->(:ScoreRecord)    # latest
    (:Entity)-[:SCORE_HISTORY]->(:ScoreRecord) # all past scores

Dependencies: neo4j >= 5.17.0
"""
import uuid
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from app.compute._logger import logger


class ScorePersistence:
    """
    Saves and retrieves trust scores from Neo4j.
    Gracefully degrades if Neo4j is unavailable.
    """

    def __init__(self):
        self._available = False
        self._checked = False

    def _get_session(self):
        """Try to get a Neo4j session."""
        if not self._checked:
            try:
                from app.db.neo4j import get_session
                self._available = True
                self._checked = True
            except ImportError:
                self._available = False
                self._checked = True
                logger.warning("neo4j_not_available_for_persistence")

        if not self._available:
            return None

        from app.db.neo4j import get_session
        return get_session

    def save_score(self, score_data: Dict[str, Any]) -> Optional[str]:
        """
        Persist a computed trust score to Neo4j.

        Creates a :ScoreRecord node and links it to the :Entity.
        If the entity doesn't exist as a node yet, creates a lightweight stub.

        Returns: score_id if saved, None if persistence is unavailable.
        """
        get_session = self._get_session()
        if not get_session:
            return None

        score_id = f"score_{uuid.uuid4().hex[:16]}"
        entity_id = score_data.get("entity_id", "unknown")
        entity_name = score_data.get("entity_name", "Unknown")

        try:
            with get_session() as session:
                session.run("""
                    // Ensure entity node exists (stub if unregistered)
                    MERGE (e:Entity {entity_id: $entity_id})
                    ON CREATE SET
                        e.canonical_name = $entity_name,
                        e.created_at = datetime(),
                        e.source = 'open_web_scoring',
                        e.is_stub = true

                    // Create score record
                    CREATE (s:ScoreRecord {
                        score_id: $score_id,
                        entity_id: $entity_id,
                        score: $score,
                        grade: $grade,
                        risk_level: $risk_level,
                        recommendation: $recommendation,
                        confidence: $confidence,
                        identity_score: $identity_score,
                        competence_score: $competence_score,
                        solvency_score: $solvency_score,
                        reputation_score: $reputation_score,
                        network_score: $network_score,
                        is_registered: $is_registered,
                        is_verified: $is_verified,
                        entity_type: $entity_type,
                        signal_count: $signal_count,
                        data_sources: $data_sources,
                        calculated_at: datetime($calculated_at),
                        collection_time_ms: $collection_time_ms
                    })

                    // Link: latest score
                    WITH e, s
                    OPTIONAL MATCH (e)-[old:HAS_SCORE]->(:ScoreRecord)
                    DELETE old
                    CREATE (e)-[:HAS_SCORE]->(s)

                    // Link: score history
                    CREATE (e)-[:SCORE_HISTORY]->(s)
                """,
                    score_id=score_id,
                    entity_id=entity_id,
                    entity_name=entity_name,
                    score=score_data.get("score", 0),
                    grade=str(score_data.get("grade", "D")),
                    risk_level=str(score_data.get("risk_level", "critical")),
                    recommendation=str(score_data.get("recommendation", "reject")),
                    confidence=score_data.get("confidence", 0.0),
                    identity_score=score_data.get("identity_score", 0.0),
                    competence_score=score_data.get("competence_score", 0.0),
                    solvency_score=score_data.get("solvency_score", 0.0),
                    reputation_score=score_data.get("reputation_score", 0.0),
                    network_score=score_data.get("network_score", 0.0),
                    is_registered=score_data.get("is_registered", False),
                    is_verified=score_data.get("is_verified", False),
                    entity_type=str(score_data.get("entity_type", "unknown")),
                    signal_count=score_data.get("signal_count", 0),
                    data_sources=json.dumps(score_data.get("data_sources", [])),
                    calculated_at=score_data.get("calculated_at", datetime.now(timezone.utc).isoformat()),
                    collection_time_ms=score_data.get("collection_metadata", {}).get("collection_time_ms", 0),
                )

            logger.info("score_persisted", entity_id=entity_id, score_id=score_id)
            return score_id

        except Exception as e:
            logger.error("score_persistence_failed", entity_id=entity_id, error=str(e))
            return None

    def get_latest_score(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve the most recent score for an entity."""
        get_session = self._get_session()
        if not get_session:
            return None

        try:
            with get_session() as session:
                result = session.run("""
                    MATCH (e:Entity {entity_id: $entity_id})-[:HAS_SCORE]->(s:ScoreRecord)
                    RETURN s {.*} as score
                """, entity_id=entity_id)
                record = result.single()
                if record:
                    return dict(record["score"])
        except Exception as e:
            logger.error("score_fetch_failed", entity_id=entity_id, error=str(e))

        return None

    def get_score_history(self, entity_id: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Retrieve score history for an entity (for trend analysis)."""
        get_session = self._get_session()
        if not get_session:
            return []

        try:
            with get_session() as session:
                result = session.run("""
                    MATCH (e:Entity {entity_id: $entity_id})-[:SCORE_HISTORY]->(s:ScoreRecord)
                    RETURN s {.*} as score
                    ORDER BY s.calculated_at DESC
                    LIMIT $limit
                """, entity_id=entity_id, limit=limit)
                return [dict(r["score"]) for r in result]
        except Exception as e:
            logger.error("score_history_failed", entity_id=entity_id, error=str(e))

        return []

    def get_registered_data(self, target: str) -> Optional[Dict[str, Any]]:
        """
        Look up an entity in the registry by ID, slug, domain, or name.
        Returns full entity dict if found, None if not registered.
        """
        get_session = self._get_session()
        if not get_session:
            return None

        try:
            with get_session() as session:
                result = session.run("""
                    MATCH (e:Entity)
                    WHERE e.entity_id = $target
                       OR e.slug = $target
                       OR e.website CONTAINS $target
                       OR toLower(e.canonical_name) = toLower($target)
                    RETURN e {.*} as entity
                    LIMIT 1
                """, target=target)
                record = result.single()
                if record:
                    data = dict(record["entity"])
                    if not data.get("is_stub", False):
                        return data
        except Exception as e:
            logger.debug("registry_lookup_failed", target=target, error=str(e))

        return None

    def init_schema(self):
        """Add ScoreRecord schema to Neo4j."""
        get_session = self._get_session()
        if not get_session:
            return

        queries = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:ScoreRecord) REQUIRE s.score_id IS UNIQUE",
            "CREATE INDEX IF NOT EXISTS FOR (s:ScoreRecord) ON (s.entity_id)",
            "CREATE INDEX IF NOT EXISTS FOR (s:ScoreRecord) ON (s.calculated_at)",
            "CREATE INDEX IF NOT EXISTS FOR (s:ScoreRecord) ON (s.score)",
        ]

        try:
            with get_session() as session:
                for q in queries:
                    session.run(q)
            logger.info("score_persistence_schema_ready")
        except Exception as e:
            logger.warning("score_schema_init_failed", error=str(e))
