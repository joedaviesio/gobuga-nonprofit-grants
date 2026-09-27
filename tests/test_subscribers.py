"""Unit tests for api/subscribers.py and the list emails in api/email.py.

In-process, no network. RESEND_API_KEY is cleared and `resend.Emails.send`
is stubbed for every test; the send command is always given a fake sender.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

# api.email calls load_dotenv() on import, which can pull a developer's .env
# (and a real RESEND_API_KEY) into os.environ. Import it first so the autouse
# fixture's delenv wins.
import api.email as email_mod
import resend

from api import published, sources, subscribers, tenant

NOW = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
APP = "https://gobuga.test"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_FROM", raising=False)
    monkeypatch.delenv("DIRECTOR_EMAIL", raising=False)
    monkeypatch.setenv("APP_URL", APP + "/")
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setattr(sources, "must_appear_names", lambda country: [])
    return tmp_path


@pytest.fixture(autouse=True)
def resend_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(resend.Emails, "send", staticmethod(lambda params: calls.append(params)))
    monkeypatch.setattr(resend, "api_key", None)
    return calls


class FakeSender:
    def __init__(self, fail_for=(), stop_after=None):
        self.sent = []
        self.fail_for = set(fail_for)
        self.stop_after = stop_after

    def __call__(self, to, message):
        if to in self.fail_for:
            raise RuntimeError("mailbox unavailable")
        if self.stop_after is not None and len(self.sent) >= self.stop_after:
            raise KeyboardInterrupt
        self.sent.append((to, message))


def _file(country="nz"):
    with open(subscribers.subscribers_path(country)) as f:
        return f.read()


def _records(country="nz"):
    return json.loads(_file(country))["subscribers"]


def _confirmed(email, country="nz", now=NOW, lang="en"):
    """A confirmed subscriber; returns an unsubscribe token for them."""
    _, token = subscribers.subscribe(email, lang, country, now)
    assert subscribers.confirm(token, country, now)
    return subscribers.issue_unsubscribe_token(email, country, now)


# --- Address validation --------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("a@b.co", "a@b.co"),
    ("  Foo.Bar+tag@Example.ORG ", "foo.bar+tag@example.org"),
    ("o'neil@sub.domain.nz", "o'neil@sub.domain.nz"),
    ("x@xn--80ak6aa92e.com", "x@xn--80ak6aa92e.com"),
])
def test_valid_addresses_are_trimmed_and_lowercased(raw, expected):
    assert subscribers.normalise_email(raw) == expected


@pytest.mark.parametrize("raw", [
    None, 42, "", "   ", "no-at-sign", "a@@b.co", "a@b@c.co", "a@localhost", "@b.co", "a@",
    "a b@c.co", "a\t@b.co", "a@b.co\x00", "a@b..co", ".a@b.co", "a.@b.co", "a..b@c.co",
    "a@-b.co", "a@b-.co", "a@b.c", "a@b.12", "a@b.co ", "ü@b.co", "a@bü.co",
    "<a@b.co>", "a@b.co,c@d.co", "a@b.co;c@d.co", "x" * 65 + "@b.co",
    "a@" + "b" * 250 + ".co",
])
def test_invalid_addresses_are_rejected(raw):
    with pytest.raises(subscribers.InvalidEmail):
        subscribers.normalise_email(raw)


@pytest.mark.parametrize("raw", [
    "a@b.co\n", "\na@b.co", "a@b.co\r", "a@b.co\r\nBcc: victim@example.org",
    "a@b.co\nSubject: hi", "a\r@b.co",
])
def test_header_injection_is_rejected_even_at_the_ends(raw):
    with pytest.raises(subscribers.InvalidEmail):
        subscribers.normalise_email(raw)
    with pytest.raises(subscribers.InvalidEmail):
        subscribers.subscribe(raw, "en", now=NOW)


def test_invalid_address_error_does_not_echo_the_input():
    with pytest.raises(subscribers.InvalidEmail) as exc:
        subscribers.normalise_email("secret-thing@@x")
    assert "secret-thing" not in str(exc.value)


# --- State transitions -----------------------------------------------------------

def test_new_subscriber_is_pending_with_a_confirm_token():
    outcome, token = subscribers.subscribe("New@Example.org", "en", now=NOW)
    assert outcome == "new" and token
    record = _records()["new@example.org"]
    assert record["confirmed_at"] is None and record["unsubscribed_at"] is None
    assert subscribers.state_of(record) == "pending"
    assert subscriber_counts() == {"confirmed": 0, "pending": 1, "unsubscribed": 0}


def subscriber_counts(now=NOW):
    return subscribers.subscriber_counts("nz", now)


def test_confirm_moves_pending_to_confirmed_and_is_idempotent():
    _, token = subscribers.subscribe("a@example.org", "en", now=NOW)
    assert subscribers.confirm(token, now=NOW + timedelta(hours=1))
    assert subscribers.confirm(token, now=NOW + timedelta(hours=2))
    record = _records()["a@example.org"]
    assert record["confirmed_at"] == (NOW + timedelta(hours=1)).isoformat()
    assert subscriber_counts() == {"confirmed": 1, "pending": 0, "unsubscribed": 0}


def test_subscribing_a_confirmed_address_does_nothing():
    _confirmed("a@example.org")
    before = _records()["a@example.org"]
    assert subscribers.subscribe("a@example.org", "en", now=NOW + timedelta(days=3)) == (
        "already_confirmed", None)
    assert _records()["a@example.org"] == before


def test_pending_resend_is_throttled_to_once_an_hour():
    _, first = subscribers.subscribe("a@example.org", "en", now=NOW)
    assert subscribers.subscribe("a@example.org", "en", now=NOW + timedelta(minutes=59)) == (
        "throttled", None)
    outcome, second = subscribers.subscribe("a@example.org", "en", now=NOW + timedelta(minutes=61))
    assert outcome == "resent" and second and second != first
    # Only the newest link confirms.
    assert not subscribers.confirm(first, now=NOW + timedelta(minutes=62))
    assert subscribers.confirm(second, now=NOW + timedelta(minutes=62))


def test_unsubscribe_then_resubscribe_starts_again_as_pending():
    old_unsub = _confirmed("a@example.org")
    assert subscribers.unsubscribe(old_unsub, now=NOW + timedelta(days=1))
    assert subscribers.unsubscribe(old_unsub, now=NOW + timedelta(days=2))  # idempotent
    record = _records()["a@example.org"]
    assert record["unsubscribed_at"] == (NOW + timedelta(days=1)).isoformat()
    assert record["confirm_token_hash"] is None
    assert subscriber_counts(NOW + timedelta(days=2)) == {"confirmed": 0, "pending": 0, "unsubscribed": 1}

    outcome, token = subscribers.subscribe("a@example.org", "en", now=NOW + timedelta(days=3))
    assert outcome == "resubscribed" and token
    record = _records()["a@example.org"]
    assert subscribers.state_of(record) == "pending"
    assert record["unsubscribe_token_hashes"] == []
    # The old subscription's unsubscribe link belongs to the old subscription.
    assert not subscribers.unsubscribe(old_unsub, now=NOW + timedelta(days=3))
    assert subscribers.confirm(token, now=NOW + timedelta(days=3))


def test_pending_address_can_unsubscribe_from_the_confirmation_email():
    _, confirm_token = subscribers.subscribe("a@example.org", "en", now=NOW)
    unsub = subscribers.issue_unsubscribe_token("a@example.org", now=NOW)
    assert subscribers.unsubscribe(unsub, now=NOW)
    assert not subscribers.confirm(confirm_token, now=NOW)


def test_unsubscribe_token_lasts_as_long_as_the_subscription():
    unsub = _confirmed("a@example.org")
    later = NOW + timedelta(days=400)
    assert subscribers.unsubscribe(unsub, now=later)


# --- Tokens --------------------------------------------------------------------

def test_only_token_hashes_are_stored():
    _, confirm_token = subscribers.subscribe("a@example.org", "en", now=NOW)
    unsub = subscribers.issue_unsubscribe_token("a@example.org", now=NOW)
    raw = _file()
    assert confirm_token not in raw and unsub not in raw
    record = _records()["a@example.org"]
    assert record["confirm_token_hash"] == hashlib.sha256(confirm_token.encode()).hexdigest()
    assert record["unsubscribe_token_hashes"] == [hashlib.sha256(unsub.encode()).hexdigest()]


def test_a_stored_hash_is_not_itself_a_token():
    _, confirm_token = subscribers.subscribe("a@example.org", "en", now=NOW)
    stored = _records()["a@example.org"]["confirm_token_hash"]
    assert not subscribers.confirm(stored, now=NOW)


def test_confirm_token_expires_after_seven_days():
    _, token = subscribers.subscribe("a@example.org", "en", now=NOW)
    assert not subscribers.confirm(token, now=NOW + timedelta(days=7, seconds=1))


def test_confirm_token_works_just_inside_seven_days():
    _, token = subscribers.subscribe("a@example.org", "en", now=NOW)
    assert subscribers.confirm(token, now=NOW + timedelta(days=6, hours=23))


@pytest.mark.parametrize("token", [None, "", "x", "a" * 200, "has space", "tok\n", 12, "../../x"])
def test_malformed_tokens_never_match(token):
    subscribers.subscribe("a@example.org", "en", now=NOW)
    assert not subscribers.confirm(token, now=NOW)
    assert not subscribers.unsubscribe(token, now=NOW)


def test_confirm_token_cannot_unsubscribe_and_vice_versa():
    _, confirm_token = subscribers.subscribe("a@example.org", "en", now=NOW)
    unsub = subscribers.issue_unsubscribe_token("a@example.org", now=NOW)
    assert not subscribers.unsubscribe(confirm_token, now=NOW)
    assert not subscribers.confirm(unsub, now=NOW)


def test_unsubscribe_token_list_is_capped():
    _confirmed("a@example.org")
    for _ in range(subscribers.MAX_UNSUBSCRIBE_TOKENS + 5):
        last = subscribers.issue_unsubscribe_token("a@example.org", now=NOW)
    hashes = _records()["a@example.org"]["unsubscribe_token_hashes"]
    assert len(hashes) == subscribers.MAX_UNSUBSCRIBE_TOKENS
    assert subscribers.unsubscribe(last, now=NOW)


# --- Purging -----------------------------------------------------------------------

def test_unconfirmed_records_are_purged_after_seven_days():
    subscribers.subscribe("a@example.org", "en", now=NOW)
    later = NOW + timedelta(days=7, minutes=1)
    assert subscriber_counts(later) == {"confirmed": 0, "pending": 0, "unsubscribed": 0}
    subscribers.subscribe("b@example.org", "en", now=later)  # any write purges
    assert set(_records()) == {"b@example.org"}


def test_unsubscribed_records_lose_their_email_after_thirty_days():
    unsub = _confirmed("gone@example.org")
    subscribers.unsubscribe(unsub, now=NOW)
    later = NOW + timedelta(days=31)
    subscribers.subscribe("other@example.org", "en", now=later)
    assert "gone@example.org" not in _file()
    assert json.loads(_file())["purged_unsubscribed"] == 1
    assert subscriber_counts(later) == {"confirmed": 0, "pending": 1, "unsubscribed": 1}


def test_unsubscribed_record_is_kept_within_thirty_days():
    unsub = _confirmed("a@example.org")
    subscribers.unsubscribe(unsub, now=NOW)
    subscribers.subscribe("other@example.org", "en", now=NOW + timedelta(days=29))
    assert "a@example.org" in _records()


# --- Shape of what is stored and returned ----------------------------------------------

RECORD_KEYS = {
    "email", "lang", "created_at", "confirmed_at", "unsubscribed_at", "last_sent_publish",
    "confirm_token_hash", "confirm_sent_at", "unsubscribe_token_hashes",
}


def test_record_holds_only_the_allowed_fields():
    _confirmed("a@example.org")
    data = json.loads(_file())
    assert set(data) == {"country", "subscribers", "purged_unsubscribed"}
    assert set(data["subscribers"]["a@example.org"]) == RECORD_KEYS
    raw = _file().lower()
    for word in ("ip", "user_agent", "agent", "name", "forwarded"):
        assert f'"{word}"' not in raw


def test_subscriber_counts_shape_with_nothing_stored():
    assert subscribers.subscriber_counts("nz") == {"confirmed": 0, "pending": 0, "unsubscribed": 0}


def test_confirmed_subscribers_have_no_token_hashes():
    _confirmed("a@example.org")
    subscribers.subscribe("pending@example.org", "en", now=NOW)
    rows = subscribers.confirmed_subscribers("nz", NOW)
    assert [r["email"] for r in rows] == ["a@example.org"]
    assert not any("hash" in k for k in rows[0])


def test_lang_is_one_of_the_country_ui_languages():
    subscribers.subscribe("a@example.org", "ru", "md", NOW)
    subscribers.subscribe("b@example.org", "fr", "md", NOW)
    subscribers.subscribe("c@example.org", '"><script>', "md", NOW)
    recs = _records("md")
    assert recs["a@example.org"]["lang"] == "ru"
    assert recs["b@example.org"]["lang"] == "ro"
    assert recs["c@example.org"]["lang"] == "ro"
    # NZ offers only English.
    subscribers.subscribe("d@example.org", "ru", "nz", NOW)
    assert _records("nz")["d@example.org"]["lang"] == "en"


def test_country_slug_is_validated():
    with pytest.raises(ValueError):
        subscribers.subscribers_path("../etc")


def test_malformed_file_is_not_overwritten(isolated):
    path = subscribers.subscribers_path("nz")
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("[]")
    with pytest.raises(RuntimeError):
        subscribers.subscribe("a@example.org", "en", now=NOW)
    assert _file() == "[]"


# --- Emails -----------------------------------------------------------------------

def _hrefs(html_text):
    return re.findall(r'href="([^"]*)"', html_text)


def test_confirmation_email_content_and_headers():
    msg = email_mod.build_confirmation_email(
        confirm_url=f"{APP}/api/subscribe/confirm?token=T1&lang=en&utm_medium=email",
        unsubscribe_url=f"{APP}/api/subscribe/unsubscribe?token=U1&lang=en&utm_medium=email",
        lang="en", interval_months=2, confirm_days=7,
    )
    assert msg["subject"] == "Confirm your GoBuga email subscription"
    for part in (msg["text"], msg["html"]):
        assert "every two months" in part
        assert "7 days" in part
        assert "you will not hear from us again" in part
    assert f"{APP}/api/subscribe/confirm?token=T1&lang=en&utm_medium=email" in msg["text"]
    assert f"{APP}/api/subscribe/unsubscribe?token=U1&lang=en&utm_medium=email" in msg["text"]
    assert msg["headers"] == {
        "List-Unsubscribe": f"<{APP}/api/subscribe/unsubscribe?token=U1&lang=en&utm_medium=email>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    assert "<img" not in msg["html"].lower()
    assert all("utm_medium=email" in h for h in _hrefs(msg["html"]))


@pytest.mark.parametrize("lang, needle", [
    ("ro", "o dată la două luni"), ("ru", "раз в два месяца"), ("en", "every two months"),
])
def test_confirmation_email_is_written_in_the_subscribers_language(lang, needle):
    msg = email_mod.build_confirmation_email(
        confirm_url="https://x.test/c", unsubscribe_url="https://x.test/u",
        lang=lang, interval_months=2, confirm_days=7,
    )
    assert needle in msg["text"]
    assert f'<html lang="{lang}">' in msg["html"]


@pytest.mark.parametrize("n, lang, expected", [
    (1, "en", "1 day"), (7, "en", "7 days"), (7, "ro", "7 zile"), (30, "ro", "30 de zile"),
    (1, "ro", "1 zi"), (7, "ru", "7 дней"), (2, "ru", "2 дня"), (21, "ru", "21 день"),
    (11, "ru", "11 дней"), (30, "ru", "30 дней"),
])
def test_count_of(n, lang, expected):
    assert email_mod.count_of(n, lang, "day") == expected


def _summary(**overrides):
    summary = {
        "publish_id": "2026-10-05T00:00:00+00:00",
        "sweep_month": "2026-10",
        "next_sweep_due": "2026-12",
        "new_count": 12,
        "changed_count": 3,
        "closing_count": 4,
        "closing_days": 30,
        "new_grants": [{"id": "OPP-1", "title": "Grant one",
                        "url": f"{APP}/grants/OPP-1?utm_medium=email"}],
        "changes_url": f"{APP}/changes?utm_medium=email",
        "home_url": f"{APP}/?utm_medium=email",
    }
    summary.update(overrides)
    return summary


def test_sweep_email_content():
    msg = email_mod.build_sweep_email(_summary(), unsubscribe_url=f"{APP}/u?token=U", lang="en")
    assert msg["subject"] == "GoBuga grant update: October 2026"
    text = msg["text"]
    for line in ("New grants: 12", "Changed grants: 3", "Closing in the next 30 days: 4",
                 "- Grant one", f"{APP}/grants/OPP-1?utm_medium=email",
                 "Plus 11 more on the changes page.", f"{APP}/changes?utm_medium=email",
                 "The next sweep is due in December 2026.", f"{APP}/u?token=U"):
        assert line in text
    assert msg["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert msg["headers"]["List-Unsubscribe"] == f"<{APP}/u?token=U>"


def test_sweep_email_has_no_pixel_and_no_wrapped_links():
    msg = email_mod.build_sweep_email(_summary(), unsubscribe_url=f"{APP}/u?token=U", lang="en")
    html_text = msg["html"].lower()
    assert "<img" not in html_text and "background=" not in html_text and "url(" not in html_text
    hrefs = _hrefs(msg["html"])
    assert hrefs and all(h.startswith(APP) for h in hrefs)


def test_hostile_grant_title_is_escaped_in_html():
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>"\'&'
    msg = email_mod.build_sweep_email(
        _summary(new_grants=[{"id": "OPP-1", "title": evil, "url": f'{APP}/grants/OPP-1?a="><b>'}]),
        unsubscribe_url=f"{APP}/u?token=U", lang="en",
    )
    assert "<script" not in msg["html"] and "<img" not in msg["html"] and "<b>" not in msg["html"]
    assert "&lt;script&gt;" in msg["html"]
    assert "&quot;&gt;&lt;b&gt;" in msg["html"]


def test_overlong_grant_title_is_cut():
    msg = email_mod.build_sweep_email(
        _summary(new_grants=[{"id": "OPP-1", "title": "x" * 5000, "url": f"{APP}/g"}]),
        unsubscribe_url="u", lang="en",
    )
    assert "x" * email_mod.MAX_TITLE_CHARS in msg["text"]
    assert "x" * (email_mod.MAX_TITLE_CHARS + 1) not in msg["text"]


@pytest.mark.parametrize("lang", ["xx", "", None, '"><script>', "../en", "EN", 5])
def test_hostile_lang_falls_back_to_english(lang):
    msg = email_mod.build_sweep_email(_summary(), unsubscribe_url="u", lang=lang)
    assert msg["subject"] == "GoBuga grant update: October 2026"
    assert '<html lang="en">' in msg["html"]


def test_sweep_email_in_russian_and_romanian():
    ru = email_mod.build_sweep_email(_summary(), unsubscribe_url="u", lang="ru")
    ro = email_mod.build_sweep_email(_summary(), unsubscribe_url="u", lang="ro")
    assert ru["subject"] == "Обновление грантов GoBuga: октябрь 2026"
    assert "Закрываются в ближайшие 30 дней: 4" in ru["text"]
    assert ro["subject"] == "Actualizare granturi GoBuga: octombrie 2026"
    assert "Se închid în următoarele 30 de zile: 4" in ro["text"]


def test_send_list_email_without_key_prints_and_skips_resend(resend_calls, capsys):
    msg = email_mod.build_sweep_email(_summary(), unsubscribe_url="u", lang="en")
    email_mod.send_list_email("a@example.org", msg)
    assert resend_calls == []
    assert "a@example.org" in capsys.readouterr().out


def test_send_list_email_passes_headers_to_resend(resend_calls, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_fake")
    msg = email_mod.build_sweep_email(_summary(), unsubscribe_url=f"{APP}/u?token=U", lang="en")
    email_mod.send_list_email("a@example.org", msg)
    (payload,) = resend_calls
    assert payload["to"] == ["a@example.org"]
    assert payload["headers"] == msg["headers"]
    assert payload["text"] == msg["text"] and payload["html"] == msg["html"]


def test_send_confirmation_builds_links_from_app_url():
    _, token = subscribers.subscribe("a@example.org", "en", now=NOW)
    sender = FakeSender()
    subscribers.send_confirmation("a@example.org", token, "en", sender=sender)
    ((to, msg),) = sender.sent
    assert to == "a@example.org"
    assert f"{APP}/api/subscribe/confirm?token={token}&lang=en&utm_medium=email" in msg["text"]
    unsub = re.search(r"unsubscribe\?token=([A-Za-z0-9_-]+)", msg["headers"]["List-Unsubscribe"]).group(1)
    assert subscribers.unsubscribe(unsub)


# --- Sweep summary and the send command ----------------------------------------------

def _row(n, deadline="2027-06-30", title=None):
    return {
        "id": f"OPP-NZ-2026-10-{n:04d}",
        "country": "nz",
        "title": title or f"Grant {n}",
        "funder": f"Funder {n}",
        "deadline": deadline,
        "deadline_state": "dated",
        "verified_at": "2026-10-04T00:00:00+00:00",
        "source_url": f"https://funder{n}.example/grants",
        "first_seen": "2026-10-04T00:00:00+00:00",
        "last_seen": "2026-10-04T00:00:00+00:00",
    }


def _publish(rows, now=NOW, month="2026-10"):
    published.publish_pool("nz", month, rows, now=now, notifier=lambda *a: None)


def test_sweep_summary_counts_and_links():
    rows = [_row(i) for i in range(1, 13)] + [_row(13, deadline="2026-10-20")]
    _publish(rows)
    s = subscribers.sweep_summary("nz", NOW)
    assert s["new_count"] == 13 and s["changed_count"] == 0 and s["closing_count"] == 1
    assert len(s["new_grants"]) == subscribers.MAX_NEW_IN_EMAIL
    assert s["new_grants"][0]["url"] == f"{APP}/grants/OPP-NZ-2026-10-0001?utm_medium=email"
    assert s["changes_url"] == f"{APP}/changes?utm_medium=email"
    assert s["next_sweep_due"] == "2026-12"


def test_sweep_summary_none_when_nothing_published():
    assert subscribers.sweep_summary("nz", NOW) is None


def test_send_refuses_when_nothing_published():
    _confirmed("a@example.org")
    out, sender = [], FakeSender()
    assert subscribers.send_sweep("nz", confirm_send=True, now=NOW, sender=sender, out=out.append) == 1
    assert sender.sent == [] and "nothing has been published" in out[-1]


def test_dry_run_prints_and_sends_nothing(resend_calls):
    _publish([_row(1, title="Community fund")])
    _confirmed("a@example.org")
    before = _file()
    out, sender = [], FakeSender()
    assert subscribers.send_sweep("nz", now=NOW, sender=sender, out=out.append) == 0
    printed = "\n".join(out)
    assert "to email now: 1" in printed
    assert "Subject: GoBuga grant update: October 2026" in printed
    assert "- Community fund" in printed
    assert "Dry run: nothing was sent" in printed
    assert sender.sent == [] and resend_calls == []
    assert _file() == before


def test_confirmed_send_reaches_only_confirmed_and_never_double_sends():
    _publish([_row(1)])
    _confirmed("a@example.org")
    _confirmed("b@example.org")
    subscribers.subscribe("pending@example.org", "en", now=NOW)
    gone = _confirmed("gone@example.org")
    subscribers.unsubscribe(gone, now=NOW)

    sender = FakeSender()
    assert subscribers.send_sweep("nz", confirm_send=True, now=NOW, sender=sender, out=lambda *_: None) == 0
    assert sorted(to for to, _ in sender.sent) == ["a@example.org", "b@example.org"]
    publish_id = published.load_changes("nz")[0]["published_at"]
    recs = _records()
    assert recs["a@example.org"]["last_sent_publish"] == publish_id
    assert recs["pending@example.org"]["last_sent_publish"] is None

    out, again = [], FakeSender()
    assert subscribers.send_sweep("nz", confirm_send=True, now=NOW, sender=again, out=out.append) == 1
    assert again.sent == [] and "no confirmed subscriber is waiting" in out[-1]


def test_each_sweep_email_carries_a_working_unsubscribe_token():
    _publish([_row(1)])
    _confirmed("a@example.org")
    sender = FakeSender()
    subscribers.send_sweep("nz", confirm_send=True, now=NOW, sender=sender, out=lambda *_: None)
    ((_, msg),) = sender.sent
    token = re.search(r"unsubscribe\?token=([A-Za-z0-9_-]+)", msg["text"]).group(1)
    assert token not in _file()
    assert subscribers.unsubscribe(token, now=NOW)


def test_send_refuses_an_empty_email_unless_allowed():
    _publish([_row(1)])
    _publish([_row(1)], now=NOW + timedelta(days=60), month="2026-12")  # nothing new or changed
    _confirmed("a@example.org", now=NOW + timedelta(days=60))
    later = NOW + timedelta(days=61)
    out, sender = [], FakeSender()
    assert subscribers.send_sweep("nz", confirm_send=True, now=later, sender=sender, out=out.append) == 1
    assert sender.sent == [] and "--allow-empty" in out[-1]
    assert subscribers.send_sweep("nz", confirm_send=True, allow_empty=True, now=later,
                                  sender=sender, out=lambda *_: None) == 0
    assert len(sender.sent) == 1


def test_one_failed_address_does_not_stop_the_run_and_is_retried():
    _publish([_row(1)])
    for name in ("a", "b", "c"):
        _confirmed(f"{name}@example.org")
    out, sender = [], FakeSender(fail_for={"b@example.org"})
    assert subscribers.send_sweep("nz", confirm_send=True, now=NOW, sender=sender, out=out.append) == 0
    assert sorted(to for to, _ in sender.sent) == ["a@example.org", "c@example.org"]
    assert any("failed: b@example.org" in line for line in out)
    assert _records()["b@example.org"]["last_sent_publish"] is None

    retry = FakeSender()
    subscribers.send_sweep("nz", confirm_send=True, now=NOW, sender=retry, out=lambda *_: None)
    assert [to for to, _ in retry.sent] == ["b@example.org"]


def test_interrupted_run_resumes_without_resending():
    _publish([_row(1)])
    for name in ("a", "b", "c", "d"):
        _confirmed(f"{name}@example.org")
    first = FakeSender(stop_after=2)
    with pytest.raises(KeyboardInterrupt):
        subscribers.send_sweep("nz", confirm_send=True, now=NOW, sender=first, out=lambda *_: None)
    done = {to for to, _ in first.sent}
    assert len(done) == 2

    second = FakeSender()
    subscribers.send_sweep("nz", confirm_send=True, now=NOW, sender=second, out=lambda *_: None)
    assert {to for to, _ in second.sent} == {"a@example.org", "b@example.org", "c@example.org",
                                              "d@example.org"} - done


def test_send_to_one_address_touches_no_record():
    _publish([_row(1)])
    _confirmed("a@example.org")
    before = _file()
    out, sender = [], FakeSender()
    code = subscribers.send_sweep("nz", to="Director@Example.org", to_lang="en", now=NOW,
                                  sender=sender, out=out.append)
    assert code == 0
    assert [to for to, _ in sender.sent] == ["director@example.org"]
    assert _file() == before


def test_send_to_rejects_a_bad_address():
    _publish([_row(1)])
    sender = FakeSender()
    assert subscribers.send_sweep("nz", to="a@b.co\nBcc: x@y.co", now=NOW, sender=sender,
                                  out=lambda *_: None) == 1
    assert sender.sent == []


def test_sweep_emails_go_out_in_each_subscribers_language():
    published.publish_pool("md", "2026-10", [{**_row(1), "country": "md", "id": "OPP-MD-2026-10-0001"}],
                           now=NOW, notifier=lambda *a: None)
    _confirmed("ro@example.org", "md", lang="ro")
    _confirmed("ru@example.org", "md", lang="ru")
    sender = FakeSender()
    subscribers.send_sweep("md", confirm_send=True, now=NOW, sender=sender, out=lambda *_: None)
    subjects = {to: msg["subject"] for to, msg in sender.sent}
    assert subjects == {"ro@example.org": "Actualizare granturi GoBuga: octombrie 2026",
                        "ru@example.org": "Обновление грантов GoBuga: октябрь 2026"}


def test_cli_status_prints_counts(capsys):
    _confirmed("a@example.org")
    assert subscribers.main(["status", "nz"]) == 0
    assert json.loads(capsys.readouterr().out) == {"confirmed": 1, "pending": 0, "unsubscribed": 0}


@pytest.mark.parametrize("argv", [[], ["send"], ["send", "nz", "--bogus"], ["send", "nz", "--to"],
                                  ["frobnicate", "nz"]])
def test_cli_usage_errors(argv, capsys):
    assert subscribers.main(argv) == 2
    assert "Usage" in capsys.readouterr().out


def test_cli_send_defaults_to_dry_run(monkeypatch, capsys, resend_calls):
    _publish([_row(1)])
    _confirmed("a@example.org")
    before = _file()
    calls = []
    monkeypatch.setattr(email_mod, "send_list_email", lambda *a: calls.append(a))
    assert subscribers.main(["send", "nz"]) == 0
    assert calls == [] and resend_calls == []
    assert _file() == before
    assert "Dry run" in capsys.readouterr().out
