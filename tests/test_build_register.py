"""scripts/build_register.py: the per-country register settings, the prompt
sent for each category, and the rules that pick a funding page.

Pure-Python. Nothing is fetched and no model is called.
"""

import json
import os
import shutil
import sys
from types import SimpleNamespace

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
    page = {"links": [
        ("https://fond.example/despre-noi", "Despre noi"),
        ("https://fond.example/noutati/granturi-acordate", "Granturi acordate"),
        ("https://fond.example/cariera/concurs-post", "Concurs pentru ocuparea postului"),
        ("https://fond.example/apeluri", "Apel de propuneri"),
        ("https://fond.example/ru/programmy", "Гранты и финансирование"),
    ]}
    assert md_links(page) == ["https://fond.example/ru/programmy", "https://fond.example/apeluri"]
    # The English rules alone miss the call and follow the news story.
    assert rs.pick_links(page, "https://fond.example/", set()) == [
        "https://fond.example/noutati/granturi-acordate"]


def md_links(page):
    settings = br.register_settings("md")
    return rs.pick_links(page, "https://fond.example/", set(), settings["grant_words"],
                         settings["skip_link"], settings["fold"])


def test_md_grant_words_read_both_forms_of_the_romanian_letters():
    settings = br.register_settings("md")
    for text in ("Finanțare pentru ONG-uri", "Finanţare pentru ONG-uri", "Finantare",
                 "Concurs de granturi", "Конкурс грантов", "Call for proposals", "Bursă de studii",
                 "Bursa de merit", "Sprijin pentru tineri", "Oportunități", "Поддержка НКО"):
        assert br.has_grant_words(settings, text), text


@pytest.mark.parametrize("text", [
    "Capela satului", "apelare gratuită", "migrant workers", "we reimburse travel",
    "Programul de lucru",
])
def test_md_grant_words_match_whole_word_starts_only(text):
    assert not br.has_grant_words(br.register_settings("md"), text)


@pytest.mark.parametrize("href, text", [
    ("https://fond.example/ro/cum-sa-solicitati-finantare", "Cum să solicitați finanțare"),
    ("https://fond.example/ro/solicitati-un-grant", "Solicitați un grant"),
    ("https://fond.example/ro/beneficiari-eligibili", "Beneficiari eligibili pentru granturi"),
    ("https://fond.example/ro/granturi-pentru-beneficiari", "Granturi pentru beneficiari"),
])
def test_md_apply_and_eligibility_pages_are_not_skipped(href, text):
    assert md_links({"links": [(href, text)]}) == [href]


@pytest.mark.parametrize("href, text", [
    ("https://fond.example/ro/node/123", "Concursul pentru ocuparea funcției publice vacante"),
    ("https://fond.example/ro/post-vacant-concurs", "Concurs"),
    ("https://fond.example/ru/node/5", "Конкурс на должность специалиста"),
    ("https://fond.example/ru/%D0%B2%D0%B0%D0%BA%D0%B0%D0%BD%D1%81%D0%B8%D0%B8", "Гранты"),
    ("https://fond.example/ro/licitatii/granturi", "Granturi"),
    ("https://fond.example/ro/lista-beneficiarilor-granturi", "Granturi"),
    ("https://fond.example/ro/n%C4%83ut%C4%83%C8%9Bi/granturi", "Noutăți: granturi"),
])
def test_md_vacancies_tenders_and_past_winners_are_skipped(href, text):
    assert md_links({"links": [(href, text)]}) == []


def test_md_percent_encoded_cyrillic_address_counts():
    href = "https://fond.example/ru/%D0%B3%D1%80%D0%B0%D0%BD%D1%82%D1%8B"  # /ru/гранты
    assert md_links({"links": [(href, "Подробнее")]}) == [href]


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


@pytest.mark.parametrize("text, found", [
    ("Rātā Foundation grants", True),
    ("Rata Foundation grants", True),
    ("Strata title management", False),
])
def test_names_are_whole_words(text, found):
    stop = br.register_settings("nz")["name_stopwords"]
    assert br.names_funder({"text": text}, "Rātā Foundation", stop) is found


@pytest.mark.parametrize("name, text", [
    ("ADA", "Canada Fund for Local Initiatives"),
    ("GIZ", "The gizmo shop"),
    ("UNDP", "sundpa travel"),
])
def test_short_and_acronym_names_are_not_found_inside_other_words(name, text):
    stop = br.register_settings("md")["name_stopwords"]
    assert not br.names_funder({"text": text}, name, stop)
    assert br.names_funder({"text": f"About {name} in Moldova"}, name, stop)


def test_md_known_funders_have_one_entry_per_site():
    from urllib.parse import urlsplit
    known = br.known_funders("md", br.register_settings("md")["dedupe_known_by_site"])
    sites = [rs._site(urlsplit(f["url"]).hostname) for f in known]
    assert len(sites) == len(set(sites))
    names = {f["name"] for f in known}
    assert {"UNDP Moldova", "Primăria Municipiului Chișinău"} <= names
    assert not [n for n in names if n.endswith((" grants", " projects", " programs"))]


def test_nz_known_funders_are_not_deduped_by_site():
    assert br.register_settings("nz")["dedupe_known_by_site"] is False
    names = {f["name"] for f in br.known_funders("nz")}
    assert {"Sport NZ", "Sport NZ funding"} <= names


def test_md_seed_and_must_appear_names_agree():
    from api import sources
    seeds = {s["url"]: s["name"] for s in sources.list_seed_sources("md")}
    for f in sources.must_appear_funders("md"):
        assert seeds.get(f["url"], f["name"]) == f["name"]


def test_md_tier_one_is_narrow():
    tier_one = br.register_settings("md")["tier_one"]
    assert "MDL 20 million" in tier_one and "EUR 1 million" in tier_one
    assert "main funder for its sector or region" not in tier_one


# --- Config checks -------------------------------------------------------------

def edit_md(isolated, change):
    data = json.loads((isolated / "md.json").read_text())
    change(data["register"])
    (isolated / "md.json").write_text(json.dumps(data, ensure_ascii=False))
    clear_config_cache()


@pytest.mark.parametrize("change, message", [
    (lambda r: r.update(grant_words="grant"), "grant_words must be a list"),
    (lambda r: r["grant_words"].append(""), "grant_words must be a list"),
    (lambda r: r["grant_words"].append("  "), "grant_words must be a list"),
    (lambda r: r["skip_link_words"].append(""), "skip_link_words must be a list"),
    (lambda r: r.update(name_stopwords="the"), "name_stopwords must be a list"),
    (lambda r: r["categories"].update(embassy=3), "categories must map"),
    (lambda r: r.update(categories=["international"]), "categories must map"),
    (lambda r: r.update(tier_one=["big"]), "tier_one must be"),
    (lambda r: r.update(fold_words="yes"), "fold_words must be"),
    (lambda r: r["grant_words"].append("ъ"), "folds to nothing"),
])
def test_bad_config_stops_before_any_call(isolated, change, message):
    edit_md(isolated, change)
    with pytest.raises(SystemExit, match=message):
        br.register_settings("md")


def test_a_register_that_is_not_an_object_is_refused(isolated):
    data = json.loads((isolated / "md.json").read_text())
    data["register"] = [1, 2]
    (isolated / "md.json").write_text(json.dumps(data))
    clear_config_cache()
    with pytest.raises(SystemExit, match="register must be an object"):
        br.register_settings("md")


@pytest.mark.parametrize("slug", ["MD", "Md", "../sources/nz", "md/../nz", "m", "", "nz\n"])
def test_a_bad_country_slug_is_refused(slug):
    with pytest.raises(SystemExit, match="not a country slug"):
        br.main([slug, "--confirm"])


def test_a_country_needs_its_own_config_file():
    with pytest.raises(SystemExit, match="no zz.json"):
        br.register_settings("zz")


# --- Runs, with every paid call faked -------------------------------------------

class FakeAnthropic:
    """Stands in for the anthropic module: each ask lists one new funder and
    costs `COST` dollars at the listing model's price."""
    asks: list[str] = []
    COST = 1.0

    def __init__(self, **_):
        self.messages = self

    def stream(self, model, max_tokens, messages):
        FakeAnthropic.asks.append(messages[0]["content"])
        n = len(FakeAnthropic.asks)
        body = json.dumps({"funders": [{"name": f"Funder {n}", "website": f"https://f{n}.example/"}]})
        resp = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=0,
                                  output_tokens=round(self.COST * 1e6 / br.LIST_PRICE_OUT)),
            content=[SimpleNamespace(type="text", text=body)])
        return _Stream(resp)


class _Stream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get_final_message(self):
        return self.resp


@pytest.fixture
def paid(monkeypatch):
    """Every paid call faked; a real one would fail the test."""
    FakeAnthropic.asks = []
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setattr(rs, "fetch_page", lambda url: {"url": url, "error": "offline"})

    def no_call(*a, **k):
        raise AssertionError("a paid call was made")
    monkeypatch.setattr(rs, "anthropic_ask", no_call)
    monkeypatch.setattr(br, "tavily_site", no_call)
    monkeypatch.setattr(br, "tavily_find", no_call)
    return FakeAnthropic


@pytest.mark.parametrize("key", ["ANTHROPIC_API_KEY", "TAVILY_API_KEY"])
def test_a_missing_key_stops_the_run_before_any_call(paid, monkeypatch, key):
    monkeypatch.delenv(key)
    with pytest.raises(SystemExit, match=key):
        br.main(["md", "--confirm"])
    assert paid.asks == []


def test_confirm_without_a_register_block_stops_before_any_call(isolated, paid):
    edit_md(isolated, lambda r: r.clear())
    with pytest.raises(SystemExit, match="no register categories"):
        br.main(["md", "--confirm"])
    assert paid.asks == []


def test_a_partial_listing_is_resumed_not_taken_as_whole(isolated, paid):
    candidates = isolated / "md-register-candidates.json"
    # $1 an ask against $1.50: the second ask reaches the budget.
    assert br.main(["md", "--confirm", "--no-search", "--budget", "1.5"]) == 1
    saved = json.loads(candidates.read_text())
    assert saved["asked"] == {"international": 2} and len(saved["candidates"]) == 2
    assert not (isolated / "md-register.json").exists()
    assert br.main(["md", "--confirm", "--no-search", "--budget", "100"]) == 0
    assert len(paid.asks) == 2 + 6 * br.ASKS
    assert all("of this kind: international donors" not in a for a in paid.asks[2:])
    saved = json.loads(candidates.read_text())
    assert set(saved["asked"].values()) == {br.ASKS} and len(saved["asked"]) == 7
    assert json.loads((isolated / "md-register.json").read_text())["country"] == "md"
    assert not [p for p in os.listdir(isolated) if p.endswith(".tmp")]


def test_a_bare_list_of_candidates_is_still_read(isolated, paid):
    (isolated / "md-register-candidates.json").write_text(json.dumps([
        {"name": "Old Funder", "category": "international", "website": "", "funding_url": None,
         "regions": [], "tier": 2}]))
    assert br.main(["md", "--confirm", "--no-search", "--budget", "100"]) == 0
    assert len(paid.asks) == 6 * br.ASKS
    saved = json.loads((isolated / "md-register-candidates.json").read_text())
    assert saved["candidates"][0]["name"] == "Old Funder"


def test_a_failed_write_leaves_the_old_file(isolated):
    path = str(isolated / "md-register.json")
    br.write_json(path, {"ok": True})
    with pytest.raises(TypeError):
        br.write_json(path, {"bad": object()})
    assert json.loads(open(path).read()) == {"ok": True}
    assert not [p for p in os.listdir(isolated) if p.endswith(".tmp")]


def test_the_budget_stops_the_resolve_phase(monkeypatch):
    """Each funder costs one extract (its home page refuses a plain fetch)
    and one search; the third and later are left unresolved."""
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setattr(rs, "fetch_page", lambda url: {
        "url": url, "text": "About us.", "links": [], "via": "tavily"})

    class Tavily:
        def __init__(self, api_key):
            pass

        def search(self, query, max_results, include_domains=None):
            return {"results": [{"url": f"https://{include_domains[0]}/grants"}]}
    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=Tavily))
    settings = br.register_settings("md")
    budget = br.Budget(4.5 * br.TAVILY_PRICE)
    funders = [{"name": f"Funder {n}", "category": "international", "tier": 2, "regions": [],
                "website": f"https://f{n}.example/"} for n in range(5)]
    out = [br.resolve(f, True, settings, budget) for f in funders]
    assert [r.get("resolved_by") for r in out[:2]] == ["search", "search"]
    assert [r.get("error") for r in out[2:]] == [br.BUDGET_REACHED] * 3
    # The third funder's home page was read before the cap was reached; no
    # call is started after it.
    assert (budget.searches, budget.extracts) == (2, 3)
    assert budget.spent == pytest.approx(5 * br.TAVILY_PRICE)


def test_md_lists_no_longer_carry_usaid():
    data = json.loads(open(os.path.join(tenant.PROJECT_ROOT, "platform", "sources", "md.json"),
                           encoding="utf-8").read())
    entries = data["must_appear_funders"] + data["seed_sources"]
    assert not [e for e in entries if "usaid" in (e["name"] + e["url"]).lower()]
