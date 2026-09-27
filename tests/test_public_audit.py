"""Audit additions to the public API: funder slugs, click-out counting,
HEAD requests, the rate-limit override and CORS on a 500."""

import json

import pytest
from fastapi.testclient import TestClient

import api.tenant as tenant
from api import published, rate_limit, server, sources
from api.country_config import clear_config_cache
from api.funders import slugify_funder

NOW_ROW = {
    "id": "OPP-NZ-2026-10-0001", "country": "nz", "title": "Community Fund",
    "funder": "Example Trust", "deadline": "rolling", "deadline_state": "rolling-confirmed",
    "amount_min": None, "amount_max": 5000, "currency": "NZD", "region": ["national"],
    "tags": ["community"], "eligibility": "", "summary": "",
    "source_url": "https://funder.example/grants", "dedupe_key": "example-trust|funder.example|rolling",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.delenv("PUBLIC_RATE_LIMIT_PER_MIN", raising=False)
    monkeypatch.setattr(sources, "must_appear_names", lambda country: [])
    clear_config_cache()
    rate_limit.public_limiter.clear()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    published.publish_pool("nz", now.strftime("%Y-%m"),
                           [{**NOW_ROW, "verified_at": stamp, "first_seen": stamp, "last_seen": stamp}],
                           now=now, notifier=lambda *a: None)
    yield TestClient(server.app, raise_server_exceptions=False)
    rate_limit.public_limiter.clear()
    clear_config_cache()


def clickouts(tmp_path_platform):
    lines = []
    for f in tmp_path_platform.glob("metrics/nz/*.jsonl"):
        lines += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return [l for l in lines if l.get("kind") == "clickout"]


# --- Funder slugs ------------------------------------------------------------

@pytest.mark.parametrize("name, slug", [
    ("Foundation North", "foundation-north"),                 # ASCII is unchanged
    ("Te Puni Kōkiri", "te-puni-kokiri"),
    ("Fundația Soros Moldova", "fundatia-soros-moldova"),
    ("Agenția Națională pentru Cercetare și Dezvoltare",
     "agentia-nationala-pentru-cercetare-si-dezvoltare"),
    ("Фонд Сорос Молдова", "fond-soros-moldova"),
    ("Швейцарское агентство", "shveitsarskoe-agentstvo"),
])
def test_funder_slugs_keep_their_letters(name, slug):
    assert slugify_funder(name, "md") == slug


def test_two_cyrillic_funders_do_not_share_a_slug():
    assert slugify_funder("Фонд Восток", "md") != slugify_funder("Фонд Запад", "md")
    assert slugify_funder("Фонд Восток", "md") != ""


# --- Click-outs count people --------------------------------------------------

def test_a_crawler_following_out_is_redirected_but_not_counted(client, tmp_path):
    r = client.get("/out/OPP-NZ-2026-10-0001", follow_redirects=False,
                   headers={"User-Agent": "Mozilla/5.0 (compatible; GPTBot/1.2)"})
    assert r.status_code == 302
    assert clickouts(tmp_path / "platform") == []


def test_a_person_following_out_is_counted(client, tmp_path):
    r = client.get("/out/OPP-NZ-2026-10-0001", follow_redirects=False,
                   headers={"User-Agent": "Mozilla/5.0 (Macintosh) Safari/605"})
    assert r.status_code == 302
    assert len(clickouts(tmp_path / "platform")) == 1


# --- HEAD ---------------------------------------------------------------------

@pytest.mark.parametrize("url", ["/api/v1", "/api/v1/opportunities",
                                 "/api/v1/opportunities/OPP-NZ-2026-10-0001",
                                 "/api/v1/changes/feed.xml"])
def test_head_is_a_bodiless_get(client, url):
    got = client.get(url)
    head = client.head(url)
    assert head.status_code == got.status_code == 200
    assert head.content == b""
    assert head.headers["etag"] == got.headers["etag"]
    assert head.headers["access-control-allow-origin"] == "*"


def test_head_on_a_workspace_route_is_unchanged(client):
    assert client.head("/api/cases").status_code == 405


# --- Rate-limit override --------------------------------------------------------

def test_limit_can_be_set_from_the_environment(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_RATE_LIMIT_PER_MIN", "3")
    codes = [client.get("/api/v1/taxonomy").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


@pytest.mark.parametrize("bad", ["0", "-5", "lots", ""])
def test_a_bad_override_falls_back_to_the_default(client, monkeypatch, bad):
    monkeypatch.setenv("PUBLIC_RATE_LIMIT_PER_MIN", bad)
    assert rate_limit.public_limit_override() is None
    assert all(client.get("/api/v1/taxonomy").status_code == 200 for _ in range(5))


# --- CORS on a 500 --------------------------------------------------------------

def test_a_500_on_the_public_api_is_readable_from_any_origin(client, monkeypatch):
    from api import public_v1

    def boom(*a, **kw):
        raise RuntimeError("private detail")
    monkeypatch.setattr(public_v1, "request_context", boom)
    r = client.get("/api/v1/taxonomy", headers={"Origin": "https://someone.example"})
    assert r.status_code == 500
    assert r.headers["access-control-allow-origin"] == "*"
    assert "private detail" not in r.text


# --- Audit additions after the public pages landed -------------------------------

def hits(tmp_path_platform):
    lines = []
    for f in tmp_path_platform.glob("metrics/nz/*.jsonl"):
        lines += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return [l for l in lines if l.get("kind") == "hit"]


def test_the_frontends_own_reads_are_not_counted(client, tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNAL_HIT_SECRET", "s3cret")
    client.get("/api/v1/taxonomy", headers={"Authorization": "Bearer s3cret"})
    assert hits(tmp_path / "platform") == []
    client.get("/api/v1/taxonomy", headers={"Authorization": "Bearer wrong"})
    client.get("/api/v1/taxonomy")
    assert len(hits(tmp_path / "platform")) == 2


def test_no_secret_configured_means_every_read_is_counted(client, tmp_path, monkeypatch):
    monkeypatch.delenv("INTERNAL_HIT_SECRET", raising=False)
    client.get("/api/v1/taxonomy", headers={"Authorization": "Bearer "})
    assert len(hits(tmp_path / "platform")) == 1


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,x", "//no-scheme.example",
                                 "ftp://funder.example/x", "https://", "http://a b.example/", "", None, 7])
def test_only_http_urls_leave_the_backend(bad):
    from api.public_data import public_record
    row = {**NOW_ROW, "status": "live", "source_url": bad,
           "provenance": {"deadline": {"source_url": bad, "verified_at": "x", "excerpt": "y"}}}
    record = public_record(row, base="https://gobuga.org", retrieved_at="2026-10-01")
    assert record["source_url"] is None
    assert record["deadline"]["source_url"] is None
    assert record["provenance"]["deadline"]["source_url"] is None
    assert record["provenance"]["deadline"]["excerpt"] == "y"


def test_entity_vocabulary_follows_the_country(monkeypatch):
    from api.fit import ENTITY_VOCAB, FitParamError, entity_vocab_for, parse_fit_params
    assert entity_vocab_for("nz") == ENTITY_VOCAB
    md = entity_vocab_for("md")
    assert "marae" not in md and "iwi-hapu" not in md and "club" in md
    assert parse_fit_params({"status": "marae"}, "nz").status == "marae"
    with pytest.raises(FitParamError) as exc:
        parse_fit_params({"status": "marae"}, "md")
    assert "marae" not in exc.value.allowed


def test_taxonomy_lists_the_countrys_entities(client, monkeypatch):
    assert "marae" in client.get("/api/v1/taxonomy").json()["entity_vocab"]
    monkeypatch.setenv("GOBUGA_COUNTRY", "md")
    clear_config_cache()
    assert "marae" not in client.get("/api/v1/taxonomy").json()["entity_vocab"]
