"""The page fetcher refuses anything that is not the public internet.

Pure-Python: DNS and HTTP are faked, nothing leaves the machine.
"""

import socket

import httpx
import pytest

from orchestrator import tools


def resolver(*addresses):
    def resolve(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]
    return resolve


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://funder.example/grants",
    "gopher://funder.example/",
    "https:///no-host",
    "javascript:alert(1)",
])
def test_non_http_urls_are_refused(url):
    with pytest.raises(tools.UnsafeUrlError):
        tools.assert_public_url(url, resolve=resolver("93.184.216.34"))


@pytest.mark.parametrize("address", [
    "127.0.0.1",          # loopback
    "10.0.0.5",           # private
    "172.16.4.4",
    "192.168.1.1",
    "169.254.169.254",    # cloud metadata
    "0.0.0.0",
    "100.64.0.1",         # carrier-grade NAT, used by some hosts internally
    "::1",
    "fd00::1",            # unique local
    "fe80::1",            # link local
    "::ffff:127.0.0.1",   # loopback, IPv4-mapped
])
def test_non_public_addresses_are_refused(address):
    with pytest.raises(tools.UnsafeUrlError):
        tools.assert_public_url("https://funder.example/grants", resolve=resolver(address))


def test_one_private_address_among_public_ones_is_refused():
    with pytest.raises(tools.UnsafeUrlError):
        tools.assert_public_url("https://funder.example/",
                                resolve=resolver("93.184.216.34", "10.0.0.5"))


def test_unresolvable_host_is_refused():
    def resolve(host, port, type=None):
        raise socket.gaierror("no such host")
    with pytest.raises(tools.UnsafeUrlError):
        tools.assert_public_url("https://nowhere.example/", resolve=resolve)


def test_public_address_is_allowed():
    tools.assert_public_url("https://funder.example/grants", resolve=resolver("93.184.216.34"))


class FakeClient:
    """Stands in for httpx.Client; serves canned responses by URL."""

    def __init__(self, responses, seen):
        self.responses, self.seen = responses, seen

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):
        self.seen.append(url)
        status, headers, body = self.responses[url]
        return httpx.Response(status, headers=headers, text=body,
                              request=httpx.Request("GET", url))


def install(monkeypatch, responses, hosts):
    seen = []
    monkeypatch.setattr(tools.httpx, "Client", lambda **kw: FakeClient(responses, seen))

    def resolve(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (hosts[host], port))]
    monkeypatch.setattr(tools.socket, "getaddrinfo", resolve)
    return seen


def test_fetch_returns_page_text(monkeypatch):
    install(monkeypatch,
            {"https://funder.example/grants": (200, {}, "<p>Closes 30 November</p>")},
            {"funder.example": "93.184.216.34"})
    assert tools.handle_web_fetch({"url": "https://funder.example/grants"}) == "Closes 30 November"


def test_fetch_follows_a_public_redirect(monkeypatch):
    seen = install(monkeypatch, {
        "https://funder.example/old": (302, {"location": "/new"}, ""),
        "https://funder.example/new": (200, {}, "<p>Moved here</p>"),
    }, {"funder.example": "93.184.216.34"})
    assert tools.handle_web_fetch({"url": "https://funder.example/old"}) == "Moved here"
    assert seen == ["https://funder.example/old", "https://funder.example/new"]


def test_fetch_refuses_a_redirect_to_a_private_address(monkeypatch):
    seen = install(monkeypatch, {
        "https://funder.example/": (302, {"location": "http://internal.example/admin"}, ""),
        "http://internal.example/admin": (200, {}, "secret"),
    }, {"funder.example": "93.184.216.34", "internal.example": "169.254.169.254"})
    out = tools.handle_web_fetch({"url": "https://funder.example/"})
    assert out.startswith("Error fetching")
    assert "secret" not in out
    assert seen == ["https://funder.example/"]


def test_fetch_refuses_a_private_address_without_any_request(monkeypatch):
    seen = install(monkeypatch, {}, {"localhost": "127.0.0.1"})
    assert tools.handle_web_fetch({"url": "http://localhost:8102/api/health"}).startswith("Error fetching")
    assert seen == []


def test_fetch_gives_up_on_a_redirect_loop(monkeypatch):
    seen = install(monkeypatch,
                   {"https://funder.example/a": (302, {"location": "/a"}, "")},
                   {"funder.example": "93.184.216.34"})
    assert tools.handle_web_fetch({"url": "https://funder.example/a"}).startswith("Error fetching")
    assert len(seen) == tools.MAX_FETCH_REDIRECTS + 1
