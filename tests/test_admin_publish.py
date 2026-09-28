"""POST /api/admin/publish: rows swept elsewhere, published through the same gate."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.startup_sweep as startup_sweep
import api.tenant as tenant
from api import published, server, sources
from api.country_config import clear_config_cache

NOW = datetime.now(timezone.utc).isoformat()
ROW = {"id": "OPP-NZ-2026-09-0001", "country": "nz", "title": "Community Grants",
       "funder": "Example Trust", "deadline": "TBC", "deadline_state": "not-stated",
       "verified_at": NOW, "first_seen": NOW, "last_seen": NOW, "currency": "NZD",
       "amount_min": None, "amount_max": None, "region": ["national"], "tags": ["community"],
       "source_url": "https://trust.example/grants", "dedupe_key": "example-trust|community-grants|tbc"}
AUTH = {"Authorization": "Bearer sw33p"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setattr(startup_sweep, "SWEEP_SECRET", "sw33p")
    monkeypatch.setattr(sources, "must_appear_names", lambda country: ["Example Trust"])
    clear_config_cache()
    yield TestClient(server.app, raise_server_exceptions=False)
    clear_config_cache()


def post(client, headers=AUTH, **body):
    return client.post("/api/admin/publish", headers=headers,
                       json={"month": "2026-09", "rows": [ROW], **body})


def test_needs_the_sweep_secret(client):
    assert post(client, headers={}).status_code == 401
    assert post(client, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert published.load_published("nz") == []


def test_dry_run_reports_and_writes_nothing(client):
    r = post(client, dry_run=True)
    assert r.status_code == 200
    assert r.json()["dry_run"] is True and r.json()["counts"]["publishable"] == 1
    assert published.load_published("nz") == []


def test_publishes_through_the_gate(client):
    unverified = {**ROW, "id": "OPP-NZ-2026-09-0002", "title": "Unchecked", "verified_at": None,
                  "dedupe_key": "example-trust|unchecked|tbc"}
    r = client.post("/api/admin/publish", headers=AUTH,
                    json={"month": "2026-09", "rows": [ROW, unverified]})
    assert r.status_code == 200 and r.json()["published"] is True
    assert r.json()["counts"]["held"] == 1
    assert [x["title"] for x in published.load_published("nz")] == ["Community Grants"]
    assert client.get("/api/v1/opportunities").json()["total"] == 1


def test_a_missing_tier_one_funder_blocks_unless_forced(client, monkeypatch):
    monkeypatch.setattr(sources, "must_appear_names", lambda country: ["Example Trust", "Lottery"])
    r = post(client)
    assert r.status_code == 409 and r.json()["missing_tier_one"] == ["Lottery"]
    assert published.load_published("nz") == []
    r = post(client, force=True, forced_by="Joe")
    assert r.status_code == 200 and r.json()["forced"] is True
    assert len(published.load_published("nz")) == 1


@pytest.mark.parametrize("body", [
    {"month": "2026-13"}, {"month": None}, {"rows": "lots"}, {"rows": [1, 2]},
    {"rows": [{**ROW, "country": "md"}]},
])
def test_bad_bodies_are_400(client, body):
    assert post(client, **body).status_code == 400
    assert published.load_published("nz") == []
