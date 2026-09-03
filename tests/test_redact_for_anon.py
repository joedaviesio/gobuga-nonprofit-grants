"""Unit tests for the anon-feed redaction projection (api.opportunities).

Pure-Python — no server, no network.
"""

from datetime import datetime, timedelta, timezone

import pytest

from api.opportunities import (
    _band_amount,
    _bucket_deadline,
    _days_ago,
    _redact_funder,
    _truncate_summary,
    redact_for_anon,
)

TODAY = datetime.now(timezone.utc).date()
NOW = datetime.now(timezone.utc)


# --- Funder redaction ---

@pytest.mark.parametrize("funder,expected", [
    ("Foundation North", "F——n N——h"),
    ("AA Foundation", "AA F——n"),
    (None, "Anonymous funder"),
    ("", "Anonymous funder"),
    ("Z", "Anonymous funder"),
    ("AB", "Anonymous funder"),
])
def test_redact_funder(funder, expected):
    assert _redact_funder(funder) == expected


# --- Amount banding ---

@pytest.mark.parametrize("amount_min,amount_max,expected", [
    (None, None, None),
    (5_000, 8_000, "Under $10k"),
    (10_000, 50_000, "$10k–$50k"),
    (50_000, 250_000, "$50k–$250k"),
    (250_000, 999_999, "$250k–$1m"),
    (None, 5_000_000, "$1m+"),
    (15_000, None, "$10k–$50k"),  # falls back to amount_min when max missing
])
def test_band_amount(amount_min, amount_max, expected):
    assert _band_amount(amount_min, amount_max) == expected


# --- Deadline bucketing ---

@pytest.mark.parametrize("deadline,expected", [
    (None, "TBC"),
    ("TBC", "TBC"),
    ("rolling", "Rolling"),
    ("not-a-date", "TBC"),
])
def test_bucket_deadline_non_dates(deadline, expected):
    assert _bucket_deadline(deadline) == expected


@pytest.mark.parametrize("days_out,expected", [
    (-2, "Closed"),
    (3, "Closes this week"),
    (20, "Closes this month"),
    (90, "30+ days"),
])
def test_bucket_deadline_relative(days_out, expected):
    deadline = (TODAY + timedelta(days=days_out)).isoformat()
    assert _bucket_deadline(deadline, today=TODAY) == expected


# --- Helpers ---

def test_days_ago_now_is_zero():
    assert _days_ago(NOW.isoformat()) == 0


def test_days_ago_three_days():
    assert _days_ago((NOW - timedelta(days=3)).isoformat()) == 3


@pytest.mark.parametrize("value", [None, "garbage"])
def test_days_ago_invalid_is_none(value):
    assert _days_ago(value) is None


def test_truncate_short_text_unchanged():
    assert _truncate_summary("short") == "short"


def test_truncate_long_text_adds_ellipsis():
    truncated = _truncate_summary("a" * 200, length=50)
    assert truncated.endswith("…")
    assert len(truncated) == 51


# --- Full projection ---

FULL_ROW = {
    "id": "OPP-NZ-2026-04-0001",
    "country": "nz",
    "title": "Climate Action Grant",
    "funder": "Foundation North",
    "deadline": (TODAY + timedelta(days=10)).isoformat(),
    "amount_min": 25_000,
    "amount_max": 75_000,
    "currency": "NZD",
    "region": ["auckland"],
    "tags": ["climate", "community-development"],
    "eligibility": "Charitable trusts and incorporated societies operating in Auckland.",
    "summary": "A grant for grassroots climate action in Tāmaki Makaurau.",
    "source_url": "https://foundationnorth.org.nz/climate-action-grant",
    "evidence_ids": ["ev_1", "ev_2"],
    "first_seen": (NOW - timedelta(days=4)).isoformat(),
    "last_seen": NOW.isoformat(),
    "dedupe_key": "foundation-north|climate-action-grant|...",
}

SAFE_KEYS = {
    "id", "title", "funder_redacted", "country", "region", "tags",
    "amount_band", "deadline_bucket", "summary_preview", "posted_days_ago",
}

SENSITIVE_KEYS = {
    "funder", "source_url", "deadline", "eligibility",
    "evidence_ids", "amount_min", "amount_max",
}


@pytest.fixture
def projected():
    return redact_for_anon(FULL_ROW)


def test_projection_exposes_exactly_safe_keys(projected):
    assert set(projected.keys()) == SAFE_KEYS


def test_projection_leaks_no_sensitive_keys(projected):
    assert SENSITIVE_KEYS.isdisjoint(projected.keys())


def test_projection_redacts_funder(projected):
    assert projected["funder_redacted"] == "F——n N——h"
    assert "Foundation" not in projected["funder_redacted"]


def test_projection_buckets_deadline(projected):
    assert projected["deadline_bucket"] == "Closes this month"


def test_projection_bands_amount(projected):
    assert projected["amount_band"] == "$50k–$250k"


def test_projection_of_minimal_row():
    mini = redact_for_anon({"id": "X", "title": "Y", "country": "nz"})
    assert mini["id"] == "X"
    assert mini["title"] == "Y"
    assert mini["funder_redacted"] == "Anonymous funder"
    assert mini["amount_band"] is None
    assert mini["deadline_bucket"] == "TBC"
    assert mini["summary_preview"] == ""
    assert mini["region"] == []
    assert mini["tags"] == []
