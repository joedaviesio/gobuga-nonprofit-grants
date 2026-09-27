"""Per-client rate limiting on the public machine surface (api/rate_limit.py).

Unit tests for the limiter and the forwarded-for logic, then in-process
requests through the app with a small limit.
"""

import socket

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from api import server  # noqa: E402 — imports load_dotenv; the fixture's delenv wins
from api import rate_limit, tenant
from api.rate_limit import RateLimiter, client_address

SECRET = "internal-secret-for-tests"


def _no_network(*args, **kwargs):
    raise RuntimeError("network access is blocked in this test")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "RESEND_API_KEY", "TAVILY_API_KEY", "INTERNAL_HIT_SECRET",
                "TRUSTED_PROXY_HOPS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    rate_limit.public_limiter.clear()
    yield
    rate_limit.public_limiter.clear()


def request_with(xff=None, peer="10.0.0.9", auth=None):
    headers = []
    for value in ([xff] if isinstance(xff, str) else xff or []):
        headers.append((b"x-forwarded-for", value.encode()))
    if auth:
        headers.append((b"authorization", auth.encode()))
    return Request({"type": "http", "method": "GET", "path": "/api/v1", "headers": headers,
                    "client": (peer, 1234), "query_string": b""})


# --- Client address ------------------------------------------------------------

def test_one_hop_takes_the_rightmost_entry():
    assert client_address(request_with("203.0.113.7"), hops=1) == "203.0.113.7"


def test_spoofed_left_entries_are_ignored():
    # The client sent "X-Forwarded-For: 1.1.1.1"; our edge appended the real address.
    req = request_with("1.1.1.1, 198.51.100.4, 203.0.113.7")
    assert client_address(req, hops=1) == "203.0.113.7"


def test_two_hops_skip_the_inner_proxy():
    # Edge appended the client, then Next.js appended the edge.
    req = request_with("6.6.6.6, 203.0.113.7, 10.1.1.1")
    assert client_address(req, hops=2) == "203.0.113.7"


def test_several_headers_are_one_list():
    req = request_with(["6.6.6.6", "203.0.113.7"])
    assert client_address(req, hops=1) == "203.0.113.7"


@pytest.mark.parametrize("xff,hops", [
    (None, 1),                        # no header: direct connection
    ("203.0.113.7", 2),               # fewer entries than trusted hops
    ("203.0.113.7", 0),               # forwarded-for not trusted at all
    ("not-an-ip", 1),                 # garbage in the trusted position
    (" , ", 1),
])
def test_falls_back_to_the_socket_peer(xff, hops):
    assert client_address(request_with(xff), hops=hops) == "10.0.0.9"


def test_ipv6_is_accepted():
    assert client_address(request_with("2001:db8::1"), hops=1) == "2001:db8::1"


@pytest.mark.parametrize("value,expected", [(None, 1), ("2", 2), ("0", 0), ("-1", 1), ("x", 1)])
def test_trusted_hops_env(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", value)
    assert rate_limit.trusted_proxy_hops() == expected


# --- Limiter ---------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_limiter_allows_up_to_the_limit_then_waits():
    clock = Clock()
    lim = RateLimiter(3, window=60, clock=clock)
    assert [lim.hit("a") for _ in range(3)] == [None, None, None]
    clock.t += 10
    assert lim.hit("a") == pytest.approx(50.0)
    assert lim.hit("b") is None


def test_limiter_window_slides():
    clock = Clock()
    lim = RateLimiter(2, window=60, clock=clock)
    lim.hit("a")
    clock.t += 30
    lim.hit("a")
    clock.t += 31  # the first hit has left the window
    assert lim.hit("a") is None
    assert lim.hit("a") is not None


def test_limiter_memory_is_bounded():
    lim = RateLimiter(1, window=60, max_clients=100, clock=Clock())
    for i in range(1000):
        lim.hit(f"client-{i}")
    assert len(lim) == 100
    # The most recent client is still tracked; the oldest was evicted.
    assert lim.hit("client-999") is not None
    assert lim.hit("client-0") is None


def test_is_internal_needs_the_exact_secret(monkeypatch):
    assert not rate_limit.is_internal(request_with(auth="Bearer "))  # secret unset
    monkeypatch.setenv("INTERNAL_HIT_SECRET", SECRET)
    assert rate_limit.is_internal(request_with(auth=f"Bearer {SECRET}"))
    for auth in (None, SECRET, f"Bearer {SECRET}x", "Bearer wrong", f"bearer {SECRET}"):
        assert not rate_limit.is_internal(request_with(auth=auth))


# --- Through the app -------------------------------------------------------------------

@pytest.fixture
def small_limit(monkeypatch):
    monkeypatch.setattr(rate_limit, "public_limiter", RateLimiter(3))
    return TestClient(server.app, raise_server_exceptions=False)


def test_over_the_limit_is_a_429_envelope_with_retry_after(small_limit):
    for _ in range(3):
        assert small_limit.get("/api/v1/taxonomy").status_code == 200
    r = small_limit.get("/api/v1/taxonomy")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(r.headers["retry-after"]) <= 60


def test_click_outs_are_limited_too(small_limit):
    for _ in range(3):
        small_limit.get("/out/OPP-NZ-2099-01-0001", follow_redirects=False)
    assert small_limit.get("/out/OPP-NZ-2099-01-0001", follow_redirects=False).status_code == 429


def test_clients_are_counted_separately(small_limit):
    for _ in range(3):
        small_limit.get("/api/v1/taxonomy", headers={"X-Forwarded-For": "203.0.113.1"})
    assert small_limit.get("/api/v1/taxonomy",
                           headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 429
    assert small_limit.get("/api/v1/taxonomy",
                           headers={"X-Forwarded-For": "203.0.113.2"}).status_code == 200


def test_rotating_a_spoofed_header_does_not_escape_the_limit(small_limit):
    for i in range(3):
        small_limit.get("/api/v1/taxonomy", headers={"X-Forwarded-For": f"9.9.9.{i}, 203.0.113.1"})
    r = small_limit.get("/api/v1/taxonomy", headers={"X-Forwarded-For": "9.9.9.200, 203.0.113.1"})
    assert r.status_code == 429


def test_internal_bearer_is_exempt(small_limit, monkeypatch):
    monkeypatch.setenv("INTERNAL_HIT_SECRET", SECRET)
    for _ in range(10):
        r = small_limit.get("/api/v1/taxonomy", headers={"Authorization": f"Bearer {SECRET}"})
        assert r.status_code == 200
    for _ in range(3):
        small_limit.get("/api/v1/taxonomy", headers={"Authorization": "Bearer wrong"})
    assert small_limit.get("/api/v1/taxonomy",
                           headers={"Authorization": "Bearer wrong"}).status_code == 429


def test_workspace_routes_are_not_limited_by_this_limiter(small_limit):
    for _ in range(5):
        assert small_limit.get("/api/health").status_code == 200
