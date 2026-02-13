"""
Market2Agent - Agent API Endpoints

User endpoints:
    GET  /v1/agents/me          - Get user's agent(s) and status
    GET  /v1/agents/me/status   - Lightweight status check

Admin endpoints:
    GET    /v1/admin/agents           - List all agents
    POST   /v1/admin/agents/:id/stop  - Force stop
    POST   /v1/admin/agents/:id/start - Force start/restart
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional

from app.security import require_auth, require_admin
from app.agents.model import (
    Agent, AgentStatus,
    get_agents_for_user, get_all_agents, get_agent_by_id,
    get_agent_domains, update_agent_status,
)

import structlog

logger = structlog.get_logger()


# =============================================
# RESPONSE MODELS
# =============================================

class AgentResponse(BaseModel):
    agent_id: str
    status: str
    plan: str
    created_at: Optional[str]
    last_heartbeat: Optional[str]
    last_run_at: Optional[str]
    last_run_status: Optional[str]
    last_error: Optional[str]
    error_count: int
    paused_reason: Optional[str]
    domains: List[str] = []


class AgentAdminResponse(AgentResponse):
    user_id: str
    subscription_id: str
    user_email: Optional[str]


class AgentStatusResponse(BaseModel):
    """Lightweight status for dashboard polling."""
    has_agent: bool
    status: Optional[str]
    last_heartbeat: Optional[str]
    last_run_status: Optional[str]


# =============================================
# USER ENDPOINTS
# =============================================

user_router = APIRouter(prefix="/v1/agents", tags=["agents"])


@user_router.get("/me", response_model=List[AgentResponse])
async def get_my_agents(user: dict = Depends(require_auth)):
    """Get current user's agents with domains and status."""
    agents = get_agents_for_user(user["id"])

    result = []
    for a in agents:
        domains = get_agent_domains(a.agent_id)
        result.append(AgentResponse(
            agent_id=a.agent_id,
            status=a.status,
            plan=a.plan,
            created_at=a.created_at,
            last_heartbeat=a.last_heartbeat,
            last_run_at=a.last_run_at,
            last_run_status=a.last_run_status,
            last_error=a.last_error,
            error_count=a.error_count,
            paused_reason=a.paused_reason,
            domains=domains,
        ))

    return result


@user_router.get("/me/status", response_model=AgentStatusResponse)
async def get_my_agent_status(user: dict = Depends(require_auth)):
    """
    Lightweight agent status check.
    Use this for dashboard polling (cheaper than full /me).
    """
    agents = get_agents_for_user(user["id"])

    if not agents:
        return AgentStatusResponse(
            has_agent=False,
            status=None,
            last_heartbeat=None,
            last_run_status=None,
        )

    # Return first active agent (users have one agent in current model)
    a = agents[0]
    return AgentStatusResponse(
        has_agent=True,
        status=a.status,
        last_heartbeat=a.last_heartbeat,
        last_run_status=a.last_run_status,
    )


# =============================================
# ADMIN ENDPOINTS
# =============================================

admin_router = APIRouter(prefix="/v1/admin/agents", tags=["admin-agents"])


@admin_router.get("", response_model=List[AgentAdminResponse])
async def list_all_agents(
    admin: dict = Depends(require_admin),
    limit: int = Query(100, le=500),
):
    """List all agents with user info."""
    agents = get_all_agents(limit=limit)

    result = []
    for a in agents:
        domains = get_agent_domains(a.agent_id)
        result.append(AgentAdminResponse(
            agent_id=a.agent_id,
            user_id=a.user_id,
            subscription_id=a.subscription_id,
            user_email=a.config.get("_user_email"),
            status=a.status,
            plan=a.plan,
            created_at=a.created_at,
            last_heartbeat=a.last_heartbeat,
            last_run_at=a.last_run_at,
            last_run_status=a.last_run_status,
            last_error=a.last_error,
            error_count=a.error_count,
            paused_reason=a.paused_reason,
            domains=domains,
        ))

    return result


@admin_router.post("/{agent_id}/stop")
async def force_stop_agent(
    agent_id: str,
    admin: dict = Depends(require_admin),
):
    """Force stop an agent. Requires manual restart."""
    agent = get_agent_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.status == AgentStatus.STOPPED:
        return {"message": "Agent already stopped"}

    update_agent_status(agent_id, AgentStatus.STOPPED)
    logger.info("admin_agent_stopped",
                agent_id=agent_id,
                admin_id=admin["id"])

    return {"message": f"Agent {agent_id} stopped"}


@admin_router.post("/{agent_id}/start")
async def force_start_agent(
    agent_id: str,
    admin: dict = Depends(require_admin),
):
    """Force start/restart an agent. Resets error count."""
    agent = get_agent_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    update_agent_status(agent_id, AgentStatus.RUNNING)

    # Reset error count
    from app.agents.model import _get_session
    with _get_session() as session:
        session.run("""
            MATCH (a:Agent {agent_id: $agent_id})
            SET a.error_count = 0, a.last_error = null
        """, agent_id=agent_id)

    logger.info("admin_agent_started",
                agent_id=agent_id,
                admin_id=admin["id"])

    return {"message": f"Agent {agent_id} started"}
