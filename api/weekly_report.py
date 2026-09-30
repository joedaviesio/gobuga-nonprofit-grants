"""The weekly traffic email: one plain-text message per deployment.

The director is still learning which numbers matter, so the email leads
with what the site is for (assistants reading and citing it), then the
crawlers that feed them, then people, then the dataset's health. Raw
totals come last. Each count is shown against the week before.

Sends on Monday morning in the country's own timezone, once, however
often the container restarts: a marker file under the metrics folder
records the last week sent. `WEEKLY_REPORT_DISABLED=1` turns the
scheduler off; `POST /api/admin/weekly-report` sends on demand.

The address comes from `REPORT_EMAIL`, else `DIRECTOR_EMAIL`. With
neither set the text is printed and nothing is sent.
"""

import os
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from api import metrics
from api.country_config import get_country, get_country_config
from api.tenant import platform_dir

# Monday, local time. A later hour lets the night's crawls settle in the file.
SEND_WEEKDAY = 0
SEND_HOUR = 7
_CHECK_EVERY_SECONDS = 30 * 60

_ASSISTANT_AGENTS = ("gptbot", "claudebot", "perplexitybot")
_TRAINING_AGENTS = ("gptbot", "claudebot", "google-extended", "ccbot")
_SEARCH_AGENTS = ("googlebot", "bingbot", "gptbot", "claudebot", "perplexitybot")
_AGENT_LABELS = {
    "gptbot": "OpenAI", "claudebot": "Anthropic", "perplexitybot": "Perplexity",
    "google-extended": "Google-Extended", "ccbot": "Common Crawl",
    "googlebot": "Googlebot", "bingbot": "Bingbot", "other-bot": "other bots",
}


# --- Counting a window ---

def _parse_ts(value) -> datetime | None:
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _months(start: datetime, end: datetime) -> list[str]:
    """The month files a window can touch, oldest first."""
    out, cur = [], date(start.year, start.month, 1)
    while (cur.year, cur.month) <= (end.year, end.month):
        out.append(cur.strftime("%Y-%m"))
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


def _lines_between(country: str, start: datetime, end: datetime):
    for month in _months(start, end):
        for row in metrics._read_lines(metrics.metrics_path(country, month)):
            ts = _parse_ts(row.get("ts"))
            if ts is not None and start <= ts < end:
                yield row


def window_counts(country: str, start: datetime, end: datetime) -> dict:
    """The week's numbers, from the lines whose timestamp falls in [start, end)."""
    fetch: Counter = Counter()
    training: Counter = Counter()
    search: Counter = Counter()
    hits_by_agent: Counter = Counter()
    human_by_surface: Counter = Counter()
    human_by_ref: Counter = Counter()
    assistant_hosts: Counter = Counter()
    clickouts_by_ref: Counter = Counter()
    clickout_funders: Counter = Counter()
    api_calls = mcp_calls = hits = 0

    for row in _lines_between(country, start, end):
        agent = metrics._field(row, "agent")
        ref = metrics._field(row, "ref")
        if row.get("kind") == "clickout":
            clickouts_by_ref[ref] += 1
            clickout_funders[metrics._field(row, "funder")] += 1
            continue
        if row.get("kind") != "hit":
            continue
        hits += 1
        hits_by_agent[agent] += 1
        surface = metrics._field(row, "surface")
        if surface == "api":
            api_calls += 1
        elif surface == "mcp":
            mcp_calls += 1
        role = row.get("role")
        if role not in metrics.AGENT_ROLES:
            ua = row.get("ua")
            role = metrics.classify_role(ua) if isinstance(ua, str) else None
        if role == "fetch":
            fetch[agent] += 1
        elif role == "training":
            training[agent] += 1
        elif role == "search":
            search[agent] += 1
        if agent == "human":
            human_by_surface[surface] += 1
            human_by_ref[ref] += 1
            if ref == "assistant":
                assistant_hosts[metrics._field(row, "ref_host") or "unknown"] += 1

    return {
        "hits": hits,
        "assistant_fetches": dict(fetch),
        "assistant_referrals": human_by_ref.get("assistant", 0),
        "assistant_referral_hosts": dict(assistant_hosts),
        "assistant_clickouts": clickouts_by_ref.get("assistant", 0),
        "training": dict(training),
        "search": dict(search),
        "hits_by_agent": dict(hits_by_agent),
        "human_pages": human_by_surface.get("page", 0),
        "human_from_search": human_by_ref.get("search", 0),
        "human_from_email": human_by_ref.get("email", 0),
        "clickouts": sum(clickouts_by_ref.values()),
        "clickouts_by_ref": dict(clickouts_by_ref),
        "top_clickout_funders": clickout_funders.most_common(5),
        "api_calls": api_calls,
        "mcp_calls": mcp_calls,
    }


def all_time_before(country: str, end: datetime) -> dict:
    """Whether each headline event had ever happened before `end`, so the
    email can say "first ever"."""
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    seen = {"assistant_fetch": False, "assistant_referral": False, "assistant_clickout": False}
    for row in _lines_between(country, start, end):
        ref = metrics._field(row, "ref")
        if row.get("kind") == "clickout":
            if ref == "assistant":
                seen["assistant_clickout"] = True
            continue
        role = row.get("role")
        if role not in metrics.AGENT_ROLES:
            ua = row.get("ua")
            role = metrics.classify_role(ua) if isinstance(ua, str) else None
        if role == "fetch":
            seen["assistant_fetch"] = True
        if ref == "assistant" and metrics._field(row, "agent") == "human":
            seen["assistant_referral"] = True
        if all(seen.values()):
            break
    return seen


# --- The text ---

def _n(value: int) -> str:
    return f"{value:,}"


def _pair(now_value: int, before_value: int) -> str:
    return f"{_n(now_value)} ({_n(before_value)})"


def _by_agent(now_counts: dict, before_counts: dict, agents) -> list[str]:
    lines = []
    for agent in agents:
        a, b = now_counts.get(agent, 0), before_counts.get(agent, 0)
        if a or b:
            lines.append(f"  - {_AGENT_LABELS.get(agent, agent)}: {_pair(a, b)}")
    return lines or ["  - none"]


def week_bounds(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The last whole Monday-to-Sunday week before `now`, in UTC."""
    local = now.astimezone(tz)
    this_monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = this_monday - timedelta(days=7)
    return start.astimezone(timezone.utc), this_monday.astimezone(timezone.utc)


def compose(country: str | None = None, now: datetime | None = None) -> tuple[str, str]:
    """(subject, body) for the last whole week."""
    country = country or get_country()
    cfg = get_country_config(country)
    tz = ZoneInfo(cfg.timezone)
    now = now or datetime.now(timezone.utc)
    start, end = week_bounds(now, tz)
    week = window_counts(country, start, end)
    before = window_counts(country, start - timedelta(days=7), start)
    ever = all_time_before(country, start)

    from api.published import published_meta
    try:
        data = published_meta(country)
    except Exception as exc:  # noqa: BLE001 — the email must still go out
        data = {"error": f"{type(exc).__name__}: {exc}"}

    fetches = sum(week["assistant_fetches"].values())
    fetches_before = sum(before["assistant_fetches"].values())
    site = (os.getenv("APP_URL") or "").rstrip("/")
    label = f"{cfg.country_label} ({site})" if site else cfg.country_label
    # Wall-clock arithmetic, so a daylight-saving change in the week does
    # not shift the last day back to Saturday.
    last_day = end.astimezone(tz) - timedelta(days=1)
    week_label = f"{start.astimezone(tz):%a %d %b} to {last_day:%a %d %b %Y}"

    firsts = []
    if fetches and not ever["assistant_fetch"]:
        firsts.append("First ever: an assistant fetched a page to answer someone.")
    if week["assistant_referrals"] and not ever["assistant_referral"]:
        firsts.append("First ever: a person arrived from an assistant's answer.")
    if week["assistant_clickouts"] and not ever["assistant_clickout"]:
        firsts.append("First ever: a person sent by an assistant clicked through to a funder.")

    lines = [
        f"GoBuga {label}, week {week_label}",
        "Each number has the week before in brackets.",
        "",
    ]
    lines += [f"** {f}" for f in firsts]
    if firsts:
        lines.append("")
    lines += [
        "ASSISTANTS",
        f"- Pages fetched live by an assistant to answer a question: {_pair(fetches, fetches_before)}",
        *_by_agent(week["assistant_fetches"], before["assistant_fetches"], _ASSISTANT_AGENTS),
        f"- People arriving from an assistant's answer: {_pair(week['assistant_referrals'], before['assistant_referrals'])}",
    ]
    if week["assistant_referral_hosts"]:
        hosts = ", ".join(f"{h} {n}" for h, n in sorted(week["assistant_referral_hosts"].items(), key=lambda kv: -kv[1]))
        lines.append(f"  - from: {hosts}")
    lines += [
        f"- Click-outs to a funder by those people: {_pair(week['assistant_clickouts'], before['assistant_clickouts'])}",
        "",
        "CRAWLERS",
        f"- Training crawls: {_pair(sum(week['training'].values()), sum(before['training'].values()))}",
        *_by_agent(week["training"], before["training"], _TRAINING_AGENTS),
        f"- Search indexing: {_pair(sum(week['search'].values()), sum(before['search'].values()))}",
        *_by_agent(week["search"], before["search"], _SEARCH_AGENTS),
        "",
        "PEOPLE",
        f"- Page views by people: {_pair(week['human_pages'], before['human_pages'])}",
        f"  - arriving from a search engine: {_pair(week['human_from_search'], before['human_from_search'])}",
        f"  - arriving from an email: {_pair(week['human_from_email'], before['human_from_email'])}",
        f"- Click-outs to funders, all sources: {_pair(week['clickouts'], before['clickouts'])}",
    ]
    if week["top_clickout_funders"]:
        lines.append("  - most clicked: " + "; ".join(f"{f or 'unknown'} {n}" for f, n in week["top_clickout_funders"]))
    lines += [
        f"- API calls: {_pair(week['api_calls'], before['api_calls'])}; MCP tool calls: {_pair(week['mcp_calls'], before['mcp_calls'])}",
        "",
        "DATASET",
    ]
    if "error" in data:
        lines.append(f"- Could not read the published dataset: {data['error']}")
    else:
        published = (data.get("published_at") or "")[:10] or "never"
        lines += [
            f"- Live grants {_n(data.get('live_count', 0))}, closed {_n(data.get('closed_count', 0))}, "
            f"funders {_n(data.get('funder_count', 0))}",
            f"- Last published {published}" + (f"; next sweep due {data['next_sweep_due']}" if data.get("next_sweep_due") else ""),
        ]
        if data.get("held_count"):
            lines.append(f"- Rows held back at the last publish: {_n(data['held_count'])}")
        if data.get("forced"):
            lines.append(f"- The last publish was forced by {data.get('forced_by') or 'unknown'}")
    lines += [
        "",
        "TOTALS",
        f"- All hits: {_pair(week['hits'], before['hits'])}",
        f"- Of which other, unnamed bots: {_pair(week['hits_by_agent'].get('other-bot', 0), before['hits_by_agent'].get('other-bot', 0))}",
        "",
        f"Sent by the {country.upper()} deployment on Monday morning, its time. "
        "Replies to this address are not read.",
    ]
    subject = (f"GoBuga {country.upper()} week to {last_day:%d %b}: "
               f"{_n(fetches)} assistant fetches, {_n(week['assistant_referrals'])} referrals")
    return subject, "\n".join(lines)


# --- Sending and scheduling ---

def report_address() -> str | None:
    return os.getenv("REPORT_EMAIL") or os.getenv("DIRECTOR_EMAIL") or None


def send(country: str | None = None, now: datetime | None = None, *, dry_run: bool = False) -> dict:
    """Compose and send the week's email. Never raises."""
    country = country or get_country()
    try:
        subject, body = compose(country, now)
    except Exception as exc:  # noqa: BLE001
        print(f"[weekly] could not compose: {type(exc).__name__}: {exc}")
        return {"sent": False, "error": f"{type(exc).__name__}: {exc}"}
    to = report_address()
    if dry_run or not to:
        if not to and not dry_run:
            print(f"[weekly] no REPORT_EMAIL or DIRECTOR_EMAIL set; not sending:\n{body}")
        return {"sent": False, "to": to, "subject": subject, "body": body}
    try:
        from api.email import send_text_email
        send_text_email(to, subject, body)
    except Exception as exc:  # noqa: BLE001
        print(f"[weekly] send failed: {type(exc).__name__}: {exc}")
        return {"sent": False, "to": to, "subject": subject, "error": f"{type(exc).__name__}: {exc}"}
    return {"sent": True, "to": to, "subject": subject}


def _marker_path(country: str) -> str:
    return os.path.join(platform_dir(), "metrics", country, "weekly-sent.txt")


def _week_key(now: datetime, tz: ZoneInfo) -> str:
    start, _ = week_bounds(now, tz)
    return start.astimezone(tz).strftime("%Y-%m-%d")


def due(country: str, now: datetime) -> bool:
    """Monday at or after SEND_HOUR local time, and this week's email not yet sent."""
    tz = ZoneInfo(get_country_config(country).timezone)
    local = now.astimezone(tz)
    if local.weekday() != SEND_WEEKDAY or local.hour < SEND_HOUR:
        return False
    try:
        with open(_marker_path(country), encoding="utf-8") as f:
            return f.read().strip() != _week_key(now, tz)
    except OSError:
        return True


def _mark_sent(country: str, now: datetime) -> None:
    tz = ZoneInfo(get_country_config(country).timezone)
    path = _marker_path(country)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_week_key(now, tz) + "\n")


def tick(country: str | None = None, now: datetime | None = None) -> dict | None:
    """One scheduler check: send if due, and remember it. Returns the send
    result, or None when nothing was due."""
    country = country or get_country()
    now = now or datetime.now(timezone.utc)
    if not due(country, now):
        return None
    result = send(country, now)
    if result.get("sent") or not report_address():
        # With no address there is nothing to retry; do not print every half hour.
        _mark_sent(country, now)
    return result


def maybe_start_scheduler() -> bool:
    """Start the background thread unless disabled. Returns whether it started."""
    if os.getenv("WEEKLY_REPORT_DISABLED") == "1":
        return False

    def _loop() -> None:
        while True:
            try:
                tick()
            except Exception as exc:  # noqa: BLE001 — the loop must survive
                print(f"[weekly] tick failed: {type(exc).__name__}: {exc}")
            time.sleep(_CHECK_EVERY_SECONDS)

    threading.Thread(target=_loop, name="weekly-report", daemon=True).start()
    return True
