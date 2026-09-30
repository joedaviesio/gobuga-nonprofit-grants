"""New Zealand's register sweep must not change while other countries are added.

The golden files in tests/golden/register_sweep_nz/ were written by this
module, with GOLDEN_WRITE=1, from the sweep as it was before Moldova's
country settings (commit d98ec03). Each test runs today's code on the same
fixed pages and model answers and requires the same bytes:

  verified.json, rejected.json   a whole NZ run
  system_prompt.txt              the prompt NZ's model is sent
  title_keys.json                the dedupe key, the server's normalised
                                 title and the sweep's dedupe survivors for
                                 every NZ title in the local runs of April
                                 and September 2026 (the published pool is
                                 not in the repo; titles_input.json is the
                                 titles those runs hold)

To regenerate, which should only be done from code known to be right for NZ:
    GOLDEN_WRITE=1 STARTUP_SWEEP_DISABLED=1 python -m pytest tests/test_register_sweep_nz_golden.py
"""

import json
import os
import shutil
from datetime import datetime, timezone

import pytest

import api.tenant as tenant
from api.country_config import clear_config_cache, get_country_config
from api.published import normalise_title
from orchestrator import register_sweep as rs

HERE = os.path.join(os.path.dirname(__file__), "golden", "register_sweep_nz")
WRITE = os.getenv("GOLDEN_WRITE") == "1"
NOW = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)

FILLER = "More about our work in the community. " * 12

PAGES = {
    "https://rata.example/grants": (
        "Our grants\nThe Rātā Foundation funds community groups across Canterbury.\n"
        "Community Grants support local groups. Applications close 30 November 2026 at 5pm. "
        "Grants of up to $20,000 are available. Open to incorporated societies and charitable "
        "trusts in Canterbury.\n"
        "Kōkiri Fund\nThe Kōkiri Fund backs kaupapa Māori projects. Applications are welcome at "
        "any time of the year. Grants of $5,000 to $15,000 are offered to Māori-led groups.\n"
        "Community Loans are also offered to groups buying premises at low interest.\n"
        + FILLER,
        [("https://rata.example/grants/heritage", "Heritage Fund"),
         ("https://rata.example/news/grant-recipients", "Recent grant recipients"),
         ("https://rata.example/about", "About us"),
         ("https://rata.example/apply", "How to apply for funding")]),
    "https://rata.example/grants/heritage": (
        "Heritage Fund\nThe Heritage Fund helps restore buildings of local importance. "
        "Applications for this round are now closed. Grants of $1,500 to $2.5 million have "
        "been made. Open to owners of listed buildings.\n" + FILLER, []),
    "https://rata.example/apply": (
        "How to apply for funding\nRead the guidelines, then apply online. Community Grants "
        "support local groups. Open to incorporated societies and charitable trusts in "
        "Canterbury. Grants of up to $20k are available.\n" + FILLER, []),
    "https://www.tpk.example/funding": (
        "Te Puni Kōkiri funding\nWhānau Development Fund\nThe Whānau Development Fund "
        "supported whānau enterprise. The 2025 round closed on 30 June 2026. "
        "Apply for funding from Te Puni Kōkiri: we fund iwi, hapū and Māori organisations "
        "working for whānau wellbeing.\n" + FILLER, []),
    "https://youth.example/": (
        "Youth Grants\nYouth Grants fund projects led by young people. Open to youth groups "
        "and schools in Otago. The fund total is $40,000,000 over ten years.\n" + FILLER,
        [("https://youth.example/youth-grants", "Youth grants")]),
    "https://youth.example/youth-grants": (
        "Youth Grants\nYouth Grants fund projects led by young people. Applications close "
        "15 March 2027. Open to youth groups and schools in Otago. Up to $3,000 each.\n"
        + FILLER, []),
}
REGISTER = [
    {"name": "Rātā Foundation", "url": "https://rata.example/grants", "tier": 1,
     "category": "community-trust", "regions": ["canterbury"]},
    {"name": "Te Puni Kōkiri", "url": "https://www.tpk.example/funding", "tier": 1,
     "category": "government", "regions": ["national"]},
    {"name": "Otago Youth Trust", "url": "https://youth.example/", "tier": 2,
     "category": "philanthropic", "regions": ["otago"]},
    {"name": "Silent Trust", "url": "https://silent.example/", "tier": 2,
     "category": "philanthropic", "regions": []},
]


def _p(**kw):
    base = {"deadline_state": "unknown", "deadline": None, "deadline_excerpt": None,
            "amount_min": None, "amount_max": None, "amount_excerpt": None,
            "eligibility": None, "eligibility_excerpt": None, "regions": [], "tags": [],
            "summary": ""}
    base.update(kw)
    return base


ANSWERS = {
    "https://rata.example/grants": [
        _p(title="Community Grants", deadline_state="dated", deadline="2026-11-30",
           deadline_excerpt="Applications close 30 November 2026 at 5pm.",
           amount_max=20000, amount_excerpt="Grants of up to $20,000 are available.",
           eligibility="Incorporated societies and charitable trusts in Canterbury.",
           eligibility_excerpt="Open to incorporated societies and charitable trusts in Canterbury.",
           regions=["canterbury"], tags=["community", "made-up"], summary="Local grants."),
        _p(title="Kōkiri Fund", deadline_state="rolling",
           deadline_excerpt="Applications are welcome at any time of the year.",
           amount_min=5000, amount_max=15000,
           amount_excerpt="Grants of $5,000 to $15,000 are offered to Māori-led groups.",
           eligibility="Māori-led groups.", regions=["national", "atlantis"],
           tags=["maori", "community"], summary="Kaupapa Māori projects."),
        _p(title="Community Loans", deadline_state="unknown",
           eligibility_excerpt="Community Loans are also offered to groups buying premises at low interest."),
        _p(title="Invented Fund", deadline_state="unknown",
           eligibility_excerpt="Open to incorporated societies and charitable trusts in Canterbury."),
    ],
    "https://rata.example/grants/heritage": [
        _p(title="Heritage Fund", deadline_state="closed", deadline=None,
           deadline_excerpt="Applications for this round are now closed.",
           amount_min=1500, amount_max=2500000,
           amount_excerpt="Grants of $1,500 to $2.5 million have been made.",
           eligibility_excerpt="Open to owners of listed buildings.", tags=["arts"]),
    ],
    "https://rata.example/apply": [
        _p(title="Community Grants", deadline_state="unknown",
           amount_max=20000, amount_excerpt="Grants of up to $20k are available.",
           eligibility_excerpt="Open to incorporated societies and charitable trusts in Canterbury."),
    ],
    "https://www.tpk.example/funding": [
        _p(title="Whānau Development Fund", deadline_state="closed", deadline="2026-06-30",
           deadline_excerpt="The 2025 round closed on 30 June 2026.", tags=["maori"]),
        _p(title="GENERAL", deadline_state="unknown",
           eligibility_excerpt="we fund iwi, hapū and Māori organisations working for whānau wellbeing.",
           summary="Te Puni Kōkiri funds Māori organisations."),
        _p(title="Whānau Fund", deadline_state="dated", deadline="2026-12-01",
           deadline_excerpt="Applications close 1 December 2026."),
    ],
    "https://youth.example/": [
        _p(title="Youth Grants", deadline_state="unknown",
           amount_max=40000000, amount_excerpt="The fund total is $40,000,000 over ten years.",
           eligibility_excerpt="Open to youth groups and schools in Otago."),
    ],
    "https://youth.example/youth-grants": [
        _p(title="Youth Grants", deadline_state="dated", deadline="2027-03-15",
           deadline_excerpt="Applications close 15 March 2027.",
           amount_max=3000, amount_excerpt="Up to $3,000 each.",
           eligibility_excerpt="Open to youth groups and schools in Otago.",
           regions=["otago"], tags=["youth"]),
    ],
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setattr(tenant, "PLATFORM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setattr(rs, "PER_HOST_DELAY_S", 0)
    sources = tmp_path / "config" / "sources"
    sources.mkdir(parents=True)
    shutil.copy(os.path.join(tenant.PROJECT_ROOT, "platform", "sources", "nz.json"),
                sources / "nz.json")
    (sources / "nz-register.json").write_text(json.dumps({"funders": REGISTER}))
    clear_config_cache()
    yield tmp_path
    clear_config_cache()


def fetch(url):
    if "silent" in url:
        return {"url": url, "error": "ConnectError: refused"}
    if url not in PAGES:
        return {"url": url, "error": "HTTP 404"}
    text, links = PAGES[url]
    return {"url": url, "text": text, "links": links}


def _golden(name: str, data: str) -> None:
    path = os.path.join(HERE, name)
    if WRITE:
        os.makedirs(HERE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
    with open(path, encoding="utf-8") as f:
        assert data == f.read(), f"{name} differs from the NZ golden copy"


def test_an_nz_run_is_byte_identical():
    systems = []

    def ask(system, user):
        systems.append(system)
        url = user.split("Page address: ", 1)[1].split("\n", 1)[0]
        return json.dumps({"programmes": ANSWERS.get(url, [])}), 2000, 300
    report = rs.run("nz", budget_usd=1.0, fetch=fetch, ask=ask, now=NOW)
    with open(os.path.join(report["run_dir"], "verified.json"), encoding="utf-8") as f:
        _golden("verified.json", f.read())
    with open(os.path.join(report["run_dir"], "rejected.json"), encoding="utf-8") as f:
        _golden("rejected.json", f.read())
    assert len(set(systems)) == 1
    _golden("system_prompt.txt", systems[0])
    # The fixture exercises what it claims to.
    rows = json.load(open(os.path.join(report["run_dir"], "verified.json")))["opportunities"]
    assert {r["deadline_state"] for r in rows} >= {"dated", "rolling-confirmed", "closed", "not-stated"}
    assert any("Kōkiri" in r["title"] for r in rows)
    assert report["rejected_reasons"]


def test_nz_title_keys_are_unchanged():
    """Keys over every NZ title found in the local runs, by the production path:
    check_programme builds the dedupe_key, dedupe_rows decides which rows
    survive, normalise_title is the server's."""
    with open(os.path.join(HERE, "titles_input.json"), encoding="utf-8") as f:
        titles = json.load(f)
    cfg = get_country_config("nz")
    today = NOW.date()
    out, rows = [], []
    for t in titles:
        title, funder = t["title"], t["funder"] or "Unnamed Funder"
        page = {"url": "https://example.org/p",
                "text": f"{title}\nOpen to registered charities in the region who apply.\n"}
        item = {"title": title, "deadline_state": "unknown",
                "eligibility_excerpt": "Open to registered charities in the region who apply."}
        row, why = rs.check_programme(item, page, {"name": funder, "tier": 2, "category": "other",
                                                   "regions": []}, cfg, today, NOW.isoformat())
        out.append({"title": title, "normalised": normalise_title(title),
                    "dedupe_key": row["dedupe_key"] if row else None, "rejected": why})
        if row:
            rows.append(row)
    survivors = sorted(f"{r['funder']}|{r['title']}" for r in rs.dedupe_rows(rows))
    _golden("title_keys.json", json.dumps({"titles": out, "dedupe_survivors": survivors},
                                          ensure_ascii=False, indent=1) + "\n")
