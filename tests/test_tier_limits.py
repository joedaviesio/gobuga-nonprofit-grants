"""
Test script: tier system limits and cycle timer.

Tests:
  1. Scanner tier enforces opportunity limits (2H + 2M + 1L)
  2. Scanner tier enforces 3 open case limit
  3. Scanner tier enforces 5 chat messages/case
  4. Officer tier has no limits
  5. Cycle timer enforces 7-day cooldown
  6. Feature access gating (bots_bcd, export_docx)

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
    log("\n=== Case 1: Scanner tier opportunity limits ===")
    from api.limits import filter_opportunities_for_tier, TIERS

    scanner = TIERS["scanner"]
    limits = scanner["opportunities_per_cycle"]
    check("Scanner allows 2 high", limits.get("high") == 2)
    check("Scanner allows 2 medium", limits.get("medium") == 2)
    check("Scanner allows 1 low", limits.get("low") == 1)

    # Build a list of 10 opportunities: 5 high, 3 medium, 2 low
    opps = []
    for i in range(5):
        opps.append({"id": f"opp-h{i}", "priority": "high", "title": f"High {i}"})
    for i in range(3):
        opps.append({"id": f"opp-m{i}", "priority": "medium", "title": f"Med {i}"})
    for i in range(2):
        opps.append({"id": f"opp-l{i}", "priority": "low", "title": f"Low {i}"})

    # Use a fake org_id that resolves to scanner tier
    org_id = "_test_tier_scanner"
    # Ensure it's treated as scanner (no org file = defaults to scanner)
    filtered = filter_opportunities_for_tier(opps, org_id)

    high_count = sum(1 for o in filtered if o["priority"] == "high")
    med_count = sum(1 for o in filtered if o["priority"] == "medium")
    low_count = sum(1 for o in filtered if o["priority"] == "low")

    check("Filtered high <= 2", high_count <= 2, f"Got {high_count}")
    check("Filtered medium <= 2", med_count <= 2, f"Got {med_count}")
    check("Filtered low <= 1", low_count <= 1, f"Got {low_count}")
    check("Total filtered == 5", len(filtered) == 5, f"Got {len(filtered)}")

    # --- Backfill: when a priority bucket is short, extras fill the gap ---
    # Only 1 high available, 0 low — should still return 5 total
    sparse_opps = [
        {"id": "opp-h0", "priority": "high", "title": "High 0"},
        {"id": "opp-m0", "priority": "medium", "title": "Med 0"},
        {"id": "opp-m1", "priority": "medium", "title": "Med 1"},
        {"id": "opp-m2", "priority": "medium", "title": "Med 2"},
        {"id": "opp-m3", "priority": "medium", "title": "Med 3"},
        {"id": "opp-m4", "priority": "medium", "title": "Med 4"},
    ]
    sparse_filtered = filter_opportunities_for_tier(sparse_opps, org_id)
    check("Backfill: still returns 5 when buckets are short",
          len(sparse_filtered) == 5, f"Got {len(sparse_filtered)}")
    sparse_high = sum(1 for o in sparse_filtered if o["priority"] == "high")
    sparse_med = sum(1 for o in sparse_filtered if o["priority"] == "medium")
    check("Backfill: takes the 1 available high", sparse_high == 1, f"Got {sparse_high}")
    check("Backfill: fills remaining slots with medium", sparse_med == 4, f"Got {sparse_med}")

    # Fewer than 5 total available — returns all of them
    tiny_opps = [
        {"id": "opp-m0", "priority": "medium", "title": "Med 0"},
        {"id": "opp-m1", "priority": "medium", "title": "Med 1"},
    ]
    tiny_filtered = filter_opportunities_for_tier(tiny_opps, org_id)
    check("Undersupply: returns all when < 5 available",
          len(tiny_filtered) == 2, f"Got {len(tiny_filtered)}")


# ---------------------------------------------------------------------------
# Case 2 — Scanner tier case limit
# ---------------------------------------------------------------------------
def case_2():
    log("\n=== Case 2: Scanner tier case limit ===")

    if not TOKEN:
        check("Auth available", False, "No token")
        return

    set_tier("scanner")

    # Create three cases — all should succeed (scanner limit=3)
    case_ids = []
    for i in range(1, 4):
        r = requests.post(f"{API}/api/cases",
                          json={"grant_id": f"test-grant-{i}", "grant_brief": {"title": f"Test brief {i}"}},
                          headers=json_headers(TOKEN))
        check(f"Case {i} created (scanner)", r.status_code == 200,
              f"Got {r.status_code}: {r.text[:200]}")
        if r.status_code == 200:
            case_ids.append(r.json().get("case_id"))

    # Create fourth case — should be blocked
    r4 = requests.post(f"{API}/api/cases",
                       json={"grant_id": "test-grant-4", "grant_brief": {"title": "Test brief 4"}},
                       headers=json_headers(TOKEN))
    check("Fourth case blocked (scanner limit=3)", r4.status_code in (403, 429),
          f"Got {r4.status_code}: {r4.text[:200]}")

    # Clean up: delete the cases
    for case_id in case_ids:
        if case_id:
            requests.delete(f"{API}/api/cases/{case_id}", headers=auth_headers(TOKEN))


# ---------------------------------------------------------------------------
# Case 3 — Scanner tier chat limit
# ---------------------------------------------------------------------------
def case_3():
    log("\n=== Case 3: Scanner tier chat message limit ===")
    from api.limits import TIERS

    scanner = TIERS["scanner"]
    check("Scanner chat limit is 5", scanner["chat_messages_per_case"] == 5)

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
    check("Chat limit check returns allowed field", "allowed" in result)
    check("Chat limit check returns limit field", result.get("limit") == 5,
          f"Got limit={result.get('limit')}")

    # Clean up
    if case_id:
        requests.delete(f"{API}/api/cases/{case_id}", headers=auth_headers(TOKEN))


# ---------------------------------------------------------------------------
# Case 4 — Officer tier has no limits
# ---------------------------------------------------------------------------
def case_4():
    log("\n=== Case 4: Officer tier unlimited ===")
    from api.limits import TIERS, filter_opportunities_for_tier

    officer = TIERS["officer"]
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
    log("\n=== Case 6: Feature access gating ===")
    from api.limits import check_feature_access

    # Scanner should be blocked from officer features
    # Use a non-existent org_id to default to scanner
    org_id = "_test_features"

    bots_result = check_feature_access(org_id, "bots_bcd")
    check("Scanner blocked from bots_bcd", bots_result.get("allowed") is False,
          f"Got: {bots_result}")

    docx_result = check_feature_access(org_id, "export_docx")
    check("Scanner blocked from export_docx", docx_result.get("allowed") is False,
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
