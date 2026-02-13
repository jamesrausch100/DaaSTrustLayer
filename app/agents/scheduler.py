"""
Market2Agent - Agent Scheduler

The scheduler runs as a periodic job inside the existing arq worker.
Every N minutes, it:
    1. Loads all runnable agents
    2. Checks each agent's billing enforcement (plan limits, execution interval)
    3. Queues audit jobs for eligible agents
    4. Records heartbeats and run results

This is NOT a long-running daemon. It's a cron-like job
executed by arq's cron_jobs mechanism.

Execution interval: every 15 minutes.
Agents with weekly plans only execute when enough time has elapsed.
"""
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import redis
import structlog

from app.config import settings
from app.agents.model import (
    Agent, AgentStatus, PLAN_LIMITS,
    get_runnable_agents, get_agent_domains,
    record_heartbeat, record_run_result,
    update_agent_status,
)

logger = structlog.get_logger()

# Redis client for distributed locks and ephemeral state
_redis: Optional[redis.Redis] = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


# =============================================
# BILLING ENFORCEMENT
# =============================================

def check_execution_allowed(agent: Agent) -> tuple[bool, str]:
    """
    Hard billing enforcement. Returns (allowed, reason).
    
    Checks:
    1. Agent status is 'running'
    2. Enough time has elapsed since last run (per plan)
    3. Agent is not currently executing (lock check)
    """
    # Status check
    if not agent.is_runnable:
        return False, f"agent_status_{agent.status}"

    # Interval check
    limits = agent.limits
    interval_hours = limits["execution_interval_hours"]

    if agent.last_run_at:
        try:
            last_run = datetime.fromisoformat(agent.last_run_at)
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
            
            next_allowed = last_run + timedelta(hours=interval_hours)
            now = datetime.now(timezone.utc)

            if now < next_allowed:
                hours_remaining = (next_allowed - now).total_seconds() / 3600
                return False, f"interval_not_elapsed ({hours_remaining:.1f}h remaining)"
        except (ValueError, TypeError) as e:
            # Can't parse last_run_at — allow execution (first run or data issue)
            logger.warning("unparseable_last_run", agent_id=agent.agent_id, value=agent.last_run_at)

    # Lock check (prevent double execution)
    r = _get_redis()
    lock_key = f"agent_lock:{agent.agent_id}"
    if r.get(lock_key):
        return False, "already_executing"

    return True, "ok"


def enforce_domain_limit(agent: Agent, domains: list[str]) -> list[str]:
    """
    Enforce max_domains limit from plan.
    Returns truncated domain list.
    """
    max_domains = agent.limits.get("max_domains", 10)
    if len(domains) > max_domains:
        logger.warning("domain_limit_enforced",
                       agent_id=agent.agent_id,
                       requested=len(domains),
                       limit=max_domains)
        return domains[:max_domains]
    return domains


# =============================================
# EXECUTION LOCK
# =============================================

def acquire_lock(agent_id: str, ttl_seconds: int = 300) -> bool:
    """Acquire execution lock. Returns True if acquired."""
    r = _get_redis()
    lock_key = f"agent_lock:{agent_id}"
    return bool(r.set(lock_key, "1", nx=True, ex=ttl_seconds))


def release_lock(agent_id: str):
    """Release execution lock."""
    r = _get_redis()
    r.delete(f"agent_lock:{agent_id}")


# =============================================
# MAIN SCHEDULER
# =============================================

async def tick(ctx: dict = None):
    """
    Main scheduler tick. Called periodically by arq cron.
    
    Iterates all runnable agents, enforces limits,
    and queues audit jobs for eligible ones.
    """
    logger.info("scheduler_tick_start")

    agents = get_runnable_agents()
    logger.info("scheduler_agents_found", count=len(agents))

    queued = 0
    skipped = 0

    for agent in agents:
        # Record heartbeat regardless of execution
        record_heartbeat(agent.agent_id)

        # Enforce billing limits
        allowed, reason = check_execution_allowed(agent)

        if not allowed:
            if reason != "already_executing" and "interval" not in reason:
                logger.info("agent_skipped",
                            agent_id=agent.agent_id,
                            reason=reason)
            skipped += 1
            continue

        # Get domains to audit
        domains = get_agent_domains(agent.agent_id)
        if not domains:
            record_run_result(agent.agent_id, "skipped")
            skipped += 1
            continue

        # Enforce domain limit
        domains = enforce_domain_limit(agent, domains)

        # Acquire execution lock
        if not acquire_lock(agent.agent_id, ttl_seconds=600):
            skipped += 1
            continue

        # Queue audit jobs for each domain
        try:
            from app.workers.agent_runner import queue_agent_run
            await queue_agent_run(agent.agent_id, domains)
            queued += 1
            logger.info("agent_execution_queued",
                        agent_id=agent.agent_id,
                        domains=len(domains))
        except Exception as e:
            release_lock(agent.agent_id)
            record_run_result(agent.agent_id, "failed", str(e))
            logger.error("agent_queue_failed",
                         agent_id=agent.agent_id,
                         error=str(e))

    logger.info("scheduler_tick_complete",
                total=len(agents),
                queued=queued,
                skipped=skipped)


# =============================================
# ERROR ESCALATION
# =============================================

MAX_CONSECUTIVE_ERRORS = 5


def check_error_escalation(agent_id: str, error_count: int):
    """
    If an agent has failed too many times consecutively,
    transition it to 'errored' status.
    
    This prevents infinite retry loops.
    An admin must manually restart.
    """
    if error_count >= MAX_CONSECUTIVE_ERRORS:
        update_agent_status(agent_id, AgentStatus.ERRORED)
        logger.warning("agent_error_escalated",
                       agent_id=agent_id,
                       error_count=error_count)
