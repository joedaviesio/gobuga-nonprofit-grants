"""Unit tests for orchestrator/verify_pass.py.

The fetch and model functions are always fakes: no network, no Anthropic.
"""

import json
import threading
from datetime import date, datetime, timezone

import pytest

from api import tenant
from orchestrator import verify_pass
from orchestrator.verify_pass import interpret_answer, verify_rows


NOW = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)   # 5 Oct, 13:00 in Auckland
TODAY = date(2026, 10, 5)

PAGE = (
    "Community Grants\n\n  The Community Grants round is open.   Applications close "
    "30 November 2026 at 5pm.\nGrants of up to $20,000 are available from the Rata "
    "Foundation for incorporated societies and charitable trusts."
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))

    def no_real_fetch(*a, **kw):
        raise AssertionError("real fetch called")

    def no_real_ask(*a, **kw):
        raise AssertionError("real model called")
    monkeypatch.setattr(verify_pass, "default_fetch", no_real_fetch)
    monkeypatch.setattr(verify_pass, "make_default_ask", lambda model: no_real_ask)


def row(n=1, **kw):
    base = {
        "id": f"OPP-NZ-2026-10-{n:04d}",
        "title": "Community Grants",
        "funder": "Rata Foundation",
        "deadline": "TBC",
        "amount_min": None,
        "amount_max": 20000,
        "source_url": f"https://rata.example/grants/{n}",
        "first_seen": "2026-10-04T20:00:00+00:00",
        "dedupe_key": "rata-foundation|rata.example|tbc",
    }
    base.update(kw)
    return base


def answer(**kw):
    base = {
        "mentions_programme": True,
        "deadline_state": "dated",
        "deadline": "2026-11-30",
        "deadline_excerpt": "Applications close 30 November 2026 at 5pm.",
        "funder_confirmed": True,
        "funder_excerpt": "available from the Rata Foundation",
        "amount_confirmed": True,
        "amount_excerpt": "Grants of up to $20,000 are available",
        "eligibility_excerpt": "for incorporated societies and charitable trusts",
    }
    base.update(kw)
    return base


class FakeFetch:
    def __init__(self, pages=None, default=PAGE, fail=()):
        self.pages = pages or {}
        self.default = default
        self.fail = set(fail)
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, url, timeout):
        with self.lock:
            self.calls.append((url, timeout))
        if url in self.fail:
            raise TimeoutError("timed out")
        return self.pages.get(url, self.default)


class FakeAsk:
    def __init__(self, reply):
        self.reply = reply          # dict, str, or callable(row) -> dict|str
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, page_text, row, today):
        with self.lock:
            self.calls.append((row["id"], len(page_text), today))
        reply = self.reply(row) if callable(self.reply) else self.reply
        raw = reply if isinstance(reply, str) else json.dumps(reply)
        return raw, {"input_tokens": 1000, "output_tokens": 50, "api_calls": 1, "cost_usd": 0.001}


def run(rows, reply, **kw):
    kw.setdefault("fetch", FakeFetch())
    kw.setdefault("now", NOW)
    kw.setdefault("country", "nz")
    return verify_rows(rows, ask=FakeAsk(reply) if not isinstance(reply, FakeAsk) else reply, **kw)


# --- Happy paths -------------------------------------------------------------------

def test_dated_row_gets_fields_and_provenance():
    out, stats = run([row()], answer())
    r = out[0]
    assert r["deadline_state"] == "dated"
    assert r["deadline"] == "2026-11-30"
    assert r["dedupe_key"] == "rata-foundation|rata.example|2026-11-30"
    assert r["verified_at"] == NOW.isoformat()
    assert r["verified_by"].startswith("verify-pass/")
    assert r["source_excerpt"] == "Applications close 30 November 2026 at 5pm."
    assert set(r["provenance"]) == {"deadline", "funder", "amount", "eligibility"}
    assert r["provenance"]["deadline"]["source_url"] == "https://rata.example/grants/1"
    assert stats["dated"] == 1 and stats["unresolved"] == 0
    assert stats["api_calls"] == 1 and stats["cost_usd"] == 0.001


def test_rolling_and_closed():
    page = PAGE + " Applications are accepted at any time. This round is now closed."
    replies = {
        "OPP-NZ-2026-10-0001": answer(deadline_state="rolling", deadline=None,
                                      deadline_excerpt="Applications are accepted at any time."),
        "OPP-NZ-2026-10-0002": answer(deadline_state="closed", deadline=None,
                                      deadline_excerpt="This round is now closed."),
    }
    out, stats = run([row(1, deadline="rolling"), row(2)], lambda r: replies[r["id"]],
                     fetch=FakeFetch(default=page))
    assert out[0]["deadline_state"] == "rolling-confirmed" and out[0]["deadline"] == "rolling"
    assert out[1]["deadline_state"] == "closed" and out[1]["deadline"] == "TBC"
    assert stats["rolling_confirmed"] == 1 and stats["closed"] == 1


def test_past_date_means_closed():
    page = "The 2026 round closed on 1 September 2026. Thanks to all applicants."
    out, _ = run([row()], answer(deadline="2026-09-01",
                                 deadline_excerpt="The 2026 round closed on 1 September 2026."),
                 fetch=FakeFetch(default=page))
    assert out[0]["deadline_state"] == "closed" and out[0]["deadline"] == "2026-09-01"


def test_fenced_json_answer_is_accepted():
    out, _ = run([row()], "```json\n" + json.dumps(answer()) + "\n```")
    assert out[0]["deadline_state"] == "dated"


# --- The invented-deadline defence -------------------------------------------------------

def test_excerpt_not_in_page_is_unresolved():
    out, stats = run([row()], answer(deadline_excerpt="Applications close 15 December 2026."))
    assert out[0]["deadline_state"] == "unresolved"
    assert out[0]["unresolved_reason"] == "deadline excerpt not found in page"
    assert out[0]["verified_at"] is None and out[0]["provenance"] == {}
    assert out[0]["deadline"] == "TBC"          # nothing from the answer leaks in
    assert stats["excerpt_rejected"] == 1


def test_excerpt_match_normalises_whitespace_and_case():
    # PAGE has "open.   Applications close" and a newline; the excerpt does not.
    excerpt = "the community grants round is open. applications close 30 november 2026 at 5pm."
    out, _ = run([row()], answer(deadline_excerpt=excerpt))
    assert out[0]["deadline_state"] == "dated"


def test_excerpt_must_state_the_date_it_supports():
    # The excerpt is real, but says 30 November while the answer claims the 15th.
    out, _ = run([row()], answer(deadline="2026-11-15"))
    assert out[0]["deadline_state"] == "unresolved"
    assert "does not state the deadline date" in out[0]["unresolved_reason"]


def test_excerpt_naming_another_year_is_unresolved():
    # Last year's closing date, read as this year's: same day, wrong year.
    out, _ = run([row()], answer(deadline="2027-11-30"))
    assert out[0]["deadline_state"] == "unresolved"
    assert "different year" in out[0]["unresolved_reason"]


def test_excerpt_with_no_year_is_accepted():
    page = "Community Grants. Applications for this round close on 30 November at 5pm sharp."
    out, _ = run([row()], answer(deadline_excerpt="Applications for this round close on 30 November at 5pm sharp."),
                 fetch=FakeFetch(default=page))
    assert out[0]["deadline_state"] == "dated"
    assert out[0]["deadline"] == "2026-11-30"


def test_too_short_excerpt_rejected():
    out, _ = run([row()], answer(deadline_excerpt="30 November"))
    assert out[0]["unresolved_reason"] == "deadline excerpt not found in page"


def test_unverifiable_side_excerpts_are_dropped_not_fatal():
    out, _ = run([row()], answer(amount_excerpt="Grants of up to $99,000", funder_confirmed=False))
    assert out[0]["deadline_state"] == "dated"
    assert set(out[0]["provenance"]) == {"deadline", "eligibility"}


# --- Date plausibility -----------------------------------------------------------------

@pytest.mark.parametrize("bad, reason", [
    ("2029-11-30", "implausibly far ahead"),
    ("2024-11-30", "implausibly old"),
    ("30/11/2026", "not a YYYY-MM-DD date"),
    ("2026-02-30", "not a real date"),
    (None, "not a YYYY-MM-DD date"),
])
def test_implausible_dates_are_unresolved(bad, reason):
    page = PAGE + " Previous rounds: 30 November 2024. Future: 30 November 2029."
    fields, why = interpret_answer(answer(deadline=bad), page, row(), TODAY)
    assert fields is None and reason in why


def test_date_just_inside_two_years_is_accepted():
    page = "Applications for the 2028 round close on 30 September 2028."
    fields, _ = interpret_answer(
        answer(deadline="2028-09-30",
               deadline_excerpt="Applications for the 2028 round close on 30 September 2028."),
        page, row(), TODAY)
    assert fields["deadline"] == "2028-09-30"


# --- Other unresolved causes -------------------------------------------------------------

@pytest.mark.parametrize("reply, reason", [
    ("I think it closes in November.", "not a JSON object"),
    (answer(mentions_programme=False), "does not mention the programme"),
    (answer(deadline_state="unknown", deadline=None), "page gives no deadline"),
    (answer(deadline_state="soon"), "invalid deadline_state"),
])
def test_bad_answers_are_unresolved(reply, reason):
    out, _ = run([row()], reply)
    assert out[0]["deadline_state"] == "unresolved" and reason in out[0]["unresolved_reason"]


def test_model_exception_is_unresolved():
    def boom(page, r, today):
        raise RuntimeError("overloaded")
    out, stats = verify_rows([row()], fetch=FakeFetch(), ask=boom, now=NOW, country="nz")
    assert out[0]["unresolved_reason"].startswith("model call failed")
    assert stats["api_calls"] == 0


def test_fetch_failure_and_missing_url_are_unresolved_without_model_calls():
    fetch = FakeFetch(fail={"https://rata.example/grants/1"})
    ask = FakeAsk(answer())
    out, stats = run([row(1), row(2, source_url="")], ask, fetch=fetch)
    assert out[0]["unresolved_reason"].startswith("fetch failed")
    assert out[1]["unresolved_reason"] == "no source_url"
    assert ask.calls == []
    assert stats["fetch_failures"] == 1 and stats["unresolved"] == 2


def test_empty_page_is_unresolved():
    out, _ = run([row()], answer(), fetch=FakeFetch(default="   "))
    assert out[0]["unresolved_reason"] == "fetch returned an empty page"


# --- Bounds ---------------------------------------------------------------------------

def test_one_fetch_per_distinct_url_with_timeout():
    shared = "https://council.example/grants"
    rows = [row(i, source_url=shared) for i in range(1, 6)] + [row(6)]
    fetch = FakeFetch()
    ask = FakeAsk(answer())
    out, stats = run(rows, ask, fetch=fetch, fetch_timeout=7, max_concurrent=3)
    assert sorted(u for u, _ in fetch.calls) == sorted([shared, "https://rata.example/grants/6"])
    assert all(t == 7 for _, t in fetch.calls)
    assert len(ask.calls) == 6
    assert stats["distinct_urls"] == 2 and stats["fetches"] == 2
    assert [r["id"] for r in out] == [r["id"] for r in rows]     # order preserved


def test_page_is_capped_before_the_model_sees_it():
    ask = FakeAsk(answer())
    run([row()], ask, fetch=FakeFetch(default=PAGE + "x" * 50000), max_page_chars=1000)
    assert ask.calls[0][1] == 1000


def test_input_rows_are_not_mutated():
    original = row()
    snapshot = dict(original)
    run([original], answer())
    assert original == snapshot


def test_usage_is_logged_for_the_sweep_run(tmp_path):
    out, stats = run([row()], answer(), org_id_for_usage="_sweep:nz:2026-10:20261005T000000",
                     cycle_date="2026-10-05")
    log_dir = tmp_path / "platform" / "cycles" / "nz" / "2026-10" / "run-20261005T000000" / "logs"
    lines = [json.loads(l) for f in log_dir.iterdir() for l in f.read_text().splitlines()]
    assert lines[0]["caller"] == "verify_pass" and lines[0]["api_calls"] == 1
    assert lines[0]["input_tokens"] == 1000
