# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""Per-request context and parameter validation for `/api/v1`.

`request_context()` fixes, once per request, the country, the clock, the
local date, the public base URL and the publish metadata, so every row,
record and header in one response agrees on "today". Validation helpers turn
a bad parameter into a 400 envelope that names the parameter and lists the
allowed values, and never echoes the input.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone

from fastapi import Request, Response

from api import published
from api.country_config import CountryConfig, get_country, get_country_config
from api.errors import PublicError
from api.public_data import (
    AMOUNT_BANDS, DEADLINE_BUCKETS, LIST_STATUSES, SLUG_RE, SORTS,
    base_url, day_start_utc, fold, group_by_funder, local_today, matches_amount,
    matches_deadline, matches_q, matches_region, matches_tags, parse_ts, public_record,
)
from api.public_http import cached_response, json_bytes

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_OFFSET = 1_000_000
MAX_Q_CHARS = 200

JSON = "application/json"


def now_utc() -> datetime:
    """The request clock. Tests replace this to pin the date."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Ctx:
    country: str
    cfg: CountryConfig
    now: datetime
    today: date
    base: str
    meta: dict

    @property
    def retrieved_at(self) -> str:
        return self.today.isoformat()

    @property
    def last_modified(self) -> datetime:
        """The later of the last publish and local midnight today, since
        statuses and "closes in" move at midnight."""
        midnight = day_start_utc(self.today, self.cfg.timezone)
        published_at = parse_ts(self.meta.get("published_at"))
        return max(midnight, published_at) if published_at else midnight

    @property
    def list_meta(self) -> dict:
        return {"published_at": self.meta.get("published_at"),
                "sweep_month": self.meta.get("sweep_month")}

    def rows(self, *statuses: str) -> list[dict]:
        return published.load_published(self.country, today=self.today, include=statuses)

    def record(self, row: dict) -> dict:
        return public_record(row, base=self.base, retrieved_at=self.retrieved_at)


def request_context() -> Ctx:
    country = get_country()
    cfg = get_country_config(country)
    now = now_utc()
    return Ctx(country=country, cfg=cfg, now=now, today=local_today(now, cfg.timezone),
               base=base_url(), meta=published.published_meta(country))


def json_response(request: Request, ctx: Ctx, body, **kw) -> Response:
    kw.setdefault("last_modified", ctx.last_modified)
    return cached_response(request, json_bytes(body), media_type=JSON, **kw)


# --- Parameter validation ------------------------------------------------------

def invalid(param: str, allowed) -> PublicError:
    return PublicError(400, "invalid_parameter",
                       f"Invalid value for '{param}'. Allowed values: {', '.join(allowed)}")


def choice(param: str, raw: str | None, allowed, default: str | None = None) -> str | None:
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value not in allowed:
        raise invalid(param, allowed)
    return value


def integer(param: str, raw: str | None, default: int, lo: int, hi: int) -> int:
    if raw is None or not raw.strip():
        return default
    value = raw.strip()
    if not value.isascii() or not value.isdigit() or not lo <= int(value) <= hi:
        raise invalid(param, [f"an integer from {lo} to {hi}"])
    return int(value)


def page(offset: str | None, limit: str | None, default_limit: int = DEFAULT_LIMIT) -> tuple[int, int]:
    return (integer("offset", offset, 0, 0, MAX_OFFSET),
            integer("limit", limit, default_limit, 1, MAX_LIMIT))


def list_body(items: list, offset: int, limit: int, ctx: Ctx) -> dict:
    """The contract's list envelope over one page of `items`."""
    return {"data": items[offset:offset + limit], "total": len(items), "offset": offset,
            "limit": limit, "meta": ctx.list_meta}


# Parameter descriptions, shared by the OpenAPI document and `GET /api/v1`.
PARAM_DOCS = {
    "q": "Free-text search: every word must appear in the title, funder, summary, "
         "eligibility or tags. Case and accents are ignored.",
    "tag": "Comma-separated tag slugs; every one must match. Values: /api/v1/taxonomy.",
    "region": "Region slug. National grants match every region. Values: /api/v1/taxonomy.",
    "funder": "Funder slug, as in /api/v1/funders.",
    "amount": "Amount band slug; matches grants whose stated range overlaps it. "
              "Values: /api/v1/taxonomy.",
    "deadline": "Deadline bucket: 30d, 90d, rolling or dated.",
    "status": "live (default), closed or all. Stale rows are never listed.",
    "sort": "deadline (default), recency or amount.",
    "offset": "Rows to skip. Offsets never expire.",
    "limit": f"Rows to return, 1 to {MAX_LIMIT}.",
    "sector": "Comma-separated sector tag slugs.",
    "fit_region": "Region slug of the organisation.",
    "fit_status": "Legal status of the organisation, from the entity vocabulary.",
    "size": "Annual revenue band of the organisation.",
    "need": "What the money is for.",
}


# --- Search --------------------------------------------------------------------

def search_params(ctx: Ctx, q, tag, region, funder, amount, deadline, status, sort) -> dict:
    """Validated `/api/v1/opportunities` parameters, or a 400 envelope."""
    cfg = ctx.cfg
    if q is not None and len(q) > MAX_Q_CHARS:
        raise invalid("q", [f"text of at most {MAX_Q_CHARS} characters"])
    tags = [t.strip().lower() for t in (tag or "").split(",") if t.strip()]
    if any(t not in cfg.tags for t in tags):
        raise invalid("tag", cfg.tags)
    funder = (funder or "").strip().lower() or None
    if funder is not None and (not SLUG_RE.match(funder) or funder not in
                               group_by_funder(ctx.rows("live", "closed"), ctx.country)):
        raise invalid("funder", ["the slugs listed at /api/v1/funders"])
    return {
        "q": (q or "").strip() or None,
        "tag": tags,
        "region": choice("region", region, cfg.regions),
        "funder": funder,
        "amount": choice("amount", amount, [b[0] for b in AMOUNT_BANDS]),
        "deadline": choice("deadline", deadline, list(DEADLINE_BUCKETS)),
        "status": choice("status", status, list(LIST_STATUSES), "live"),
        "sort": choice("sort", sort, list(SORTS), "deadline"),
    }


def filtered(ctx: Ctx, *, q, tag, region, funder, amount, deadline, status) -> list[dict]:
    """Rows of the requested statuses matching every filter. Never stale."""
    cfg = ctx.cfg
    rows = ctx.rows(*LIST_STATUSES[status])
    national = frozenset(fold(s) for s in cfg.national_scope_synonyms)
    needles = fold(q).split() if q else []
    funder_ids = None
    if funder:
        funder_ids = {r["id"] for r in group_by_funder(rows, ctx.country).get(funder, [])}
    return [
        r for r in rows
        if matches_q(r, needles)
        and matches_tags(r, tag)
        and matches_region(r, region, national)
        and (funder_ids is None or r.get("id") in funder_ids)
        and matches_amount(r, amount, cfg.currency)
        and matches_deadline(r, deadline, ctx.today)
    ]
