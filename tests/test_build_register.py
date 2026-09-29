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
def test_an_extract_that_brings_nothing_is_still_counted(served, extract, sweep_count):
    """A page with no text without scripts is sent to Tavily's extract. The
    build counts every attempt; the sweep's own count is as it was."""
    served.html = "<html><body><p>Loading…</p></body></html>"
    served.extract = extract
    before = rs.SEARCH_CREDITS["extract"]
    settings = br.register_settings("md")
    budget = br.Budget(2.5 * br.TAVILY_PRICE)
    funders = [{"name": f"Funder {n}", "category": "international", "tier": 2, "regions": [],
                "website": f"https://f{n}.example/"} for n in range(5)]
    out = [br.resolve(f, False, settings, budget) for f in funders]
    assert [r["error"] for r in out] == ["no funding page found"] * 3 + [br.BUDGET_REACHED] * 2
    assert budget.extracts == 3 and budget.spent == pytest.approx(3 * br.TAVILY_PRICE)
    assert rs.SEARCH_CREDITS["extract"] - before == 3 * sweep_count


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
        claim("Swiss Agency for Development and Cooperation (SDC)"), claim("Sida"),
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
                       "Sida", "EEA and Norway Grants", "Moldovan Social Investment Fund (FISM)",
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
    ("USAID", True), ("USAID Moldova", True), ("Sida", True), ("SIDA Moldova", True),
    ("GIZ Moldova", True), ("Swiss Agency for Development and Cooperation (SDC)", True),
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
        return {"url": url, "text": "Granturi. " * 40, "links": [], "extract_tried": True}
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
