"""
Market2Agent - Agent Provisioner

Maps Stripe subscription events to agent lifecycle transitions.

This module is called FROM the existing billing webhook handler.
It does not own the webhook endpoint — billing.py does.

All operations are idempotent. Calling provision() twice with the
same subscription_id returns the same agent without side effects.

Lifecycle mapping:
    checkout.session.completed       -> create agent, set 'running'
    customer.subscription.created    -> create agent, set 'running'  (redundant safety)
    customer.subscription.updated
        status=active                -> resume agent (set 'running')
        status=past_due              -> pause agent (set 'paused')
        status=unpaid                -> pause agent (set 'paused')
    customer.subscription.deleted    -> stop agent (set 'stopped')
    invoice.payment_failed           -> pause agent (set 'paused')
"""
import uuid
import structlog

from app.agents.model import (
    Agent, AgentStatus, AgentPlan,
    create_agent, get_agent_by_subscription,
    update_agent_status_by_subscription,
    destroy_agent as model_destroy_agent,
)

logger = structlog.get_logger()


def provision_agent(user_id: str, subscription_id: str, plan: str = "pro") -> Agent:
    """
    Create and start an agent for a new subscription.
    Idempotent: if agent for this subscription exists, activates it.

    Called on: checkout.session.completed, customer.subscription.created
    """
    existing = get_agent_by_subscription(subscription_id)

    if existing:
        # Agent exists — make sure it's running
        if existing.status != AgentStatus.RUNNING:
            update_agent_status_by_subscription(subscription_id, AgentStatus.RUNNING)
            logger.info("agent_reactivated",
                        agent_id=existing.agent_id,
                        subscription_id=subscription_id)
        return existing

    # Create new agent
    agent_id = str(uuid.uuid4())
    agent = create_agent(
        agent_id=agent_id,
        user_id=user_id,
        subscription_id=subscription_id,
        plan=plan,
    )

    # Transition: provisioning -> running
    update_agent_status_by_subscription(subscription_id, AgentStatus.RUNNING)

    logger.info("agent_provisioned",
                agent_id=agent_id,
                user_id=user_id,
                subscription_id=subscription_id,
                plan=plan)

    return agent


def pause_agent(subscription_id: str, reason: str = "payment_issue") -> bool:
    """
    Pause an agent due to billing issues.
    Agent stops executing but is not destroyed. Data preserved.

    Called on: invoice.payment_failed, subscription.updated(past_due/unpaid)
    """
    existing = get_agent_by_subscription(subscription_id)

    if not existing:
        logger.warning("pause_no_agent", subscription_id=subscription_id)
        return False

    if existing.status == AgentStatus.STOPPED:
        logger.info("pause_already_stopped",
                     agent_id=existing.agent_id,
                     subscription_id=subscription_id)
        return False

    if existing.status == AgentStatus.PAUSED:
        logger.info("pause_already_paused",
                     agent_id=existing.agent_id,
                     subscription_id=subscription_id)
        return True

    update_agent_status_by_subscription(
        subscription_id, AgentStatus.PAUSED, reason=reason
    )

    logger.info("agent_paused",
                agent_id=existing.agent_id,
                subscription_id=subscription_id,
                reason=reason)

    return True


def resume_agent(subscription_id: str) -> bool:
    """
    Resume a paused agent after payment recovery.

    Called on: subscription.updated(active) when previously paused
    """
    existing = get_agent_by_subscription(subscription_id)

    if not existing:
        logger.warning("resume_no_agent", subscription_id=subscription_id)
        return False

    if existing.status == AgentStatus.STOPPED:
        logger.info("resume_stopped_agent",
                     agent_id=existing.agent_id,
                     subscription_id=subscription_id)
        return False

    if existing.status == AgentStatus.RUNNING:
        return True  # Already running

    update_agent_status_by_subscription(subscription_id, AgentStatus.RUNNING)

    logger.info("agent_resumed",
                agent_id=existing.agent_id,
                subscription_id=subscription_id)

    return True


def destroy_agent(subscription_id: str) -> bool:
    """
    Permanently stop an agent. Removes domain monitoring links.
    Agent node preserved for audit trail.

    Called on: customer.subscription.deleted
    """
    existing = get_agent_by_subscription(subscription_id)

    if not existing:
        logger.warning("destroy_no_agent", subscription_id=subscription_id)
        return False

    if existing.status == AgentStatus.STOPPED:
        return True  # Already stopped

    model_destroy_agent(existing.agent_id)

    logger.info("agent_destroyed",
                agent_id=existing.agent_id,
                subscription_id=subscription_id)

    return True


# =============================================
# WEBHOOK DISPATCHER
# =============================================

def handle_stripe_event(event_type: str, data: dict) -> dict:
    """
    Entry point called from billing webhook.
    Returns dict with action taken for logging.

    Wire this into your existing billing.py webhook handler.
    """
    result = {"event": event_type, "action": "none"}

    try:
        if event_type == "checkout.session.completed":
            customer_id = data.get("customer")
            subscription_id = data.get("subscription")
            user_id = data.get("metadata", {}).get("user_id")

            if not user_id or not subscription_id:
                logger.warning("checkout_missing_metadata", data_keys=list(data.keys()))
                return {"event": event_type, "action": "skipped", "reason": "missing metadata"}

            agent = provision_agent(user_id, subscription_id)
            result = {"event": event_type, "action": "provisioned", "agent_id": agent.agent_id}

        elif event_type == "customer.subscription.created":
            subscription_id = data.get("id")
            customer_id = data.get("customer")
            user_id = data.get("metadata", {}).get("user_id")
            status = data.get("status")

            if user_id and subscription_id and status == "active":
                agent = provision_agent(user_id, subscription_id)
                result = {"event": event_type, "action": "provisioned", "agent_id": agent.agent_id}

        elif event_type == "customer.subscription.updated":
            subscription_id = data.get("id")
            status = data.get("status")

            if status == "active":
                resume_agent(subscription_id)
                result = {"event": event_type, "action": "resumed"}
            elif status in ("past_due", "unpaid"):
                pause_agent(subscription_id, reason=f"subscription_{status}")
                result = {"event": event_type, "action": "paused", "reason": status}

        elif event_type == "customer.subscription.deleted":
            subscription_id = data.get("id")
            destroy_agent(subscription_id)
            result = {"event": event_type, "action": "destroyed"}

        elif event_type == "invoice.payment_failed":
            subscription_id = data.get("subscription")
            if subscription_id:
                pause_agent(subscription_id, reason="payment_failed")
                result = {"event": event_type, "action": "paused", "reason": "payment_failed"}

    except Exception as e:
        logger.error("provisioner_error", event_type=event_type, error=str(e))
        result = {"event": event_type, "action": "error", "error": str(e)}

    logger.info("provisioner_result", **result)
    return result
