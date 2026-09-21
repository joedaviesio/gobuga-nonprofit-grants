"""POST /api/org/toggle-tier must be a 404 unless GOBUGA_DEV_TIER_TOGGLE=1.

The route grants the paid Officer tier without Stripe. It exists only so the
test suite can put its throwaway org on Officer (tests/test_exports.py), and
must never be reachable on a production deployment. In-process, no server:
the session dependency is overridden so we exercise the gate itself, not auth.
"""

import os

import pytest
from fastapi.testclient import TestClient

from api import server


@pytest.fixture
def client():
    server.app.dependency_overrides[server.get_current_org] = lambda: "org_test_gate"
    try:
        yield TestClient(server.app)
    finally:
        server.app.dependency_overrides.pop(server.get_current_org, None)


def test_toggle_tier_is_404_when_flag_unset(client, monkeypatch):
    monkeypatch.delenv("GOBUGA_DEV_TIER_TOGGLE", raising=False)
    r = client.post("/api/org/toggle-tier")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not found"}


@pytest.mark.parametrize("value", ["0", "", "true", "yes"])
def test_toggle_tier_is_404_unless_flag_is_exactly_1(client, monkeypatch, value):
    monkeypatch.setenv("GOBUGA_DEV_TIER_TOGGLE", value)
    assert client.post("/api/org/toggle-tier").status_code == 404


def test_toggle_tier_passes_gate_when_flag_set(client, monkeypatch):
    """With the flag on, the gate lets the request through to toggle_tier."""
    monkeypatch.setenv("GOBUGA_DEV_TIER_TOGGLE", "1")
    calls = []
    import api.limits as limits
    monkeypatch.setattr(limits, "toggle_tier", lambda org_id: calls.append(org_id) or "officer")
    monkeypatch.setattr(limits, "get_tier", lambda org_id: {"label": "Grant Officer"})
    monkeypatch.setattr(limits, "get_cycle_timer", lambda org_id: None)
    r = client.post("/api/org/toggle-tier")
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "officer"
    assert calls == ["org_test_gate"]
