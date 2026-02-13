"""
Market2Agent — TrustChain
Blockchain Ledger for Trust Observations

Every trust signal ever collected is a block in the chain.
Timestamped. Hashed. Chained. Immutable. Auditable.

This is NOT cryptocurrency. This is cryptographic proof of data integrity.

When an AI agent asks "is stripe.com safe?" we don't just give a score.
We give a score backed by a verifiable chain of observations that proves:
    - WHAT was observed (DNS records, SSL certs, VT results)
    - WHEN it was observed (UTC timestamp, never backdated)
    - WHERE it came from (which sensor/source)
    - THAT it hasn't been tampered with (hash chain)

The chain per entity:

    Block 0 (genesis)     Block 1              Block 2
    ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
    │ entity_id    │     │ entity_id    │     │ entity_id    │
    │ sensor: dns  │     │ sensor: vt   │     │ sensor: dns  │
    │ signals: {}  │     │ signals: {}  │     │ signals: {}  │
    │ timestamp    │     │ timestamp    │     │ timestamp    │
    │ prev: 0x00   │──→  │ prev: 0xA3.. │──→  │ prev: 0xF1.. │
    │ hash: 0xA3.. │     │ hash: 0xF1.. │     │ hash: 0x7B.. │
    └──────────────┘     └──────────────┘     └──────────────┘

To verify: recompute each hash and confirm it matches the next block's
prev_hash. If any observation was altered, the chain breaks.

Storage:
    Hot (Redis):     Latest block hash per entity (for fast chaining)
    Warm (Postgres): Recent blocks (queryable, indexed)
    Cold (S3/Azure): All blocks (Parquet, partitioned by date)

This is the data lake. This is the product. This is the moat.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

import structlog

logger = structlog.get_logger()

GENESIS_HASH = "0" * 64  # The "Big Bang" — first block in any entity's chain


class Sensor(str, Enum):
    """Each data source is a sensor. Sensors produce observations."""
    TRANCO          = "tranco"
    CRTSH           = "crtsh"
    VIRUSTOTAL      = "virustotal"
    DNS             = "dns"
    HTTP_HEADERS    = "http_headers"
    WHOIS           = "whois"
    KNOWLEDGE_GRAPH = "knowledge_graph"
    WEB_PRESENCE    = "web_presence"
    SOCIAL          = "social"
    SAFE_BROWSING   = "google_safe_browsing"
    SCORE           = "score_computed"       # The score itself is also a block


@dataclass
class Block:
    """
    A single observation. The atom of the TrustChain.
    Once created, it is sealed — the hash proves integrity.
    """
    # Identity
    entity_id: str                              # "stripe.com"
    sensor: str                                 # Sensor enum value
    block_index: int = 0                        # Position in this entity's chain

    # Timestamp — UTC, nanosecond precision
    observed_at: str = ""                       # ISO 8601
    observed_at_unix: float = 0.0               # Unix epoch (for sorting)

    # Payload — the raw sensor data
    signals: Dict[str, Any] = field(default_factory=dict)

    # Chain integrity
    prev_hash: str = GENESIS_HASH               # Hash of previous block
    block_hash: str = ""                        # SHA-256 of this block's content

    # Delta — what changed since last observation from this sensor
    delta: Dict[str, Any] = field(default_factory=dict)

    # Metadata
    collection_time_ms: float = 0.0
    sensor_version: str = "3.0.0"
    node_id: str = ""                           # Which M2A node produced this

    def __post_init__(self):
        if not self.observed_at:
            now = datetime.now(timezone.utc)
            self.observed_at = now.isoformat()
            self.observed_at_unix = now.timestamp()
        if not self.node_id:
            self.node_id = os.environ.get("M2A_NODE_ID", "node-0")
        if not self.block_hash:
            self.block_hash = self.compute_hash()

    def compute_hash(self) -> str:
        """
        Deterministic SHA-256 of block content.
        Changing ANY field invalidates the hash → breaks the chain.
        """
        content = json.dumps({
            "entity_id": self.entity_id,
            "sensor": self.sensor,
            "block_index": self.block_index,
            "observed_at": self.observed_at,
            "signals": self.signals,
            "prev_hash": self.prev_hash,
        }, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()

    def verify(self) -> bool:
        """Verify this block's integrity."""
        return self.block_hash == self.compute_hash()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_compact(self) -> Dict[str, Any]:
        """Minimal representation for API responses."""
        return {
            "sensor": self.sensor,
            "observed_at": self.observed_at,
            "block_hash": self.block_hash[:16] + "...",
            "signals_count": len(self.signals),
            "has_delta": bool(self.delta),
        }


def compute_delta(old_signals: Dict[str, Any], new_signals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute what changed between two observations from the same sensor.
    The delta is MORE VALUABLE than the state.
    "Stripe's SPF record disappeared" is an alert.
    "Stripe has an SPF record" is a fact.
    """
    delta = {}

    all_keys = set(list(old_signals.keys()) + list(new_signals.keys()))
    for key in all_keys:
        old_val = old_signals.get(key)
        new_val = new_signals.get(key)

        if old_val != new_val:
            delta[key] = {
                "was": old_val,
                "now": new_val,
            }

            # Flag critical changes
            if key in ("dns_has_spf", "dns_has_dmarc", "ssl_valid") and old_val is True and new_val is False:
                delta[key]["severity"] = "critical"
                delta[key]["alert"] = f"{key} was present, now missing"

            elif key == "vt_malicious_count" and isinstance(new_val, int) and isinstance(old_val, int):
                if new_val > old_val:
                    delta[key]["severity"] = "high"
                    delta[key]["alert"] = f"VirusTotal flags increased from {old_val} to {new_val}"

            elif key == "tranco_rank":
                if old_val and old_val > 0 and (not new_val or new_val == 0):
                    delta[key]["severity"] = "warning"
                    delta[key]["alert"] = "Dropped out of Tranco Top 1M"

    return delta


# ── Chain Storage Interface ───────────────────────

class TrustChainStore:
    """
    Storage layer for the TrustChain.

    Hot path (Redis): Latest hash + latest signals per entity+sensor
    Cold path (S3/Azure): Full block history (append-only)

    This class handles the hot path. Cold path is handled by the
    lake writer (see lake.py).
    """

    def __init__(self):
        import redis
        self._redis = redis.Redis(
            host=os.environ.get("REDIS_HOST", "redis"),
            port=int(os.environ.get("REDIS_PORT", 6379)),
            db=2,  # Dedicated DB for chain data
            decode_responses=True,
        )

    def get_chain_head(self, entity_id: str) -> Tuple[str, int]:
        """Get the latest block hash and index for an entity."""
        key = f"chain:{entity_id}:head"
        data = self._redis.hgetall(key)
        if data:
            return data.get("hash", GENESIS_HASH), int(data.get("index", 0))
        return GENESIS_HASH, 0

    def get_last_signals(self, entity_id: str, sensor: str) -> Optional[Dict[str, Any]]:
        """Get the last observed signals for an entity+sensor pair."""
        key = f"chain:{entity_id}:latest:{sensor}"
        data = self._redis.get(key)
        if data:
            return json.loads(data)
        return None

    def append_block(self, block: Block) -> Block:
        """
        Append a block to the chain. Updates the head hash.
        Returns the block with chain fields populated.
        """
        pipe = self._redis.pipeline()

        # Update chain head
        head_key = f"chain:{block.entity_id}:head"
        pipe.hset(head_key, mapping={
            "hash": block.block_hash,
            "index": block.block_index,
            "updated_at": block.observed_at,
        })

        # Store latest signals for this sensor (for delta computation)
        latest_key = f"chain:{block.entity_id}:latest:{block.sensor}"
        pipe.set(latest_key, json.dumps(block.signals, default=str))

        # Store the block itself (hot cache — recent blocks only)
        block_key = f"block:{block.block_hash}"
        pipe.set(block_key, json.dumps(block.to_dict(), default=str), ex=86400 * 7)  # 7 day TTL

        # Add to entity's block list (for history queries)
        list_key = f"chain:{block.entity_id}:blocks"
        pipe.lpush(list_key, block.block_hash)
        pipe.ltrim(list_key, 0, 999)  # Keep last 1000 blocks in Redis

        # Track entity in global set (for refresh scheduling)
        pipe.sadd("chain:entities", block.entity_id)

        pipe.execute()

        logger.debug("block_appended",
            entity=block.entity_id,
            sensor=block.sensor,
            index=block.block_index,
            hash=block.block_hash[:16],
        )
        return block

    def create_block(self, entity_id: str, sensor: str, signals: Dict[str, Any],
                     collection_time_ms: float = 0.0) -> Block:
        """
        Create a new block, compute delta from previous observation,
        chain it to the head, and store it.
        """
        # Get chain state
        prev_hash, prev_index = self.get_chain_head(entity_id)
        new_index = prev_index + 1 if prev_hash != GENESIS_HASH else 0

        # Compute delta
        old_signals = self.get_last_signals(entity_id, sensor) or {}
        delta = compute_delta(old_signals, signals) if old_signals else {}

        # Create block
        block = Block(
            entity_id=entity_id,
            sensor=sensor,
            block_index=new_index,
            signals=signals,
            prev_hash=prev_hash,
            delta=delta,
            collection_time_ms=collection_time_ms,
        )

        # Append to chain
        return self.append_block(block)

    def get_entity_history(self, entity_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent blocks for an entity."""
        list_key = f"chain:{entity_id}:blocks"
        hashes = self._redis.lrange(list_key, 0, limit - 1)

        blocks = []
        for h in hashes:
            block_data = self._redis.get(f"block:{h}")
            if block_data:
                blocks.append(json.loads(block_data))
        return blocks

    def verify_chain(self, entity_id: str, limit: int = 100) -> Dict[str, Any]:
        """
        Verify the integrity of an entity's chain.
        Returns verification result with any breaks found.
        """
        blocks = self.get_entity_history(entity_id, limit)
        if not blocks:
            return {"verified": True, "blocks_checked": 0, "message": "No blocks found"}

        # Sort by index
        blocks.sort(key=lambda b: b.get("block_index", 0))

        breaks = []
        for i, block_data in enumerate(blocks):
            # Recompute hash
            b = Block(**{k: v for k, v in block_data.items()
                        if k in Block.__dataclass_fields__})
            recomputed = b.compute_hash()
            if recomputed != block_data.get("block_hash"):
                breaks.append({
                    "block_index": block_data.get("block_index"),
                    "expected_hash": recomputed,
                    "stored_hash": block_data.get("block_hash"),
                    "type": "hash_mismatch",
                })

            # Verify chain linkage
            if i > 0:
                expected_prev = blocks[i - 1].get("block_hash")
                actual_prev = block_data.get("prev_hash")
                if expected_prev != actual_prev:
                    breaks.append({
                        "block_index": block_data.get("block_index"),
                        "expected_prev": expected_prev,
                        "actual_prev": actual_prev,
                        "type": "chain_break",
                    })

        return {
            "verified": len(breaks) == 0,
            "blocks_checked": len(blocks),
            "breaks": breaks,
            "chain_head": blocks[-1].get("block_hash", "") if blocks else "",
        }

    def get_entity_count(self) -> int:
        """How many entities are in the chain."""
        return self._redis.scard("chain:entities") or 0

    def get_block_count(self, entity_id: str) -> int:
        """How many blocks an entity has."""
        return self._redis.llen(f"chain:{entity_id}:blocks") or 0


# ── Lake Writer (Cold Storage) ────────────────────

class LakeWriter:
    """
    Writes blocks to cold storage (S3/Azure Data Lake).

    Today: Writes Parquet files to local disk (portable)
    Tomorrow: Writes to Azure Data Lake Gen2 / S3

    Partition scheme: /lake/{entity_id}/{YYYY}/{MM}/{DD}/{sensor}.parquet
    """

    def __init__(self, base_path: str = "/data/lake"):
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)

    def write_block(self, block: Block):
        """Append a block to the entity's partition."""
        # Parse date from observation
        dt = datetime.fromisoformat(block.observed_at.replace("Z", "+00:00"))
        partition = os.path.join(
            self.base_path,
            block.entity_id.replace(".", "_"),
            str(dt.year),
            f"{dt.month:02d}",
            f"{dt.day:02d}",
        )
        os.makedirs(partition, exist_ok=True)

        # Append as JSONL (JSON Lines — one block per line)
        # Parquet conversion happens in batch job
        filepath = os.path.join(partition, f"{block.sensor}.jsonl")
        with open(filepath, "a") as f:
            f.write(json.dumps(block.to_dict(), default=str) + "\n")

    def write_blocks(self, blocks: List[Block]):
        """Batch write multiple blocks."""
        for block in blocks:
            self.write_block(block)

    def get_entity_path(self, entity_id: str) -> str:
        return os.path.join(self.base_path, entity_id.replace(".", "_"))


# ── Convenience: Record an observation ────────────

_chain_store: Optional[TrustChainStore] = None
_lake_writer: Optional[LakeWriter] = None


def get_chain() -> TrustChainStore:
    global _chain_store
    if _chain_store is None:
        _chain_store = TrustChainStore()
    return _chain_store


def get_lake() -> LakeWriter:
    global _lake_writer
    if _lake_writer is None:
        _lake_writer = LakeWriter()
    return _lake_writer


def record_observation(
    entity_id: str,
    sensor: str,
    signals: Dict[str, Any],
    collection_time_ms: float = 0.0,
) -> Block:
    """
    Record a single observation. Chains it, stores it, lakes it.
    This is the function every collector calls after gathering data.
    """
    chain = get_chain()
    lake = get_lake()

    # Create and chain the block
    block = chain.create_block(entity_id, sensor, signals, collection_time_ms)

    # Write to cold storage
    try:
        lake.write_block(block)
    except Exception as e:
        logger.warning("lake_write_failed", entity=entity_id, error=str(e))

    # Log alerts from deltas
    if block.delta:
        critical_deltas = {k: v for k, v in block.delta.items()
                         if isinstance(v, dict) and v.get("severity") in ("critical", "high")}
        if critical_deltas:
            logger.warning("trust_alert",
                entity=entity_id,
                sensor=sensor,
                alerts=critical_deltas,
            )

    return block
