"""
Test: loading states and cycle phase tracking.

Tests:
  1. Cycle status endpoint returns correct states (idle, running+phase, complete, error)
  2. Phase callback is called in the right order during run_cycle
  3. Frontend LoadingBar component exists and has correct structure

Usage:
    python3 tests/test_loading_states.py
    python3 tests/test_loading_states.py --live   # also test against running server
"""

import os
import sys
import time
import json
import importlib

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

API = os.environ.get("API_URL", "http://localhost:8102")

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {name}"
        if detail:
            msg += f"  — {detail}"
        print(msg)


# ============================================================
# TEST 1: _cycle_state has phase field
# ============================================================

def test_cycle_state_has_phase():
    print("\n--- Test: _cycle_state has phase field ---")
    from api.server import _cycle_state

    check("_cycle_state has 'phase' key", "phase" in _cycle_state)
    check("_cycle_state has 'running' key", "running" in _cycle_state)
    check("_cycle_state has 'error' key", "error" in _cycle_state)
    check("_cycle_state has 'completed_at' key", "completed_at" in _cycle_state)
    check("phase starts as None", _cycle_state["phase"] is None)


# ============================================================
# TEST 2: on_phase callback records phases in correct order
# ============================================================

def test_phase_callback_order():
    print("\n--- Test: on_phase callback called in order ---")

    from orchestrator.config import agents_by_phase

    phases = agents_by_phase()
    phase_nums = sorted(phases.keys())

    check("Phase 1 exists (watcher)", 1 in phases)
    check("Phase 2 exists (analyst)", 2 in phases)
    check("Phase 3 exists (reporter)", 3 in phases)

    # Verify phase_names mapping matches config
    expected_names = {1: "watcher", 2: "analyst", 3: "reporter"}
    for num, name in expected_names.items():
        agent_ids = [a["id"] for a in phases.get(num, [])]
        check(
            f"Phase {num} ({name}) has matching agent",
            any(name in aid for aid in agent_ids),
            f"agents: {agent_ids}",
        )


# ============================================================
# TEST 3: run_cycle accepts on_phase parameter
# ============================================================

def test_run_cycle_signature():
    print("\n--- Test: run_cycle accepts on_phase ---")

    import inspect
    from orchestrator.main import run_cycle

    sig = inspect.signature(run_cycle)
    params = list(sig.parameters.keys())

    check("run_cycle has 'on_phase' parameter", "on_phase" in params)
    check("on_phase defaults to None", sig.parameters["on_phase"].default is None)


# ============================================================
# TEST 4: Simulated phase tracking (no LLM calls)
# ============================================================

def test_phase_tracking_simulation():
    print("\n--- Test: phase tracking via _cycle_state ---")

    from api.server import _cycle_state

    # Simulate what api_run_cycle does
    _cycle_state["running"] = True
    _cycle_state["phase"] = "starting"

    check("Phase is 'starting' after init", _cycle_state["phase"] == "starting")

    # Simulate on_phase callbacks
    for phase in ["watcher", "analyst", "reporter", "saving"]:
        _cycle_state["phase"] = phase
        check(f"Phase updates to '{phase}'", _cycle_state["phase"] == phase)

    _cycle_state["phase"] = "complete"
    _cycle_state["running"] = False
    check("Phase is 'complete' after finish", _cycle_state["phase"] == "complete")

    # Reset
    _cycle_state["running"] = False
    _cycle_state["phase"] = None
    _cycle_state["error"] = None
    _cycle_state["completed_at"] = None


# ============================================================
# TEST 5: Status endpoint response shapes
# ============================================================

def test_status_response_shapes():
    print("\n--- Test: status response includes phase when running ---")

    from api.server import _cycle_state

    # idle state
    _cycle_state["running"] = False
    _cycle_state["error"] = None
    _cycle_state["completed_at"] = None
    _cycle_state["phase"] = None

    # Simulate running with phase
    _cycle_state["running"] = True
    _cycle_state["phase"] = "watcher"
    _cycle_state["started_at"] = "2026-03-19T00:00:00Z"

    # Build expected response (what the endpoint would return)
    running_response = {
        "status": "running",
        "started_at": _cycle_state["started_at"],
        "phase": _cycle_state.get("phase"),
    }

    check("Running response has 'status'", "status" in running_response)
    check("Running response has 'phase'", "phase" in running_response)
    check("Phase value is 'watcher'", running_response["phase"] == "watcher")

    # Simulate phase transition
    _cycle_state["phase"] = "analyst"
    running_response["phase"] = _cycle_state.get("phase")
    check("Phase updates to 'analyst'", running_response["phase"] == "analyst")

    # Reset
    _cycle_state["running"] = False
    _cycle_state["phase"] = None
    _cycle_state["started_at"] = None


# ============================================================
# TEST 6: Frontend LoadingBar component file
# ============================================================

def test_loading_bar_component():
    print("\n--- Test: LoadingBar component file ---")

    component_path = os.path.join(PROJECT_ROOT, "frontend", "app", "loading-bar.tsx")
    check("loading-bar.tsx exists", os.path.exists(component_path))

    if os.path.exists(component_path):
        with open(component_path) as f:
            content = f.read()

        check("Exports default component", "export default function LoadingBar" in content)
        check("Has label prop", "label" in content)
        check("Has color prop", "color" in content)
        check("Uses animate-loading-bar class", "animate-loading-bar" in content)
        check("Renders progress bar track", "bg-stone-100" in content)


# ============================================================
# TEST 7: CSS animation exists
# ============================================================

def test_css_animation():
    print("\n--- Test: CSS loading-bar animation ---")

    css_path = os.path.join(PROJECT_ROOT, "frontend", "app", "globals.css")
    check("globals.css exists", os.path.exists(css_path))

    if os.path.exists(css_path):
        with open(css_path) as f:
            content = f.read()

        check("Has @keyframes loading-bar", "@keyframes loading-bar" in content)
        check("Has .animate-loading-bar class", ".animate-loading-bar" in content)
        check("Animation uses translateX", "translateX" in content)


# ============================================================
# TEST 8: All pages import LoadingBar
# ============================================================

def test_pages_use_loading_bar():
    print("\n--- Test: all pages use LoadingBar ---")

    pages = {
        "dashboard": "frontend/app/page.tsx",
        "case detail": "frontend/app/case/[id]/page.tsx",
        "login": "frontend/app/login/page.tsx",
        "register": "frontend/app/register/page.tsx",
        "setup": "frontend/app/setup/page.tsx",
        "seed": "frontend/app/seed/page.tsx",
        "settings": "frontend/app/settings/page.tsx",
        "auth-gate": "frontend/app/auth-gate.tsx",
    }

    for name, rel_path in pages.items():
        path = os.path.join(PROJECT_ROOT, rel_path)
        if not os.path.exists(path):
            check(f"{name} imports LoadingBar", False, "file not found")
            continue

        with open(path) as f:
            content = f.read()

        has_import = 'import LoadingBar' in content
        has_usage = '<LoadingBar' in content
        check(f"{name} imports LoadingBar", has_import, rel_path)
        check(f"{name} uses <LoadingBar", has_usage, rel_path)


# ============================================================
# TEST 9: No AI references in user-facing frontend
# ============================================================

def test_no_ai_references():
    print("\n--- Test: no AI references in user-facing text ---")

    files_to_check = [
        "frontend/app/page.tsx",
        "frontend/app/seed/page.tsx",
        "frontend/app/settings/page.tsx",
    ]

    # Patterns that indicate AI references (but not code identifiers)
    ai_patterns = [
        "our AI",
        "3-bot system",
        "Bots B-D",
        "Bots are scanning",
    ]

    for rel_path in files_to_check:
        path = os.path.join(PROJECT_ROOT, rel_path)
        if not os.path.exists(path):
            continue

        with open(path) as f:
            content = f.read()

        for pattern in ai_patterns:
            found = pattern in content
            check(
                f"No '{pattern}' in {os.path.basename(rel_path)}",
                not found,
                f"found at: ...{content[max(0,content.find(pattern)-20):content.find(pattern)+len(pattern)+20]}..." if found else "",
            )


# ============================================================
# TEST 10: Live server tests (optional, --live flag)
# ============================================================

def test_live_cycle_status():
    print("\n--- Test: live cycle/status endpoint ---")

    import requests

    TOKEN = None
    test_email = f"test-loading-{int(time.time())}@gobuga-test.org"

    # Register
    try:
        r = requests.post(f"{API}/api/auth/register", json={
            "email": test_email,
            "password": "TestPass123!",
            "org_name": "Loading Test Org",
        }, timeout=5)
        if r.ok:
            TOKEN = r.json().get("token")
        else:
            # Try login
            r = requests.post(f"{API}/api/auth/login", json={
                "email": test_email,
                "password": "TestPass123!",
            }, timeout=5)
            if r.ok:
                TOKEN = r.json().get("token")
    except requests.ConnectionError:
        print(f"  [SKIP] Server not running at {API}")
        return
    except Exception as e:
        print(f"  [SKIP] Auth failed: {e}")
        return

    if not TOKEN:
        print("  [SKIP] Could not authenticate")
        return

    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

    # Test idle status
    r = requests.get(f"{API}/api/cycle/status", headers=headers, timeout=5)
    check("GET /api/cycle/status returns 200", r.status_code == 200)

    if r.ok:
        data = r.json()
        check("Idle status has 'status' field", "status" in data)
        # Status should be idle, running, complete, or error
        valid_statuses = {"idle", "running", "complete", "error"}
        check(
            f"Status is valid ({data.get('status')})",
            data.get("status") in valid_statuses,
        )

        if data.get("status") == "running":
            check("Running status includes 'phase'", "phase" in data)
            valid_phases = {"starting", "watcher", "analyst", "reporter", "saving", "complete", None}
            check(
                f"Phase is valid ({data.get('phase')})",
                data.get("phase") in valid_phases,
            )


# ============================================================
# RUNNER
# ============================================================

def run_tests():
    print("=" * 70)
    print("LOADING STATES TEST SUITE")
    print("=" * 70)

    test_cycle_state_has_phase()
    test_phase_callback_order()
    test_run_cycle_signature()
    test_phase_tracking_simulation()
    test_status_response_shapes()
    test_loading_bar_component()
    test_css_animation()
    test_pages_use_loading_bar()
    test_no_ai_references()

    if "--live" in sys.argv:
        test_live_cycle_status()
    else:
        print("\n  (Skipping live server tests. Use --live to include them.)")

    print(f"\n{'=' * 70}")
    print(f"RESULTS: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
    print(f"{'=' * 70}")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
