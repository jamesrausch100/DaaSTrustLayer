"""
Market2Agent - Stripe Subscriptions
Handles checkout, webhooks, and subscription management.
"""
import stripe
from fastapi import APIRouter, HTTPException, Request, Depends, Header
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional
import structlog

from app.config import settings
from app.db import get_session
from app.auth import require_auth

logger = structlog.get_logger()

# Initialize Stripe
stripe.api_key = settings.STRIPE_SECRET_KEY

router = APIRouter(prefix="/v1", tags=["subscriptions"])


# ===========================================
# Models
# ===========================================

class SubscriptionResponse(BaseModel):
    status: str
    tier: str
    current_period_end: Optional[str]
    cancel_at_period_end: bool


class CheckoutResponse(BaseModel):
    checkout_url: str


# ===========================================
# Database Operations
# ===========================================

def update_user_subscription(user_id: str, stripe_customer_id: str, status: str, tier: str):
    """Update user's subscription status in database."""
    
    
    with get_session() as session:
        session.run("""
            MATCH (u:User {id: $user_id})
            SET u.stripe_customer_id = $stripe_customer_id,
                u.subscription_status = $status,
                u.subscription_tier = $tier,
                u.subscription_updated_at = datetime()
        """, user_id=user_id, stripe_customer_id=stripe_customer_id, status=status, tier=tier)


def update_subscription_by_customer_id(stripe_customer_id: str, status: str, tier: str):
    """Update subscription by Stripe customer ID (for webhooks)."""
    
    
    with get_session() as session:
        session.run("""
            MATCH (u:User {stripe_customer_id: $stripe_customer_id})
            SET u.subscription_status = $status,
                u.subscription_tier = $tier,
                u.subscription_updated_at = datetime()
        """, stripe_customer_id=stripe_customer_id, status=status, tier=tier)


def get_user_by_stripe_customer(stripe_customer_id: str) -> Optional[dict]:
    """Get user by Stripe customer ID."""
    
    
    with get_session() as session:
        result = session.run("""
            MATCH (u:User {stripe_customer_id: $stripe_customer_id})
            RETURN u
        """, stripe_customer_id=stripe_customer_id)
        
        record = result.single()
        return dict(record["u"]) if record else None


# ===========================================
# Endpoints
# ===========================================

@router.post("/subscribe", response_model=CheckoutResponse)
async def create_checkout_session(user: dict = Depends(require_auth)):
    """Create a Stripe checkout session for subscription."""
    
    try:
        # Check if user already has a Stripe customer ID
        customer_id = user.get("stripe_customer_id")
        
        if not customer_id:
            # Create new Stripe customer
            customer = stripe.Customer.create(
                email=user["email"],
                name=user.get("name"),
                metadata={"user_id": user["id"]}
            )
            customer_id = customer.id
            
            # Save customer ID to database
            
            with get_session() as session:
                session.run("""
                    MATCH (u:User {id: $user_id})
                    SET u.stripe_customer_id = $customer_id
                """, user_id=user["id"], customer_id=customer_id)
        
        # Create checkout session
        # MIS-03: Use tier-specific price from config
        price_id = getattr(settings, f"STRIPE_PRICE_{tier.upper()}", "") if tier else settings.STRIPE_PRICE_PRO
        if not price_id:
            raise HTTPException(status_code=400, detail=f"No Stripe price configured for tier: {tier}")
        
        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{
                "price": price_id,
                "quantity": 1
            }],
            mode="subscription",
            success_url=f"{settings.APP_URL}/?subscription=success",
            cancel_url=f"{settings.APP_URL}/?subscription=canceled",
            metadata={"user_id": user["id"], "tier": tier or "pro"}
        )
        
        logger.info("checkout_created", user_id=user["id"], session_id=checkout_session.id)
        
        return CheckoutResponse(checkout_url=checkout_session.url)
        
    except stripe.error.StripeError as e:
        logger.error("stripe_error", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(user: dict = Depends(require_auth)):
    """Get current user's subscription status."""
    
    customer_id = user.get("stripe_customer_id")
    
    if not customer_id or user.get("subscription_status") == "free":
        return SubscriptionResponse(
            status="free",
            tier="free",
            current_period_end=None,
            cancel_at_period_end=False
        )
    
    try:
        # Get subscription from Stripe
        subscriptions = stripe.Subscription.list(customer=customer_id, limit=1)
        
        if not subscriptions.data:
            return SubscriptionResponse(
                status="free",
                tier="free",
                current_period_end=None,
                cancel_at_period_end=False
            )
        
        sub = subscriptions.data[0]
        
        return SubscriptionResponse(
            status=sub.status,
            tier="agent" if sub.status == "active" else "free",
            current_period_end=str(sub.current_period_end),
            cancel_at_period_end=sub.cancel_at_period_end
        )
        
    except stripe.error.StripeError as e:
        logger.error("stripe_error", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/subscription/cancel")
async def cancel_subscription(user: dict = Depends(require_auth)):
    """Cancel subscription at end of billing period."""
    
    customer_id = user.get("stripe_customer_id")
    
    if not customer_id:
        raise HTTPException(status_code=400, detail="No subscription found")
    
    try:
        subscriptions = stripe.Subscription.list(customer=customer_id, limit=1)
        
        if not subscriptions.data:
            raise HTTPException(status_code=400, detail="No active subscription")
        
        sub = subscriptions.data[0]
        
        # Cancel at period end (don't immediately cancel)
        stripe.Subscription.modify(sub.id, cancel_at_period_end=True)
        
        logger.info("subscription_canceled", user_id=user["id"], subscription_id=sub.id)
        
        return {"message": "Subscription will be canceled at end of billing period"}
        
    except stripe.error.StripeError as e:
        logger.error("stripe_error", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/subscription/reactivate")
async def reactivate_subscription(user: dict = Depends(require_auth)):
    """Reactivate a canceled subscription."""
    
    customer_id = user.get("stripe_customer_id")
    
    if not customer_id:
        raise HTTPException(status_code=400, detail="No subscription found")
    
    try:
        subscriptions = stripe.Subscription.list(customer=customer_id, limit=1)
        
        if not subscriptions.data:
            raise HTTPException(status_code=400, detail="No subscription found")
        
        sub = subscriptions.data[0]
        
        # Remove cancellation
        stripe.Subscription.modify(sub.id, cancel_at_period_end=False)
        
        logger.info("subscription_reactivated", user_id=user["id"], subscription_id=sub.id)
        
        return {"message": "Subscription reactivated"}
        
    except stripe.error.StripeError as e:
        logger.error("stripe_error", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


# ===========================================
# Stripe Webhook
# ===========================================

@router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events."""
    
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    logger.info("stripe_webhook", event_type=event["type"])
    
    # Handle subscription events
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_id = session["customer"]
        user_id = session.get("metadata", {}).get("user_id")
        tier = session.get("metadata", {}).get("tier", "pro")
        
        if user_id:
            update_user_subscription(user_id, customer_id, "active", tier)
            logger.info("subscription_activated", user_id=user_id, tier=tier)
    
    elif event["type"] == "customer.subscription.updated":
        subscription = event["data"]["object"]
        customer_id = subscription["customer"]
        status = subscription["status"]
        
        # Preserve existing tier on status change, downgrade to free on cancel
        tier = "free" if status != "active" else None
        if tier:
            update_subscription_by_customer_id(customer_id, status, tier)
        else:
            # Keep current tier, just update status
            update_subscription_by_customer_id(customer_id, status, None)
        logger.info("subscription_updated", customer_id=customer_id, status=status)
    
    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription["customer"]
        
        update_subscription_by_customer_id(customer_id, "canceled", "free")
        logger.info("subscription_deleted", customer_id=customer_id)
    
    elif event["type"] == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_id = invoice["customer"]
        
        update_subscription_by_customer_id(customer_id, "past_due", "agent")
        logger.info("payment_failed", customer_id=customer_id)
    
    return {"status": "ok"}
