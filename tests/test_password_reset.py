"""
Test script: Password reset flow (end-to-end).

Tests against the Railway production deployment by default.
Since we can't intercept emails in production, cases 3-5 use
direct API calls to the backend to mint and consume tokens.

Cases:
  1. Forgot-password endpoint returns ok for known email
  2. Forgot-password endpoint returns ok for unknown email (no enumeration)
  3. Reset-password with valid token changes the password
  4. Reset-password with expired token is rejected
  5. Reset-password with already-used token is rejected
  6. Reset-password with short password is rejected (<8 chars)
  7. Reset-password with bogus token is rejected
  8. New reset request invalidates old token
  9. Login works with the new password after reset

Usage:
    API_URL=https://gobuga-nonprofit-grants-production.up.railway.app python3 tests/test_password_reset.py
    python3 tests/test_password_reset.py                          # defaults to localhost:8102
    python3 tests/test_password_reset.py --case 3
"""

import os
import sys
import time
import uuid
import requests

API = os.environ.get("API_URL", "http://localhost:8102")

PASS = 0
FAIL = 0

# Unique test user per run to avoid collisions
_RUN_ID = uuid.uuid4().hex[:8]
TEST_EMAIL = f"resettest-{_RUN_ID}@test.gobuga.org"
TEST_PASSWORD = "Original_Pass_123"
NEW_PASSWORD = "NewReset_Pass_456"


def log(msg, indent=0):
    print("  " * indent + msg)


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        log(f"[PASS] {label}")
    else:
        FAIL += 1
        log(f"[FAIL] {label}" + (f"  -- {detail}" if detail else ""))
    return condition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def register_test_user():
    """Register a disposable user and return the auth token."""
    r = requests.post(f"{API}/api/auth/register", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
        "org_name": f"Reset Test Org {_RUN_ID}",
    })
    if r.status_code == 200:
        return r.json().get("token")
    # Already exists — try logging in
    r = requests.post(f"{API}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
    })
    if r.status_code == 200:
        return r.json().get("token")
    return None


def mint_reset_token_via_api():
    """
    Request a password reset and retrieve the token.
    1. Try the test endpoint (requires ALLOW_TEST_ENDPOINTS=1 on server)
    2. Fall back to reading the local store file
    """
    # Trigger the reset email
    requests.post(f"{API}/api/auth/forgot-password", json={"email": TEST_EMAIL})

    # Try the test endpoint
    r = requests.get(f"{API}/api/test/latest-reset-token", params={"email": TEST_EMAIL})
    if r.status_code == 200:
        return r.json().get("token")

    # Fallback: read the store file directly (local dev only)
    store_paths = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "platform", "password_resets.json"),
        "/data/password_resets.json",  # Railway persistent volume
    ]
    import json
    for path in store_paths:
        if os.path.exists(path):
            with open(path) as f:
                resets = json.load(f)
            for token, record in sorted(resets.items(), key=lambda x: x[1].get("created", ""), reverse=True):
                if not record.get("used"):
                    return token

    return None


def inject_expired_token():
    """Write a fake expired token directly into the store (local only).
    Returns None when running against a remote API."""
    import json
    from datetime import datetime, timezone

    fake_token = f"expired-test-{_RUN_ID}"
    record = {
        "user_id": get_test_user_id(),
        "expires_at": time.time() - 600,  # expired 10 minutes ago
        "used": False,
        "created": datetime.now(timezone.utc).isoformat(),
    }

    store_paths = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "platform", "password_resets.json"),
        "/data/password_resets.json",
    ]
    for path in store_paths:
        if os.path.exists(path):
            with open(path) as f:
                resets = json.load(f)
            resets[fake_token] = record
            with open(path, "w") as f:
                json.dump(resets, f)
            return fake_token
    return None


def get_test_user_id():
    """Resolve the user_id for our test email from the users store."""
    import json
    store_paths = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "platform", "users.json"),
        "/data/users.json",
    ]
    for path in store_paths:
        if os.path.exists(path):
            with open(path) as f:
                users = json.load(f)
            for uid, u in users.items():
                if u.get("email", "").lower() == TEST_EMAIL.lower():
                    return uid
    return None


# ---------------------------------------------------------------------------
# Case 1 -- Forgot-password returns ok for known email
# ---------------------------------------------------------------------------
def case_1():
    log("\n=== Case 1: Forgot-password — known email ===")

    r = requests.post(f"{API}/api/auth/forgot-password", json={"email": TEST_EMAIL})
    check("Returns 200", r.status_code == 200, f"Got {r.status_code}")
    data = r.json()
    check("Body has ok=true", data.get("ok") is True, f"Got: {data}")


# ---------------------------------------------------------------------------
# Case 2 -- Forgot-password returns ok for unknown email (no enumeration)
# ---------------------------------------------------------------------------
def case_2():
    log("\n=== Case 2: Forgot-password — unknown email (anti-enumeration) ===")

    fake_email = f"nonexistent-{_RUN_ID}@example.com"
    r = requests.post(f"{API}/api/auth/forgot-password", json={"email": fake_email})
    check("Returns 200", r.status_code == 200, f"Got {r.status_code}")
    data = r.json()
    check("Body has ok=true (no leak)", data.get("ok") is True, f"Got: {data}")


# ---------------------------------------------------------------------------
# Case 3 -- Valid token resets the password
# ---------------------------------------------------------------------------
def case_3():
    log("\n=== Case 3: Reset password with valid token ===")

    token = mint_reset_token_via_api()
    if not check("Got a reset token", token is not None, "Could not retrieve token — skipping"):
        return

    r = requests.post(f"{API}/api/auth/reset-password", json={
        "token": token,
        "password": NEW_PASSWORD,
    })
    check("Returns 200", r.status_code == 200, f"Got {r.status_code}: {r.text}")
    data = r.json()
    check("Body has ok=true", data.get("ok") is True, f"Got: {data}")


# ---------------------------------------------------------------------------
# Case 4 -- Expired token is rejected
# ---------------------------------------------------------------------------
def case_4():
    log("\n=== Case 4: Expired token rejected ===")

    token = inject_expired_token()
    if not check("Injected expired token", token is not None, "Store not accessible — skipping"):
        return

    r = requests.post(f"{API}/api/auth/reset-password", json={
        "token": token,
        "password": "SomeValid_123",
    })
    check("Returns 400", r.status_code == 400, f"Got {r.status_code}")
    check("Error mentions expired", "expired" in r.text.lower(), f"Body: {r.text}")


# ---------------------------------------------------------------------------
# Case 5 -- Already-used token is rejected
# ---------------------------------------------------------------------------
def case_5():
    log("\n=== Case 5: Used token rejected ===")

    # Mint a fresh token
    requests.post(f"{API}/api/auth/forgot-password", json={"email": TEST_EMAIL})
    token = mint_reset_token_via_api()
    if not check("Got a reset token", token is not None, "Could not retrieve token — skipping"):
        return

    # Use it once
    r1 = requests.post(f"{API}/api/auth/reset-password", json={
        "token": token,
        "password": NEW_PASSWORD,
    })
    check("First use returns 200", r1.status_code == 200, f"Got {r1.status_code}")

    # Try to reuse it
    r2 = requests.post(f"{API}/api/auth/reset-password", json={
        "token": token,
        "password": "AnotherPass_789",
    })
    check("Reuse returns 400", r2.status_code == 400, f"Got {r2.status_code}")
    # Note: _load_resets() prunes used tokens, so the server returns
    # "Invalid or expired" rather than "already been used" on reuse.
    check("Error rejects reuse", "invalid" in r2.text.lower() or "used" in r2.text.lower(),
          f"Body: {r2.text}")


# ---------------------------------------------------------------------------
# Case 6 -- Short password rejected
# ---------------------------------------------------------------------------
def case_6():
    log("\n=== Case 6: Short password rejected ===")

    # Mint token
    requests.post(f"{API}/api/auth/forgot-password", json={"email": TEST_EMAIL})
    token = mint_reset_token_via_api()
    if not check("Got a reset token", token is not None, "Could not retrieve token — skipping"):
        return

    r = requests.post(f"{API}/api/auth/reset-password", json={
        "token": token,
        "password": "short",
    })
    check("Returns 400", r.status_code == 400, f"Got {r.status_code}")
    check("Error mentions 8 characters", "8" in r.text or "character" in r.text.lower(),
          f"Body: {r.text}")


# ---------------------------------------------------------------------------
# Case 7 -- Bogus token rejected
# ---------------------------------------------------------------------------
def case_7():
    log("\n=== Case 7: Bogus token rejected ===")

    r = requests.post(f"{API}/api/auth/reset-password", json={
        "token": "this-token-does-not-exist-at-all",
        "password": "ValidPass_123",
    })
    check("Returns 400", r.status_code == 400, f"Got {r.status_code}")
    check("Error mentions invalid or expired", "invalid" in r.text.lower() or "expired" in r.text.lower(),
          f"Body: {r.text}")


# ---------------------------------------------------------------------------
# Case 8 -- New reset request invalidates old token
# ---------------------------------------------------------------------------
def case_8():
    log("\n=== Case 8: New reset request kills old token ===")

    # Get first token
    requests.post(f"{API}/api/auth/forgot-password", json={"email": TEST_EMAIL})
    old_token = mint_reset_token_via_api()
    if not check("Got first token", old_token is not None, "Could not retrieve token — skipping"):
        return

    # Request a second reset — should invalidate the first
    requests.post(f"{API}/api/auth/forgot-password", json={"email": TEST_EMAIL})
    new_token = mint_reset_token_via_api()
    if not check("Got second token", new_token is not None, "Could not retrieve token — skipping"):
        return
    check("Tokens are different", old_token != new_token,
          f"Old and new token are the same: {old_token}")

    # Old token should no longer work
    r = requests.post(f"{API}/api/auth/reset-password", json={
        "token": old_token,
        "password": "OldToken_Pass_123",
    })
    check("Old token rejected", r.status_code == 400, f"Got {r.status_code} — old token should be dead")

    # New token should work
    r = requests.post(f"{API}/api/auth/reset-password", json={
        "token": new_token,
        "password": NEW_PASSWORD,
    })
    check("New token accepted", r.status_code == 200, f"Got {r.status_code}")


# ---------------------------------------------------------------------------
# Case 9 -- Login works with new password after reset
# ---------------------------------------------------------------------------
def case_9():
    log("\n=== Case 9: Login with new password after reset ===")

    # Mint and use a reset token
    requests.post(f"{API}/api/auth/forgot-password", json={"email": TEST_EMAIL})
    token = mint_reset_token_via_api()
    if not check("Got a reset token", token is not None, "Could not retrieve token — skipping"):
        return

    final_password = f"FinalPass_{_RUN_ID}"
    r = requests.post(f"{API}/api/auth/reset-password", json={
        "token": token,
        "password": final_password,
    })
    if not check("Reset succeeded", r.status_code == 200, f"Got {r.status_code}"):
        return

    # Old password should fail
    r = requests.post(f"{API}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
    })
    check("Old password rejected", r.status_code in (400, 401, 403),
          f"Got {r.status_code} — old password should not work")

    # New password should work
    r = requests.post(f"{API}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": final_password,
    })
    check("New password accepted", r.status_code == 200, f"Got {r.status_code}")
    data = r.json()
    check("Login returns token", "token" in data, f"Got: {data}")


# ---------------------------------------------------------------------------

def main():
    log(f"Target API: {API}")
    log(f"Test email: {TEST_EMAIL}")

    # Setup: register a test user
    auth_token = register_test_user()
    if not auth_token:
        log("[FATAL] Could not register or login test user — aborting")
        sys.exit(1)
    log(f"[SETUP] Test user ready\n")

    cases = {
        1: case_1, 2: case_2, 3: case_3, 4: case_4,
        5: case_5, 6: case_6, 7: case_7, 8: case_8, 9: case_9,
    }

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
