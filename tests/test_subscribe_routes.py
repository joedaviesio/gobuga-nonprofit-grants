"""Routes for the subscribe list (api/subscribe_routes.py).

In-process through TestClient, no server, no network. RESEND_API_KEY is
cleared, `resend.Emails.send` is stubbed, and `send_list_email` is replaced
by a recorder so the confirmation emails can be inspected.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from starlette.requests import Request

# api.email calls load_dotenv() on import; import it before the fixtures clear
# RESEND_API_KEY so a developer's .env cannot put a real key back.
import api.email as email_mod
import resend
from fastapi.testclient import TestClient

from api import server, subscribe_routes, subscribers, tenant

APP = "https://gobuga.test"
URL = "/api/subscribe"
CONFIRM = "/api/subscribe/confirm"
UNSUB = "/api/subscribe/unsubscribe"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    monkeypatch.setenv("APP_URL", APP)
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setenv("STARTUP_SWEEP_DISABLED", "1")
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setattr(resend.Emails, "send", staticmethod(lambda params: pytest.fail("resend called")))
    for limiter in (subscribe_routes.client_subscribe_limiter,
                    subscribe_routes.target_subscribe_limiter,
                    subscribe_routes.client_token_limiter):
        limiter.reset()
    yield tmp_path
    for limiter in (subscribe_routes.client_subscribe_limiter,
                    subscribe_routes.target_subscribe_limiter,
                    subscribe_routes.client_token_limiter):
        limiter.reset()


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(email_mod, "send_list_email", lambda to, msg: sent.append((to, msg)))
    return sent


@pytest.fixture
def client():
    return TestClient(server.app, raise_server_exceptions=False, follow_redirects=False)


def _file(country="nz"):
    try:
        with open(subscribers.subscribers_path(country)) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _token(url_or_text, kind):
    return re.search(rf"{kind}\?token=([A-Za-z0-9_-]+)", url_or_text).group(1)


def _subscribe_and_get_tokens(client, mails, email="a@example.org", lang="en"):
    client.post(URL, json={"email": email, "lang": lang})
    to, msg = mails[-1]
    assert to == email
    return _token(msg["text"], "confirm"), _token(msg["headers"]["List-Unsubscribe"], "unsubscribe")


def _assert_private_headers(r):
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-robots-tag"] == "noindex"


# --- POST /api/subscribe ------------------------------------------------------------

def test_json_subscribe_sends_a_confirmation(client, mails):
    r = client.post(URL, json={"email": " A@Example.org ", "lang": "en"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    _assert_private_headers(r)
    ((to, msg),) = mails
    assert to == "a@example.org"
    assert msg["subject"] == "Confirm your GoBuga email subscription"
    assert f"{APP}/api/subscribe/confirm?token=" in msg["text"]
    assert msg["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_form_subscribe_redirects_to_the_sent_page(client, mails):
    r = client.post(URL, data={"email": "a@example.org", "lang": "en"})
    assert r.status_code == 303
    assert r.headers["location"] == f"{APP}/subscribe?state=sent"
    _assert_private_headers(r)
    assert len(mails) == 1


def _prepare(client, mails, state):
    email = f"{state}@example.org"
    if state == "new":
        return email
    confirm_token, unsub_token = _subscribe_and_get_tokens(client, mails, email)
    if state in ("confirmed", "unsubscribed"):
        client.post(CONFIRM, data={"token": confirm_token})
    if state == "unsubscribed":
        client.post(UNSUB, data={"token": unsub_token})
    return email


@pytest.mark.parametrize("as_form", [False, True])
def test_response_is_identical_whatever_the_address_state(client, mails, as_form):
    responses = []
    for state in ("new", "pending", "confirmed", "unsubscribed"):
        email = _prepare(client, mails, state)
        # A fresh client address for each, so no rate limit is involved.
        headers = {"X-Forwarded-For": f"10.0.0.{len(responses) + 1}"}
        if as_form:
            r = client.post(URL, data={"email": email, "lang": "en"}, headers=headers)
        else:
            r = client.post(URL, json={"email": email, "lang": "en"}, headers=headers)
        responses.append((r.status_code, r.content, r.headers.get("location"),
                          r.headers.get("content-type"), r.headers.get("cache-control")))
    assert len(set(responses)) == 1, responses


def test_confirmed_address_gets_no_email(client, mails):
    _prepare(client, mails, "confirmed")
    count = len(mails)
    client.post(URL, json={"email": "confirmed@example.org"})
    assert len(mails) == count


@pytest.mark.parametrize("email", ["not-an-address", "a@b.co\r\nBcc: x@y.co", "a@b.co\n", "", None, 7])
def test_invalid_address_json_gets_a_400_envelope(client, mails, email):
    r = client.post(URL, json={"email": email})
    assert r.status_code == 400
    assert r.json() == {"error": {"code": "invalid_email", "message": "Enter a valid email address"}}
    assert mails == [] and _file() == ""


@pytest.mark.parametrize("body", [
    "email=not-an-address", "email=a%40b.co%0D%0ABcc%3A+x%40y.co", "email=a%40b.co%0A", "lang=en", "",
])
def test_invalid_address_form_redirects_to_invalid(client, mails, body):
    r = client.post(URL, content=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 303
    assert r.headers["location"] == f"{APP}/subscribe?state=invalid"
    assert mails == []


def test_malformed_json_is_an_invalid_address(client, mails):
    r = client.post(URL, content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_email"


def test_other_content_types_are_refused(client, mails):
    r = client.post(URL, content=b"email=a@b.co", headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"


def test_oversized_body_is_refused(client, mails):
    r = client.post(URL, json={"email": "a@b.co", "pad": "x" * 5000})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


def test_hostile_lang_is_stored_as_the_content_language(client, mails, monkeypatch):
    monkeypatch.setenv("GOBUGA_COUNTRY", "md")
    client.post(URL, json={"email": "a@example.org", "lang": '"><script>alert(1)</script>'})
    record = json.loads(_file("md"))["subscribers"]["a@example.org"]
    assert record["lang"] == "ro"
    assert mails[0][1]["subject"] == "Confirmați abonarea la e-mailurile GoBuga"


def test_stored_file_has_no_ip_or_user_agent(client, mails):
    client.post(URL, json={"email": "a@example.org"},
                headers={"X-Forwarded-For": "203.0.113.77", "User-Agent": "Mozilla/5.0 SpecialAgent"})
    raw = _file()
    assert "203.0.113.77" not in raw and "SpecialAgent" not in raw and "testclient" not in raw


def test_resend_failure_does_not_change_the_response(client, monkeypatch):
    def boom(to, msg):
        raise RuntimeError("resend down")
    monkeypatch.setattr(email_mod, "send_list_email", boom)
    r = client.post(URL, json={"email": "a@example.org"})
    assert r.status_code == 200 and r.json() == {"ok": True}


# --- Rate limits ------------------------------------------------------------------

def test_per_client_rate_limit(client, mails):
    limit = subscribe_routes.CLIENT_SUBSCRIBE_LIMIT[0]
    headers = {"X-Forwarded-For": "198.51.100.1"}
    for i in range(limit):
        assert client.post(URL, json={"email": f"u{i}@example.org"}, headers=headers).status_code == 200
    r = client.post(URL, json={"email": "late@example.org"}, headers=headers)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    form = client.post(URL, data={"email": "late@example.org"}, headers=headers)
    assert form.status_code == 303 and form.headers["location"] == f"{APP}/subscribe?state=limited"
    # Another client is unaffected.
    other = client.post(URL, json={"email": "late@example.org"}, headers={"X-Forwarded-For": "198.51.100.2"})
    assert other.status_code == 200


def test_per_address_rate_limit_across_clients(client, mails):
    limit = subscribe_routes.TARGET_SUBSCRIBE_LIMIT[0]
    for i in range(limit):
        r = client.post(URL, json={"email": "Target@example.org"}, headers={"X-Forwarded-For": f"10.1.0.{i}"})
        assert r.status_code == 200
    r = client.post(URL, json={"email": "target@example.org "}, headers={"X-Forwarded-For": "10.1.1.1"})
    assert r.status_code == 429
    # Pending resends were throttled to one email regardless.
    assert len(mails) == 1


def test_rate_limiter_window_slides():
    limiter = subscribe_routes.RateLimiter(2, 60)
    assert limiter.allow("k", now=0) and limiter.allow("k", now=1)
    assert not limiter.allow("k", now=59)
    assert limiter.allow("k", now=61)


def test_token_routes_are_rate_limited_per_client(client):
    limit = subscribe_routes.CLIENT_TOKEN_LIMIT[0]
    headers = {"X-Forwarded-For": "198.51.100.9"}
    for _ in range(limit):
        assert client.post(CONFIRM, data={"token": "x" * 43}, headers=headers).status_code == 400
    r = client.post(UNSUB, data={"token": "x" * 43}, headers=headers)
    assert r.status_code == 429 and "Too many requests" in r.text
    _assert_private_headers(r)


def _request(headers=(), client=("192.0.2.1", 5000)):
    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers], "client": client}
    return Request(scope)


@pytest.mark.parametrize("hops, headers, expected", [
    (None, [], "192.0.2.1"),
    (None, [("X-Forwarded-For", "203.0.113.5")], "203.0.113.5"),
    # The leftmost entry is whatever the client sent: spoofable, ignored.
    (None, [("X-Forwarded-For", "6.6.6.6, 203.0.113.5")], "203.0.113.5"),
    ("2", [("X-Forwarded-For", "6.6.6.6, 203.0.113.5, 10.0.0.2")], "203.0.113.5"),
    ("2", [("X-Forwarded-For", "203.0.113.5")], "192.0.2.1"),
    ("0", [("X-Forwarded-For", "203.0.113.5")], "192.0.2.1"),
    ("junk", [("X-Forwarded-For", "1.1.1.1, 203.0.113.5")], "203.0.113.5"),
    (None, [("X-Forwarded-For", "6.6.6.6"), ("X-Forwarded-For", "203.0.113.5")], "203.0.113.5"),
    (None, [("X-Forwarded-For", " , ")], "192.0.2.1"),
])
def test_client_address_counts_forwarded_for_from_the_right(monkeypatch, hops, headers, expected):
    if hops is not None:
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", hops)
    assert subscribe_routes.client_address(_request(headers)) == expected


def test_client_address_without_a_peer():
    assert subscribe_routes.client_address(_request(client=None)) == "unknown"


# --- Confirm ----------------------------------------------------------------------

def test_get_confirm_changes_nothing(client, mails):
    confirm_token, _ = _subscribe_and_get_tokens(client, mails)
    before = _file()
    r = client.get(CONFIRM, params={"token": confirm_token, "lang": "en"})
    assert r.status_code == 200
    assert _file() == before
    assert subscribers.subscriber_counts("nz")["pending"] == 1
    assert '<form method="post" action="/api/subscribe/confirm?lang=en">' in r.text
    assert f'name="token" value="{confirm_token}"' in r.text
    assert "<script" not in r.text.lower()
    _assert_private_headers(r)
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_post_confirm_confirms(client, mails):
    confirm_token, _ = _subscribe_and_get_tokens(client, mails)
    r = client.post(f"{CONFIRM}?lang=en", data={"token": confirm_token})
    assert r.status_code == 200 and "Subscription confirmed" in r.text
    assert f'href="{APP}/"' in r.text
    _assert_private_headers(r)
    assert subscribers.subscriber_counts("nz")["confirmed"] == 1


def test_unknown_and_expired_confirm_tokens_get_the_same_page(client, mails, monkeypatch):
    confirm_token, _ = _subscribe_and_get_tokens(client, mails)
    unknown = client.post(CONFIRM, data={"token": "A" * 43})
    real_now = datetime.now(timezone.utc)
    monkeypatch.setattr(subscribers, "_now", lambda now: now or real_now + timedelta(days=8))
    expired = client.post(CONFIRM, data={"token": confirm_token})
    assert unknown.status_code == expired.status_code == 400
    assert unknown.text == expired.text
    assert "This link is not valid" in unknown.text
    assert '<form method="post" action="/api/subscribe">' in unknown.text  # subscribe again


def test_hostile_token_and_lang_are_not_reflected(client):
    r = client.get(CONFIRM, params={"token": '"><script>alert(1)</script>', "lang": '"><b>x'})
    assert r.status_code == 400
    assert "<script>" not in r.text and "<b>x" not in r.text
    assert '<html lang="en">' in r.text


def test_page_language_follows_lang_within_the_country(client, monkeypatch):
    monkeypatch.setenv("GOBUGA_COUNTRY", "md")
    token = "A" * 43
    assert "Подтвердите подписку" in client.get(CONFIRM, params={"token": token, "lang": "ru"}).text
    assert "Confirm your subscription" in client.get(CONFIRM, params={"token": token, "lang": "en"}).text
    # Unknown language: the country's content language.
    assert "Confirmați abonarea" in client.get(CONFIRM, params={"token": token, "lang": "de"}).text
    # NZ offers only English, so a Russian request is served in English.
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    assert "Confirm your subscription" in client.get(CONFIRM, params={"token": token, "lang": "ru"}).text


def test_get_confirm_without_token(client):
    r = client.get(CONFIRM)
    assert r.status_code == 400 and "This link is not valid" in r.text


# --- Unsubscribe ------------------------------------------------------------------

def test_get_unsubscribe_changes_nothing(client, mails):
    confirm_token, unsub = _subscribe_and_get_tokens(client, mails)
    client.post(CONFIRM, data={"token": confirm_token})
    before = _file()
    r = client.get(UNSUB, params={"token": unsub})
    assert r.status_code == 200 and _file() == before
    assert subscribers.subscriber_counts("nz")["confirmed"] == 1
    assert '<form method="post" action="/api/subscribe/unsubscribe?lang=en">' in r.text
    _assert_private_headers(r)


def test_post_unsubscribe_unsubscribes(client, mails):
    confirm_token, unsub = _subscribe_and_get_tokens(client, mails)
    client.post(CONFIRM, data={"token": confirm_token})
    r = client.post(UNSUB, data={"token": unsub})
    assert r.status_code == 200 and "You have unsubscribed" in r.text
    _assert_private_headers(r)
    assert subscribers.subscriber_counts("nz") == {"confirmed": 0, "pending": 0, "unsubscribed": 1}


def test_one_click_unsubscribe_urlencoded(client, mails):
    confirm_token, unsub = _subscribe_and_get_tokens(client, mails)
    client.post(CONFIRM, data={"token": confirm_token})
    r = client.post(f"{UNSUB}?token={unsub}&lang=en&utm_medium=email",
                    content="List-Unsubscribe=One-Click",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    assert subscribers.subscriber_counts("nz")["unsubscribed"] == 1


def test_one_click_unsubscribe_multipart(client, mails):
    _, unsub = _subscribe_and_get_tokens(client, mails)
    r = client.post(f"{UNSUB}?token={unsub}", files={"List-Unsubscribe": (None, "One-Click")})
    assert r.status_code == 200
    assert subscribers.subscriber_counts("nz")["unsubscribed"] == 1


def test_unknown_unsubscribe_token(client):
    r = client.post(UNSUB, data={"token": "B" * 43})
    assert r.status_code == 400 and "This unsubscribe link is not valid" in r.text


def test_email_links_work_end_to_end(client, mails):
    """Follow the exact URLs from the email: GET shows a button; POSTing the form works."""
    client.post(URL, json={"email": "a@example.org"})
    (_, msg), = mails
    link = re.search(r"https://gobuga\.test(/api/subscribe/confirm\?\S+)", msg["text"]).group(1)
    page = client.get(link)
    action = re.search(r'action="([^"]+)"', page.text).group(1).replace("&amp;", "&")
    token = re.search(r'name="token" value="([^"]+)"', page.text).group(1)
    assert client.post(action, data={"token": token}).status_code == 200
    assert subscribers.subscriber_counts("nz")["confirmed"] == 1


def test_subscribe_routes_are_not_opened_to_other_origins(client):
    r = client.options(URL, headers={"Origin": "https://evil.example",
                                     "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") != "https://evil.example"


# --- Audit addition: the redirect keeps the visitor's language ------------------

def test_form_redirect_carries_a_non_default_language(monkeypatch):
    from api import subscribe_routes as routes
    from api.country_config import clear_config_cache
    monkeypatch.setenv("GOBUGA_COUNTRY", "md")
    monkeypatch.setenv("APP_URL", "https://md.gobuga.org")
    clear_config_cache()
    try:
        assert routes._form_redirect("sent", "ru").headers["location"] == \
            "https://md.gobuga.org/subscribe?state=sent&lang=ru"
        # The default language, an unknown one and a hostile one add nothing.
        for lang in ("ro", "xx", "ru&state=invalid", None, 5):
            assert routes._form_redirect("sent", lang).headers["location"] == \
                "https://md.gobuga.org/subscribe?state=sent"
    finally:
        clear_config_cache()
