"""Published dataset — the verified, cumulative, public record of grants.

The sweep writes the workspace pool under `platform/cycles/`. This module
writes the public dataset under `<platform_dir>/published/<country>/`:

- `pool.json`    every row ever published, each with a stable `id`
- `changes.json` one entry per publish, newest first
- `meta.json`    when, and from which sweep, the dataset was last published
- `held.json`    internal report: rows held back and why, merges, shared-URL
                 groups. Never served publicly.

`publish_pool` is the only writer. It applies the publish gate, dedupes,
checks the must-appear funders and writes all four files atomically.

Readers compute each row's effective `status` from the day they are called,
so a deadline that passes between sweeps closes on the day and a rolling row
whose verification has expired goes stale on the day.

CLI (no network):
    python -m api.published status nz
    python -m api.published publish nz 2026-10 [--force] [--by NAME]
"""

import difflib
import json
import os
import re
import sys
import tempfile
import threading
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from api import sources, tenant
from api.country_config import get_country, get_country_config
from api.funders import slugify_funder


SWEEP_INTERVAL_MONTHS = 2
# One sweep interval plus two weeks' grace: a rolling row survives until the
# next sweep can re-check it, and no longer.
ROLLING_EXPIRY_DAYS = 75

PUBLISHABLE_STATES = ("dated", "rolling-confirmed", "closed")

# Public fact fields: a difference in any of these is a "change" in the
# changelog. `last_seen` and `verified_at` are deliberately absent.
FACT_FIELDS = (
    "title", "funder", "deadline", "amount_min", "amount_max", "region",
    "tags", "eligibility", "source_url", "status",
)

# Two titles on the same source page count as the same programme at or above
# this similarity (difflib ratio on normalised titles).
NEAR_IDENTICAL_TITLE = 0.9
# Rows that share a dedupe_key merge only if their titles are at least this
# similar (or one contains the other). The sweep's dedupe_key is
# funder|domain|deadline, so on its own it cannot tell two programmes on one
# funder site apart.
COMPATIBLE_TITLE = 0.6

_publish_lock = threading.Lock()


class PublishBlocked(Exception):
    """The must-appear check failed; nothing public was written."""

    def __init__(self, missing: list[str], report: dict):
        self.missing = list(missing)
        self.report = report
        super().__init__(f"must-appear funders missing: {', '.join(self.missing)}")


# --- Paths (resolved at call time so GOBUGA_DATA_DIR/test redirects apply) ---

def published_dir(country: str) -> str:
    return os.path.join(tenant.platform_dir(), "published", country)


def _path(country: str, name: str) -> str:
    return os.path.join(published_dir(country), name)


# --- File I/O ---------------------------------------------------------------

def _read_json(path: str, default):
    """Read JSON, or return `default` if the file is missing or unreadable."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_files_atomically(files: dict[str, str]) -> None:
    """Write several files so a failure leaves every one of them untouched.

    Every file is first written in full to a temp file beside its target.
    Only once all temps exist are they moved into place with `os.replace`,
    which is atomic per file, so a reader sees either the old or the new
    version of each file and never a partial one.
    """
    staged: list[tuple[str, str]] = []
    try:
        for path, text in files.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
            staged.append((tmp, path))
            with os.fdopen(fd, "w") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
    except BaseException:
        for tmp, _ in staged:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    for tmp, path in staged:
        os.replace(tmp, path)


def _dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


# Parsed pool per path, keyed by the file's (mtime, size). Public pages read
# the pool on every request; it changes once per publish.
_rows_cache: dict[str, tuple[tuple[int, int], list[dict]]] = {}


def _load_rows(country: str) -> list[dict]:
    path = _path(country, "pool.json")
    try:
        stat = os.stat(path)
    except OSError:
        return []
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _rows_cache.get(path)
    if cached is None or cached[0] != signature:
        data = _read_json(path, {})
        rows = data.get("opportunities") if isinstance(data, dict) else data
        rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        _rows_cache[path] = cached = (signature, rows)
    # A new list each call; the row dicts are shared, so callers copy before
    # changing one (`_with_status` and `_dedupe` both do).
    return list(cached[1])


# --- Dates and status ---------------------------------------------------------

def _tz(country: str) -> ZoneInfo:
    return ZoneInfo(get_country_config(country).timezone)


def _parse_ts(value) -> datetime | None:
    """ISO timestamp -> aware datetime (naive is taken as UTC), or None."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _parse_date(value) -> date | None:
    """Strict `YYYY-MM-DD` -> date, or None (so `rolling`/`TBC` give None)."""
    if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _local_today(country: str, today=None) -> date:
    """The current date in the country's timezone.

    `today` may be a date (used as is) or an aware datetime (converted to the
    country's local date), which is how tests pin the timezone edge.
    """
    if isinstance(today, datetime):
        aware = today if today.tzinfo else today.replace(tzinfo=timezone.utc)
        return aware.astimezone(_tz(country)).date()
    if isinstance(today, date):
        return today
    return datetime.now(_tz(country)).date()


def effective_status(row: dict, today: date, tz: ZoneInfo) -> str:
    """`live`, `closed` or `stale` for a stored row on the given local date.

    A `dated` deadline is live all day on its date, local time. A
    `rolling-confirmed` row goes stale when its verification is more than
    ROLLING_EXPIRY_DAYS old. Anything else (no state, `unresolved`, a dated
    row with no date) is `stale`, so it can never reach a list by accident.
    """
    state = row.get("deadline_state")
    if state == "closed":
        return "closed"
    if state == "dated":
        deadline = _parse_date(row.get("deadline"))
        if deadline is None:
            return "stale"
        return "closed" if deadline < today else "live"
    if state == "rolling-confirmed":
        verified = _parse_ts(row.get("verified_at"))
        if verified is None:
            return "stale"
        age = (today - verified.astimezone(tz).date()).days
        return "stale" if age > ROLLING_EXPIRY_DAYS else "live"
    return "stale"


def _dated_closed_at(row: dict, tz: ZoneInfo) -> str | None:
    """For a dated row: the moment it closed, i.e. local midnight after the
    deadline, in UTC. None when the row has no date."""
    deadline = _parse_date(row.get("deadline"))
    if deadline is None:
        return None
    local_midnight = datetime.combine(deadline + timedelta(days=1), time(0), tzinfo=tz)
    return local_midnight.astimezone(timezone.utc).isoformat()


def _with_status(row: dict, today: date, tz: ZoneInfo) -> dict:
    out = dict(row)
    out["status"] = effective_status(row, today, tz)
    if out["status"] == "closed" and not out.get("closed_at"):
        out["closed_at"] = _dated_closed_at(row, tz)
    return out


# --- Readers ----------------------------------------------------------------

def load_published(country=None, *, today=None, include=("live", "closed")) -> list[dict]:
    """Published rows with their effective status, filtered to `include`.

    Stale rows are left out unless asked for. Empty list if nothing has been
    published.
    """
    country = country or get_country()
    rows = _load_rows(country)
    if not rows:
        return []
    tz = _tz(country)
    local_today = _local_today(country, today)
    wanted = set(include)
    return [r for r in (_with_status(row, local_today, tz) for row in rows)
            if r["status"] in wanted]


def get_published_row(opp_id, country=None, *, today=None) -> dict | None:
    """One published row by ID, whatever its status. None if unknown."""
    country = country or get_country()
    for row in _load_rows(country):
        if row.get("id") == opp_id:
            return _with_status(row, _local_today(country, today), _tz(country))
    return None


def published_meta(country: str | None = None) -> dict:
    """Publish metadata plus counts computed for today."""
    country = country or get_country()
    meta = _read_json(_path(country, "meta.json"), {})
    meta = meta if isinstance(meta, dict) else {}
    rows = load_published(country, include=("live", "closed", "stale"))
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("live", "closed", "stale")}
    listed_funders = {r.get("funder") for r in rows if r["status"] != "stale" and r.get("funder")}
    return {
        "published_at": meta.get("published_at"),
        "sweep_month": meta.get("sweep_month"),
        "run_ts": meta.get("run_ts"),
        "live_count": counts["live"],
        "closed_count": counts["closed"],
        "stale_count": counts["stale"],
        "held_count": meta.get("held_count", 0),
        "funder_count": len(listed_funders),
        "next_sweep_due": meta.get("next_sweep_due"),
        "forced": meta.get("forced", False),
        "forced_by": meta.get("forced_by"),
    }


def load_changes(country: str | None = None) -> list[dict]:
    """Changelog entries, newest first. Empty list if nothing published."""
    country = country or get_country()
    data = _read_json(_path(country, "changes.json"), [])
    return data if isinstance(data, list) else []


def load_held(country: str | None = None) -> dict:
    """The internal held report from the last publish attempt, or {}."""
    country = country or get_country()
    data = _read_json(_path(country, "held.json"), {})
    return data if isinstance(data, dict) else {}


# --- Normalisation for dedupe ---------------------------------------------------

def normalise_title(title: str | None) -> str:
    """Lowercase, fold accents, `&` -> `and`, punctuation to spaces."""
    text = unicodedata.normalize("NFKD", title or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def normalise_url(url: str | None) -> str:
    """Scheme-less, `www.`-less, fragment-less, no trailing slash."""
    if not url or not url.strip():
        return ""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"


def _title_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _titles_near_identical(a: dict, b: dict) -> bool:
    ta, tb = normalise_title(a.get("title")), normalise_title(b.get("title"))
    return bool(ta and tb) and (ta == tb or _title_ratio(ta, tb) >= NEAR_IDENTICAL_TITLE)


def _titles_compatible(a: dict, b: dict) -> bool:
    ta, tb = normalise_title(a.get("title")), normalise_title(b.get("title"))
    if not ta or not tb:
        return True
    return ta in tb or tb in ta or _title_ratio(ta, tb) >= COMPATIBLE_TITLE


def _facts_match(a: dict, b: dict, country: str) -> bool:
    """Same funder, deadline and amounts, with at least one amount stated.

    Two programmes on one council page with no amounts and a `rolling`
    deadline would otherwise "match" on nothing but blanks.
    """
    if a.get("amount_min") is None and a.get("amount_max") is None:
        return False
    return (
        slugify_funder(a.get("funder") or "", country) == slugify_funder(b.get("funder") or "", country)
        and a.get("deadline") == b.get("deadline")
        and a.get("amount_min") == b.get("amount_min")
        and a.get("amount_max") == b.get("amount_max")
    )


# --- Gate --------------------------------------------------------------------

def held_reason(row: dict) -> str | None:
    """Why a candidate row may not be published, or None if it may."""
    state = row.get("deadline_state")
    if not row.get("verified_at"):
        return row.get("unresolved_reason") or "not verified (no verified_at)"
    if state not in PUBLISHABLE_STATES:
        return row.get("unresolved_reason") or f"deadline_state is {state or 'missing'}"
    if state == "dated" and _parse_date(row.get("deadline")) is None:
        return f"deadline_state is dated but deadline is {row.get('deadline')!r}"
    if not (row.get("title") or "").strip() or not (row.get("funder") or "").strip():
        return "missing title or funder"
    return None


# --- Dedupe ------------------------------------------------------------------

class _Groups:
    """Union-find over rows that refuses to join two published rows.

    Published IDs must never disappear, so a group holds at most one row that
    is already in the published dataset.
    """

    def __init__(self, rows: list[dict], published_flags: list[bool]):
        self.parent = list(range(len(rows)))
        self.has_published = list(published_flags)
        self.rules: dict[int, set[str]] = {i: set() for i in range(len(rows))}
        self.refused: list[tuple[int, int, str]] = []

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int, rule: str) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return
        if self.has_published[ri] and self.has_published[rj]:
            self.refused.append((i, j, rule))
            return
        self.parent[rj] = ri
        self.has_published[ri] = self.has_published[ri] or self.has_published[rj]
        self.rules[ri] |= self.rules.pop(rj) | {rule}


def _dedupe_edges(rows: list[dict], published_flags: list[bool], country: str):
    """Yield (i, j, rule) candidate merges, in rule order.

    1. same `dedupe_key`, when the titles are compatible
    2. same normalised title within the same funder
    3. same source URL, when the titles are near-identical or funder,
       deadline and amounts all match

    Pairs of two already-published rows are skipped: they are never merged.
    """
    def pairs(key_fn):
        buckets: dict[str, list[int]] = {}
        for i, row in enumerate(rows):
            key = key_fn(row)
            if key:
                buckets.setdefault(key, []).append(i)
        for members in buckets.values():
            for a_pos, i in enumerate(members):
                for j in members[a_pos + 1:]:
                    if not (published_flags[i] and published_flags[j]):
                        yield i, j

    for i, j in pairs(lambda r: r.get("dedupe_key") or ""):
        yield i, j, ("dedupe_key" if _titles_compatible(rows[i], rows[j]) else "key_conflict")

    def funder_title(r):
        title = normalise_title(r.get("title"))
        return f"{slugify_funder(r.get('funder') or '', country)}|{title}" if title else ""

    for i, j in pairs(funder_title):
        yield i, j, "title"

    for i, j in pairs(lambda r: normalise_url(r.get("source_url"))):
        if _titles_near_identical(rows[i], rows[j]) or _facts_match(rows[i], rows[j], country):
            yield i, j, "source_url"


def _brief(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "funder": row.get("funder"),
        "source_url": row.get("source_url"),
        "deadline": row.get("deadline"),
    }


def _earliest(values) -> str | None:
    parsed = [(ts, v) for v in values if (ts := _parse_ts(v))]
    return min(parsed)[1] if parsed else None


def _latest(values) -> str | None:
    parsed = [(ts, v) for v in values if (ts := _parse_ts(v))]
    return max(parsed)[1] if parsed else None


def _id_sequence(opp_id: str, prefix: str) -> int:
    if not opp_id.startswith(prefix):
        return 0
    tail = opp_id[len(prefix):]
    return int(tail) if tail.isdigit() else 0


def _dedupe(previous: list[dict], candidates: list[dict], country: str, month: str) -> tuple[list[dict], dict]:
    """Merge publishable candidates into the previously published rows.

    Returns (rows, details) where rows is the full cumulative dataset and
    details lists merges, refused merges and key conflicts for the report.
    """
    rows = list(previous) + list(candidates)
    published_flags = [True] * len(previous) + [False] * len(candidates)
    groups = _Groups(rows, published_flags)
    key_conflicts = []
    for i, j, rule in _dedupe_edges(rows, published_flags, country):
        if rule == "key_conflict":
            key_conflicts.append({"rows": [_brief(rows[i]), _brief(rows[j])]})
            continue
        groups.union(i, j, rule)

    members: dict[int, list[int]] = {}
    for i in range(len(rows)):
        members.setdefault(groups.find(i), []).append(i)

    published_ids = {r.get("id") for r in previous if r.get("id")}
    used_ids = set(published_ids)
    prefix = f"OPP-{country.upper()}-{month}-"
    next_seq = max([_id_sequence(i, prefix) for i in used_ids] +
                   [_id_sequence(r.get("id") or "", prefix) for r in candidates] + [0])

    out: list[dict] = []
    merges = []
    for root in sorted(members, key=lambda r: members[r][0]):
        idxs = members[root]
        pub = [i for i in idxs if published_flags[i]]
        # Winner: most recent verified_at; ties go to a fresh candidate, then
        # to input order.
        winner = max(idxs, key=lambda i: (
            _parse_ts(rows[i].get("verified_at")) or datetime.min.replace(tzinfo=timezone.utc),
            not published_flags[i],
            -i,
        ))
        row = dict(rows[winner])

        if pub:
            row["id"] = rows[pub[0]]["id"]
        else:
            # Oldest candidate id not already taken; otherwise a fresh one.
            options = sorted(
                (i for i in idxs if rows[i].get("id") and rows[i]["id"] not in used_ids),
                key=lambda i: (_parse_ts(rows[i].get("first_seen")) or datetime.max.replace(tzinfo=timezone.utc),
                               rows[i]["id"]),
            )
            if options:
                row["id"] = rows[options[0]]["id"]
            else:
                next_seq += 1
                row["id"] = f"{prefix}{next_seq:04d}"
        used_ids.add(row["id"])

        row["first_seen"] = _earliest(rows[i].get("first_seen") for i in idxs) or row.get("first_seen")
        row["last_seen"] = _latest(rows[i].get("last_seen") for i in idxs) or row.get("last_seen")
        if len(idxs) > 1:
            evidence = []
            for i in idxs:
                for ev in rows[i].get("evidence_ids") or []:
                    if ev not in evidence:
                        evidence.append(ev)
            row["evidence_ids"] = evidence
            merges.append({
                "into": row["id"],
                "kept": _brief(rows[winner]),
                "merged": [_brief(rows[i]) for i in idxs if i != winner],
                "rules": sorted(groups.rules.get(root, set())),
            })
        row.pop("unresolved_reason", None)
        row["_from_candidate"] = any(not published_flags[i] for i in idxs)
        out.append(row)

    refused = [
        {"rule": rule, "rows": [_brief(rows[i]), _brief(rows[j])]}
        for i, j, rule in groups.refused
    ]
    return out, {"merges": merges, "refused_merges": refused, "key_conflicts": key_conflicts}


def _shared_url_groups(rows: list[dict]) -> list[dict]:
    """Distinct rows that share a source URL, for a human to look at."""
    by_url: dict[str, list[dict]] = {}
    for row in rows:
        url = normalise_url(row.get("source_url"))
        if url:
            by_url.setdefault(url, []).append(row)
    return [
        {"source_url": group[0].get("source_url"), "rows": [_brief(r) for r in group]}
        for url, group in sorted(by_url.items())
        if len(group) > 1
    ]


# --- Must-appear -----------------------------------------------------------------

def _must_appear_check(rows: list[dict], country: str) -> dict:
    """Which manifest must-appear funders have at least one publishable row.

    Names are compared through `slugify_funder`, which canonicalises aliases
    ("Sport New Zealand" matches "Sport NZ").
    """
    try:
        expected = sources.must_appear_names(country)
    except sources.SourceManifestError:
        expected = []
    present_keys = {slugify_funder(r.get("funder") or "", country) for r in rows}
    present = [n for n in expected if slugify_funder(n, country) in present_keys]
    missing = [n for n in expected if slugify_funder(n, country) not in present_keys]
    return {"expected": expected, "present": present, "missing": missing}


# --- Notifier ------------------------------------------------------------------

def notify_director(country: str, month: str, missing: list[str], held_path: str) -> None:
    """Tell the director a publish was blocked. Logs always; emails only when
    DIRECTOR_EMAIL and RESEND_API_KEY are both set. Never raises."""
    try:
        subject = f"GoBuga {country.upper()} {month}: publish blocked"
        text = (
            f"The {country} sweep for {month} was not published.\n\n"
            f"Must-appear funders missing from the verified rows:\n"
            + "".join(f"- {name}\n" for name in missing)
            + f"\nHeld report: {held_path}\n"
            f"To publish anyway: python -m api.published publish {country} {month} --force --by <name>\n"
        )
        print(f"[published] BLOCKED {country} {month}: missing must-appear funders: "
              f"{', '.join(missing)} (held report: {held_path})")
        to = os.getenv("DIRECTOR_EMAIL")
        if to and os.getenv("RESEND_API_KEY"):
            from api.email import send_text_email
            send_text_email(to, subject, text)
    except Exception as exc:
        print(f"[published] notifier failed: {exc}")


# --- Eligibility entities -----------------------------------------------------

def _entity_parser():
    """Phase 2's parser if it is importable, else a stand-in returning []."""
    try:
        from api.fit import parse_eligible_entities
    except ImportError:
        return lambda text, country: []

    def parse(text, country):
        try:
            return list(parse_eligible_entities(text or "", country))
        except Exception:
            return []
    return parse


# --- Changelog -------------------------------------------------------------------

def _diff(previous: list[dict], current: list[dict]) -> dict:
    """new / changed / closed between two stored pools (stored statuses)."""
    old = {r["id"]: r for r in previous if r.get("id")}
    new_ids, changed, closed = [], [], []
    for row in current:
        prior = old.get(row["id"])
        if prior is None:
            new_ids.append(row["id"])
            continue
        fields = [f for f in FACT_FIELDS if prior.get(f) != row.get(f)]
        just_closed = row.get("status") == "closed" and prior.get("status") != "closed"
        if just_closed:
            closed.append(row["id"])
        # A closure is reported under `closed`; list it under `changed` too
        # only if some other fact also changed.
        if fields and not (just_closed and fields == ["status"]):
            changed.append({"id": row["id"], "fields": fields})
    return {"new": new_ids, "changed": changed, "closed": closed}


def _add_months(month: str, n: int) -> str:
    y, m = (int(p) for p in month.split("-"))
    total = y * 12 + (m - 1) + n
    return f"{total // 12}-{total % 12 + 1:02d}"


# --- Publish -------------------------------------------------------------------

def publish_pool(
    country,
    month,
    candidates,
    *,
    now=None,
    force=False,
    forced_by: str | None = None,
    run_ts: str | None = None,
    notifier=None,
) -> dict:
    """Publish a sweep's candidate rows into the cumulative public dataset.

    Order: gate (unresolved rows are held), dedupe against the published
    rows, status at `now`, must-appear check, then one atomic write of
    pool, changelog, meta and held report.

    Raises PublishBlocked, having written only `held.json` and called the
    notifier, when a must-appear funder has no publishable row and `force`
    is false. Returns a report dict otherwise.
    """
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    notifier = notifier or notify_director
    tz = _tz(country)
    local_today = _local_today(country, now)

    with _publish_lock:
        previous = _load_rows(country)

        publishable, held = [], []
        for row in candidates:
            reason = held_reason(row)
            if reason:
                held.append({**_brief(row), "deadline_state": row.get("deadline_state"),
                             "reason": reason})
            else:
                publishable.append(row)

        rows, details = _dedupe(previous, publishable, country, month)

        parse_entities = _entity_parser()
        for row in rows:
            from_candidate = row.pop("_from_candidate")
            if from_candidate or "eligible_entities" not in row:
                row["eligible_entities"] = parse_entities(row.get("eligibility"), country)
            row["status"] = effective_status(row, local_today, tz)
            if row["status"] == "closed":
                if not row.get("closed_at"):
                    row["closed_at"] = (_dated_closed_at(row, tz)
                                        if row.get("deadline_state") == "dated" else now_iso)
            else:
                row["closed_at"] = None
        rows.sort(key=lambda r: r["id"])

        must_appear = _must_appear_check(publishable, country)
        changes = _diff(previous, rows)
        counts = {
            "candidates": len(candidates),
            "publishable": len(publishable),
            "held": len(held),
            "published_total": len(rows),
            "new": len(changes["new"]),
            "changed": len(changes["changed"]),
            "closed": len(changes["closed"]),
            "merges": len(details["merges"]),
            **{s: sum(1 for r in rows if r["status"] == s) for s in ("live", "stale")},
            "closed_total": sum(1 for r in rows if r["status"] == "closed"),
        }
        blocked = bool(must_appear["missing"]) and not force
        report = {
            "country": country,
            "sweep_month": month,
            "run_ts": run_ts,
            "generated_at": now_iso,
            "published": not blocked,
            "forced": bool(force),
            "forced_by": forced_by if force else None,
            "must_appear": must_appear,
            "counts": counts,
            "changes": changes,
            "held": held,
            "shared_url_groups": _shared_url_groups(rows),
            **details,
        }
        held_path = _path(country, "held.json")

        if blocked:
            _write_files_atomically({held_path: _dumps(report)})
            try:
                notifier(country, month, must_appear["missing"], held_path)
            except Exception as exc:
                print(f"[published] notifier failed: {exc}")
            raise PublishBlocked(must_appear["missing"], report)

        entry = {"published_at": now_iso, "sweep_month": month, **changes}
        meta = {
            "published_at": now_iso,
            "sweep_month": month,
            "run_ts": run_ts,
            "held_count": len(held),
            "next_sweep_due": _add_months(month, SWEEP_INTERVAL_MONTHS),
            "forced": bool(force),
            "forced_by": forced_by if force else None,
            "missing_must_appear": must_appear["missing"],
        }
        _write_files_atomically({
            _path(country, "pool.json"): _dumps({"country": country, "opportunities": rows}),
            _path(country, "changes.json"): _dumps([entry] + load_changes(country)),
            _path(country, "meta.json"): _dumps(meta),
            held_path: _dumps(report),
        })
        if force and must_appear["missing"]:
            print(f"[published] FORCED publish {country} {month} by {forced_by or 'unknown'} "
                  f"despite missing: {', '.join(must_appear['missing'])}")
        return report


# --- CLI ---------------------------------------------------------------------

def _swept_rows(country: str, month: str) -> tuple[list[dict], str]:
    """The run's verified rows if the sweep saved them, else its workspace pool."""
    from api.opportunities import load_pool
    latest = os.path.join(tenant.platform_cycles_dir(country, month), "latest")
    verified_path = os.path.join(latest, "verified.json")
    data = _read_json(verified_path, None)
    if isinstance(data, dict) and isinstance(data.get("opportunities"), list):
        return data["opportunities"], verified_path
    return load_pool(country, month), os.path.join(latest, "opportunities.json")


def _print_summary(report: dict) -> None:
    c = report["counts"]
    print(f"  candidates {c['candidates']}, publishable {c['publishable']}, held {c['held']}")
    print(f"  dataset: {c['published_total']} rows ({c['live']} live, "
          f"{c['closed_total']} closed, {c['stale']} stale)")
    print(f"  changes: {c['new']} new, {c['changed']} changed, {c['closed']} closed; "
          f"{c['merges']} merges")
    ma = report["must_appear"]
    print(f"  must-appear: {len(ma['present'])}/{len(ma['expected'])} present"
          + (f"; missing: {', '.join(ma['missing'])}" if ma["missing"] else ""))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = ("Usage:\n  python -m api.published status <country>\n"
             "  python -m api.published publish <country> <YYYY-MM> [--force] [--by NAME]")
    force = "--force" in argv
    forced_by = None
    if "--by" in argv:
        pos = argv.index("--by")
        if pos + 1 >= len(argv):
            print(usage)
            return 2
        forced_by = argv[pos + 1]
        del argv[pos:pos + 2]
    args = [a for a in argv if a != "--force"]

    if len(args) == 2 and args[0] == "status":
        print(json.dumps(published_meta(args[1]), indent=2))
        return 0

    if len(args) == 3 and args[0] == "publish":
        country, month = args[1], args[2]
        rows, source = _swept_rows(country, month)
        if not rows:
            print(f"No swept rows found at {source}")
            return 1
        unverified = sum(1 for r in rows if not r.get("verified_at"))
        print(f"Publishing {len(rows)} rows from {source}")
        if unverified:
            print(f"  {unverified} rows have no verification fields: they are unresolved "
                  f"and will be held, not published")
        try:
            report = publish_pool(country, month, rows, force=force, forced_by=forced_by)
        except PublishBlocked as exc:
            print(f"Publish BLOCKED: {exc}")
            _print_summary(exc.report)
            print(f"  held report: {_path(country, 'held.json')}")
            return 1
        print("Published" + (" (forced)" if report["forced"] else ""))
        _print_summary(report)
        return 0

    print(usage)
    return 2


if __name__ == "__main__":
    sys.exit(main())
