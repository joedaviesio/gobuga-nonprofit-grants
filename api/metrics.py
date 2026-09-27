# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""Server-side counters for the public surface: hits, click-outs, API and MCP calls.

Storage is append-only JSONL, one file per country per month:

    <platform_dir>/metrics/<country>/<YYYY-MM>.jsonl

What a line holds: the path (query string dropped), the agent class, the raw
user agent only when the class is a bot, the referrer class, the referrer host
only when the class is `assistant` or `search`, and the timestamp. Click-out
lines add the grant ID and funder name. Never the IP address, never a cookie,
never the raw user agent of a human.

`record_hit` and `record_clickout` never raise into the caller: a failed write
is logged in one line and dropped. Appends are serialised by a module lock,
which is enough for one uvicorn process with a threadpool.
"""

import json
import os
import re
import threading
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from api.country_config import get_country
from api.tenant import platform_dir

SURFACES: tuple[str, ...] = ("page", "api", "mcp", "out")
AGENT_CLASSES: tuple[str, ...] = (
    "gptbot", "claudebot", "perplexitybot", "google-extended", "ccbot",
    "googlebot", "bingbot", "other-bot", "human",
)
REFERRER_CLASSES: tuple[str, ...] = ("assistant", "search", "email", "direct", "other")

# Caps on what one line may carry, so a hostile client cannot bloat the log.
MAX_PATH_CHARS = 512
MAX_UA_CHARS = 512
MAX_FUNDER_CHARS = 200
TOP_N = 10

_COUNTRY_RE = re.compile(r"^[a-z]{2,8}$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

_write_lock = threading.Lock()


# --- Paths ---

def metrics_path(country: str, month: str) -> str:
    """Path of one month's JSONL file. Rejects malformed slugs (no traversal)."""
    if not _COUNTRY_RE.match(country or ""):
        raise ValueError(f"invalid country slug: {country!r}")
    if not _MONTH_RE.match(month or ""):
        raise ValueError(f"invalid month: {month!r}")
    return os.path.join(platform_dir(), "metrics", country, f"{month}.jsonl")


# --- Agent classification ---

# Checked in order; first match wins. Tokens are matched case-insensitively as
# substrings of the user agent, which is how the vendors document them.
# Google-Extended is a robots.txt token rather than a crawler UA, but it is
# listed so a client that does send it is counted under its own name.
_AGENT_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gptbot", ("gptbot", "oai-searchbot", "chatgpt-user")),
    ("claudebot", ("claudebot", "claude-user", "claude-searchbot", "anthropic-ai")),
    ("perplexitybot", ("perplexitybot", "perplexity-user")),
    ("google-extended", ("google-extended",)),
    ("ccbot", ("ccbot",)),
    ("googlebot", ("googlebot",)),
    ("bingbot", ("bingbot",)),
)

# Generic fallback: self-declared bots, plus the HTTP libraries that scripts
# and agent fetch tools send by default. None of these is a person's browser.
_GENERIC_BOT_RE = re.compile(
    r"bot|crawler|spider"
    r"|^curl/|^wget/|python-requests|python-httpx|python-urllib|aiohttp"
    r"|go-http-client|node-fetch|axios/|okhttp",
    re.IGNORECASE,
)


def classify_agent(user_agent: str) -> str:
    """Class of the client behind a user agent string.

    An empty user agent is `other-bot`: browsers always send one.
    """
    ua = (user_agent or "").strip().lower()
    if not ua:
        return "other-bot"
    for cls, tokens in _AGENT_TOKENS:
        if any(t in ua for t in tokens):
            return cls
    if _GENERIC_BOT_RE.search(ua):
        return "other-bot"
    return "human"


def _is_bot(agent_class: str) -> bool:
    return agent_class != "human"


# --- Referrer classification ---

_ASSISTANT_HOSTS: tuple[str, ...] = (
    "chatgpt.com", "chat.openai.com", "perplexity.ai", "claude.ai",
    "gemini.google.com", "copilot.microsoft.com",
)

_WEBMAIL_HOSTS: tuple[str, ...] = (
    "mail.google.com", "outlook.live.com", "outlook.office.com",
    "outlook.office365.com", "mail.yahoo.com", "mail.proton.me", "mail.aol.com",
    "mail.ru", "mail.yandex.ru", "mail.yandex.com",
)

_SEARCH_HOSTS: tuple[str, ...] = (
    "bing.com", "duckduckgo.com", "search.yahoo.com", "ecosia.org", "baidu.com",
    "search.brave.com", "startpage.com", "qwant.com", "kagi.com",
)

# Engines that run a country domain per market (google.co.nz, yandex.md).
_SEARCH_BRANDS: tuple[str, ...] = ("google", "yandex")


def _host_in(host: str, domains: tuple[str, ...]) -> bool:
    """Exact host or a subdomain of it. Never a substring match."""
    return any(host == d or host.endswith("." + d) for d in domains)


def _is_brand_search_host(host: str) -> bool:
    """google.com, www.google.co.nz, yandex.md — but not google.com.evil.example,
    and not other Google properties such as docs.google.com."""
    labels = host.split(".")
    for brand in _SEARCH_BRANDS:
        if brand not in labels:
            continue
        i = labels.index(brand)
        if labels[:i] not in ([], ["www"]):
            continue
        after = labels[i + 1:]
        if len(after) == 1 and after[0].isalpha():
            return True
        if len(after) == 2 and after[0] in ("co", "com") and len(after[1]) == 2:
            return True
    return False


def _referrer_host(referrer: str) -> str | None:
    try:
        parts = urlsplit(referrer)
        host = parts.hostname
        if host is None and "//" not in referrer:
            # A bare host such as "chatgpt.com" with no scheme.
            host = urlsplit("//" + referrer).hostname
    except ValueError:
        return None
    return host.rstrip(".") if host else None


def _has_email_utm(url: str) -> bool:
    try:
        query = urlsplit(url).query
    except ValueError:
        return False
    values = parse_qs(query).get("utm_medium", [])
    return any(v.strip().lower() == "email" for v in values)


def classify_referrer(referrer: str | None) -> str:
    """assistant, search, email, direct or other. Matches on the parsed host only."""
    referrer = (referrer or "").strip()
    if not referrer:
        return "direct"
    if _has_email_utm(referrer):
        return "email"
    host = _referrer_host(referrer)
    if not host:
        return "other"
    if _host_in(host, _ASSISTANT_HOSTS):
        return "assistant"
    if _host_in(host, _WEBMAIL_HOSTS):
        return "email"
    if _host_in(host, _SEARCH_HOSTS) or _is_brand_search_host(host):
        return "search"
    return "other"


# --- Recording ---

def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _base_line(path: str, user_agent: str, referrer: str | None, now: datetime) -> dict:
    """The fields every line carries, with the privacy rules applied."""
    path = path or ""
    agent = classify_agent(user_agent)
    # A page reached from an email link usually has no referrer; the campaign
    # tag on the landing URL is the only signal, so it wins.
    ref = "email" if _has_email_utm(path) else classify_referrer(referrer)
    line = {
        "ts": now.isoformat(),
        "path": path.split("?", 1)[0].split("#", 1)[0][:MAX_PATH_CHARS],
        "agent": agent,
        "ref": ref,
    }
    if _is_bot(agent):
        line["ua"] = (user_agent or "")[:MAX_UA_CHARS]
    if ref in ("assistant", "search"):
        line["ref_host"] = _referrer_host(referrer or "")
    return line


def _append(country: str | None, now: datetime, line: dict) -> None:
    path = metrics_path(country or get_country(), now.strftime("%Y-%m"))
    data = json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _write_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(data)


def record_hit(path, user_agent, referrer, *, surface, country=None, now=None) -> None:
    """Append one hit. surface: page | api | mcp | out.

    The MCP layer records tool calls with `path="mcp:<tool>"`.
    """
    try:
        if surface not in SURFACES:
            raise ValueError(f"unknown surface: {surface!r}")
        now = _now(now)
        line = {"kind": "hit", "surface": surface, **_base_line(path, user_agent, referrer, now)}
        _append(country, now, line)
    except Exception as exc:  # noqa: BLE001 — recording must never break a request
        print(f"[metrics] record_hit dropped: {type(exc).__name__}: {exc}")


def record_clickout(opp_id, funder, referrer, user_agent, *, country=None, now=None) -> None:
    """Append one click-out through `/out/{id}`."""
    try:
        now = _now(now)
        line = {
            "kind": "clickout",
            "surface": "out",
            **_base_line(f"/out/{opp_id}", user_agent, referrer, now),
            "opp_id": str(opp_id)[:MAX_PATH_CHARS],
            "funder": str(funder or "")[:MAX_FUNDER_CHARS],
        }
        _append(country, now, line)
    except Exception as exc:  # noqa: BLE001 — recording must never break a request
        print(f"[metrics] record_clickout dropped: {type(exc).__name__}: {exc}")


# --- Aggregation ---

def _api_prefix(path: str) -> str:
    """`/api/v1/opportunities/OPP-1` -> `/api/v1/opportunities`."""
    parts = [p for p in path.split("/") if p]
    if len(parts) <= 2:
        return "/" + "/".join(parts)
    head = parts[2].split(".", 1)[0]  # /api/v1/stats.json -> stats
    return f"/api/v1/{head}"


def _read_lines(path: str):
    """Yield each well-formed JSON object in the file; skip anything else."""
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return
    with f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _field(row: dict, key: str) -> str:
    """A string field from a stored line; anything missing or odd is "unknown"."""
    value = row.get(key)
    return value if isinstance(value, str) and value else "unknown"


def _top(counter: Counter) -> list[list]:
    # Ties broken by key so the output is deterministic.
    return [[k, n] for k, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]]


def monthly_counters(country=None, month=None) -> dict:
    """Aggregate one month's file. A missing or empty file gives zero counts.

    Raises ValueError for a malformed country or month, so callers validate
    route parameters before they reach the filesystem.
    """
    country = country or get_country()
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    path = metrics_path(country, month)

    hits_by_surface: Counter = Counter()
    hits_by_agent: Counter = Counter()
    hits_by_referrer: Counter = Counter()
    clickouts_by_referrer: Counter = Counter()
    clickouts_by_agent: Counter = Counter()
    clickout_grants: Counter = Counter()
    clickout_funders: Counter = Counter()
    api_by_prefix: Counter = Counter()
    mcp_by_tool: Counter = Counter()
    clickouts = 0

    for row in _read_lines(path):
        kind = row.get("kind")
        path_ = _field(row, "path")
        if kind == "hit":
            surface = _field(row, "surface")
            hits_by_surface[surface] += 1
            hits_by_agent[_field(row, "agent")] += 1
            hits_by_referrer[_field(row, "ref")] += 1
            if surface == "api":
                api_by_prefix[_api_prefix(path_)] += 1
            elif surface == "mcp" and path_.startswith("mcp:"):
                mcp_by_tool[path_[len("mcp:"):]] += 1
        elif kind == "clickout":
            clickouts += 1
            clickouts_by_referrer[_field(row, "ref")] += 1
            clickouts_by_agent[_field(row, "agent")] += 1
            clickout_grants[_field(row, "opp_id")] += 1
            clickout_funders[_field(row, "funder")] += 1

    return {
        "country": country,
        "month": month,
        "hits_by_surface": dict(hits_by_surface),
        "hits_by_agent": dict(hits_by_agent),
        "hits_by_referrer": dict(hits_by_referrer),
        "clickouts_total": clickouts,
        "clickouts_by_referrer": dict(clickouts_by_referrer),
        "clickouts_by_agent": dict(clickouts_by_agent),
        "top_clickout_grants": _top(clickout_grants),
        "top_clickout_funders": _top(clickout_funders),
        "api_calls_by_prefix": dict(api_by_prefix),
        "mcp_calls_by_tool": dict(mcp_by_tool),
    }
