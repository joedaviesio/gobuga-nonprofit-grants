"""Deadline resolution pass — runs between the analyst and the deterministic
opportunity builder. For any analyst item whose deadline is `TBC` or `rolling`
*and* whose source URL is reachable, fetch the page and ask Haiku to extract a
date. Updates the item's parsed content in-flight (evidence files on disk are
not modified — they're an immutable record of what the analyst said).
"""

import json
import re
from datetime import date, datetime, timezone

import anthropic
from dotenv import load_dotenv

from api.usage import DEFAULT_MODEL, finalize_usage, new_usage, track_usage
from api.usage_log import log_usage
from orchestrator.tools import handle_web_fetch

load_dotenv()


_VAGUE = {"tbc", "rolling", "", "unknown", "none"}
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_content(content: str) -> dict | None:
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


def _is_vague(deadline: str | None) -> bool:
    if not deadline:
        return True
    return str(deadline).strip().lower() in _VAGUE


def _ask_for_deadline(client: anthropic.Anthropic, page_text: str, programme: str, today: str) -> tuple[str, dict]:
    """Call Haiku to extract a deadline. Returns (deadline, usage_dict)."""
    system = (
        "You extract grant application deadlines from web pages. "
        f"Today is {today}. "
        "Output exactly one of: an ISO date `YYYY-MM-DD` (the next upcoming deadline), "
        "or the literal word `rolling` (continuously open, no fixed deadline), "
        "or the literal word `TBC` (no extractable deadline on the page). "
        "Output the value and nothing else — no prose, no quotes, no commentary."
    )
    msg = (
        f"Programme: {programme}\n\n"
        f"Page text (truncated):\n{page_text[:6000]}"
    )
    usage = new_usage()
    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=20,
        system=system,
        messages=[{"role": "user", "content": msg}],
    )
    track_usage(usage, response)
    raw = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            raw += block.text
    answer = raw.strip().strip("`").strip()
    if _ISO_RE.match(answer):
        # Reject dates in the past
        try:
            if datetime.fromisoformat(answer).date() < datetime.now(timezone.utc).date():
                answer = "TBC"
        except ValueError:
            answer = "TBC"
    elif answer.lower() == "rolling":
        answer = "rolling"
    else:
        answer = "TBC"
    return answer, finalize_usage(usage, DEFAULT_MODEL)


def resolve_deadlines(
    analyst_evidence: list[dict],
    org_id_for_usage: str = "_platform",
    cycle_date: str | None = None,
    max_resolutions: int = 50,
) -> tuple[list[dict], dict]:
    """Walk analyst evidence, resolve vague deadlines via web_fetch + Haiku.

    Returns (updated_evidence, stats). The original list is not mutated; new
    dicts are produced for items whose deadline changed.
    """
    cycle_date = cycle_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_str = date.today().isoformat()
    client = anthropic.Anthropic()
    out: list[dict] = []
    resolved_count = 0
    rolling_confirmed = 0
    still_tbc = 0
    fetch_failures = 0
    api_calls_total = 0
    cost_total = 0.0

    for ev in analyst_evidence:
        if resolved_count >= max_resolutions:
            out.append(ev)
            continue

        parsed = _parse_content(ev.get("content", ""))
        if not parsed:
            out.append(ev)
            continue

        deadline = parsed.get("deadline")
        url = ev.get("source_url") or parsed.get("source_url") or ""
        if not _is_vague(deadline) or not url:
            out.append(ev)
            continue

        # Fetch the page
        page_text = handle_web_fetch({"url": url})
        if not page_text or page_text.startswith("Error fetching"):
            fetch_failures += 1
            out.append(ev)
            continue

        programme = parsed.get("programme") or parsed.get("title") or ev.get("title", "")
        try:
            new_dl, usage = _ask_for_deadline(client, page_text, programme, today_str)
            api_calls_total += usage.get("api_calls", 1)
            cost_total += usage.get("cost_usd", 0.0)
        except Exception:
            out.append(ev)
            continue

        if new_dl == "TBC":
            still_tbc += 1
            out.append(ev)
            continue

        if new_dl == "rolling":
            rolling_confirmed += 1
            if str(deadline or "").strip().lower() == "rolling":
                # Already rolling; no change
                out.append(ev)
                continue

        # Update the parsed content with the new deadline; keep the original
        # evidence record intact and produce a new dict with updated content.
        new_parsed = dict(parsed)
        new_parsed["deadline"] = new_dl
        new_parsed["deadline_resolved"] = True
        new_ev = dict(ev)
        new_ev["content"] = json.dumps(new_parsed, indent=2, ensure_ascii=False)
        out.append(new_ev)
        resolved_count += 1

    log_usage(org_id_for_usage, "deadline_resolver", {
        "input_tokens": 0,
        "output_tokens": 0,
        "api_calls": api_calls_total,
        "cost_usd": round(cost_total, 4),
        "model": DEFAULT_MODEL,
    }, cycle_date=cycle_date) if api_calls_total else None

    stats = {
        "resolved_to_date": resolved_count,
        "rolling_confirmed": rolling_confirmed,
        "still_tbc": still_tbc,
        "fetch_failures": fetch_failures,
        "api_calls": api_calls_total,
        "cost_usd": round(cost_total, 4),
    }
    return out, stats


def resolve_deadlines_for_rows(
    rows: list[dict],
    org_id_for_usage: str = "_platform",
    cycle_date: str | None = None,
    max_resolutions: int = 100,
) -> tuple[list[dict], dict]:
    """Like `resolve_deadlines` but operates on opportunity rows directly.

    Walks rows, fetches `source_url` for any row with a vague deadline, asks
    Haiku to extract a date. Updates the row's `deadline` in place (and
    rebuilds `dedupe_key` so cross-month merge works correctly next month).

    Returns (updated_rows, stats). The original list is not mutated.
    """
    cycle_date = cycle_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_str = date.today().isoformat()
    client = anthropic.Anthropic()
    out: list[dict] = []
    resolved = rolling_confirmed = still_tbc = fetch_failures = api_calls = 0
    cost_total = 0.0

    for row in rows:
        if resolved >= max_resolutions:
            out.append(row)
            continue

        deadline = row.get("deadline") or ""
        url = row.get("source_url") or ""
        if not _is_vague(deadline) or not url:
            out.append(row)
            continue

        page = handle_web_fetch({"url": url})
        if not page or page.startswith("Error fetching"):
            fetch_failures += 1
            out.append(row)
            continue

        programme = row.get("title") or row.get("funder") or ""
        try:
            new_dl, usage = _ask_for_deadline(client, page, programme, today_str)
            api_calls += usage.get("api_calls", 1)
            cost_total += usage.get("cost_usd", 0.0)
        except Exception:
            out.append(row)
            continue

        if new_dl == "TBC":
            still_tbc += 1
            out.append(row)
            continue

        if new_dl == "rolling":
            rolling_confirmed += 1
            if str(deadline).strip().lower() == "rolling":
                out.append(row)
                continue

        new_row = dict(row)
        new_row["deadline"] = new_dl
        # Rebuild dedupe_key so next month's merge matches correctly
        if "dedupe_key" in new_row:
            parts = new_row["dedupe_key"].rsplit("|", 1)
            if len(parts) == 2:
                new_row["dedupe_key"] = f"{parts[0]}|{new_dl}".lower()
        out.append(new_row)
        resolved += 1

    if api_calls:
        log_usage(org_id_for_usage, "deadline_resolver", {
            "input_tokens": 0,
            "output_tokens": 0,
            "api_calls": api_calls,
            "cost_usd": round(cost_total, 4),
            "model": DEFAULT_MODEL,
        }, cycle_date=cycle_date)

    stats = {
        "resolved_to_date": resolved,
        "rolling_confirmed": rolling_confirmed,
        "still_tbc": still_tbc,
        "fetch_failures": fetch_failures,
        "api_calls": api_calls,
        "cost_usd": round(cost_total, 4),
    }
    return out, stats
