# Agent Provisioning & Lifecycle System

## Overview

This adds first-class per-customer agents to Market2Agent. When a customer
subscribes, an agent is automatically created, begins running weekly audits
on their tracked domains, and stops when billing stops.

No manual steps after payment.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     STRIPE                                    │
│  checkout.session.completed                                   │
│  customer.subscription.created/updated/deleted                │
│  invoice.payment_failed                                       │
└──────────────┬───────────────────────────────────────────────┘
               │ webhook
               ▼
┌──────────────────────────────────────────────────────────────┐
│  billing.py (existing) ──→ provisioner.py (NEW)               │
│                                                               │
│  Provisioner maps events to lifecycle transitions:            │
│    checkout.completed  → create_agent()  → status: running    │
│    subscription.deleted → destroy_agent() → status: stopped   │
│    payment_failed      → pause_agent()   → status: paused    │
│    subscription.active → resume_agent()  → status: running    │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│  Neo4j: (:Agent) node                                         │
│                                                               │
│  (:User)-[:OWNS_AGENT]->(:Agent)-[:MONITORS]->(:Domain)      │
│                                                               │
│  Fields: agent_id, user_id, subscription_id, plan, status,    │
│          last_heartbeat, last_run_at, last_run_status,        │
│          last_error, error_count                              │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│  arq Worker (existing process, new jobs)                      │
│                                                               │
│  Cron: scheduler.tick() every 15 min                          │
│    └─ For each agent with status='running':                   │
│       1. Record heartbeat                                     │
│       2. Check billing enforcement (interval, limits)         │
│       3. Acquire Redis lock                                   │
│       4. Queue execute_agent() job                            │
│                                                               │
│  Job: execute_agent(agent_id, domains)                        │
│    └─ For each domain (with concurrency limit):               │
│       1. Crawl structured data                                │
│       2. Check entity presence                                │
│       3. Calculate score                                      │
│       4. Save audit to Neo4j                                  │
│    └─ Record success/failure                                  │
│    └─ Release lock                                            │
│    └─ Escalate if error_count >= 5                            │
└──────────────────────────────────────────────────────────────┘
```

---

## Agent Lifecycle

```
                    ┌─────────────────┐
                    │  PROVISIONING   │
     checkout.      │  (initial)      │
     completed      └────────┬────────┘
                             │ automatic
                             ▼
         ┌──────────────────────────────────────┐
         │             RUNNING                   │
         │  Agent is active, scheduler will      │
         │  pick it up on next tick               │
         └───┬──────────┬──────────┬─────────────┘
             │          │          │
    payment  │   5 consecutive     │  subscription
    failed   │   errors            │  deleted
             ▼          ▼          ▼
        ┌────────┐ ┌────────┐ ┌────────┐
        │ PAUSED │ │ERRORED │ │STOPPED │
        │        │ │        │ │(final) │
        └───┬────┘ └───┬────┘ └────────┘
            │          │
   payment  │   admin  │
   recovered│   restart│
            ▼          ▼
        ┌──────────────────┐
        │     RUNNING      │
        └──────────────────┘
```

---

## Stripe Event → System Behavior

| Stripe Event                        | Agent Action           | New Status     | Notes                              |
|-------------------------------------|------------------------|----------------|------------------------------------|
| `checkout.session.completed`        | Create + start agent   | `running`      | Idempotent; links user's domains   |
| `customer.subscription.created`     | Create + start agent   | `running`      | Safety net; same as checkout       |
| `customer.subscription.updated`     |                        |                |                                    |
| &nbsp;&nbsp;→ status = `active`     | Resume agent           | `running`      | Clears paused_reason               |
| &nbsp;&nbsp;→ status = `past_due`   | Pause agent            | `paused`       | Preserves data, stops execution    |
| &nbsp;&nbsp;→ status = `unpaid`     | Pause agent            | `paused`       | Same as past_due                   |
| `customer.subscription.deleted`     | Destroy agent          | `stopped`      | Removes MONITORS links             |
| `invoice.payment_failed`            | Pause agent            | `paused`       | Agent resumes when payment recovers|

---

## Billing Enforcement (Hard Limits)

| Plan   | Price   | Max Domains | Execution Interval | Max Pages/Domain | Max Concurrent |
|--------|---------|-------------|-------------------|------------------|----------------|
| Pro    | $20/mo  | 10          | 168h (weekly)     | 5                | 3              |

Enforcement happens at two points:

1. **Before execution** (in `scheduler.tick()`):
   - Status must be `running`
   - Time since `last_run_at` must exceed `execution_interval_hours`
   - Redis lock must be acquirable (prevents double execution)

2. **During execution** (in `execute_agent()`):
   - Domain list truncated to `max_domains`
   - Asyncio semaphore limits concurrent audits to `max_concurrent_audits`
   - Page crawl depth limited to `max_pages_per_domain` (enforced in crawler)

These are not "best effort". If a check fails, execution is blocked.

---

## Runtime Model

**Choice: arq cron + arq jobs (shared worker)**

Why not alternatives:
- **asyncio background tasks**: No persistence across restarts, no graceful shutdown, no job queuing.
- **Celery**: Adds a dependency. arq is already running and Redis-backed.
- **Separate daemon process**: Unnecessary for this scale. Adds ops burden.

How it works:
1. arq's `cron_jobs` runs `scheduler.tick()` every 15 minutes
2. `tick()` iterates all `running` agents, checks enforcement, queues `execute_agent` jobs
3. `execute_agent` runs as a normal arq job with timeout
4. This runs in the same worker process as your existing `run_audit` jobs

The scheduler is the clock. The runner is the execution. Redis provides locking. Neo4j provides state.

---

## Observability

### Agent status (stored in Neo4j):
- `status`: current lifecycle state
- `last_heartbeat`: updated every scheduler tick (every 15 min while running)
- `last_run_at`: timestamp of last audit execution
- `last_run_status`: `success` | `failed` | `skipped`
- `last_error`: error message from last failure
- `error_count`: consecutive failures (resets on success)
- `paused_reason`: why agent was paused (payment_failed, etc.)

### Execution locks (stored in Redis):
- Key: `agent_lock:{agent_id}`, TTL: 600s
- Prevents double execution if scheduler ticks overlap

### Endpoints:
- **User**: `GET /v1/agents/me` — full agent info + domains
- **User**: `GET /v1/agents/me/status` — lightweight poll
- **Admin**: `GET /v1/admin/agents` — all agents with user emails
- **Admin**: `POST /v1/admin/agents/{id}/stop` — force stop
- **Admin**: `POST /v1/admin/agents/{id}/start` — force start + reset errors

---

## Files Added

```
app/
├── agents/
│   ├── __init__.py
│   ├── model.py           # Agent schema, Neo4j CRUD, plan limits
│   ├── provisioner.py     # Stripe event → lifecycle transitions
│   └── scheduler.py       # Periodic tick, billing enforcement, locks
├── api/
│   ├── agents.py          # User + admin HTTP endpoints
│   ├── billing_patch.py   # Integration instructions for billing.py
│   └── main_patch.py      # Integration instructions for main.py
└── workers/
    ├── agent_runner.py    # Per-agent audit execution job
    └── worker_settings.py # Updated arq config
```

## Files Modified (patches)

- `app/api/billing.py` — Add 2 lines (import + call provisioner)
- `app/main.py` — Add 2 lines (import + register routers)
- `app/worker.py` — Add execute_agent to functions list, add cron_jobs
- `app/workers/audit_worker.py` or `app/worker.py` — Merge WorkerSettings

---

## Integration Steps (on Droplet 2)

```bash
cd /opt/market2agent

# 1. Copy new files
cp -r /tmp/m2a_agents/app/agents/ app/agents/
cp /tmp/m2a_agents/app/api/agents.py app/api/agents.py
cp /tmp/m2a_agents/app/workers/agent_runner.py app/workers/agent_runner.py

# 2. Patch billing.py — add 2 lines
# At top: from app.agents.provisioner import handle_stripe_event
# Before return: provisioner_result = handle_stripe_event(event_type, data)

# 3. Patch main.py — add 2 lines
# Import: from app.api.agents import user_router, admin_router
# Register: app.include_router(user_router); app.include_router(admin_router)

# 4. Patch worker — add execute_agent + cron
# See worker_settings.py for exact code

# 5. Restart
systemctl restart m2a-api
systemctl restart m2a-worker

# 6. Verify
curl https://api.market2agent.ai/health
curl https://api.market2agent.ai/v1/admin/agents  # (with auth)
```

---

## How to Test Locally

### 1. Test provisioner directly:
```python
from app.agents.provisioner import provision_agent, pause_agent, resume_agent, destroy_agent

# Simulate checkout
agent = provision_agent("user-123", "sub_test_001", "pro")
print(f"Created: {agent.agent_id}, status: {agent.status}")

# Simulate payment failure
pause_agent("sub_test_001", "payment_failed")

# Simulate recovery
resume_agent("sub_test_001")

# Simulate cancellation
destroy_agent("sub_test_001")
```

### 2. Test billing enforcement:
```python
from app.agents.scheduler import check_execution_allowed
from app.agents.model import get_agent_by_id

agent = get_agent_by_id("your-agent-id")
allowed, reason = check_execution_allowed(agent)
print(f"Allowed: {allowed}, Reason: {reason}")
```

### 3. Test with Stripe CLI (webhook testing):
```bash
stripe listen --forward-to https://api.market2agent.ai/v1/billing/webhook

# In another terminal:
stripe trigger checkout.session.completed
stripe trigger customer.subscription.deleted
stripe trigger invoice.payment_failed
```

### 4. Test scheduler manually:
```python
import asyncio
from app.agents.scheduler import tick
asyncio.run(tick())
```

---

## Failure Modes & Recovery

| Failure                         | Behavior                                     | Recovery                              |
|---------------------------------|----------------------------------------------|---------------------------------------|
| Neo4j down                      | Scheduler tick fails, no agents execute       | Auto-recovers when Neo4j returns      |
| Redis down                      | Can't acquire locks, no execution             | Auto-recovers when Redis returns      |
| Single audit fails              | Recorded as partial success, agent continues  | Automatic on next cycle               |
| All audits fail                 | error_count incremented                       | Auto-retry next cycle                 |
| 5 consecutive failures          | Agent → `errored` status                      | Admin must restart via API            |
| Worker crashes mid-execution    | Redis lock expires after 600s                 | Next tick re-queues normally          |
| Stripe webhook missed           | Agent not created/paused                      | Re-send from Stripe dashboard         |
| Duplicate webhook               | All operations idempotent, no double-create   | No action needed                      |
| User adds domain after agent    | Domain not monitored until sync               | Deferred: auto-sync not implemented   |

### Intentionally Deferred

1. **Auto domain sync**: When a user adds/removes a domain, the agent's MONITORS
   relationships are not automatically updated. `sync_agent_domains()` exists in model.py
   but is not wired into the domain add/remove endpoints. Wire it in by calling
   `sync_agent_domains(agent_id, user_id)` in dashboard.py after add/remove. Low risk
   since the scheduler runs weekly.

2. **Email alerts on agent errors**: The scheduler module doesn't send emails.
   The email service exists but is not wired to error escalation. Add a call to
   `send_email()` inside `check_error_escalation()` when ready.

3. **Plan upgrade/downgrade**: PLAN_LIMITS only has 'pro'. When you add tiers,
   add entries to PLAN_LIMITS and the provisioner will pick up the plan from
   Stripe subscription metadata.

4. **Metrics/Prometheus**: Structured logs cover observability for now.
   Add Prometheus counters when you have enough traffic to justify it.

---

## Neo4j Schema Additions

Run this on Droplet 3 (or via Droplet 2):

```cypher
// Agent constraints
CREATE CONSTRAINT agent_id_unique IF NOT EXISTS
FOR (a:Agent) REQUIRE a.agent_id IS UNIQUE;

CREATE CONSTRAINT agent_subscription_unique IF NOT EXISTS
FOR (a:Agent) REQUIRE a.subscription_id IS UNIQUE;

// Index for scheduler queries
CREATE INDEX agent_status IF NOT EXISTS
FOR (a:Agent) ON (a.status);
```

---

## Definition of Done Checklist

- [x] New Stripe subscription creates and starts an agent automatically
- [x] Cancelling a subscription stops the agent
- [x] Payment failure pauses the agent, recovery resumes it
- [x] Admin can see all agent statuses
- [x] Admin can force stop/start agents
- [x] User can see their agent is alive
- [x] Billing limits enforced before every execution
- [x] No manual steps required after payment
- [x] All operations idempotent
- [x] Deployable on single VM with existing stack
