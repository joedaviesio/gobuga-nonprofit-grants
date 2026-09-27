"""The sweep's verify + publish wiring, the startup hook default, and the
`--confirm` guard on the sweep script.

Every agent, fetch and model call is replaced with a fake; nothing here can
spend money or touch the network.
"""

import importlib.util
import json
import os
from datetime import datetime, timezone

import pytest

import api.email  # noqa: F401  — load_dotenv() on import; see tests/test_published.py
from api import published, sources, startup_sweep, tenant
from orchestrator import sweep


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.delenv("DIRECTOR_EMAIL", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    return tmp_path


def _row(n, **kw):
    base = {
        "id": f"OPP-NZ-2026-10-{n:04d}", "country": "nz", "title": f"Programme {n}",
        "funder": f"Funder {n}", "deadline": "TBC", "amount_min": None, "amount_max": 1000,
        "currency": "NZD", "region": [], "tags": ["community"], "eligibility": "",
        "summary": "", "source_url": f"https://f{n}.example/", "evidence_ids": [],
        "first_seen": "2026-10-01T00:00:00+00:00", "last_seen": "2026-10-01T00:00:00+00:00",
        "dedupe_key": f"funder-{n}|f{n}.example|tbc",
    }
    base.update(kw)
    return base


def _fake_verify(rows, **kw):
    """Row 1 verifies as dated; every other row stays unresolved."""
    out = []
    for r in rows:
        r = dict(r)
        if r["id"].endswith("0001"):
            r.update(deadline="2026-12-01", deadline_state="dated",
                     verified_at="2026-10-01T00:00:00+00:00", verified_by="verify-pass/fake",
                     source_excerpt="Closes 1 December 2026.", provenance={})
        else:
            r.update(deadline_state="unresolved", verified_at=None,
                     unresolved_reason="page gives no deadline")
        out.append(r)
    return out, {"rows": len(rows), "distinct_urls": len(rows), "fetches": len(rows),
                 "fetch_failures": 0, "dated": 1, "rolling_confirmed": 0, "closed": 0,
                 "unresolved": len(rows) - 1, "unresolved_reasons": {"page gives no deadline": 1},
                 "excerpt_rejected": 0, "api_calls": len(rows), "cost_usd": 0.0}


@pytest.fixture
def fake_sweep(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("real agent called")
    monkeypatch.setattr(sweep, "run_agent", lambda *a, **kw: {"raw_text": "fake"})
    monkeypatch.setattr(sweep, "load_sweep_evidence", lambda *a, **kw: [])
    monkeypatch.setattr(sweep, "build_opportunities_from_watchers",
                        lambda *a, **kw: [_row(1), _row(2)])
    monkeypatch.setattr(sweep, "resolve_deadlines_for_rows",
                        lambda rows, **kw: (rows, {"resolved_to_date": 0, "rolling_confirmed": 0,
                                                   "still_tbc": 0, "fetch_failures": 0,
                                                   "api_calls": 0, "cost_usd": 0.0}))
    monkeypatch.setattr(sweep, "verify_rows", _fake_verify)
    monkeypatch.setattr(sweep, "latest_available_month", lambda *a, **kw: None)


def _run(**kw):
    return sweep.run_country_sweep("nz", "2026-10", sectors_filter=["sport"], skip_urban=True, **kw)


def test_dry_run_calls_nothing_and_writes_nothing(isolated, monkeypatch):
    for name in ("run_agent", "verify_rows", "publish_pool", "resolve_deadlines_for_rows"):
        monkeypatch.setattr(sweep, name, lambda *a, **kw: pytest.fail("called on dry run"))
    result = sweep.run_country_sweep("nz", "2026-10", dry_run=True)
    assert result["dry_run"] is True
    assert not (isolated / "platform").exists()


def test_blocked_publish_keeps_run_files_and_promotes_latest(fake_sweep, monkeypatch):
    # Real NZ manifest: 13 must-appear funders, none present -> blocked.
    result = _run()
    assert result["publish"]["published"] is False
    assert "must-appear funders missing" in result["publish"]["reason"]

    run_dir = result["run_dir"]
    for name in ("opportunities.json", "verified.json", "coverage.json", "report.md"):
        assert os.path.exists(os.path.join(run_dir, name)), name
    latest = tenant.platform_latest_path("nz", "2026-10")
    assert os.path.islink(latest) and os.readlink(latest) == os.path.basename(run_dir)

    # The workspace pool is the pre-verify pool: no Phase 1 fields.
    pool = json.load(open(os.path.join(run_dir, "opportunities.json")))["opportunities"]
    assert [r["id"] for r in pool] == ["OPP-NZ-2026-10-0001", "OPP-NZ-2026-10-0002"]
    assert all("deadline_state" not in r for r in pool)

    coverage = json.load(open(os.path.join(run_dir, "coverage.json")))
    assert coverage["verify"]["dated"] == 1
    assert coverage["publish"]["published"] is False
    assert coverage["publish"]["held"][0]["reason"] == "page gives no deadline"

    report = open(os.path.join(run_dir, "report.md")).read()
    assert "NOT updated: must-appear funders missing" in report
    assert "Held rows (unresolved, not published)" in report
    assert "page gives no deadline" in report

    assert published.load_published("nz") == []          # public dataset untouched
    assert published.load_held("nz")["published"] is False


def test_successful_publish_is_reported(fake_sweep, monkeypatch):
    monkeypatch.setattr(sources, "must_appear_names", lambda c: ["Funder 1"])
    result = _run()
    assert result["publish"]["published"] is True
    assert result["publish"]["counts"]["new"] == 1
    ids = [r["id"] for r in published.load_published(
        "nz", today=datetime(2026, 10, 2, tzinfo=timezone.utc))]
    assert ids == ["OPP-NZ-2026-10-0001"]
    assert published.published_meta("nz")["run_ts"] == result["run_ts"]


def test_unexpected_publish_error_does_not_crash_the_sweep(fake_sweep, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(sweep, "publish_pool", broken)
    result = _run()
    assert result["publish"] == {"published": False, "reason": "publish failed: disk on fire"}
    assert os.path.islink(tenant.platform_latest_path("nz", "2026-10"))


def test_non_promoting_run_verifies_but_does_not_publish(fake_sweep, monkeypatch):
    monkeypatch.setattr(sweep, "publish_pool", lambda *a, **kw: pytest.fail("published"))
    result = _run(promote_latest=False)
    assert result["publish"]["published"] is False
    assert result["verify"]["dated"] == 1
    assert not os.path.exists(tenant.platform_latest_path("nz", "2026-10"))


# --- Startup hook ----------------------------------------------------------------------

@pytest.fixture
def dispatches(monkeypatch):
    calls = []
    monkeypatch.setattr(startup_sweep, "_dispatch_sweep", lambda c, m, **kw: calls.append((c, m)))
    monkeypatch.setattr(startup_sweep, "latest_available_month", lambda *a, **kw: None)
    monkeypatch.delenv("STARTUP_SWEEP_ENABLED", raising=False)
    monkeypatch.delenv("STARTUP_SWEEP_DISABLED", raising=False)
    return calls


def test_startup_sweep_off_by_default(dispatches):
    startup_sweep.maybe_seed_pool()
    assert dispatches == []


def test_startup_sweep_runs_only_when_enabled(dispatches, monkeypatch):
    monkeypatch.setenv("STARTUP_SWEEP_ENABLED", "1")
    startup_sweep.maybe_seed_pool()
    assert len(dispatches) == 1


def test_startup_sweep_disabled_overrides_enabled(dispatches, monkeypatch):
    monkeypatch.setenv("STARTUP_SWEEP_ENABLED", "1")
    monkeypatch.setenv("STARTUP_SWEEP_DISABLED", "1")
    startup_sweep.maybe_seed_pool()
    assert dispatches == []


# --- Sweep script guard -------------------------------------------------------------------

def _load_script():
    spec = importlib.util.spec_from_file_location(
        "run_monthly_sweep", os.path.join(ROOT, "scripts", "run_monthly_sweep.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sweep_script_refuses_without_confirm(monkeypatch, capsys):
    script = _load_script()
    monkeypatch.setattr(script, "run_country_sweep", lambda *a, **kw: pytest.fail("swept"))
    assert script.main([]) != 0
    out = capsys.readouterr().out
    assert "Would run a full paid sweep" in out and "--confirm" in out


def test_sweep_script_runs_with_confirm(monkeypatch):
    script = _load_script()
    calls = []
    monkeypatch.setattr(script, "run_country_sweep", lambda c, m: calls.append((c, m)))
    assert script.main(["--confirm"]) == 0
    assert len(calls) == len(script.COUNTRIES)
