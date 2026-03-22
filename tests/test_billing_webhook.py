"""
Test script: billing webhook handling and Stripe integration.

Tests:
  1. Webhook with valid signature processes checkout.session.completed
  2. Webhook with valid signature processes subscription.deleted (downgrade)
  3. Webhook with invalid signature returns 400
  4. Checkout session creation returns a URL

Usage:
    python3 tests/test_billing_webhook.py
    python3 tests/test_billing_webhook.py --case 1
"""

import os
import sys
import time
import json
import hmac
import hashlib
import requests

API = os.environ.get("API_URL", "http://localhost:8102")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

PASS = 0
FAIL = 0
TOKEN = None
ORG_ID = None
TEST_EMAIL = f"test-billing-{int(time.time())}@gobuga-test.org"
TEST_PASSWORD = "TestPass123!"


def log(msg, indent=0):
    print("  " * indent + msg)


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        log(f"[PASS] {label}")
    else:
        FAIL += 1
        log(f"[FAIL] {label}" + (f"  — {detail}" if detail else ""))
    return condition


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def setup_auth():
    """Register a test org and get auth token."""
    global TOKEN, ORG_ID
    r = requests.post(f"{API}/api/auth/register", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
        "org_name": "Test Billing Org",
    })
    if r.status_code == 200:
        data = r.json()
        TOKEN = data.get("token")
        ORG_ID = data.get("org_id")
    else:
        log(f"[WARN] Registration failed ({r.status_code}), trying login...")
        r = requests.post(f"{API}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        data = r.json()
        TOKEN = data.get("token")
        ORG_ID = data.get("org_id")


def _build_stripe_signature(payload: str, secret: str) -> str:
    """Build a Stripe-compatible webhook signature."""
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload}"
    sig = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={sig}"


# ---------------------------------------------------------------------------
# Case 1 — checkout.session.completed upgrades org
# ---------------------------------------------------------------------------
def case_1():
    log("\n=== Case 1: Webhook checkout.session.completed ===")

    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        # Test structurally — verify the endpoint exists and rejects unsigned payloads
        log("  (No STRIPE_WEBHOOK_SECRET set — testing endpoint existence and rejection)")

        payload = json.dumps({
            "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": ORG_ID or "test", "subscription": "sub_test"}},
        })
        r = requests.post(f"{API}/api/billing/webhook",
                          data=payload,
                          headers={"Content-Type": "application/json", "Stripe-Signature": "t=0,v1=invalid"})

        check("Webhook endpoint exists (not 404)", r.status_code != 404,
              f"Got {r.status_code}")
        check("Invalid signature rejected (400)", r.status_code == 400,
              f"Got {r.status_code}")
        return

    # Full test with valid signature
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": ORG_ID,
                "subscription": "sub_test_upgrade",
                "metadata": {"plan": "professional"},
            }
        },
    })
    sig = _build_stripe_signature(payload, webhook_secret)
    r = requests.post(f"{API}/api/billing/webhook",
                      data=payload,
                      headers={"Content-Type": "application/json", "Stripe-Signature": sig})

    check("Webhook accepted (200)", r.status_code == 200, f"Got {r.status_code}")
    if r.status_code == 200:
        check("Response confirms receipt", r.json().get("received") is True)


# ---------------------------------------------------------------------------
# Case 2 — subscription.deleted downgrades to free
# ---------------------------------------------------------------------------
def case_2():
    log("\n=== Case 2: Webhook subscription.deleted ===")

    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        log("  (No STRIPE_WEBHOOK_SECRET — testing rejection only)")
        payload = json.dumps({
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_test", "customer": "cus_test"}},
        })
        r = requests.post(f"{API}/api/billing/webhook",
                          data=payload,
                          headers={"Content-Type": "application/json", "Stripe-Signature": "t=0,v1=bad"})
        check("Downgrade webhook rejects bad sig", r.status_code == 400,
              f"Got {r.status_code}")
        return

    payload = json.dumps({
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_test_upgrade",
                "customer": "cus_test",
            }
        },
    })
    sig = _build_stripe_signature(payload, webhook_secret)
    r = requests.post(f"{API}/api/billing/webhook",
                      data=payload,
                      headers={"Content-Type": "application/json", "Stripe-Signature": sig})
    check("Downgrade webhook accepted (200)", r.status_code == 200, f"Got {r.status_code}")


# ---------------------------------------------------------------------------
# Case 3 — Invalid signature returns 400
# ---------------------------------------------------------------------------
def case_3():
    log("\n=== Case 3: Invalid webhook signature ===")

    payload = json.dumps({"type": "checkout.session.completed", "data": {"object": {}}})

    # Completely bogus signature
    r = requests.post(f"{API}/api/billing/webhook",
                      data=payload,
                      headers={"Content-Type": "application/json", "Stripe-Signature": "t=0,v1=deadbeef"})
    check("Bogus signature returns 400", r.status_code == 400,
          f"Got {r.status_code}")

    # Missing signature header
    r = requests.post(f"{API}/api/billing/webhook",
                      data=payload,
                      headers={"Content-Type": "application/json"})
    check("Missing signature returns 400", r.status_code == 400,
          f"Got {r.status_code}")

    # Empty body
    r = requests.post(f"{API}/api/billing/webhook",
                      data="",
                      headers={"Content-Type": "application/json", "Stripe-Signature": "t=0,v1=abc"})
    check("Empty body returns 400", r.status_code == 400,
          f"Got {r.status_code}")


# ---------------------------------------------------------------------------
# Case 4 — Checkout session creation
# ---------------------------------------------------------------------------
def case_4():
    log("\n=== Case 4: Checkout session creation ===")

    if not TOKEN:
        check("Auth token available", False, "No token — skipping")
        return

    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe_key:
        log("  (No STRIPE_SECRET_KEY — testing endpoint exists and auth required)")

        # Without auth → 401/403
        r = requests.post(f"{API}/api/billing/checkout", json={"plan": "professional"})
        check("Checkout requires auth", r.status_code in (401, 403, 422),
              f"Got {r.status_code}")

        # With auth but no Stripe key → should get an error, not crash
        r = requests.post(f"{API}/api/billing/checkout",
                          json={"plan": "professional"},
                          headers=auth_headers(TOKEN))
        check("Checkout with no Stripe key doesn't crash (not 500)",
              r.status_code != 500,
              f"Got {r.status_code}")
        return

    r = requests.post(f"{API}/api/billing/checkout",
                      json={"plan": "professional"},
                      headers=auth_headers(TOKEN))
    check("Checkout returns 200", r.status_code == 200, f"Got {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        check("Checkout returns URL", "url" in data and data["url"].startswith("https://"),
              f"Got: {data}")
        check("Checkout returns session_id", "session_id" in data)


# ---------------------------------------------------------------------------

def main():
    setup_auth()

    cases = {1: case_1, 2: case_2, 3: case_3, 4: case_4}

    selected = None
    if "--case" in sys.argv:
        idx = sys.argv.index("--case")
        if idx + 1 < len(sys.argv):
            selected = int(sys.argv[idx + 1])

    if selected:
        if selected in cases:
            cases[selected]()
        else:
            print(f"Unknown case {selected}. Available: {list(cases.keys())}")
            sys.exit(1)
    else:
        for fn in cases.values():
            fn()

    log(f"\n{'='*40}")
    log(f"PASS: {PASS}  FAIL: {FAIL}")
    log(f"{'='*40}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
