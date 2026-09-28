"""Register sweep: crawl by rule, extract, and the checks that decide what is kept.

Pure-Python. The fetch and the model are fakes; nothing leaves the machine.
"""

import json
from datetime import datetime, timezone

import pytest

import api.tenant as tenant
from api import published, sources
from api.country_config import clear_config_cache, get_country_config
from orchestrator import register_sweep as rs

NOW = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
FUNDER = {"name": "Example Trust", "url": "https://trust.example/funding", "tier": 1,
          "category": "community-trust", "regions": ["canterbury"]}
PAGE_TEXT = (
    "Community Grants\nOur Community Grants support local groups. Applications close "
    "30 November 2026 at 5pm. Grants of up to $20,000 are available. Open to incorporated "
    "societies and charitable trusts in Canterbury.\n" + "More about our work. " * 30)
PAGE = {"url": "https://trust.example/funding/community", "text": PAGE_TEXT}


def item(**kw):
    base = {"title": "Community Grants", "deadline_state": "dated", "deadline": "2026-11-30",
            "deadline_excerpt": "Applications close 30 November 2026 at 5pm.",
            "amount_min": None, "amount_max": 20000,
            "amount_excerpt": "Grants of up to $20,000 are available.",
            "eligibility": "Incorporated societies and charitable trusts in Canterbury.",
            "eligibility_excerpt": "Open to incorporated societies and charitable trusts in Canterbury.",
            "regions": ["canterbury"], "tags": ["community", "made-up"], "summary": "Local grants."}
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setattr(tenant, "PLATFORM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setattr(rs, "PER_HOST_DELAY_S", 0)
    (tmp_path / "config" / "sources").mkdir(parents=True)
    import shutil, os
    real = os.path.join(tenant.PROJECT_ROOT, "platform", "sources", "nz.json")
    shutil.copy(real, tmp_path / "config" / "sources" / "nz.json")
    clear_config_cache()
    yield tmp_path
    clear_config_cache()


def check(**kw):
    cfg = get_country_config("nz")
    return rs.check_programme(item(**kw), PAGE, FUNDER, cfg, TODAY, NOW.isoformat())


def test_a_supported_programme_becomes_a_verified_row():
    row, reason = check()
    assert reason == ""
    assert row["deadline"] == "2026-11-30" and row["deadline_state"] == "dated"
    assert row["amount_max"] == 20000 and row["verified_at"] == NOW.isoformat()
    assert row["tags"] == ["community"]          # the made-up tag is dropped
    assert set(row["provenance"]) == {"deadline", "amount", "eligibility"}
    assert row["source_url"] == PAGE["url"]


@pytest.mark.parametrize("change, reason", [
    ({"title": "Rangatahi Innovation Prize"}, "title not found"),
    ({"deadline": "2026-11-15"}, "does not state the deadline date"),
    ({"deadline": "2027-11-30"}, "different year"),
    ({"deadline_state": "closed", "deadline": None, "deadline_excerpt": "This round is closed."},
     "excerpt not found"),
    ({"deadline_state": "unknown", "deadline_excerpt": None, "eligibility_excerpt": "Made up.",
      "amount_excerpt": None}, "nothing on the page supports"),
])
def test_what_the_page_does_not_support_is_rejected(change, reason):
    row, why = check(**change)
    assert row is None and reason in why


@pytest.mark.parametrize("change", [
    {"amount_max": 50000},                                   # not the figure on the page
    {"amount_excerpt": "Grants of up to $50,000 are available."},   # not on the page
    {"amount_max": 40_000_000},                              # a fund total
    {"amount_min": 30000, "amount_max": 20000},
])
def test_an_unsupported_amount_is_dropped_but_the_row_is_kept(change):
    row, _ = check(**change)
    assert row is not None and row["amount_min"] is None and row["amount_max"] is None
    assert "amount" not in row["provenance"]


def test_a_past_date_is_closed_and_rolling_needs_its_words():
    page = {"url": PAGE["url"], "text": PAGE_TEXT.replace("30 November 2026", "30 June 2026")}
    cfg = get_country_config("nz")
    row, _ = rs.check_programme(
        item(deadline="2026-06-30", deadline_excerpt="Applications close 30 June 2026 at 5pm."),
        page, FUNDER, cfg, TODAY, NOW.isoformat())
    assert row["deadline_state"] == "closed"
    # "Rolling" with words the page does not carry is not rolling: no date is claimed.
    row, _ = check(deadline_state="rolling", deadline=None,
                   deadline_excerpt="Applications are accepted at any time.")
    assert row["deadline_state"] == "not-stated" and row["deadline"] == "TBC"


def test_a_programme_with_no_date_is_kept_as_not_stated():
    row, reason = check(deadline_state="unknown", deadline=None, deadline_excerpt=None)
    assert reason == "" and row["deadline_state"] == "not-stated"
    assert "deadline" not in row["provenance"] and "eligibility" in row["provenance"]
    assert row["source_excerpt"].startswith("Open to incorporated societies")
    # An invented date is dropped, not published.
    row, _ = check(deadline="2026-12-25", deadline_excerpt="Applications close 25 December 2026.")
    assert row["deadline_state"] == "not-stated" and row["deadline"] == "TBC"


def test_not_stated_rows_publish_and_go_stale(isolated):
    from datetime import timedelta
    from api import published as pub
    row, _ = check(deadline_state="unknown", deadline=None, deadline_excerpt=None)
    row["id"] = "OPP-NZ-2026-09-0001"
    pub.publish_pool("nz", "2026-09", [row], now=NOW, force=True, notifier=lambda *a: None)
    assert [r["status"] for r in pub.load_published("nz", today=NOW)] == ["live"]
    later = NOW + timedelta(days=80)
    assert pub.load_published("nz", today=later) == []
    assert pub.get_published_row(row["id"], "nz", today=later)["status"] == "stale"


def test_title_words_may_be_split_across_the_page():
    assert rs.title_on_page("Community Grants", rs._norm(PAGE_TEXT))
    assert rs.title_on_page("Local Community Grants Support", rs._norm(PAGE_TEXT))
    assert not rs.title_on_page("Marine Science Scholarship", rs._norm(PAGE_TEXT))


def test_links_are_chosen_by_rule():
    page = {"links": [
        ("https://trust.example/funding/community-grants", "Community grants"),
        ("https://trust.example/news/grant-recipients-announced", "Read the news"),
        ("https://trust.example/about", "About us"),
        ("https://other.example/grants", "Grants elsewhere"),
        ("https://trust.example/apply", "How to apply for funding"),
        ("https://trust.example/files/form.docx", "Application form"),
        ("mailto:grants@trust.example", "Email the grants team"),
    ]}
    picked = rs.pick_links(page, "https://www.trust.example/", set())
    assert picked == ["https://trust.example/apply", "https://trust.example/funding/community-grants"]


def test_crawl_stops_at_the_page_cap_and_skips_pages_without_grant_words():
    pages = {"https://trust.example/funding": {
        "url": "https://trust.example/funding", "text": PAGE_TEXT,
        "links": [(f"https://trust.example/grants/{n}", "Grant programme") for n in range(30)]}}

    def fetch(url):
        if url in pages:
            return pages[url]
        n = int(url.rsplit("/", 1)[1])
        text = "Nothing to see. " * 40 if n % 2 else PAGE_TEXT
        return {"url": url, "text": text, "links": []}
    out = rs.crawl_funder(FUNDER, fetch)
    assert len(out["pages"]) == rs.MAX_PAGES_PER_FUNDER
    assert all(rs.GRANT_WORDS.search(p["text"]) for p in out["pages"])


def write_register(tmp_path, funders):
    path = tmp_path / "config" / "sources" / "nz-register.json"
    path.write_text(json.dumps({"funders": funders}))


def test_run_end_to_end_with_budget_and_publish(isolated):
    write_register(isolated, [FUNDER, {**FUNDER, "name": "Silent Trust", "url": "https://silent.example/"}])

    def fetch(url):
        if "silent" in url:
            return {"url": url, "error": "ConnectError: refused"}
        return {"url": url, "text": PAGE_TEXT, "links": []}

    def ask(system, user):
        return json.dumps({"programmes": [item(), item(title="Invented Fund")]}), 3000, 400
    report = rs.run("nz", budget_usd=1.0, publish=True, fetch=fetch, ask=ask, now=NOW)
    assert report["rows"] == 1 and report["rejected_reasons"] == {"title not found on page": 1}
    assert report["funders_with_no_page"] == ["Silent Trust"]
    assert report["coverage_by_tier"]["1"]["missing"] == ["Silent Trust"]
    assert report["budget"]["spent_usd"] == pytest.approx(0.005)
    # Tier 1 of the register is the must-appear list, so the publish is blocked.
    assert report["publish"] == {"published": False, "missing": ["Silent Trust"]}
    assert published.load_published("nz") == []

    report = rs.run("nz", budget_usd=1.0, publish=True, force=True, forced_by="test",
                    fetch=fetch, ask=ask, now=NOW)
    assert report["publish"]["published"] is True
    rows = published.load_published("nz", today=NOW)
    assert [r["title"] for r in rows] == ["Community Grants"] and rows[0]["status"] == "live"


def test_budget_stops_new_model_calls(isolated):
    write_register(isolated, [{**FUNDER, "name": f"Trust {n}", "url": f"https://t{n}.example/"}
                              for n in range(12)])
    calls = []

    def ask(system, user):
        calls.append(1)
        return json.dumps({"programmes": []}), 400_000, 0     # $0.40 a call
    report = rs.run("nz", budget_usd=1.0, fetch=lambda u: {"url": u, "text": PAGE_TEXT, "links": []},
                    ask=ask, now=NOW)
    assert report["pages_skipped_for_budget"] > 0
    assert len(calls) + report["pages_skipped_for_budget"] == 12
    assert report["budget"]["spent_usd"] < 1.0 + 0.4 * rs.EXTRACT_CONCURRENCY


def test_must_appear_falls_back_to_the_manifest_without_a_register(isolated):
    assert "Sport NZ" in sources.must_appear_names("nz")
    write_register(isolated, [FUNDER, {**FUNDER, "name": "Small Trust", "tier": 2}])
    assert sources.must_appear_names("nz") == ["Example Trust"]


def test_a_funders_unnamed_scheme_is_kept_under_the_funders_name():
    row, _ = check(title="GENERAL")
    assert row["title"] == "Example Trust grants"
    row, why = check(title="GENERAL", eligibility_excerpt="Anyone at all may apply, honest.")
    assert row is None and "nothing on the page supports" in why


def test_main_content_drops_the_menus():
    html = ("<html><body><nav><a href='/a'>Heritage Fund</a> <a href='/b'>Bursary</a></nav>"
            "<main><h1>Community Grants</h1><p>" + "Support for local groups. " * 40 + "</p></main>"
            "<footer>Contact us about funding</footer></body></html>")
    text = rs.html_to_text(html)
    assert "Community Grants" in text and "Heritage Fund" not in text and "Contact us" not in text
    # A page with no <main> keeps its body and still loses the menus.
    text = rs.html_to_text("<body><nav>Menu Fund</nav><div>Apply for a grant here.</div></body>")
    assert "Apply for a grant" in text and "Menu Fund" not in text


def test_lists_of_past_recipients_are_not_followed():
    page = {"links": [("https://trust.example/grant-recipient-listings", "Grant recipients"),
                      ("https://trust.example/files/ApprovedGrantsFY2023.pdf", "Approved grants"),
                      ("https://trust.example/grants-policy", "Grants policy")]}
    assert rs.pick_links(page, "https://trust.example/", set()) == ["https://trust.example/grants-policy"]
