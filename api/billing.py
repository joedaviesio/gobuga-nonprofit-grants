"""Stripe billing — checkout, webhooks, customer portal."""

import json
import os
from dotenv import load_dotenv

load_dotenv()

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_STARTER_PRICE_ID = os.getenv("STRIPE_STARTER_PRICE_ID", "")
STRIPE_PROFESSIONAL_PRICE_ID = os.getenv("STRIPE_PROFESSIONAL_PRICE_ID", "")
APP_URL = os.getenv("APP_URL", "http://localhost:3002")

PRICE_TO_PLAN = {}
if STRIPE_STARTER_PRICE_ID:
    PRICE_TO_PLAN[STRIPE_STARTER_PRICE_ID] = "starter"
if STRIPE_PROFESSIONAL_PRICE_ID:
    PRICE_TO_PLAN[STRIPE_PROFESSIONAL_PRICE_ID] = "professional"


def _get_stripe():
    """Lazy import stripe to avoid import errors when not installed."""
    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        return stripe
    except ImportError:
        raise RuntimeError("Stripe is not installed. Run: pip install stripe")


def create_checkout_session(org_id: str, plan: str) -> dict:
    """Create a Stripe Checkout session for upgrading to a paid plan.
    Returns {"url": checkout_url}.
    """
    stripe = _get_stripe()
    from api.auth import get_org, update_org

    org = get_org(org_id)
    if not org:
        raise ValueError("Org not found")

    # Map plan to price ID
    price_id = {
        "starter": STRIPE_STARTER_PRICE_ID,
        "professional": STRIPE_PROFESSIONAL_PRICE_ID,
    }.get(plan)

    if not price_id:
        raise ValueError(f"Invalid plan: {plan}. Use 'starter' or 'professional'.")

    # Get or create Stripe customer
    customer_id = org.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            metadata={"org_id": org_id, "org_name": org.get("name", "")},
        )
        customer_id = customer.id
        update_org(org_id, {"stripe_customer_id": customer_id})

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{APP_URL}/settings?checkout=success",
        cancel_url=f"{APP_URL}/settings?checkout=cancel",
        metadata={"org_id": org_id, "plan": plan},
    )

    return {"url": session.url, "session_id": session.id}


def create_portal_session(org_id: str) -> dict:
    """Create a Stripe Customer Portal session. Returns {"url": portal_url}."""
    stripe = _get_stripe()
    from api.auth import get_org

    org = get_org(org_id)
    if not org:
        raise ValueError("Org not found")

    customer_id = org.get("stripe_customer_id")
    if not customer_id:
        raise ValueError("No billing account. Subscribe to a plan first.")

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{APP_URL}/settings",
    )

    return {"url": session.url}


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Process a Stripe webhook event. Returns {"received": True}."""
    stripe = _get_stripe()
    from api.auth import update_org

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        raise ValueError(f"Webhook verification failed: {e}")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        org_id = data.get("metadata", {}).get("org_id")
        plan = data.get("metadata", {}).get("plan")
        subscription_id = data.get("subscription")
        if org_id and plan:
            # Idempotency: skip if already on this plan with this subscription
            from api.auth import get_org
            org = get_org(org_id)
            if org and org.get("plan") == plan and org.get("stripe_subscription_id") == subscription_id:
                print(f"[Billing] Org {org_id} already on {plan} — skipping duplicate webhook")
            else:
                update_org(org_id, {
                    "plan": plan,
                    "stripe_subscription_id": subscription_id,
                })
                print(f"[Billing] Org {org_id} upgraded to {plan}")

    elif event_type == "customer.subscription.updated":
        # Handle plan changes
        subscription_id = data.get("id")
        price_id = data.get("items", {}).get("data", [{}])[0].get("price", {}).get("id")
        new_plan = PRICE_TO_PLAN.get(price_id)
        if new_plan:
            from api.auth import _load_orgs, _save_orgs
            orgs = _load_orgs()
            for oid, org in orgs.items():
                if org.get("stripe_subscription_id") == subscription_id:
                    if org.get("plan") == new_plan:
                        print(f"[Billing] Org {oid} already on {new_plan} — skipping")
                    else:
                        org["plan"] = new_plan
                        _save_orgs(orgs)
                        print(f"[Billing] Org {oid} plan changed to {new_plan}")
                    break

    elif event_type == "customer.subscription.deleted":
        # Downgrade to free on cancellation
        subscription_id = data.get("id")
        from api.auth import _load_orgs, _save_orgs
        orgs = _load_orgs()
        for oid, org in orgs.items():
            if org.get("stripe_subscription_id") == subscription_id:
                org["plan"] = "free"
                org["stripe_subscription_id"] = None
                _save_orgs(orgs)
                print(f"[Billing] Org {oid} downgraded to free (subscription cancelled)")
                break

    elif event_type == "invoice.payment_failed":
        # Log failed payment — org keeps access until subscription is cancelled by Stripe
        customer_id = data.get("customer")
        attempt = data.get("attempt_count", 0)
        from api.auth import _load_orgs
        orgs = _load_orgs()
        for oid, org in orgs.items():
            if org.get("stripe_customer_id") == customer_id:
                print(f"[Billing] Payment failed for org {oid} (attempt {attempt})")
                break

    return {"received": True}
