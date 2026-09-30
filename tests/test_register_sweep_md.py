"""Register sweep for Moldova: the `sweep` block of md.json at work.

Country words in the crawl, Cyrillic-safe keys, ş/ș folding, currencies and
Moldovan figures, the Romanian prompt, and a simulated sweep over five real
funders' addresses and one invented Russian-language site. The fetch and the
model are fakes; nothing leaves the machine.
"""

import json
import os
import shutil
from datetime import datetime, timezone

import pytest

import api.tenant as tenant
from api import published
from api.country_config import clear_config_cache, get_country_config
from orchestrator import register_sweep as rs
from orchestrator import register_words as rw

NOW = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
FILLER = "Informații despre activitatea instituției și echipa noastră. " * 8


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setattr(tenant, "PLATFORM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("GOBUGA_COUNTRY", "md")
    monkeypatch.setattr(rs, "PER_HOST_DELAY_S", 0)
    sources = tmp_path / "config" / "sources"
    sources.mkdir(parents=True)
    for country in ("md", "nz"):
        shutil.copy(os.path.join(tenant.PROJECT_ROOT, "platform", "sources", f"{country}.json"),
                    sources / f"{country}.json")
    clear_config_cache()
    yield sources
    clear_config_cache()


def md():
    return get_country_config("md")


def funder(name="Fundația Est-Europeană", url="https://eef.md/ro/oportunitati-de-finantare/333",
           tier=1, regions=("national",)):
    return {"name": name, "url": url, "tier": tier, "category": "foundation",
            "regions": list(regions)}


def item(**kw):
    base = {"title": None, "deadline_state": "unknown", "deadline": None, "deadline_excerpt": None,
            "amount_min": None, "amount_max": None, "currency": None, "amount_excerpt": None,
            "eligibility": None, "eligibility_excerpt": None, "regions": [], "tags": [],
            "summary": ""}
    base.update(kw)
    return base


def check(page_text, cfg=None, f=None, **kw):
    page = {"url": "https://eef.md/ro/apel/1", "text": page_text}
    return rs.check_programme(item(**kw), page, f or funder(), cfg or md(), TODAY, NOW.isoformat())


# --- Config ------------------------------------------------------------------------

def test_nz_has_no_sweep_block_and_md_has_calarasi():
    assert get_country_config("nz").sweep == {}
    assert rs.country_rules(get_country_config("nz")) == {}
    assert "calarasi" in md().regions and md().regions[-1] == "national"
    from api.fit import parse_fit_params
    assert parse_fit_params({"region": "Călărași"}, "md").region == "calarasi"


# --- Piece 1: country words in the crawl ----------------------------------------------

RU_GRANTS = ("Гранты для общественных организаций\nПрием заявок на гранты до 15 октября 2026. "
             "Финансирование проектов местных НКО. " + "Подробнее о нашей работе. " * 20)


def test_a_russian_page_passes_the_md_filter_but_not_the_english_one():
    settings = rw.register_settings("md")
    assert rw.has_grant_words(settings, RU_GRANTS)
    assert not rs.GRANT_WORDS.search(RU_GRANTS)
    ro = "Apel de propuneri pentru finanțarea proiectelor culturale. " + FILLER
    assert rw.has_grant_words(settings, ro.replace("ț", "t").replace("ă", "a"))


def test_md_links_are_chosen_by_romanian_and_russian_words():
    settings = rw.register_settings("md")
    page = {"links": [
        ("https://www.mc.gov.md/ro/content/concursul-de-finantare-a-proiectelor-culturale-2026",
         "Concursul de finanțare a proiectelor culturale 2026"),
        ("https://www.mc.gov.md/ro/content/concurs-ocuparea-functiei-publice-vacante",
         "Concurs pentru ocuparea funcției publice vacante"),
        ("https://www.mc.gov.md/ro/achizitii-publice", "Achiziții publice"),
        ("https://www.mc.gov.md/ro/noutati", "Noutăți"),
        ("https://www.mc.gov.md/ru/%D0%B3%D1%80%D0%B0%D0%BD%D1%82%D1%8B", "Гранты"),
        ("https://www.mc.gov.md/ru/itogi", "Итоги конкурса"),
        ("https://www.mc.gov.md/ro/despre", "Despre minister"),
    ]}
    picked = rw.country_pick_links(page, "https://www.mc.gov.md/", settings, set())
    assert picked == [
        "https://www.mc.gov.md/ro/content/concursul-de-finantare-a-proiectelor-culturale-2026",
        "https://www.mc.gov.md/ru/%D0%B3%D1%80%D0%B0%D0%BD%D1%82%D1%8B"]
    # The English rule finds none of them.
    assert rs.pick_links(page, "https://www.mc.gov.md/", set()) == []


def test_a_home_page_start_reaches_the_call_through_romanian_links():
    pages = {
        "https://www.mc.gov.md/": ("Ministerul Culturii al Republicii Moldova. " + FILLER, [
            ("https://www.mc.gov.md/ro/content/concursul-de-finantare-a-proiectelor-culturale-2026",
             "Concursul de finanțare a proiectelor culturale 2026"),
            ("https://www.mc.gov.md/ro/content/concurs-ocuparea-functiei-publice-vacante",
             "Concurs pentru ocuparea funcției publice vacante")]),
        "https://www.mc.gov.md/ro/content/concursul-de-finantare-a-proiectelor-culturale-2026": (
            "Concurs pentru finanțarea nerambursabilă a proiectelor culturale. " + FILLER, []),
    }

    def fetch(url):
        text, links = pages.get(url, ("", []))
        return {"url": url, "text": text, "links": links} if text else {"url": url, "error": "HTTP 404"}
    out = rs.crawl_funder(funder("Ministerul Culturii", "https://www.mc.gov.md/"), fetch,
                          rw.register_settings("md"))
    assert [p["url"] for p in out["pages"]] == [
        "https://www.mc.gov.md/ro/content/concursul-de-finantare-a-proiectelor-culturale-2026"]
    assert {"url": "https://www.mc.gov.md/", "reached_by": "registered page", "kept": False,
            "why_not": "no grant words"}.items() <= out["visits"][0].items()
    assert out["visits"][1]["reached_by"].startswith('link "Concursul de finanțare')
    # With the English words the same site gives nothing.
    assert rs.crawl_funder(funder("Ministerul Culturii", "https://www.mc.gov.md/"), fetch)["pages"] == []


def test_strict_links_applies_the_register_page_rules_only_when_asked():
    settings = rw.register_settings("md")
    news = "https://eef.md/ro/comunicate/granturi-pentru-comunitati"
    pages = {"https://eef.md/ro/granturi": ("Granturi. " + FILLER, [(news, "Granturi pentru comunități")]),
             news: ("Granturi pentru comunități: apel de propuneri. " + FILLER, [])}

    def fetch(url):
        text, links = pages[url]
        return {"url": url, "text": text, "links": links}
    f = funder(url="https://eef.md/ro/granturi")
    assert news in [p["url"] for p in rs.crawl_funder(f, fetch, settings)["pages"]]
    assert news not in [p["url"] for p in rs.crawl_funder(f, fetch, settings, strict_links=True)["pages"]]


def test_crawl_only_calls_no_model_and_no_tavily(isolated, monkeypatch, capsys):
    (isolated / "md-register.json").write_text(json.dumps({"funders": [
        funder("Ministerul Culturii", "https://www.mc.gov.md/")]}))
    monkeypatch.setenv("TAVILY_API_KEY", "set-but-must-not-be-used")
    calls = []

    def fake_fetch_page(url, use_tavily=True):
        calls.append((url, use_tavily))
        return {"url": url, "text": "Apel de propuneri. " + FILLER, "links": []}

    def no_call(*a, **kw):
        raise AssertionError("no paid call in --crawl-only")
    monkeypatch.setattr(rs, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(rs, "anthropic_ask", no_call)
    import tavily
    monkeypatch.setattr(tavily, "TavilyClient", no_call)
    assert rs.main(["md", "--crawl-only"]) == 0
    assert calls == [("https://www.mc.gov.md/", False)]
    out = capsys.readouterr().out
    assert "READ  https://www.mc.gov.md/" in out and "1 pages would be read" in out
    # And a refused page stays refused without Tavily, key or not.
    assert rs.fetch_blocked("https://x.md/", "HTTP 403", use_tavily=False) == {
        "url": "https://x.md/", "error": "HTTP 403"}


# --- Piece 2: Unicode-safe keys and matching -----------------------------------------------

def test_cyrillic_titles_keep_their_words_in_every_key():
    a, b = "Программа малых грантов", "Прием заявок на гранты до 15 октября 2026"
    assert rs.title_key(a) == "programma malykh grantov"
    assert published.normalise_title(a) == "programma malykh grantov"
    assert rs.title_key(a) != rs.title_key(b) and published.normalise_title(b)
    # Text with no Cyrillic keys as it always has: NZ's macrons are not folded.
    assert rs.title_key("Kōkiri Fund") == "k kiri fund"
    assert published.normalise_title("Kōkiri Fund") == "kokiri fund"
    # Under md's fold_letters, diacritics or none, one key.
    assert rs.title_key("Concurs pentru finanțarea nerambursabilă", True) == \
        rs.title_key("Concurs pentru finantarea nerambursabila", True) == \
        rs.title_key("Concurs pentru finanţarea nerambursabilă", True)


RU_PAGE = ("Фонд развития\nПрограмма малых грантов для молодежи. Прием заявок на гранты до "
           "15 октября 2026. Поддержка местных инициатив. Могут подать заявку общественные "
           "организации Гагаузии. Размер гранта до 50 тыс. леев.\n" + "Подробнее. " * 30)


def ru_row(title, **kw):
    f = funder("Фонд развития Гагаузии", "https://fond.example/ru/", 2, ["gagauzia"])
    return check(RU_PAGE, f=f, title=title, eligibility="Organizații obștești din Găgăuzia.",
                 eligibility_excerpt="Могут подать заявку общественные организации Гагаузии.", **kw)


def test_two_cyrillic_titles_of_one_funder_stay_two_rows_and_one_title_twice_is_one():
    one, _ = ru_row("Программа малых грантов для молодежи")
    two, _ = ru_row("Прием заявок на гранты до 15 октября 2026")
    again, _ = ru_row("Программа малых грантов для молодежи")
    assert one["dedupe_key"] != two["dedupe_key"]
    assert one["dedupe_key"] == "fond-razvitiya-gagauzii|programma-malykh-grantov-dlya-molodezhi|tbc"
    assert len(rs.dedupe_rows([one, two, again])) == 2
    # And on the server, where these rows would once have been merged into one.
    for n, row in enumerate((one, two, again), 1):
        row["id"] = f"OPP-MD-2026-09-{n:04d}"
    rows, _ = published._dedupe([], [one, two, again], "md", "2026-09")
    assert sorted(r["title"] for r in rows) == ["Прием заявок на гранты до 15 октября 2026",
                                                "Программа малых грантов для молодежи"]


def test_cedilla_and_comma_letters_are_one_and_the_page_spelling_is_kept():
    page = ("Concurs pentru finanţarea nerambursabilă a proiectelor culturale\n"
            "Pot participa asociaţiile obşteşti înregistrate în Republica Moldova. " + FILLER)
    row, why = check(page, title="Concurs pentru finanțarea nerambursabilă a proiectelor culturale",
                     eligibility_excerpt="Pot participa asociațiile obștești înregistrate în Republica Moldova.")
    assert why == ""
    assert row["title"] == "Concurs pentru finanţarea nerambursabilă a proiectelor culturale"
    assert row["provenance"]["eligibility"]["excerpt"] == \
        "Pot participa asociaţiile obşteşti înregistrate în Republica Moldova."
    # Typographic quotes too.
    row, why = check("Programul „Diaspora Acasă Reușește” oferă granturi. " + FILLER,
                     title='Programul "Diaspora Acasă Reușește"',
                     eligibility_excerpt='Programul "Diaspora Acasă Reușește" oferă granturi.')
    assert why == "" and row["title"] == "Programul „Diaspora Acasă Reușește”"
    # A page written without diacritics is matched as it is written.
    row, why = check("Concurs pentru finantarea nerambursabila a proiectelor culturale. " + FILLER,
                     title="Concurs pentru finantarea nerambursabila a proiectelor culturale",
                     eligibility_excerpt="Concurs pentru finantarea nerambursabila a proiectelor culturale.")
    assert why == ""


def test_nz_matching_does_not_fold_letters():
    nz = get_country_config("nz")
    row, why = check("Concurs pentru finanţarea proiectelor culturale. " + FILLER, cfg=nz,
                     title="Concurs pentru finanțarea proiectelor culturale xx",
                     eligibility_excerpt="Concurs pentru finanțarea proiectelor culturale.")
    assert row is None


# --- Piece 3: currency and amounts ---------------------------------------------------------

@pytest.mark.parametrize("excerpt, lo, hi, currency, expect", [
    ("Granturi de până la 200.000 lei pentru fiecare proiect.", None, 200000, "MDL", (None, 200000, "MDL")),
    ("Granturi de până la 200 000 lei pentru fiecare proiect.", None, 200000, "MDL", (None, 200000, "MDL")),
    ("Granturi de până la 200\u00a0000 lei pentru fiecare proiect.", None, 200000, "MDL", (None, 200000, "MDL")),
    ("Размер гранта до 50 тыс. леев на один проект.", None, 50000, "MDL", (None, 50000, "MDL")),
    ("Suma maximă a grantului este de 1,5 mil. lei.", None, 1500000, "MDL", (None, 1500000, "MDL")),
    ("Grants of up to EUR 20,000 per project are available.", None, 20000, "EUR", (None, 20000, "EUR")),
    ("Finanțare de la 5 000 până la 20 000 € per proiect.", 5000, 20000, "EUR", (5000, 20000, "EUR")),
    ("Granturi de până la 20 000 de euro pentru ONG-uri.", None, 20000, "EUR", (None, 20000, "EUR")),
    ("Small grants of up to $5,000 for media outlets.", None, 5000, "USD", (None, 5000, "USD")),
    ("Grantul acordat constituie 1.500 lei pentru fiecare participant.", None, 1500, "MDL", (None, 1500, "MDL")),
    # No currency beside the amount: blank, not guessed.
    ("Granturi de până la 20 000 pentru fiecare proiect.", None, 20000, "MDL", (None, None, None)),
    # The model's currency is not the page's: blank, never relabelled.
    ("Grants of up to EUR 20,000 per project are available.", None, 20000, "MDL", (None, None, None)),
    ("Granturi de până la 200.000 lei pentru fiecare proiect.", None, 200000, "EUR", (None, None, None)),
    # A currency the country does not accept.
    ("Grants of up to £20,000 per project are available.", None, 20000, "GBP", (None, None, None)),
    # A bare "$" beside Canadian dollars is not USD.
    ("Contributions of up to $50,000 CAD are available.", None, 50000, "USD", (None, None, None)),
    # "1.500" is fifteen hundred in Moldova, never one and a half thousand.
    ("Grantul acordat constituie 1.500 lei pentru fiecare participant.", None, 1500000, "MDL", (None, None, None)),
    # Over the currency's cap: a fund total.
    ("Bugetul programului este de 3 mil. euro în total.", None, 3000000, "EUR", (None, None, None)),
    # A figure the excerpt does not state.
    ("Granturi de până la 200.000 lei pentru fiecare proiect.", None, 250000, "MDL", (None, None, None)),
])
def test_md_amounts_need_the_currency_beside_them(excerpt, lo, hi, currency, expect):
    rules = rs.country_rules(md())
    assert rs.country_amounts({"amount_min": lo, "amount_max": hi, "currency": currency},
                              excerpt, rules) == expect


def test_a_row_carries_its_verified_currency_or_none():
    page = "Granturi comunitare. Suma grantului: până la 20 000 € per proiect. " + FILLER
    base = dict(title="Granturi comunitare", eligibility_excerpt="Granturi comunitare. Suma grantului:")
    row, _ = check(page, amount_max=20000, currency="EUR",
                   amount_excerpt="Suma grantului: până la 20 000 € per proiect.", **base)
    assert (row["amount_max"], row["currency"]) == (20000, "EUR")
    assert row["provenance"]["amount"]["excerpt"] == "Suma grantului: până la 20 000 € per proiect."
    row, _ = check(page, amount_max=20000, currency="MDL",
                   amount_excerpt="Suma grantului: până la 20 000 € per proiect.", **base)
    assert (row["amount_max"], row["currency"]) == (None, None) and "amount" not in row["provenance"]


def test_nz_amounts_are_unchanged():
    nz = get_country_config("nz")
    page = "Community Grants. Grants of $1.500 are made. Open to all groups in the region. " + FILLER
    row, _ = check(page, cfg=nz, title="Community Grants", amount_max=1500,
                   amount_excerpt="Grants of $1.500 are made.",
                   eligibility_excerpt="Open to all groups in the region.")
    # NZ reads digits as it always has: "1.500" is not 1500 there.
    assert row["amount_max"] is None and row["currency"] == "NZD"


# --- Piece 4: country prompt and text ------------------------------------------------------

def test_md_prompt_is_moldovan_and_romanian():
    prompt = rs.system_prompt(md(), TODAY)
    assert "a Moldovan funder's website" in prompt and "New Zealand" not in prompt
    assert "NZD" not in prompt and '"currency": "MDL" | "EUR" | "USD" or null' in prompt
    assert "always written in Romanian" in prompt and "Subsidies count as grants" in prompt
    assert "calarasi (Călărași)" in prompt and '["transnistria"]' in prompt
    assert rs.system_prompt(get_country_config("nz"), TODAY).startswith(
        "You read one page from a New Zealand funder's website")


def test_general_scheme_is_titled_in_romanian():
    row, _ = check("Organizațiile obștești pot aplica pentru finanțare în orice moment. " + FILLER,
                   title="GENERAL", eligibility_excerpt="Organizațiile obștești pot aplica pentru finanțare")
    assert row["title"] == "Granturi Fundația Est-Europeană"


def test_romanian_eligibility_of_a_russian_page_is_kept():
    row, why = ru_row("Программа малых грантов для молодежи", summary="Granturi mici pentru tineri.")
    assert why == ""
    assert row["eligibility"] == "Organizații obștești din Găgăuzia."
    assert row["provenance"]["eligibility"]["excerpt"].startswith("Могут подать заявку")


@pytest.mark.parametrize("title, kept", [
    ("Linia de credit pentru IMM-uri", False),
    ("Programul de garantare a creditelor", False),
    ("Împrumuturi preferențiale pentru tineri", False),
    ("Кредиты для фермеров", False),
    ("Licitație pentru achiziția de servicii", False),
    ("Тендер на закупку услуг", False),
    ("Subvenții pentru tinerii antreprenori", True),
    ("Granturi pentru acreditarea laboratoarelor", True),
    ("Granturi și credite preferențiale pentru femei", True),
    ("Субсидии для фермеров", True),
])
def test_loans_and_tenders_are_not_grants_but_subsidies_are(title, kept):
    row, why = check(f"{title}. Pot aplica organizațiile înregistrate. " + FILLER, title=title,
                     eligibility_excerpt="Pot aplica organizațiile înregistrate.")
    assert (row is not None) == kept, why


@pytest.mark.parametrize("excerpt", [
    "Cererile se depun pe tot parcursul anului.",
    "Cererile pot fi depuse în orice moment, fără termen limită.",
    "Заявки принимаются в течение всего года.",
    "Заявки принимаются постоянно.",
])
def test_rolling_words_in_romanian_and_russian(excerpt):
    row, _ = check(f"Granturi comunitare. {excerpt} " + FILLER, title="Granturi comunitare",
                   deadline_state="rolling", deadline_excerpt=excerpt)
    assert row["deadline_state"] == "rolling-confirmed"


def test_rolling_still_needs_its_words_in_md():
    row, _ = check("Granturi comunitare. Termenul limită este data de 10 a fiecărei luni. " + FILLER,
                   title="Granturi comunitare", deadline_state="rolling",
                   deadline_excerpt="Termenul limită este data de 10 a fiecărei luni.")
    assert row["deadline_state"] == "not-stated"


def test_transnistria_only_programmes_are_not_published():
    page = ("Granturi pentru ONG-urile din regiunea transnistreană. Pot aplica organizațiile din "
            "stânga Nistrului. Granturi pentru Călărași. Pot aplica organizațiile din raionul "
            "Călărași. " + FILLER)
    row, why = check(page, title="Granturi pentru ONG-urile din regiunea transnistreană",
                     eligibility_excerpt="Pot aplica organizațiile din stânga Nistrului.",
                     regions=["national"])
    assert row is None and why == "only for an area not yet covered"
    row, why = check(page, title="Granturi pentru Călărași", regions=["transnistria"],
                     eligibility_excerpt="Pot aplica organizațiile din raionul Călărași.")
    assert row is None and why == "only for an area not yet covered"
    row, _ = check(page, title="Granturi pentru Călărași", regions=["calarasi"],
                   eligibility_excerpt="Pot aplica organizațiile din raionul Călărași.")
    assert row["region"] == ["calarasi"]


# --- A simulated sweep ---------------------------------------------------------------------

EEF = "https://eef.md/ro/oportunitati-de-finantare/333"
EEF_CALL = "https://eef.md/ro/apel-de-propuneri-granturi-comunitare/412"
ANCD = "https://www.ancd.gov.md/ro/content/apeluri-deschise"
ODA = "https://oda.md/ro/granturi/calendar-granturi"
MC = "https://www.mc.gov.md/"
MC_CALL = "https://www.mc.gov.md/ro/content/concursul-de-finantare-a-proiectelor-culturale-2026"
SOROS = "https://soros.md/concursuri/"
RU = "https://fond-gagauz.example/ru/"
RU_CALLS = "https://fond-gagauz.example/ru/granty"
RU_YOUTH = "https://fond-gagauz.example/ru/granty/molodezh"

SITE = {
    EEF: ("Oportunități de finanțare\nFundația Est-Europeană lansează apeluri de propuneri. " + FILLER, [
        (EEF_CALL, "Apel de propuneri: Granturi pentru inițiative comunitare"),
        ("https://eef.md/ro/rezultatele-concursului-2025", "Rezultatele concursului de granturi 2025"),
        ("https://eef.md/ro/noutati", "Noutăți"),
        ("https://eef.md/ro/despre-noi", "Despre noi")]),
    EEF_CALL: ("Granturi pentru iniţiative comunitare\nFundaţia oferă granturi de până la 20 000 € "
               "pentru proiecte locale. Termenul limită de depunere: 15 noiembrie 2026. Pot aplica "
               "asociaţiile obşteşti din Republica Moldova.\n" + FILLER, []),
    ANCD: ("Apeluri deschise\nConcursul de proiecte pentru mobilitatea cercetătorilor 2026. "
           "Valoarea unui proiect: până la 200.000 lei. Termen-limită: 30 octombrie 2026. Pot "
           "participa organizațiile de cercetare acreditate.\n" + FILLER, []),
    ODA: ("Calendar granturi\nProgramul PARE 1+1: grant de până la 200 000 lei. Cererile se "
          "depun pe tot parcursul anului. Pot aplica migranții și rudele lor de gradul I.\n"
          "Subvenții pentru tinerii antreprenori: până la 1,5 mil. lei. Pot aplica tinerii cu "
          "vârsta de până la 35 de ani.\n"
          "Linia de credit pentru IMM-uri oferă împrumuturi la dobândă redusă. Pot aplica "
          "întreprinderile mici.\n" + FILLER, []),
    MC: ("Ministerul Culturii al Republicii Moldova. " + FILLER, [
        (MC_CALL, "Concursul de finanțare a proiectelor culturale 2026"),
        ("https://www.mc.gov.md/ro/content/concurs-ocuparea-functiei-publice-vacante",
         "Concurs pentru ocuparea funcției publice vacante"),
        ("https://www.mc.gov.md/ro/achizitii-publice", "Achiziții publice")]),
    MC_CALL: ("Concurs pentru finanțarea nerambursabilă a proiectelor culturale\nMinisterul "
              "Culturii anunță concursul. Suma maximă: 300 000 de lei per proiect. Dosarele se "
              "depun până la 20 noiembrie 2026. Pot participa asociațiile obștești din domeniul "
              "culturii.\n" + FILLER, []),
    SOROS: ("Concursuri\nConcurs de granturi pentru mass-media independentă: granturi de până la "
            "$5,000 pentru redacții locale. Pot aplica redacțiile înregistrate în Republica Moldova.\n"
            "Program de granturi pentru educație civică: granturi de până la 20 000 pentru școli. "
            "Pot aplica școlile publice.\n" + FILLER, []),
    RU: ("Фонд развития сообщества Гагаузии. " + "Подробнее о фонде. " * 30, [
        (RU_CALLS, "Гранты"),
        ("https://fond-gagauz.example/ru/novosti", "Новости"),
        ("https://fond-gagauz.example/ru/itogi", "Итоги конкурса")]),
    RU_CALLS: ("Гранты\nПрием заявок на гранты до 15 октября 2026. Могут подать заявку НКО "
               "Гагаузии. Размер гранта до 50 тыс. леев.\nПрограмма малых грантов для молодежи. "
               "Могут подать заявку молодежные организации.\nГранты для НКО Приднестровья. Могут "
               "подать заявку организации Приднестровья.\n" + "Подробнее. " * 30,
               [(RU_YOUTH, "Программа малых грантов для молодежи")]),
    RU_YOUTH: ("Программа малых грантов для молодежи\nЗаявки принимаются до 30 ноября 2026. "
               "Могут подать заявку молодежные организации. Размер гранта до 100 000 леев.\n"
               + "Подробнее. " * 30, []),
}

ANSWERS = {
    EEF_CALL: [item(title="Granturi pentru inițiative comunitare", deadline_state="dated",
                    deadline="2026-11-15", deadline_excerpt="Termenul limită de depunere: 15 noiembrie 2026.",
                    amount_max=20000, currency="EUR",
                    amount_excerpt="Fundaţia oferă granturi de până la 20 000 € pentru proiecte locale.",
                    eligibility="Asociații obștești din Republica Moldova.",
                    eligibility_excerpt="Pot aplica asociațiile obștești din Republica Moldova.",
                    regions=["national"], tags=["community"],
                    summary="Granturi pentru proiecte locale ale comunităților.")],
    ANCD: [item(title="Concursul de proiecte pentru mobilitatea cercetătorilor 2026",
                deadline_state="dated", deadline="2026-10-30",
                deadline_excerpt="Termen-limită: 30 octombrie 2026.", amount_max=200000,
                currency="MDL", amount_excerpt="Valoarea unui proiect: până la 200.000 lei.",
                eligibility_excerpt="Pot participa organizațiile de cercetare acreditate.",
                regions=["national"], tags=["research"], summary="Mobilitatea cercetătorilor.")],
    ODA: [item(title="Programul PARE 1+1", deadline_state="rolling",
               deadline_excerpt="Cererile se depun pe tot parcursul anului.", amount_max=200000,
               currency="MDL", amount_excerpt="Programul PARE 1+1: grant de până la 200 000 lei.",
               eligibility_excerpt="Pot aplica migranții și rudele lor de gradul I.",
               summary="Grant pentru migranți care investesc în afaceri."),
          item(title="Subvenții pentru tinerii antreprenori", amount_max=1500000, currency="MDL",
               amount_excerpt="Subvenții pentru tinerii antreprenori: până la 1,5 mil. lei.",
               eligibility_excerpt="Pot aplica tinerii cu vârsta de până la 35 de ani.",
               summary="Subvenții pentru tineri."),
          item(title="Linia de credit pentru IMM-uri",
               eligibility_excerpt="Pot aplica întreprinderile mici.")],
    MC: [],
    MC_CALL: [item(title="Concurs pentru finanțarea nerambursabilă a proiectelor culturale",
                   deadline_state="dated", deadline="2026-11-20",
                   deadline_excerpt="Dosarele se depun până la 20 noiembrie 2026.",
                   amount_max=300000, currency="MDL",
                   amount_excerpt="Suma maximă: 300 000 de lei per proiect.",
                   eligibility_excerpt="Pot participa asociațiile obștești din domeniul culturii.",
                   tags=["arts"], summary="Finanțare pentru proiecte culturale.")],
    SOROS: [item(title="Concurs de granturi pentru mass-media independentă", amount_max=5000,
                 currency="USD",
                 amount_excerpt="Concurs de granturi pentru mass-media independentă: granturi de până la $5,000 pentru redacții locale.",
                 eligibility_excerpt="Pot aplica redacțiile înregistrate în Republica Moldova.",
                 summary="Granturi pentru presa locală."),
            item(title="Program de granturi pentru educație civică", amount_max=20000, currency="MDL",
                 amount_excerpt="Program de granturi pentru educație civică: granturi de până la 20 000 pentru școli.",
                 eligibility_excerpt="Pot aplica școlile publice.", summary="Educație civică.")],
    RU_CALLS: [item(title="Прием заявок на гранты до 15 октября 2026", deadline_state="dated",
                    deadline="2026-10-15", deadline_excerpt="Прием заявок на гранты до 15 октября 2026.",
                    amount_max=50000, currency="MDL", amount_excerpt="Размер гранта до 50 тыс. леев.",
                    eligibility="ONG-uri din Găgăuzia.",
                    eligibility_excerpt="Могут подать заявку НКО Гагаузии.", regions=["gagauzia"],
                    summary="Granturi pentru ONG-uri din Găgăuzia."),
               item(title="Программа малых грантов для молодежи",
                    eligibility_excerpt="Могут подать заявку молодежные организации.",
                    summary="Granturi mici pentru tineri."),
               item(title="Гранты для НКО Приднестровья", regions=["transnistria"],
                    eligibility_excerpt="Могут подать заявку организации Приднестровья.")],
    RU_YOUTH: [item(title="Программа малых грантов для молодежи", deadline_state="dated",
                    deadline="2026-11-30", deadline_excerpt="Заявки принимаются до 30 ноября 2026.",
                    amount_max=100000, currency="MDL", amount_excerpt="Размер гранта до 100 000 леев.",
                    eligibility_excerpt="Могут подать заявку молодежные организации.",
                    summary="Granturi mici pentru organizațiile de tineret.")],
}

REGISTER = [
    funder("Fundația Est-Europeană", EEF, 1),
    funder("Agenția Națională pentru Cercetare și Dezvoltare", ANCD, 1),
    funder("Organizația pentru Dezvoltarea Antreprenoriatului", ODA, 1),
    funder("Ministerul Culturii", MC, 1),
    funder("Fundația Soros Moldova", SOROS, 2),
    funder("Фонд развития сообщества Гагаузии", RU, 2, ["gagauzia"]),
]


def site_fetch(url):
    if url not in SITE:
        return {"url": url, "error": "HTTP 404"}
    text, links = SITE[url]
    return {"url": url, "text": text, "links": links}


def test_a_simulated_md_sweep(isolated):
    (isolated / "md-register.json").write_text(json.dumps({"funders": REGISTER}, ensure_ascii=False))
    asked, systems = [], set()

    def ask(system, user):
        systems.add(system)
        url = user.split("Page address: ", 1)[1].split("\n", 1)[0]
        asked.append(url)
        return json.dumps({"programmes": ANSWERS.get(url, [])}, ensure_ascii=False), 3000, 400
    report = rs.run("md", budget_usd=1.0, fetch=site_fetch, ask=ask, now=NOW)

    # Pages reached through Romanian and Russian links, results and news skipped.
    assert sorted(asked) == sorted([EEF, EEF_CALL, ANCD, ODA, MC_CALL, SOROS, RU_CALLS, RU_YOUTH])
    assert len(systems) == 1 and "Moldovan" in systems.pop()
    rows = json.load(open(os.path.join(report["run_dir"], "verified.json")))["opportunities"]
    by_title = {r["title"]: r for r in rows}

    # Per-row currency, as the page names it; blank where it names none.
    assert (by_title["Granturi pentru iniţiative comunitare"]["amount_max"],
            by_title["Granturi pentru iniţiative comunitare"]["currency"]) == (20000, "EUR")
    assert by_title["Concursul de proiecte pentru mobilitatea cercetătorilor 2026"]["currency"] == "MDL"
    assert by_title["Concurs de granturi pentru mass-media independentă"]["currency"] == "USD"
    civic = by_title["Program de granturi pentru educație civică"]
    assert (civic["amount_max"], civic["currency"]) == (None, None)
    assert by_title["Subvenții pentru tinerii antreprenori"]["amount_max"] == 1500000
    # Rolling in Romanian; the subsidy kept, the credit line not.
    assert by_title["Programul PARE 1+1"]["deadline_state"] == "rolling-confirmed"
    assert "Linia de credit pentru IMM-uri" not in by_title
    # Romanian summaries passed through, also for the Russian page.
    assert by_title["Прием заявок на гранты до 15 октября 2026"]["summary"] == \
        "Granturi pentru ONG-uri din Găgăuzia."
    # Two Cyrillic programmes stay two; the one seen twice is one row, the dated one.
    ru = [r for r in rows if r["funder"] == "Фонд развития сообщества Гагаузии"]
    assert sorted(r["title"] for r in ru) == ["Прием заявок на гранты до 15 октября 2026",
                                              "Программа малых грантов для молодежи"]
    assert by_title["Программа малых грантов для молодежи"]["deadline"] == "2026-11-30"
    assert len({r["dedupe_key"] for r in rows}) == len(rows)
    # The Transnistria-only programme is not published as national, or at all.
    assert "Гранты для НКО Приднестровья" not in by_title
    assert report["rejected_reasons"] == {"not a grant": 1, "only for an area not yet covered": 1}
    # The home-page start found the Ministry's call.
    assert by_title["Concurs pentru finanțarea nerambursabilă a proiectelor culturale"]["source_url"] == MC_CALL
    # Must-appear coverage: every tier 1 funder has a row.
    assert report["coverage_by_tier"]["1"] == {"funders": 4, "reached": 4, "present": 4, "missing": []}
    assert report["coverage_by_tier"]["2"]["missing"] == []


def test_md_reads_a_page_once_and_skips_funded_projects_and_vacancies(isolated):
    ancd = "https://www.ancd.gov.md/ro/content/apeluri-deschise"
    page = ("Apeluri deschise. Concursul de proiecte 2026. " + FILLER, [
        ("https://ancd.gov.md/ro/content/apeluri-deschise", "Apeluri deschise"),
        ("https://ancd.gov.md/ro/content/proiecte-finantate-1", "Proiecte finanțate"),
        ("https://ancd.gov.md/ro/content/admiterea-la-concurs",
         "Cu privire la admiterea candidaților la concurs pentru funcțiile vacante"),
        ("https://ancd.gov.md/ro/content/anunt-concurs", "Anunț - Concurs"),
        ("https://ancd.gov.md/ro/content/apel-de-proiecte", "Apel de propuneri")])

    def fetch(url):
        # The registered www. address redirects to the bare host.
        return {"url": url.replace("www.", ""), "text": page[0], "links": page[1]}
    out = rs.crawl_all("md", md(), [funder("ANCD", ancd)], fetch)[0]
    # "Anunț - Concurs" is an HR notice on this site, and not followed.
    assert [p["url"] for p in out["pages"]] == [
        "https://ancd.gov.md/ro/content/apeluri-deschise", "https://ancd.gov.md/ro/content/apel-de-proiecte"]
    assert [v["why_not"] for v in out["visits"] if not v["kept"]] == ["already read"]
    assert len(out["visits"]) == 3


def test_a_left_bank_call_in_russian_is_not_published():
    title = ("Фонд Восточная Европа объявляет конкурс грантов в поддержку социального "
             "предпринимательства на левом берегу Днестра")
    row, why = check(f"{title}. Могут подать заявку организации. " + "Подробнее. " * 30,
                     title=title, eligibility_excerpt="Могут подать заявку организации.",
                     regions=["national"])
    assert row is None and why == "only for an area not yet covered"


def test_md_does_not_follow_another_languages_copy_or_a_regulation():
    oda = "https://oda.md/ro/granturi/calendar-granturi"
    links = [("https://oda.md/ru/granty/kalendar-granty", "RU"),
             ("https://oda.md/files/2026/Regulament privind organizarea apelurilor pentru "
              "finan_are nerambursabila.pdf",
              "Regulamentul privind organizarea și desfășurarea apelurilor de finanțare"),
             ("https://oda.md/ro/granturi/crestem-imm-main", "Creștem IMM - granturi"),
             ("https://oda.md/granturi/start-uri", "Granturi pentru start-uri")]

    def fetch(url):
        return {"url": url, "text": "Granturi pentru IMM. " + FILLER,
                "links": links if url == oda else []}
    out = rs.crawl_all("md", md(), [funder("ODA", oda)], fetch)[0]
    assert [p["url"] for p in out["pages"]] == [
        oda, "https://oda.md/granturi/start-uri", "https://oda.md/ro/granturi/crestem-imm-main"]
    # A funder whose page names no language follows every copy.
    assert rs._language("https://oda.md/granturi", {"ro", "ru"}) is None


# --- Sweep audit: currency, Transnistria, translations, words, crawl, budget -------

CFLI = "https://www.international.gc.ca/world-monde/funding-financement/cfli-fcil/moldova.aspx"


@pytest.mark.parametrize("excerpt, page, url, expect", [
    # The Canada Fund writes "$20k" and says CAD elsewhere on the page.
    ("Grants of up to $20k are available.", "Canada Fund for Local Initiatives. All amounts "
     "are in Canadian dollars (CAD).", "https://cfli.example.org/", (None, None, None)),
    # A bare "$" on a .ca site is never USD, whatever the page says.
    ("Grants of up to $20k are available.", "Small grants for local projects.", CFLI,
     (None, None, None)),
    # A bare "$" on a page naming no other dollar is USD, as before.
    ("Grants of up to $20k are available.", "Small grants for local projects.",
     "https://soros.md/x", (None, 20000, "USD")),
    # The excerpt names the US dollar: it stands, even beside CAD elsewhere.
    ("Grants of up to US$20,000 are available.", "Some partners give CAD.",
     "https://cfli.example.org/", (None, 20000, "USD")),
])
def test_a_bare_dollar_is_not_usd_where_the_page_or_site_names_another_dollar(
        excerpt, page, url, expect):
    rules = rs.country_rules(md())
    assert rs.country_amounts({"amount_max": 20000, "currency": "USD"}, excerpt, rules,
                              page, url) == expect


def test_a_cfli_like_page_gives_no_usd_amount():
    page = ("Canada Fund for Local Initiatives in Moldova. Grants of up to $20k are available "
            "for local organisations. All funding is in Canadian dollars.\n" + FILLER)
    row, why = rs.check_programme(
        item(title="Canada Fund for Local Initiatives in Moldova", amount_max=20000,
             currency="USD", amount_excerpt="Grants of up to $20k are available for local organisations.",
             eligibility_excerpt="Grants of up to $20k are available for local organisations."),
        {"url": "https://cfli.example.org/moldova", "text": page}, funder(), md(), TODAY,
        NOW.isoformat())
    assert why == "" and (row["amount_max"], row["currency"]) == (None, None)


def test_another_currency_beside_a_stated_figure_blanks_the_amount():
    rules = rs.country_rules(md())
    assert rs.country_amounts({"amount_max": 20000, "currency": "MDL"},
                              "Granturi de până la 20 000 € (20 000 lei).", rules) == (None, None, None)


@pytest.mark.parametrize("regions, eligibility, kept, row_regions", [
    (["national"], "Pot aplica organizațiile din toată țara, inclusiv din regiunea transnistreană.",
     True, ["national"]),
    (["transnistria", "national"], "Pot aplica organizațiile obștești înregistrate.", True,
     ["national"]),
    (["transnistria"], "Pot aplica organizațiile obștești înregistrate.", False, None),
    (["national"], "Pot aplica doar organizațiile din regiunea transnistreană.", False, None),
    (["national"], "Могут подать заявку организации обоих берегов Днестра, включая Приднестровье.",
     True, ["national"]),
])
def test_transnistria_rejects_only_a_programme_limited_to_it(regions, eligibility, kept, row_regions):
    row, why = check(f"Granturi comunitare. {eligibility} " + FILLER, title="Granturi comunitare",
                     eligibility_excerpt=eligibility, regions=regions)
    assert (row is not None) == kept, why
    if kept:
        assert row["region"] == row_regions


def md_row(title, deadline="2026-10-25", amount=None, funder_name="Fundația Est-Europeană"):
    return {"country": "md", "funder": funder_name, "title": title, "deadline": deadline,
            "deadline_state": "dated" if deadline[0].isdigit() else "not-stated",
            "amount_min": None, "amount_max": amount, "currency": "EUR" if amount else None,
            "provenance": {}}


def test_one_call_posted_in_romanian_and_russian_is_one_row():
    ro = md_row("Programul de sprijin al micilor producători", amount=20000)
    ru = md_row("Программа поддержки малых производителей", amount=20000)
    assert rs.dedupe_rows([ru, ro]) == [ro]
    # Different amounts, or two Russian calls and one Romanian on one date: left alone.
    assert len(rs.dedupe_rows([md_row("Программа А", amount=10000),
                               md_row("Programul B", amount=20000)])) == 2
    assert len(rs.dedupe_rows([md_row("Программа А"), md_row("Программа Б"),
                               md_row("Programul C")])) == 3
    # No closing date: not joined.
    assert len(rs.dedupe_rows([md_row("Программа А", "TBC"), md_row("Programul C", "TBC")])) == 2
    # Not for NZ.
    nz = [{**r, "country": "nz"} for r in (ro, ru)]
    assert len(rs.dedupe_rows(nz)) == 2


def test_md_dedupe_folds_diacritics_in_the_title_key():
    a = md_row("Granturi pentru inițiative comunitare", "TBC")
    b = md_row("Granturi pentru initiative comunitare", "TBC")
    assert len(rs.dedupe_rows([a, b])) == 1


@pytest.mark.parametrize("title, kept", [
    ("Programul de susținere pentru achiziția de echipamente", True),
    ("Granturi pentru achiziții de utilaje agricole", True),
    ("Achiziții publice de servicii de consultanță", False),
    ("Anunț privind achiziția publică de lucrări", False),
    ("Государственные закупки услуг", False),
    ("Программа поддержки закупки оборудования", True),
])
def test_buying_equipment_is_a_grant_but_public_procurement_is_not(title, kept):
    row, why = check(f"{title}. Pot aplica organizațiile înregistrate. " + FILLER, title=title,
                     eligibility_excerpt="Pot aplica organizațiile înregistrate.")
    assert (row is not None) == kept, why


def test_crawl_only_asks_the_sweeps_fetch_for_no_extract_on_either_path(isolated, monkeypatch):
    """A refused page (HTTP 403) and a page empty without scripts both reach
    fetch_blocked with use_tavily=False from --crawl-only."""
    import httpx
    (isolated / "md-register.json").write_text(json.dumps({"funders": [
        funder("Refused", "https://refused.example/"), funder("Empty", "https://empty.example/")]}))
    seen = []

    def spy(url, why, use_tavily=True):
        seen.append((url, why, use_tavily))
        return {"url": url, "error": why}

    class Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            request = httpx.Request("GET", url)
            if "refused" in url:
                return httpx.Response(403, request=request)
            return httpx.Response(200, request=request, text="<html><body>tiny</body></html>")
    monkeypatch.setattr(rs, "fetch_blocked", spy)
    monkeypatch.setattr(rs.httpx, "Client", Client)
    monkeypatch.setattr(rs, "assert_public_url", lambda url: None)
    rs.crawl_only("md", now=NOW)
    assert sorted(seen) == [("https://empty.example/", "page has no text without scripts", False),
                            ("https://refused.example/", "HTTP 403", False)]


def test_md_crawl_follows_programme_links_and_drops_hr_notices_and_old_years():
    home = "https://ancd.gov.md/ro/content/apeluri-deschise"
    links = [("https://ancd.gov.md/ro/content/anunt-concurs", "Anunț - Concurs"),
             ("https://ancd.gov.md/ro/content/informatii-despre-concurs", "Informații despre concurs"),
             ("https://ancd.gov.md/ro/content/finantarea-proiectelor-in-derulare", "Proiecte în derulare"),
             ("https://ancd.gov.md/ro/content/program-de-stat-2020-2023", "Program de Stat 2020-2023"),
             ("https://ancd.gov.md/ro/press/concursul-proiectelor-2024-2025",
              "CONCURSUL PROIECTELOR DE INOVARE 2024-2025"),
             ("https://ancd.gov.md/ro/content/programe-de-postdoctorat-2025-2026",
              "Programe de postdoctorat 2025-2026"),
             ("https://ancd.gov.md/ro/content/apel-granturi-2026", "Apel de propuneri 2026")]

    def fetch(url):
        return {"url": url, "text": "Apeluri deschise pentru granturi. " + FILLER,
                "links": links if url == home else []}
    out = rs.crawl_all("md", md(), [funder("ANCD", home)], fetch, NOW)[0]
    assert [p["url"] for p in out["pages"]] == [
        home, "https://ancd.gov.md/ro/content/apel-granturi-2026",
        "https://ancd.gov.md/ro/content/programe-de-postdoctorat-2025-2026",
        "https://ancd.gov.md/ro/press/concursul-proiectelor-2024-2025"]


def test_the_budget_is_a_ceiling_with_calls_in_flight():
    """Six calls at once may not take the spend past the cap: each is held at
    its worst case before it is made."""
    import threading
    budget = rs.Budget(0.2)
    worst = rs.worst_case_usd("s" * 3000, "u" * 20000)
    started, go = [], threading.Event()

    def call():
        if budget.reserve(worst):
            started.append(1)
            go.wait(2)
            budget.add(10000, rs.MAX_ANSWER_TOKENS, reserved=worst)
    threads = [threading.Thread(target=call) for _ in range(6)]
    for t in threads:
        t.start()
    go.set()
    for t in threads:
        t.join()
    assert len(started) == int(0.2 // worst) < 6
    assert budget.spent <= 0.2 and budget.reserved == pytest.approx(0)


def test_extract_page_holds_the_budget_before_asking():
    """Six pages at once against a cap that fits two worst-case calls: only
    two are asked, however slowly they answer."""
    import threading
    cfg, f = md(), funder()
    page = {"url": "https://eef.md/ro/apel/1", "text": "Granturi. " * 2000}
    worst = rs.worst_case_usd(rs.system_prompt(cfg, TODAY),
                              f"Funder: {f['name']}\nPage address: {page['url']}\n\nPage text:\n{page['text']}")
    budget, asked, go = rs.Budget(worst * 2.5), [], threading.Event()

    def ask(system, user):
        asked.append(1)
        go.wait(2)
        return '{"programmes": []}', 1000, 100
    threads = [threading.Thread(target=rs.extract_page,
                                args=(page, f, cfg, budget, ask, TODAY, NOW.isoformat()))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(0.3)
    go.set()
    for t in threads:
        t.join()
    assert len(asked) == 2 and budget.reserved == pytest.approx(0)
