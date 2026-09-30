"""The weekly traffic email: what it counts, what it says, and when it sends.

Pure Python: metrics lines are written to a throwaway data dir, Resend is
never imported, and the scheduler is driven by hand with fixed clocks.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from api import metrics, tenant
from api import weekly_report as wr

GPTBOT = "Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)"
CHATGPT_USER = "Mozilla/5.0 (compatible; ChatGPT-User/1.0; +https://openai.com/bot)"
CLAUDE_USER = "Claude-User/1.0"
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
BROWSER = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/128 Safari/537.36"

# A Wednesday, so "last week" is Mon 21 to Sun 27 Sep 2026 in NZ time.
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)
IN_WEEK = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
WEEK_BEFORE = IN_WEEK - timedelta(days=7)
LONG_AGO = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GOBUGA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setenv("STARTUP_SWEEP_DISABLED", "1")
    monkeypatch.delenv("REPORT_EMAIL", raising=False)
    monkeypatch.delenv("DIRECTOR_EMAIL", raising=False)
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    return tmp_path


def _hit(path, ua, ref=None, *, surface="page", now=IN_WEEK):
    metrics.record_hit(path, ua, ref, surface=surface, country="nz", now=now)


def _out(opp, funder, ua=BROWSER, ref=None, *, now=IN_WEEK):
    metrics.record_clickout(opp, funder, ref, ua, country="nz", now=now)


# --- Counting ---

def test_window_counts_roles_people_and_clickouts(data_dir):
    _hit("/grants/a", CHATGPT_USER)
    _hit("/grants/b", CLAUDE_USER)
    _hit("/grants/c", GPTBOT)
    _hit("/grants/d", GOOGLEBOT)
    _hit("/grants/e", BROWSER, "https://chatgpt.com/c/123")
    _hit("/grants/f", BROWSER, "https://www.google.com/")
    _hit("/api/v1/opportunities", "python-requests/2.32", surface="api")
    _out("OPP-1", "Lottery", ref="https://chatgpt.com/c/123")
    _out("OPP-2", "Lottery")
    _hit("/grants/old", CHATGPT_USER, now=WEEK_BEFORE)

    start, end = wr.week_bounds(NOW, wr.ZoneInfo("Pacific/Auckland"))
    week = wr.window_counts("nz", start, end)

    assert week["assistant_fetches"] == {"gptbot": 1, "claudebot": 1}
    assert week["training"] == {"gptbot": 1}
    assert week["search"] == {"googlebot": 1}
    assert week["assistant_referrals"] == 1
    assert week["assistant_referral_hosts"] == {"chatgpt.com": 1}
    assert week["human_pages"] == 2
    assert week["human_from_search"] == 1
    assert week["clickouts"] == 2
    assert week["assistant_clickouts"] == 1
    assert week["top_clickout_funders"] == [("Lottery", 2)]
    assert week["api_calls"] == 1
    assert week["hits"] == 7
    before = wr.window_counts("nz", start - timedelta(days=7), start)
    assert sum(before["assistant_fetches"].values()) == 1


def test_window_spans_a_month_boundary(data_dir):
    _hit("/x", CHATGPT_USER, now=datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc))
    _hit("/y", CHATGPT_USER, now=datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc))
    start = datetime(2026, 8, 31, tzinfo=timezone.utc)
    end = datetime(2026, 9, 2, tzinfo=timezone.utc)
    assert sum(wr.window_counts("nz", start, end)["assistant_fetches"].values()) == 2


def test_missing_month_file_counts_nothing(data_dir):
    start, end = wr.week_bounds(NOW, wr.ZoneInfo("Pacific/Auckland"))
    assert wr.window_counts("nz", start, end)["hits"] == 0


# --- The text ---

def test_email_leads_with_assistants_and_flags_firsts(data_dir):
    _hit("/grants/a", CHATGPT_USER)
    _hit("/grants/e", BROWSER, "https://chatgpt.com/c/1")
    subject, body = wr.compose("nz", NOW)

    assert subject.startswith("GoBuga NZ week to 27 Sep: 1 assistant fetches, 1 referrals")
    assert "week Mon 21 Sep to Sun 27 Sep 2026" in body
    assert body.index("ASSISTANTS") < body.index("CRAWLERS") < body.index("PEOPLE") < body.index("DATASET") < body.index("TOTALS")
    assert "First ever: an assistant fetched a page" in body
    assert "First ever: a person arrived from an assistant" in body
    assert "- OpenAI: 1 (0)" in body
    assert "from: chatgpt.com 1" in body


def test_no_first_ever_when_it_happened_before(data_dir):
    _hit("/grants/a", CHATGPT_USER, now=LONG_AGO)
    _hit("/grants/b", CHATGPT_USER)
    _, body = wr.compose("nz", NOW)
    assert "First ever" not in body
    assert "answer a question: 1 (0)" in body


def test_email_reports_the_dataset(data_dir, monkeypatch):
    from api import published
    monkeypatch.setattr(published, "published_meta", lambda country=None: {
        "published_at": "2026-09-28T02:41:00+00:00", "live_count": 563, "closed_count": 210,
        "funder_count": 140, "held_count": 0, "forced": False, "next_sweep_due": "2026-11",
    })
    _, body = wr.compose("nz", NOW)
    assert "Live grants 563, closed 210, funders 140" in body
    assert "Last published 2026-09-28; next sweep due 2026-11" in body


def test_email_survives_a_broken_dataset(data_dir, monkeypatch):
    from api import published
    def boom(country=None):
        raise RuntimeError("volume gone")
    monkeypatch.setattr(published, "published_meta", boom)
    _, body = wr.compose("nz", NOW)
    assert "Could not read the published dataset: RuntimeError: volume gone" in body


# --- Sending ---

def test_send_uses_report_email_then_director_email(data_dir, monkeypatch):
    sent = []
    from api import email as email_mod
    monkeypatch.setattr(email_mod, "send_text_email", lambda to, subject, text: sent.append((to, subject)))
    monkeypatch.setenv("DIRECTOR_EMAIL", "director@example.org")
    assert wr.send("nz", NOW)["to"] == "director@example.org"
    monkeypatch.setenv("REPORT_EMAIL", "reports@example.org")
    result = wr.send("nz", NOW)
    assert result == {"sent": True, "to": "reports@example.org", "subject": result["subject"]}
    assert [t for t, _ in sent] == ["director@example.org", "reports@example.org"]


def test_send_without_address_prints_and_does_not_send(data_dir, capsys, monkeypatch):
    from api import email as email_mod
    monkeypatch.setattr(email_mod, "send_text_email", lambda *a: pytest.fail("must not send"))
    result = wr.send("nz", NOW)
    assert result["sent"] is False and result["to"] is None
    assert "ASSISTANTS" in capsys.readouterr().out


def test_dry_run_returns_the_text_and_sends_nothing(data_dir, monkeypatch):
    from api import email as email_mod
    monkeypatch.setenv("REPORT_EMAIL", "reports@example.org")
    monkeypatch.setattr(email_mod, "send_text_email", lambda *a: pytest.fail("must not send"))
    result = wr.send("nz", NOW, dry_run=True)
    assert result["sent"] is False and "TOTALS" in result["body"]


def test_send_failure_is_reported_not_raised(data_dir, monkeypatch):
    from api import email as email_mod
    monkeypatch.setenv("REPORT_EMAIL", "reports@example.org")
    def boom(*a):
        raise RuntimeError("resend down")
    monkeypatch.setattr(email_mod, "send_text_email", boom)
    assert wr.send("nz", NOW)["error"] == "RuntimeError: resend down"


# --- Scheduling ---

def _nz(y, m, d, hour):
    return datetime(y, m, d, hour, tzinfo=wr.ZoneInfo("Pacific/Auckland")).astimezone(timezone.utc)


def test_due_only_on_monday_morning_once(data_dir, monkeypatch):
    monkeypatch.setenv("REPORT_EMAIL", "reports@example.org")
    sent = []
    from api import email as email_mod
    monkeypatch.setattr(email_mod, "send_text_email", lambda to, s, t: sent.append(s))

    assert wr.tick("nz", _nz(2026, 9, 27, 9)) is None          # Sunday
    assert wr.tick("nz", _nz(2026, 9, 28, 6)) is None          # Monday, too early
    assert wr.tick("nz", _nz(2026, 9, 28, 7))["sent"] is True  # Monday 07:00
    assert wr.tick("nz", _nz(2026, 9, 28, 12)) is None         # same week, already sent
    assert wr.tick("nz", _nz(2026, 9, 29, 9)) is None          # Tuesday
    assert wr.tick("nz", _nz(2026, 10, 5, 8))["sent"] is True  # next Monday
    assert len(sent) == 2
    assert "week to 04 Oct" in sent[1]


def test_failed_send_is_retried_next_tick(data_dir, monkeypatch):
    monkeypatch.setenv("REPORT_EMAIL", "reports@example.org")
    from api import email as email_mod
    calls = []
    def flaky(to, s, t):
        calls.append(s)
        if len(calls) == 1:
            raise RuntimeError("resend down")
    monkeypatch.setattr(email_mod, "send_text_email", flaky)
    assert wr.tick("nz", _nz(2026, 9, 28, 7))["sent"] is False
    assert wr.tick("nz", _nz(2026, 9, 28, 8))["sent"] is True
    assert wr.tick("nz", _nz(2026, 9, 28, 9)) is None


def test_no_address_marks_the_week_so_it_does_not_nag(data_dir, capsys):
    assert wr.tick("nz", _nz(2026, 9, 28, 7))["sent"] is False
    assert wr.tick("nz", _nz(2026, 9, 28, 8)) is None
    marker = os.path.join(str(data_dir / "platform"), "metrics", "nz", "weekly-sent.txt")
    assert open(marker).read().strip() == "2026-09-21"


def test_scheduler_respects_disable_flag(monkeypatch):
    monkeypatch.setenv("WEEKLY_REPORT_DISABLED", "1")
    assert wr.maybe_start_scheduler() is False


def test_moldova_uses_its_own_clock(data_dir, monkeypatch):
    monkeypatch.setenv("GOBUGA_COUNTRY", "md")
    md = wr.ZoneInfo("Europe/Chisinau")
    early = datetime(2026, 9, 28, 6, 30, tzinfo=md).astimezone(timezone.utc)
    late = datetime(2026, 9, 28, 7, 30, tzinfo=md).astimezone(timezone.utc)
    assert wr.due("md", early) is False
    assert wr.due("md", late) is True
