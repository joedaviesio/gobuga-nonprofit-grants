"""Verify pass — re-read every candidate row's source page before it can publish.

Runs in the sweep after deadline resolution. For each row it fetches the
row's `source_url` (once per distinct URL), asks a cheap model to read the
page and answer in strict JSON, and checks that answer against the page:

- the deadline excerpt must occur in the fetched text (whitespace and case
  normalised), or the row is `unresolved`. This is the defence against a
  model inventing a deadline;
- a date must parse, be plausible (not more than about two years ahead, not
  before the year before the row was first seen) and its day of the month
  must appear in the excerpt;
- a date already past means the round is `closed`.

The row gets `deadline_state`, `verified_at`, `verified_by`,
`source_excerpt` and `provenance`. A fetch failure, an unparseable answer or
a page that does not mention the programme leaves it `unresolved` with an
`unresolved_reason`, and the publish gate holds it back.

`fetch` and `ask` are parameters so tests inject fakes; the defaults make
real network and Anthropic calls and are only used by a real sweep.
"""

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from api.country_config import get_country, get_country_config
from api.usage import finalize_usage, new_usage, track_usage
from api.usage_log import log_usage
from orchestrator.config import MODEL_VERIFY_COUNTRY
from orchestrator.deadline_resolver import _parse_content
from orchestrator.tools import handle_web_fetch


MAX_CONCURRENT = 4
MAX_PAGE_CHARS = 12000
FETCH_TIMEOUT_S = 15
MAX_DAYS_AHEAD = 730          # about two years
MIN_EXCERPT_CHARS = 15        # a bare "2026" or "rolling" proves nothing

_STATES = {"dated": "dated", "rolling": "rolling-confirmed", "closed": "closed"}

SYSTEM_PROMPT = (
    "You check one grant programme against the text of its funder's web page. "
    "Today is {today}. Answer with a single JSON object and nothing else:\n"
    "{{\n"
    '  "mentions_programme": true or false,\n'
    '  "deadline_state": "dated" | "rolling" | "closed" | "unknown",\n'
    '  "deadline": "YYYY-MM-DD" or null,\n'
    '  "deadline_excerpt": "..." or null,\n'
    '  "funder_confirmed": true or false,\n'
    '  "funder_excerpt": "..." or null,\n'
    '  "amount_confirmed": true or false,\n'
    '  "amount_excerpt": "..." or null,\n'
    '  "eligibility_excerpt": "..." or null\n'
    "}}\n"
    "Rules:\n"
    "- mentions_programme: the page describes this programme (or clearly the same fund).\n"
    "- dated: the page states the next closing date for this programme; give it as deadline.\n"
    "- rolling: the page says applications are accepted at any time or continuously.\n"
    "- closed: the page says this round or programme is closed, or its only dates are past.\n"
    "- unknown: anything else. Never guess or infer a date the page does not state.\n"
    "- Every excerpt must be copied character for character from the page text: one or "
    "two sentences, no paraphrase, no ellipses. deadline_excerpt must contain the stated "
    "date or the words that make it rolling or closed.\n"
    "- funder_confirmed / amount_confirmed: the page names this funder / states this amount."
)


class FetchError(Exception):
    pass


# --- Production defaults (never called by tests) --------------------------------

def default_fetch(url: str, timeout: float) -> str:
    text = handle_web_fetch({"url": url}, timeout=timeout, max_chars=MAX_PAGE_CHARS)
    if text.startswith("Error fetching"):
        raise FetchError(text)
    return text


_client = None


def _anthropic_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def make_default_ask(model: str = MODEL_VERIFY_COUNTRY):
    """An `ask(page_text, row, today)` that calls Anthropic. Returns
    (raw answer text, usage dict)."""
    def ask(page_text: str, row: dict, today: str) -> tuple[str, dict]:
        usage = new_usage()
        response = _anthropic_client().messages.create(
            model=model,
            max_tokens=600,
            system=SYSTEM_PROMPT.format(today=today),
            messages=[{"role": "user", "content": build_user_message(page_text, row)}],
        )
        track_usage(usage, response)
        raw = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        return raw, finalize_usage(usage, model)
    return ask


def build_user_message(page_text: str, row: dict) -> str:
    amount = row.get("amount_max") or row.get("amount_min")
    return (
        f"Programme: {row.get('title') or ''}\n"
        f"Funder: {row.get('funder') or ''}\n"
        f"Amount as listed: {amount if amount is not None else 'not stated'}\n\n"
        f"Page text:\n{page_text}"
    )


# --- Validation (pure) ------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _verbatim(excerpt, page_norm: str) -> str | None:
    """The excerpt, whitespace-tidied, if it really occurs in the page."""
    if not isinstance(excerpt, str):
        return None
    tidy = re.sub(r"\s+", " ", excerpt).strip()
    if len(tidy) < MIN_EXCERPT_CHARS or tidy.casefold() not in page_norm:
        return None
    return tidy


def _plausible_date(raw, row: dict, today: date) -> tuple[date | None, str]:
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw.strip()):
        return None, f"deadline {raw!r} is not a YYYY-MM-DD date"
    try:
        d = date.fromisoformat(raw.strip())
    except ValueError:
        return None, f"deadline {raw!r} is not a real date"
    if d > today + timedelta(days=MAX_DAYS_AHEAD):
        return None, f"deadline {d} is implausibly far ahead"
    first_seen = row.get("first_seen") or ""
    first_year = int(first_seen[:4]) if first_seen[:4].isdigit() else today.year
    if d.year < first_year - 1:
        return None, f"deadline {d} is implausibly old"
    return d, ""


def _day_in(excerpt: str, d: date) -> bool:
    """The deadline's day of the month appears as a number in the excerpt.
    Language-neutral: works for "30 November", "30.11.2026", "noiembrie 30"."""
    return re.search(rf"(?<!\d)0?{d.day}(?!\d)", excerpt) is not None


def interpret_answer(answer, page_text: str, row: dict, today: date) -> tuple[dict | None, str]:
    """Check a parsed model answer against the page.

    Returns (fields, "") when the row verifies, where fields has
    `deadline_state`, `deadline`, `excerpt` and optional `funder_excerpt`,
    `amount_excerpt`, `eligibility_excerpt`; or (None, reason).
    """
    if not isinstance(answer, dict):
        return None, "model answer was not a JSON object"
    if answer.get("mentions_programme") is not True:
        return None, "page does not mention the programme"
    state = answer.get("deadline_state")
    if state == "unknown":
        return None, "page gives no deadline"
    if state not in _STATES:
        return None, f"invalid deadline_state {state!r}"

    page_norm = _norm(page_text)
    excerpt = _verbatim(answer.get("deadline_excerpt"), page_norm)
    if excerpt is None:
        return None, "deadline excerpt not found in page"

    deadline = None
    if state == "dated" or (state == "closed" and answer.get("deadline")):
        deadline, why = _plausible_date(answer.get("deadline"), row, today)
        if deadline is not None and not _day_in(excerpt, deadline):
            deadline, why = None, "excerpt does not state the deadline date"
        if deadline is None and state == "dated":
            return None, why
    if state == "dated" and deadline < today:
        state = "closed"

    if deadline is not None:
        deadline_text = deadline.isoformat()
    elif state == "rolling":
        deadline_text = "rolling"
    else:
        deadline_text = row.get("deadline") or "TBC"

    fields = {"deadline_state": _STATES[state], "deadline": deadline_text, "excerpt": excerpt}
    for name, flag in (("funder", "funder_confirmed"), ("amount", "amount_confirmed"),
                       ("eligibility", None)):
        if flag and answer.get(flag) is not True:
            continue
        found = _verbatim(answer.get(f"{name}_excerpt"), page_norm)
        if found:
            fields[f"{name}_excerpt"] = found
    return fields, ""


# --- Row updates --------------------------------------------------------------------

def _rekey(row: dict, deadline: str) -> None:
    """Rebuild the trailing deadline segment of `dedupe_key`, as the deadline
    resolver does, so rows for the same programme keep matching."""
    parts = (row.get("dedupe_key") or "").rsplit("|", 1)
    if len(parts) == 2:
        row["dedupe_key"] = f"{parts[0]}|{deadline}".lower()


def _verified_row(row: dict, fields: dict, now_iso: str, verified_by: str) -> dict:
    new = dict(row)
    new.pop("unresolved_reason", None)
    if fields["deadline"] != row.get("deadline"):
        new["deadline"] = fields["deadline"]
        _rekey(new, fields["deadline"])
    new["deadline_state"] = fields["deadline_state"]
    new["verified_at"] = now_iso
    new["verified_by"] = verified_by
    new["source_excerpt"] = fields["excerpt"]
    url = row.get("source_url")
    new["provenance"] = {"deadline": {"source_url": url, "verified_at": now_iso,
                                      "excerpt": fields["excerpt"]}}
    for name in ("funder", "amount", "eligibility"):
        if fields.get(f"{name}_excerpt"):
            new["provenance"][name] = {"source_url": url, "verified_at": now_iso,
                                       "excerpt": fields[f"{name}_excerpt"]}
    return new


def _unresolved_row(row: dict, reason: str) -> dict:
    new = dict(row)
    new.update({
        "deadline_state": "unresolved",
        "verified_at": None,
        "verified_by": None,
        "source_excerpt": None,
        "provenance": {},
        "unresolved_reason": reason,
    })
    return new


# --- The pass ---------------------------------------------------------------------

def verify_rows(
    rows: list[dict],
    *,
    country: str | None = None,
    fetch=None,
    ask=None,
    model: str = MODEL_VERIFY_COUNTRY,
    now: datetime | None = None,
    max_concurrent: int = MAX_CONCURRENT,
    max_page_chars: int = MAX_PAGE_CHARS,
    fetch_timeout: float = FETCH_TIMEOUT_S,
    org_id_for_usage: str = "_platform",
    cycle_date: str | None = None,
) -> tuple[list[dict], dict]:
    """Verify every row against its source page.

    Returns (rows, stats) in input order. The input list and its dicts are
    not mutated. `fetch(url, timeout) -> text` and
    `ask(page_text, row, today_iso) -> (raw_answer, usage)` default to the
    real network and Anthropic calls.
    """
    fetch = fetch or default_fetch
    ask = ask or make_default_ask(model)
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    country = country or get_country()
    today = now.astimezone(ZoneInfo(get_country_config(country).timezone)).date()
    verified_by = f"verify-pass/{model}"

    urls = list(dict.fromkeys(r.get("source_url") for r in rows if r.get("source_url")))

    def fetch_one(url: str) -> tuple[str | None, str]:
        try:
            text = fetch(url, fetch_timeout)
        except Exception as exc:
            return None, f"fetch failed: {exc}"
        if not text or not text.strip():
            return None, "fetch returned an empty page"
        return text[:max_page_chars], ""

    def check(row: dict) -> tuple[dict, dict | None]:
        url = row.get("source_url")
        if not url:
            return _unresolved_row(row, "no source_url"), None
        page, error = pages[url]
        if page is None:
            return _unresolved_row(row, error), None
        try:
            raw, usage = ask(page, row, today.isoformat())
        except Exception as exc:
            return _unresolved_row(row, f"model call failed: {exc}"), None
        fields, reason = interpret_answer(_parse_content(raw or ""), page, row, today)
        if fields is None:
            return _unresolved_row(row, reason), usage
        return _verified_row(row, fields, now_iso, verified_by), usage

    with ThreadPoolExecutor(max_workers=max(1, max_concurrent)) as pool:
        pages = dict(zip(urls, pool.map(fetch_one, urls)))
        results = list(pool.map(check, rows))

    out = [r for r, _ in results]
    usage_total = new_usage()
    for _, usage in results:
        if usage:
            for key in ("input_tokens", "output_tokens", "api_calls"):
                usage_total[key] += usage.get(key, 0)
            usage_total["cost_usd"] += usage.get("cost_usd", 0.0)
    usage_total["cost_usd"] = round(usage_total["cost_usd"], 4)
    usage_total["model"] = model

    if usage_total["api_calls"]:
        log_usage(org_id_for_usage, "verify_pass", usage_total,
                  cycle_date=cycle_date or now.strftime("%Y-%m-%d"))

    states = Counter(r["deadline_state"] for r in out)
    reasons = Counter(r["unresolved_reason"].split(":")[0] for r in out
                      if r.get("unresolved_reason"))
    stats = {
        "rows": len(rows),
        "distinct_urls": len(urls),
        "fetches": len(urls),
        "fetch_failures": sum(1 for page, _ in pages.values() if page is None),
        "dated": states["dated"],
        "rolling_confirmed": states["rolling-confirmed"],
        "closed": states["closed"],
        "unresolved": states["unresolved"],
        "unresolved_reasons": dict(reasons),
        "excerpt_rejected": reasons["deadline excerpt not found in page"],
        "funder_confirmed": sum(1 for r in out if "funder" in (r.get("provenance") or {})),
        "amount_confirmed": sum(1 for r in out if "amount" in (r.get("provenance") or {})),
        "api_calls": usage_total["api_calls"],
        "input_tokens": usage_total["input_tokens"],
        "output_tokens": usage_total["output_tokens"],
        "cost_usd": usage_total["cost_usd"],
        "model": model,
    }
    return out, stats
