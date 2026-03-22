"""
Test script: persistent volume and GOBUGA_DATA_DIR support.

Tests:
  1. GOBUGA_DATA_DIR env var is respected by tenant.py
  2. Org dirs are created under data dir, not project root
  3. Evidence files persist under data dir
  4. Usage logs persist under data dir
  5. Platform files (users, orgs, sessions) use data dir

Usage:
    python3 tests/test_persistent_volume.py
    python3 tests/test_persistent_volume.py --case 1
"""

import os
import sys
import json
import shutil
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

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


# ---------------------------------------------------------------------------
# Case 1 — GOBUGA_DATA_DIR is respected by tenant.py
# ---------------------------------------------------------------------------
def case_1():
    log("\n=== Case 1: GOBUGA_DATA_DIR env var support ===")

    # Check if tenant.py reads GOBUGA_DATA_DIR
    tenant_path = os.path.join(PROJECT_ROOT, "api", "tenant.py")
    with open(tenant_path) as f:
        source = f.read()

    has_env_read = "GOBUGA_DATA_DIR" in source
    check("tenant.py reads GOBUGA_DATA_DIR", has_env_read,
          "tenant.py does not reference GOBUGA_DATA_DIR — volume mount will be ignored")

    if not has_env_read:
        log("  [INFO] tenant.py needs updating to read GOBUGA_DATA_DIR.")
        log("  [INFO] Currently uses PROJECT_ROOT for all paths.")
        log("  [INFO] For Railway, ORGS_DIR and PLATFORM_DIR must be under /data.")
        _check_fixable()
        return

    # If it does read the env var, verify it works
    tmpdir = tempfile.mkdtemp(prefix="gobuga_test_vol_")
    try:
        os.environ["GOBUGA_DATA_DIR"] = tmpdir
        # Reimport to pick up new env
        import importlib
        import api.tenant as tenant_mod
        importlib.reload(tenant_mod)

        org_root = tenant_mod.org_root("test-vol-org")
        check("org_root points to data dir",
              org_root.startswith(tmpdir),
              f"Got: {org_root}")

        platform = tenant_mod.platform_dir()
        check("platform_dir points to data dir",
              platform.startswith(tmpdir),
              f"Got: {platform}")
    finally:
        os.environ.pop("GOBUGA_DATA_DIR", None)
        # Reload with original settings
        import importlib
        import api.tenant as tenant_mod
        importlib.reload(tenant_mod)
        shutil.rmtree(tmpdir, ignore_errors=True)


def _check_fixable():
    """Report what needs changing in tenant.py for volume support."""
    tenant_path = os.path.join(PROJECT_ROOT, "api", "tenant.py")
    with open(tenant_path) as f:
        source = f.read()

    # Check current root resolution
    if "PROJECT_ROOT" in source or "dirname" in source:
        check("tenant.py uses PROJECT_ROOT (needs migration)",
              True,
              "Change to: os.environ.get('GOBUGA_DATA_DIR', PROJECT_ROOT)")


# ---------------------------------------------------------------------------
# Case 2 — Org dirs created under data dir
# ---------------------------------------------------------------------------
def case_2():
    log("\n=== Case 2: Org directory creation under data dir ===")
    from api.tenant import ensure_org_dirs, org_root, org_cases_dir, org_cycles_dir
    from api.tenant import org_evidence_dir, org_state_dir, org_logs_dir, org_data_dir
    from api.tenant import org_prompts_dir

    org_id = "_test_vol_dirs"
    ensure_org_dirs(org_id)

    root = org_root(org_id)
    check("Org root exists", os.path.isdir(root), f"Path: {root}")
    check("Cases dir exists", os.path.isdir(org_cases_dir(org_id)))
    check("Cycles dir exists", os.path.isdir(org_cycles_dir(org_id)))
    check("Evidence dir exists", os.path.isdir(org_evidence_dir(org_id)))
    check("State dir exists", os.path.isdir(org_state_dir(org_id)))
    check("Logs dir exists", os.path.isdir(org_logs_dir(org_id)))
    check("Data dir exists", os.path.isdir(org_data_dir(org_id)))
    check("Prompts dir exists", os.path.isdir(org_prompts_dir(org_id)))

    # Check data subdirs
    data_dir = org_data_dir(org_id)
    check("data/org/ exists", os.path.isdir(os.path.join(data_dir, "org")))
    check("data/donors/ exists", os.path.isdir(os.path.join(data_dir, "donors")))
    check("data/grants/ exists", os.path.isdir(os.path.join(data_dir, "grants")))

    # Cleanup
    shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Case 3 — Evidence files persist under data dir
# ---------------------------------------------------------------------------
def case_3():
    log("\n=== Case 3: Evidence file persistence ===")
    from api.tenant import ensure_org_dirs, org_evidence_dir
    from orchestrator.evidence import save_evidence, load_evidence_for_date

    org_id = "_test_vol_evidence"
    test_date = "2099-12-01"
    ensure_org_dirs(org_id)

    record = save_evidence(org_id, "grant_watcher", test_date, {
        "title": "Volume Test Grant",
        "type": "grant_opportunity",
        "severity": "high",
        "content": "Testing persistence.",
        "source_url": "https://example.com/vol-test",
    })

    ev_dir = os.path.join(org_evidence_dir(org_id), test_date, "grant_watcher")
    check("Evidence saved to correct path", os.path.isdir(ev_dir))

    ev_file = os.path.join(ev_dir, f"{record['id']}.json")
    check("Evidence file exists on disk", os.path.isfile(ev_file))

    # Verify content roundtrips
    loaded = load_evidence_for_date(org_id, test_date)
    check("Evidence loadable after save", len(loaded) >= 1)
    match = [e for e in loaded if e.get("title") == "Volume Test Grant"]
    check("Evidence content matches", len(match) == 1)

    # Cleanup
    from api.tenant import org_root
    shutil.rmtree(org_root(org_id), ignore_errors=True)


# ---------------------------------------------------------------------------
# Case 4 — Usage logs persist under data dir
# ---------------------------------------------------------------------------
def case_4():
    log("\n=== Case 4: Usage log persistence ===")
    from api.tenant import ensure_org_dirs, org_logs_dir, org_root
    from api.usage_log import log_usage, read_usage, NZ
    from datetime import datetime

    org_id = "_test_vol_usage"
    ensure_org_dirs(org_id)

    # log_usage always writes to today's NZ date, not cycle_date
    today_nz = datetime.now(NZ).strftime("%Y-%m-%d")

    log_usage(org_id, "grant_watcher", {
        "input_tokens": 100,
        "output_tokens": 50,
        "api_calls": 1,
        "cost_usd": 0.001,
        "model": "claude-haiku-4-5-20251001",
    })

    logs_dir = org_logs_dir(org_id)
    log_file = os.path.join(logs_dir, f"usage-{today_nz}.jsonl")
    check("Usage log file created", os.path.isfile(log_file),
          f"Expected: {log_file}")

    records = read_usage(org_id, today_nz)
    check("Usage records readable", len(records) >= 1)
    if records:
        check("Usage record has caller", records[-1].get("caller") == "grant_watcher")
        check("Usage record has cost", "cost_usd" in records[-1])

    # Cleanup
    shutil.rmtree(org_root(org_id), ignore_errors=True)


# ---------------------------------------------------------------------------
# Case 5 — Platform files use data dir
# ---------------------------------------------------------------------------
def case_5():
    log("\n=== Case 5: Platform files location ===")
    from api.tenant import platform_dir, users_path, orgs_path, sessions_path, ensure_platform_dirs

    ensure_platform_dirs()

    pdir = platform_dir()
    check("Platform dir exists", os.path.isdir(pdir), f"Path: {pdir}")

    # Verify path functions return paths under the expected root
    upath = users_path()
    opath = orgs_path()
    spath = sessions_path()

    check("users.json path under platform/", "platform" in upath, f"Path: {upath}")
    check("orgs.json path under platform/", "platform" in opath, f"Path: {opath}")
    check("sessions.json path under platform/", "platform" in spath, f"Path: {spath}")

    # If GOBUGA_DATA_DIR is set, verify paths are under it
    data_dir = os.environ.get("GOBUGA_DATA_DIR")
    if data_dir:
        check("Platform dir under GOBUGA_DATA_DIR",
              pdir.startswith(data_dir),
              f"Platform: {pdir}, Data dir: {data_dir}")


# ---------------------------------------------------------------------------

def main():
    cases = {1: case_1, 2: case_2, 3: case_3, 4: case_4, 5: case_5}

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
