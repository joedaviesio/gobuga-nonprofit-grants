"""scripts/build_register.py: the per-country register settings, the prompt
sent for each category, and the rules that pick a funding page.

Pure-Python. Nothing is fetched and no model is called.
"""

import json
import os
import shutil
import socket
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


@pytest.fixture(autouse=True)
def no_real_calls(monkeypatch):
    """No test here may reach a paid service or the network, whatever keys
    the environment holds: the real clients and outbound connections fail
    the test. A test that needs a client fakes it over this."""
    def refuse(*a, **k):
        raise AssertionError("a real client or connection was made")
    guard = SimpleNamespace(Anthropic=refuse, TavilyClient=refuse)
    monkeypatch.setitem(sys.modules, "anthropic", guard)
    monkeypatch.setitem(sys.modules, "tavily", guard)
    monkeypatch.setattr(rs, "_client", None)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """A Tavily retry's waits are recorded, not slept through."""
    waits = []
    monkeypatch.setattr(br, "_pause", waits.append)
    br._out_of_credits.clear()
    yield waits
    br._out_of_credits.clear()


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
    return br.pick_links(page, "https://fond.example/", br.register_settings("md"))


def test_nz_links_are_the_sweeps_own():
    page = {"links": [
        ("https://trust.example/funding/community-grants", "Community grants"),
        ("https://trust.example/news/grant-recipients-announced", "Read the news"),
        ("https://trust.example/apply", "How to apply for funding"),
        ("https://trust.example/contact", "Contact us to apply for grants"),
    ]}
    assert br.pick_links(page, "https://trust.example/", br.register_settings("nz")) \
        == rs.pick_links(page, "https://trust.example/", {"https://trust.example"})


@pytest.mark.parametrize("text", [
    "Finanțare pentru ONG-uri", "Finanţare pentru ONG-uri", "Finantare", "Concurs de granturi",
    "Конкурс грантов", "Call for proposals", "Bursă de studii", "Bursa de merit",
    "Sprijin pentru tineri", "Oportunități de finanțare", "Поддержка НКО", "Прием заявок",
    "ПРИЁМ ЗАЯВОК",
    "Apel deschis", "Apel de propuneri", "Apeluri", "Apelul de granturi",
    "Program de microgranturi", "Subgranturi pentru ONG-uri", "Minigranturi", "Микрогранты",
    "Мини-гранты", "Cofinanțare", "Co-finanțare",
])
def test_md_grant_words_are_found(text):
    assert br.has_grant_words(br.register_settings("md"), text)


@pytest.mark.parametrize("text", [
    "Capela satului", "apelare gratuită", "Apelul la 112", "Apel telefonic", "migrant workers",
    "we reimburse travel", "Programul de lucru",
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


@pytest.mark.parametrize("text", [
    "Granturi pentru angajarea tinerilor",
    "Subvenții pentru angajarea persoanelor cu dizabilități",
    "Program de granturi pentru dezvoltarea carierei",
    "Program de granturi pentru ocuparea forței de muncă",
    "Granturi pentru funcții de sprijin comunitar",
    "Granturi pentru locuri de muncă vacante",
    "Concurs de granturi — rezultate și calendar",
    "Achiziții verzi — granturi",
    "Гранты для поддержки должностных лиц",
    # The English skip list is for addresses only; these texts are calls.
    "Contact us to apply for grants",
    "Terms of Reference – call for proposals",
    "News and calls for proposals",
])
def test_md_grant_links_that_share_a_skip_word_are_kept(text):
    href = "https://fond.example/ro/p/7"
    assert md_links({"links": [(href, text)]}) == [href]


@pytest.mark.parametrize("href, text", [
    ("https://fond.example/ro/node/123", "Concursul pentru ocuparea funcției publice vacante"),
    ("https://fond.example/ro/post-vacant-concurs", "Concurs"),
    ("https://fond.example/ru/node/5", "Конкурс на должность"),
    ("https://fond.example/ru/node/6", "Конкурс на замещение должности"),
    ("https://fond.example/ru/%D0%B2%D0%B0%D0%BA%D0%B0%D0%BD%D1%81%D0%B8%D0%B8", "Вакансии"),
    ("https://fond.example/ru/%D0%B2%D0%B0%D0%BA%D0%B0%D0%BD%D1%81%D0%B8%D0%B8", "Гранты"),
    ("https://fond.example/ro/licitatii/granturi", "Granturi"),
    ("https://fond.example/ro/lista-beneficiarilor-granturi", "Granturi"),
    ("https://fond.example/ro/n%C4%83ut%C4%83%C8%9Bi/granturi", "Noutăți: granturi"),
    ("https://fond.example/ro/p/8", "Rezultatele concursului de granturi 2025"),
    ("https://fond.example/ro/p/9", "Achiziții publice: granturi"),
])
def test_md_vacancies_tenders_and_past_winners_are_skipped(href, text):
    assert md_links({"links": [(href, text)]}) == []


@pytest.mark.parametrize("text", [
    "Concurs de angajare", "Anunț privind organizarea concursului de angajare",
    "Concurs pentru suplinirea funcției vacante", "Concurs pentru funcția de director",
    "Итоги конкурса", "Результаты конкурса", "Concursuri închise", "Apeluri închise",
    "Oportunități de angajare", "Oportunități de carieră", "Карьера",
])
def test_md_vacancy_and_results_texts_are_skipped(text):
    assert md_links({"links": [("https://fond.example/ro/content/481", text)]}) == []


@pytest.mark.parametrize("path", [
    "/ro/content/anunt-privind-organizarea-concursului-de-angajare",
    "/ro/content/concurs-suplinirea-functiei-vacante-de-specialist",
    "/ro/content/concurs-pentru-functia-de-director",
    "/ro/cariere/concurs-angajare",
])
def test_md_vacancy_addresses_are_skipped(path):
    assert md_links({"links": [("https://fond.example" + path, "Detalii")]}) == []


@pytest.mark.parametrize("text", [
    "Apeluri deschise", "Concurs de granturi", "Oportunități de finanțare",
    "Granturi pentru programe de angajare a tinerilor", "Гранты для карьерного роста",
])
def test_md_calls_near_vacancy_words_are_kept(text):
    href = "https://fond.example/ro/content/482"
    assert md_links({"links": [(href, text)]}) == [href]


def test_a_weak_skip_word_gives_way_only_to_a_grant():
    kept = ("https://fond.example/ro/cariera-granturi", "Granturi pentru tineri")
    assert md_links({"links": [kept]}) == [kept[0]]
    # The same address with text that names no grant is a jobs page.
    assert md_links({"links": [("https://fond.example/ro/cariera-granturi", "Cariera")]}) == []
    assert md_links({"links": [("https://fond.example/ro/granturi/p", "Angajări")]}) == []


def test_md_percent_encoded_cyrillic_address_counts():
    href = "https://fond.example/ru/%D0%B3%D1%80%D0%B0%D0%BD%D1%82%D1%8B"  # /ru/гранты
    assert md_links({"links": [(href, "Подробнее")]}) == [href]
    twice = "https://fond.example/ru/%25D0%25B3%25D1%2580%25D0%25B0%25D0%25BD%25D1%2582%25D1%258B"
    assert md_links({"links": [(twice, "Подробнее")]}) == [twice]


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
    assert {"PNUD Moldova", "Primăria Municipiului Chișinău"} <= names
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


def test_md_aliases_name_funders_on_the_lists():
    from api import funders, sources
    funders._get_lookup.cache_clear()
    listed = {f["name"] for f in sources.list_seed_sources("md") + sources.must_appear_funders("md")}
    assert set(get_country_config("md").funder_aliases) <= listed
    # The Romanian name is the funder's name; English and Russian lead to it.
    assert funders.canonicalise_funder("East Europe Foundation", "md") == "Fundația Est-Europeană"
    assert funders.canonicalise_funder("Фонд Сорос-Молдова", "md") == "Fundația Soros Moldova"
    funders._get_lookup.cache_clear()


def test_md_aliases_are_not_bare_acronyms_shared_with_other_bodies():
    aliases = {a for names in get_country_config("md").funder_aliases.values() for a in names}
    assert not aliases & {"undp", "oda", "eef", "ned", "eed", "bst", "giz", "sdc", "sida"}


def _md_lists():
    from api import sources
    return sources.list_seed_sources("md"), sources.must_appear_funders("md")


@pytest.mark.parametrize("gone", [
    "eeagrants", "norway grants", "fism", "social investment fund", "cntm", "youth council",
    "gagauzia", "giz", "eda.admin.ch", "swiss agency", "sida", "swedish",
])
def test_md_lists_no_longer_carry_the_removed_funders(gone):
    seeds, must = _md_lists()
    assert not [e for e in seeds + must if gone in (e["name"] + " " + e["url"]).lower()]
    known = br.known_funders("md", br.register_settings("md")["dedupe_known_by_site"])
    assert not [f for f in known if gone in (f["name"] + " " + f["url"]).lower()]


def test_md_seed_categories_are_configured():
    seeds, _ = _md_lists()
    assert {s["category"] for s in seeds} <= set(br.register_settings("md")["categories"])


def test_md_must_appear_funders_are_seeds_too():
    seeds, must = _md_lists()
    listed = {(s["name"], s["url"]) for s in seeds}
    assert [(f["name"], f["url"]) for f in must if (f["name"], f["url"]) not in listed] == []


def test_md_seeds_do_not_share_a_url():
    seeds, _ = _md_lists()
    assert len({s["url"] for s in seeds}) == len(seeds) == 25


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
def test_a_bad_country_slug_is_refused(paid, slug):
    with pytest.raises(SystemExit, match="not a country slug"):
        br.main([slug, "--confirm"])
    assert paid.asks == []


def test_a_country_needs_its_own_config_file():
    with pytest.raises(SystemExit, match="no zz.json"):
        br.register_settings("zz")


# --- Runs, with every paid call faked -------------------------------------------

class FakeAnthropic:
    """Stands in for the anthropic module: each ask lists one new funder and
    costs `COST` dollars at the listing model's price. `reply`, when set,
    takes (ask number, prompt) and returns (reply text, stop reason)."""
    asks: list[str] = []
    options: list[dict] = []
    COST = 1.0
    reply = None

    def __init__(self, **_):
        self.messages = self

    def stream(self, model, max_tokens, messages, **options):
        FakeAnthropic.asks.append(messages[0]["content"])
        FakeAnthropic.options.append({"max_tokens": max_tokens, **options})
        n = len(FakeAnthropic.asks)
        body, stop = json.dumps({"funders": [
            {"name": f"Funder {n}", "website": f"https://f{n}.example/"}]}), "end_turn"
        if FakeAnthropic.reply:
            body, stop = FakeAnthropic.reply(n, messages[0]["content"])
        resp = SimpleNamespace(
            stop_reason=stop,
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
    FakeAnthropic.asks, FakeAnthropic.options, FakeAnthropic.reply = [], [], None
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


def test_nzs_bare_list_of_candidates_is_still_read():
    path = os.path.join(tenant.PROJECT_ROOT, "platform", "sources", "nz-register-candidates.json")
    candidates, asked = br.load_candidates(path, "nz", NZ_CATEGORIES)
    assert len(candidates) == len(json.load(open(path, encoding="utf-8")))
    assert asked == {c: br.ASKS for c in NZ_CATEGORIES}


OLD_FUNDER = {"name": "Trust Waikato", "category": "council", "website": "", "funding_url": None,
              "regions": [], "tier": 2}


@pytest.mark.parametrize("content, message", [
    ([OLD_FUNDER], "old-format list with no country, not known to be md's"),
    ({"country": "nz", "asked": {"council": 2}, "candidates": [OLD_FUNDER]}, "holds 'nz'"),
    ({"country": "md", "candidates": [OLD_FUNDER]}, "missing its candidates or its count"),
    ({"country": "md", "asked": {}, "candidates": "none"}, "missing its candidates or its count"),
    ("{not json", "cannot be read"),
])
def test_a_candidates_file_that_is_not_this_countrys_is_refused(isolated, paid, content, message):
    path = isolated / "md-register-candidates.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    with pytest.raises(SystemExit, match=message) as stop:
        br.main(["md", "--confirm", "--no-search"])
    assert str(path) in str(stop.value)
    assert paid.asks == [] and not (isolated / "md-register.json").exists()


def test_candidates_of_a_dropped_category_are_dropped(isolated, capsys):
    path = isolated / "md-register-candidates.json"
    path.write_text(json.dumps({
        "country": "md", "asked": {"international": 2, "gaming": 2},
        "candidates": [{**OLD_FUNDER, "category": "gaming"},
                       {**OLD_FUNDER, "name": "Kept", "category": "international"}]}))
    candidates, asked = br.load_candidates(str(path), "md", br.register_settings("md")["categories"])
    assert [c["name"] for c in candidates] == ["Kept"] and asked == {"international": 2}
    assert "dropped 1 candidates" in capsys.readouterr().out


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
        "url": url, "text": "About us.", "links": [], "via": "tavily", "extract_tried": True})

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


@pytest.fixture
def served(monkeypatch):
    """fetch_page reading `served.html` from a fake server, and Tavily's
    extract answering with `served.extract` (a callable)."""
    import httpx
    state = SimpleNamespace(html="", extract=lambda urls: {"results": []})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, html=state.html))
    real_client = httpx.Client
    monkeypatch.setattr(rs.httpx, "Client", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(rs, "assert_public_url", lambda url: None)
    monkeypatch.setattr(rs, "PER_HOST_DELAY_S", 0)
    monkeypatch.setenv("TAVILY_API_KEY", "test")

    class Tavily:
        def __init__(self, api_key):
            pass

        def extract(self, urls):
            return state.extract(urls)
    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=Tavily))
    return state


def failed_extract(urls):
    raise RuntimeError("Tavily is down")


@pytest.mark.parametrize("extract, sweep_count", [
    (lambda urls: {"results": []}, 1),      # empty: the sweep counts it, as before
    (failed_extract, 0),                     # failed: the sweep does not, as before
])
def test_an_extract_that_brings_nothing_is_not_counted(served, extract, sweep_count):
    """A page with no text without scripts is sent to Tavily's extract.
    Tavily bills only an extraction that succeeded, so the build counts
    only those; the sweep's own count is as it was. (The first paid md
    build counted every attempt: 33, where Tavily's account shows 8.)"""
    served.html = "<html><body><p>Loading…</p></body></html>"
    served.extract = extract
    before = rs.SEARCH_CREDITS["extract"]
    settings = br.register_settings("md")
    budget = br.Budget(2.5 * br.TAVILY_PRICE)
    funders = [{"name": f"Funder {n}", "category": "international", "tier": 2, "regions": [],
                "website": f"https://f{n}.example/"} for n in range(5)]
    out = [br.resolve(f, False, settings, budget) for f in funders]
    assert [r["error"] for r in out] == ["no funding page found"] * 5
    assert budget.extracts == 0 and budget.spent == 0
    assert rs.SEARCH_CREDITS["extract"] - before == 5 * sweep_count


def test_an_extract_that_brings_the_page_is_counted(served):
    served.html = "<html><body><p>Loading…</p></body></html>"
    served.extract = lambda urls: {"results": [{"raw_content": "Granturi pentru ONG-uri. " * 30}]}
    budget = br.Budget(100)
    page = br.fetch("https://f.example/", budget)
    assert page["via"] == "tavily" and budget.extracts == 1
    assert budget.spent == pytest.approx(br.TAVILY_PRICE)


def test_a_page_with_text_is_returned_to_the_sweep_as_before(served):
    served.html = "<html><body><main><p>" + "Community grants open now. " * 30 + "</p></main></body></html>"
    served.extract = failed_extract
    page = rs.fetch_page("https://f.example/")
    assert set(page) == {"url", "text", "links"}


# --- The owner's lists hold: exclusions, the gate, merges, resumes ------------------

def reply_by_category(funders_by_category: dict, stop="end_turn"):
    """A listing reply that depends on which category the prompt asks for."""
    descriptions = br.register_settings("md")["categories"]

    def reply(n, prompt):
        category = next(c for c, d in descriptions.items() if f"of this kind: {d}." in prompt)
        return json.dumps({"funders": funders_by_category.get(category, [])}), stop
    return reply


def claim(name, website="", tier=1):
    return {"name": name, "website": website, "tier": tier}


SIMULATED_LISTING = {
    "international": [
        claim("USAID Moldova", "https://www.usaid.gov/moldova"), claim("GIZ"),
        claim("Swiss Agency for Development and Cooperation (SDC)"), claim("Sida Moldova"),
        claim("EEA and Norway Grants"), claim("Delegation of the European Union to Moldova"),
        claim("UNDP Moldova"), claim("World Bank Moldova", "https://www.worldbank.org/"),
    ],
    "government": [
        claim("Ministry of Culture of the Republic of Moldova", "https://mc.gov.md/"),
        claim("Ministry of Education and Research of the Republic of Moldova", "https://mec.gov.md/"),
        claim("Agency for Interventions and Payments in Agriculture (AIPA)", "https://aipa.gov.md/"),
        claim("Moldovan Social Investment Fund (FISM)"),
        claim("Ministerul Muncii și Protecției Sociale", "https://social.gov.md/"),
        claim("Ministry of Culture of Moldova Republic", "https://www.mc.gov.md/"),
        claim("Cultural Projects Fund", "https://cultural-projects.example/"),
    ],
    "council": [
        claim("Gagauzia Executive Committee"), claim("Primăria Municipiului Cahul", "https://cahul.md/"),
        claim("Consiliul Raional Orhei", "https://orhei.md/"),
    ],
    "embassy": [
        claim("Embassy of Japan in the Republic of Moldova", "https://www.md.emb-japan.go.jp/"),
        claim("Embassy of the Federal Republic of Germany in Chisinau", "https://chisinau.diplo.de/"),
        claim("German Embassy in Moldova", "https://chisinau.diplo.de/md-ro"),
        claim("US Embassy Chisinau", "https://md.usembassy.gov/"),
    ],
    "cross-border": [
        claim("Interreg NEXT Romania-Republic of Moldova", "https://ro-md.net/"),
        claim("Interreg NEXT Black Sea Basin", "https://blacksea-cbc.net/"),
    ],
    "foundation": [
        claim("Soros Foundation Moldova"), claim("National Endowment for Democracy"),
        claim("Fundația Sidanova", "https://sidanova.example/"),
        claim("German Marshall Fund of the United States", "https://www.gmfus.org/"),
    ],
    "corporate": [claim("Fundația Orange Moldova", "https://www.orange.md/", tier=2)],
}
EXCLUDED_IN_LISTING = {"USAID Moldova", "GIZ", "Swiss Agency for Development and Cooperation (SDC)",
                       "Sida Moldova", "EEA and Norway Grants",
                       "Moldovan Social Investment Fund (FISM)",
                       "Gagauzia Executive Committee"}


@pytest.fixture
def simulated(isolated, paid, monkeypatch):
    """The md build with every paid call faked: the listing above, and every
    page answering with grant words. The Cultural Projects Fund's page turns
    out to live on the Ministry of Culture's site."""
    paid.reply = reply_by_category(SIMULATED_LISTING)
    paid.COST = 0.0

    def fetch_page(url):
        if "cultural-projects" in url:
            url = "https://mc.gov.md/ro/content/proiecte-culturale"
        return {"url": url, "text": "Granturi și finanțare pentru ONG-uri. " * 20, "links": []}
    monkeypatch.setattr(rs, "fetch_page", fetch_page)
    resolved = []
    real_resolve = br.resolve

    def resolve(funder, *a, **k):
        resolved.append(funder["name"])
        return real_resolve(funder, *a, **k)
    monkeypatch.setattr(br, "resolve", resolve)
    assert br.main(["md", "--confirm", "--no-search", "--budget", "100"]) == 0
    register = json.loads((isolated / "md-register.json").read_text())
    return SimpleNamespace(register=register, resolved=resolved)


def test_md_prompts_name_no_excluded_funder():
    settings = br.register_settings("md")
    for category in settings["categories"]:
        prompt = br.list_prompt(settings, category, get_country_config("md").regions)
        assert not settings["exclude"].search(br._to_ascii(prompt)), category


@pytest.mark.parametrize("name, excluded", [
    ("USAID", True), ("USAID Moldova", True), ("SIDA Moldova", True),
    ("Swedish International Development Cooperation Agency (Sida)", True),
    ("GIZ Moldova", True), ("Swiss Agency for Development and Cooperation (SDC)", True),
    # SIDA is also the Romanian for AIDS, so the bare acronym rules nothing out.
    ("Liga Persoanelor care Trăiesc cu HIV/SIDA", False), ("Sida", False),
    ("Granturile SEE și Norvegiene", True), ("Fondul de Investitii Sociale din Moldova", True),
    ("Fundația Sidanova", False), ("Gizmo Foundation", False), ("SDCX Trust", False),
    ("Mesida Fund", False), ("Fundația Est-Europeană", False),
])
def test_exclusion_matches_whole_words_only(name, excluded):
    assert br.is_excluded(br.register_settings("md"), name, "md") is excluded


def test_nz_has_no_exclusions_or_manifest_tier():
    settings = br.register_settings("nz")
    assert settings["exclude"] is None and settings["tier_one_from_manifest"] is False
    assert not br.is_excluded(settings, "USAID", "nz")


@pytest.mark.parametrize("change, message", [
    (lambda r: r.update(exclude="USAID"), "exclude must be a list"),
    (lambda r: r["exclude"].append(""), "exclude must be a list"),
    (lambda r: r.update(shared_hosts=[1]), "shared_hosts must be a list"),
    (lambda r: r.update(tier_one_from_manifest="yes"), "tier_one_from_manifest must be"),
    (lambda r: r.update(merge_new_by_site="yes"), "merge_new_by_site must be"),
    (lambda r: r.update(strict_pages=1), "strict_pages must be"),
    (lambda r: r.update(strong_grant_words=[]), "strict_pages needs fold_words and strong"),
    (lambda r: r.update(fold_words=False, weak_skip_link_words=[]),
     "strict_pages needs fold_words"),
    (lambda r: r["not_funding_path_words"].append(""), "not_funding_path_words must be a list"),
    (lambda r: r.update(merge_name_stopwords="small"), "merge_name_stopwords must be a list"),
])
def test_bad_new_keys_stop_before_any_call(isolated, change, message):
    edit_md(isolated, change)
    with pytest.raises(SystemExit, match=message):
        br.register_settings("md")


def test_an_excluded_candidate_never_reaches_resolve(simulated):
    assert not EXCLUDED_IN_LISTING & set(simulated.resolved)
    names = {f["name"] for f in simulated.register["funders"] + simulated.register["unresolved"]}
    assert not EXCLUDED_IN_LISTING & names
    assert "Fundația Sidanova" in names


def test_the_register_tier_one_is_the_manifests_must_appear_list(simulated):
    from api import sources
    tier_one = {f["name"] for f in simulated.register["funders"] if f["tier"] == 1}
    assert sum(1 for fs in SIMULATED_LISTING.values() for f in fs if f["tier"] == 1) >= 20
    assert tier_one == {f["name"] for f in sources.must_appear_funders("md")}


def test_a_candidate_on_a_known_site_is_merged_not_added(simulated):
    from urllib.parse import urlsplit
    names = {f["name"] for f in simulated.register["funders"]}
    english = {"Ministry of Culture of the Republic of Moldova", "Ministry of Culture of Moldova Republic",
               "Ministry of Education and Research of the Republic of Moldova",
               "Agency for Interventions and Payments in Agriculture (AIPA)",
               "Interreg NEXT Romania-Republic of Moldova",
               "Embassy of Japan in the Republic of Moldova",
               "Embassy of the Federal Republic of Germany in Chisinau", "German Embassy in Moldova",
               "Cultural Projects Fund"}
    assert not english & names
    # Merged before resolving, except the one whose page was found there.
    assert not (english - {"Cultural Projects Fund"}) & set(simulated.resolved)
    assert "Cultural Projects Fund" in simulated.resolved
    shared = br.register_settings("md")["shared_hosts"]
    sites = [rs._site(urlsplit(f["url"]).hostname) for f in simulated.register["funders"]]
    assert [s for s in sites if sites.count(s) > 1 and s not in shared] == []
    # A shared host holds two funders: they are not merged.
    assert {"German Marshall Fund of the United States",
            "Black Sea Trust for Regional Cooperation"} <= names


def test_a_truncated_listing_reply_is_not_counted_as_asked(isolated, paid):
    cut = [True]

    def reply(n, prompt):
        if cut.pop() if cut else False:
            return '{"funders": [{"name": "Half a fun', "max_tokens"
        return json.dumps({"funders": []}), "end_turn"
    paid.reply = reply
    paid.COST = 0.0
    assert br.main(["md", "--confirm", "--no-search"]) == 1
    saved = json.loads((isolated / "md-register-candidates.json").read_text())
    assert saved["asked"]["international"] == 1 and saved["asked"]["embassy"] == 2
    assert not (isolated / "md-register.json").exists()
    assert all(o["thinking"] == {"type": "disabled"} for o in paid.options)
    # The next run asks only the ask that failed.
    paid.asks = []
    assert br.main(["md", "--confirm", "--no-search"]) == 0
    assert len(paid.asks) == 1 and "of this kind: international donors" in paid.asks[0]


def test_a_run_stopped_in_resolve_resumes_without_paying_again(isolated, paid, monkeypatch):
    paid.COST = 0.0
    fetched = []

    def fetch_page(url):
        fetched.append(url)
        return {"url": url, "text": "Granturi. " * 40, "links": [], "via": "tavily",
                "extract_tried": True}
    monkeypatch.setattr(rs, "fetch_page", fetch_page)
    monkeypatch.setattr(rs, "CRAWL_CONCURRENCY", 1)
    progress = isolated / "md-register-progress.json"
    # Each resolve costs one extract; the budget covers four.
    assert br.main(["md", "--confirm", "--no-search", "--budget", str(3.5 * br.TAVILY_PRICE)]) == 1
    assert not (isolated / "md-register.json").exists()
    assert len(json.loads(progress.read_text())["resolved"]) == 4 == len(fetched)
    fetched.clear()
    assert br.main(["md", "--confirm", "--no-search", "--budget", "100"]) == 0
    assert len(fetched) == 14 - 4
    register = json.loads((isolated / "md-register.json").read_text())
    assert sum(1 for f in register["funders"] if f["name"].startswith("Funder ")) == 14
    assert not progress.exists()


def test_another_countrys_resolve_progress_is_refused(isolated, paid):
    progress = isolated / "md-register-progress.json"
    progress.write_text(json.dumps({"country": "nz", "resolved": {"trust-waikato": OLD_FUNDER}}))
    with pytest.raises(SystemExit, match="not the resolve progress for 'md'") as stop:
        br.main(["md", "--confirm", "--no-search"])
    assert str(progress) in str(stop.value)


def test_md_lists_no_longer_carry_usaid():
    data = json.loads(open(os.path.join(tenant.PROJECT_ROOT, "platform", "sources", "md.json"),
                           encoding="utf-8").read())
    entries = data["must_appear_funders"] + data["seed_sources"]
    assert not [e for e in entries if "usaid" in (e["name"] + e["url"]).lower()]


# --- Tavily's 429: waited out when it is too many requests -----------------------

class UsageLimitExceededError(Exception):
    """Named as the tavily SDK's: it raises this for every HTTP 429."""


class FlakyTavily:
    """A Tavily client whose searches answer `results` after `fails` 429s
    with `message`. Counts every attempt and every answer."""
    fails, message, results = 0, "Rate limit exceeded. Please slow down.", []
    attempts = answers = 0

    def __init__(self, api_key):
        pass

    def search(self, **kwargs):
        FlakyTavily.attempts += 1
        if FlakyTavily.attempts <= FlakyTavily.fails:
            raise UsageLimitExceededError(FlakyTavily.message)
        FlakyTavily.answers += 1
        return {"results": FlakyTavily.results}


@pytest.fixture
def flaky(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    FlakyTavily.fails, FlakyTavily.attempts, FlakyTavily.answers = 0, 0, 0
    FlakyTavily.message = "Rate limit exceeded. Please slow down."
    FlakyTavily.results = [{"url": "https://f.example/granturi", "title": "Granturi"}]
    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=FlakyTavily))
    return FlakyTavily


def test_a_rate_limited_search_is_tried_again_and_counted_once(flaky, no_waiting):
    flaky.fails = 2
    budget = br.Budget(100)
    assert br.tavily_find("F", "f.example", "granturi", budget) == "https://f.example/granturi"
    assert (flaky.attempts, flaky.answers, budget.searches) == (3, 1, 1)
    assert budget.spent == pytest.approx(br.TAVILY_PRICE)
    # Two waits, doubling from TAVILY_WAIT_S, each jittered down by up to half.
    assert len(no_waiting) == 2
    assert br.TAVILY_WAIT_S / 2 <= no_waiting[0] <= br.TAVILY_WAIT_S
    assert br.TAVILY_WAIT_S <= no_waiting[1] <= 2 * br.TAVILY_WAIT_S


def test_a_search_that_stays_rate_limited_gives_up_unpaid_and_says_why(
        flaky, no_waiting, capsys):
    flaky.fails = 99
    budget = br.Budget(100)
    funder = {"name": "F", "category": "international", "tier": 2, "regions": [],
              "website": "https://f.example/"}
    home = {"url": "https://f.example/", "text": "About us.", "links": []}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rs, "fetch_page", lambda url: home)
        out = br.resolve(funder, True, br.register_settings("md"), budget)
    assert out["error"] == "no funding page found" and out["search_failed"] is True
    assert flaky.attempts == br.TAVILY_TRIES and budget.searches == 0 and budget.spent == 0
    assert sum(no_waiting) <= br.TAVILY_MAX_WAIT_S
    assert ("search failed for F: UsageLimitExceededError: Rate limit exceeded. Please slow "
            "down.") in capsys.readouterr().out


def test_a_429_that_says_the_credits_are_used_up_is_not_retried(flaky, no_waiting):
    flaky.fails = 99
    flaky.message = ("This request exceeds your plan's set usage limit. Please upgrade your "
                     "plan or contact support@tavily.com")
    budget = br.Budget(100)
    with pytest.raises(UsageLimitExceededError, match="upgrade your plan"):
        br.tavily_find("F", "f.example", "granturi", budget)
    assert flaky.attempts == 1 and no_waiting == [] and budget.searches == 0
    # Nor is any other call made after it.
    flaky.message, flaky.attempts = "Too many requests", 0
    with pytest.raises(br.TavilyStopped):
        br.tavily_find("F", "f.example", "granturi", budget)
    assert flaky.attempts == 0


def test_another_tavily_error_is_not_retried(flaky, no_waiting):
    def broken(**kwargs):
        flaky.attempts += 1
        raise RuntimeError("bad request")
    flaky.search = staticmethod(broken)
    try:
        with pytest.raises(RuntimeError):
            br.tavily_find("F", "f.example", "granturi", br.Budget(100))
    finally:
        del flaky.search
    assert flaky.attempts == 1 and no_waiting == []


def test_a_rate_limited_extract_is_tried_again_and_counted_once(served, no_waiting):
    served.html = "<html><body><p>Loading…</p></body></html>"
    tries = []

    def extract(urls):
        tries.append(urls)
        if len(tries) == 1:
            raise UsageLimitExceededError("Rate limit exceeded")
        return {"results": [{"raw_content": "Granturi pentru ONG-uri. " * 30}]}
    served.extract = extract
    budget = br.Budget(100)
    page = br.fetch("https://f.example/", budget)
    assert page["via"] == "tavily" and len(tries) == 2 and len(no_waiting) == 1
    assert budget.extracts == 1


def test_a_result_after_a_failed_search_is_not_saved_and_a_rerun_tries_again(
        isolated, paid, monkeypatch):
    """The first paid md build lost 60 funders to 429s, and a run stopped
    halfway would have saved each as done. Now such a result is left out
    of the saved progress, and the next run tries it again."""
    paid.COST = 0.0
    paid.reply = reply_by_category({"embassy": [
        claim("Flaky Embassy", "https://flaky.example/", tier=2),
        claim("Steady Embassy", "https://steady.example/", tier=2)]})
    monkeypatch.setattr(rs, "fetch_page", lambda url: {
        "url": url, "text": "Granturi. " * 40, "links": []})
    calls, flaked = [], []

    def tavily_find(name, *a, **k):
        calls.append(name)
        if name == "Flaky Embassy" and not flaked:
            flaked.append(name)
            raise UsageLimitExceededError("Rate limit exceeded")
        return None
    monkeypatch.setattr(br, "tavily_find", tavily_find)
    monkeypatch.setattr(rs, "CRAWL_CONCURRENCY", 1)
    # The register is not written while a funder is unfinished, and the
    # progress file stays.
    assert br.main(["md", "--confirm", "--budget", "100"]) == 1
    progress = isolated / "md-register-progress.json"
    assert not (isolated / "md-register.json").exists()
    assert set(json.loads(progress.read_text())["resolved"]) == {"steady-embassy"}
    calls.clear()
    assert br.main(["md", "--confirm", "--budget", "100"]) == 0
    assert calls == ["Flaky Embassy"]
    assert (isolated / "md-register.json").exists() and not progress.exists()


def test_a_saved_result_the_rules_now_refuse_is_resolved_again(isolated, paid, monkeypatch):
    """Progress saved by the old rules: a news story, a funder cut short by
    a 429, and a good page. Only the good page is kept."""
    paid.COST = 0.0
    listing = [claim("UN Women Moldova", "https://moldova.unwomen.org", tier=2),
               claim("Flaky Embassy", "https://flaky.example/", tier=2),
               claim("Visegrad Fund", "https://www.visegradfund.org", tier=2)]
    paid.reply = reply_by_category({"international": listing})
    story = ("https://moldova.unwomen.org/ro/stories/reportaj/2026/02/de-la-adapost-temporar-la-"
             "sprijin-durabil-femeile-refugiate-construiesc-un-viitor")
    saved = {
        "un-women-moldova": {**listing[0], "category": "international", "regions": [],
                             "url": story, "resolved_by": "link"},
        "flaky-embassy": {**listing[1], "category": "international", "regions": [],
                          "error": "HTTP 403; extract failed: UsageLimitExceededError"},
        "visegrad-fund": {**listing[2], "category": "international", "regions": [],
                          "url": "https://www.visegradfund.org/grants", "resolved_by": "model"},
    }
    (isolated / "md-register-progress.json").write_text(
        json.dumps({"country": "md", "resolved": saved}))
    fetched = []

    def fetch_page(url):
        fetched.append(url)
        return {"url": url, "text": "Granturi. " * 40, "links": []}
    monkeypatch.setattr(rs, "fetch_page", fetch_page)
    assert br.main(["md", "--confirm", "--no-search", "--budget", "100"]) == 0
    assert sorted(fetched) == ["https://flaky.example/", "https://moldova.unwomen.org"]
    register = json.loads((isolated / "md-register.json").read_text())
    urls = {f["name"]: f["url"] for f in register["funders"]}
    assert urls["UN Women Moldova"] == "https://moldova.unwomen.org"
    assert urls["Visegrad Fund"] == "https://www.visegradfund.org/grants"


# --- New funders merged with each other by site ----------------------------------

@pytest.mark.parametrize("a, b", [
    ("Konrad Adenauer Foundation Moldova", "Konrad Adenauer Stiftung Moldova"),
    ("Friedrich Ebert Foundation Moldova", "Friedrich Ebert Stiftung Moldova"),
    ("European Bank for Reconstruction and Development (EBRD)",
     "European Bank for Reconstruction and Development (EBRD) Moldova"),
    ("UN Women Moldova", "United Nations Entity for Gender Equality and the Empowerment of "
                         "Women (UN Women) Moldova"),
    ("Ministry of Agriculture and Food Industry of Moldova",
     "Ministry of Agriculture and Food Industry of the Republic of Moldova"),
    ("Ministry of Environment of Moldova", "Ministry of Environment of the Republic of Moldova"),
    ("MAIB Foundation", "Moldova Agroindbank (MAIB) Community Grants"),
    ("Victoriabank Community Fund", "Victoriabank Community Program"),
    ("Purcari Wineries Community Fund", "Purcari Wineries Community Program"),
    ("U.S. Embassy Chisinau Democracy Commission Small Grants Program",
     "United States Embassy in Moldova (Democracy Commission Small Grants Program)"),
    ("U.S. Embassy Chisinau Public Affairs Section Small Grants",
     "Embassy of the United States in Chisinau (Public Affairs Section grants)"),
])
def test_md_names_that_agree_are_one_funder(a, b):
    settings = br.register_settings("md")
    assert br.names_agree(settings, a, b) and br.names_agree(settings, b, a)


@pytest.mark.parametrize("a, b", [
    # Two programmes of one embassy, each a way in for applicants.
    ("U.S. Embassy Chisinau Democracy Commission Small Grants Program",
     "U.S. Embassy Chisinau Public Affairs Section Small Grants"),
    ("Chevening / British Embassy Chisinau Small Grants",
     "British Embassy Chisinau (UK Government Bilateral Programme)"),
    ("Danube Transnational Programme", "Interreg NEXT Danube Region Programme"),
    ("Community Foundation Moldova", "Community Foundation Moldova"),  # no distinctive word
])
def test_md_names_that_differ_are_not_merged_by_name(a, b):
    assert not br.names_agree(br.register_settings("md"), a, b)


def test_nz_does_not_merge_new_funders_or_refuse_pages():
    settings = br.register_settings("nz")
    assert settings["merge_new_by_site"] is False and settings["strict_pages"] is False


def test_md_shared_hosts_include_the_big_government_hosts():
    assert {"gov.uk", "ec.europa.eu", "eeas.europa.eu"} <= br.register_settings("md")["shared_hosts"]


# --- Pages that are not funding pages --------------------------------------------

# From the first paid md build (platform/sources/md-register.json, 2026-09-30).
REFUSED_BY_ADDRESS = [
    "https://master-lux.md/experts-auto/cg%D1%9End-se-schimbd%D1%93-anvelopele-de-iarnd%D1%93-cu-"
    "cele-de-vard%D1%93-g-n-moldova-g-n-2026",
    "https://www.unicef.org/executiveboard/media/1381/file/2002-8-Rev1-Board-report-annex-"
    "Compendium-of-decisions-2002-EN-ODS.pdf",
    "https://agepi.gov.md/sites/default/files/bopi/intellectus_04-2006.pdf",
    "https://ondrl.gov.md/wp-content/uploads/2024/12/ESIA_Apeduct-Riscani_ro_10.12.2024.pdf",
    "https://www.iri.org/resources/iri-preliminary-statement-of-the-2025-moldova-parliamentary-"
    "elections",
    "https://ndi.org/publications/statement-national-democratic-institutes-pre-election-"
    "delegation-moldova",
    "https://www.jica.go.jp/english/overseas/moldova/information/topics/2025/1579816_59640.html",
    "https://md.usembassy.gov/news",
    "https://md.usembassy.gov/united-states-announces-substantial-additional-assistance-for-moldova",
    "https://moldova.fes.de/e/moldova-enters-the-eu-accession-negotiations-phase-reforms-eu-"
    "funding-and-the-test-of-domestic-consensus.html",
    "https://moldova.unwomen.org/ro/stories/reportaj/2026/02/de-la-adapost-temporar-la-sprijin-"
    "durabil-femeile-refugiate-construiesc-un-viitor",
    "https://czechaid.gov.cz/en/projects/application-of-innovative-curative-substances-in-modern-"
    "pharmaceutics-production-transfer-of-pharmaceutics-technologies-from-eu-to-moldova",
    "https://www.eurasia.org/eurasia-foundation-supports-kazakhstani-universities-through-unilinks",
    "https://www.maib.md/ro/maib-people/cum-obtineti-o-finantare-mai-rapid-6-lucruri-pe-care-"
    "orice-antreprenor-ar-trebui-sa-le-stie",
    "https://www.chisinau.md/ro/sedinta-comisiei-buget-economie-finante-patrimoniu-public-local-"
    "si-20924_283846.html",
    "https://proeducatie.md/mediatorii-comunitari-au-analizat-rezultatele-proiectului-si-"
    "perspectivele-de-continuare-a-sprijinului-pentru-copiii-romi/",
    # Borderline, and refused: a call's guide, in a dated uploads folder. A
    # file has no links to follow and goes stale with the call; the
    # funder's own page is found instead, or its home page.
    "https://www.keystonemoldova.md/wp-content/uploads/sites/4/2026/09/00.CG_KSM_2026_Ghid-de-"
    "aplicare-la-CG-RO.pdf",
]

ACCEPTED = [
    "https://www.mott.org/grants/", "https://moldova.iom.int/grants",
    "https://www.visegradfund.org/grants", "https://www.opensocietyfoundations.org/grants",
    "https://bosch-stiftung.de/en/funding", "https://culture.ec.europa.eu/funding/calls",
    "https://interreg-danube.eu/how-to-apply",
    "https://md.usembassy.gov/democracy-commission-small-grants-program-2",
    "https://erasmusplus.md/burse-erasmus",
    # Long slugs that name funding stand.
    "https://tineret.gov.md/programe/programul-de-granturi-pentru-organizatiile-de-tineret",
    "https://www.afd.fr/en/grants-stand-alone-tool-or-complement-loans",
]


@pytest.mark.parametrize("url", REFUSED_BY_ADDRESS)
def test_md_refuses_documents_news_and_headlines(url):
    assert br.page_problem(br.register_settings("md"), url)


@pytest.mark.parametrize("url", ACCEPTED)
def test_md_accepts_real_funding_pages(url):
    settings = br.register_settings("md")
    assert br.page_problem(settings, url) is None
    assert br.strong_path(settings, url)


def test_md_accepts_every_known_funders_page():
    settings = br.register_settings("md")
    known = br.known_funders("md", settings["dedupe_known_by_site"])
    assert len(known) >= 20
    assert [k["url"] for k in known if br.page_problem(settings, k["url"])] == []


def md_funding_links(links, home="https://fond.example/"):
    return br.funding_links(br.register_settings("md"), {"url": home, "links": links})


@pytest.mark.parametrize("home, href, text", [
    ("https://www.gov.uk/world/organisations/british-embassy-chisinau",
     "https://www.gov.uk/emergency-travel-document", "Apply for an emergency travel document"),
    ("https://www.victoriabank.md/", "https://www.victoriabank.md/publicarea-informatiei/"
     "cadrul-administrare-capital", "Fonduri proprii și cerințe de capital"),
    ("https://cahul.md/", "https://cahul.md/ro/Direc%C8%9Bia%20general%C4%83%20finan%C8%9Be",
     "Direcția generală finanțe"),
    ("http://www.soroca.org.md/", "http://www.soroca.org.md/index.php/subdiviziunile-cr/"
     "directia-finante", "Direcția finanțe"),
    ("https://www.ebrd.com/", "https://www.ebrd.com/home/comment-on-proposal.html",
     "Comment on proposals"),
    ("https://mediu.gov.md/", "https://mediu.gov.md/ro/acte-pentru-concurs", "Acte pentru concurs"),
    ("https://www.orange.md/", "https://www.orange.md/sprijin", "Sprijin clienți"),
])
def test_md_link_with_one_broad_stem_is_not_a_funding_page(home, href, text):
    # The old rule took each of these: one grant stem in the text or path.
    assert br.pick_links({"links": [(href, text)]}, home, br.register_settings("md")) == [href]
    assert md_funding_links([(href, text)], home) == []


@pytest.mark.parametrize("home, href, text", [
    ("https://www.mott.org/", "https://www.mott.org/grants/", "Grants"),
    ("https://interreg-danube.eu/", "https://interreg-danube.eu/how-to-apply", "How to apply"),
    ("https://erasmusplus.md/", "https://erasmusplus.md/burse-erasmus", "Burse Erasmus"),
    ("https://fond.example/", "https://fond.example/ro/content/12", "Granturi"),
    ("https://fond.example/", "https://fond.example/ro/p/4", "Oportunități de finanțare"),
    ("https://fond.example/", "https://fond.example/ru/p/5", "Гранты"),
    ("https://fond.example/", "https://fond.example/ro/apeluri", "Apel de propuneri"),
])
def test_md_link_that_names_funding_is_kept(home, href, text):
    assert md_funding_links([(href, text)], home) == [href]


def test_a_news_slug_no_longer_ties_with_the_real_grants_page():
    article = ("https://www.maib.md/ro/maib-people/cum-obtineti-o-finantare-mai-rapid-6-lucruri-pe-"
               "care-orice-antreprenor-ar-trebui-sa-le-stie")
    links = [(article, "Cum obțineți o finanțare mai rapid"),
             ("https://www.maib.md/ro/granturi", "Granturi")]
    assert md_funding_links(links, "https://www.maib.md/") == ["https://www.maib.md/ro/granturi"]


@pytest.mark.parametrize("url, name, website, belongs", [
    ("https://master-lux.md/experts-auto/x", "Energy Efficiency Fund of Moldova (FEE)",
     "https://fee.md", False),
    ("https://proeducatie.md/mediatorii", "Fundatia pentru Dezvoltare (FDR)", "", False),
    ("https://thefriendsofmoldova.com/grants/grant-writing-materials/",
     "Community Foundation Moldova", "", False),
    ("https://agepi.gov.md/", "Agency for Innovation and Technology Transfer", "", False),
    ("https://fee.md/ro/granturi", "Energy Efficiency Fund of Moldova (FEE)", "", True),
    ("https://cahul.md/", "Consiliul Raional Cahul", "", True),
    ("http://www.crungheni.md", "Consiliul Raional Ungheni", "", True),
    ("https://civilspace.eu/en/x", "Civil Society Development Foundation Moldova", "", True),
    ("https://www.purcari.wine/", "Purcari Wineries Community Fund", "https://purcari.wine", True),
])
def test_a_site_found_by_search_must_carry_the_funders_name(url, name, website, belongs):
    funder = {"name": name, "website": website}
    assert br.site_is_funders(br.register_settings("md"), url, funder) is belongs


@pytest.mark.parametrize("result, ok", [
    ({"url": "https://md.usembassy.gov/democracy-commission-small-grants-program-2",
      "title": "Democracy Commission Small Grants Program"}, True),
    ({"url": "https://www.greengrants.org/grantseekers/", "title": "Grantseekers"}, True),
    ({"url": "https://fond.example/ro/node/7", "title": "Apel de propuneri 2026"}, True),
    ({"url": "https://md.usembassy.gov/news", "title": "News | U.S. Embassy grants"}, False),
    ({"url": "https://www.gov.uk/emergency-travel-document",
      "title": "Get an emergency travel document"}, False),
    ({"url": "https://www.kas.de/en/web/moldau/about-us", "title": "About us"}, False),
    ({"url": "https://who.int/countries/mda", "title": "Republic of Moldova"}, False),
])
def test_md_search_result_needs_a_strong_word_in_its_path_or_title(result, ok):
    assert br.search_result_is_funding(br.register_settings("md"), result) is ok


def _nz_register_funders():
    path = os.path.join(tenant.PROJECT_ROOT, "platform", "sources", "nz-register.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)["funders"]


def test_nz_link_choice_and_pages_are_unchanged():
    """For every NZ register site, the links offered are its register pages
    and a spread of others; the build's choice is the sweep's own, and no
    NZ register page is refused."""
    from urllib.parse import urlsplit
    settings = br.register_settings("nz")
    funders = _nz_register_funders()
    assert len(funders) > 200
    extra = [("news/grant-recipients", "Grant recipients"), ("apply", "How to apply"),
             ("files/grant-guide.pdf", "Grant guide"), ("2025/06/grants-round-open", "Grants"),
             ("stories/a-very-long-headline-about-our-community-grants-this-year", "Read")]
    for f in funders:
        assert br.page_problem(settings, f["url"]) is None
        home = "{0.scheme}://{0.netloc}/".format(urlsplit(f["url"]))
        links = [(g["url"], g["name"]) for g in funders
                 if urlsplit(g["url"]).netloc == urlsplit(f["url"]).netloc]
        links += [(home + p, t) for p, t in extra]
        page = {"url": home, "links": links}
        assert br.funding_links(settings, page) == rs.pick_links(page, home, {home.rstrip("/")})


# --- A whole md build, every paid call faked -----------------------------------

REAL_TAVILY_SITE, REAL_TAVILY_FIND = br.tavily_site, br.tavily_find

USEMB = "https://md.usembassy.gov"
STRICT_LISTING = {
    "international": [
        claim("USAID Moldova", "https://www.usaid.gov/moldova"), claim("GIZ"),
        claim("Konrad Adenauer Foundation Moldova", "https://www.kas.de/en/web/moldawien"),
        claim("Konrad Adenauer Stiftung Moldova", "https://www.kas.de/en/web/moldawien"),
        claim("UN Women Moldova", "https://moldova.unwomen.org"),
        claim("United Nations Entity for Gender Equality and the Empowerment of Women (UN Women) "
              "Moldova", "https://moldova.unwomen.org"),
        claim("United Nations Children's Fund (UNICEF) Moldova", "https://www.unicef.org/moldova"),
        claim("Japan International Cooperation Agency (JICA) Moldova", "https://www.jica.go.jp/english/"),
        claim("British Embassy Chisinau (UK Government Bilateral Programme)",
              "https://www.gov.uk/world/organisations/british-embassy-chisinau"),
        claim("Chevening / British Embassy Chisinau Small Grants",
              "https://www.gov.uk/world/organisations/british-embassy-chisinau"),
        claim("United States Embassy in Moldova (Democracy Commission Small Grants Program)", USEMB),
        claim("Embassy of the United States in Chisinau (Public Affairs Section grants)", USEMB),
        claim("Visegrad Fund", "https://www.visegradfund.org"),
    ],
    "embassy": [
        claim("U.S. Embassy Chisinau Democracy Commission Small Grants Program", USEMB),
        claim("U.S. Embassy Chisinau Public Affairs Section Small Grants", USEMB),
    ],
    "foundation": [
        claim("MAIB Foundation", "https://maib.md"),
        claim("Charles Stewart Mott Foundation", "https://www.mott.org"),
        claim("Fundatia pentru Dezvoltare (FDR)"), claim("Community Foundation Moldova"),
        claim("Keystone Moldova", "https://www.keystonemoldova.md"),
    ],
    "government": [
        claim("Energy Efficiency Fund of Moldova (FEE)", "https://fee.md"),
        claim("Agency for Innovation and Technology Transfer"),
        claim("Moldovan Social Investment Fund (FISM)"),
    ],
    "council": [claim("Consiliul Raional Cahul"), claim("Gagauzia Executive Committee")],
    "cross-border": [
        claim("Interreg NEXT Danube Region Programme", "https://interreg-danube.eu"),
        claim("Danube Transnational Programme", "https://www.interreg-danube.eu"),
    ],
    "corporate": [
        claim("Moldova Agroindbank (MAIB) Community Grants", "https://www.maib.md"),
        claim("Victoriabank Community Fund", "https://victoriabank.md"),
        claim("Victoriabank Community Program", "https://www.victoriabank.md"),
    ],
}
STRICT_LISTING["international"][-1]["funding_url"] = "https://www.visegradfund.org/grants"
STRICT_EXCLUDED = {"USAID Moldova", "GIZ", "Moldovan Social Investment Fund (FISM)",
                   "Gagauzia Executive Committee"}

GRANT_TEXT = "Granturi și finanțare pentru ONG-uri. " * 20
ABOUT_TEXT = "Despre noi. Contacte. " * 20
MAIB_ARTICLE = ("https://www.maib.md/ro/maib-people/cum-obtineti-o-finantare-mai-rapid-6-"
                "lucruri-pe-care-orice-antreprenor-ar-trebui-sa-le-stie")
US_ANNOUNCES = f"{USEMB}/united-states-announces-substantial-additional-assistance-for-moldova"


def _page(url, text=GRANT_TEXT, links=()):
    return {"url": url, "text": text, "links": list(links)}


# Every page the build may fetch; any other answers 404, as fee.md does.
STRICT_PAGES = {p["url"].rstrip("/"): p for p in [
    _page("https://www.kas.de/en/web/moldawien", "Konrad-Adenauer-Stiftung Moldova. " * 20),
    _page("https://moldova.unwomen.org", links=[(
        "https://moldova.unwomen.org/ro/stories/reportaj/2026/02/de-la-adapost-temporar-la-"
        "sprijin-durabil-femeile-refugiate-construiesc-un-viitor", "Sprijin durabil")]),
    _page("https://www.unicef.org/moldova"),
    _page("https://www.jica.go.jp/english/", ABOUT_TEXT),
    _page("https://www.gov.uk/world/organisations/british-embassy-chisinau", ABOUT_TEXT, [
        ("https://www.gov.uk/emergency-travel-document", "Apply for an emergency travel document")]),
    _page(USEMB, links=[(f"{USEMB}/news", "News"), (US_ANNOUNCES, "United States announces "
                                                    "substantial additional assistance")]),
    _page("https://www.visegradfund.org", ABOUT_TEXT),
    _page("https://www.visegradfund.org/grants"),
    _page("https://maib.md", links=[(MAIB_ARTICLE, "Cum obțineți o finanțare mai rapid"),
                                    ("https://www.maib.md/ro/granturi", "Granturi")]),
    _page("https://www.mott.org", links=[("https://www.mott.org/grants/", "Grants")]),
    _page("https://www.keystonemoldova.md"),
    _page("https://master-lux.md/experts-auto/anvelope", "Energy efficiency: winter tyres. " * 20),
    _page("https://proeducatie.md/mediatorii-comunitari-au-analizat-rezultatele-proiectului-si-"
          "perspectivele-de-continuare-a-sprijinului-pentru-copiii-romi",
          "Fundatia pentru Dezvoltare a sprijinit mediatorii. " * 20),
    _page("https://thefriendsofmoldova.com/grants/grant-writing-materials",
          "Community Foundation grant writing. " * 20),
    _page("https://agepi.gov.md/sites/default/files/bopi/intellectus_04-2006.pdf",
          "Agency for Innovation and Technology Transfer. " * 20),
    _page("https://cahul.md", "Consiliul Raional Cahul. Direcția generală finanțe. " * 20, [
        ("https://cahul.md/ro/Direc%C8%9Bia%20general%C4%83%20finan%C8%9Be",
         "Direcția generală finanțe")]),
    _page("https://interreg-danube.eu", links=[("https://interreg-danube.eu/how-to-apply",
                                                "How to apply")]),
    _page("https://www.victoriabank.md", links=[(
        "https://www.victoriabank.md/publicarea-informatiei/cadrul-administrare-capital",
        "Fonduri proprii și cerințe de capital")]),
]}
STRICT_PAGES["https://victoriabank.md"] = STRICT_PAGES["https://www.victoriabank.md"]
STRICT_PAGES["https://www.interreg-danube.eu"] = STRICT_PAGES["https://interreg-danube.eu"]

# Search results by site, and the site search (no site) by funder name.
STRICT_SEARCH = {
    "unicef.org": [{"url": "https://www.unicef.org/executiveboard/media/1381/file/2002-8-Rev1-"
                           "Board-report-annex-Compendium-of-decisions-2002-EN-ODS.pdf",
                    "title": "Compendium of decisions 2002"}],
    "jica.go.jp": [{"url": "https://www.jica.go.jp/english/overseas/moldova/information/topics/"
                           "2025/1579816_59640.html", "title": "JICA grant aid signed"}],
    "gov.uk": [{"url": "https://www.gov.uk/emergency-travel-document",
                "title": "Get an emergency travel document"}],
    "md.usembassy.gov": [
        {"url": f"{USEMB}/news", "title": "News"},
        {"url": US_ANNOUNCES, "title": "United States announces assistance for Moldova"},
        {"url": f"{USEMB}/democracy-commission-small-grants-program-2",
         "title": "Democracy Commission Small Grants Program"}],
    "keystonemoldova.md": [{"url": "https://www.keystonemoldova.md/wp-content/uploads/sites/4/"
                                   "2026/09/00.CG_KSM_2026_Ghid-de-aplicare-la-CG-RO.pdf",
                            "title": "Ghid de aplicare"}],
    "victoriabank.md": [], "cahul.md": [], "kas.de": [], "moldova.unwomen.org": [],
}
STRICT_SITE_SEARCH = {
    "Energy Efficiency Fund of Moldova (FEE)": "https://master-lux.md/experts-auto/anvelope",
    "Fundatia pentru Dezvoltare (FDR)": "https://proeducatie.md/mediatorii-comunitari-au-"
    "analizat-rezultatele-proiectului-si-perspectivele-de-continuare-a-sprijinului-pentru-"
    "copiii-romi/",
    "Community Foundation Moldova": "https://thefriendsofmoldova.com/grants/grant-writing-"
                                    "materials/",
    "Agency for Innovation and Technology Transfer": "https://agepi.gov.md/sites/default/files/"
                                                     "bopi/intellectus_04-2006.pdf",
    "Consiliul Raional Cahul": "https://cahul.md/",
}


class StrictTavily:
    """Tavily for the whole build: every search is turned away once with a
    429 (too many requests), then answers. Thread-safe: the build resolves
    in a pool."""
    import threading
    lock = threading.Lock()
    seen: dict = {}
    answers = 0

    def __init__(self, api_key):
        pass

    def search(self, query, max_results, include_domains=None):
        key = (query, tuple(include_domains or ()))
        with StrictTavily.lock:
            StrictTavily.seen[key] = StrictTavily.seen.get(key, 0) + 1
            if StrictTavily.seen[key] == 1:
                raise UsageLimitExceededError("Rate limit exceeded. Please slow down.")
            StrictTavily.answers += 1
        if include_domains and "Public Affairs" in query:
            # The embassy's other programme: its search finds no funding page.
            return {"results": [{"url": f"{USEMB}/news", "title": "News"},
                                {"url": f"{USEMB}/america-house", "title": "America House"}]}
        if include_domains:
            return {"results": STRICT_SEARCH.get(include_domains[0], [])}
        name = next((n for n in STRICT_SITE_SEARCH if f'"{n}"' in query), None)
        return {"results": [{"url": STRICT_SITE_SEARCH[name], "title": name}] if name else []}


@pytest.fixture
def strict_build(isolated, paid, monkeypatch, capsys):
    paid.reply = reply_by_category(STRICT_LISTING)
    paid.COST = 0.0
    StrictTavily.seen, StrictTavily.answers = {}, 0
    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=StrictTavily))
    monkeypatch.setattr(br, "tavily_site", REAL_TAVILY_SITE)
    monkeypatch.setattr(br, "tavily_find", REAL_TAVILY_FIND)
    monkeypatch.setattr(rs, "anthropic_ask", lambda system, user: ('{"index": 0}', 0, 0))
    monkeypatch.setattr(rs, "fetch_page", lambda url: STRICT_PAGES.get(
        url.rstrip("/"), {"url": url, "error": "HTTP 404"}))
    resolved = []
    real_resolve = br.resolve

    def resolve(funder, *a, **k):
        resolved.append(funder["name"])
        return real_resolve(funder, *a, **k)
    monkeypatch.setattr(br, "resolve", resolve)
    capsys.readouterr()
    assert br.main(["md", "--confirm", "--budget", "100"]) == 0
    out = capsys.readouterr().out
    register = json.loads((isolated / "md-register.json").read_text())
    return SimpleNamespace(register=register, resolved=resolved, out=out,
                           summary=json.loads(out[out.index('{\n  "candidates"'):]),
                           urls={f["name"]: f["url"] for f in register["funders"]},
                           unresolved={u["name"]: u["error"] for u in register["unresolved"]})


def test_strict_build_merges_new_funders_on_one_site(strict_build):
    merged = strict_build.summary["merged_by_site"]
    expected = [
        "Konrad Adenauer Stiftung Moldova -> Konrad Adenauer Foundation Moldova "
        "(listed site kas.de, same name)",
        "United Nations Entity for Gender Equality and the Empowerment of Women (UN Women) "
        "Moldova -> UN Women Moldova (listed site moldova.unwomen.org, same name)",
        "U.S. Embassy Chisinau Democracy Commission Small Grants Program -> United States "
        "Embassy in Moldova (Democracy Commission Small Grants Program) "
        "(listed site md.usembassy.gov, same name)",
        "U.S. Embassy Chisinau Public Affairs Section Small Grants -> Embassy of the United "
        "States in Chisinau (Public Affairs Section grants) (listed site md.usembassy.gov, "
        "same name)",
        "Moldova Agroindbank (MAIB) Community Grants -> MAIB Foundation (listed site maib.md, "
        "same name)",
        "Victoriabank Community Program -> Victoriabank Community Fund "
        "(listed site victoriabank.md, same name)",
        "Danube Transnational Programme -> Interreg NEXT Danube Region Programme "
        "(funding page on interreg-danube.eu, same page)",
        # A shared host: one page listed for both, and a word in common.
        "Chevening / British Embassy Chisinau Small Grants -> British Embassy Chisinau "
        "(UK Government Bilateral Programme) (listed site gov.uk, shared host, same page)",
    ]
    assert sorted(merged) == sorted(expected)
    for line in expected:
        assert f"[register] merged {line.split(' -> ')[0]!r}" in strict_build.out
    # Merged before resolve: no spend on the second of each pair.
    assert "Konrad Adenauer Stiftung Moldova" not in strict_build.resolved
    assert "Danube Transnational Programme" in strict_build.resolved
    # Two programmes of the U.S. Embassy stay two.
    assert {"United States Embassy in Moldova (Democracy Commission Small Grants Program)",
            "Embassy of the United States in Chisinau (Public Affairs Section grants)"} <= (
        set(strict_build.urls) | set(strict_build.unresolved))
    assert "British Embassy Chisinau (UK Government Bilateral Programme)" in (
        strict_build.unresolved)


def test_strict_build_refuses_the_bad_pages(strict_build):
    urls, unresolved = strict_build.urls, strict_build.unresolved
    # Good pages, by link, guess and search.
    assert urls["Charles Stewart Mott Foundation"] == "https://www.mott.org/grants/"
    assert urls["Visegrad Fund"] == "https://www.visegradfund.org/grants"
    assert urls["MAIB Foundation"] == "https://www.maib.md/ro/granturi"
    assert urls["Interreg NEXT Danube Region Programme"] == "https://interreg-danube.eu/how-to-apply"
    assert urls["United States Embassy in Moldova (Democracy Commission Small Grants Program)"] \
        == f"{USEMB}/democracy-commission-small-grants-program-2"
    # A refused page falls back to the home page when that names funding...
    assert urls["United Nations Children's Fund (UNICEF) Moldova"] == "https://www.unicef.org/moldova"
    assert urls["Keystone Moldova"] == "https://www.keystonemoldova.md"
    assert urls["UN Women Moldova"] == "https://moldova.unwomen.org"
    assert urls["Consiliul Raional Cahul"] == "https://cahul.md"
    assert urls["Victoriabank Community Fund"] == "https://www.victoriabank.md"
    assert urls["Embassy of the United States in Chisinau (Public Affairs Section grants)"] == USEMB
    # ...and is otherwise left unresolved, never kept.
    assert "Japan International Cooperation Agency (JICA) Moldova" in unresolved
    assert "British Embassy Chisinau (UK Government Bilateral Programme)" in unresolved
    assert unresolved["Energy Efficiency Fund of Moldova (FEE)"] \
        == "search found master-lux.md, not the funder's site"
    assert unresolved["Fundatia pentru Dezvoltare (FDR)"] \
        == "search found proeducatie.md, not the funder's site"
    assert unresolved["Community Foundation Moldova"] \
        == "search found thefriendsofmoldova.com, not the funder's site"
    assert unresolved["Agency for Innovation and Technology Transfer"] \
        == "search found agepi.gov.md, not the funder's site"
    settings = br.register_settings("md")
    assert [u for u in urls.values() if br.page_problem(settings, u)] == []


def test_strict_build_retries_429s_and_counts_each_search_once(strict_build):
    assert StrictTavily.answers > 10
    # Every search was turned away once, then answered, and is paid for once.
    assert set(StrictTavily.seen.values()) == {2}
    assert strict_build.summary["tavily_searches"] == StrictTavily.answers
    assert "search failed" not in strict_build.out


def test_strict_build_keeps_the_gate_and_the_exclusions(strict_build):
    from api import sources
    tier_one = {f["name"] for f in strict_build.register["funders"] if f["tier"] == 1}
    assert tier_one == {f["name"] for f in sources.must_appear_funders("md")}
    names = set(strict_build.urls) | set(strict_build.unresolved)
    assert not STRICT_EXCLUDED & names and not STRICT_EXCLUDED & set(strict_build.resolved)


# --- Audit 2: Tavily limits stop the write --------------------------------------

class ForbiddenError(Exception):
    """Named as the tavily SDK's: raised for HTTP 403, 432 and 433."""


def test_a_forbidden_error_is_not_retried_and_stops_later_calls(flaky, no_waiting):
    def forbidden(**kwargs):
        flaky.attempts += 1
        raise ForbiddenError("This request exceeds your plan's set usage limit.")
    flaky.search = staticmethod(forbidden)
    try:
        with pytest.raises(ForbiddenError):
            br.tavily_find("F", "f.example", "granturi", br.Budget(100))
        assert flaky.attempts == 1 and no_waiting == []
        with pytest.raises(br.TavilyStopped):
            br.tavily_site("G", "Moldova", br.Budget(100))
        assert flaky.attempts == 1
    finally:
        del flaky.search


@pytest.mark.parametrize("error", [
    "HTTP 403; extract failed: UsageLimitExceededError",
    "HTTP 403; extract failed: ForbiddenError",
])
def test_a_funder_whose_extract_hit_a_limit_is_retried_later(error, monkeypatch):
    monkeypatch.setattr(rs, "fetch_page", lambda url: {"url": url, "error": error})
    out = br.resolve({"name": "F", "category": "embassy", "tier": 2, "regions": [],
                      "website": "https://f.example/"}, False, br.register_settings("md"),
                     br.Budget(100))
    assert br.retry_later(out)


class GivingUpTavily:
    """Tavily for a two-run build. In `mode` "429" every search for a funder
    named in `bad` is turned away for ever; "429-credits" says the credits
    are gone; "432" raises ForbiddenError; "extract-429" turns away every
    extract. "ok" answers everything. Counts searches and extracts answered."""
    import threading
    lock = threading.Lock()
    mode, bad = "ok", ("Alpha",)
    searches: list = []
    extracts: list = []

    def __init__(self, api_key):
        pass

    def search(self, query, max_results, include_domains=None):
        mode = GivingUpTavily.mode
        if any(b in query for b in GivingUpTavily.bad):
            if mode == "429":
                raise UsageLimitExceededError("Your request has been blocked due to excessive "
                                              "requests. Please reduce rate of requests.")
            if mode == "429-credits":
                raise UsageLimitExceededError("You have used all your credits. Please upgrade "
                                              "your plan.")
            if mode == "432":
                raise ForbiddenError("This request exceeds your plan's set usage limit.")
            if mode == "bad-query":
                raise ValueError("Query is too long")
        with GivingUpTavily.lock:
            GivingUpTavily.searches.append(query)
        if include_domains:
            return {"results": [{"url": f"https://{include_domains[0]}/granturi",
                                 "title": "Granturi"}]}
        return {"results": [{"url": "https://gamma.example/", "title": "Gamma"}]}

    def extract(self, urls):
        if GivingUpTavily.mode == "extract-429":
            raise UsageLimitExceededError(None)
        with GivingUpTavily.lock:
            GivingUpTavily.extracts.append(urls[0])
        return {"results": [{"raw_content": "Granturi pentru ONG-uri. " * 30
                             + "[Granturi](https://beta.example/granturi)"}]}


GIVING_UP_LISTING = {"foundation": [
    claim("Alpha Fund", "https://alpha.example/", tier=2),   # needs a search
    claim("Beta Fund", "https://beta.example/", tier=2),     # needs an extract
    claim("Gamma Fund", "", tier=2),                         # needs a site search
    claim("Delta Fund", "https://delta.example/", tier=2),   # needs nothing paid
]}


@pytest.fixture
def giving_up(isolated, paid, monkeypatch):
    paid.reply = reply_by_category(GIVING_UP_LISTING)
    paid.COST = 0.0
    GivingUpTavily.searches, GivingUpTavily.extracts, GivingUpTavily.mode = [], [], "ok"
    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=GivingUpTavily))
    monkeypatch.setattr(br, "tavily_site", REAL_TAVILY_SITE)
    monkeypatch.setattr(br, "tavily_find", REAL_TAVILY_FIND)
    monkeypatch.setattr(rs, "anthropic_ask", lambda system, user: ('{"index": 0}', 0, 0))
    # One at a time, in listing order, so which funders follow a credits
    # message is known.
    monkeypatch.setattr(rs, "CRAWL_CONCURRENCY", 1)

    def fetch_page(url, use_tavily=True):
        if "beta.example" in url:
            return rs.fetch_blocked(url, "HTTP 403", use_tavily)
        if "gamma.example" in url:
            return {"url": url, "text": "Gamma Fund. Granturi. " * 20, "links": []}
        if "delta.example" in url:
            return {"url": url, "text": "Granturi. " * 20,
                    "links": [("https://delta.example/granturi", "Granturi")]}
        if "alpha.example" in url:
            return {"url": url, "text": "Despre noi. " * 40, "links": []}
        return {"url": url, "error": "HTTP 404"}
    monkeypatch.setattr(rs, "fetch_page", fetch_page)
    return SimpleNamespace(register=isolated / "md-register.json",
                           progress=isolated / "md-register-progress.json")


@pytest.mark.parametrize("mode, gives_up", [
    ("429", {"alpha-fund"}),
    # Out of credits: no later Tavily call is made, so neither Beta's
    # extract nor Gamma's site search is tried; both wait for a re-run.
    ("429-credits", {"alpha-fund", "beta-fund", "gamma-fund"}),
    ("432", {"alpha-fund", "beta-fund", "gamma-fund"}),
    ("extract-429", {"beta-fund"}),
])
def test_tavily_giving_up_keeps_the_register_unwritten_and_a_rerun_pays_only_for_those(
        giving_up, capsys, mode, gives_up):
    GivingUpTavily.mode = mode
    assert br.main(["md", "--confirm", "--budget", "100"]) == 1
    out = capsys.readouterr().out
    assert not giving_up.register.exists()
    saved = set(json.loads(giving_up.progress.read_text())["resolved"])
    assert saved == {"alpha-fund", "beta-fund", "gamma-fund", "delta-fund"} - gives_up
    assert f"{len(gives_up)} funders hit Tavily limits; re-run" in out
    summary = json.loads(out[out.index('{\n  "candidates"'):])
    assert summary["written"] is None and summary["stopped_by_tavily"] == len(gives_up)
    # Tavily healthy again: only the funders it gave up on are paid for.
    GivingUpTavily.mode, GivingUpTavily.searches, GivingUpTavily.extracts = "ok", [], []
    br._out_of_credits.clear()
    assert br.main(["md", "--confirm", "--budget", "100"]) == 0
    out = capsys.readouterr().out
    summary = json.loads(out[out.index('{\n  "candidates"'):])
    paid_for = GivingUpTavily.searches + GivingUpTavily.extracts
    names = {"alpha-fund": "Alpha", "beta-fund": "beta", "gamma-fund": "Gamma"}
    assert {n for k, n in names.items() if any(n in q for q in paid_for)} == {
        names[k] for k in gives_up}
    assert summary["tavily_searches"] + summary["tavily_extracts"] == len(paid_for)
    register = json.loads(giving_up.register.read_text())
    urls = {f["name"]: f["url"] for f in register["funders"]}
    assert urls["Alpha Fund"] == "https://alpha.example/granturi"
    assert urls["Beta Fund"] == "https://beta.example/granturi"
    assert urls["Gamma Fund"] == "https://gamma.example/granturi"
    assert urls["Delta Fund"] == "https://delta.example/granturi"
    assert not giving_up.progress.exists()


# --- Audit 2: skip words hold for search results and guesses ------------------

SKIPPED_RESULTS = [
    ("ro/rezultatele-concursului-de-granturi", "Rezultatele concursului de granturi 2025"),
    ("ro/lista-beneficiarilor-granturi", "Lista beneficiarilor de granturi"),
    ("ro/castigatorii-granturilor", "Câștigătorii granturilor"),
    ("ro/posturi-vacante", "Posturi vacante - finanțare"),
    ("ro/achizitii-publice/granturi", "Achiziții publice"),
    ("ro/bursa-locurilor-de-munca", "Bursa locurilor de muncă"),
    ("ro/bursa-de-valori", "Bursa de valori"),
    ("ro/granturi-inchise", "Granturi închise"),
]


@pytest.mark.parametrize("path, title", SKIPPED_RESULTS)
def test_md_skip_words_refuse_a_search_result_as_they_refuse_a_link(path, title):
    settings = br.register_settings("md")
    url = "https://www.ex.md/" + path
    assert not br.search_result_is_funding(settings, {"url": url, "title": title})
    assert br.funding_links(settings, {"url": "https://www.ex.md/", "links": [(url, title)]}) \
        == []


@pytest.mark.parametrize("path", ["ro/rezultatele-concursului-de-granturi",
                                  "ro/lista-beneficiarilor-granturi", "ro/posturi-vacante",
                                  "ro/granturi-inchise", "ro/bursa-locurilor-de-munca"])
def test_md_a_guessed_page_under_a_skip_word_is_not_taken(path, monkeypatch):
    monkeypatch.setattr(rs, "fetch_page", lambda url: {
        "url": url, "text": "Granturi și finanțare pentru ONG-uri. " * 20, "links": []})
    funder = {"name": "F", "category": "foundation", "tier": 2, "regions": [],
              "website": "https://www.ex.md/", "funding_url": "https://www.ex.md/" + path}
    out = br.resolve(funder, False, br.register_settings("md"), br.Budget(100))
    assert out["resolved_by"] == "home" and out["url"] == "https://www.ex.md/"


# --- Audit 2: shared hosts, and a town hall is not its raion council -----------

def shared_row(name, url, aliases=()):
    return {"name": name, "url": url, "website": url, "aliases": list(aliases)}


@pytest.mark.parametrize("known, c", [
    # A new entry for a known funder on a shared host, by its alias.
    (shared_row("Delegația Uniunii Europene în Republica Moldova",
                "https://www.eeas.europa.eu/delegations/moldova/finan%C8%9Bare-%C8%99i-granturi_ro",
                ["delegation of the european union to moldova"]),
     shared_row("European Union (Delegation to Moldova)",
                "https://www.eeas.europa.eu/delegations/moldova/funding-and-grants_en")),
    (shared_row("Canada Fund for Local Initiatives", "https://www.international.gc.ca/world-monde/"
                "funding-financement/cfli-fcil/moldova.aspx?lang=eng"),
     shared_row("Embassy of Canada Fund for Local Initiatives Moldova",
                "https://www.international.gc.ca/world-monde/funding-financement/"
                "funding_development_projects-financement_projets_developpement.aspx?lang=eng")),
    # The same page, with the owner's alias sharing a word: PNUD and UNDP.
    (shared_row("PNUD Moldova", "https://www.undp.org/moldova",
                ["undp moldova", "united nations development programme moldova"]),
     shared_row("United Nations Development Programme (UNDP) Moldova",
                "https://www.undp.org/moldova")),
    (shared_row("Black Sea Trust for Regional Cooperation", "https://www.gmfus.org/bst-grantmaking"),
     shared_row("Black Sea Trust for Regional Cooperation (BST)",
                "https://www.gmfus.org/black-sea-trust")),
    (shared_row("British Embassy Chisinau (UK Government Bilateral Programme)",
                "https://www.gov.uk/emergency-travel-document"),
     shared_row("Chevening / British Embassy Chisinau Small Grants",
                "https://www.gov.uk/emergency-travel-document")),
])
def test_a_shared_host_merges_the_same_funder(known, c):
    settings = br.register_settings("md")
    assert br.same_funder(settings, [known], c, by_page=True, shared=True) is known


@pytest.mark.parametrize("a, b", [
    (shared_row("Black Sea Trust for Regional Cooperation", "https://www.gmfus.org/bst-grantmaking"),
     shared_row("German Marshall Fund of the United States", "https://www.gmfus.org/")),
    # A name inside a longer one is not enough on a shared host.
    (shared_row("German Marshall Fund", "https://www.gmfus.org/grants"),
     shared_row("German Marshall Fund Black Sea Trust", "https://www.gmfus.org/bst")),
    # Two UN agencies that reached the same generic page of undp.org.
    (shared_row("United Nations Development Programme (UNDP)", "https://www.undp.org/"),
     shared_row("United Nations Population Fund (UNFPA)", "https://www.undp.org/")),
    (shared_row("UN Women Moldova", "https://www.undp.org/moldova/funding"),
     shared_row("UNFPA Moldova", "https://www.undp.org/moldova/funding")),
    # Two Czech embassies on the foreign ministry's host, on one page.
    (shared_row("Embassy of the Czech Republic in Chișinău",
                "https://mzv.gov.cz/chisinau/cz/rozvojova_pomoc/x.html"),
     shared_row("Embassy of the Czech Republic in Kyiv",
                "https://mzv.gov.cz/kyiv/cz/rozvojova_pomoc/x.html")),
])
def test_a_shared_host_does_not_merge_different_bodies(a, b):
    settings = br.register_settings("md")
    assert br.same_funder(settings, [a], b, by_page=True, shared=True) is None


@pytest.mark.parametrize("a, b", [
    ("Consiliul Raional Cahul", "Primăria Cahul"),
    ("Consiliul Raional Orhei", "Primăria Municipiului Orhei"),
    ("Primăria Ungheni", "Consiliul Raional Ungheni"),
])
def test_a_town_hall_and_its_raion_council_are_two_funders(a, b):
    settings = br.register_settings("md")
    assert not br.names_agree(settings, a, b)
    ra, rb = shared_row(a, "https://cahul.md/"), shared_row(b, "https://cahul.md/")
    # Not even on one page of one site.
    assert br.same_funder(settings, [ra], rb, by_page=True) is None


def test_the_same_council_twice_still_merges():
    settings = br.register_settings("md")
    assert br.names_agree(settings, "Primăria Municipiului Bălți", "Primăria Bălți")
    assert br.names_agree(settings, "Consiliul Raional Cahul", "Consiliul Raional Cahul (CR)")


def test_shared_host_merges_in_a_whole_build(isolated, paid, monkeypatch, capsys):
    """The real candidates from the first md build on shared hosts."""
    paid.COST = 0.0
    paid.reply = reply_by_category({"international": [
        claim("European Union (Delegation to Moldova)",
              "https://www.eeas.europa.eu/delegations/moldova_en", tier=2),
        claim("European Union Delegation to Moldova (EU grants)",
              "https://www.eeas.europa.eu/delegations/moldova_en", tier=2),
        claim("United Nations Development Programme (UNDP) Moldova",
              "https://www.undp.org/moldova", tier=2),
        claim("British Embassy Chisinau (UK Government Bilateral Programme)",
              "https://www.gov.uk/world/organisations/british-embassy-chisinau", tier=2),
        claim("Chevening / British Embassy Chisinau Small Grants",
              "https://www.gov.uk/world/organisations/british-embassy-chisinau", tier=2),
        claim("German Marshall Fund of the United States", "https://www.gmfus.org/", tier=2),
    ]})
    monkeypatch.setattr(rs, "fetch_page", lambda url: {
        "url": url, "text": "Grants and funding. " * 20, "links": []})
    assert br.main(["md", "--confirm", "--no-search", "--budget", "100"]) == 0
    out = capsys.readouterr().out
    summary = json.loads(out[out.index('{\n  "candidates"'):])
    merged = summary["merged_by_site"]
    assert ("European Union (Delegation to Moldova) -> Delegația Uniunii Europene în Republica "
            "Moldova (listed site eeas.europa.eu, shared host, same name)") in merged
    assert ("European Union Delegation to Moldova (EU grants) -> Delegația Uniunii Europene în "
            "Republica Moldova (listed site eeas.europa.eu, shared host, same name)") in merged
    # UNDP's own name is already led to PNUD Moldova by the owner's aliases.
    assert ("Chevening / British Embassy Chisinau Small Grants -> British Embassy Chisinau "
            "(UK Government Bilateral Programme) (listed site gov.uk, shared host, same page)") \
        in merged
    names = {f["name"] for f in json.loads((isolated / "md-register.json").read_text())["funders"]}
    # Known funders keep their names; GMF and the Black Sea Trust stay two.
    assert {"Delegația Uniunii Europene în Republica Moldova", "PNUD Moldova",
            "German Marshall Fund of the United States",
            "Black Sea Trust for Regional Cooperation"} <= names
    assert not {"European Union (Delegation to Moldova)",
                "United Nations Development Programme (UNDP) Moldova"} & names


# --- Audit 2: page rules, refined ----------------------------------------------

@pytest.mark.parametrize("path", [
    "/en/media-grants", "/en/programmes/media", "/ro/programe/media-si-comunicare",
    "/en/events-support-grants", "/resources/grants", "/en/contact-grants",
    "/granturi/2026/apel-deschis", "/en/calls/2026/small-grants", "/ro/granturi/2026",
    "/ro/apel-de-propuneri-de-proiecte-pentru-finantarea-organizatiilor-societatii-civile-2026",
    "/ro/content/apel-de-propuneri-pentru-programul-de-granturi-pentru-organizatiile-de-tineret",
    "/en/press-freedom-fund", "/ro/cum-sa-aplici", "/en/request-for-proposals",
    "/en/grants/apply-for-a-grant-to-support-community-organisations-in-rural-areas",
    # A part that starts with a news word but names grants itself.
    "/en/news-and-grants", "/ro/blog-granturi-pentru-ong",
])
def test_md_real_funding_pages_are_not_refused(path):
    assert br.page_problem(br.register_settings("md"), "https://www.example.md" + path) is None


@pytest.mark.parametrize("path", [
    "/wp/2026/09/apel-granturi", "/2026/apel-de-propuneri", "/en/news/call-for-proposals-2026",
    "/ro/noutati/apel-de-propuneri", "/ro/stiri/apel-deschis", "/en/blog/grants",
    "/en/about-us", "/media", "/en/resources", "/contact/embassy-chisinau",
])
def test_md_news_and_dated_pages_are_still_refused(path):
    assert br.page_problem(br.register_settings("md"), "https://www.example.md" + path)


@pytest.mark.parametrize("text", ["Apel de proiecte", "Apeluri de proiecte",
                                  "Fonduri nerambursabile", "Request for proposals",
                                  "Финансовая поддержка"])
def test_md_new_strong_words(text):
    assert br.strong_text(br.register_settings("md"), text)


@pytest.mark.parametrize("text", ["Concursuri", "Конкурсы", "Sprijin financiar", "Apply now",
                                  "Direcția finanțe"])
def test_md_ambiguous_words_are_not_strong(text):
    # "Concursuri" and "Конкурсы" are as often job competitions as calls.
    assert not br.strong_text(br.register_settings("md"), text)


def test_md_cum_sa_aplici_is_a_funding_path():
    assert br.strong_path(br.register_settings("md"), "https://www.example.md/ro/cum-sa-aplici")


# --- Audit 2: a site found by search must really be the funder's --------------

@pytest.mark.parametrize("url, name", [
    ("https://www.developmentaid.org/donors/view/123",
     "United Nations Development Programme (UNDP) Moldova"),
    ("https://www.europeanfundingguide.eu/x", "European Endowment for Democracy"),
    ("https://fundsforngos.org/x", "Moldova Agroindbank (MAIB) Community Grants"),
    ("https://rural.md/x", "National Agency for Rural Development (AIPA)"),
    ("https://www.internationalgrants.org/x", "International Renaissance Foundation"),
    ("https://www.womenfund.org/x", "UN Women Moldova"),
    ("https://proeducatie.md/x", "Fundatia pentru Dezvoltare (FDR)"),
    ("https://master-lux.md/x", "Energy Efficiency Fund of Moldova (FEE)"),
    ("https://coffee.md/x", "Energy Efficiency Fund of Moldova (FEE)"),
])
def test_md_a_generic_word_or_an_aggregator_is_not_the_funders_site(url, name):
    assert not br.site_is_funders(br.register_settings("md"), url, {"name": name, "website": ""})


@pytest.mark.parametrize("url, name, website", [
    ("https://cahul.md/", "Consiliul Raional Cahul", ""),
    ("http://www.crungheni.md", "Consiliul Raional Ungheni", ""),
    ("https://fee.md/ro/granturi", "Energy Efficiency Fund of Moldova (FEE)", ""),
    ("https://civilspace.eu/en/x", "Civil Society Development Foundation Moldova", ""),
    ("https://democracyendowment.eu/support", "European Endowment for Democracy", ""),
    ("https://www.developmentaid.org/x", "Some Fund", "https://developmentaid.org/"),
])
def test_md_the_funders_own_site_is_found(url, name, website):
    assert br.site_is_funders(br.register_settings("md"), url, {"name": name, "website": website})


# --- Audit 5: search errors, Tavily's stop, sites, pages, merges ------------------

def test_a_search_error_that_is_not_a_limit_is_nothing_found_and_the_register_is_written(
        giving_up, capsys):
    """A query Tavily rejects fails the same way every run; before, it kept
    the register unwritten for good and was reported as a Tavily limit."""
    GivingUpTavily.mode = "bad-query"
    assert br.main(["md", "--confirm", "--budget", "100"]) == 0
    out = capsys.readouterr().out
    assert giving_up.register.exists() and "hit Tavily limits" not in out
    assert "search failed for Alpha Fund: ValueError: Query is too long; taken as nothing found" in out
    summary = json.loads(out[out.index('{\n  "candidates"'):])
    assert summary["stopped_by_tavily"] == 0


@pytest.mark.parametrize("error, limited", [
    (ValueError("cannot read the model's pick"), False), (KeyError("results"), False),
    (type("UsageLimitExceededError", (Exception,), {})("Too many requests"), True),
    (type("ForbiddenError", (Exception,), {})("plan limit"), True),
    (br.TavilyStopped("stopped"), True),
])
def test_only_a_tavily_limit_leaves_a_funder_for_a_rerun(monkeypatch, error, limited):
    monkeypatch.setattr(rs, "fetch_page", lambda url: {"url": url, "error": "HTTP 404"})

    def raises(*a, **k):
        raise error
    monkeypatch.setattr(br, "tavily_site", raises)
    funder = {"name": "F", "category": "foundation", "tier": 2, "regions": [],
              "website": "https://f.example/"}
    out = br.resolve(funder, True, br.register_settings("md"), br.Budget(100))
    assert bool(out.get("search_failed")) is limited and br.retry_later(out) is limited
    br._out_of_credits.clear()


def test_once_tavily_stops_no_extract_is_tried(monkeypatch):
    """After a ForbiddenError, a page that refuses a plain fetch stays
    refused: the builder's fetch asks the sweep's for no extract."""
    asked = []

    def fetch_page(url, use_tavily=True):
        asked.append(use_tavily)
        return {"url": url, "error": "HTTP 403"}
    monkeypatch.setattr(rs, "fetch_page", fetch_page)
    br._out_of_credits.clear()
    br.fetch("https://a.example/", br.Budget(10))
    br._out_of_credits.set()
    br.fetch("https://b.example/", br.Budget(10))
    br._out_of_credits.clear()
    assert asked == [True, False]


def test_an_extract_429_that_says_credits_are_used_up_stops_tavily(monkeypatch):
    """The sweep's fetch keeps the extract's message beside its error, so a
    429 for credits used up is told from one for too many requests."""
    class UsageLimitExceededError(Exception):
        pass

    class Tavily:
        calls = 0

        def __init__(self, api_key=None):
            pass

        def extract(self, urls):
            Tavily.calls += 1
            raise UsageLimitExceededError("You have used all your credits. Please upgrade your plan.")
    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=Tavily))
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setattr(rs, "fetch_page", lambda url, use_tavily=True: rs.fetch_blocked(
        url, "HTTP 403", use_tavily))
    waits = []
    monkeypatch.setattr(br, "_pause", waits.append)
    br._out_of_credits.clear()
    failed = []
    page = br.fetch("https://a.example/", br.Budget(10), failed)
    assert br._out_of_credits.is_set() and waits == [] and failed == ["extract"]
    assert page["error"] == "HTTP 403; extract failed: UsageLimitExceededError"
    br.fetch("https://b.example/", br.Budget(10))
    assert Tavily.calls == 1
    br._out_of_credits.clear()


# From the audit of the first paid md build: real funders' own sites.
FUNDERS_OWN_SITES = [
    ("Consiliul Raional Orhei", "https://or.md/"),                      # trusted_hosts
    ("Consiliul Raional Ialoveni", "https://il.md/"),                   # trusted_hosts
    ("Bureau of Interethnic Relations", "https://bri.gov.md/"),         # trusted_hosts
    ("Agency for Interethnic Relations (Agenția Relații Interetnice)", "https://bri.gov.md/"),
    ("Ministerul Muncii și Protecției Sociale", "https://msmps.gov.md/"),   # trusted_hosts
    ("UN Women Moldova", "https://moldova.unwomen.org/"),
    ("Heinrich Boll Stiftung Moldova", "https://md.boell.org/"),
    ("Heinrich Böll Stiftung", "https://www.boell.de/"),
    ("Energy Efficiency Fund of Moldova (FEE)", "https://fee.md/"),
    ("National Employment Agency (ANOFM)", "https://anofm.md/"),
    ("United Nations Children's Fund (UNICEF) Moldova", "https://www.unicef.org/moldova"),
    ("World Health Organization (WHO) Moldova", "https://www.who.int/moldova"),
    ("Consiliul Raional Ștefan Vodă", "https://stefan-voda.md/"),
    ("Consiliul Raional Ștefan Vodă", "https://stefanvoda.md/"),
    ("Consiliul Raional Hîncești", "https://hincesti.md/"),
    ("Primăria Municipiului Chișinău", "https://www.chisinau.md/"),
    ("Moldcell Foundation", "https://www.moldcell.md/"),
    ("Cancelaria de Stat", "https://cancelaria.gov.md/"),
    ("National Agency for Interethnic Relations (Agenția Națională Relații Interetnice)",
     "https://anri.gov.md/"),                                           # bracketed acronym
]

NOT_THE_FUNDERS_SITES = [
    ("Energy Efficiency Fund of Moldova (FEE)", "https://feedback.md/"),
    ("Consiliul Raional Cahul", "https://cahulexpress.md/"),
    ("Consiliul Raional Orhei", "https://orheiinfo.md/"),
    ("Consiliul Raional Ungheni", "https://ungheni.info/"),
    ("Friedrich Ebert Stiftung Moldova", "https://ebertfans.md/"),
    ("Fundatia Soros Moldova", "https://soros-news.md/"),
    ("Moldcell Foundation", "https://moldcellnews.md/"),
    ("UN Women Moldova", "https://womenfund.md/"),
    ("UN Women Moldova", "https://www.un.org/"),
    ("European Endowment for Democracy", "https://www.ned.org/"),
]


@pytest.mark.parametrize("name, url", FUNDERS_OWN_SITES)
def test_md_a_funders_own_short_or_acronym_site_is_its_own(name, url):
    assert br.site_is_funders(br.register_settings("md"), url, {"name": name, "website": ""})


@pytest.mark.parametrize("name, url", NOT_THE_FUNDERS_SITES)
def test_md_a_site_that_only_starts_with_the_name_is_not_the_funders(name, url):
    assert not br.site_is_funders(br.register_settings("md"), url, {"name": name, "website": ""})


@pytest.mark.parametrize("path", [
    "https://www.soros.md/ro/concursuri/2026/apel-de-propuneri-pentru-ong-uri",
    "https://tineret.gov.md/ro/programe/2026/granturi",
    "https://example.md/about-us/grants",
    "https://example.md/ro/despre-noi/granturi",
    "https://example.md/ro/finantare/apel-deschis-pentru-organizatiile-societatii-civile-din-"
    "regiunea-de-dezvoltare-nord",
    "https://example.md/en/call-for-applications-2026-small-grants-for-civil-society-"
    "organisations-in-moldova",
])
def test_md_a_round_a_section_or_a_calls_title_is_a_funding_page(path):
    assert br.page_problem(br.register_settings("md"), path) is None


@pytest.mark.parametrize("path", [
    "https://example.md/2026/granturi", "https://example.md/ro/posts/2026/apel",
    "https://www.chisinau.md/ro/2026/05/concurs-de-proiecte-pentru-ong-uri",
    "https://example.md/en/news/2026/call-for-proposals", "https://example.md/ro/despre-noi",
    "https://example.md/en/about-us/team", "https://example.md/ro/noutati/apel-de-propuneri",
])
def test_md_dated_news_and_about_pages_without_a_funding_part_are_still_refused(path):
    assert br.page_problem(br.register_settings("md"), path)


def test_a_candidate_on_a_known_town_halls_site_that_is_its_council_is_not_merged(
        isolated, paid, monkeypatch, capsys):
    """Consiliul Municipal Bălți on balti.md is not Primăria Municipiului
    Bălți, whose site it is; and a known funder keeps its own category."""
    paid.COST = 0.0
    paid.reply = reply_by_category({
        "council": [claim("Consiliul Municipal Bălți", "https://www.balti.md/", tier=2)],
        "foundation": [claim("Delegația Uniunii Europene în Republica Moldova",
                             "https://www.eeas.europa.eu/delegations/moldova_en", tier=2)],
        "international": [claim("National Endowment for Democracy", "https://www.ned.org/",
                                tier=2)],
    })
    monkeypatch.setattr(rs, "fetch_page", lambda url: {
        "url": url, "text": "Granturi și finanțare. " * 20, "links": []})
    assert br.main(["md", "--confirm", "--no-search", "--budget", "100"]) == 0
    out = capsys.readouterr().out
    summary = json.loads(out[out.index('{\n  "candidates"'):])
    assert not [m for m in summary["merged_by_site"] if m.startswith("Consiliul Municipal")]
    funders = {f["name"]: f for f in json.loads((isolated / "md-register.json").read_text())["funders"]}
    assert {"Consiliul Municipal Bălți", "Primăria Municipiului Bălți"} <= set(funders)
    assert funders["Delegația Uniunii Europene în Republica Moldova"]["category"] == "international"
    assert funders["National Endowment for Democracy"]["category"] == "foundation"
    assert funders["Primăria Municipiului Bălți"]["category"] == "council"
