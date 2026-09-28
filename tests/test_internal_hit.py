"""POST /api/internal/hit: secret handling, size caps, and what it records.

In-process, no server. The secret is set per test with monkeypatch, which
works because the route reads INTERNAL_HIT_SECRET on every request.
"""

import json

import pytest
from fastapi.testclient import TestClient

import api.tenant as tenant
from api import internal_hit, server

SECRET = "s3cret-for-tests"
URL = "/api/internal/hit"
GOOD = {"path": "/grants/OPP-NZ-1", "user_agent": "ClaudeBot/1.0", "referrer": None}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GOBUGA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setenv("STARTUP_SWEEP_DISABLED", "1")
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    return tmp_path


@pytest.fixture
def client(data_dir, monkeypatch):
    monkeypatch.setenv("INTERNAL_HIT_SECRET", SECRET)
    return TestClient(server.app, raise_server_exceptions=False)


def _auth(secret=SECRET):
    return {"Authorization": f"Bearer {secret}"}


def _stored(data_dir):
    files = list((data_dir / "platform" / "metrics" / "nz").glob("*.jsonl"))
    return [json.loads(l) for f in files for l in f.read_text().splitlines() if l.strip()]


# --- secret ---

@pytest.mark.parametrize("value", [None, ""])
def test_404_when_secret_unset(data_dir, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("INTERNAL_HIT_SECRET", raising=False)
    else:
        monkeypatch.setenv("INTERNAL_HIT_SECRET", value)
    c = TestClient(server.app)
    r = c.post(URL, json=GOOD, headers=_auth(""))
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "Not found"}}
    assert _stored(data_dir) == []


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong"},
    {"Authorization": f"Bearer {SECRET}x"},
    {"Authorization": SECRET},  # no Bearer prefix
    {"Authorization": "Bearer "},
])
def test_401_on_bad_secret(client, data_dir, headers):
    r = client.post(URL, json=GOOD, headers=headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert _stored(data_dir) == []


def test_uses_constant_time_compare(client, monkeypatch):
    calls = []
    real = internal_hit.hmac.compare_digest
    monkeypatch.setattr(internal_hit.hmac, "compare_digest",
                        lambda a, b: calls.append((a, b)) or real(a, b))
    client.post(URL, json=GOOD, headers=_auth())
    assert calls == [(SECRET.encode(), SECRET.encode())]


# --- recording ---

def test_records_page_hit(client, data_dir):
    r = client.post(URL, json=GOOD, headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    [line] = _stored(data_dir)
    assert line["surface"] == "page"
    assert line["path"] == "/grants/OPP-NZ-1"
    assert line["agent"] == "claudebot"
    assert line["ua"] == "ClaudeBot/1.0"


def test_human_hit_stores_no_user_agent(client, data_dir):
    body = {"path": "/grants", "user_agent": "Mozilla/5.0 (Macintosh) Safari/605",
            "referrer": "https://www.perplexity.ai/search/abc"}
    assert client.post(URL, json=body, headers=_auth()).status_code == 200
    [line] = _stored(data_dir)
    assert "ua" not in line
    assert (line["ref"], line["ref_host"]) == ("assistant", "www.perplexity.ai")
    assert "Safari" not in json.dumps(line)


def test_optional_fields_default(client, data_dir):
    assert client.post(URL, json={"path": "/"}, headers=_auth()).status_code == 200
    [line] = _stored(data_dir)
    assert line["agent"] == "other-bot"  # empty UA
    assert line["ref"] == "direct"


# --- validation and caps ---

@pytest.mark.parametrize("field, cap", [
    ("path", internal_hit.MAX_PATH_CHARS),
    ("user_agent", internal_hit.MAX_UA_CHARS),
    ("referrer", internal_hit.MAX_REFERRER_CHARS),
])
def test_field_caps(client, data_dir, field, cap):
    at_cap = {**GOOD, field: "/" + "a" * (cap - 1)}
    assert client.post(URL, json=at_cap, headers=_auth()).status_code == 200
    over = {**GOOD, field: "/" + "a" * cap}
    r = client.post(URL, json=over, headers=_auth())
    assert r.status_code == 400
    assert r.json() == {"error": {"code": "field_too_long",
                                  "message": f"'{field}' exceeds {cap} characters"}}
    assert len(_stored(data_dir)) == 1


def test_body_cap(client, data_dir):
    r = client.post(URL, content=b"x" * (internal_hit.MAX_BODY_BYTES + 1),
                    headers={**_auth(), "Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "body_too_large"


def test_body_cap_without_content_length(client, data_dir):
    def chunks():
        for _ in range(10):
            yield b"x" * 1024
    r = client.post(URL, content=chunks(), headers=_auth())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "body_too_large"


@pytest.mark.parametrize("content", [
    b"not json",
    b"[]",
    b"{}",
    json.dumps({"path": 5}).encode(),
    json.dumps({"path": "/x", "user_agent": ["a"]}).encode(),
    b"\xff\xfe",
])
def test_invalid_body(client, data_dir, content):
    r = client.post(URL, content=content, headers=_auth())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"
    assert _stored(data_dir) == []


def test_path_must_be_absolute(client, data_dir):
    r = client.post(URL, json={"path": "https://evil.example/"}, headers=_auth())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_path"


def test_get_is_not_allowed(client):
    r = client.get(URL, headers=_auth())
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "method_not_allowed"


# --- GET /api/internal/forwarded (temporary diagnostic) -----------------------

FORWARDED = "/api/internal/forwarded"


def test_forwarded_shows_the_callers_own_chain(client, data_dir):
    r = client.get(FORWARDED, headers={**_auth(), "X-Forwarded-For": "203.0.113.7, 100.64.0.9",
                                        "X-Real-IP": "203.0.113.7"})
    assert r.status_code == 200
    body = r.json()
    assert body["forwarded_for"] == ["203.0.113.7", "100.64.0.9"]
    assert body["headers"]["x-real-ip"] == ["203.0.113.7"]
    assert body["trusted_proxy_hops"] == 1
    assert body["limiter_key"] == "100.64.0.9"
    assert r.headers["cache-control"] == "no-store"
    # Nothing is stored.
    assert not (data_dir / "platform" / "metrics").exists()


def test_forwarded_follows_the_hop_setting(client, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    body = client.get(FORWARDED, headers={**_auth(), "X-Forwarded-For": "203.0.113.7, 100.64.0.9"}).json()
    assert body["trusted_proxy_hops"] == 2 and body["limiter_key"] == "203.0.113.7"


def test_forwarded_needs_the_secret(client):
    assert client.get(FORWARDED).status_code == 401
    assert client.get(FORWARDED, headers=_auth("wrong")).status_code == 401
    assert "forwarded_for" not in client.get(FORWARDED).text


def test_forwarded_is_404_when_no_secret_is_configured(client, monkeypatch):
    monkeypatch.delenv("INTERNAL_HIT_SECRET")
    assert client.get(FORWARDED, headers=_auth()).status_code == 404
