"""The workspace reads the published dataset, the same rows the public site
lists, so a grant has one ID on both sides.

In-process (fastapi TestClient) against a throwaway data dir. Bot A, which
opening a case runs, is faked; nothing reaches the network.
"""

import json
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.bots as bots
import api.tenant as tenant
from api import published, server, sources
from api.country_config import clear_config_cache
from api.usage_log import log_usage

NOW = datetime.now(timezone.utc)


def _fake_bot_a(org_id, case_id, model=None):
    log_usage(org_id, "bot_a", {"api_calls": 1, "model": "fake"}, case_id=case_id)
    return {"summary": "fake", "usage": {}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setenv("STARTUP_SWEEP_DISABLED", "1")
    monkeypatch.delenv("DAILY_LLM_CALL_BUDGET", raising=False)
    monkeypatch.setattr(sources, "must_appear_names", lambda country: [])
    monkeypatch.setattr(bots, "bot_a_generate_summary", _fake_bot_a)
    monkeypatch.setattr(server, "bot_a_generate_summary", _fake_bot_a)
    clear_config_cache()
    yield TestClient(server.app)
    clear_config_cache()


def row(n, **kw):
    base = {
        "id": f"OPP-NZ-2026-09-{n:04d}",
        "country": "nz",
        "title": f"Programme {n}",
        "funder": f"Funder {n}",
        "deadline": "2099-11-30",
        "deadline_state": "dated",
        "amount_min": None,
        "amount_max": 20000,
        "currency": "NZD",
        "region": ["canterbury"],
        "tags": ["community"],
        "eligibility": "Incorporated societies",
        "summary": "A fund.",
        "source_url": f"https://funder{n}.example.nz/grants",
        "evidence_ids": [f"EV-{n:04d}"],
        "dedupe_key": f"funder-{n}|funder{n}.example.nz|{n}",
        "first_seen": "2026-09-01T00:00:00+00:00",
        "last_seen": "2026-09-24T00:00:00+00:00",
        "verified_at": "2026-09-24T00:00:00+00:00",
        "verified_by": "verify-pass/test",
        "source_excerpt": "Applications close on 30 November.",
    }
    base.update(kw)
    return base


@pytest.fixture
def dataset(client):
    """Two live rows and one whose deadline has passed."""
    published.publish_pool("nz", "2026-09", [row(1), row(2), row(3, deadline="2020-01-31")],
                           now=NOW, notifier=lambda *a: None)


def _register(client):
    r = client.post("/api/auth/register", json={
        "email": "ws@gobuga-test.org", "password": "TestPass123!", "org_name": "Test Org"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _legacy_pool(month, rows):
    latest = tenant.platform_latest_path("nz", month)
    os.makedirs(latest, exist_ok=True)
    with open(os.path.join(latest, "opportunities.json"), "w") as f:
        json.dump({"opportunities": rows}, f)


def test_workspace_lists_the_live_published_rows(client, dataset):
    headers = _register(client)
    body = client.get("/api/opportunities", headers=headers).json()
    assert sorted(r["id"] for r in body["opportunities"]) == ["OPP-NZ-2026-09-0001", "OPP-NZ-2026-09-0002"]
    assert body["total"] == 2


def test_workspace_and_public_list_share_ids(client, dataset):
    headers = _register(client)
    ws = {r["id"] for r in client.get("/api/opportunities", headers=headers).json()["opportunities"]}
    public = {r["id"] for r in client.get("/api/v1/opportunities").json()["data"]}
    assert ws == public


def test_workspace_ignores_the_old_monthly_pool(client, dataset):
    _legacy_pool("2026-04", [row(99, id="OPP-NZ-2026-04-0099")])
    headers = _register(client)
    ids = [r["id"] for r in client.get("/api/opportunities", headers=headers).json()["opportunities"]]
    assert "OPP-NZ-2026-04-0099" not in ids


def test_workspace_list_is_empty_before_anything_is_published(client):
    headers = _register(client)
    body = client.get("/api/opportunities", headers=headers).json()
    assert body["opportunities"] == [] and body["total"] == 0


def test_filters_still_apply(client, dataset):
    headers = _register(client)
    body = client.get("/api/opportunities", headers=headers, params={"q": "Programme 2"}).json()
    assert [r["id"] for r in body["opportunities"]] == ["OPP-NZ-2026-09-0002"]


def test_open_case_by_the_public_id(client, dataset):
    headers = _register(client)
    r = client.post("/api/opportunities/open-case", headers=headers,
                    json={"opportunity_id": "OPP-NZ-2026-09-0001"})
    assert r.status_code == 200, r.text
    brief = r.json()["grant_brief"]
    assert brief["opportunity_id"] == "OPP-NZ-2026-09-0001"
    assert brief["title"] == "Programme 1"
    assert brief["source_cycle"] == "nz/2026-09"


def test_open_case_for_an_unknown_id_is_404(client, dataset):
    headers = _register(client)
    r = client.post("/api/opportunities/open-case", headers=headers,
                    json={"opportunity_id": "OPP-NZ-2026-09-0404"})
    assert r.status_code == 404


def test_open_case_for_a_closed_grant_is_409(client, dataset):
    headers = _register(client)
    r = client.post("/api/opportunities/open-case", headers=headers,
                    json={"opportunity_id": "OPP-NZ-2026-09-0003"})
    assert r.status_code == 409
    assert "no longer open" in r.json()["detail"]


def test_open_case_ignores_a_row_only_in_the_old_pool(client, dataset):
    _legacy_pool("2026-09", [row(99, id="OPP-NZ-2026-04-0099")])
    headers = _register(client)
    r = client.post("/api/opportunities/open-case", headers=headers,
                    json={"opportunity_id": "OPP-NZ-2026-04-0099"})
    assert r.status_code == 404


# --- Best fit (the default sort) -------------------------------------------------

OPEN = "Charitable trusts and incorporated societies."


@pytest.fixture
def ranked(client):
    """A charitable trust in Canterbury working on sport, and five rows."""
    published.publish_pool("nz", "2026-09", [
        row(1, tags=["arts"], eligibility=OPEN), row(2, tags=["sport"], eligibility=OPEN),
        row(3, region=["otago"], eligibility=OPEN), row(4, deadline="2020-01-31", eligibility=OPEN),
        row(5, tags=["sport"]),  # incorporated societies only
    ], now=NOW, notifier=lambda *a: None)
    headers = _register(client)
    r = client.post("/api/org/setup", headers=headers, json={
        "sectors": ["Sport & recreation"], "geographies": ["New Zealand > Canterbury"],
        "fit_status": "charitable-trust"})
    assert r.status_code == 200, r.text
    return headers


def test_the_list_is_ranked_for_the_org_by_default(client, ranked):
    body = client.get("/api/opportunities", headers=ranked).json()
    assert body["fit_params"] == {"sector": ["sport"], "region": ["canterbury"], "status": "charitable-trust"}
    rows = body["opportunities"]
    ids = [r["id"] for r in rows]
    assert ids[0] == "OPP-NZ-2026-09-0002"           # sector match first
    assert rows[0]["why"] and rows[0]["fit_score"] > 0
    assert "OPP-NZ-2026-09-0004" not in ids          # closed: not live at all


def test_grants_that_do_not_fit_come_last_without_reasons(client, ranked):
    rows = client.get("/api/opportunities", headers=ranked).json()["opportunities"]
    ids = [r["id"] for r in rows]
    assert ids[-2:] == ["OPP-NZ-2026-09-0003", "OPP-NZ-2026-09-0005"]  # Otago only; societies only
    assert all(r["why"] == [] and r["fit_score"] is None for r in rows[-2:])
    assert len(ids) == 4


def test_best_fit_applies_after_the_filters(client, ranked):
    body = client.get("/api/opportunities", headers=ranked, params={"tags": "sport"}).json()
    assert [r["id"] for r in body["opportunities"]] == ["OPP-NZ-2026-09-0002", "OPP-NZ-2026-09-0005"]


def test_best_fit_paginates(client, ranked):
    body = client.get("/api/opportunities", headers=ranked, params={"limit": 1, "cursor": 1}).json()
    assert body["total"] == 4 and body["has_more"] is True and len(body["opportunities"]) == 1


def test_other_sorts_still_work(client, ranked):
    body = client.get("/api/opportunities", headers=ranked, params={"sort": "deadline"}).json()
    assert body["total"] == 4


def test_an_empty_profile_has_no_fit_params(client, dataset):
    headers = _register(client)
    body = client.get("/api/opportunities", headers=headers).json()
    assert body["fit_params"] == {}
    assert body["total"] == 2


def test_setup_rejects_a_fit_value_outside_the_vocabulary(client):
    headers = _register(client)
    r = client.post("/api/org/setup", headers=headers, json={"fit_size": "enormous"})
    assert r.status_code == 400
    assert "size" in r.json()["detail"]


def test_profile_edit_saves_and_clears_fit_fields(client):
    headers = _register(client)
    r = client.patch("/api/org/profile", headers=headers, json={"fit_need": "equipment", "fit_size": "under-50k"})
    assert r.status_code == 200, r.text
    assert r.json()["fit_need"] == "equipment"
    r = client.patch("/api/org/profile", headers=headers, json={"fit_need": ""})
    assert r.json()["fit_need"] == "" and r.json()["fit_size"] == "under-50k"
