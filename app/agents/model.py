"""
Market2Agent - Agent Domain Model

Persisted in Neo4j. An Agent represents a per-customer automation
that runs periodic audits on their tracked domains.

Schema:
    (:User)-[:OWNS_AGENT]->(:Agent)-[:MONITORS]->(:Domain)

Status lifecycle:
    provisioning -> running -> paused -> running (recovery)
                                      -> stopped (cancellation)
    running -> errored -> running (auto-retry or manual restart)
    * -> stopped (terminal)
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from enum import Enum


class AgentStatus(str, Enum):
    PROVISIONING = "provisioning"
    RUNNING = "running"
    PAUSED = "paused"
    ERRORED = "errored"
    STOPPED = "stopped"


class AgentPlan(str, Enum):
    PRO = "pro"  # $20/mo — maps to your existing tier


# Hard limits per plan. Enforced before every execution.
# Add plans here as pricing evolves.
PLAN_LIMITS = {
    AgentPlan.PRO: {
        "max_domains": 10,
        "execution_interval_hours": 168,  # Weekly (7 * 24)
        "max_pages_per_domain": 5,
        "max_concurrent_audits": 3,
    },
}


@dataclass
class Agent:
    agent_id: str
    user_id: str
    subscription_id: str
    plan: str
    status: str
    config: dict = field(default_factory=dict)
    created_at: Optional[str] = None
    last_heartbeat: Optional[str] = None
    last_run_at: Optional[str] = None
    last_run_status: Optional[str] = None  # "success" | "failed" | "skipped"
    last_error: Optional[str] = None
    error_count: int = 0
    paused_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @staticmethod
    def from_record(record: dict) -> "Agent":
        return Agent(
            agent_id=record.get("agent_id", ""),
            user_id=record.get("user_id", ""),
            subscription_id=record.get("subscription_id", ""),
            plan=record.get("plan", "pro"),
            status=record.get("status", "provisioning"),
            config=record.get("config", {}),
            created_at=_to_iso(record.get("created_at")),
            last_heartbeat=_to_iso(record.get("last_heartbeat")),
            last_run_at=_to_iso(record.get("last_run_at")),
            last_run_status=record.get("last_run_status"),
            last_error=record.get("last_error"),
            error_count=record.get("error_count", 0),
            paused_reason=record.get("paused_reason"),
        )

    @property
    def limits(self) -> dict:
        """Get hard limits for this agent's plan."""
        return PLAN_LIMITS.get(self.plan, PLAN_LIMITS[AgentPlan.PRO])

    @property
    def is_runnable(self) -> bool:
        return self.status == AgentStatus.RUNNING


def _to_iso(val) -> Optional[str]:
    """Convert Neo4j DateTime or any datetime to ISO string."""
    if val is None:
        return None
    if hasattr(val, "to_native"):
        return val.to_native().isoformat()
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


# =============================================
# NEO4J QUERIES
# =============================================

def _get_session():
    """Import here to avoid circular deps."""
    from app.db.neo4j import get_session
    return get_session()


def create_agent(
    agent_id: str,
    user_id: str,
    subscription_id: str,
    plan: str = "pro",
) -> Agent:
    """
    Create a new agent linked to a user.
    Idempotent: if agent for this subscription already exists, returns it.
    """
    with _get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})
            MERGE (a:Agent {subscription_id: $subscription_id})
            ON CREATE SET
                a.agent_id = $agent_id,
                a.user_id = $user_id,
                a.plan = $plan,
                a.status = 'provisioning',
                a.config = '{}',
                a.created_at = datetime(),
                a.error_count = 0
            ON MATCH SET
                a.plan = $plan
            MERGE (u)-[:OWNS_AGENT]->(a)
            WITH a, u
            OPTIONAL MATCH (u)-[:TRACKS]->(d:Domain)
            FOREACH (d IN collect(d) |
                MERGE (a)-[:MONITORS]->(d)
            )
            RETURN a {
                .agent_id, .user_id, .subscription_id, .plan,
                .status, .config, .created_at, .last_heartbeat,
                .last_run_at, .last_run_status, .last_error,
                .error_count, .paused_reason
            } as agent
        """, agent_id=agent_id, user_id=user_id,
             subscription_id=subscription_id, plan=plan)

        record = result.single()
        if not record:
            raise RuntimeError(f"Failed to create agent for user {user_id}")
        return Agent.from_record(dict(record["agent"]))


def get_agent_by_subscription(subscription_id: str) -> Optional[Agent]:
    """Get agent by Stripe subscription ID. Returns None if not found."""
    with _get_session() as session:
        result = session.run("""
            MATCH (a:Agent {subscription_id: $subscription_id})
            RETURN a {
                .agent_id, .user_id, .subscription_id, .plan,
                .status, .config, .created_at, .last_heartbeat,
                .last_run_at, .last_run_status, .last_error,
                .error_count, .paused_reason
            } as agent
        """, subscription_id=subscription_id)

        record = result.single()
        return Agent.from_record(dict(record["agent"])) if record else None


def get_agent_by_id(agent_id: str) -> Optional[Agent]:
    """Get agent by agent_id."""
    with _get_session() as session:
        result = session.run("""
            MATCH (a:Agent {agent_id: $agent_id})
            RETURN a {
                .agent_id, .user_id, .subscription_id, .plan,
                .status, .config, .created_at, .last_heartbeat,
                .last_run_at, .last_run_status, .last_error,
                .error_count, .paused_reason
            } as agent
        """, agent_id=agent_id)

        record = result.single()
        return Agent.from_record(dict(record["agent"])) if record else None


def get_agents_for_user(user_id: str) -> list[Agent]:
    """Get all agents owned by a user."""
    with _get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})-[:OWNS_AGENT]->(a:Agent)
            RETURN a {
                .agent_id, .user_id, .subscription_id, .plan,
                .status, .config, .created_at, .last_heartbeat,
                .last_run_at, .last_run_status, .last_error,
                .error_count, .paused_reason
            } as agent
        """, user_id=user_id)

        return [Agent.from_record(dict(r["agent"])) for r in result]


def get_all_agents(limit: int = 100) -> list[Agent]:
    """Get all agents (admin). Includes user email."""
    with _get_session() as session:
        result = session.run("""
            MATCH (a:Agent)
            OPTIONAL MATCH (u:User)-[:OWNS_AGENT]->(a)
            RETURN a {
                .agent_id, .user_id, .subscription_id, .plan,
                .status, .config, .created_at, .last_heartbeat,
                .last_run_at, .last_run_status, .last_error,
                .error_count, .paused_reason
            } as agent,
            u.email as user_email
            ORDER BY a.created_at DESC
            LIMIT $limit
        """, limit=limit)

        agents = []
        for r in result:
            agent = Agent.from_record(dict(r["agent"]))
            agent.config["_user_email"] = r["user_email"]
            agents.append(agent)
        return agents


def get_runnable_agents() -> list[Agent]:
    """Get all agents with status 'running'. Used by scheduler."""
    with _get_session() as session:
        result = session.run("""
            MATCH (a:Agent {status: 'running'})
            RETURN a {
                .agent_id, .user_id, .subscription_id, .plan,
                .status, .config, .created_at, .last_heartbeat,
                .last_run_at, .last_run_status, .last_error,
                .error_count, .paused_reason
            } as agent
        """)

        return [Agent.from_record(dict(r["agent"])) for r in result]


def get_agent_domains(agent_id: str) -> list[str]:
    """Get domain names monitored by this agent."""
    with _get_session() as session:
        result = session.run("""
            MATCH (a:Agent {agent_id: $agent_id})-[:MONITORS]->(d:Domain)
            RETURN d.name as domain
        """, agent_id=agent_id)

        return [r["domain"] for r in result]


def update_agent_status(agent_id: str, status: str, reason: str = None):
    """
    Update agent status. Idempotent.
    If status is 'paused', stores reason in paused_reason.
    If status is 'running', clears paused_reason.
    """
    with _get_session() as session:
        session.run("""
            MATCH (a:Agent {agent_id: $agent_id})
            SET a.status = $status,
                a.paused_reason = CASE WHEN $status = 'paused' THEN $reason ELSE null END
        """, agent_id=agent_id, status=status, reason=reason)


def update_agent_status_by_subscription(subscription_id: str, status: str, reason: str = None):
    """Update agent status by subscription ID. Idempotent."""
    with _get_session() as session:
        session.run("""
            MATCH (a:Agent {subscription_id: $subscription_id})
            SET a.status = $status,
                a.paused_reason = CASE WHEN $status = 'paused' THEN $reason ELSE null END
        """, subscription_id=subscription_id, status=status, reason=reason)


def record_heartbeat(agent_id: str):
    """Update last_heartbeat to now."""
    with _get_session() as session:
        session.run("""
            MATCH (a:Agent {agent_id: $agent_id})
            SET a.last_heartbeat = datetime()
        """, agent_id=agent_id)


def record_run_result(agent_id: str, status: str, error: str = None):
    """
    Record the result of an agent execution cycle.
    status: 'success' | 'failed' | 'skipped'
    """
    with _get_session() as session:
        if status == "failed":
            session.run("""
                MATCH (a:Agent {agent_id: $agent_id})
                SET a.last_run_at = datetime(),
                    a.last_run_status = $status,
                    a.last_error = $error,
                    a.error_count = coalesce(a.error_count, 0) + 1
            """, agent_id=agent_id, status=status, error=error)
        else:
            session.run("""
                MATCH (a:Agent {agent_id: $agent_id})
                SET a.last_run_at = datetime(),
                    a.last_run_status = $status,
                    a.last_error = null,
                    a.error_count = 0
            """, agent_id=agent_id, status=status)


def sync_agent_domains(agent_id: str, user_id: str):
    """
    Sync the agent's MONITORS relationships with the user's TRACKS relationships.
    Called when a user adds/removes domains.
    """
    with _get_session() as session:
        # Remove stale MONITORS
        session.run("""
            MATCH (a:Agent {agent_id: $agent_id})-[r:MONITORS]->(d:Domain)
            WHERE NOT ((:User {id: $user_id})-[:TRACKS]->(d))
            DELETE r
        """, agent_id=agent_id, user_id=user_id)

        # Add missing MONITORS
        session.run("""
            MATCH (u:User {id: $user_id})-[:TRACKS]->(d:Domain)
            MATCH (a:Agent {agent_id: $agent_id})
            MERGE (a)-[:MONITORS]->(d)
        """, agent_id=agent_id, user_id=user_id)


def destroy_agent(agent_id: str):
    """Set agent to stopped and remove MONITORS relationships."""
    with _get_session() as session:
        session.run("""
            MATCH (a:Agent {agent_id: $agent_id})
            OPTIONAL MATCH (a)-[r:MONITORS]->()
            DELETE r
            SET a.status = 'stopped'
        """, agent_id=agent_id)
