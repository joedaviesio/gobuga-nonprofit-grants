"""scripts/build_register.py: the per-country register settings, the prompt
sent for each category, and the rules that pick a funding page.

Pure-Python. Nothing is fetched and no model is called.
"""

import json
import os
import shutil

import pytest

import api.tenant as tenant
from api.country_config import clear_config_cache, get_country_config
from orchestrator import register_sweep as rs
from scripts import build_register as br

NZ_REGIONS = ("northland, auckland, waikato, bay-of-plenty, gisborne, hawkes-bay, taranaki, "
              "manawatu-whanganui, wellington, tasman, nelson, marlborough, west-coast, "
              "canterbury, otago, southland, national")

# The prompt as it was when the categories lived in the script. NZ must be
# asked exactly this, so a rebuilt NZ register is comparable with the last one.
NZ_GOVERNMENT_PROMPT = (
    "List funders in New Zealand of this kind: central government departments, ministries "
    "and Crown entities that run contestable funds open to community organisations, iwi, "
    "schools or clubs (list each funder once, not each fund).\n"
    "\n"
    "Be complete: list every funder of this kind that you are confident exists and gives "
    "grants an organisation or person can apply for. The name is what matters. Never invent "
    "a web address: give null for any address you are not sure of, and it will be looked up.\n"
    "\n"
    "Answer with one JSON object and nothing else:\n"
    '{"funders": [{"name": "official name", "website": "https://... or null", "funding_url": '
    '"https://... the page about applying for funding, or null if unsure", "regions": '
    '["slugs"], "tier": 1 or 2}]}\n'
    "\n"
    f"regions: slugs from this list only: {NZ_REGIONS}. Use [\"national\"] for a funder open "
    "nationwide.\n"
    "tier 1: a funder that distributes more than about NZD 5 million a year, or is the main "
    "funder for its region. tier 2: everyone else.")

NZ_CATEGORIES = {
    "government": "central government departments, ministries and Crown entities that run "
                  "contestable funds open to community organisations, iwi, schools or clubs "
                  "(list each funder once, not each fund)",
    "council": "every territorial authority and regional council: all 67 city and district "
               "councils and all 11 regional councils",
    "community-trust": "the 12 community trusts formed from the trust banks",
    "community-foundation": "community foundations (members of Community Foundations of "
                            "Aotearoa New Zealand)",
    "energy-trust": "energy, power and lines consumer trusts that make community grants",
    "gaming": "class 4 gaming societies that distribute grants to the community",
    "sports-trust": "regional sports trusts, and national sport funders",
    "licensing-trust": "licensing trusts and their charitable foundations",
    "philanthropic": "private, family and statutory philanthropic trusts and foundations that "
                     "accept applications, including funds administered by Perpetual Guardian "
                     "and Public Trust (list the fund, with the administrator's site if it has "
                     "no site of its own)",
    "corporate": "corporate foundations and company community-grant programmes",
    "maori": "iwi, rūnanga, Māori trusts and Māori-focused funders that make grants",
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "PLATFORM_CONFIG_DIR", str(tmp_path / "config"))
    sources = tmp_path / "config" / "sources"
    sources.mkdir(parents=True)
    for country in ("nz", "md"):
        shutil.copy(os.path.join(tenant.PROJECT_ROOT, "platform", "sources", f"{country}.json"),
                    sources / f"{country}.json")
    clear_config_cache()
    yield sources
    clear_config_cache()


def test_nz_categories_are_unchanged():
    assert br.register_settings("nz")["categories"] == NZ_CATEGORIES


def test_nz_prompt_is_unchanged():
    settings = br.register_settings("nz")
    assert br.list_prompt(settings, "government", get_country_config("nz").regions) \
        == NZ_GOVERNMENT_PROMPT


def test_nz_site_search_names_the_country():
    settings = br.register_settings("nz")
    assert settings["country"] == "New Zealand"
    assert br.PICK_PROMPT.format(country=settings["country"], name="X", results="").startswith(
        'Which of these search results is the official website of the New Zealand funder "X"?')
    assert settings["search_terms"] == "grants funding how to apply"


def test_nz_grant_words_are_the_sweeps_own():
    assert br.register_settings("nz")["grant_words"].pattern == rs.GRANT_WORDS.pattern
    assert br.register_settings("nz")["skip_link"] is rs.SKIP_LINK


def test_md_categories_load():
    settings = br.register_settings("md")
    assert settings["country"] == "Moldova"
    assert set(settings["categories"]) == {"international", "embassy", "foundation", "government",
                                           "council", "cross-border", "corporate"}
    assert "MDL" in settings["tier_one"]
    prompt = br.list_prompt(settings, "council", ["chisinau", "national"])
    assert prompt.startswith("List funders in Moldova of this kind: municipalities")
    assert "NZD" not in prompt and "New Zealand" not in prompt


def test_a_country_without_categories_stops_before_spending(isolated):
    data = json.loads((isolated / "md.json").read_text())
    del data["register"]["categories"]
    (isolated / "md.json").write_text(json.dumps(data))
    clear_config_cache()
    with pytest.raises(SystemExit, match="no register categories for 'md'"):
        br.register_settings("md")
    # The check comes first in main, ahead of the dry run and any call.
    with pytest.raises(SystemExit, match="no register categories"):
        br.main(["md"])


def test_a_country_with_no_register_is_not_given_nzs(isolated):
    (isolated / "xx.json").write_text(json.dumps({"country": "xx", "regions": ["national"]}))
    with pytest.raises(SystemExit, match="categories, tier_one, grant_words, search_terms"):
        br.register_settings("xx")


def test_an_unknown_category_is_refused():
    with pytest.raises(SystemExit):
        br.main(["md", "--only-category", "gaming"])


def test_md_links_are_chosen_by_romanian_and_russian_words():
    settings = br.register_settings("md")
    page = {"links": [
        ("https://fond.example/despre-noi", "Despre noi"),
        ("https://fond.example/noutati/granturi-acordate", "Granturi acordate"),
        ("https://fond.example/cariera/concurs-post", "Concurs pentru ocuparea postului"),
        ("https://fond.example/apeluri", "Apel de propuneri"),
        ("https://fond.example/ru/programmy", "Гранты и финансирование"),
    ]}
    picked = rs.pick_links(page, "https://fond.example/", set(), settings["grant_words"],
                           settings["skip_link"])
    assert picked == ["https://fond.example/apeluri", "https://fond.example/ru/programmy"]
    # The English rules alone miss the call and follow the news story.
    assert rs.pick_links(page, "https://fond.example/", set()) == [
        "https://fond.example/noutati/granturi-acordate"]


def test_md_grant_words_read_both_forms_of_the_romanian_letters():
    words = br.register_settings("md")["grant_words"]
    for text in ("Finanțare pentru ONG-uri", "Finanţare pentru ONG-uri", "Finantare",
                 "Concurs de granturi", "Конкурс грантов", "Call for proposals"):
        assert words.search(text), text


@pytest.mark.parametrize("name, text", [
    ("Fundația Est-Europeană", "Despre Fundaţia Est-Europeana"),
    ("Primăria Bălți", "PRIMARIA MUNICIPIULUI BALTI"),
    ("Фонд Сорос-Молдова", "Фонд Сорос-Молдова поддерживает"),
])
def test_md_names_are_found_whatever_the_diacritics(name, text):
    stop = br.register_settings("md")["name_stopwords"]
    assert br.names_funder({"text": text}, name, stop)


def test_a_page_that_does_not_name_the_funder_is_not_its_site():
    stop = br.register_settings("md")["name_stopwords"]
    assert not br.names_funder({"text": "Primăria Cahul"}, "Primăria Bălți", stop)
    assert not br.names_funder({"text": "Toi Foundation"}, "Rātā Foundation",
                               br.register_settings("nz")["name_stopwords"])


def test_md_lists_no_longer_carry_usaid():
    data = json.loads(open(os.path.join(tenant.PROJECT_ROOT, "platform", "sources", "md.json"),
                           encoding="utf-8").read())
    entries = data["must_appear_funders"] + data["seed_sources"]
    assert not [e for e in entries if "usaid" in (e["name"] + e["url"]).lower()]
