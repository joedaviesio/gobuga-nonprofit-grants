# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""The keyless machine API: `/api/v1/*` and the `/out/{id}` click-out.

The primary public surface (doctrine 10): the HTML pages render what these
routes return, and every page's `.json` twin maps to exactly one route here
with the same query string (see URL_SCHEME).

Rules every route follows:
- rows come only from `api.published`, ranking only from `api.fit`;
- no LLM, no network, no writes, except the click-out append in `/out`;
- stale rows appear in no list, feed or fit result; their own record
  resolves with `X-Robots-Tag: noindex`;
- errors are `{"error": {code, message}}`; a malformed query is a 400, never
  a 500, and error messages never echo the input;
- read responses carry ETag, Last-Modified and Cache-Control and honour
  conditional GET (`api.public_http.cached_response`);
- every route is rate limited per client (`api.rate_limit`).

Record shaping, vocabularies and filters live in `api.public_data`.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute

from api import metrics, public_stats, published
from api.errors import PublicError
from api.fit import (
    ENTITY_VOCAB, NEEDS, PARAM_ORDER, SIZE_BAND_REVENUE, SIZE_BANDS,
    FitParamError, parse_fit_params, score_fit,
)
from api.public_context import (
    JSON, MAX_LIMIT, PARAM_DOCS as _P, Ctx, filtered, invalid, json_response, list_body,
    page, request_context, search_params,
)
from api.public_data import (
    AMOUNT_BANDS, DEADLINE_BUCKETS, EXAMPLE_RECORD, ID_RE, LICENCE, LIST_STATUSES, MONTH_RE,
    SLUG_RE, SORTS, brief, funder_summary, grant_url, group_by_funder, parse_ts, related_live, sort_rows,
    with_query,
)
from api.public_http import STATS_MAX_AGE, atom_feed, atom_time, cached_response, json_bytes
from api.rate_limit import check_public_rate_limit

router = APIRouter(dependencies=[Depends(check_public_rate_limit)])

FEED_MAX_ENTRIES = 200
ATOM = "application/atom+xml; charset=utf-8"

# Page -> public twin or feed -> backend route. One source of truth for the
# Next.js rewrites and llms.txt; served in `GET /api/v1`. Each public path
# forwards its query string unchanged.
URL_SCHEME: tuple[dict, ...] = (
    {"page": "/grants/{id}", "public": "/grants/{id}.json",
     "backend": "/api/v1/opportunities/{id}", "format": "json"},
    {"page": "/grants", "public": "/grants.json",
     "backend": "/api/v1/opportunities", "format": "json"},
    {"page": "/fit", "public": "/fit.json", "backend": "/api/v1/fit", "format": "json"},
    {"page": "/fit", "public": "/fit/feed.xml", "backend": "/api/v1/fit/feed.xml", "format": "atom"},
    {"page": "/funders", "public": "/funders.json", "backend": "/api/v1/funders", "format": "json"},
    {"page": "/funders/{slug}", "public": "/funders/{slug}.json",
     "backend": "/api/v1/funders/{slug}", "format": "json"},
    {"page": "/changes", "public": "/changes.json", "backend": "/api/v1/changes", "format": "json"},
    {"page": "/changes", "public": "/changes/feed.xml",
     "backend": "/api/v1/changes/feed.xml", "format": "atom"},
    {"page": "/stats", "public": "/stats.json", "backend": "/api/v1/stats", "format": "json"},
    {"page": "/stats/{month}", "public": "/stats/{month}.json",
     "backend": "/api/v1/stats/{month}", "format": "json"},
    {"page": None, "public": "/out/{id}", "backend": "/out/{id}", "format": "redirect"},
    # Model Context Protocol over Streamable HTTP (POST only); see api/mcp_server.py.
    {"page": None, "public": "/mcp", "backend": "/mcp", "format": "mcp"},
)

# OpenAPI response examples.
_META_EXAMPLE = {"published_at": "2026-08-05T00:00:00+00:00", "sweep_month": "2026-08"}
_LIST_EXAMPLE = {"data": [EXAMPLE_RECORD], "total": 1, "offset": 0, "limit": 20, "meta": _META_EXAMPLE}
_FIT_EXAMPLE = {
    "data": [{**EXAMPLE_RECORD, "score": 83.0,
              "why": ["open to incorporated societies", "Auckland", "sector: Sport & recreation",
                      "closes in 63 days", "grants from $1,000 to $20,000"]}],
    "total": 1, "offset": 0, "limit": 20, "meta": _META_EXAMPLE,
    "canonical_query": "sector=sport&region=auckland&status=incorporated-society",
    "canonical_url": "https://gobuga.org/fit?sector=sport&region=auckland&status=incorporated-society",
    "json_url": "https://gobuga.org/fit.json?sector=sport&region=auckland&status=incorporated-society",
    "feed_url": "https://gobuga.org/fit/feed.xml?sector=sport&region=auckland&status=incorporated-society",
}


def _responses(example: dict, *errors: int) -> dict:
    out = {200: {"content": {JSON: {"example": example}}}}
    samples = {
        400: ("invalid_parameter", "Invalid value for 'deadline'. Allowed values: 30d, 90d, rolling, dated"),
        404: ("not_found", "No published grant with that ID"),
    }
    for status in errors:
        code, message = samples[status]
        out[status] = {"description": code.replace("_", " ").capitalize(),
                       "content": {JSON: {"example": {"error": {"code": code, "message": message}}}}}
    return out


LICENCE_SUMMARY = ("Facts are free to reuse, including by AI assistants, with attribution: "
                   "name GoBuga and link the record's canonical_url.")


@router.get("/api/v1/opportunities", summary="Search published grants", tags=["grants"],
            responses=_responses(_LIST_EXAMPLE, 400))
def list_opportunities(
    request: Request,
    q: str | None = Query(None, description=_P["q"], examples=["youth sport"]),
    tag: str | None = Query(None, description=_P["tag"], examples=["sport,youth"]),
    region: str | None = Query(None, description=_P["region"]),
    funder: str | None = Query(None, description=_P["funder"]),
    amount: str | None = Query(None, description=_P["amount"], examples=["10k-50k"]),
    deadline: str | None = Query(None, description=_P["deadline"], examples=["30d"]),
    status: str | None = Query(None, description=_P["status"]),
    sort: str | None = Query(None, description=_P["sort"]),
    offset: str | None = Query(None, description=_P["offset"]),
    limit: str | None = Query(None, description=_P["limit"]),
):
    """Published grants matching every given filter, as public records.

    Same parameters as the `/grants` page. Unknown values are a 400 naming
    the parameter and listing the allowed values.
    """
    ctx = request_context()
    rows = search_rows(ctx, q=q, tag=tag, region=region, funder=funder, amount=amount,
                       deadline=deadline, status=status, sort=sort)
    off, lim = page(offset, limit)
    body = list_body(rows, off, lim, ctx)
    body["data"] = [ctx.record(r) for r in body["data"]]
    return json_response(request, ctx, body)


def search_rows(ctx: Ctx, **raw) -> list[dict]:
    """Every row matching the raw search parameters (q, tag, region, funder,
    amount, deadline, status, sort), in order. Shared with the MCP
    `search_grants` tool; a bad value raises the 400 PublicError."""
    params = search_params(ctx, **raw)
    sort_key = params.pop("sort")
    return sort_rows(filtered(ctx, **params), sort_key)


def _published_row(ctx: Ctx, opp_id: str) -> dict | None:
    """Any published row by ID, or None; a malformed ID never reaches the lookup."""
    if not ID_RE.match(opp_id):
        return None
    return published.get_published_row(opp_id, ctx.country, today=ctx.today)


@router.get("/api/v1/opportunities/{opp_id}", summary="One grant by ID", tags=["grants"],
            responses=_responses(EXAMPLE_RECORD, 404))
def get_opportunity(request: Request, opp_id: str):
    """One public record, whatever its status.

    A closed record carries `related`: up to five live grants sharing its
    tags. A stale record (verification expired) carries `notice` and the
    response is marked `X-Robots-Tag: noindex`. Unknown IDs are a 404.
    """
    ctx = request_context()
    record = grant_record(ctx, opp_id)
    if record is None:
        raise PublicError(404, "not_found", "No published grant with that ID")
    headers = {"X-Robots-Tag": "noindex"} if record["status"] == "stale" else {}
    return json_response(request, ctx, record, headers=headers)


def grant_record(ctx: Ctx, opp_id: str) -> dict | None:
    """One public record by ID, whatever its status, or None. A closed
    record gains `related`; a stale one carries `notice` (from
    `public_record`). Shared with the MCP `get_grant` tool."""
    row = _published_row(ctx, opp_id)
    if row is None:
        return None
    record = ctx.record(row)
    if row["status"] == "closed":
        record["related"] = related_live(row, ctx.rows("live"), ctx.base)
    return record


@router.get("/api/v1/ids", summary="Every listed grant ID, for sitemaps", tags=["grants"])
def list_ids(request: Request):
    """Every live and closed grant as `{id, status, last_seen, canonical_url}`.
    Stale rows are left out. Not paginated."""
    ctx = request_context()
    rows = sorted(ctx.rows("live", "closed"), key=lambda r: r.get("id") or "")
    data = [{"id": r["id"], "status": r["status"], "last_seen": r.get("last_seen"),
             "canonical_url": grant_url(r["id"], ctx.base)} for r in rows]
    return json_response(request, ctx, {"data": data, "total": len(data), "meta": ctx.list_meta})


# --- Fit -----------------------------------------------------------------------

@dataclass(frozen=True)
class FitResult:
    results: list[dict]
    query: str
    page_url: str
    json_url: str
    feed_url: str


def fit_result(ctx: Ctx, sector, region, status, size, need) -> FitResult:
    """Validate the five fit parameters and rank the live rows. Shared with
    the MCP `fit_grants` tool; a bad value raises the 400 PublicError."""
    raw = {"sector": sector, "region": region, "status": status, "size": size, "need": need}
    try:
        profile = parse_fit_params({k: v for k, v in raw.items() if v is not None}, ctx.country)
    except FitParamError as exc:
        raise invalid(exc.param, exc.allowed) from None
    query = profile.canonical_query()
    return FitResult(
        results=score_fit(ctx.rows("live"), profile, today=ctx.today),
        query=query,
        page_url=with_query(f"{ctx.base}/fit", query),
        json_url=with_query(f"{ctx.base}/fit.json", query),
        feed_url=with_query(f"{ctx.base}/fit/feed.xml", query),
    )


def _fit_query(name: str):
    """A fresh Query for one of the five fit parameters (two routes take them)."""
    description, example = {
        "sector": (_P["sector"], "sport,youth"),
        "region": (_P["fit_region"], None),
        "status": (_P["fit_status"], "incorporated-society"),
        "size": (_P["size"], "50k-250k"),
        "need": (_P["need"], "equipment"),
    }[name]
    return Query(None, description=description, examples=[example] if example else None)


@router.get("/api/v1/fit", summary="Grants ranked for an organisation", tags=["fit"],
            responses=_responses(_FIT_EXAMPLE, 400))
def fit(
    request: Request,
    sector: str | None = _fit_query("sector"),
    region: str | None = _fit_query("region"),
    status: str | None = _fit_query("status"),
    size: str | None = _fit_query("size"),
    need: str | None = _fit_query("need"),
    offset: str | None = Query(None, description=_P["offset"]),
    limit: str | None = Query(None, description=_P["limit"]),
):
    """Live grants ranked for an organisation described by five parameters.

    Deterministic, no LLM. Each item is the public record plus `score` and
    `why`. `canonical_query`, `canonical_url` and `json_url` are the same for
    every equivalent request (any case, order or label form of the same values).
    """
    ctx = request_context()
    result = fit_result(ctx, sector, region, status, size, need)
    off, lim = page(offset, limit)
    return json_response(request, ctx, fit_body(ctx, result, off, lim))


def fit_body(ctx: Ctx, result: FitResult, offset: int, limit: int) -> dict:
    """One page of a fit as `/api/v1/fit` returns it. Shared with the MCP
    `fit_grants` tool."""
    items = [{**ctx.record(e["row"]), "score": e["score"], "why": e["why"]}
             for e in result.results[offset:offset + limit]]
    return {"data": items, "total": len(result.results), "offset": offset, "limit": limit,
            "meta": ctx.list_meta, "canonical_query": result.query,
            "canonical_url": result.page_url, "json_url": result.json_url,
            "feed_url": result.feed_url}


@router.get("/api/v1/fit/feed.xml", summary="Atom feed of a fit", tags=["fit"],
            response_class=Response, responses={200: {"content": {"application/atom+xml": {}}}})
def fit_feed(
    request: Request,
    sector: str | None = _fit_query("sector"),
    region: str | None = _fit_query("region"),
    status: str | None = _fit_query("status"),
    size: str | None = _fit_query("size"),
    need: str | None = _fit_query("need"),
    limit: str | None = Query(None, description=_P["limit"]),
):
    """The same ranking as `/api/v1/fit`, best first, as Atom 1.0."""
    ctx = request_context()
    result = fit_result(ctx, sector, region, status, size, need)
    _, lim = page(None, limit)
    entries = []
    for e in result.results[:lim]:
        row = e["row"]
        url = grant_url(row["id"], ctx.base)
        updated = (parse_ts(row.get("verified_at")) or parse_ts(row.get("last_seen"))
                   or ctx.last_modified)
        entries.append({"id": url, "title": row.get("title"), "updated": atom_time(updated),
                        "link": url, "summary": "; ".join(e["why"])})
    title = f"GoBuga fit: {result.query}" if result.query else "GoBuga fit"
    body = atom_feed(feed_id=result.page_url, title=title,
                     updated=atom_time(ctx.last_modified), self_url=result.feed_url,
                     alternate_url=result.page_url, entries=entries)
    return cached_response(request, body, media_type=ATOM, last_modified=ctx.last_modified)


# --- Funders -------------------------------------------------------------------

@router.get("/api/v1/funders", summary="Funders in the published pool", tags=["funders"])
def list_funders(
    request: Request,
    offset: str | None = Query(None, description=_P["offset"]),
    limit: str | None = Query(None, description=f"Rows to return, 1 to {MAX_LIMIT}; default {MAX_LIMIT}."),
):
    """Every funder with a live or closed grant: canonical name, slug, counts."""
    ctx = request_context()
    groups = group_by_funder(ctx.rows("live", "closed"), ctx.country)
    items = [funder_summary(slug, rows, ctx.country, ctx.base) for slug, rows in sorted(groups.items())]
    off, lim = page(offset, limit, default_limit=MAX_LIMIT)
    return json_response(request, ctx, list_body(items, off, lim, ctx))


@router.get("/api/v1/funders/{slug}", summary="One funder and its programmes", tags=["funders"])
def get_funder(request: Request, slug: str):
    """A funder's live and closed programmes as public records. 404 if the
    funder has no live or closed grant."""
    ctx = request_context()
    groups = group_by_funder(ctx.rows("live", "closed"), ctx.country) if SLUG_RE.match(slug) else {}
    rows = groups.get(slug)
    if not rows:
        raise PublicError(404, "not_found", "No funder with that slug")
    body = {
        **funder_summary(slug, rows, ctx.country, ctx.base),
        "live": [ctx.record(r) for r in sort_rows([r for r in rows if r["status"] == "live"], "deadline")],
        "closed": [ctx.record(r) for r in sort_rows([r for r in rows if r["status"] == "closed"], "deadline")],
        "meta": ctx.list_meta,
    }
    return json_response(request, ctx, body)


# --- Taxonomy ------------------------------------------------------------------

@router.get("/api/v1/taxonomy", summary="Every allowed parameter value", tags=["reference"])
def taxonomy(request: Request):
    """Everything needed to construct any valid URL: tags, regions, sector
    labels, the entity vocabulary, size bands, needs, amount bands, deadline
    buckets, list statuses and sorts."""
    ctx = request_context()
    cfg = ctx.cfg
    labels: dict[str, str] = {}
    for label, tag in cfg.sector_label_to_tag.items():
        labels.setdefault(tag, label)
    body = {
        "country": ctx.country,
        "country_label": cfg.country_label,
        "currency": cfg.currency,
        "tags": [{"slug": t, "label": labels.get(t)} for t in cfg.tags],
        "regions": list(cfg.regions),
        "sector_labels": dict(cfg.sector_label_to_tag),
        "entity_vocab": list(ENTITY_VOCAB),
        "size_bands": [{"slug": s, "revenue_min": SIZE_BAND_REVENUE[s][0],
                        "revenue_max": SIZE_BAND_REVENUE[s][1], "currency": cfg.currency}
                       for s in SIZE_BANDS],
        "needs": list(NEEDS),
        "amount_bands": [{"slug": s, "min": lo, "max": hi, "currency": cfg.currency}
                         for s, lo, hi in AMOUNT_BANDS],
        "deadline_buckets": [{"slug": k, "description": v} for k, v in DEADLINE_BUCKETS.items()],
        "list_statuses": list(LIST_STATUSES),
        "sorts": [{"slug": k, "description": v} for k, v in SORTS.items()],
        "fit_params": list(PARAM_ORDER),
    }
    return json_response(request, ctx, body)


# --- Changes -------------------------------------------------------------------

def _changes(ctx: Ctx) -> list[dict]:
    """Changelog entries with IDs expanded; rows now stale are left out."""
    rows = {r["id"]: r for r in ctx.rows("live", "closed")}
    out = []
    for entry in published.load_changes(ctx.country):
        if not isinstance(entry, dict):
            continue
        changed = [c for c in entry.get("changed") or [] if isinstance(c, dict)]
        out.append({
            "published_at": entry.get("published_at"),
            "sweep_month": entry.get("sweep_month"),
            "new": [brief(rows[i], ctx.base) for i in entry.get("new") or [] if i in rows],
            "changed": [{**brief(rows[c.get("id")], ctx.base), "fields": list(c.get("fields") or [])}
                        for c in changed if c.get("id") in rows],
            "closed": [brief(rows[i], ctx.base) for i in entry.get("closed") or [] if i in rows],
        })
    return out


@router.get("/api/v1/changes", summary="What each publish added, changed and closed",
            tags=["changes"])
def list_changes(
    request: Request,
    offset: str | None = Query(None, description=_P["offset"]),
    limit: str | None = Query(None, description=_P["limit"]),
):
    """The changelog, newest publish first. Each entry lists new, changed
    (with the fields that changed) and closed grants as `{id, title, canonical_url}`."""
    ctx = request_context()
    off, lim = page(offset, limit)
    return json_response(request, ctx, list_body(_changes(ctx), off, lim, ctx))


@router.get("/api/v1/changes/feed.xml", summary="Atom feed of the changelog", tags=["changes"],
            response_class=Response, responses={200: {"content": {"application/atom+xml": {}}}})
def changes_feed(request: Request):
    """One Atom entry per new, changed or closed grant, newest publish first
    (at most 200 entries)."""
    ctx = request_context()
    entries = []
    for change in _changes(ctx):
        when = parse_ts(change["published_at"]) or ctx.last_modified
        for kind in ("new", "changed", "closed"):
            for item in change[kind]:
                summary = kind + (f": {', '.join(item['fields'])}" if item.get("fields") else "")
                entries.append({"id": f"{item['canonical_url']}#{kind}-{atom_time(when)}",
                                "title": item["title"], "updated": atom_time(when),
                                "link": item["canonical_url"], "summary": summary})
    body = atom_feed(feed_id=f"{ctx.base}/changes", title=f"GoBuga changes: {ctx.cfg.country_label}",
                     updated=atom_time(ctx.last_modified), self_url=f"{ctx.base}/changes/feed.xml",
                     alternate_url=f"{ctx.base}/changes", entries=entries[:FEED_MAX_ENTRIES])
    return cached_response(request, body, media_type=ATOM, last_modified=ctx.last_modified)


# --- Stats ---------------------------------------------------------------------

def _stats_response(request: Request, ctx: Ctx, month: str, *, current: bool) -> Response:
    counters, computed_at = public_stats.counters(ctx.country, month, ctx.now)
    endpoints = public_stats.endpoint_keys(r.path for r in _routes())
    body = {"country": ctx.country, "generated_at": computed_at.isoformat(),
            "usage": public_stats.usage(counters, endpoints)}
    last_modified = computed_at
    if current:
        last_modified = max(computed_at, ctx.last_modified)
        meta = ctx.meta
        listed = ctx.rows("live", "closed")
        # Counted here for the request's own date, and funders by slug as
        # /api/v1/funders counts them, so every endpoint agrees.
        body["dataset"] = {
            "live_count": sum(1 for r in listed if r["status"] == "live"),
            "closed_count": sum(1 for r in listed if r["status"] == "closed"),
            "funder_count": len(group_by_funder(listed, ctx.country)),
            "last_sweep": meta.get("sweep_month"),
            "last_sweep_run": meta.get("run_ts"),
            "published_at": meta.get("published_at"),
            "next_sweep_due": meta.get("next_sweep_due"),
        }
        confirmed = public_stats.confirmed_subscribers(ctx.country)
        if confirmed is not None:
            body["subscribers"] = {"confirmed": confirmed}
    return json_response(request, ctx, body, last_modified=last_modified, max_age=STATS_MAX_AGE)


@router.get("/api/v1/stats", summary="Public counters, this month", tags=["stats"])
def stats(request: Request):
    """Dataset counts, sweep dates, and this month's usage: click-outs by
    referrer class, fit URLs built, API and MCP calls, crawler hits by agent
    class, confirmed subscribers. Counts only; nothing about any person."""
    ctx = request_context()
    return _stats_response(request, ctx, ctx.now.strftime("%Y-%m"), current=True)


@router.get("/api/v1/stats/{month}", summary="Public counters for one month", tags=["stats"])
def stats_month(request: Request, month: str):
    """Usage counters for a past or the current month (UTC), `YYYY-MM`."""
    ctx = request_context()
    if not MONTH_RE.match(month):
        raise invalid("month", ["YYYY-MM"])
    if month > ctx.now.strftime("%Y-%m"):
        raise PublicError(404, "not_found", "No counters for a future month")
    return _stats_response(request, ctx, month, current=False)


# --- Click-out -----------------------------------------------------------------

def _safe_destination(url) -> str | None:
    """The stored URL if it is absolute http(s) with a host and no whitespace
    or control characters; otherwise None."""
    if not isinstance(url, str) or any(ord(c) <= 0x20 or ord(c) == 0x7f for c in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return url if parts.scheme.lower() in ("http", "https") and parts.hostname else None


@router.get("/out/{opp_id}", summary="Click out to the funder's page", tags=["grants"],
            response_class=RedirectResponse, status_code=302)
def click_out(request: Request, opp_id: str):
    """302 to the grant's stored source URL, after counting the click if a
    person made it.

    Only ever redirects to a stored http(s) `source_url`; nothing in the
    request can choose the destination. Works for closed grants. Not cached
    and not indexed.
    """
    ctx = request_context()
    row = _published_row(ctx, opp_id)
    destination = _safe_destination(row.get("source_url")) if row else None
    if destination is None:
        raise PublicError(404, "not_found", "No published grant with that ID")
    # A click-out is a person going to a funder. Crawlers follow the link too;
    # the hit-log middleware counts them as hits on the `out` surface.
    agent = request.headers.get("user-agent", "")
    if metrics.classify_agent(agent) == "human":
        metrics.record_clickout(row["id"], row.get("funder"), request.headers.get("referer"),
                                agent, country=ctx.country)
    return RedirectResponse(destination, status_code=302, headers={
        "Cache-Control": "no-store",
        "X-Robots-Tag": "noindex, nofollow",
    })


# --- Self-description ----------------------------------------------------------

def _routes() -> list[APIRoute]:
    return [r for r in router.routes if isinstance(r, APIRoute)]


_openapi_cache: dict[str, bytes] = {}


def _openapi_doc(base: str) -> dict:
    doc = get_openapi(
        title="GoBuga public API",
        version="1",
        summary="Keyless, read-only index of verified grant funding.",
        description=f"{LICENCE_SUMMARY} Every allowed parameter value is at /api/v1/taxonomy.",
        routes=_routes(),
        servers=[{"url": base}],
    )
    # FastAPI documents a 422 for every route; ours answer 400 envelopes instead.
    for operations in doc.get("paths", {}).values():
        for op in operations.values():
            op.get("responses", {}).pop("422", None)
    schemas = doc.get("components", {}).get("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):
        schemas.pop(name, None)
    if "components" in doc and not schemas:
        doc["components"].pop("schemas", None)
        if not doc["components"]:
            doc.pop("components")
    return doc


@router.get("/api/v1/openapi.json", summary="OpenAPI 3 document for this API", tags=["reference"])
def openapi(request: Request):
    """A curated OpenAPI document describing only the public routes."""
    ctx = request_context()
    doc = _openapi_cache.get(ctx.base)
    if doc is None:
        doc = json_bytes(_openapi_doc(ctx.base))
        _openapi_cache.clear()  # one base URL per process in practice
        _openapi_cache[ctx.base] = doc
    return cached_response(request, doc, media_type=JSON, last_modified=ctx.last_modified)


@router.get("/api/v1", summary="What this API is and how to use it", tags=["reference"])
def index(request: Request):
    """A self-describing index: what GoBuga is, the country served, every
    endpoint with its parameters, the page and JSON-twin URL scheme, the
    licence line and the OpenAPI document."""
    ctx = request_context()
    endpoints = [{
        "path": r.path,
        "method": "GET",
        "summary": r.summary,
        "path_parameters": [p.name for p in r.dependant.path_params],
        "query_parameters": [p.name for p in r.dependant.query_params],
    } for r in _routes()]
    endpoints.append({
        "path": "/mcp",
        "method": "POST",
        "summary": "Model Context Protocol endpoint (Streamable HTTP, keyless, read-only): "
                   "tools search_grants, get_grant, fit_grants",
        "path_parameters": [],
        "query_parameters": [],
    })
    body = {
        "name": "GoBuga",
        "description": ("A public, verified, machine-readable index of grant funding. "
                        "Every row says how and when it was verified. Keyless and read-only."),
        "country": ctx.country,
        "country_label": ctx.cfg.country_label,
        "base_url": ctx.base,
        "api_base_url": f"{ctx.base}/api/v1",
        "openapi_url": f"{ctx.base}/api/v1/openapi.json",
        "taxonomy_url": f"{ctx.base}/api/v1/taxonomy",
        "mcp_url": f"{ctx.base}/mcp",
        "licence": {"id": LICENCE, "summary": LICENCE_SUMMARY},
        "endpoints": endpoints,
        "url_scheme": list(URL_SCHEME),
        "meta": ctx.list_meta,
    }
    return json_response(request, ctx, body)
