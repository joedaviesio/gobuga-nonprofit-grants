"""
Test script: upload all 15 fixture files through both backend paths (multi-tenant).

Path 1 — File upload → data bank ingestion
Path 2 — Submission upload → Bot B parse (skips actual LLM call, just tests extraction)

Usage:
    python3 tests/test_uploads.py
    python3 tests/test_uploads.py --skip-parse
"""

import os
import sys
import json
import time
import requests

API = os.environ.get("API_URL", "http://localhost:8102")
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

TOKEN = None
TEST_EMAIL = f"test-{int(time.time())}@gobuga-test.org"
TEST_PASSWORD = "TestPass123!"
TEST_ORG = "Test Org Upload Suite"


def setup_auth():
    """Register a test org and login."""
    global TOKEN

    # Register
    r = requests.post(f"{API}/api/auth/register", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
        "org_name": TEST_ORG,
    })
    if r.ok:
        TOKEN = r.json().get("token")
        print(f"  Auth: registered {TEST_EMAIL}, got token")
        return
    elif r.status_code == 409:
        print(f"  Auth: user exists, logging in...")
    else:
        print(f"  Auth: register returned {r.status_code} {r.text[:100]}")

    # Login
    r = requests.post(f"{API}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
    })
    if r.ok:
        TOKEN = r.json().get("token")
        print(f"  Auth: logged in, got token")
    else:
        print(f"  Auth: login failed {r.status_code} — tests will fail")


def headers():
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def auth_headers():
    """Headers without Content-Type (for multipart uploads)."""
    h = {}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def create_test_case(label: str) -> str:
    """Create a fresh case for testing."""
    r = requests.post(
        f"{API}/api/cases",
        json={
            "grant_id": f"test-{label}",
            "grant_brief": {
                "title": f"Test Case: {label}",
                "funder": "Test Funder",
                "deadline": "2026-12-31",
                "source_cycle": "2026-03-19",
            },
        },
        headers=headers(),
    )
    if not r.ok:
        print(f"    FAIL create case: {r.status_code} {r.text[:200]}")
        return None
    case_id = r.json()["case_id"]
    return case_id


def test_file_upload(case_id: str, filepath: str, filename: str) -> dict:
    """Path 1: Upload file to data bank."""
    with open(filepath, "rb") as f:
        r = requests.post(
            f"{API}/api/cases/{case_id}/upload",
            files={"file": (filename, f)},
            data={"file_type": "reference"},
            headers=auth_headers(),
        )
    return {
        "path": "file_upload",
        "filename": filename,
        "status": r.status_code,
        "ok": r.ok,
        "sanitized_name": r.json().get("filename") if r.ok else None,
        "error": r.text[:200] if not r.ok else None,
    }


def test_ingest(case_id: str, sanitized_name: str) -> dict:
    """Ingest uploaded file into data bank."""
    r = requests.post(
        f"{API}/api/cases/{case_id}/bot/ingest",
        json={"filename": sanitized_name},
        headers=headers(),
    )
    return {
        "path": "ingest",
        "filename": sanitized_name,
        "status": r.status_code,
        "ok": r.ok,
        "entries_added": r.json().get("entries_added") if r.ok else None,
        "error": r.text[:200] if not r.ok else None,
    }


def test_submission_upload(case_id: str, filepath: str, filename: str) -> dict:
    """Path 2: Upload as submission form."""
    with open(filepath, "rb") as f:
        r = requests.post(
            f"{API}/api/cases/{case_id}/upload",
            files={"file": (filename, f)},
            data={"file_type": "application_form"},
            headers=auth_headers(),
        )
    return {
        "path": "submission_upload",
        "filename": filename,
        "status": r.status_code,
        "ok": r.ok,
        "sanitized_name": r.json().get("filename") if r.ok else None,
        "error": r.text[:200] if not r.ok else None,
    }


def test_parse(case_id: str, sanitized_name: str) -> dict:
    """Bot B: parse submission form (this calls the LLM)."""
    r = requests.post(
        f"{API}/api/cases/{case_id}/bot/parse",
        json={"filename": sanitized_name},
        headers=headers(),
        timeout=60,
    )
    return {
        "path": "parse",
        "filename": sanitized_name,
        "status": r.status_code,
        "ok": r.ok,
        "sections_found": r.json().get("sections_found") if r.ok else None,
        "error": r.text[:200] if not r.ok else None,
    }


def run_tests():
    print("=" * 70)
    print("UPLOAD TEST SUITE — 5 formats x 3 variants x 2 paths (multi-tenant)")
    print("=" * 70)

    setup_auth()

    fixtures = sorted(os.listdir(FIXTURES_DIR))
    if not fixtures:
        print("No fixtures found! Run: python3 tests/generate_fixtures.py")
        sys.exit(1)

    print(f"\nFound {len(fixtures)} fixtures:\n")
    for f in fixtures:
        ext = os.path.splitext(f)[1]
        size = os.path.getsize(os.path.join(FIXTURES_DIR, f))
        print(f"  {ext:6s}  {size:>6d}B  {f}")

    results = []
    skip_parse = "--skip-parse" in sys.argv

    # --- PATH 1: File upload → data bank ---
    print(f"\n{'=' * 70}")
    print("PATH 1: File Upload → Data Bank Ingestion")
    print("=" * 70)

    case_id = create_test_case("databank")
    if not case_id:
        print("FATAL: Could not create test case")
        sys.exit(1)
    print(f"  Test case: {case_id}\n")

    for filename in fixtures:
        filepath = os.path.join(FIXTURES_DIR, filename)
        ext = os.path.splitext(filename)[1]

        upload_result = test_file_upload(case_id, filepath, filename)
        status = "OK" if upload_result["ok"] else "FAIL"
        san = upload_result.get("sanitized_name", "")
        print(f"  [{status:4s}] Upload  {ext:5s}  {filename}")
        if san != filename:
            print(f"         Sanitized → {san}")
        results.append(upload_result)

        if not upload_result["ok"]:
            continue

        ingest_result = test_ingest(case_id, upload_result["sanitized_name"])
        status = "OK" if ingest_result["ok"] else "FAIL"
        entries = ingest_result.get("entries_added", "?")
        print(f"  [{status:4s}] Ingest  {ext:5s}  → {entries} entries")
        if not ingest_result["ok"]:
            print(f"         Error: {ingest_result['error']}")
        results.append(ingest_result)

    # --- PATH 2: Submission upload → Bot B parse ---
    print(f"\n{'=' * 70}")
    print("PATH 2: Submission Upload → Bot B Parse")
    if skip_parse:
        print("  (--skip-parse: skipping LLM calls, testing upload + extraction only)")
    print("=" * 70)

    case_id2 = create_test_case("submission")
    if not case_id2:
        print("FATAL: Could not create test case")
        sys.exit(1)
    print(f"  Test case: {case_id2}\n")

    for filename in fixtures:
        filepath = os.path.join(FIXTURES_DIR, filename)
        ext = os.path.splitext(filename)[1]

        upload_result = test_submission_upload(case_id2, filepath, filename)
        status = "OK" if upload_result["ok"] else "FAIL"
        san = upload_result.get("sanitized_name", "")
        print(f"  [{status:4s}] Upload  {ext:5s}  {filename}")
        if san != filename:
            print(f"         Sanitized → {san}")
        results.append(upload_result)

        if not upload_result["ok"] or skip_parse:
            continue

        print(f"         Parsing with Bot B...")
        parse_result = test_parse(case_id2, upload_result["sanitized_name"])
        status = "OK" if parse_result["ok"] else "FAIL"
        sections = parse_result.get("sections_found", "?")
        print(f"  [{status:4s}] Parse   {ext:5s}  → {sections} sections")
        if not parse_result["ok"]:
            print(f"         Error: {parse_result['error']}")
        results.append(parse_result)

    # --- SUMMARY ---
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)

    total = len(results)
    passed = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])

    print(f"  Total: {total}  Passed: {passed}  Failed: {failed}")

    if failed:
        print(f"\n  FAILURES:")
        for r in results:
            if not r["ok"]:
                print(f"    {r['path']:20s}  {r['filename']:40s}  {r['status']}  {r.get('error', '')[:80]}")

    print(f"\n  By format:")
    by_ext = {}
    for r in results:
        ext = os.path.splitext(r["filename"])[1]
        by_ext.setdefault(ext, {"ok": 0, "fail": 0})
        if r["ok"]:
            by_ext[ext]["ok"] += 1
        else:
            by_ext[ext]["fail"] += 1
    for ext in sorted(by_ext):
        counts = by_ext[ext]
        print(f"    {ext:6s}  {counts['ok']} passed  {counts['fail']} failed")

    print(f"\n{'=' * 70}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
