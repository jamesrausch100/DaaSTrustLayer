// ==============================================================
// Market2Agent - Graph Schema
// Run this in Neo4j Browser or via cypher-shell after install
// ==============================================================

// --------------------------------------------------------------
// CONSTRAINTS (enforce uniqueness, auto-create indexes)
// --------------------------------------------------------------

// Each domain is unique
CREATE CONSTRAINT domain_unique IF NOT EXISTS
FOR (d:Domain) REQUIRE d.name IS UNIQUE;

// Wikidata entities have unique QIDs
CREATE CONSTRAINT wikidata_qid_unique IF NOT EXISTS
FOR (w:WikidataEntity) REQUIRE w.qid IS UNIQUE;

// Audit reports have unique IDs
CREATE CONSTRAINT audit_unique IF NOT EXISTS
FOR (a:Audit) REQUIRE a.audit_id IS UNIQUE;

// --------------------------------------------------------------
// INDEXES (speed up common queries)
// --------------------------------------------------------------

// Fast lookup of audits by timestamp
CREATE INDEX audit_timestamp IF NOT EXISTS
FOR (a:Audit) ON (a.created_at);

// Fast lookup of domains by score
CREATE INDEX domain_score IF NOT EXISTS
FOR (d:Domain) ON (d.current_score);

// Fast lookup of entities by type
CREATE INDEX entity_type IF NOT EXISTS
FOR (e:Entity) ON (e.schema_type);

// --------------------------------------------------------------
// NODE TYPES (for reference - Neo4j is schema-optional)
// --------------------------------------------------------------

// :Domain
// - name: "example.com" (unique)
// - first_seen: datetime
// - last_audited: datetime
// - current_score: float (0-100)
// - tier: "free" | "agent" | "managed"

// :Audit
// - audit_id: uuid (unique)
// - created_at: datetime
// - overall_score: float
// - structured_data_score: float
// - entity_presence_score: float
// - content_clarity_score: float
// - raw_data: JSON string (full audit payload)

// :Entity (extracted from structured data)
// - schema_type: "Organization" | "Person" | "Product" | "Article"
// - name: string
// - description: string
// - url: string
// - extracted_from: "json-ld" | "microdata" | "rdfa"

// :WikidataEntity
// - qid: "Q123456" (unique)
// - label: string
// - description: string
// - last_checked: datetime

// :WebPage
// - url: string
// - title: string
// - last_crawled: datetime

// --------------------------------------------------------------
// RELATIONSHIP TYPES
// --------------------------------------------------------------

// (:Domain)-[:HAS_AUDIT]->(:Audit)
// (:Domain)-[:HAS_ENTITY]->(:Entity)
// (:Domain)-[:HAS_PAGE]->(:WebPage)
// (:Entity)-[:SAME_AS]->(:WikidataEntity)
// (:Entity)-[:MENTIONS]->(:Entity)
// (:WebPage)-[:LINKS_TO]->(:WebPage)
// (:WebPage)-[:CONTAINS_ENTITY]->(:Entity)

// --------------------------------------------------------------
// SAMPLE DATA (for testing - remove in production)
// --------------------------------------------------------------

// Create a test domain
MERGE (d:Domain {name: "test-example.com"})
SET d.first_seen = datetime(),
    d.current_score = 0,
    d.tier = "free";

// Verify setup
MATCH (n) RETURN labels(n)[0] AS type, count(*) AS count;
