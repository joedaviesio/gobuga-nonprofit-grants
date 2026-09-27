"""
Test script: use cases for the signup + seeding flow.

Sign-up is register, then one optional screen (/seed), then the feed. The
screen posts whatever the person filled in (sectors, regions, website — all
optional) to POST /api/org/setup, uploads any documents, then calls
POST /api/org/seeding-complete. In-process equivalents live in
tests/test_open_workspace.py.

Use cases:
  1. Full flow — register, fill the screen, upload all 5 doc types, complete
  2. Skip everything — register, submit the screen empty, land on the feed
  3. Partial uploads — register, fill the screen, upload 2 of 5 docs, then continue
  4. Replace upload — upload a doc, then replace it with a different file
  5. Existing org backward compat — login as pre-existing org, verify seeding_complete defaults True
  6. Bad doc type rejected
  7. Partial screen — only sectors filled in; name from registration is kept

Usage:
    python3 tests/test_signup_seeding.py
    python3 tests/test_signup_seeding.py --case 1      # run single case
"""

import os
import sys
import time
import json
import requests

API = os.environ.get("API_URL", "http://localhost:8102")
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "seed_fixtures")

# Map doc types to fixture files
DOC_TYPE_FILES = {
    "annual-reports": "annual-report-2025.pdf",
    "mission-statements": "mission-statement.docx",
    "organisational-reviews": "organisational-review-2025.txt",
    "previous-applications": "previous-application-lottery-2024.docx",
    "financial-statements": "financial-statement-fy2025.xlsx",
}

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
    """Register a new user and return (token, org_id, email). The website
    moved from the register form to the seeding screen, so none is sent."""
    ts = int(time.time() * 1000)
    email = f"test-seed-{ts}{suffix}@gobuga-test.org"
    r = requests.post(f"{API}/api/auth/register", json={
        "email": email,
        "password": "TestPass123!",
        "org_name": f"Test Org {ts}",
    })
    if not r.ok:
        log(f"  Register failed: {r.status_code} {r.text[:200]}")
        return None, None, None
    data = r.json()
    return data["token"], data["org_id"], email


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def json_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def verify(token):
    r = requests.post(f"{API}/api/auth/verify", headers=auth_headers(token))
    if not r.ok:
        return None
    return r.json()


SCREEN_FILLED = {
    "sectors": ["Education & research", "Youth & children"],
    "geographies": ["New Zealand"],
    "website": "https://example.com",
}


def do_setup(token, payload=None):
    """Submit the one sign-up screen. Every field is optional; `{}` is a skip.
    Note: a website makes the backend fetch it for the org's data."""
    r = requests.post(f"{API}/api/org/setup",
                      json=SCREEN_FILLED if payload is None else payload,
                      headers=json_headers(token))
    return r


def upload_doc(token, doc_type, fixture_filename):
    """Upload a fixture file as org document."""
    filepath = os.path.join(FIXTURES_DIR, fixture_filename)
    if not os.path.exists(filepath):
        return None
    with open(filepath, "rb") as f:
        r = requests.post(
            f"{API}/api/org/upload",
            files={"file": (fixture_filename, f)},
            data={"doc_type": doc_type},
            headers=auth_headers(token),
        )
    return r


def list_uploads(token):
    r = requests.get(f"{API}/api/org/uploads", headers=auth_headers(token))
    if not r.ok:
        return []
    return r.json()


def complete_seeding(token):
    r = requests.post(f"{API}/api/org/seeding-complete",
                       headers=json_headers(token))
    return r


# ─────────────────────────────────────────────────────────────────
# USE CASE 1: Full flow
# ─────────────────────────────────────────────────────────────────

def case_1_full_flow():
    log("\n" + "=" * 70)
    log("USE CASE 1: Full flow — register, fill the screen, upload all 5 docs, complete")
    log("=" * 70)

    # 1. Register (no website on the register form any more)
    token, org_id, email = register_user("-c1")
    check("Register returns token", token is not None)
    if not token:
        return

    # 2. Verify session — setup_complete=False, seeding_complete=False
    session = verify(token)
    check("Verify: setup_complete is False", session and not session["setup_complete"])
    check("Verify: seeding_complete is False", session and not session["seeding_complete"])

    # 3. Submit the screen with sectors, region and website
    r = do_setup(token)
    check("Setup completes successfully", r.ok, r.text[:200] if not r.ok else "")

    # 4. Verify now shows setup_complete=True, seeding_complete=False
    session = verify(token)
    check("Post-setup: setup_complete is True", session and session["setup_complete"])
    check("Post-setup: seeding_complete is False", session and not session["seeding_complete"])

    # 5. Check website scrape was created
    org_data_path = os.path.join("orgs", org_id, "data", "org", "website-scrape.md")
    check("Website scrape file exists", os.path.exists(org_data_path),
          f"Expected at {org_data_path}")

    # 6. Upload all 5 doc types
    for doc_type, fixture_file in DOC_TYPE_FILES.items():
        r = upload_doc(token, doc_type, fixture_file)
        check(f"Upload {doc_type}", r is not None and r.ok,
              r.text[:200] if r and not r.ok else "")

    # 7. List uploads — should have 5 files (+ website scrape)
    uploads = list_uploads(token)
    check("Uploads list has >= 5 files", len(uploads) >= 5, f"Got {len(uploads)}")

    # 8. Complete seeding
    r = complete_seeding(token)
    check("Complete seeding OK", r.ok)

    # 9. Verify seeding_complete=True
    session = verify(token)
    check("Final: seeding_complete is True", session and session["seeding_complete"])


# ─────────────────────────────────────────────────────────────────
# USE CASE 2: Skip seeding
# ─────────────────────────────────────────────────────────────────

def case_2_skip_seeding():
    log("\n" + "=" * 70)
    log("USE CASE 2: Skip everything — register, submit the screen empty, feed")
    log("=" * 70)

    token, org_id, _ = register_user("-c2")
    check("Register OK", token is not None)
    if not token:
        return

    # Skip: nothing filled in, nothing uploaded
    r = do_setup(token, {})
    check("Empty setup submission OK", r.ok, r.text[:200] if not r.ok else "")

    uploads = list_uploads(token)
    check("No uploads after skip", uploads == [], f"Got {uploads}")

    r = complete_seeding(token)
    check("Complete seeding (skip) OK", r.ok)

    session = verify(token)
    check("setup_complete is True after skip", session and session["setup_complete"])
    check("seeding_complete is True after skip", session and session["seeding_complete"])

    # The feed works unranked
    r = requests.get(f"{API}/api/opportunities", headers=auth_headers(token))
    check("Feed loads after skip", r.ok, f"Got {r.status_code}")


# ─────────────────────────────────────────────────────────────────
# USE CASE 3: Partial uploads (2 of 5)
# ─────────────────────────────────────────────────────────────────

def case_3_partial_uploads():
    log("\n" + "=" * 70)
    log("USE CASE 3: Partial uploads — upload 2 of 5 doc types")
    log("=" * 70)

    token, org_id, _ = register_user("-c3")
    check("Register OK", token is not None)
    if not token:
        return

    r = do_setup(token)
    check("Setup OK", r.ok)

    # Upload only annual report and mission statement
    partial = ["annual-reports", "mission-statements"]
    for dt in partial:
        r = upload_doc(token, dt, DOC_TYPE_FILES[dt])
        check(f"Upload {dt}", r is not None and r.ok)

    uploads = list_uploads(token)
    user_uploads = [u for u in uploads if any(u["filename"].startswith(dt + "_") for dt in partial)]
    check("Exactly 2 user uploads present", len(user_uploads) == 2, f"Got {len(user_uploads)}")

    r = complete_seeding(token)
    check("Complete seeding with partial uploads OK", r.ok)

    session = verify(token)
    check("seeding_complete is True", session and session["seeding_complete"])


# ─────────────────────────────────────────────────────────────────
# USE CASE 4: Replace upload
# ─────────────────────────────────────────────────────────────────

def case_4_replace_upload():
    log("\n" + "=" * 70)
    log("USE CASE 4: Replace upload — upload, then re-upload same doc type")
    log("=" * 70)

    token, org_id, _ = register_user("-c4")
    check("Register OK", token is not None)
    if not token:
        return

    r = do_setup(token)
    check("Setup OK", r.ok)

    # Upload annual report first time
    r = upload_doc(token, "annual-reports", "annual-report-2025.pdf")
    check("First upload OK", r is not None and r.ok)
    first_filename = r.json()["filename"] if r.ok else None
    first_size = r.json()["size"] if r.ok else 0

    # Upload a different file as same doc type (use the financial statement as replacement)
    r = upload_doc(token, "annual-reports", "financial-statement-fy2025.xlsx")
    check("Replacement upload OK", r is not None and r.ok)
    second_filename = r.json()["filename"] if r.ok else None
    second_size = r.json()["size"] if r.ok else 0

    check("Replacement has different filename", first_filename != second_filename,
          f"{first_filename} vs {second_filename}")
    check("Replacement has different size", first_size != second_size,
          f"{first_size} vs {second_size}")

    # Both should appear in uploads list (server doesn't auto-delete old)
    uploads = list_uploads(token)
    annual_uploads = [u for u in uploads if u["filename"].startswith("annual-reports_")]
    check("Both uploads present in listing", len(annual_uploads) >= 2,
          f"Got {len(annual_uploads)}")

    r = complete_seeding(token)
    check("Complete seeding OK", r.ok)


# ─────────────────────────────────────────────────────────────────
# USE CASE 5: Existing org backward compatibility
# ─────────────────────────────────────────────────────────────────

def case_5_existing_org_compat():
    log("\n" + "=" * 70)
    log("USE CASE 5: Existing org backward compat — seeding_complete defaults True")
    log("=" * 70)

    # Simulate a pre-existing org by registering, doing setup, then manually
    # removing the seeding_complete field from the org record
    token, org_id, email = register_user("-c5")
    check("Register OK", token is not None)
    if not token:
        return

    r = do_setup(token)
    check("Setup OK", r.ok)

    # Manually remove seeding_complete from org record to simulate old org
    orgs_file = os.path.join("platform", "orgs.json")
    if os.path.exists(orgs_file):
        with open(orgs_file) as f:
            orgs = json.load(f)
        if org_id in orgs and "seeding_complete" in orgs[org_id]:
            del orgs[org_id]["seeding_complete"]
            with open(orgs_file, "w") as f:
                json.dump(orgs, f, indent=2)
            log("  (Removed seeding_complete from org record to simulate legacy org)")

    # Verify — should default to True for old orgs
    session = verify(token)
    check("Legacy org: seeding_complete defaults to True",
          session and session.get("seeding_complete") is True,
          f"Got: {session.get('seeding_complete') if session else 'no session'}")

    # Verify user can access dashboard (no redirect to /seed)
    check("Legacy org: setup_complete is True", session and session["setup_complete"])


# ─────────────────────────────────────────────────────────────────
# Validation: bad doc type rejected
# ─────────────────────────────────────────────────────────────────

def case_bonus_bad_doc_type():
    log("\n" + "=" * 70)
    log("BONUS: Invalid doc_type is rejected")
    log("=" * 70)

    token, org_id, _ = register_user("-bonus")
    check("Register OK", token is not None)
    if not token:
        return

    r = do_setup(token)
    check("Setup OK", r.ok)

    # Try uploading with invalid doc type
    filepath = os.path.join(FIXTURES_DIR, "annual-report-2025.pdf")
    if os.path.exists(filepath):
        with open(filepath, "rb") as f:
            r = requests.post(
                f"{API}/api/org/upload",
                files={"file": ("test.pdf", f)},
                data={"doc_type": "not-a-real-type"},
                headers=auth_headers(token),
            )
        check("Invalid doc_type returns 400", r.status_code == 400,
              f"Got {r.status_code}")
    else:
        log("  (Skipped — fixture not found)")


# ─────────────────────────────────────────────────────────────────
# USE CASE 7: Partial screen
# ─────────────────────────────────────────────────────────────────

def case_7_partial_screen():
    log("\n" + "=" * 70)
    log("USE CASE 7: Partial screen — only sectors filled in")
    log("=" * 70)

    token, org_id, _ = register_user("-c7")
    check("Register OK", token is not None)
    if not token:
        return
    org_name = verify(token)["org_name"]

    r = do_setup(token, {"sectors": ["Sport & recreation"]})
    check("Partial setup submission OK", r.ok, r.text[:200] if not r.ok else "")

    r = requests.get(f"{API}/api/org/profile", headers=auth_headers(token))
    profile = r.json() if r.ok else {}
    check("Sectors saved", profile.get("sectors") == ["Sport & recreation"], f"Got {profile.get('sectors')}")
    check("Name from registration kept", profile.get("name") == org_name, f"Got {profile.get('name')}")


# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────

ALL_CASES = {
    1: ("Full flow", case_1_full_flow),
    2: ("Skip seeding", case_2_skip_seeding),
    3: ("Partial uploads", case_3_partial_uploads),
    4: ("Replace upload", case_4_replace_upload),
    5: ("Existing org compat", case_5_existing_org_compat),
    6: ("Bad doc type rejected", case_bonus_bad_doc_type),
    7: ("Partial screen", case_7_partial_screen),
}


def main():
    global PASS, FAIL

    # Check fixtures exist
    if not os.path.isdir(FIXTURES_DIR) or not os.listdir(FIXTURES_DIR):
        print(f"No seed fixtures found at {FIXTURES_DIR}")
        print("Run: python3 tests/generate_seed_fixtures.py")
        sys.exit(1)

    print("=" * 70)
    print("SIGNUP + SEEDING TEST SUITE")
    print(f"API: {API}")
    print(f"Fixtures: {FIXTURES_DIR} ({len(os.listdir(FIXTURES_DIR))} files)")
    print("=" * 70)

    # Parse --case N
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

    # Summary
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
