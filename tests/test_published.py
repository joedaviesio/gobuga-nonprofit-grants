"""Unit tests for api/published.py — the public, cumulative, verified dataset.

Pure Python: storage is redirected to tmp_path, the must-appear list is
patched per test, and the notifier is always a fake.
"""

import json
import os
from datetime import date, datetime, timezone

import pytest

# api.email calls load_dotenv() on import, which can pull a developer's .env
# into os.environ. Import it here so the autouse fixture's delenv wins.
import api.email
from api import published, sources, tenant


NOW = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
VERIFIED = "2026-10-04T22:00:00+00:00"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setattr(sources, "must_appear_names", lambda country: [])
    monkeypatch.delenv("DIRECTOR_EMAIL", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    return tmp_path


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def __call__(self, country, month, missing, held_path):
        self.calls.append((country, month, list(missing), held_path))


def row(n, **kw):
    """A verified, dated candidate row modelled on the sweep's output."""
    base = {
        "id": f"OPP-NZ-2026-10-{n:04d}",
        "country": "nz",
        "title": f"Programme {n}",
        "funder": f"Funder {n}",
        "deadline": "2026-11-30",
        "amount_min": None,
        "amount_max": 20000,
        "currency": "NZD",
        "region": ["canterbury"],
        "tags": ["community"],
        "eligibility": "Incorporated societies",
        "summary": "A fund.",
        "source_url": f"https://funder{n}.example.nz/grants",
        "evidence_ids": [f"EV-{n:04d}"],
        "first_seen": "2026-10-04T20:00:00+00:00",
        "last_seen": "2026-10-04T20:00:00+00:00",
        "dedupe_key": f"funder-{n}|funder{n}.example.nz|2026-11-30",
        "deadline_state": "dated",
        "verified_at": VERIFIED,
        "verified_by": "verify-pass/test",
        "source_excerpt": "Applications close 30 November 2026.",
        "provenance": {},
    }
    base.update(kw)
    return base


def publish(candidates, **kw):
    kw.setdefault("now", NOW)
    kw.setdefault("notifier", FakeNotifier())
    return published.publish_pool("nz", kw.pop("month", "2026-10"), candidates, **kw)


def stored_ids():
    return sorted(r["id"] for r in published.load_published("nz", today=date(2026, 10, 5),
                                                             include=("live", "closed", "stale")))


# --- Empty state -------------------------------------------------------------

def test_readers_return_empty_when_nothing_published():
    assert published.load_published("nz") == []
    assert published.get_published_row("OPP-NZ-2026-10-0001", "nz") is None
    assert published.load_changes("nz") == []
    meta = published.published_meta("nz")
    assert meta["published_at"] is None and meta["sweep_month"] is None
    assert meta["live_count"] == 0 and meta["held_count"] == 0 and meta["next_sweep_due"] is None


def test_readers_tolerate_corrupt_files(isolated):
    d = published.published_dir("nz")
    os.makedirs(d)
    for name in ("pool.json", "changes.json", "meta.json"):
        with open(os.path.join(d, name), "w") as f:
            f.write("{not json")
    assert published.load_published("nz") == []
    assert published.load_changes("nz") == []
    assert published.published_meta("nz")["published_at"] is None


# --- Gate --------------------------------------------------------------------

def test_gate_publishes_only_verified_resolved_rows():
    rows = [
        row(1),
        row(2, deadline="rolling", deadline_state="rolling-confirmed"),
        row(3, deadline_state="closed", deadline="2026-09-01"),
        row(4, deadline="TBC", deadline_state="unresolved", verified_at=None,
            unresolved_reason="page gives no deadline"),
        # Claims to be dated but was never verified: held regardless.
        row(5, verified_at=None),
        # Dated with no usable date.
        row(6, deadline="TBC"),
    ]
    report = publish(rows)
    assert stored_ids() == ["OPP-NZ-2026-10-0001", "OPP-NZ-2026-10-0002", "OPP-NZ-2026-10-0003"]
    held = {h["id"]: h["reason"] for h in report["held"]}
    assert held["OPP-NZ-2026-10-0004"] == "page gives no deadline"
    assert "not verified" in held["OPP-NZ-2026-10-0005"]
    assert "dated" in held["OPP-NZ-2026-10-0006"]
    on_disk = json.load(open(os.path.join(published.published_dir("nz"), "held.json")))
    assert {h["id"] for h in on_disk["held"]} == set(held)
    pool = json.load(open(os.path.join(published.published_dir("nz"), "pool.json")))
    assert all(r["deadline_state"] != "unresolved" for r in pool["opportunities"])
    assert all("unresolved_reason" not in r for r in pool["opportunities"])


# --- Dedupe -------------------------------------------------------------------

def test_dedupe_by_key_keeps_most_recent_verification_and_earliest_first_seen():
    a = row(1, dedupe_key="k|x|2026-11-30", title="Community Grants",
            verified_at="2026-10-01T00:00:00+00:00", first_seen="2026-09-01T00:00:00+00:00")
    b = row(2, dedupe_key="k|x|2026-11-30", title="Community Grant",
            verified_at="2026-10-04T00:00:00+00:00", amount_max=30000)
    report = publish([a, b])
    rows = published.load_published("nz", today=date(2026, 10, 5))
    assert len(rows) == 1
    only = rows[0]
    assert only["id"] == "OPP-NZ-2026-10-0001"          # oldest first_seen's id
    assert only["amount_max"] == 30000                 # facts from the newer verification
    assert only["first_seen"] == "2026-09-01T00:00:00+00:00"
    assert report["merges"][0]["rules"] == ["dedupe_key"]
    assert report["merges"][0]["merged"][0]["id"] == "OPP-NZ-2026-10-0001"


def test_same_key_with_unrelated_titles_is_not_merged():
    # The sweep's key is funder|domain|deadline: two programmes on one site
    # can share it. They stay separate and are flagged for review.
    a = row(1, dedupe_key="council|council.nz|tbc", title="Creative Communities Scheme",
            source_url="https://council.nz/a")
    b = row(2, dedupe_key="council|council.nz|tbc", title="Climate Action Fund",
            source_url="https://council.nz/b")
    report = publish([a, b])
    assert len(stored_ids()) == 2
    assert len(report["key_conflicts"]) == 1


def test_dedupe_by_normalised_title_within_funder():
    a = row(1, funder="Rata Foundation", title="Community Grants – Small",
            source_url="https://rata.example/a")
    b = row(2, funder="Rata Foundation", title="community grants (small)",
            source_url="https://rata.example/b", dedupe_key="other")
    publish([a, b])
    assert stored_ids() == ["OPP-NZ-2026-10-0001"]


def test_same_title_different_funders_not_merged():
    a = row(1, funder="Rata Foundation", title="Community Grants")
    b = row(2, funder="Toi Foundation", title="Community Grants")
    publish([a, b])
    assert len(stored_ids()) == 2


def test_dedupe_by_source_url_when_titles_near_identical():
    url = "https://www.pubcharity.example/grants/"
    a = row(1, funder="Pub Charity", title="Community Grant Funding", source_url=url)
    b = row(2, funder="Pub Charity Ltd", title="Community Grants Funding",
            source_url="https://pubcharity.example/grants", dedupe_key="other")
    report = publish([a, b])
    assert len(stored_ids()) == 1
    assert report["merges"][0]["rules"] == ["source_url"]


def test_dedupe_by_source_url_when_funder_deadline_amounts_match():
    url = "https://tect.example/"
    a = row(1, funder="TECT", title="Community Funding (five streams)", source_url=url,
            amount_max=39000000)
    b = row(2, funder="TECT", title="Multiple funding streams", source_url=url,
            amount_max=39000000, dedupe_key="other")
    publish([a, b])
    assert len(stored_ids()) == 1


def test_shared_source_url_with_distinct_programmes_is_not_collapsed():
    # One council page listing several programmes: all kept, group reported.
    url = "https://www.tauranga.example/community/grants-and-funding"
    rows = [
        row(1, funder="Tauranga City Council", title="Community Grant Fund", source_url=url,
            amount_max=None, deadline="rolling", deadline_state="rolling-confirmed",
            dedupe_key="a"),
        row(2, funder="Tauranga City Council", title="Creative Communities Scheme",
            source_url=url, amount_max=None, deadline="rolling",
            deadline_state="rolling-confirmed", dedupe_key="b"),
        row(3, funder="Tauranga City Council", title="Event Funding", source_url=url,
            amount_max=5000, dedupe_key="c"),
        row(4, funder="Tauranga City Council", title="Climate Action Fund", source_url=url,
            amount_max=10000, dedupe_key="d"),
    ]
    report = publish(rows)
    assert len(stored_ids()) == 4
    assert report["merges"] == []
    assert len(report["shared_url_groups"]) == 1
    assert len(report["shared_url_groups"][0]["rows"]) == 4


def test_dedupe_never_drops_a_published_id():
    # Two rows published separately; a later sweep finds a row that matches
    # both. Both published IDs survive; the refused merge is reported.
    publish([row(1, funder="F", title="Alpha Fund", dedupe_key="k1"),
             row(2, funder="F", title="Alpha Fund Two", dedupe_key="k2")])
    later = row(9, funder="F", title="Alpha Fund", dedupe_key="k2",
                verified_at="2026-10-05T00:00:00+00:00")
    report = publish([later], now=datetime(2026, 10, 6, tzinfo=timezone.utc))
    assert stored_ids() == ["OPP-NZ-2026-10-0001", "OPP-NZ-2026-10-0002"]
    assert report["refused_merges"]


def test_candidate_id_colliding_with_published_id_gets_fresh_id():
    # A second run in the same month renumbers rows from 0001.
    publish([row(1, funder="A", title="Alpha")])
    report = publish([row(1, funder="B", title="Beta", source_url="https://b.example",
                          dedupe_key="b")],
                     now=datetime(2026, 10, 6, tzinfo=timezone.utc))
    ids = stored_ids()
    assert len(ids) == 2 and "OPP-NZ-2026-10-0001" in ids
    beta = [r for r in published.load_published("nz", today=date(2026, 10, 6))
            if r["title"] == "Beta"][0]
    assert beta["id"] != "OPP-NZ-2026-10-0001"
    assert report["changes"]["new"] == [beta["id"]]


# --- Stability and cumulative retention ---------------------------------------------

def test_ids_and_first_seen_stable_across_three_publishes():
    first = row(1, title="Stable Fund", funder="Stable Trust", id="OPP-NZ-2026-06-0001",
                first_seen="2026-06-01T00:00:00+00:00", last_seen="2026-06-01T00:00:00+00:00",
                verified_at="2026-06-01T00:00:00+00:00",
                dedupe_key="stable|x|2026-11-30")
    publish([first], month="2026-06", now=datetime(2026, 6, 2, tzinfo=timezone.utc))

    # Next sweep: renumbered, newer first_seen, deadline moved so the key
    # changed; matched by title within funder.
    second = row(7, title="Stable Fund", funder="Stable Trust", id="OPP-NZ-2026-08-0007",
                 first_seen="2026-08-01T00:00:00+00:00", deadline="2027-02-28",
                 dedupe_key="stable|x|2027-02-28", verified_at="2026-08-02T00:00:00+00:00",
                 last_seen="2026-08-02T00:00:00+00:00")
    publish([second], month="2026-08", now=datetime(2026, 8, 3, tzinfo=timezone.utc))

    third = row(3, title="Stable Fund", funder="Stable Trust", id="OPP-NZ-2026-10-0003",
                first_seen="2026-10-01T00:00:00+00:00", deadline="2027-02-28",
                dedupe_key="stable|x|2027-02-28", verified_at="2026-10-02T00:00:00+00:00",
                last_seen="2026-10-02T00:00:00+00:00")
    publish([third], month="2026-10", now=datetime(2026, 10, 3, tzinfo=timezone.utc))

    rows = published.load_published("nz", today=date(2026, 10, 3))
    assert len(rows) == 1
    assert rows[0]["id"] == "OPP-NZ-2026-06-0001"
    assert rows[0]["first_seen"] == "2026-06-01T00:00:00+00:00"
    assert rows[0]["last_seen"] == "2026-10-02T00:00:00+00:00"
    assert rows[0]["verified_at"] == "2026-10-02T00:00:00+00:00"


def test_rows_absent_from_later_sweep_are_retained_with_last_known_facts():
    publish([row(1), row(2, amount_max=5000)])
    publish([row(1, amount_max=99999, verified_at="2026-10-05T00:00:00+00:00")],
            now=datetime(2026, 10, 6, tzinfo=timezone.utc))
    rows = {r["id"]: r for r in published.load_published("nz", today=date(2026, 10, 6))}
    assert set(rows) == {"OPP-NZ-2026-10-0001", "OPP-NZ-2026-10-0002"}
    assert rows["OPP-NZ-2026-10-0001"]["amount_max"] == 99999
    assert rows["OPP-NZ-2026-10-0002"]["amount_max"] == 5000


def test_unresolved_rematch_does_not_overwrite_published_facts():
    publish([row(1)])
    publish([row(1, deadline="TBC", deadline_state="unresolved", verified_at=None,
                 amount_max=1)],
            now=datetime(2026, 10, 6, tzinfo=timezone.utc))
    only = published.get_published_row("OPP-NZ-2026-10-0001", "nz", today=date(2026, 10, 6))
    assert only["deadline"] == "2026-11-30" and only["amount_max"] == 20000


# --- Status at read time ------------------------------------------------------------

def test_dated_row_closes_on_the_day_in_country_timezone():
    publish([row(1, deadline="2026-11-30")])
    # 23:30 on 30 Nov in Auckland (NZDT, UTC+13): still live.
    late_on_the_day = datetime(2026, 11, 30, 10, 30, tzinfo=timezone.utc)
    assert published.get_published_row("OPP-NZ-2026-10-0001", "nz",
                                        today=late_on_the_day)["status"] == "live"
    # 00:30 on 1 Dec in Auckland, still 30 Nov in UTC: closed.
    just_after = datetime(2026, 11, 30, 11, 30, tzinfo=timezone.utc)
    closed = published.get_published_row("OPP-NZ-2026-10-0001", "nz", today=just_after)
    assert closed["status"] == "closed"
    assert closed["closed_at"] == "2026-11-30T11:00:00+00:00"   # local midnight
    assert [r["id"] for r in published.load_published("nz", today=just_after)] == \
        ["OPP-NZ-2026-10-0001"]                                  # closed is listed
    assert published.load_published("nz", today=just_after, include=("live",)) == []


def test_rolling_row_goes_stale_after_expiry_and_leaves_lists():
    publish([row(1, deadline="rolling", deadline_state="rolling-confirmed",
                 verified_at="2026-10-01T12:00:00+00:00")])  # 2 Oct in Auckland
    on_last_day = date(2026, 12, 16)                          # 2 Oct + 75 days
    day_after = date(2026, 12, 17)
    assert published.load_published("nz", today=on_last_day)[0]["status"] == "live"
    assert published.load_published("nz", today=day_after) == []
    stale = published.get_published_row("OPP-NZ-2026-10-0001", "nz", today=day_after)
    assert stale["status"] == "stale"
    assert published.load_published("nz", today=day_after, include=("stale",))[0]["id"] == \
        "OPP-NZ-2026-10-0001"


def test_verify_found_closed_row_is_closed_with_closed_at():
    publish([row(1, deadline="rolling", deadline_state="closed")])
    r = published.get_published_row("OPP-NZ-2026-10-0001", "nz", today=date(2026, 10, 5))
    assert r["status"] == "closed" and r["closed_at"] == NOW.isoformat()


# --- Changelog ----------------------------------------------------------------

def test_changelog_new_changed_closed_newest_first():
    publish([row(1), row(2), row(3, deadline="2026-10-10")])
    later = datetime(2026, 10, 20, tzinfo=timezone.utc)   # row 3's deadline has passed
    publish([
        row(1, amount_max=50000, verified_at="2026-10-19T00:00:00+00:00"),
        # Verification-only update: new verified_at and last_seen, same facts.
        row(2, verified_at="2026-10-19T00:00:00+00:00", last_seen="2026-10-19T00:00:00+00:00"),
        row(4),
    ], now=later)
    changes = published.load_changes("nz")
    assert len(changes) == 2
    latest, first = changes
    assert first["new"] == ["OPP-NZ-2026-10-0001", "OPP-NZ-2026-10-0002", "OPP-NZ-2026-10-0003"]
    assert latest["published_at"] == later.isoformat()
    assert latest["new"] == ["OPP-NZ-2026-10-0004"]
    assert latest["changed"] == [{"id": "OPP-NZ-2026-10-0001", "fields": ["amount_max"]}]
    assert latest["closed"] == ["OPP-NZ-2026-10-0003"]


# --- Must-appear ----------------------------------------------------------------------

def test_must_appear_pass(monkeypatch):
    monkeypatch.setattr(sources, "must_appear_names", lambda c: ["Funder 1"])
    report = publish([row(1)])
    assert report["published"] and report["must_appear"]["missing"] == []


def test_must_appear_matches_through_aliases(monkeypatch):
    # "Sport NZ" in the manifest; the row says "Sport New Zealand (managed by
    # Sport Canterbury)", an alias in the NZ country config.
    monkeypatch.setattr(sources, "must_appear_names", lambda c: ["Sport NZ", "Te Puni Kōkiri"])
    report = publish([row(1, funder="Sport New Zealand (managed by Sport Canterbury)"),
                      row(2, funder="TPK")])
    assert report["must_appear"]["missing"] == []


def test_must_appear_failure_blocks_and_writes_nothing_public(monkeypatch):
    publish([row(1)])
    before = {n: open(os.path.join(published.published_dir("nz"), n)).read()
              for n in ("pool.json", "changes.json", "meta.json")}
    monkeypatch.setattr(sources, "must_appear_names", lambda c: ["Funder 1", "Lottery Grants Board"])
    notifier = FakeNotifier()
    with pytest.raises(published.PublishBlocked) as exc:
        publish([row(1, amount_max=1), row(5)], notifier=notifier,
                now=datetime(2026, 10, 6, tzinfo=timezone.utc))
    assert exc.value.missing == ["Lottery Grants Board"]
    after = {n: open(os.path.join(published.published_dir("nz"), n)).read() for n in before}
    assert after == before
    held = published.load_held("nz")
    assert held["published"] is False
    assert held["must_appear"]["missing"] == ["Lottery Grants Board"]
    assert notifier.calls and notifier.calls[0][2] == ["Lottery Grants Board"]


def test_unverified_row_does_not_satisfy_must_appear(monkeypatch):
    monkeypatch.setattr(sources, "must_appear_names", lambda c: ["Funder 1"])
    with pytest.raises(published.PublishBlocked):
        publish([row(1, verified_at=None)])


def test_force_publishes_and_records_who(monkeypatch):
    monkeypatch.setattr(sources, "must_appear_names", lambda c: ["Missing Funder"])
    notifier = FakeNotifier()
    report = publish([row(1)], force=True, forced_by="joe", notifier=notifier)
    assert report["published"] and report["forced"] and report["forced_by"] == "joe"
    assert report["must_appear"]["missing"] == ["Missing Funder"]
    meta = published.published_meta("nz")
    assert meta["forced"] is True and meta["forced_by"] == "joe"
    assert notifier.calls == []
    assert stored_ids() == ["OPP-NZ-2026-10-0001"]


def test_notifier_failure_does_not_mask_the_block(monkeypatch):
    monkeypatch.setattr(sources, "must_appear_names", lambda c: ["Nobody"])

    def broken(*a):
        raise RuntimeError("smtp down")
    with pytest.raises(published.PublishBlocked):
        publish([row(1)], notifier=broken)


def test_default_notifier_emails_only_with_both_env_vars(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(api.email, "send_text_email", lambda *a: sent.append(a))
    published.notify_director("nz", "2026-10", ["X"], "/tmp/held.json")
    assert sent == [] and "BLOCKED" in capsys.readouterr().out
    monkeypatch.setenv("DIRECTOR_EMAIL", "director@example.org")
    published.notify_director("nz", "2026-10", ["X"], "/tmp/held.json")
    assert sent == []
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    published.notify_director("nz", "2026-10", ["X"], "/tmp/held.json")
    assert len(sent) == 1 and sent[0][0] == "director@example.org"


def test_default_notifier_never_raises(monkeypatch):
    monkeypatch.setenv("DIRECTOR_EMAIL", "director@example.org")
    monkeypatch.setenv("RESEND_API_KEY", "test-key")

    def boom(*a):
        raise RuntimeError("resend down")
    monkeypatch.setattr(api.email, "send_text_email", boom)
    published.notify_director("nz", "2026-10", ["X"], "/tmp/held.json")


# --- Atomicity ----------------------------------------------------------------

def test_failed_write_leaves_previous_dataset_intact(monkeypatch):
    publish([row(1)])
    d = published.published_dir("nz")
    before = {n: open(os.path.join(d, n)).read() for n in ("pool.json", "changes.json", "meta.json")}

    real_mkstemp = published.tempfile.mkstemp
    calls = {"n": 0}

    def failing_mkstemp(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:      # pool and changes staged, meta fails
            raise OSError("disk full")
        return real_mkstemp(*a, **kw)
    monkeypatch.setattr(published.tempfile, "mkstemp", failing_mkstemp)

    with pytest.raises(OSError):
        publish([row(1, amount_max=1), row(2)], now=datetime(2026, 10, 6, tzinfo=timezone.utc))
    after = {n: open(os.path.join(d, n)).read() for n in before}
    assert after == before
    assert [f for f in os.listdir(d) if f.startswith(".tmp-")] == []


# --- Meta, entities, CLI ----------------------------------------------------------

def test_meta_after_publish():
    publish([row(1), row(2, funder="Funder 1"), row(3, deadline="TBC",
                                                     deadline_state="unresolved",
                                                     verified_at=None)],
            run_ts="20261005T000000")
    meta = published.published_meta("nz")
    assert meta["sweep_month"] == "2026-10"
    assert meta["run_ts"] == "20261005T000000"
    assert meta["next_sweep_due"] == "2026-12"
    assert meta["held_count"] == 1
    assert meta["funder_count"] == 1


def test_eligible_entities_empty_when_parser_unavailable(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_fit(name, *a, **kw):
        if name == "api.fit":
            raise ImportError("not yet")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_fit)
    publish([row(1)])
    assert published.load_published("nz", today=date(2026, 10, 5))[0]["eligible_entities"] == []


def test_cli_status_and_publish_holds_unverified_rows(isolated, capsys):
    run = os.path.join(tenant.platform_cycles_dir("nz", "2026-10"), "run-x")
    os.makedirs(run)
    os.symlink("run-x", os.path.join(tenant.platform_cycles_dir("nz", "2026-10"), "latest"))
    legacy = {k: v for k, v in row(1).items()
              if k not in ("deadline_state", "verified_at", "verified_by", "source_excerpt",
                           "provenance")}
    with open(os.path.join(run, "opportunities.json"), "w") as f:
        json.dump({"opportunities": [legacy]}, f)

    assert published.main(["publish", "nz", "2026-10"]) == 0
    out = capsys.readouterr().out
    assert "1 rows have no verification fields" in out
    assert published.load_published("nz") == []
    assert published.load_held("nz")["counts"]["held"] == 1

    assert published.main(["status", "nz"]) == 0
    assert json.loads(capsys.readouterr().out)["held_count"] == 1
    assert published.main(["bogus"]) == 2
