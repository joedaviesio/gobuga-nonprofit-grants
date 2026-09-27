"""Error envelope and hidden internal OpenAPI.

In-process, no server. Unhandled exceptions return a fixed envelope with no
exception text; public prefixes get {"error": {code, message}} for 4xx;
workspace routes keep their {"detail": ...} bodies; /docs, /redoc and
/openapi.json are gone.
"""

import pytest
from fastapi.testclient import TestClient

import api.tenant as tenant
from api import errors, server

SECRET_TEXT = "db password is hunter2"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GOBUGA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STARTUP_SWEEP_DISABLED", "1")
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))

    # Throwaway routes, removed again so no other test sees them.
    before = list(server.app.router.routes)

    @server.app.get("/api/_test/boom")
    def _boom():
        raise RuntimeError(SECRET_TEXT)

    @server.app.get("/api/v1/_test/public-error")
    def _public_error():
        raise errors.PublicError(404, "not_found", "No grant with that ID")

    @server.app.get("/api/v1/_test/needs-int")
    def _needs_int(n: int):
        return {"n": n}

    try:
        yield TestClient(server.app, raise_server_exceptions=False)
    finally:
        server.app.router.routes[:] = before


# --- 500 envelope ---

def test_unhandled_exception_returns_envelope_without_leak(client, capsys):
    r = client.get("/api/_test/boom")
    assert r.status_code == 500
    assert r.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"},
        "detail": "Internal server error",
    }
    assert SECRET_TEXT not in r.text
    assert "Traceback" not in r.text
    # ...but the log still has it.
    assert SECRET_TEXT in capsys.readouterr().out


def test_500_carries_cors_header_for_allowed_origin(client):
    r = client.get("/api/_test/boom", headers={"Origin": "http://localhost:3002"})
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3002"


def test_500_has_no_cors_header_for_other_origin(client):
    r = client.get("/api/_test/boom", headers={"Origin": "https://evil.example"})
    assert r.status_code == 500
    assert "access-control-allow-origin" not in r.headers


# --- Internal OpenAPI hidden ---

@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"])
def test_internal_docs_are_gone(client, path):
    assert client.get(path).status_code == 404


# --- Public 4xx envelopes ---

def test_public_error_envelope(client):
    r = client.get("/api/v1/_test/public-error")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "No grant with that ID"}}


@pytest.mark.parametrize("path", ["/api/v1/nope", "/out", "/mcp/x", "/api/internal/nope",
                                  "/api/subscribe/nope"])
def test_unknown_public_path_gets_envelope(client, path):
    r = client.get(path)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_public_validation_error_gets_envelope_without_echo(client):
    r = client.get("/api/v1/_test/needs-int", params={"n": "<script>"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "invalid_request"
    assert "<script>" not in r.text


# --- Workspace routes unchanged ---

def test_workspace_unknown_path_keeps_detail(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_workspace_4xx_keeps_detail(client):
    r = client.post("/api/auth/verify")
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid or expired session"}


def test_workspace_validation_error_keeps_fastapi_shape(client):
    r = client.post("/api/auth/login", json={})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)


@pytest.mark.parametrize("path, public", [
    ("/api/v1", True), ("/api/v1/opportunities", True), ("/out/OPP-1", True),
    ("/mcp", True), ("/api/subscribe", True), ("/api/internal/hit", True),
    ("/api/v10", False), ("/outreach", False), ("/mcpx", False),
    ("/api/cases", False), ("/", False),
])
def test_is_public_path(path, public):
    assert errors.is_public_path(path) is public
