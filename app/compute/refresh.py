"""
Market2Agent — Data Refresh Engine
Layer A maintenance: keeps the data fresh.

Jobs:
    1. Tranco ingest — daily download of Top 1M domains into Redis
    2. Score refresh — re-score entities on a priority schedule

Run manually:
    python -m app.compute.refresh tranco       # ingest Tranco
    python -m app.compute.refresh scores       # refresh due scores
"""
import asyncio
import csv
import io
import os
import sys
import time
import zipfile
from datetime import datetime, timezone

import httpx
import redis
import structlog

logger = structlog.get_logger()

REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"


# ── Tranco Ingest ─────────────────────────────────

async def ingest_tranco():
    """
    Download Tranco Top 1M CSV and load into Redis sorted set.
    ~15MB download, ~1M entries, takes ~30 seconds.

    Redis key: tranco (sorted set, score = rank)
    Lookup: ZSCORE tranco stripe.com → 47
    """
    logger.info("tranco_ingest_starting")
    start = time.time()

    # Download
    async with httpx.AsyncClient() as client:
        resp = await client.get(TRANCO_URL, timeout=60.0)
        resp.raise_for_status()

    # Extract CSV from zip
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    csv_name = z.namelist()[0]
    csv_data = z.read(csv_name).decode("utf-8")

    # Parse and load into Redis
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=1, decode_responses=True)

    # Use pipeline for bulk insert (much faster than individual commands)
    pipe = r.pipeline(transaction=False)

    # Clear old data
    pipe.delete("tranco")

    count = 0
    reader = csv.reader(io.StringIO(csv_data))
    batch_size = 10000
    batch = {}

    for row in reader:
        if len(row) >= 2:
            rank = int(row[0])
            domain = row[1].strip().lower()
            batch[domain] = rank
            count += 1

            if len(batch) >= batch_size:
                pipe.zadd("tranco", batch)
                batch = {}

    # Flush remaining
    if batch:
        pipe.zadd("tranco", batch)

    # Store metadata
    pipe.set("tranco:last_updated", datetime.now(timezone.utc).isoformat())
    pipe.set("tranco:count", str(count))

    pipe.execute()

    elapsed = round(time.time() - start, 1)
    logger.info("tranco_ingest_complete", count=count, elapsed_seconds=elapsed)
    print(f"Tranco ingest complete: {count:,} domains loaded in {elapsed}s")

    # Quick verification
    test_rank = r.zscore("tranco", "google.com")
    print(f"Verification: google.com rank = {test_rank}")

    return count


# ── Score Refresh ─────────────────────────────────

async def refresh_due_scores(batch_size: int = 50):
    """
    Re-score entities that are due for a refresh.

    Priority tiers:
        Hot:    queried >10x in 24h    → refresh every 6h
        Active: queried in past 7d      → refresh every 24h
        Warm:   queried in past 30d     → refresh every 7d
        Cold:   not queried in 30d      → skip (on-demand only)
    """
    from app.compute.collectors_v3 import collect_all_signals
    from app.trust.engine_v3 import compute_score
    from app.compute.cache import ScoreCache

    cache = ScoreCache()

    # For now, refresh everything in cache that's older than 24h
    # TODO: implement priority tiers from Neo4j query log
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

    # Get all cached score keys
    keys = r.keys("score:*")
    refreshed = 0

    for key in keys[:batch_size]:
        target = key.replace("score:", "")
        try:
            signals = await collect_all_signals(target)
            result = compute_score(signals)
            cache.set(target, result.to_full())
            refreshed += 1
            logger.info("score_refreshed", target=target, score=result.score)
        except Exception as e:
            logger.warning("refresh_failed", target=target, error=str(e))

        # Rate limit: don't hammer external APIs
        await asyncio.sleep(2)

    print(f"Refreshed {refreshed}/{len(keys)} scores")
    return refreshed


# ── CLI Entry Point ───────────────────────────────

async def main():
    if len(sys.argv) < 2:
        print("Usage: python -m app.compute.refresh [tranco|scores]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "tranco":
        await ingest_tranco()
    elif cmd == "scores":
        await refresh_due_scores()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
