# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""The public record and the vocabularies behind every `/api/v1` URL.

Pure functions over published rows: no I/O, no clock (callers pass `today`),
no network. `api/public_v1.py` reads rows through `api.published` and shapes
them here, so the record a page, a JSON twin, a feed and the MCP server show
is built by one function, `public_record`.

Vocabularies defined here once and exposed in `/api/v1/taxonomy`:

- AMOUNT_BANDS    the `amount=` values, in the country's currency
- DEADLINE_BUCKETS the `deadline=` values
- LIST_STATUSES, SORTS
"""

import os
import re
import unicodedata
from datetime import date, datetime, time, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from api.funders import canonicalise_funder, slugify_funder

DEFAULT_APP_URL = "http://localhost:3002"  # matches api/server.py's CORS default
LICENCE = "attribution-required"
SOURCE_NAME = "GoBuga"

# Carried by a stale record, which is served on direct fetch but listed nowhere.
STALE_NOTICE = ("This listing could not be re-verified recently enough to be listed. "
                "Check the funder's own page before relying on it.")

# Fields that must never leave the backend, whatever route builds a response.
INTERNAL_FIELDS = ("evidence_ids", "dedupe_key", "notes", "unresolved_reason")

# (slug, min, max) in whole units of the country's currency; max None is
# open-ended. A row matches a band when the range it states overlaps it. The
# same edges serve every country, as fit's SIZE_BAND_REVENUE does, so a URL
# means the same thing on both deployments; the currency is the country's.
AMOUNT_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("under-10k", 0, 10_000),
    ("10k-50k", 10_000, 50_000),
    ("50k-250k", 50_000, 250_000),
    ("250k-1m", 250_000, 1_000_000),
    ("over-1m", 1_000_000, None),
)
_BAND_BY_SLUG = {slug: (lo, hi) for slug, lo, hi in AMOUNT_BANDS}

# Deadline buckets. `30d` and `90d` nest: a grant closing in 20 days is in both.
DEADLINE_BUCKETS: dict[str, str] = {
    "30d": "dated deadline today or within the next 30 days",
    "90d": "dated deadline today or within the next 90 days",
    "rolling": "rolling deadline confirmed by the verify pass",
    "dated": "any dated deadline, past or future",
}

# `status=` on list endpoints. `all` is live and closed; stale rows are never listed.
LIST_STATUSES: dict[str, tuple[str, ...]] = {
    "live": ("live",),
    "closed": ("closed",),
    "all": ("live", "closed"),
}

SORTS: dict[str, str] = {
    "deadline": "live before closed, then soonest dated deadline first, then rolling",
    "recency": "most recently first seen first",
    "amount": "largest stated maximum first; rows with no amount last",
}

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,79}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,119}$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# --- URLs ---------------------------------------------------------------------

def base_url() -> str:
    """Public site origin from APP_URL, read per call, no trailing slash."""
    return (os.environ.get("APP_URL") or DEFAULT_APP_URL).strip().rstrip("/")


def grant_url(opp_id: str, base: str) -> str:
    return f"{base}/grants/{opp_id}"


def funder_url(slug: str, base: str) -> str:
    return f"{base}/funders/{slug}"


def with_query(url: str, query: str) -> str:
    return f"{url}?{query}" if query else url


# --- Dates --------------------------------------------------------------------

def local_today(now: datetime, tz_name: str) -> date:
    return now.astimezone(ZoneInfo(tz_name)).date()


def day_start_utc(today: date, tz_name: str) -> datetime:
    """Local midnight at the start of `today`, in UTC: when statuses last moved."""
    return datetime.combine(today, time(0), tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _date(value) -> date | None:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


# --- Public record ------------------------------------------------------------

def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _str_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def funder_ref(raw_name: str, country: str, base: str) -> dict:
    slug = slugify_funder(raw_name or "", country)
    return {
        "name": canonicalise_funder(raw_name or "", country),
        "slug": slug,
        "url": funder_url(slug, base) if slug else None,
    }


def http_url(value) -> str | None:
    """The URL if it is absolute http(s) with a host, else None.

    Source URLs are read from funder pages by the sweep, so they are
    untrusted; a page or an agent that links one must never be handed a
    `javascript:` or `data:` URL.
    """
    if not isinstance(value, str) or any(ord(c) <= 0x20 or ord(c) == 0x7f for c in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return value if parts.scheme.lower() in ("http", "https") and parts.hostname else None


def _provenance(row: dict) -> dict:
    """Stored provenance, keeping only the three public keys per field."""
    out = {}
    stored = row.get("provenance")
    if not isinstance(stored, dict):
        return out
    for field, entry in stored.items():
        if isinstance(entry, dict):
            out[field] = {"source_url": http_url(entry.get("source_url")),
                          "verified_at": entry.get("verified_at"),
                          "excerpt": entry.get("excerpt")}
    return out


def public_record(row: dict, *, base: str, retrieved_at: str) -> dict:
    """The contract's public record for one published row.

    Built field by field from the stored row, so an internal field can only
    reach a response by being named here. `retrieved_at` is the request's
    local date: citations quote a day, and a per-second stamp would change the
    body (and its ETag) on every request.
    """
    opp_id = row.get("id") or ""
    country = row.get("country") or ""
    canonical = grant_url(opp_id, base)
    provenance = _provenance(row)
    dl_prov = provenance.get("deadline", {})
    state = row.get("deadline_state")
    record = {
        "id": opp_id,
        "country": country,
        "status": row.get("status"),
        "title": row.get("title"),
        "funder": funder_ref(row.get("funder") or "", country or None, base),
        "deadline": {
            "state": state,
            "date": row.get("deadline") if _date(row.get("deadline")) else None,
            "verified_at": dl_prov.get("verified_at") or row.get("verified_at"),
            "source_url": dl_prov.get("source_url") or http_url(row.get("source_url")),
            "excerpt": dl_prov.get("excerpt") or row.get("source_excerpt"),
        },
        "amount": {
            "min": _amount(row.get("amount_min")),
            "max": _amount(row.get("amount_max")),
            "currency": row.get("currency"),
        },
        "region": _str_list(row.get("region")),
        "tags": _str_list(row.get("tags")),
        "eligibility": row.get("eligibility"),
        "eligible_entities": _str_list(row.get("eligible_entities")),
        "summary": row.get("summary"),
        "source_url": http_url(row.get("source_url")),
        "apply_url": f"{base}/out/{opp_id}",
        "canonical_url": canonical,
        "json_url": f"{canonical}.json",
        "first_seen": row.get("first_seen"),
        "last_seen": row.get("last_seen"),
        "closed_at": row.get("closed_at") if row.get("status") == "closed" else None,
        "verified_at": row.get("verified_at"),
        "verified_by": row.get("verified_by"),
        "provenance": provenance,
        "attribution": {
            "source": SOURCE_NAME,
            "url": canonical,
            "retrieved_at": retrieved_at,
            "licence": LICENCE,
        },
    }
    if row.get("status") == "stale":
        record["notice"] = STALE_NOTICE
    return record


# For the OpenAPI document: what `public_record` returns for a live row.
EXAMPLE_RECORD = {
    "id": "OPP-NZ-2026-08-0209",
    "country": "nz",
    "status": "live",
    "title": "Community Grants: Sport and Recreation",
    "funder": {"name": "Foundation North", "slug": "foundation-north",
               "url": "https://gobuga.org/funders/foundation-north"},
    "deadline": {"state": "dated", "date": "2026-11-30", "verified_at": "2026-08-04T22:00:00+00:00",
                 "source_url": "https://funder.example/grants",
                 "excerpt": "Applications close 30 November 2026."},
    "amount": {"min": 1000, "max": 20000, "currency": "NZD"},
    "region": ["auckland"],
    "tags": ["sport"],
    "eligibility": "Incorporated societies and charitable trusts",
    "eligible_entities": ["incorporated-society", "charitable-trust"],
    "summary": "Funding for community sport clubs and events.",
    "source_url": "https://funder.example/grants",
    "apply_url": "https://gobuga.org/out/OPP-NZ-2026-08-0209",
    "canonical_url": "https://gobuga.org/grants/OPP-NZ-2026-08-0209",
    "json_url": "https://gobuga.org/grants/OPP-NZ-2026-08-0209.json",
    "first_seen": "2026-08-04T20:00:00+00:00",
    "last_seen": "2026-08-04T20:00:00+00:00",
    "closed_at": None,
    "verified_at": "2026-08-04T22:00:00+00:00",
    "verified_by": "verify-pass/haiku",
    "provenance": {"deadline": {"source_url": "https://funder.example/grants",
                                "verified_at": "2026-08-04T22:00:00+00:00",
                                "excerpt": "Applications close 30 November 2026."}},
    "attribution": {"source": SOURCE_NAME, "url": "https://gobuga.org/grants/OPP-NZ-2026-08-0209",
                    "retrieved_at": "2026-09-28", "licence": LICENCE},
}


def brief(row: dict, base: str) -> dict:
    """{id, title, canonical_url}: how lists of IDs are expanded."""
    opp_id = row.get("id") or ""
    return {"id": opp_id, "title": row.get("title"), "canonical_url": grant_url(opp_id, base)}


# --- Filters ------------------------------------------------------------------

def fold(text: str) -> str:
    """Lower-case and strip diacritics, so `q=fundatia` finds "Fundația"."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def matches_q(row: dict, needles: list[str]) -> bool:
    if not needles:
        return True
    hay = fold(" ".join(str(row.get(k) or "") for k in ("title", "funder", "summary", "eligibility"))
               + " " + " ".join(_str_list(row.get("tags"))))
    return all(n in hay for n in needles)


def matches_tags(row: dict, tags: list[str]) -> bool:
    row_tags = {t.lower() for t in _str_list(row.get("tags"))}
    return all(t in row_tags for t in tags)


def row_regions(row: dict, national_synonyms: frozenset[str]) -> set[str]:
    out = set()
    for value in _str_list(row.get("region")):
        v = value.strip().lower()
        out.add("national" if fold(v) in national_synonyms else v)
    return out


def matches_region(row: dict, region: str | None, national_synonyms: frozenset[str]) -> bool:
    """A national row is open to every region; `region=national` wants national rows only."""
    if not region:
        return True
    regions = row_regions(row, national_synonyms)
    return region in regions or "national" in regions


def amount_range(row: dict) -> tuple[float, float] | None:
    """(low, high) the row states, or None. "Up to 20k" is (0, 20k);
    "from 5k" is (5k, inf); a contradictory min above max is None."""
    lo, hi = _amount(row.get("amount_min")), _amount(row.get("amount_max"))
    lo = lo if lo is not None and lo > 0 else None
    hi = hi if hi is not None and hi > 0 else None
    if lo is None and hi is None:
        return None
    lo, hi = (lo or 0), (hi if hi is not None else float("inf"))
    return None if lo > hi else (lo, hi)


def matches_amount(row: dict, band: str | None, currency: str) -> bool:
    """Overlap between the row's stated range and the band, in the country's
    currency only: a row in another currency is unknown, not converted."""
    if not band:
        return True
    rng = amount_range(row)
    if rng is None or (row.get("currency") or "").upper() != currency.upper():
        return False
    lo, hi = _BAND_BY_SLUG[band]
    band_hi = float("inf") if hi is None else hi
    return rng[0] < band_hi and rng[1] >= lo


def matches_deadline(row: dict, bucket: str | None, today: date) -> bool:
    if not bucket:
        return True
    state = row.get("deadline_state")
    if bucket == "rolling":
        return state == "rolling-confirmed"
    when = _date(row.get("deadline"))
    if state != "dated" or when is None:
        return False
    if bucket == "dated":
        return True
    days = (when - today).days
    return 0 <= days <= (30 if bucket == "30d" else 90)


# --- Sorting ------------------------------------------------------------------

def sort_rows(rows: list[dict], sort: str) -> list[dict]:
    """Deterministic order: every key ends in the row's id."""
    if sort == "recency":
        def key(r):
            ts = parse_ts(r.get("first_seen"))
            return (ts is None, -(ts.timestamp() if ts else 0), r.get("id") or "")
    elif sort == "amount":
        def key(r):
            rng = amount_range(r)
            top = None if rng is None else (rng[1] if rng[1] != float("inf") else rng[0])
            return (top is None, -(top or 0), r.get("id") or "")
    else:
        def key(r):
            when = _date(r.get("deadline")) if r.get("deadline_state") == "dated" else None
            return (r.get("status") != "live", when is None, when or date.max, r.get("id") or "")
    return sorted(rows, key=key)


# --- Funders ------------------------------------------------------------------

def group_by_funder(rows: list[dict], country: str) -> dict[str, list[dict]]:
    """Rows keyed by funder slug, in id order within each funder."""
    groups: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda r: r.get("id") or ""):
        slug = slugify_funder(row.get("funder") or "", country)
        if slug:
            groups.setdefault(slug, []).append(row)
    return groups


def funder_summary(slug: str, rows: list[dict], country: str, base: str) -> dict:
    ref = funder_ref(rows[0].get("funder") or "", country, base)
    return {
        "name": ref["name"],
        "slug": slug,
        "url": funder_url(slug, base),
        "json_url": f"{funder_url(slug, base)}.json",
        "live_count": sum(1 for r in rows if r.get("status") == "live"),
        "closed_count": sum(1 for r in rows if r.get("status") == "closed"),
    }


def related_live(row: dict, live_rows: list[dict], base: str, limit: int = 5) -> list[dict]:
    """Up to `limit` live rows sharing at least one tag, most shared tags
    first, then by the deadline sort."""
    tags = {t.lower() for t in _str_list(row.get("tags"))}
    if not tags:
        return []
    candidates = []
    for other in sort_rows(live_rows, "deadline"):
        if other.get("id") == row.get("id"):
            continue
        shared = len(tags & {t.lower() for t in _str_list(other.get("tags"))})
        if shared:
            candidates.append((-shared, len(candidates), other))
    candidates.sort(key=lambda c: (c[0], c[1]))
    return [brief(other, base) for _, _, other in candidates[:limit]]
