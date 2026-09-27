"""
Test script: tier system limits and cycle timer.

Since the 27 Sep 2026 plan the free tier (scanner) is unlimited; these cases
assert that, and that the officer tier is unchanged. In-process equivalents
live in tests/test_open_workspace.py.

Tests:
  1. Scanner tier has no opportunity cap
  2. Scanner tier opens more than 3 cases
  3. Scanner tier has unlimited chat messages per case
  4. Officer tier has no limits
  5. Cycle timer enforces 7-day cooldown
  6. Scanner tier has every feature (bots_bcd, export_docx)

Usage:
    python3 tests/test_tier_limits.py
    python3 tests/test_tier_limits.py --case 1
"""

import os
import sys
import time
import json
import requests

API = os.environ.get("API_URL", "http://localhost:8102")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

PASS = 0
FAIL = 0
TOKEN = None
ORG_ID = None
TEST_EMAIL = f"test-tier-{int(time.time())}@gobuga-test.org"
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


def json_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def setup_auth():
    """Register a test org."""
    global TOKEN, ORG_ID
    r = requests.post(f"{API}/api/auth/register", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
        "org_name": "Test Tier Org",
    })
    if r.status_code == 200:
        data = r.json()
        TOKEN = data.get("token")
        ORG_ID = data.get("org_id")
    else:
        r = requests.post(f"{API}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        data = r.json()
        TOKEN = data.get("token")
        ORG_ID = data.get("org_id")


def set_tier(tier_key):
    """Set tier via direct org update (toggle is one-way to starter now)."""
    from api.auth import update_org
    plan_key = "free" if tier_key == "scanner" else "starter"
    update_org(ORG_ID, {"plan": plan_key})


# ---------------------------------------------------------------------------
# Case 1 — Scanner tier opportunity limits
# ---------------------------------------------------------------------------
def case_1():
    log("\n=== Case 1: Scanner tier has no opportunity cap ===")
    from api.limits import filter_opportunities_for_tier, _get_tiers

    scanner = _get_tiers()["scanner"]
    check("Scanner has no opportunity cap", scanner["opportunities_per_cycle"] is None,
          f"Got {scanner['opportunities_per_cycle']}")

    # Build a list of 10 opportunities: 5 high, 3 medium, 2 low
    opps = []
    for i in range(5):
        opps.append({"id": f"opp-h{i}", "priority": "high", "title": f"High {i}"})
    for i in range(3):
        opps.append({"id": f"opp-m{i}", "priority": "medium", "title": f"Med {i}"})
    for i in range(2):
        opps.append({"id": f"opp-l{i}", "priority": "low", "title": f"Low {i}"})

    # A fake org_id resolves to scanner (no org record = scanner)
    filtered = filter_opportunities_for_tier(opps, "_test_tier_scanner")
    check("Scanner gets all 10 opportunities", filtered == opps, f"Got {len(filtered)}")


# ---------------------------------------------------------------------------
# Case 2 — Scanner tier case limit
# ---------------------------------------------------------------------------
def case_2():
    log("\n=== Case 2: Scanner tier opens more than 3 cases ===")

    if not TOKEN:
        check("Auth available", False, "No token")
        return

    set_tier("scanner")

    # Create five cases — all should succeed (the old scanner limit was 3)
    case_ids = []
    for i in range(1, 6):
        r = requests.post(f"{API}/api/cases",
                          json={"grant_id": f"test-grant-{i}", "grant_brief": {"title": f"Test brief {i}"}},
                          headers=json_headers(TOKEN))
        check(f"Case {i} created (scanner)", r.status_code == 200,
              f"Got {r.status_code}: {r.text[:200]}")
        if r.status_code == 200:
            case_ids.append(r.json().get("case_id"))

    # Clean up: delete the cases
    for case_id in case_ids:
        if case_id:
            requests.delete(f"{API}/api/cases/{case_id}", headers=auth_headers(TOKEN))


# ---------------------------------------------------------------------------
# Case 3 — Scanner tier chat limit
# ---------------------------------------------------------------------------
def case_3():
    log("\n=== Case 3: Scanner tier has unlimited chat ===")
    from api.limits import _get_tiers

    scanner = _get_tiers()["scanner"]
    check("Scanner chat is unlimited", scanner["chat_messages_per_case"] == -1)

    if not TOKEN:
        check("Auth available", False, "No token")
        return

    set_tier("scanner")

    # Create a case for chat testing
    r = requests.post(f"{API}/api/cases",
                      json={"grant_id": "test-chat-limit", "grant_brief": {"title": "Chat limit test"}},
                      headers=json_headers(TOKEN))
    if r.status_code != 200:
        check("Case created for chat test", False, f"Got {r.status_code}")
        return

    case_id = r.json().get("case_id")

    # Verify the limit check function
    from api.limits import check_chat_limit
    result = check_chat_limit(ORG_ID, case_id)
    check("Chat limit check allows", result.get("allowed") is True, f"Got {result}")
    check("Chat limit check reports no limit", result.get("limit") == -1,
          f"Got limit={result.get('limit')}")

    # Clean up
    if case_id:
        requests.delete(f"{API}/api/cases/{case_id}", headers=auth_headers(TOKEN))


# ---------------------------------------------------------------------------
# Case 4 — Officer tier has no limits
# ---------------------------------------------------------------------------
def case_4():
    log("\n=== Case 4: Officer tier unlimited ===")
    from api.limits import _get_tiers, filter_opportunities_for_tier

    officer = _get_tiers()["officer"]
    check("Officer has no opportunity limits", officer.get("opportunities_per_cycle") is None)
    check("Officer has no case limit", officer.get("max_open_cases") == -1)
    check("Officer has no chat limit", officer.get("chat_messages_per_case") == -1)
    check("Officer has export_docx", officer.get("export_docx") is True)
    check("Officer has bots_bcd", officer.get("bots_bcd") is True)

    # Officer tier should not filter opportunities
    opps = [{"id": f"opp-{i}", "priority": "high", "title": f"Grant {i}"} for i in range(20)]

    if not TOKEN:
        log("  (Skipping live officer filter test — no auth)")
        return

    set_tier("officer")

    filtered = filter_opportunities_for_tier(opps, ORG_ID)
    check("Officer tier returns all opportunities", len(filtered) == 20,
          f"Got {len(filtered)}")

    # Reset to scanner
    set_tier("scanner")


# ---------------------------------------------------------------------------
# Case 5 — Cycle timer 7-day cooldown
# ---------------------------------------------------------------------------
def case_5():
    log("\n=== Case 5: Cycle timer cooldown ===")
    from api.limits import trigger_cycle, can_trigger_cycle, get_cycle_timer

    if not ORG_ID:
        check("Auth available for timer test", False, "No ORG_ID")
        return

    # Clear any existing timer first
    from api.auth import update_org
    update_org(ORG_ID, {"cycle_triggered_at": None})

    # Trigger a cycle
    timer = trigger_cycle(ORG_ID)
    check("Trigger returns timer", timer is not None and "triggered_at" in timer)
    check("Timer has expires_at", "expires_at" in timer)
    check("Timer has remaining_seconds", "remaining_seconds" in timer)
    check("Timer not expired immediately", timer.get("expired") is False)

    # Check cooldown blocks re-trigger
    result = can_trigger_cycle(ORG_ID)
    check("Cannot re-trigger within cooldown", result.get("allowed") is False,
          f"Got: {result}")
    check("Cooldown message present", "message" in result and len(result["message"]) > 0)

    # Verify timer duration is ~7 days
    remaining = timer.get("remaining_seconds", 0)
    seven_days = 7 * 24 * 3600
    check("Timer is approximately 7 days",
          abs(remaining - seven_days) < 60,
          f"Got {remaining}s, expected ~{seven_days}s")

    # Clean up timer
    update_org(ORG_ID, {"cycle_triggered_at": None})


# ---------------------------------------------------------------------------
# Case 6 — Feature access gating
# ---------------------------------------------------------------------------
def case_6():
    log("\n=== Case 6: Feature access ===")
    from api.limits import check_feature_access

    # Scanner has every feature now
    # Use a non-existent org_id to default to scanner
    org_id = "_test_features"

    bots_result = check_feature_access(org_id, "bots_bcd")
    check("Scanner allowed bots_bcd", bots_result.get("allowed") is True,
          f"Got: {bots_result}")

    docx_result = check_feature_access(org_id, "export_docx")
    check("Scanner allowed export_docx", docx_result.get("allowed") is True,
          f"Got: {docx_result}")

    # Test with a live officer org if we have auth
    if TOKEN and ORG_ID:
        set_tier("officer")
        bots_result = check_feature_access(ORG_ID, "bots_bcd")
        check("Officer allowed bots_bcd", bots_result.get("allowed") is True,
              f"Got: {bots_result}")
        docx_result = check_feature_access(ORG_ID, "export_docx")
        check("Officer allowed export_docx", docx_result.get("allowed") is True,
              f"Got: {docx_result}")
        set_tier("scanner")


# ---------------------------------------------------------------------------

def main():
    setup_auth()

    cases = {1: case_1, 2: case_2, 3: case_3, 4: case_4, 5: case_5, 6: case_6}

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
