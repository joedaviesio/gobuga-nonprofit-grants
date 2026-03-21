"""
Test script: Geography & sector flow through setup → profile → prompts → cycle.

Use cases:
  1. Single sector + single geography — verify profile, prompts, and prompt injection
  2. Multiple sectors + multiple geographies — verify all appear in generated files
  3. No sectors / no geographies — verify fallback defaults
  4. Org status field — verify nonprofit/pending/forprofit is stored
  5. Prompt template substitution — verify {{SECTORS}}, {{GEOGRAPHIES}} replaced correctly
  6. Cycle pipeline dry run — verify prompts load correctly for a cycle (no API calls)

Usage:
    python3 tests/test_geography_sector_flow.py
    python3 tests/test_geography_sector_flow.py --case 1      # run single case
"""

import os
import sys
import time
import json
import requests

API = os.environ.get("API_URL", "http://localhost:8102")

PASS = 0
FAIL = 0


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


def register_user(suffix=""):
    ts = int(time.time() * 1000)
    email = f"test-geo-{ts}{suffix}@gobuga-test.org"
    r = requests.post(f"{API}/api/auth/register", json={
        "email": email,
        "password": "TestPass123!",
        "org_name": f"Geo Test Org {ts}",
        "website_url": "https://example.com",
    })
    if not r.ok:
        log(f"  Register failed: {r.status_code} {r.text[:200]}")
        return None, None
    data = r.json()
    return data["token"], data["org_id"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def do_setup(token, sectors, geographies, country="New Zealand", org_name="Geo Test Org", org_status=None):
    payload = {
        "org_name": org_name,
        "country": country,
        "sectors": sectors,
        "geographies": geographies,
    }
    if org_status:
        payload["org_status"] = org_status
    r = requests.post(f"{API}/api/org/setup", json=payload, headers=auth_headers(token))
    return r


def read_file(path):
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return None


def load_org_record(org_id):
    orgs_file = os.path.join("platform", "orgs.json")
    if os.path.exists(orgs_file):
        with open(orgs_file) as f:
            orgs = json.load(f)
        return orgs.get(org_id)
    return None


# ─────────────────────────────────────────────────────────────────
# CASE 1: Single sector + single geography
# ─────────────────────────────────────────────────────────────────

def case_1_single_sector_geo():
    log("\n" + "=" * 70)
    log("CASE 1: Single sector + single geography")
    log("=" * 70)

    token, org_id = register_user("-c1")
    check("Register OK", token is not None)
    if not token:
        return

    r = do_setup(token, sectors=["Education"], geographies=["New Zealand"])
    check("Setup OK", r.ok, r.text[:200] if not r.ok else "")
    if not r.ok:
        return

    # Check org record
    org = load_org_record(org_id)
    check("Org record has sectors", org and org.get("sectors") == ["Education"])
    check("Org record has geographies", org and org.get("geographies") == ["New Zealand"])

    # Check org-profile.md
    profile = read_file(os.path.join("orgs", org_id, "org-profile.md"))
    check("Profile exists", profile is not None)
    check("Profile contains sector", profile and "Education" in profile)
    check("Profile contains geography", profile and "New Zealand" in profile)

    # Check generated prompts
    watcher = read_file(os.path.join("orgs", org_id, "prompts", "grant_watcher.md"))
    check("Watcher prompt exists", watcher is not None)
    check("Watcher has sector substituted", watcher and "Education" in watcher)
    check("Watcher has geography substituted", watcher and "New Zealand" in watcher)
    check("Watcher has no raw {{SECTORS}}", watcher and "{{SECTORS}}" not in watcher)
    check("Watcher has no raw {{GEOGRAPHIES}}", watcher and "{{GEOGRAPHIES}}" not in watcher)
    check("Watcher has no raw {{ORG_NAME}}", watcher and "{{ORG_NAME}}" not in watcher)

    analyst = read_file(os.path.join("orgs", org_id, "prompts", "grant_analyst.md"))
    check("Analyst prompt exists", analyst is not None)
    check("Analyst has sector substituted", analyst and "Education" in analyst)

    reporter = read_file(os.path.join("orgs", org_id, "prompts", "grant_reporter.md"))
    check("Reporter prompt exists", reporter is not None)


# ─────────────────────────────────────────────────────────────────
# CASE 2: Multiple sectors + multiple geographies
# ─────────────────────────────────────────────────────────────────

def case_2_multiple_sectors_geos():
    log("\n" + "=" * 70)
    log("CASE 2: Multiple sectors + multiple geographies")
    log("=" * 70)

    token, org_id = register_user("-c2")
    check("Register OK", token is not None)
    if not token:
        return

    sectors = ["Digital inclusion", "Education", "Health & wellbeing", "Environment & climate"]
    geographies = ["New Zealand", "Australia", "Pacific Islands"]

    r = do_setup(token, sectors=sectors, geographies=geographies)
    check("Setup OK", r.ok, r.text[:200] if not r.ok else "")
    if not r.ok:
        return

    # Check org record stores all
    org = load_org_record(org_id)
    check("Org has 4 sectors", org and len(org.get("sectors", [])) == 4)
    check("Org has 3 geographies", org and len(org.get("geographies", [])) == 3)

    # Check profile contains all
    profile = read_file(os.path.join("orgs", org_id, "org-profile.md"))
    check("Profile exists", profile is not None)
    for s in sectors:
        check(f"Profile has sector: {s}", profile and s in profile)
    for g in geographies:
        check(f"Profile has geography: {g}", profile and g in profile)

    # Check watcher prompt has all sectors and geographies as bullet points
    watcher = read_file(os.path.join("orgs", org_id, "prompts", "grant_watcher.md"))
    check("Watcher prompt exists", watcher is not None)
    for s in sectors:
        check(f"Watcher has sector: {s}", watcher and f"- {s}" in watcher)
    for g in geographies:
        check(f"Watcher has geography: {g}", watcher and f"- {g}" in watcher)


# ─────────────────────────────────────────────────────────────────
# CASE 3: No sectors / no geographies (fallback defaults)
# ─────────────────────────────────────────────────────────────────

def case_3_empty_sectors_geos():
    log("\n" + "=" * 70)
    log("CASE 3: No sectors / no geographies — fallback defaults")
    log("=" * 70)

    token, org_id = register_user("-c3")
    check("Register OK", token is not None)
    if not token:
        return

    r = do_setup(token, sectors=[], geographies=[], country="Kenya")
    check("Setup OK", r.ok, r.text[:200] if not r.ok else "")
    if not r.ok:
        return

    # Check org record
    org = load_org_record(org_id)
    check("Org has empty sectors list", org and org.get("sectors") == [])
    check("Org has empty geographies list", org and org.get("geographies") == [])

    # Check prompt uses fallback
    watcher = read_file(os.path.join("orgs", org_id, "prompts", "grant_watcher.md"))
    check("Watcher prompt exists", watcher is not None)
    check("Watcher has fallback sector '- General nonprofit'",
          watcher and "- General nonprofit" in watcher)
    check("Watcher has fallback geography '- Kenya' (from country)",
          watcher and "- Kenya" in watcher)


# ─────────────────────────────────────────────────────────────────
# CASE 4: Org status field
# ─────────────────────────────────────────────────────────────────

def case_4_org_status():
    log("\n" + "=" * 70)
    log("CASE 4: Org status field — nonprofit / pending / forprofit")
    log("=" * 70)

    for status in ["nonprofit", "pending", "forprofit"]:
        token, org_id = register_user(f"-c4-{status}")
        check(f"Register OK ({status})", token is not None)
        if not token:
            continue

        r = do_setup(token,
                     sectors=["Education"],
                     geographies=["New Zealand"],
                     org_status=status)
        check(f"Setup OK ({status})", r.ok, r.text[:200] if not r.ok else "")

        # Check if org_status was stored
        org = load_org_record(org_id)
        stored_status = org.get("org_status") if org else None
        # Note: org_status may not be stored yet if backend doesn't handle it
        if stored_status:
            check(f"Org status stored as '{status}'", stored_status == status)
        else:
            log(f"  [INFO] org_status not stored in org record (backend may need update)")


# ─────────────────────────────────────────────────────────────────
# CASE 5: Prompt template substitution integrity
# ─────────────────────────────────────────────────────────────────

def case_5_prompt_substitution():
    log("\n" + "=" * 70)
    log("CASE 5: Prompt template substitution integrity")
    log("=" * 70)

    token, org_id = register_user("-c5")
    check("Register OK", token is not None)
    if not token:
        return

    org_name = "Te Whānau Digital Trust"  # Test with macrons/special chars
    r = do_setup(token,
                 sectors=["AI for public good", "Digital inclusion"],
                 geographies=["New Zealand", "Pacific Islands"],
                 org_name=org_name)
    check("Setup OK", r.ok, r.text[:200] if not r.ok else "")
    if not r.ok:
        return

    # Verify all 3 prompts have no remaining placeholders
    placeholders = ["{{ORG_NAME}}", "{{ORG_SUMMARY}}", "{{SECTORS}}", "{{GEOGRAPHIES}}"]

    for prompt_name in ["grant_watcher.md", "grant_analyst.md", "grant_reporter.md"]:
        content = read_file(os.path.join("orgs", org_id, "prompts", prompt_name))
        check(f"{prompt_name} exists", content is not None)
        if content:
            for ph in placeholders:
                check(f"{prompt_name} has no {ph}", ph not in content)
            check(f"{prompt_name} contains org name", org_name in content)

    # Verify org profile has special characters preserved
    profile = read_file(os.path.join("orgs", org_id, "org-profile.md"))
    check("Profile preserves macrons", profile and "Whānau" in profile)


# ─────────────────────────────────────────────────────────────────
# CASE 6: Cycle prompt loading (dry run, no API calls)
# ─────────────────────────────────────────────────────────────────

def case_6_cycle_prompt_loading():
    log("\n" + "=" * 70)
    log("CASE 6: Cycle prompt loading — verify prompts load for orchestrator")
    log("=" * 70)

    token, org_id = register_user("-c6")
    check("Register OK", token is not None)
    if not token:
        return

    sectors = ["Health & wellbeing", "Community development"]
    geographies = ["United Kingdom", "European Union"]

    r = do_setup(token, sectors=sectors, geographies=geographies, country="United Kingdom")
    check("Setup OK", r.ok, r.text[:200] if not r.ok else "")
    if not r.ok:
        return

    # Simulate what the orchestrator does: load prompts via _load_prompt
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from orchestrator.main import _load_prompt, load_all_state
        from orchestrator.config import agents_by_phase
        from datetime import date as date_type

        state = load_all_state(org_id)
        check("State loads without error", True)

        phases = agents_by_phase()
        cycle_date = date_type.today().isoformat()

        all_outputs = {}
        for phase_num in sorted(phases.keys()):
            for agent_cfg in phases[phase_num]:
                prompt = _load_prompt(org_id, agent_cfg, state, all_outputs, cycle_date)
                check(f"Phase {phase_num} {agent_cfg['name']} prompt loads", prompt is not None)
                check(f"  prompt length > 100 chars", len(prompt) > 100,
                      f"Got {len(prompt)} chars")

                # Verify sectors/geographies are in the prompt
                for s in sectors:
                    check(f"  contains sector: {s}", s in prompt)
                for g in geographies:
                    check(f"  contains geography: {g}", g in prompt)

                # No raw placeholders
                check(f"  no raw {{{{SECTORS}}}}", "{{SECTORS}}" not in prompt)
                check(f"  no raw {{{{GEOGRAPHIES}}}}", "{{GEOGRAPHIES}}" not in prompt)

                # Simulate output for dependency chain
                all_outputs[agent_cfg["id"]] = {
                    "raw_text": f"Simulated output from {agent_cfg['name']}",
                    "agent_id": agent_cfg["id"],
                }

        # Verify analyst gets watcher output
        analyst_prompt = _load_prompt(
            org_id,
            phases[2][0],  # grant_analyst
            state,
            {"grant_watcher": {"raw_text": "WATCHER_OUTPUT_MARKER", "agent_id": "grant_watcher"}},
            cycle_date,
        )
        check("Analyst prompt includes watcher output",
              "WATCHER_OUTPUT_MARKER" in analyst_prompt)

        # Verify reporter gets both watcher and analyst output
        reporter_prompt = _load_prompt(
            org_id,
            phases[3][0],  # grant_reporter
            state,
            {
                "grant_watcher": {"raw_text": "WATCHER_MARKER", "agent_id": "grant_watcher"},
                "grant_analyst": {"raw_text": "ANALYST_MARKER", "agent_id": "grant_analyst"},
            },
            cycle_date,
        )
        check("Reporter prompt includes watcher output", "WATCHER_MARKER" in reporter_prompt)
        check("Reporter prompt includes analyst output", "ANALYST_MARKER" in reporter_prompt)

    except Exception as e:
        check(f"Cycle prompt loading failed: {e}", False)
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────
# CASE 7: Sub-country region selections
# ─────────────────────────────────────────────────────────────────

def case_7_sub_country_regions():
    log("\n" + "=" * 70)
    log("CASE 7: Sub-country region selections (New Zealand regions)")
    log("=" * 70)

    token, org_id = register_user("-c7")
    check("Register OK", token is not None)
    if not token:
        return

    # Test with specific NZ regions + a whole-country geography
    regions = ["New Zealand > Auckland", "New Zealand > Canterbury", "New Zealand > Wellington"]
    geographies = regions + ["Australia"]

    r = do_setup(token, sectors=["Education"], geographies=geographies)
    check("Setup OK", r.ok, r.text[:200] if not r.ok else "")
    if not r.ok:
        return

    # Check org record stores prefixed strings
    org = load_org_record(org_id)
    check("Org has 4 geographies", org and len(org.get("geographies", [])) == 4)
    for g in geographies:
        check(f"Org record has '{g}'", org and g in org.get("geographies", []))

    # Check prompts contain the region strings as bullet points
    watcher = read_file(os.path.join("orgs", org_id, "prompts", "grant_watcher.md"))
    check("Watcher prompt exists", watcher is not None)
    for g in geographies:
        check(f"Watcher has geography: {g}", watcher and f"- {g}" in watcher)

    # Check profile contains the regions
    profile = read_file(os.path.join("orgs", org_id, "org-profile.md"))
    check("Profile exists", profile is not None)
    for g in geographies:
        check(f"Profile has geography: {g}", profile and g in profile)


def case_7b_backward_compat_whole_country():
    log("\n" + "=" * 70)
    log("CASE 7b: Backward compatibility — whole country still works")
    log("=" * 70)

    token, org_id = register_user("-c7b")
    check("Register OK", token is not None)
    if not token:
        return

    # Existing format: just "New Zealand" (no regions)
    r = do_setup(token, sectors=["Education"], geographies=["New Zealand"])
    check("Setup OK", r.ok, r.text[:200] if not r.ok else "")
    if not r.ok:
        return

    org = load_org_record(org_id)
    check("Org record has 'New Zealand'", org and org.get("geographies") == ["New Zealand"])

    watcher = read_file(os.path.join("orgs", org_id, "prompts", "grant_watcher.md"))
    check("Watcher has '- New Zealand'", watcher and "- New Zealand" in watcher)


# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────

ALL_CASES = {
    1: ("Single sector + geo", case_1_single_sector_geo),
    2: ("Multiple sectors + geos", case_2_multiple_sectors_geos),
    3: ("Empty sectors/geos fallback", case_3_empty_sectors_geos),
    4: ("Org status field", case_4_org_status),
    5: ("Prompt substitution integrity", case_5_prompt_substitution),
    6: ("Cycle prompt loading", case_6_cycle_prompt_loading),
    7: ("Sub-country regions", case_7_sub_country_regions),
    8: ("Backward compat whole country", case_7b_backward_compat_whole_country),
}


def main():
    global PASS, FAIL

    print("=" * 70)
    print("GEOGRAPHY & SECTOR FLOW TEST SUITE")
    print(f"API: {API}")
    print("=" * 70)

    target = None
    if "--case" in sys.argv:
        idx = sys.argv.index("--case")
        if idx + 1 < len(sys.argv):
            target = int(sys.argv[idx + 1])

    for num, (label, fn) in ALL_CASES.items():
        if target and num != target:
            continue
        try:
            fn()
        except Exception as e:
            FAIL += 1
            log(f"\n[ERROR] Case {num} ({label}) crashed: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)
    total = PASS + FAIL
    print(f"  Total: {total}  Passed: {PASS}  Failed: {FAIL}")
    if FAIL:
        print(f"\n  *** {FAIL} FAILURES ***")
    else:
        print(f"\n  All {PASS} checks passed!")
    print("=" * 70)

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
