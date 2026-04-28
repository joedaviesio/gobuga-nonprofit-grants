"""Unit tests for the anon-feed redaction projection.

Pure-Python — no server, no network. Run with:

    python3 tests/test_redact_for_anon.py
"""

import sys
from datetime import datetime, timedelta, timezone

# Make the project root importable when run as a script.
sys.path.insert(0, ".")

from api.opportunities import (  # noqa: E402
    _band_amount,
    _bucket_deadline,
    _days_ago,
    _redact_funder,
    _truncate_summary,
    redact_for_anon,
)


PASS = 0
FAIL = 0


def check(label: str, got, expected):
    global PASS, FAIL
    if got == expected:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}")
        print(f"      expected: {expected!r}")
        print(f"      got:      {got!r}")


def truthy(label: str, got):
    global PASS, FAIL
    if got:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} (got falsy: {got!r})")


# --- Funder redaction ---

print("\nFunder redaction")
check("initial-em-dash style", _redact_funder("Foundation North"), "F——n N——h")
check("short word kept intact", _redact_funder("AA Foundation"), "AA F——n")
check("None falls back", _redact_funder(None), "Anonymous funder")
check("empty falls back", _redact_funder(""), "Anonymous funder")
check("single char falls back", _redact_funder("Z"), "Anonymous funder")
check("two char falls back", _redact_funder("AB"), "Anonymous funder")


# --- Amount banding ---

print("\nAmount banding")
check("None+None → None", _band_amount(None, None), None)
check("under 10k", _band_amount(5_000, 8_000), "Under $10k")
check("10k–50k", _band_amount(10_000, 50_000), "$10k–$50k")
check("50k–250k", _band_amount(50_000, 250_000), "$50k–$250k")
check("250k–1m", _band_amount(250_000, 999_999), "$250k–$1m")
check("1m+", _band_amount(None, 5_000_000), "$1m+")
check("falls back to amount_min when max missing", _band_amount(15_000, None), "$10k–$50k")


# --- Deadline bucketing ---

print("\nDeadline bucketing")
check("None → TBC", _bucket_deadline(None), "TBC")
check("'TBC' → TBC", _bucket_deadline("TBC"), "TBC")
check("'rolling' → Rolling", _bucket_deadline("rolling"), "Rolling")
check("garbage → TBC", _bucket_deadline("not-a-date"), "TBC")

today = datetime.now(timezone.utc).date()
check(
    "past date → Closed",
    _bucket_deadline((today - timedelta(days=2)).isoformat(), today=today),
    "Closed",
)
check(
    "3 days out → Closes this week",
    _bucket_deadline((today + timedelta(days=3)).isoformat(), today=today),
    "Closes this week",
)
check(
    "20 days out → Closes this month",
    _bucket_deadline((today + timedelta(days=20)).isoformat(), today=today),
    "Closes this month",
)
check(
    "90 days out → 30+ days",
    _bucket_deadline((today + timedelta(days=90)).isoformat(), today=today),
    "30+ days",
)


# --- Helpers ---

print("\nHelpers")
check("days_ago(now) == 0", _days_ago(datetime.now(timezone.utc).isoformat()), 0)
check(
    "days_ago(3d ago) == 3",
    _days_ago((datetime.now(timezone.utc) - timedelta(days=3)).isoformat()),
    3,
)
check("days_ago(None) is None", _days_ago(None), None)
check("days_ago(garbage) is None", _days_ago("garbage"), None)

check("truncate short text unchanged", _truncate_summary("short"), "short")
truncated = _truncate_summary("a" * 200, length=50)
check("truncate ends with ellipsis", truncated.endswith("…"), True)
check("truncate length = 50 + ellipsis", len(truncated), 51)


# --- Full projection ---

print("\nFull projection")

_FULL_ROW = {
    "id": "OPP-NZ-2026-04-0001",
    "country": "nz",
    "title": "Climate Action Grant",
    "funder": "Foundation North",
    "deadline": (today + timedelta(days=10)).isoformat(),
    "amount_min": 25_000,
    "amount_max": 75_000,
    "currency": "NZD",
    "region": ["auckland"],
    "tags": ["climate", "community-development"],
    "eligibility": "Charitable trusts and incorporated societies operating in Auckland.",
    "summary": "A grant for grassroots climate action in Tāmaki Makaurau.",
    "source_url": "https://foundationnorth.org.nz/climate-action-grant",
    "evidence_ids": ["ev_1", "ev_2"],
    "first_seen": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(),
    "last_seen": datetime.now(timezone.utc).isoformat(),
    "dedupe_key": "foundation-north|climate-action-grant|...",
}

out = redact_for_anon(_FULL_ROW)

expected_keys = {
    "id", "title", "funder_redacted", "country", "region", "tags",
    "amount_band", "deadline_bucket", "summary_preview", "posted_days_ago",
}
check("output has exactly the safe-key set", set(out.keys()), expected_keys)

leaked = {"funder", "source_url", "deadline", "eligibility", "evidence_ids", "amount_min", "amount_max"}
truthy("no sensitive keys leak", leaked.isdisjoint(out.keys()))
check("funder is em-dash redacted", out["funder_redacted"], "F——n N——h")
truthy("'Foundation' is not in redacted funder", "Foundation" not in out["funder_redacted"])
check("deadline 10d out → Closes this month", out["deadline_bucket"], "Closes this month")
check("amount max=75k → $50k–$250k band", out["amount_band"], "$50k–$250k")

# Minimal row
print("\nMinimal projection (mostly-empty input)")
mini = redact_for_anon({"id": "X", "title": "Y", "country": "nz"})
check("id passthrough", mini["id"], "X")
check("title passthrough", mini["title"], "Y")
check("funder absent → Anonymous funder", mini["funder_redacted"], "Anonymous funder")
check("amount absent → None", mini["amount_band"], None)
check("deadline absent → TBC", mini["deadline_bucket"], "TBC")
check("summary absent → empty string", mini["summary_preview"], "")
check("region defaults to []", mini["region"], [])
check("tags default to []", mini["tags"], [])

print(f"\n{'-' * 40}\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
