# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""What `/api/v1/stats` publishes from `api.metrics`, and nothing more.

`monthly_counters` returns some client-chosen strings (API path prefixes,
MCP tool names, referrer hosts, top grant IDs). This module turns it into
classes and counts over fixed key sets, so nothing a client sent can reach a
public page: no user agent, no host, no path outside our own routes, nothing
per person (doctrine 9).

`monthly_counters` reads a whole month's file, so results are cached in
memory for COUNTERS_TTL_SECONDS.
"""

import time
from datetime import datetime
from threading import Lock

from api import metrics

COUNTERS_TTL_SECONDS = 300
MCP_TOOLS = ("search_grants", "get_grant", "fit_grants")
_CACHE_MAX = 64

# (country, month) -> (expires at, monotonic; counters; computed at).
_cache: dict[tuple[str, str], tuple[float, dict, datetime]] = {}
_cache_lock = Lock()


def counters(country: str, month: str, now: datetime) -> tuple[dict, datetime]:
    """(monthly_counters, when they were computed), cached. `month` must
    already be validated: monthly_counters raises ValueError otherwise."""
    key = (country, month)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1], hit[2]
    value = metrics.monthly_counters(country, month)
    with _cache_lock:
        if key not in _cache and len(_cache) >= _CACHE_MAX:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
        _cache[key] = (time.monotonic() + COUNTERS_TTL_SECONDS, value, now)
    return value, now


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def endpoint_keys(paths) -> list[str]:
    """The `/api/v1/<first segment>` keys metrics aggregates API hits under,
    for the given route paths only."""
    keys = set()
    for path in paths:
        parts = [p for p in path.split("/") if p]
        if parts[:2] == ["api", "v1"]:
            keys.add("/api/v1" if len(parts) == 2 else f"/api/v1/{parts[2].split('.', 1)[0]}")
    return sorted(keys)


def usage(value: dict, endpoints: list[str]) -> dict:
    """One month's public usage block. Every key set is fixed; counts only."""
    by_ref = value.get("clickouts_by_referrer", {})
    by_agent = value.get("hits_by_agent", {})
    by_surface = value.get("hits_by_surface", {})
    api = value.get("api_calls_by_prefix", {})
    mcp = value.get("mcp_calls_by_tool", {})
    bots = [c for c in metrics.AGENT_CLASSES if c != "human"]
    return {
        "month": value["month"],
        "clickouts": {"total": value.get("clickouts_total", 0),
                      "by_referrer": {c: by_ref.get(c, 0) for c in metrics.REFERRER_CLASSES}},
        # Includes the fit Atom feed and the pages' server-side renders.
        "fit_urls_built": api.get("/api/v1/fit", 0),
        "api_calls": {"total": by_surface.get("api", 0),
                      "by_endpoint": {k: api.get(k, 0) for k in endpoints}},
        # Tool calls: one `mcp:<tool>` line each. The `mcp` surface count also
        # holds a transport line for every POST (initialize, tools/list, ...),
        # so it is not a count of calls.
        "mcp_calls": {"total": sum(n for n in mcp.values() if isinstance(n, int)),
                      "by_tool": {t: mcp.get(t, 0) for t in MCP_TOOLS}},
        # Every surface: pages, API, MCP and click-outs.
        "crawler_hits": {"total": sum(by_agent.get(c, 0) for c in bots),
                         "by_agent": {c: by_agent.get(c, 0) for c in bots}},
    }


def confirmed_subscribers(country: str) -> int | None:
    """Phase 6's confirmed count, or None when that module is absent or fails."""
    try:
        from api.subscribers import subscriber_counts
    except ImportError:
        return None
    try:
        return int(subscriber_counts(country).get("confirmed", 0))
    except Exception as exc:  # noqa: BLE001 — stats must not fail on a counter
        print(f"[public_stats] subscriber_counts failed: {type(exc).__name__}")
        return None
