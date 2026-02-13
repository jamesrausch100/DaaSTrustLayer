"""
Market2Agent - Agent Runner

Background worker job that executes audits for a specific agent.
Called by the scheduler when an agent is eligible to run.

This runs inside the existing arq worker process.
Each invocation processes one agent's full audit cycle:
    1. Run audits for each monitored domain
    2. Record results
    3. Release execution lock
    4. Check error escalation
"""
import json
import uuid
import asyncio
from datetime import datetime, timezone

import structlog
from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings
from app.agents.model import (
    get_agent_by_id, get_agent_domains,
    record_heartbeat, record_run_result,
    PLAN_LIMITS,
)
from app.agents.scheduler import release_lock, check_error_escalation
from app.db.neo4j import create_audit, complete_audit, fail_audit

logger = structlog.get_logger()

REDIS_SETTINGS = RedisSettings.from_dsn(settings.REDIS_URL)


async def queue_agent_run(agent_id: str, domains: list[str]):
    """Queue an agent execution job."""
    redis_pool = await create_pool(REDIS_SETTINGS)
    await redis_pool.enqueue_job("execute_agent", agent_id, domains)


async def execute_agent(ctx, agent_id: str, domains: list[str]):
    """
    Execute a full audit cycle for one agent.
    
    This is the arq job function. It:
    1. Validates the agent is still runnable
    2. Runs audits for each domain (respecting concurrency limits)
    3. Records success/failure
    4. Releases the execution lock
    """
    logger.info("agent_run_start", agent_id=agent_id, domains=len(domains))

    agent = get_agent_by_id(agent_id)
    if not agent:
        logger.error("agent_not_found", agent_id=agent_id)
        release_lock(agent_id)
        return {"status": "error", "reason": "agent_not_found"}

    if not agent.is_runnable:
        logger.info("agent_not_runnable", agent_id=agent_id, status=agent.status)
        release_lock(agent_id)
        return {"status": "skipped", "reason": f"status_{agent.status}"}

    # Get plan limits
    limits = agent.limits
    max_pages = limits.get("max_pages_per_domain", 5)
    max_concurrent = limits.get("max_concurrent_audits", 3)

    results = []
    errors = []

    try:
        # Run audits with concurrency limit
        semaphore = asyncio.Semaphore(max_concurrent)

        async def run_single_audit(domain: str) -> dict:
            async with semaphore:
                return await _execute_domain_audit(
                    agent_id=agent_id,
                    user_id=agent.user_id,
                    domain=domain,
                    max_pages=max_pages,
                )

        # Execute all domain audits
        audit_results = await asyncio.gather(
            *[run_single_audit(d) for d in domains],
            return_exceptions=True,
        )

        for domain, result in zip(domains, audit_results):
            if isinstance(result, Exception):
                errors.append({"domain": domain, "error": str(result)})
            else:
                results.append(result)

        # Record heartbeat
        record_heartbeat(agent_id)

        # Determine overall result
        if errors and not results:
            # All failed
            error_summary = "; ".join(f"{e['domain']}: {e['error']}" for e in errors[:3])
            record_run_result(agent_id, "failed", error_summary)
            check_error_escalation(agent_id, agent.error_count + 1)
            logger.error("agent_run_all_failed",
                         agent_id=agent_id,
                         errors=len(errors))
        elif errors:
            # Partial success — record as success but log errors
            record_run_result(agent_id, "success")
            logger.warning("agent_run_partial",
                           agent_id=agent_id,
                           success=len(results),
                           errors=len(errors))
        else:
            # Full success
            record_run_result(agent_id, "success")
            logger.info("agent_run_complete",
                        agent_id=agent_id,
                        audits=len(results))

    except Exception as e:
        record_run_result(agent_id, "failed", str(e))
        check_error_escalation(agent_id, agent.error_count + 1)
        logger.error("agent_run_exception", agent_id=agent_id, error=str(e))

    finally:
        release_lock(agent_id)

    return {
        "agent_id": agent_id,
        "domains": len(domains),
        "success": len(results),
        "errors": len(errors),
    }


async def _execute_domain_audit(
    agent_id: str,
    user_id: str,
    domain: str,
    max_pages: int,
) -> dict:
    """
    Execute a single domain audit.
    Uses existing crawl and scoring infrastructure.
    """
    audit_id = str(uuid.uuid4())

    logger.info("domain_audit_start",
                agent_id=agent_id,
                domain=domain,
                audit_id=audit_id)

    # Create audit record
    create_audit(audit_id, domain, user_id)

    try:
        # Use existing crawlers
        from app.crawlers.structured_data import crawl_domain
        from app.crawlers.entity_presence import check_entity_presence
        from app.analyzers.scoring import calculate_geo_score

        # Crawl
        crawl_data = await crawl_domain(domain)

        # Extract org name
        org_name = None
        for entity in crawl_data.get("all_entities", []):
            if entity.get("type") in ("Organization", "Corporation"):
                org_name = entity.get("name")
                break

        # Entity presence check
        entity_data = await check_entity_presence(domain, org_name)

        # Score
        scores = calculate_geo_score(crawl_data, entity_data, domain)

        overall = sum(scores.get("scores", {}).values())
        grade = _grade(overall)

        scores["grade"] = grade
        scores["domain"] = domain
        scores["audit_id"] = audit_id
        scores["agent_id"] = agent_id

        # Save
        complete_audit(
            audit_id=audit_id,
            score=overall,
            grade=grade,
            raw_data=json.dumps(scores),
        )

        logger.info("domain_audit_complete",
                     agent_id=agent_id,
                     domain=domain,
                     score=overall,
                     grade=grade)

        return {"audit_id": audit_id, "domain": domain, "score": overall, "grade": grade}

    except Exception as e:
        fail_audit(audit_id, str(e))
        logger.error("domain_audit_failed",
                     agent_id=agent_id,
                     domain=domain,
                     error=str(e))
        raise


def _grade(score: int) -> str:
    if score >= 90: return "A"
    if score >= 80: return "B"
    if score >= 70: return "C"
    if score >= 60: return "D"
    return "F"
