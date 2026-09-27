"""The keyless machine API: /api/v1/* and /out/{id}.

In-process with TestClient. Storage is redirected to tmp_path, the request
clock is pinned, the must-appear list is empty, and outbound sockets are
blocked so no test can reach a real model, mail or search API.
"""

import json
import socket
import sys
import types
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime

import pytest
from fastapi.testclient import TestClient

# Importing the server imports api.email and friends, which call load_dotenv()
# and may pull a developer's keys into os.environ. Import first, so the
# fixture's delenv below wins.
from api import server  # noqa: E402
from api import metrics, public_context, public_stats, published, request_log, sources, tenant
from api.public_data import INTERNAL_FIELDS
from api.rate_limit import public_limiter

NOW = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)        # 14:00, 10 Oct in NZ
FIRST_PUBLISH = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
SECOND_PUBLISH = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
BASE = "https://gobuga.test"
HOSTILE = '<script>alert("x")</script> & </title>\x07 Fund'

SECRET_ENV = ("ANTHROPIC_API_KEY", "RESEND_API_KEY", "TAVILY_API_KEY", "STRIPE_SECRET_KEY",
              "DIRECTOR_EMAIL", "INTERNAL_HIT_SECRET", "TRUSTED_PROXY_HOPS")


def _no_network(*args, **kwargs):
    raise RuntimeError("network access is blocked in this test")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in SECRET_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setenv("STARTUP_SWEEP_DISABLED", "1")
    monkeypatch.setenv("APP_URL", BASE + "/")
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setattr(sources, "must_appear_names", lambda country: [])
    monkeypatch.setattr(public_context, "now_utc", lambda: NOW)
    public_limiter.clear()
    public_stats.clear_cache()
    yield tmp_path
    public_limiter.clear()
    public_stats.clear_cache()


@pytest.fixture
def client():
    return TestClient(server.app, raise_server_exceptions=False)


def row(n, **kw):
    base = {
        "id": f"OPP-NZ-2026-10-{n:04d}",
        "country": "nz",
        "title": f"Programme {n}",
        "funder": f"Funder {n}",
        "deadline": "2026-11-30",
        "deadline_state": "dated",
        "amount_min": None,
        "amount_max": None,
        "currency": "NZD",
        "region": ["canterbury"],
        "tags": ["community"],
        "eligibility": "Incorporated societies",
        "summary": "A fund.",
        "source_url": f"https://funder{n}.example.nz/grants",
        "evidence_ids": [f"EV-{n:04d}"],
        "dedupe_key": f"funder-{n}|funder{n}.example.nz|{n}",
        "notes": "internal note",
        "first_seen": f"2026-09-{10 + n:02d}T00:00:00+00:00",
        "last_seen": "2026-10-04T20:00:00+00:00",
        "verified_at": "2026-10-04T22:00:00+00:00",
        "verified_by": "verify-pass/test",
        "source_excerpt": "Applications close soon.",
        "provenance": {"deadline": {"source_url": f"https://funder{n}.example.nz/grants",
                                    "verified_at": "2026-10-04T22:00:00+00:00",
                                    "excerpt": "Applications close soon.",
                                    "internal": "never shown"}},
    }
    base.update(kw)
    return base


def fixture_rows():
    return [
        row(1, funder="Foundation North", deadline="2026-10-25", region=["auckland"],
            tags=["community", "youth"], amount_min=5000, amount_max=20000,
            summary="Youth sport equipment"),
        row(2, funder="Sport NZ", deadline="2026-12-20", tags=["sport"],
            amount_min=50000, amount_max=200000),
        row(3, title="Māori arts fund", funder="ASB Community Trust", deadline="rolling",
            deadline_state="rolling-confirmed", region=["national"], tags=["arts", "community"],
            amount_min=1000),
        row(4, funder="Sport New Zealand", deadline="2026-09-01", deadline_state="closed",
            tags=["sport", "youth"]),
        row(5, funder="Sport NZ", deadline="2026-10-07", tags=["community"]),
        row(6, funder="Stale Fund", deadline="rolling", deadline_state="rolling-confirmed",
            verified_at="2026-07-01T00:00:00+00:00", tags=["sport"]),
        row(7, title=HOSTILE, funder="Evil & Co", deadline="2026-11-15", region=["otago"],
            tags=["arts"], amount_max=5000, currency="EUR"),
        row(8, funder="Odd Links Trust", deadline="2026-11-01", region=["national"],
            tags=["health"], source_url="javascript:alert(1)"),
        row(9, funder="Odd Links Trust", deadline="2026-11-02", tags=["health"],
            source_url="ftp://files.example/grant"),
    ]


def oid(n):
    return f"OPP-NZ-2026-10-{n:04d}"


LIVE = [oid(n) for n in (1, 2, 3, 7, 8, 9)]
CLOSED = [oid(4), oid(5)]
STALE = oid(6)


def _notifier(*args):
    raise AssertionError("publish should not be blocked")


@pytest.fixture
def dataset():
    """Two publishes: everything, then row 2's amount changes (row 5 has closed by then)."""
    published.publish_pool("nz", "2026-10", fixture_rows(), now=FIRST_PUBLISH, notifier=_notifier)
    changed = row(2, funder="Sport NZ", deadline="2026-12-20", tags=["sport"], amount_min=50000,
                  amount_max=250000, verified_at="2026-10-07T22:00:00+00:00")
    published.publish_pool("nz", "2026-10", [changed], now=SECOND_PUBLISH, notifier=_notifier)


def ids(resp):
    return [item["id"] for item in resp.json()["data"]]


# --- Empty state -------------------------------------------------------------------

JSON_LISTS = ["/api/v1/opportunities", "/api/v1/fit", "/api/v1/funders", "/api/v1/changes"]


@pytest.mark.parametrize("url", JSON_LISTS)
def test_empty_lists_are_well_formed(client, url):
    r = client.get(url)
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == [] and body["total"] == 0 and body["offset"] == 0
    assert body["meta"] == {"published_at": None, "sweep_month": None}


@pytest.mark.parametrize("url", ["/api/v1", "/api/v1/ids", "/api/v1/taxonomy", "/api/v1/stats",
                                 "/api/v1/stats/2026-10", "/api/v1/openapi.json"])
def test_empty_other_endpoints_are_well_formed(client, url):
    r = client.get(url)
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_empty_stats_has_zero_dataset(client):
    body = client.get("/api/v1/stats").json()
    assert body["dataset"]["live_count"] == 0 and body["dataset"]["funder_count"] == 0
    assert body["dataset"]["published_at"] is None and body["usage"]["clickouts"]["total"] == 0


@pytest.mark.parametrize("url", ["/api/v1/fit/feed.xml", "/api/v1/changes/feed.xml"])
def test_empty_feeds_parse(client, url):
    r = client.get(url)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/atom+xml")
    root = ET.fromstring(r.content)
    assert root.findall("{http://www.w3.org/2005/Atom}entry") == []


@pytest.mark.parametrize("url", ["/api/v1/opportunities/OPP-NZ-2026-10-0001",
                                 "/api/v1/funders/sport-nz", "/out/OPP-NZ-2026-10-0001"])
def test_empty_single_resources_are_404_envelopes(client, url):
    r = client.get(url, follow_redirects=False)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


# --- Record shape ------------------------------------------------------------------

RECORD_KEYS = {
    "id", "country", "status", "title", "funder", "deadline", "amount", "region", "tags",
    "eligibility", "eligible_entities", "summary", "source_url", "apply_url", "canonical_url",
    "json_url", "first_seen", "last_seen", "closed_at", "verified_at", "verified_by",
    "provenance", "attribution",
}


def test_record_shape_matches_contract(client, dataset):
    r = client.get(f"/api/v1/opportunities/{oid(1)}")
    assert r.status_code == 200
    rec = r.json()
    assert set(rec) == RECORD_KEYS
    assert rec["status"] == "live" and rec["country"] == "nz"
    assert rec["funder"] == {"name": "Foundation North", "slug": "foundation-north",
                             "url": f"{BASE}/funders/foundation-north"}
    assert rec["deadline"] == {"state": "dated", "date": "2026-10-25",
                               "verified_at": "2026-10-04T22:00:00+00:00",
                               "source_url": "https://funder1.example.nz/grants",
                               "excerpt": "Applications close soon."}
    assert rec["amount"] == {"min": 5000, "max": 20000, "currency": "NZD"}
    assert rec["canonical_url"] == f"{BASE}/grants/{oid(1)}"
    assert rec["json_url"] == f"{BASE}/grants/{oid(1)}.json"
    assert rec["apply_url"] == f"{BASE}/out/{oid(1)}"
    assert rec["attribution"] == {"source": "GoBuga", "url": rec["canonical_url"],
                                  "retrieved_at": "2026-10-10", "licence": "attribution-required"}
    assert rec["provenance"] == {"deadline": {"source_url": "https://funder1.example.nz/grants",
                                              "verified_at": "2026-10-04T22:00:00+00:00",
                                              "excerpt": "Applications close soon."}}
    assert rec["eligible_entities"] == ["incorporated-society"]
    assert rec["closed_at"] is None and "related" not in rec and "notice" not in rec


def test_alias_is_canonicalised_in_record(client, dataset):
    rec = client.get(f"/api/v1/opportunities/{oid(3)}").json()
    assert rec["funder"]["name"] == "Foundation North"
    assert rec["deadline"]["state"] == "rolling-confirmed" and rec["deadline"]["date"] is None


@pytest.mark.parametrize("url", [
    "/api/v1/opportunities?status=all&limit=100", f"/api/v1/opportunities/{oid(1)}",
    f"/api/v1/opportunities/{oid(4)}", f"/api/v1/opportunities/{STALE}", "/api/v1/fit",
    "/api/v1/funders/sport-nz", "/api/v1/changes", "/api/v1/ids",
])
def test_internal_fields_never_appear(client, dataset, url):
    text = client.get(url).text
    for field in INTERNAL_FIELDS:
        assert f'"{field}"' not in text
    assert "EV-000" not in text and "internal note" not in text and "never shown" not in text


def test_app_url_is_read_per_request(client, dataset, monkeypatch):
    monkeypatch.setenv("APP_URL", "https://md.example///")
    rec = client.get(f"/api/v1/opportunities/{oid(1)}").json()
    assert rec["canonical_url"] == f"https://md.example/grants/{oid(1)}"


# --- Status: closed and stale ---------------------------------------------------------

def test_deadline_passing_between_sweeps_closes_on_the_day(client, dataset):
    assert client.get(f"/api/v1/opportunities/{oid(5)}").json()["status"] == "closed"
    assert oid(5) not in ids(client.get("/api/v1/opportunities"))


def test_closed_record_carries_related_live_grants(client, dataset):
    rec = client.get(f"/api/v1/opportunities/{oid(4)}").json()
    assert rec["status"] == "closed"
    assert rec["related"] == [
        {"id": oid(1), "title": "Programme 1", "canonical_url": f"{BASE}/grants/{oid(1)}"},
        {"id": oid(2), "title": "Programme 2", "canonical_url": f"{BASE}/grants/{oid(2)}"},
    ]
    assert rec["closed_at"]


def test_stale_record_resolves_with_notice_and_noindex(client, dataset):
    r = client.get(f"/api/v1/opportunities/{STALE}")
    assert r.status_code == 200
    assert r.json()["status"] == "stale" and r.json()["notice"]
    assert r.headers["x-robots-tag"] == "noindex"
    live = client.get(f"/api/v1/opportunities/{oid(1)}")
    assert "x-robots-tag" not in live.headers


@pytest.mark.parametrize("url", [
    "/api/v1/opportunities?status=all&limit=100", "/api/v1/opportunities?status=all&sort=recency",
    "/api/v1/opportunities?tag=sport&status=all", "/api/v1/ids", "/api/v1/fit?sector=sport",
    "/api/v1/fit/feed.xml?sector=sport", "/api/v1/funders", "/api/v1/changes",
    "/api/v1/changes/feed.xml",
])
def test_stale_rows_appear_in_no_list_or_feed(client, dataset, url):
    text = client.get(url).text
    assert STALE not in text and "Stale Fund" not in text and "stale-fund" not in text


def test_stale_funder_has_no_page(client, dataset):
    assert client.get("/api/v1/funders/stale-fund").status_code == 404


def test_unknown_or_malformed_id_is_404(client, dataset):
    for bad in ("OPP-NZ-2099-01-0001", "a" * 200, "%00", "OPP%2F..%2Fx"):
        r = client.get(f"/api/v1/opportunities/{bad}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


# --- Search ----------------------------------------------------------------------------

def test_default_list_is_live_in_deadline_order(client, dataset):
    body = client.get("/api/v1/opportunities").json()
    assert ids_of(body) == [oid(1), oid(8), oid(9), oid(7), oid(2), oid(3)]
    assert body["total"] == 6 and body["limit"] == 20
    assert body["meta"] == {"published_at": SECOND_PUBLISH.isoformat(), "sweep_month": "2026-10"}


def ids_of(body):
    return [item["id"] for item in body["data"]]


@pytest.mark.parametrize("query,expected", [
    ("status=closed", [oid(5), oid(4)]),
    ("status=all", [oid(1), oid(8), oid(9), oid(7), oid(2), oid(3), oid(5), oid(4)]),
    ("tag=community,youth", [oid(1)]),
    ("tag=SPORT", [oid(2)]),
    ("region=auckland", [oid(1), oid(8), oid(3)]),
    ("region=national", [oid(8), oid(3)]),
    ("funder=foundation-north", [oid(1), oid(3)]),
    ("funder=sport-nz&status=all", [oid(2), oid(5), oid(4)]),
    ("amount=under-10k", [oid(1), oid(3)]),
    ("amount=50k-250k", [oid(2), oid(3)]),
    ("amount=over-1m", [oid(3)]),
    ("deadline=30d", [oid(1), oid(8), oid(9)]),
    ("deadline=90d", [oid(1), oid(8), oid(9), oid(7), oid(2)]),
    ("deadline=rolling", [oid(3)]),
    ("deadline=dated&status=closed", [oid(5)]),
    ("q=YOUTH+equipment", [oid(1)]),
    ("q=maori", [oid(3)]),
    ("q=m%C4%81ori+ARTS", [oid(3)]),
    ("sort=recency", [oid(9), oid(8), oid(7), oid(3), oid(2), oid(1)]),
    ("sort=amount", [oid(2), oid(1), oid(7), oid(3), oid(8), oid(9)]),
    ("tag=sport&region=auckland", []),
    ("utm_source=newsletter&tag=health", [oid(8), oid(9)]),  # unknown names are ignored
])
def test_search_filters(client, dataset, query, expected):
    r = client.get(f"/api/v1/opportunities?{query}")
    assert r.status_code == 200, r.text
    assert ids_of(r.json()) == expected


def test_amount_band_ignores_other_currencies(client, dataset):
    # Row 7 states EUR 5,000: not converted, so in no NZD band.
    assert oid(7) not in ids(client.get("/api/v1/opportunities?amount=under-10k"))


@pytest.mark.parametrize("param,value", [
    ("tag", "sport,notatag"), ("region", "mars"), ("funder", "nobody"), ("funder", "../etc"),
    ("amount", "huge"), ("deadline", "7d"), ("status", "stale"), ("sort", "random"),
    ("offset", "-1"), ("offset", "abc"), ("offset", "1e3"), ("limit", "0"), ("limit", "101"),
    ("limit", "１０"), ("q", "x" * 201),
])
def test_invalid_search_parameters_are_400_naming_the_parameter(client, dataset, param, value):
    r = client.get("/api/v1/opportunities", params={param: value})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "invalid_parameter"
    assert f"'{param}'" in err["message"] and "Allowed values" in err["message"]


def test_invalid_value_lists_allowed_values(client, dataset):
    msg = client.get("/api/v1/opportunities?deadline=soon").json()["error"]["message"]
    assert all(b in msg for b in ("30d", "90d", "rolling", "dated"))
    msg = client.get("/api/v1/opportunities?amount=x").json()["error"]["message"]
    assert "under-10k" in msg and "over-1m" in msg


def test_error_messages_never_echo_input(client, dataset):
    r = client.get("/api/v1/opportunities", params={"region": "<script>alert(1)</script>"})
    assert r.status_code == 400 and "<script>" not in r.text


def test_empty_parameters_mean_no_filter(client, dataset):
    r = client.get("/api/v1/opportunities?tag=&region=&q=&amount=&deadline=&status=&sort=&offset=&limit=")
    assert r.status_code == 200 and r.json()["total"] == 6


@pytest.mark.parametrize("query,expected_ids,offset,limit", [
    ("offset=2&limit=2", [oid(9), oid(7)], 2, 2),
    ("offset=5&limit=100", [oid(3)], 5, 100),
    ("offset=6", [], 6, 20),
    ("offset=1000000", [], 1000000, 20),
])
def test_pagination_bounds(client, dataset, query, expected_ids, offset, limit):
    body = client.get(f"/api/v1/opportunities?{query}").json()
    assert ids_of(body) == expected_ids
    assert (body["total"], body["offset"], body["limit"]) == (6, offset, limit)


def test_offset_above_maximum_is_400(client, dataset):
    assert client.get("/api/v1/opportunities?offset=1000001").status_code == 400


def test_ids_lists_live_and_closed(client, dataset):
    body = client.get("/api/v1/ids").json()
    assert [d["id"] for d in body["data"]] == sorted(LIVE + CLOSED)
    first = body["data"][0]
    assert set(first) == {"id", "status", "last_seen", "canonical_url"}
    assert first["canonical_url"] == f"{BASE}/grants/{oid(1)}"


# --- Fit -------------------------------------------------------------------------------

def test_fit_ranks_live_rows_with_score_and_why(client, dataset):
    body = client.get("/api/v1/fit?sector=sport").json()
    assert body["data"][0]["id"] == oid(2)
    assert set(ids_of(body)) == set(LIVE)
    top = body["data"][0]
    assert RECORD_KEYS <= set(top) and isinstance(top["score"], float) and top["why"]
    assert body["canonical_query"] == "sector=sport"
    assert body["canonical_url"] == f"{BASE}/fit?sector=sport"
    assert body["json_url"] == f"{BASE}/fit.json?sector=sport"
    assert body["feed_url"] == f"{BASE}/fit/feed.xml?sector=sport"


def test_fit_equivalent_queries_advertise_the_same_url(client, dataset):
    a = client.get("/api/v1/fit?sector=Youth,sport&region=Auckland&status=Incorporated%20Society")
    b = client.get("/api/v1/fit?status=incorporated-society&region=auckland&sector=sport,youth")
    assert a.status_code == b.status_code == 200
    for key in ("canonical_query", "canonical_url", "json_url", "feed_url", "data"):
        assert a.json()[key] == b.json()[key]
    assert a.json()["canonical_query"] == "sector=sport,youth&region=auckland&status=incorporated-society"
    assert a.headers["etag"] == b.headers["etag"]


def test_fit_with_no_parameters_has_bare_canonical_url(client, dataset):
    body = client.get("/api/v1/fit").json()
    assert body["canonical_query"] == "" and body["canonical_url"] == f"{BASE}/fit"


@pytest.mark.parametrize("param,value", [
    ("sector", "sport,space"), ("region", "mars"), ("status", "alien"), ("size", "huge"),
    ("need", "yacht"), ("offset", "x"), ("limit", "500"),
])
def test_fit_invalid_parameters_are_400(client, dataset, param, value):
    r = client.get("/api/v1/fit", params={param: value})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_parameter"
    assert f"'{param}'" in r.json()["error"]["message"]
    assert value not in r.json()["error"]["message"]


def test_fit_pagination(client, dataset):
    full = ids(client.get("/api/v1/fit?sector=sport"))
    body = client.get("/api/v1/fit?sector=sport&offset=1&limit=2").json()
    assert ids_of(body) == full[1:3] and body["total"] == len(full)


def test_fit_feed_matches_fit_order(client, dataset):
    order = ids(client.get("/api/v1/fit?sector=arts"))
    r = client.get("/api/v1/fit/feed.xml?sector=arts")
    root = ET.fromstring(r.content)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry_ids = [e.find("a:id", ns).text for e in root.findall("a:entry", ns)]
    assert entry_ids == [f"{BASE}/grants/{i}" for i in order]
    assert root.find("a:id", ns).text == f"{BASE}/fit?sector=arts"


# --- Atom escaping -----------------------------------------------------------------------

@pytest.mark.parametrize("url", ["/api/v1/fit/feed.xml?sector=arts", "/api/v1/changes/feed.xml"])
def test_feeds_escape_hostile_titles(client, dataset, url):
    r = client.get(url)
    assert b"<script>" not in r.content and b"&lt;script&gt;" in r.content
    root = ET.fromstring(r.content)   # would raise on the raw \x07 or unescaped markup
    titles = [e.text for e in root.iter("{http://www.w3.org/2005/Atom}title")]
    assert HOSTILE.replace("\x07", "") in titles


# --- Funders ------------------------------------------------------------------------------

def test_funders_index(client, dataset):
    body = client.get("/api/v1/funders").json()
    by_slug = {f["slug"]: f for f in body["data"]}
    assert list(by_slug) == ["evil-co", "foundation-north", "odd-links-trust", "sport-nz"]
    assert by_slug["sport-nz"] == {
        "name": "Sport NZ", "slug": "sport-nz", "url": f"{BASE}/funders/sport-nz",
        "json_url": f"{BASE}/funders/sport-nz.json", "live_count": 1, "closed_count": 2,
    }
    assert by_slug["foundation-north"]["live_count"] == 2
    assert body["total"] == 4 and body["limit"] == 100


def test_funder_detail_lists_live_and_closed_records(client, dataset):
    body = client.get("/api/v1/funders/sport-nz").json()
    assert [r["id"] for r in body["live"]] == [oid(2)]
    assert [r["id"] for r in body["closed"]] == [oid(5), oid(4)]
    assert set(body["live"][0]) == RECORD_KEYS


@pytest.mark.parametrize("slug", ["nobody", "Sport-NZ", "a" * 300])
def test_unknown_funder_is_404(client, dataset, slug):
    r = client.get(f"/api/v1/funders/{slug}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


# --- Taxonomy -----------------------------------------------------------------------------

def test_taxonomy_exposes_every_vocabulary(client):
    body = client.get("/api/v1/taxonomy").json()
    assert body["currency"] == "NZD"
    assert {"slug": "sport", "label": "Sport & recreation"} in body["tags"]
    assert "canterbury" in body["regions"] and "national" in body["regions"]
    assert "incorporated-society" in body["entity_vocab"]
    assert [b["slug"] for b in body["size_bands"]] == ["under-50k", "50k-250k", "250k-1m", "over-1m"]
    assert body["needs"] == ["operating", "capital", "project", "event", "equipment"]
    assert [b["slug"] for b in body["amount_bands"]] == [
        "under-10k", "10k-50k", "50k-250k", "250k-1m", "over-1m"]
    assert all(b["currency"] == "NZD" for b in body["amount_bands"])
    assert [b["slug"] for b in body["deadline_buckets"]] == ["30d", "90d", "rolling", "dated"]
    assert body["list_statuses"] == ["live", "closed", "all"]
    assert [s["slug"] for s in body["sorts"]] == ["deadline", "recency", "amount"]


def test_taxonomy_follows_the_country(client, monkeypatch):
    monkeypatch.setenv("GOBUGA_COUNTRY", "md")
    body = client.get("/api/v1/taxonomy").json()
    assert body["country"] == "md" and body["currency"] == "MDL"
    assert "chisinau" in body["regions"]
    assert all(b["currency"] == "MDL" for b in body["amount_bands"])


# --- Changes -------------------------------------------------------------------------------

def test_changes_expand_ids_newest_first(client, dataset):
    body = client.get("/api/v1/changes").json()
    assert body["total"] == 2
    latest, first = body["data"]
    assert latest["published_at"] == SECOND_PUBLISH.isoformat()
    assert latest["changed"] == [{"id": oid(2), "title": "Programme 2",
                                  "canonical_url": f"{BASE}/grants/{oid(2)}",
                                  "fields": ["amount_max"]}]
    assert [c["id"] for c in latest["closed"]] == [oid(5)]
    assert latest["new"] == []
    assert sorted(c["id"] for c in first["new"]) == sorted(LIVE + CLOSED)


def test_changes_feed_has_one_entry_per_change(client, dataset):
    root = ET.fromstring(client.get("/api/v1/changes/feed.xml").content)
    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    assert len(entries) == 1 + 1 + 8
    entry_ids = [e.find("{http://www.w3.org/2005/Atom}id").text for e in entries]
    assert len(set(entry_ids)) == len(entry_ids)


def test_changes_pagination(client, dataset):
    body = client.get("/api/v1/changes?offset=1&limit=1").json()
    assert len(body["data"]) == 1 and body["data"][0]["published_at"] == FIRST_PUBLISH.isoformat()


# --- Conditional GET -------------------------------------------------------------------------

def test_read_responses_carry_cache_headers(client, dataset):
    r = client.get("/api/v1/opportunities")
    assert r.headers["etag"].startswith('"')
    assert r.headers["cache-control"] == "public, max-age=600"
    # Local midnight on 10 Oct in Auckland is later than the last publish.
    assert r.headers["last-modified"] == "Fri, 09 Oct 2026 11:00:00 GMT"
    assert client.get("/api/v1/stats").headers["cache-control"] == "public, max-age=60"


@pytest.mark.parametrize("url", ["/api/v1/opportunities", f"/api/v1/opportunities/{oid(1)}",
                                 "/api/v1/fit?sector=sport", "/api/v1/fit/feed.xml",
                                 "/api/v1/taxonomy", "/api/v1/changes/feed.xml", "/api/v1/stats"])
def test_if_none_match_gives_304(client, dataset, url):
    first = client.get(url)
    etag = first.headers["etag"]
    for header in (etag, f"W/{etag}", f'"nope", {etag}', "*"):
        r = client.get(url, headers={"If-None-Match": header})
        assert r.status_code == 304 and r.content == b""
        assert r.headers["etag"] == etag
    assert client.get(url, headers={"If-None-Match": '"other"'}).status_code == 200


def test_if_modified_since(client, dataset):
    later = format_datetime(datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc), usegmt=True)
    earlier = format_datetime(datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc), usegmt=True)
    assert client.get("/api/v1/ids", headers={"If-Modified-Since": later}).status_code == 304
    assert client.get("/api/v1/ids", headers={"If-Modified-Since": earlier}).status_code == 200
    assert client.get("/api/v1/ids", headers={"If-Modified-Since": "garbage"}).status_code == 200
    # If-None-Match wins when both are sent.
    r = client.get("/api/v1/ids", headers={"If-Modified-Since": later, "If-None-Match": '"x"'})
    assert r.status_code == 200


def test_etag_changes_with_the_day(client, dataset, monkeypatch):
    before = client.get("/api/v1/fit?sector=sport").headers["etag"]
    monkeypatch.setattr(public_context, "now_utc", lambda: datetime(2026, 10, 11, 1, 0, tzinfo=timezone.utc))
    assert client.get("/api/v1/fit?sector=sport").headers["etag"] != before


# --- Click-out -------------------------------------------------------------------------------

def _clickouts(tmp_path):
    lines = []
    for f in (tmp_path / "platform" / "metrics" / "nz").glob("*.jsonl"):
        lines += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return [l for l in lines if l.get("kind") == "clickout"]


def test_out_redirects_to_stored_source_and_counts(client, dataset, isolated):
    r = client.get(f"/out/{oid(1)}?url=https://evil.example/",
                   headers={"Referer": "https://chatgpt.com/c/1", "User-Agent": "Mozilla/5.0"},
                   follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "https://funder1.example.nz/grants"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-robots-tag"] == "noindex, nofollow"
    [line] = _clickouts(isolated)
    assert line["opp_id"] == oid(1) and line["funder"] == "Foundation North"
    assert line["ref"] == "assistant" and "ua" not in line
    assert "testclient" not in json.dumps(line)


def test_out_works_for_closed_rows(client, dataset):
    r = client.get(f"/out/{oid(4)}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://funder4.example.nz/grants"


@pytest.mark.parametrize("n", [8, 9])
def test_out_refuses_non_http_schemes(client, dataset, isolated, n):
    r = client.get(f"/out/{oid(n)}", follow_redirects=False)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert "location" not in r.headers
    assert _clickouts(isolated) == []


@pytest.mark.parametrize("url", ["/out/OPP-NZ-2099-01-0001", "/out/OPP%2F..%2Fx", "/out/x%0d%0aSet-Cookie:a=b"])
def test_out_unknown_id_is_404(client, dataset, isolated, url):
    r = client.get(url, follow_redirects=False)
    assert r.status_code == 404
    assert _clickouts(isolated) == []


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://x.example/", "//evil.example/",
                                 "https://", "https://ok.example/a b", "https://ok.example/\r\nX: y",
                                 "data:text/html,hi", None, 42])
def test_safe_destination_rejects(url):
    from api.public_v1 import _safe_destination
    assert _safe_destination(url) is None


def test_safe_destination_accepts_http_and_https():
    from api.public_v1 import _safe_destination
    assert _safe_destination("http://a.example/x?y=1") == "http://a.example/x?y=1"
    assert _safe_destination("HTTPS://a.example") == "HTTPS://a.example"


# --- Stats -----------------------------------------------------------------------------------

@pytest.fixture
def seeded_metrics(monkeypatch):
    """A month of hits and click-outs, written directly; the request log is
    then silenced so the test's own requests do not move the counts."""
    at = datetime(2026, 10, 3, tzinfo=timezone.utc)
    gpt = "Mozilla/5.0 (compatible; GPTBot/1.1; +https://openai.com/gptbot)"
    human = "Mozilla/5.0 (Macintosh) SecretBrowser/9"
    metrics.record_hit("/api/v1/fit", gpt, None, surface="api", country="nz", now=at)
    metrics.record_hit("/api/v1/fit/feed.xml", human, None, surface="api", country="nz", now=at)
    metrics.record_hit("/api/v1/opportunities", human, "https://www.google.co.nz/", surface="api",
                       country="nz", now=at)
    metrics.record_hit("/api/v1/zzz-attacker-chosen", human, None, surface="api", country="nz", now=at)
    metrics.record_hit("mcp:search_grants", "ClaudeBot/1.0", None, surface="mcp", country="nz", now=at)
    metrics.record_hit("mcp:evil_tool_name", "ClaudeBot/1.0", None, surface="mcp", country="nz", now=at)
    metrics.record_hit("/grants/x", gpt, None, surface="page", country="nz", now=at)
    metrics.record_clickout("OPP-NZ-2026-10-0001", "Foundation North", "https://chatgpt.com/c/9",
                            human, country="nz", now=at)
    metrics.record_clickout("OPP-NZ-2026-10-0002", "Sport NZ", None, gpt, country="nz", now=at)
    monkeypatch.setattr(request_log, "metrics", types.SimpleNamespace(record_hit=lambda *a, **k: None))


def test_stats_publishes_classes_and_counts_only(client, dataset, seeded_metrics):
    r = client.get("/api/v1/stats")
    assert r.status_code == 200
    body = r.json()
    usage = body["usage"]
    assert usage["month"] == "2026-10"
    assert usage["clickouts"] == {"total": 2, "by_referrer": {
        "assistant": 1, "search": 0, "email": 0, "direct": 1, "other": 0}}
    assert usage["fit_urls_built"] == 2
    assert usage["api_calls"]["total"] == 4
    assert usage["api_calls"]["by_endpoint"]["/api/v1/opportunities"] == 1
    assert usage["mcp_calls"] == {"total": 2, "by_tool": {
        "search_grants": 1, "get_grant": 0, "fit_grants": 0}}
    assert usage["crawler_hits"]["by_agent"]["gptbot"] == 2  # hit lines only
    assert usage["crawler_hits"]["by_agent"]["claudebot"] == 2
    assert "human" not in usage["crawler_hits"]["by_agent"]
    ds = body["dataset"]
    assert (ds["live_count"], ds["closed_count"], ds["funder_count"]) == (6, 2, 4)
    assert ds["last_sweep"] == "2026-10" and ds["next_sweep_due"] == "2026-12"
    assert ds["published_at"] == SECOND_PUBLISH.isoformat()
    assert body["subscribers"] == {"confirmed": 0}  # the real module, empty list
    text = r.text
    for leak in ("Mozilla", "GPTBot/1.1", "SecretBrowser", "ClaudeBot/1.0", "chatgpt.com",
                 "google.co.nz", "zzz-attacker", "evil_tool", "OPP-NZ", "Foundation North"):
        assert leak not in text


def test_stats_month_route(client, seeded_metrics):
    body = client.get("/api/v1/stats/2026-10").json()
    assert body["usage"]["clickouts"]["total"] == 2
    assert "dataset" not in body
    assert client.get("/api/v1/stats/2025-01").json()["usage"]["clickouts"]["total"] == 0


@pytest.mark.parametrize("month,status", [
    ("2026-13", 400), ("2026-00", 400), ("26-10", 400), ("2026-1", 400), ("abcd-ef", 400),
    ("2026-11", 404), ("2999-01", 404),
])
def test_stats_month_validation(client, month, status):
    r = client.get(f"/api/v1/stats/{month}")
    assert r.status_code == status
    assert "error" in r.json()


def test_stats_counters_are_cached(client, monkeypatch):
    calls = []
    real = metrics.monthly_counters
    monkeypatch.setattr(metrics, "monthly_counters", lambda c, m: calls.append(m) or real(c, m))
    client.get("/api/v1/stats")
    client.get("/api/v1/stats")
    client.get("/api/v1/stats/2026-10")
    assert calls == ["2026-10"]


def test_stats_publishes_only_confirmed_subscribers(client, monkeypatch):
    fake = types.ModuleType("api.subscribers")
    fake.subscriber_counts = lambda country: {"confirmed": 3, "pending": 7, "unsubscribed": 1}
    monkeypatch.setitem(sys.modules, "api.subscribers", fake)
    body = client.get("/api/v1/stats").json()
    assert body["subscribers"] == {"confirmed": 3}
    assert "pending" not in json.dumps(body)


def test_stats_omits_subscribers_when_the_counter_fails(client, monkeypatch):
    fake = types.ModuleType("api.subscribers")
    fake.subscriber_counts = lambda country: 1 / 0
    monkeypatch.setitem(sys.modules, "api.subscribers", fake)
    r = client.get("/api/v1/stats")
    assert r.status_code == 200 and "subscribers" not in r.json()


# --- Self-description -------------------------------------------------------------------------

def test_index_describes_the_api(client):
    body = client.get("/api/v1").json()
    assert body["country"] == "nz" and body["base_url"] == BASE
    assert body["openapi_url"] == f"{BASE}/api/v1/openapi.json"
    assert body["licence"]["id"] == "attribution-required"
    paths = {e["path"] for e in body["endpoints"]}
    assert {"/api/v1/opportunities", "/api/v1/opportunities/{opp_id}", "/api/v1/fit",
            "/api/v1/fit/feed.xml", "/api/v1/funders", "/api/v1/funders/{slug}", "/api/v1/ids",
            "/api/v1/taxonomy", "/api/v1/changes", "/api/v1/changes/feed.xml", "/api/v1/stats",
            "/api/v1/stats/{month}", "/out/{opp_id}", "/api/v1/openapi.json", "/api/v1"} == paths
    search = next(e for e in body["endpoints"] if e["path"] == "/api/v1/opportunities")
    assert search["query_parameters"] == ["q", "tag", "region", "funder", "amount", "deadline",
                                          "status", "sort", "offset", "limit"]
    twins = {t["public"]: t["backend"] for t in body["url_scheme"]}
    assert twins["/grants/{id}.json"] == "/api/v1/opportunities/{id}"
    assert twins["/fit/feed.xml"] == "/api/v1/fit/feed.xml"


def test_every_twin_backend_route_exists(client):
    from api.public_v1 import URL_SCHEME
    paths = {e["path"].replace("{opp_id}", "{id}") for e in client.get("/api/v1").json()["endpoints"]}
    for twin in URL_SCHEME:
        assert twin["backend"] in paths, twin


def test_openapi_describes_only_public_routes(client):
    r = client.get("/api/v1/openapi.json")
    doc = r.json()
    assert doc["openapi"].startswith("3.")
    assert doc["servers"] == [{"url": BASE}]
    assert all(p.startswith("/api/v1") or p.startswith("/out/") for p in doc["paths"])
    assert len(doc["paths"]) == 15
    for workspace in ("/api/auth", "/api/cases", "/api/org", "/api/admin", "/api/billing",
                      "/api/public/", "/api/internal", "/api/opportunities\"", "/api/chat",
                      "/api/databank", "/api/usage", "/api/health"):
        assert workspace not in r.text
    assert '"422"' not in r.text and "ValidationError" not in r.text
    op = doc["paths"]["/api/v1/opportunities"]["get"]
    assert op["description"] and any(p["name"] == "tag" and p.get("description") for p in op["parameters"])


def test_internal_openapi_stays_hidden(client):
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


# --- Robustness -------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "/api/v1/opportunities?limit=%00", "/api/v1/opportunities?q=%00%ff", "/api/v1/opportunities?tag=,,,",
    "/api/v1/opportunities?offset=99999999999999999999", "/api/v1/fit?sector=%E2%80%AE",
    "/api/v1/fit?sector=,,", "/api/v1/opportunities?status=ALL", "/api/v1/nope",
])
def test_malformed_queries_never_500(client, dataset, url):
    r = client.get(url)
    assert r.status_code < 500
    assert r.headers["content-type"].startswith("application/json")


def test_legacy_redacted_feed_still_served(client):
    r = client.get("/api/public/opportunities")
    assert r.status_code == 200 and "opportunities" in r.json()


# --- CORS ----------------------------------------------------------------------------------

def test_public_api_readable_from_any_origin(client):
    r = client.get("/api/v1/taxonomy", headers={"Origin": "https://agent.example"})
    assert r.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in r.headers
    assert "ETag" in r.headers["access-control-expose-headers"]


def test_public_api_preflight_from_any_origin(client):
    r = client.options("/api/v1/opportunities", headers={
        "Origin": "https://agent.example", "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "If-None-Match"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "*"
    assert "POST" not in r.headers["access-control-allow-methods"]


def test_public_api_errors_carry_cors_headers(client):
    r = client.get("/api/v1/opportunities?tag=x", headers={"Origin": "https://agent.example"})
    assert r.status_code == 400 and r.headers["access-control-allow-origin"] == "*"


def test_workspace_cors_is_unchanged(client):
    pre = client.options("/api/cases", headers={
        "Origin": "https://agent.example", "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 400
    assert "access-control-allow-origin" not in pre.headers
    r = client.get("/api/health", headers={"Origin": "https://agent.example"})
    assert "access-control-allow-origin" not in r.headers
    ok = client.get("/api/health", headers={"Origin": "http://localhost:3002"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3002"
