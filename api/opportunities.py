"""Opportunity pool — read, merge across months, and adapt to legacy grant_brief.

Backs `GET /api/opportunities` and `POST /api/opportunities/open-case`.
The pool itself is produced by `orchestrator.sweep:run_country_sweep`.
"""

import json
import os
import re
from datetime import date as date_type, datetime, timezone

from api.funders import canonicalise_funder, slugify_funder
from api.taxonomy import validate_tags
from api.tenant import platform_cycles_dir, platform_latest_path


# --- Pool I/O ---

def _opportunities_path(country: str, month: str) -> str:
    return os.path.join(platform_latest_path(country, month), "opportunities.json")


def load_pool(country: str, month: str) -> list[dict]:
    """Load the canonical opportunity pool for a country/month. Empty if missing."""
    path = _opportunities_path(country, month)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "opportunities" in data:
        return data["opportunities"]
    return data


def current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


# Aliases for country values stored on org records (the setup wizard saved
# labels like "New Zealand" before the search-box pivot existed).
_COUNTRY_ALIASES = {
    "nz": "nz",
    "new zealand": "nz",
    "aotearoa": "nz",
    "aotearoa new zealand": "nz",
}


def country_slug(value: str | None, default: str = "nz") -> str:
    """Normalise any country form (slug or label) to a country slug."""
    if not value:
        return default
    return _COUNTRY_ALIASES.get(value.strip().lower(), value.strip().lower())


def previous_month(month: str) -> str:
    """Given 'YYYY-MM', return the prior month."""
    y, m = (int(p) for p in month.split("-"))
    if m == 1:
        return f"{y - 1}-12"
    return f"{y}-{m - 1:02d}"


def latest_available_month(
    country: str,
    before: str | None = None,
    max_lookback: int = 6,
) -> str | None:
    """Walk back from `before` (default: current month) to find the most recent
    month that has a populated pool. Returns None if nothing found within
    `max_lookback` months. Lets the system tolerate skipped sweeps."""
    month = before or current_month()
    for _ in range(max_lookback):
        if os.path.exists(_opportunities_path(country, month)):
            return month
        month = previous_month(month)
    return None


def load_current_pool(country: str = "nz") -> list[dict]:
    month = latest_available_month(country) or current_month()
    return load_pool(country, month)


# --- Cross-month dedupe + live filter ---

def merge_with_previous(new_pool: list[dict], previous_pool: list[dict]) -> list[dict]:
    """Carry `id` and `first_seen` from previous month for matching dedupe keys.

    A row whose `dedupe_key` matches a previous-month row keeps the prior `id`
    and `first_seen`, and bumps `last_seen` to the new row's value.
    """
    prev_by_key = {p.get("dedupe_key"): p for p in previous_pool if p.get("dedupe_key")}
    merged: list[dict] = []
    for row in new_pool:
        key = row.get("dedupe_key")
        prior = prev_by_key.get(key) if key else None
        if prior:
            row = dict(row)
            row["id"] = prior.get("id", row.get("id"))
            row["first_seen"] = prior.get("first_seen", row.get("first_seen"))
        merged.append(row)
    return merged


def live_only(pool: list[dict], today: date_type | None = None) -> list[dict]:
    """Drop rows whose `deadline` is in the past."""
    today = today or datetime.now(timezone.utc).date()
    out = []
    for row in pool:
        dl = row.get("deadline")
        if not dl or dl in ("rolling", "TBC", "tbc"):
            out.append(row)
            continue
        try:
            if datetime.fromisoformat(str(dl)).date() >= today:
                out.append(row)
        except (TypeError, ValueError):
            out.append(row)
    return out


# --- Adapter: opportunity row -> legacy grant_brief shape ---

def _format_amount(row: dict) -> str | None:
    lo = row.get("amount_min")
    hi = row.get("amount_max")
    if lo and hi and lo != hi:
        return f"${lo:,}–${hi:,} {row.get('currency', '')}".strip()
    if hi:
        return f"up to ${hi:,} {row.get('currency', '')}".strip()
    if lo:
        return f"from ${lo:,} {row.get('currency', '')}".strip()
    return None


def _build_description(row: dict) -> str:
    """Combine summary + eligibility + region into the description field
    that case_manager._next_case_id reads for sector/geo inference."""
    parts: list[str] = []
    if row.get("summary"):
        parts.append(row["summary"])
    if row.get("eligibility"):
        parts.append(f"Eligibility: {row['eligibility']}")
    regions = row.get("region") or []
    if regions:
        parts.append(f"Region: {', '.join(regions)}")
    tags = row.get("tags") or []
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    return "\n\n".join(parts)


# --- Watcher content parser (structured text block from the watcher prompt) ---

# Recognised field labels in watcher content. Lower-cased, alternate spellings
# allowed. Order doesn't matter — the parser splits the content at any label.
_WATCHER_LABELS = {
    "funder": ["funder name", "funder"],
    "programme": ["programme name", "programme", "program name", "program"],
    "eligibility": ["eligibility"],
    "amount": ["funding range", "funding amount", "funding", "amount", "grant amount", "grant size"],
    "deadline": ["deadline", "closing date", "applications close", "closes"],
    "region": ["region", "regions", "geography", "geographic scope"],
    "tags": ["sector tags", "tags", "sectors"],
    "summary": ["summary", "overview", "description"],
    "notes": ["notes", "notes / quirks", "notes/quirks", "quirks"],
}


def _strip_md(text: str) -> str:
    """Strip surrounding markdown emphasis (*, **, _) and trim."""
    if not text:
        return ""
    s = text.strip()
    # Repeatedly strip leading/trailing **, *, _
    for _ in range(3):
        new = re.sub(r"^[\*_`]+\s*", "", s)
        new = re.sub(r"\s*[\*_`]+$", "", new)
        if new == s:
            break
        s = new
    return s.strip()


def _parse_watcher_content(content: str) -> dict:
    """Extract labelled fields from a watcher evidence content block.
    Tolerant of label variants, case, markdown emphasis, and whitespace."""
    out: dict[str, str] = {}
    if not content:
        return out
    label_to_key: dict[str, str] = {}
    for key, variants in _WATCHER_LABELS.items():
        for v in variants:
            label_to_key[v.lower()] = key
    lines = content.split("\n")
    current_key: str | None = None
    buf: list[str] = []
    for line in lines:
        stripped = line.strip()
        m = re.match(r"^\*?\*?([A-Za-z][A-Za-z /&]{1,40})\*?\*?\s*[:\-—]\s*(.*)$", stripped)
        if m:
            label = m.group(1).strip().lower()
            value = m.group(2).strip()
            if label in label_to_key:
                if current_key is not None:
                    out[current_key] = _strip_md("\n".join(buf).strip())
                current_key = label_to_key[label]
                buf = [value] if value else []
                continue
        if current_key is not None:
            buf.append(line)
    if current_key is not None:
        out[current_key] = _strip_md("\n".join(buf).strip())
    # Single-line cleanup for short fields
    for k in ("funder", "programme", "deadline"):
        if k in out:
            out[k] = _strip_md(out[k].split("\n")[0])
    return out


_AMOUNT_NUM = re.compile(r"\$?\s*(\d{1,3}(?:[,\s]\d{3})*|\d+)(?:\.\d+)?\s*([kKmM])?")


def _normalise_amount_token(num: str, suffix: str | None) -> int:
    n = int(num.replace(",", "").replace(" ", ""))
    if suffix and suffix.lower() == "k":
        n *= 1_000
    elif suffix and suffix.lower() == "m":
        n *= 1_000_000
    return n


def _extract_amount_range(text: str) -> tuple[int | None, int | None]:
    """Best-effort parse of free-text funding ranges.
    Returns (min, max) — either may be None if not extractable."""
    if not text:
        return None, None
    t = text.lower()
    # Reject when no digits — e.g. "varies" / "TBC"
    if not re.search(r"\d", t):
        return None, None
    matches = _AMOUNT_NUM.findall(text)
    nums = [_normalise_amount_token(m[0], m[1]) for m in matches]
    nums = [n for n in nums if 100 <= n <= 100_000_000]  # filter junk
    if not nums:
        return None, None
    if "up to" in t or "maximum" in t or "max " in t:
        return None, max(nums)
    if "from " in t or "minimum" in t or "starts at" in t or "at least" in t:
        return min(nums), None
    if len(nums) >= 2:
        return min(nums), max(nums)
    # Single number: treat as a target amount (max)
    return None, nums[0]


_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DMY_RE = re.compile(r"\b(\d{1,2})[/\- ](\d{1,2}|[A-Za-z]{3,9})[/\- ](\d{2,4})\b")
_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _extract_deadline(text: str) -> str:
    """Return ISO date string, or 'rolling', or 'TBC'."""
    if not text:
        return "TBC"
    t = text.strip().lower()
    if "rolling" in t or "open all year" in t or "continuous" in t or "no deadline" in t:
        return "rolling"
    # Prefer ISO
    m = _ISO_DATE_RE.search(text)
    if m:
        return m.group(1)
    # DD Month YYYY style
    for match in re.finditer(
        r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", text
    ):
        day, mon_name, year = match.group(1), match.group(2).lower(), match.group(3)
        mon = _MONTH_NAMES.get(mon_name)
        if mon:
            try:
                return f"{int(year):04d}-{mon:02d}-{int(day):02d}"
            except ValueError:
                continue
    return "TBC"


def _extract_regions(text: str) -> list[str]:
    if not text:
        return ["national"]
    t = text.lower()
    if "national" in t or "nationwide" in t or "new zealand" in t or "all of nz" in t or "across new zealand" in t:
        return ["national"]
    # Pull region slugs from the content
    region_slugs = [
        "northland", "auckland", "waikato", "bay-of-plenty", "gisborne",
        "hawkes-bay", "taranaki", "manawatu-whanganui", "wellington",
        "tasman", "nelson", "marlborough", "west-coast", "canterbury",
        "otago", "southland",
    ]
    found = []
    for slug in region_slugs:
        # match slug or its space-form
        if slug in t or slug.replace("-", " ") in t:
            found.append(slug)
    return found or ["national"]


def _extract_tags(text: str, valid_tags: list[str] | None = None) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[,;|]", text)
    raw = [p.strip().lower() for p in parts if p.strip()]
    return validate_tags(raw)


def _source_domain(url: str) -> str:
    """Bare hostname (no scheme, no www., no path) for dedupe keys."""
    if not url:
        return ""
    s = url.strip().lower()
    s = re.sub(r"^[a-z]+://", "", s)
    s = s.split("/", 1)[0]
    s = s.split("?", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    return s


def build_opportunities_from_watchers(
    watcher_evidence: list[dict],
    country: str,
    month: str,
    currency: str = "NZD",
    now_iso: str | None = None,
) -> list[dict]:
    """Build opportunity rows directly from watcher evidence — no LLM analyst.

    Watcher prompts emit a structured `Field: value` content block per programme.
    This parses those blocks deterministically. Dedupe by canonicalised funder
    + programme slug + deadline, with `evidence_ids` accumulating across workers
    that found the same programme.
    """
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    country_upper = country.upper()
    by_dedupe: dict[str, dict] = {}
    seq = 0

    for ev in watcher_evidence:
        agent = ev.get("agent", "")
        if not agent.startswith("grant_watcher_"):
            continue
        if ev.get("type") != "grant_opportunity":
            continue
        parsed = _parse_watcher_content(ev.get("content", ""))
        if not parsed:
            continue

        funder_raw = parsed.get("funder") or ""
        programme = parsed.get("programme") or ""
        if not funder_raw or not programme:
            # Try title fallback ("Funder — Programme")
            title = ev.get("title") or ""
            if "—" in title and not funder_raw:
                f_part, p_part = title.split("—", 1)
                funder_raw = funder_raw or f_part.strip()
                programme = programme or p_part.strip()
            if not funder_raw or not programme:
                continue

        funder = canonicalise_funder(funder_raw)
        deadline = _extract_deadline(parsed.get("deadline") or "")
        funder_slug = slugify_funder(funder)
        programme_slug = re.sub(r"[^a-z0-9]+", "-", programme.lower()).strip("-")[:80]
        domain = _source_domain(ev.get("source_url") or "")
        # Prefer (funder, domain, deadline) so title variants of the same programme collapse.
        # Fall back to programme_slug when source_url is missing, to avoid merging unrelated rows.
        dedupe_token = domain or programme_slug
        dedupe_key = f"{funder_slug}|{dedupe_token}|{deadline}".lower()

        if dedupe_key in by_dedupe:
            existing = by_dedupe[dedupe_key]
            ev_id = ev.get("id")
            if ev_id and ev_id not in existing["evidence_ids"]:
                existing["evidence_ids"].append(ev_id)
            # Prefer the longest title — usually the most descriptive variant.
            new_title = programme
            if new_title and len(new_title) > len(existing.get("title", "")):
                existing["title"] = new_title
            # Prefer fuller summary if existing is shorter
            new_summary = parsed.get("summary") or ""
            if new_summary and len(new_summary) > len(existing.get("summary", "")):
                existing["summary"] = new_summary
            continue

        amount_min, amount_max = _extract_amount_range(parsed.get("amount") or "")
        regions = _extract_regions(parsed.get("region") or "")
        tags = _extract_tags(parsed.get("tags") or "")

        seq += 1
        row = {
            "id": f"OPP-{country_upper}-{month}-{seq:04d}",
            "country": country,
            "title": programme,
            "funder": funder,
            "deadline": deadline,
            "amount_min": amount_min,
            "amount_max": amount_max,
            "currency": currency,
            "region": regions,
            "tags": tags,
            "eligibility": parsed.get("eligibility") or "",
            "summary": parsed.get("summary") or "",
            "source_url": ev.get("source_url") or "",
            "evidence_ids": [ev.get("id")] if ev.get("id") else [],
            "first_seen": now_iso,
            "last_seen": now_iso,
            "dedupe_key": dedupe_key,
        }
        if parsed.get("notes"):
            row["notes"] = parsed["notes"]
        by_dedupe[dedupe_key] = row

    return list(by_dedupe.values())


def _parse_analyst_content(content: str) -> dict | None:
    """Analyst items put a JSON object in `content`. Tolerate fenced or bare JSON."""
    if not content:
        return None
    txt = content.strip()
    if txt.startswith("```"):
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", txt, re.DOTALL)
        if m:
            txt = m.group(1)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def build_opportunities_from_analyst(
    analyst_evidence: list[dict],
    country: str,
    month: str,
    currency: str = "NZD",
    now_iso: str | None = None,
) -> list[dict]:
    """Deterministically build opportunity rows from Analyst evidence items.

    Replaces the LLM Reporter step — analyst items are already structured per
    programme, so we just shape them into rows. No truncation risk, no parsing
    fragility, sequenced ids, validated tags.
    """
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    country_upper = country.upper()
    rows: list[dict] = []
    seq = 0
    by_dedupe: dict[str, dict] = {}

    for ev in analyst_evidence:
        if ev.get("agent") != "grant_analyst_country":
            continue
        if ev.get("type") != "analysis":
            continue
        parsed = _parse_analyst_content(ev.get("content", ""))
        if not parsed:
            continue

        funder_raw = parsed.get("funder") or ""
        funder = canonicalise_funder(funder_raw)
        programme = parsed.get("programme") or parsed.get("title") or ""
        if not (funder and programme):
            continue

        deadline = parsed.get("deadline") or "TBC"
        funder_slug = slugify_funder(funder)
        programme_slug = re.sub(r"[^a-z0-9]+", "-", programme.lower()).strip("-")
        domain = _source_domain(ev.get("source_url") or "")
        dedupe_token = domain or programme_slug
        dedupe_key = f"{funder_slug}|{dedupe_token}|{deadline}".lower()

        if dedupe_key in by_dedupe:
            existing = by_dedupe[dedupe_key]
            if ev.get("id") not in existing["evidence_ids"]:
                existing["evidence_ids"].append(ev.get("id"))
            if programme and len(programme) > len(existing.get("title", "")):
                existing["title"] = programme
            continue

        seq += 1
        row = {
            "id": f"OPP-{country_upper}-{month}-{seq:04d}",
            "country": country,
            "title": programme,
            "funder": funder,
            "deadline": deadline,
            "amount_min": parsed.get("amount_min"),
            "amount_max": parsed.get("amount_max"),
            "currency": currency,
            "region": parsed.get("region") or ["national"],
            "tags": validate_tags(parsed.get("tags") or []),
            "eligibility": parsed.get("eligibility") or "",
            "summary": parsed.get("summary") or "",
            "source_url": ev.get("source_url") or "",
            "evidence_ids": [ev.get("id")] if ev.get("id") else [],
            "first_seen": now_iso,
            "last_seen": now_iso,
            "dedupe_key": dedupe_key,
        }
        if parsed.get("amount_note"):
            row["amount_note"] = parsed["amount_note"]
        if parsed.get("notes"):
            row["notes"] = parsed["notes"]
        by_dedupe[dedupe_key] = row
        rows.append(row)

    return rows


# --- Filter / sort / paginate ---

_TBC_DEADLINE_TOKENS = {"tbc", "rolling", "", "unknown"}


def _matches_q(row: dict, q: str) -> bool:
    if not q:
        return True
    needles = [n for n in q.lower().split() if n]
    haystack = " ".join([
        str(row.get("title") or ""),
        str(row.get("funder") or ""),
        str(row.get("summary") or ""),
        str(row.get("eligibility") or ""),
        " ".join(row.get("tags") or []),
    ]).lower()
    return all(n in haystack for n in needles)


def _matches_tags(row: dict, tags: list[str]) -> bool:
    if not tags:
        return True
    row_tags = set((row.get("tags") or []))
    return all(t in row_tags for t in tags)


def _matches_region(row: dict, region: str | None) -> bool:
    if not region:
        return True
    row_regions = set((row.get("region") or []))
    if "national" in row_regions:
        return True
    return region in row_regions


def _matches_deadline_before(row: dict, before: str | None) -> bool:
    if not before:
        return True
    dl = row.get("deadline")
    if not dl or str(dl).strip().lower() in _TBC_DEADLINE_TOKENS:
        return True
    try:
        return datetime.fromisoformat(str(dl)).date() <= datetime.fromisoformat(before).date()
    except (TypeError, ValueError):
        return True


def _matches_amount(row: dict, min_amount: int | None, max_amount: int | None) -> bool:
    lo = row.get("amount_min")
    hi = row.get("amount_max")
    if min_amount is not None and hi is not None and hi < min_amount:
        return False
    if max_amount is not None and lo is not None and lo > max_amount:
        return False
    return True


def _matches_funder(row: dict, funder: str | None) -> bool:
    if not funder:
        return True
    return (row.get("funder") or "").strip().lower() == funder.strip().lower()


def _primary_sort_key(row: dict, sort: str):
    if sort == "deadline":
        dl = row.get("deadline")
        if not dl or str(dl).strip().lower() in _TBC_DEADLINE_TOKENS:
            return (1, "9999-99-99")
        return (0, str(dl))
    if sort == "amount_desc":
        return -(row.get("amount_max") or 0)
    return row.get("first_seen") or ""


def _region_priority(row: dict, picked_region: str | None) -> int:
    """0 = row is regionally specific to the picked region (rank first).
    1 = row is national-only (rank after regional matches).
    Returns 0 for everything when no region is picked or 'national' is picked."""
    if not picked_region or picked_region == "national":
        return 0
    row_regions = set(row.get("region") or [])
    if picked_region in row_regions:
        return 0
    return 1


def filter_pool(
    pool: list[dict],
    *,
    q: str = "",
    tags: list[str] | None = None,
    region: str | None = None,
    deadline_before: str | None = None,
    min_amount: int | None = None,
    max_amount: int | None = None,
    funder: str | None = None,
    sort: str = "recency",
    cursor: int = 0,
    limit: int = 50,
) -> dict:
    """Apply filters + sort + pagination to a pool. Returns
    {opportunities, total, cursor, limit, has_more}."""
    tags_list = [t.strip().lower() for t in (tags or []) if t.strip()]
    matched = [
        row for row in pool
        if _matches_q(row, q)
        and _matches_tags(row, tags_list)
        and _matches_region(row, region)
        and _matches_deadline_before(row, deadline_before)
        and _matches_amount(row, min_amount, max_amount)
        and _matches_funder(row, funder)
    ]
    if sort == "random":
        import random
        random.shuffle(matched)
        # Region priority still applies — regional matches first, random within bucket.
        matched.sort(key=lambda r: _region_priority(r, region))
        total = len(matched)
        cursor = max(0, int(cursor or 0))
        limit = max(1, min(int(limit or 50), 500))
        page = matched[cursor:cursor + limit]
        return {
            "opportunities": page,
            "total": total,
            "cursor": cursor,
            "limit": limit,
            "has_more": cursor + limit < total,
        }
    reverse = sort == "recency"
    # When a specific region is picked, regional matches always rank above
    # national-only matches. Within each partition, the primary sort applies.
    # Python's sort is stable: sort by primary first, then by region-priority,
    # and rows with the same priority keep their primary ordering.
    primary = lambda r: _primary_sort_key(r, sort)
    matched.sort(key=primary, reverse=reverse)
    matched.sort(key=lambda r: _region_priority(r, region))
    total = len(matched)
    cursor = max(0, int(cursor or 0))
    limit = max(1, min(int(limit or 50), 500))
    page = matched[cursor:cursor + limit]
    return {
        "opportunities": page,
        "total": total,
        "cursor": cursor,
        "limit": limit,
        "has_more": cursor + limit < total,
    }


def vague_deadline_rows(rows: list[dict]) -> list[dict]:
    """Rows whose deadline is TBC / rolling / missing — candidates for the
    deadline resolver."""
    out = []
    for r in rows:
        d = (r.get("deadline") or "").strip().lower()
        if d in ("", "tbc", "rolling", "unknown"):
            out.append(r)
    return out


def opportunity_to_grant_brief(opportunity: dict, country: str, month: str) -> dict:
    """Adapt a new opportunity row to the legacy `grant_brief` shape that
    `case_manager.create_case` and the case-side bots already understand."""
    description = _build_description(opportunity)

    source_url = opportunity.get("source_url")
    source_urls = []
    if source_url:
        source_urls.append({
            "title": opportunity.get("title", ""),
            "url": source_url,
            "type": "official",
        })

    eligibility = opportunity.get("eligibility")
    key_requirements = [eligibility] if eligibility else []

    details: list[str] = []
    tags = opportunity.get("tags") or []
    if tags:
        details.append(f"Tags: {', '.join(tags)}")
    regions = opportunity.get("region") or []
    if regions:
        details.append(f"Region: {', '.join(regions)}")

    brief = {
        "title": opportunity.get("title", ""),
        "description": description,
        "deadline": opportunity.get("deadline"),
        "amount": _format_amount(opportunity),
        "amount_min": opportunity.get("amount_min"),
        "amount_max": opportunity.get("amount_max"),
        "funder": opportunity.get("funder"),
        "priority": "medium",
        "details": details,
        "key_requirements": key_requirements,
        "source_urls": source_urls,
        "source_cycle": f"{country}/{month}",
        "opportunity_id": opportunity.get("id"),
        "tags": tags,
        "region": regions,
    }
    return {k: v for k, v in brief.items() if v not in (None, "", [], {})}


# --- Anon redaction (public landing feed) ---
#
# Projects a full pool row down to a deliberately narrower shape that hides
# funder identity, source URL, exact deadline, eligibility text, and evidence
# pointers. The single source of truth for "what an unauthenticated visitor
# can see" lives here — the public router calls this and never exposes the
# original row.

# Bands are upper-inclusive at the named boundary — e.g. an amount of exactly
# $50k reads as "$10k–$50k", not "$50k–$250k". "Under $10k" is the only band
# that's strict below; everything else uses <= against the labelled cap.
_AMOUNT_BANDS: list[tuple[float, str]] = [
    (50_000, "$10k–$50k"),
    (250_000, "$50k–$250k"),
    (1_000_000, "$250k–$1m"),
    (float("inf"), "$1m+"),
]


def _redact_funder(name: str | None) -> str:
    """Initial-em-dash redaction: 'Foundation North' -> 'F——n N——h'.
    Falls back to a generic label when the name is missing or too short to
    redact meaningfully (a 2-letter funder isn't worth half-revealing)."""
    if not name:
        return "Anonymous funder"
    cleaned = name.strip()
    if len(cleaned) < 3:
        return "Anonymous funder"
    parts: list[str] = []
    for word in cleaned.split():
        if len(word) <= 2:
            parts.append(word)
        else:
            parts.append(f"{word[0]}{'—' * 2}{word[-1]}")
    return " ".join(parts) or "Anonymous funder"


def _band_amount(amount_min: int | None, amount_max: int | None) -> str | None:
    figure = amount_max if amount_max else amount_min
    if not figure:
        return None
    if figure < 10_000:
        return "Under $10k"
    for ceiling, label in _AMOUNT_BANDS:
        if figure <= ceiling:
            return label
    return _AMOUNT_BANDS[-1][1]


def _bucket_deadline(deadline: str | None, today: date_type | None = None) -> str:
    """Convert an ISO deadline / 'rolling' / 'TBC' to an anon-safe bucket
    string. Hides the exact date — the visitor sees urgency, not the
    precise day."""
    if not deadline:
        return "TBC"
    s = str(deadline).strip().lower()
    if s == "rolling":
        return "Rolling"
    if s in ("tbc", "unknown", ""):
        return "TBC"
    try:
        d = datetime.fromisoformat(str(deadline)).date()
    except (TypeError, ValueError):
        return "TBC"
    today = today or datetime.now(timezone.utc).date()
    days = (d - today).days
    if days < 0:
        return "Closed"
    if days <= 7:
        return "Closes this week"
    if days <= 30:
        return "Closes this month"
    return "30+ days"


def _days_ago(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    return max(0, delta.days)


def _truncate_summary(text: str | None, length: int = 140) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    if len(cleaned) <= length:
        return cleaned
    return cleaned[:length].rstrip() + "…"


def redact_for_anon(row: dict) -> dict:
    """Project a full pool row down to the anon-safe shape. The returned
    dict contains *only* the keys listed below — funder identity, source
    URL, exact deadline, eligibility text, and evidence IDs are dropped."""
    return {
        "id": row.get("id"),
        "title": row.get("title", ""),
        "funder_redacted": _redact_funder(row.get("funder")),
        "country": row.get("country"),
        "region": row.get("region") or [],
        "tags": row.get("tags") or [],
        "amount_band": _band_amount(row.get("amount_min"), row.get("amount_max")),
        "deadline_bucket": _bucket_deadline(row.get("deadline")),
        "summary_preview": _truncate_summary(row.get("summary")),
        "posted_days_ago": _days_ago(row.get("first_seen")),
    }
