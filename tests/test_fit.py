"""
Tests for api/fit.py, the deterministic fit scorer behind /fit, /api/v1/fit
and the fit_grants MCP tool.

Pure Python: no backend, no network, no data directory. Country configs are
read from platform/sources/{nz,md}.json in the repo.

Usage:
    .venv/bin/python -m pytest tests/test_fit.py -q
"""

import json
import os
import random
import sys
import time
from datetime import date, timedelta

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from api.fit import (  # noqa: E402
    ENTITY_VOCAB,
    NEEDS,
    SIZE_BANDS,
    WEIGHTS,
    FitParamError,
    FitProfile,
    _ENTITY_LABELS,
    _NEED_LABELS,
    _WHY,
    _format_days,
    parse_eligible_entities,
    parse_fit_params,
    score_fit,
)

TODAY = date(2026, 9, 28)
ALL_BUT = lambda *slugs: [s for s in ENTITY_VOCAB if s not in slugs]  # noqa: E731


def make_row(n=1, **fields):
    """A live, Phase 1 shaped NZ row with neutral values; override per test."""
    row = {
        "id": f"OPP-NZ-2026-09-{n:04d}",
        "country": "nz",
        "title": "Community Grant",
        "funder": "Example Trust",
        "deadline": "rolling",
        "deadline_state": "unresolved",
        "amount_min": None,
        "amount_max": None,
        "currency": "NZD",
        "region": [],
        "tags": [],
        "eligibility": "",
        "summary": "",
        "source_url": f"https://funder.example/{n}",
        "verified_at": "2026-09-01T00:00:00+00:00",
        "eligible_entities": [],
        "status": "live",
    }
    row.update(fields)
    return row


def dated(days):
    return (TODAY + timedelta(days=days)).isoformat()


def fit(rows, country="nz", **params):
    return score_fit(rows, parse_fit_params(params, country), today=TODAY)


def by_id(results):
    return {r["row"].get("id"): r for r in results}


# ---------------------------------------------------------------------------
# Parameters: validation, normalisation, canonical query
# ---------------------------------------------------------------------------

def test_no_params_gives_empty_profile():
    profile = parse_fit_params({}, "nz")
    assert profile == FitProfile(country="nz")
    assert profile.canonical_query() == ""
    assert parse_fit_params(None, "nz").canonical_query() == ""


@pytest.mark.parametrize("raw", [
    "incorporated-society", "incorporated_society", "Incorporated Society",
    "  INCORPORATED-SOCIETY ", "incorporated__society",
])
def test_status_variants_normalise(raw):
    assert parse_fit_params({"status": raw}, "nz").status == "incorporated-society"


def test_status_iwi_hapu_variants():
    for raw in ("iwi-hapu", "iwi/hapu", "Iwi Hapū"):
        assert parse_fit_params({"status": raw}, "nz").status == "iwi-hapu"


def test_unknown_status_names_param_and_lists_allowed():
    with pytest.raises(FitParamError) as exc:
        parse_fit_params({"status": "charity"}, "nz")
    err = exc.value
    assert isinstance(err, ValueError)
    assert err.param == "status" and err.value == "charity"
    assert err.allowed == list(ENTITY_VOCAB)
    assert "status" in str(err) and "incorporated-society" in str(err)


def test_plural_status_is_not_guessed():
    with pytest.raises(FitParamError):
        parse_fit_params({"status": "incorporated societies"}, "nz")


def test_sector_variants_and_order():
    for raw in ("sport,youth", "Youth, Sport", ["sport", "youth"], "sport,,youth,sport"):
        assert parse_fit_params({"sector": raw}, "nz").sector == ("sport", "youth")


def test_sector_accepts_config_label():
    assert parse_fit_params({"sector": "Sport & recreation"}, "nz").sector == ("sport",)
    assert parse_fit_params({"sector": "Tineret și copii"}, "md").sector == ("youth",)


def test_sector_unknown_rejected_with_country_tags():
    with pytest.raises(FitParamError) as exc:
        parse_fit_params({"sector": "sport,cricket"}, "nz")
    assert exc.value.param == "sector" and exc.value.value == "cricket"
    assert "sport" in exc.value.allowed
    # Moldova has no sport tag: validation is per country
    with pytest.raises(FitParamError):
        parse_fit_params({"sector": "sport"}, "md")


@pytest.mark.parametrize("raw,expected", [
    ("canterbury", "canterbury"),
    ("Bay of Plenty", "bay-of-plenty"),
    ("bay_of_plenty", "bay-of-plenty"),
    ("Hawke's Bay", "hawkes-bay"),
    ("Manawatū-Whanganui", "manawatu-whanganui"),
    ("New Zealand", "national"),
    ("nationwide", "national"),
])
def test_region_variants_nz(raw, expected):
    assert parse_fit_params({"region": raw}, "nz").region == expected


def test_region_variants_md():
    assert parse_fit_params({"region": "Chișinău"}, "md").region == "chisinau"
    assert parse_fit_params({"region": "Ștefan Vodă"}, "md").region == "stefan-voda"
    assert parse_fit_params({"region": "Republica Moldova"}, "md").region == "national"


@pytest.mark.parametrize("raw", ["christchurch", "canterbury,otago", "chisinau"])
def test_region_unknown_rejected(raw):
    with pytest.raises(FitParamError) as exc:
        parse_fit_params({"region": raw}, "nz")
    assert exc.value.param == "region" and "canterbury" in exc.value.allowed


def test_size_and_need_variants():
    p = parse_fit_params({"size": "Under 50K", "need": "Equipment"}, "nz")
    assert (p.size, p.need) == ("under-50k", "equipment")
    assert parse_fit_params({"size": "250k–1m"}, "nz").size == "250k-1m"  # en dash
    with pytest.raises(FitParamError) as exc:
        parse_fit_params({"size": "tiny"}, "nz")
    assert exc.value.allowed == list(SIZE_BANDS)
    with pytest.raises(FitParamError) as exc:
        parse_fit_params({"need": "salaries"}, "nz")
    assert exc.value.allowed == list(NEEDS)


def test_empty_values_omitted_and_unknown_keys_ignored():
    p = parse_fit_params({"sector": "", "region": "  ", "status": None,
                          "offset": "20", "limit": "10", "need": "event"}, "nz")
    assert p.canonical_query() == "need=event"


def test_canonical_query_order_and_equivalence():
    a = parse_fit_params({"need": "event", "size": "under-50k", "status": "club",
                          "region": "canterbury", "sector": "youth,sport"}, "nz")
    b = parse_fit_params({"sector": ["Sport & recreation", "YOUTH"], "region": "Canterbury",
                          "status": "Club", "size": "UNDER 50k", "need": " Event "}, "nz")
    expected = "sector=sport,youth&region=canterbury&status=club&size=under-50k&need=event"
    assert a.canonical_query() == b.canonical_query() == expected
    assert a == b


def test_canonical_query_sorts_hand_built_profile():
    assert FitProfile("nz", sector=("youth", "sport")).canonical_query() == "sector=sport,youth"


# ---------------------------------------------------------------------------
# Score components, each in isolation
# ---------------------------------------------------------------------------

def test_sector_component():
    rows = [
        make_row(1, tags=["sport"]),
        make_row(2, tags=["sport", "youth", "arts"]),
        make_row(3, tags=[]),
        make_row(4, tags=["arts"]),
    ]
    res = by_id(fit(rows, sector="sport,youth"))
    assert len(res) == 4  # sector never excludes
    s = {k[-1]: v["score"] for k, v in res.items()}
    assert s["1"] == WEIGHTS["sector_first_tag"]
    assert s["2"] == WEIGHTS["sector_first_tag"] + WEIGHTS["sector_extra_tag"]
    assert s["3"] == 0  # untagged: unknown, not mismatch
    assert s["4"] == WEIGHTS["sector_no_overlap"]
    assert s["2"] > s["1"] > s["3"] > s["4"]
    assert res["OPP-NZ-2026-09-0002"]["why"] == ["sector: Sport & recreation; Youth & children"]
    assert res["OPP-NZ-2026-09-0004"]["why"] == []


def test_region_exact_national_unknown_and_exclusion():
    rows = [
        make_row(1, region=["canterbury"]),
        make_row(2, region=["national"]),
        make_row(3, region=["New Zealand"]),          # synonym of national
        make_row(4, region=[]),                        # unknown
        make_row(5, region=["otago"]),                 # confident mismatch
        make_row(6, region=["otago", "christchurch"]),  # unrecognised name: unknown
        make_row(7, region=["west-coast", "canterbury"]),
    ]
    res = by_id(fit(rows, region="canterbury"))
    ids = {k[-1] for k in res}
    assert ids == {"1", "2", "3", "4", "6", "7"}
    score = lambda n: res[f"OPP-NZ-2026-09-000{n}"]["score"]  # noqa: E731
    assert score(1) == score(7) == WEIGHTS["region_exact"]
    assert score(2) == score(3) == WEIGHTS["region_national"]
    assert score(4) == score(6) == 0
    assert res["OPP-NZ-2026-09-0001"]["why"] == ["Canterbury"]
    assert res["OPP-NZ-2026-09-0002"]["why"] == ["open nationwide"]


def test_region_label_multiword():
    res = fit([make_row(1, region=["bay-of-plenty"])], region="bay-of-plenty")
    assert res[0]["why"] == ["Bay of Plenty"]


def test_region_national_profile_excludes_regional_rows():
    rows = [make_row(1, region=["national"]), make_row(2, region=["otago"]),
            make_row(3, region=[])]
    res = by_id(fit(rows, region="national"))
    assert set(res) == {"OPP-NZ-2026-09-0001", "OPP-NZ-2026-09-0003"}
    assert res["OPP-NZ-2026-09-0001"]["score"] == WEIGHTS["region_exact"]


def test_status_named_boosts_and_explains():
    rows = [make_row(1, eligible_entities=["incorporated-society", "charitable-trust"])]
    res = fit(rows, status="incorporated-society")
    assert res[0]["score"] == WEIGHTS["status_named"]
    assert res[0]["why"] == ["open to incorporated societies"]


def test_status_excluded_when_stated_list_leaves_it_out():
    rows = [make_row(1, eligible_entities=["school"])]
    assert fit(rows, status="club") == []


def test_status_unknown_is_not_mismatch():
    rows = [make_row(1, eligible_entities=[])]
    res = fit(rows, status="informal")
    assert len(res) == 1 and res[0]["score"] == 0 and res[0]["why"] == []


def test_status_broad_list_keeps_row_without_claiming():
    # "Community groups, excluding schools": a statement of who is out
    rows = [make_row(1, eligible_entities=ALL_BUT("school"))]
    res = fit(rows, status="club")
    assert len(res) == 1 and res[0]["score"] == 0 and res[0]["why"] == []
    assert fit(rows, status="school") == []


def test_amount_against_size():
    rows = [
        make_row(1, amount_min=100_000, amount_max=500_000),  # min above under-50k revenue
        make_row(2, amount_max=20_000),
        make_row(3),                                          # unknown amount
    ]
    res = by_id(fit(rows, size="under-50k"))
    assert res["OPP-NZ-2026-09-0001"]["score"] == WEIGHTS["amount_too_large"]
    assert res["OPP-NZ-2026-09-0002"]["score"] == WEIGHTS["amount_fits_size"]
    assert res["OPP-NZ-2026-09-0003"]["score"] == 0
    assert res["OPP-NZ-2026-09-0002"]["why"] == ["grants up to $20,000"]
    assert res["OPP-NZ-2026-09-0001"]["why"] == ["grants from $100,000 to $500,000"]


def test_amount_too_small_depends_on_need():
    # over-1m band: lower bound 1,000,000. A 15,000 max is 1.5% of it.
    rows = [make_row(1, amount_max=15_000)]
    assert fit(rows, size="over-1m", need="capital")[0]["score"] == WEIGHTS["amount_too_small"]
    assert fit(rows, size="over-1m", need="equipment")[0]["score"] == WEIGHTS["amount_fits_size"]


def test_amount_other_currency_not_compared_but_stated():
    rows = [make_row(1, currency="USD", amount_min=5_000, amount_max=5_000)]
    res = fit(rows, size="under-50k")
    assert res[0]["score"] == 0
    assert res[0]["why"] == ["grants of US$5,000"]


def test_amount_contradictory_or_junk_is_unknown():
    rows = [make_row(1, amount_min=50_000, amount_max=10_000),
            make_row(2, amount_min="lots", amount_max=True),
            make_row(3, amount_max=0),
            make_row(4, amount_max=10_000, currency=None)]
    for r in fit(rows, size="under-50k"):
        assert r["score"] == 0 and r["why"] == []


def test_deadline_ordering():
    rows = [
        make_row(1, deadline=dated(20), deadline_state="dated"),
        make_row(2, deadline=dated(100), deadline_state="dated"),
        make_row(3, deadline="rolling", deadline_state="rolling-confirmed"),
        make_row(4, deadline="TBC", deadline_state="unresolved"),
        make_row(5, deadline=dated(3), deadline_state="dated"),
    ]
    res = fit(rows)
    assert [r["row"]["id"][-1] for r in res] == ["1", "2", "3", "4", "5"]
    s = by_id(res)
    assert s["OPP-NZ-2026-09-0001"]["score"] == WEIGHTS["deadline_soon"]
    assert s["OPP-NZ-2026-09-0002"]["score"] == WEIGHTS["deadline_later"]
    assert s["OPP-NZ-2026-09-0003"]["score"] == WEIGHTS["rolling_confirmed"]
    assert s["OPP-NZ-2026-09-0001"]["why"] == ["closes in 20 days"]
    assert s["OPP-NZ-2026-09-0003"]["why"] == ["rolling deadline, confirmed"]
    assert s["OPP-NZ-2026-09-0004"]["why"] == []


def test_deadline_too_close_is_not_boosted_and_says_so():
    rows = [make_row(1, deadline=dated(3), deadline_state="dated"),
            make_row(2, deadline=dated(0), deadline_state="dated"),
            make_row(3, deadline=dated(1), deadline_state="dated")]
    res = by_id(fit(rows))
    for r in res.values():
        assert r["score"] == WEIGHTS["deadline_too_close"]
    assert res["OPP-NZ-2026-09-0001"]["why"] == ["closes in 3 days, likely too soon to apply"]
    assert res["OPP-NZ-2026-09-0002"]["why"] == ["closes today, likely too soon to apply"]
    assert res["OPP-NZ-2026-09-0003"]["why"] == ["closes in 1 day, likely too soon to apply"]


def test_deadline_boundary_at_min_lead_days():
    rows = [make_row(1, deadline=dated(7), deadline_state="dated")]
    assert fit(rows)[0]["score"] == WEIGHTS["deadline_soon"]


def test_closed_and_stale_never_returned():
    rows = [
        make_row(1, status="closed"),
        make_row(2, status="stale"),
        make_row(3, deadline_state="closed"),
        make_row(4, deadline=dated(-1), deadline_state="dated"),  # passed today
        make_row(5),
    ]
    assert [r["row"]["id"] for r in fit(rows)] == ["OPP-NZ-2026-09-0005"]


def test_need_mentioned_in_listing():
    rows = [make_row(1, summary="Funds sports equipment and uniforms."),
            make_row(2, summary="This fund does not fund equipment."),
            make_row(3, summary="Operating grants.")]
    res = by_id(fit(rows, need="equipment"))
    assert res["OPP-NZ-2026-09-0001"]["score"] == WEIGHTS["need_mentioned"]
    assert res["OPP-NZ-2026-09-0001"]["why"] == ["listing mentions equipment"]
    assert res["OPP-NZ-2026-09-0002"]["score"] == 0
    assert res["OPP-NZ-2026-09-0003"]["score"] == 0


def test_no_params_returns_pool_in_default_order():
    rows = [make_row(1), make_row(2, deadline=dated(30), deadline_state="dated"),
            make_row(3, deadline="rolling", deadline_state="rolling-confirmed")]
    assert [r["row"]["id"][-1] for r in fit(rows)] == ["2", "3", "1"]


def test_today_defaults_to_clock():
    res = score_fit([make_row(1)], parse_fit_params({}, "nz"))
    assert len(res) == 1


# ---------------------------------------------------------------------------
# Determinism and tie-breaking
# ---------------------------------------------------------------------------

def _varied_rows(count=60):
    rng = random.Random(7)
    regions = ["canterbury", "otago", "national", "waikato", "christchurch"]
    tags = ["sport", "youth", "arts", "community", "health"]
    rows = []
    for n in range(1, count + 1):
        days = rng.choice([-5, 2, 10, 30, 90, None])
        rows.append(make_row(
            n,
            region=rng.sample(regions, rng.randint(0, 2)),
            tags=rng.sample(tags, rng.randint(0, 3)),
            eligible_entities=rng.choice([[], ["club"], ["school"], ALL_BUT("company")]),
            deadline=dated(days) if days is not None else rng.choice(["rolling", "TBC"]),
            deadline_state="dated" if days is not None else "unresolved",
            amount_max=rng.choice([None, 5_000, 20_000, 200_000]),
            verified_at=rng.choice([None, "2026-09-01T00:00:00Z", "2026-08-01T00:00:00+00:00"]),
        ))
    return rows


def test_shuffled_input_gives_identical_output():
    rows = _varied_rows()
    profile = parse_fit_params({"sector": "sport,youth", "region": "canterbury",
                                "status": "club", "size": "50k-250k"}, "nz")
    expected = json.dumps(score_fit(rows, profile, today=TODAY), sort_keys=True)
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert json.dumps(score_fit(shuffled, profile, today=TODAY), sort_keys=True) == expected


def test_ties_break_on_verified_at_then_id():
    rows = [
        make_row(3, verified_at="2026-09-01T00:00:00Z"),
        make_row(1, verified_at="2026-08-01T00:00:00Z"),
        make_row(2, verified_at="2026-09-01T00:00:00+00:00"),
        make_row(4, verified_at=None),
        make_row(5, verified_at="2026-09-20T00:00:00Z"),
    ]
    assert [r["row"]["id"][-1] for r in fit(rows)] == ["5", "2", "3", "1", "4"]


def test_input_rows_not_mutated():
    rows = _varied_rows(10)
    before = json.dumps(rows, sort_keys=True)
    fit(rows, sector="sport", region="canterbury", status="club", size="under-50k", need="event")
    assert json.dumps(rows, sort_keys=True) == before


# ---------------------------------------------------------------------------
# why[] truthfulness: every reason traceable to a field of its row
# ---------------------------------------------------------------------------

def _supported(reason, row, profile):
    """True if reason is backed by the row (English reason set, NZ config)."""
    ents = row.get("eligible_entities") or []
    regions = row.get("region") or []
    if reason.startswith("open to "):
        return profile.status in ents and _ENTITY_LABELS["en"][profile.status] == reason[8:]
    if reason == "open nationwide":
        return "national" in regions
    if reason.startswith("sector: "):
        labels = {"Sport & recreation": "sport", "Youth & children": "youth",
                  "Arts, culture & heritage": "arts", "Health & wellbeing": "health",
                  "Community development": "community"}
        return all(labels[l] in row["tags"] for l in reason[8:].split("; "))
    if reason.startswith("closes in ") or reason.startswith("closes today"):
        days = (date.fromisoformat(row["deadline"]) - TODAY).days
        word = "today" if days == 0 else f"in {days} day" + ("" if days == 1 else "s")
        return reason.startswith(f"closes {word}") and (
            ("too soon" in reason) == (days < 7))
    if reason.startswith("rolling deadline"):
        return row.get("deadline") == "rolling"
    if reason.startswith("grants up to $"):
        return row.get("amount_max") == int(reason[14:].replace(",", ""))
    if reason.startswith("listing mentions "):
        return reason[17:] in (row.get("summary") or "").lower()
    if reason in {"Canterbury", "Otago", "Waikato"}:
        return reason.lower() in regions and profile.region == reason.lower()
    return False


def test_every_reason_is_supported_by_its_row():
    rows = _varied_rows()
    for r in rows[::3]:
        r["summary"] = "Covers equipment for teams."
    profile = parse_fit_params({"sector": "sport,youth,arts", "region": "canterbury",
                                "status": "club", "need": "equipment"}, "nz")
    results = score_fit(rows, profile, today=TODAY)
    reasons = [(reason, r["row"]) for r in results for reason in r["why"]]
    assert len(reasons) > 50
    for reason, row in reasons:
        assert _supported(reason, row, profile), (reason, row)


def test_moldova_reasons_are_romanian_and_use_row_currency():
    row = make_row(1, country="md", region=["chisinau"], currency="MDL", amount_max=200_000,
                   deadline=dated(21), deadline_state="dated",
                   eligible_entities=["incorporated-society"], tags=["youth"])
    res = score_fit([row], parse_fit_params({"region": "chisinau", "status": "incorporated-society",
                                             "sector": "youth"}, "md"), today=TODAY)
    assert res[0]["why"] == [
        "pot aplica: asociații obștești",
        "Chisinau",
        "domeniu: Tineret și copii",
        "se închide peste 21 de zile",
        "granturi de până la 200.000 lei",
    ]


def test_reason_tables_complete_in_every_language():
    for lang in ("ro", "ru"):
        assert set(_WHY[lang]) == set(_WHY["en"])
        assert set(_ENTITY_LABELS[lang]) == set(ENTITY_VOCAB)
        assert set(_NEED_LABELS[lang]) == set(NEEDS)


@pytest.mark.parametrize("n,ro,ru", [
    (1, "1 zi", "1 день"), (3, "3 zile", "3 дня"), (5, "5 zile", "5 дней"),
    (11, "11 zile", "11 дней"), (21, "21 de zile", "21 день"), (22, "22 de zile", "22 дня"),
    (101, "101 zile", "101 день"),
])
def test_day_plurals(n, ro, ru):
    assert _format_days(n, "ro") == ro
    assert _format_days(n, "ru") == ru


# ---------------------------------------------------------------------------
# parse_eligible_entities
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Incorporated societies and charitable trusts", ["incorporated-society", "charitable-trust"]),
    ("Schools and kura in Otago", ["school"]),
    ("Marae, iwi and hapū", ["marae", "iwi-hapu"]),
    ("Individuals (New Zealand citizens or permanent residents)", ["individual"]),
    ("District councils and territorial authorities", ["local-authority"]),
    ("Sports clubs", ["club"]),
    ("Unincorporated groups", ["informal"]),
    ("Businesses operating in Taranaki", ["company"]),
    ("Young athletes aged 16-23 and their coaches", ["individual"]),
])
def test_parser_named_types(text, expected):
    assert parse_eligible_entities(text, "nz") == expected


@pytest.mark.parametrize("text", [
    "", "   ", None, 42, "Check eligibility criteria on website", "Farmers and lifestylers",
    "Also accepts individual gifts and bequests",  # donor wording, not an applicant
])
def test_parser_nothing_stated(text):
    assert parse_eligible_entities(text, "nz") == []


@pytest.mark.parametrize("text", [
    "Community organisations in Waikato region",
    "Not-for-profit organisations",
    "Registered charities",
    "Non-profit groups only",
    # named types alongside generic wording are examples, not the whole list
    "Community organisations, charities, unincorporated groups, incorporated societies, trusts",
    "Community groups, schools and clubs",
    "Young athletes and teams aged 15-23, coaches (coaching athletes 15-23)",
])
def test_parser_generic_wording_is_unknown(text):
    assert parse_eligible_entities(text, "nz") == []


@pytest.mark.parametrize("text,expected", [
    ("Community groups, excluding schools", ALL_BUT("school")),
    ("Individuals are not eligible.", ALL_BUT("individual")),
    ("Community organisations. Not open to individuals or schools.",
     ALL_BUT("school", "individual")),
    ("Clubs are eligible and individuals are not eligible", ["club"]),
    ("Any not-for-profit group except local councils", ALL_BUT("local-authority")),
])
def test_parser_negation(text, expected):
    assert parse_eligible_entities(text, "nz") == expected


def test_parser_no_false_negation():
    # "no" negates only the types directly after it
    assert parse_eligible_entities("Schools with no outstanding grants and clubs", "nz") == [
        "school", "club"]
    assert parse_eligible_entities(
        "Athletes aged 16-23; must NOT be attending university outside the region", "nz"
    ) == ["individual"]


def test_parser_subgroup_negation_keeps_type():
    text = ("Athletes aged 16-23 training in Western Bay of Plenty; "
            "athletes at university outside WBOP do not qualify")
    assert parse_eligible_entities(text, "nz") == ["individual"]


@pytest.mark.parametrize("text,expected", [
    ("Legally constituted not-for-profit community organisations "
     "(charitable trust or incorporated society); minimum 3 board members",
     ["incorporated-society", "charitable-trust"]),
    ("Must be an incorporated society or charitable trust",
     ["incorporated-society", "charitable-trust"]),
    ("Incorporated entities eligible for NZBN; incorporated under Charitable Trust Act, "
     "Incorporated Societies Act, or Companies Act (not-for-profit)",
     ["incorporated-society", "charitable-trust"]),
    ("Researchers affiliated with NZ institutions", ["individual"]),
])
def test_parser_closures_and_neutral_phrases(text, expected):
    assert parse_eligible_entities(text, "nz") == expected


def test_parser_language_follows_country():
    text = "Pot aplica asociațiile obștești"
    assert parse_eligible_entities(text, "nz") == []
    assert parse_eligible_entities(text, "md") == ["incorporated-society"]
    # MD also reads English donor text
    assert parse_eligible_entities("Schools only", "md") == ["school"]


@pytest.mark.parametrize("text,expected", [
    ("Pot aplica asociațiile obștești și fundațiile înregistrate în Republica Moldova. "
     "Persoanele fizice nu sunt eligibile.", ["incorporated-society", "charitable-trust"]),
    ("Grupuri de inițiativă și ONG-uri locale", []),
    ("Autoritățile publice locale (primăriile) din raioanele Cahul și Leova",
     ["local-authority"]),
    ("Nu sunt eligibile persoanele fizice. Pot aplica ONG-urile.", ALL_BUT("individual")),
    ("Grupurile de inițiativă sunt eligibile, dar companiile nu sunt eligibile", ["informal"]),
    ("Școlile și liceele din raionul Soroca", ["school"]),
    ("Organizații ale societății civile, cu excepția partidelor politice", []),
])
def test_parser_romanian(text, expected):
    assert parse_eligible_entities(text, "md") == expected


@pytest.mark.parametrize("text,expected", [
    ("Общественные объединения и фонды, зарегистрированные в Республике Молдова. "
     "Физические лица не могут подавать заявки.", ["incorporated-society", "charitable-trust"]),
    ("НКО, кроме школ", ALL_BUT("school")),
    ("Некоммерческие организации", []),
    ("Органы местного публичного управления", ["local-authority"]),
    ("Инициативные группы и спортивные клубы", ["club", "informal"]),
])
def test_parser_russian(text, expected):
    assert parse_eligible_entities(text, "md") == expected


def test_legacy_row_without_entities_is_parsed_for_scoring():
    row = make_row(1, eligibility="Schools in Greater Wellington region")
    del row["eligible_entities"]
    assert fit([row], status="club") == []
    assert fit([row], status="school")[0]["why"] == ["open to schools"]


# ---------------------------------------------------------------------------
# Legacy and malformed rows
# ---------------------------------------------------------------------------

def _legacy(n, deadline, **extra):
    row = {"id": f"OPP-NZ-2026-04-{n:04d}", "country": "nz", "title": "Old", "funder": "F",
           "deadline": deadline, "amount_min": None, "amount_max": 5000, "currency": "NZD",
           "region": ["national"], "tags": ["sport"], "eligibility": "Community groups",
           "summary": ""}
    row.update(extra)
    return row


def test_legacy_rows_score_from_deadline_string():
    rows = [_legacy(1, dated(15)), _legacy(2, "rolling"), _legacy(3, "TBC"),
            _legacy(4, dated(-3))]
    res = by_id(fit(rows, sector="sport", region="canterbury", status="club"))
    assert set(res) == {"OPP-NZ-2026-04-0001", "OPP-NZ-2026-04-0002", "OPP-NZ-2026-04-0003"}
    assert "closes in 15 days" in res["OPP-NZ-2026-04-0001"]["why"]
    assert "rolling deadline" in res["OPP-NZ-2026-04-0002"]["why"]
    base = WEIGHTS["sector_first_tag"] + WEIGHTS["region_national"]
    assert res["OPP-NZ-2026-04-0002"]["score"] == base + WEIGHTS["rolling_unconfirmed"]
    assert res["OPP-NZ-2026-04-0003"]["score"] == base


def test_malformed_rows_do_not_raise():
    rows = [
        None, "not a row", 42,
        {"id": None, "title": None},
        {},
        make_row(1, tags="sport", region="Canterbury", eligible_entities="club"),
        make_row(2, tags=[None, 3, "sport"], region=[None], eligible_entities=[None, "bogus"]),
        make_row(3, amount_min="1000", amount_max="20000", verified_at="garbage",
                 deadline=20261010, deadline_state=["dated"], status=["live"]),
        make_row(4, title=None, summary=None, eligibility=None, currency=7),
        make_row(5, deadline="2026-02-31", deadline_state="dated"),
        make_row(6, amount_max=float("inf")),
    ]
    res = fit(rows, sector="sport", region="canterbury", status="club",
              size="under-50k", need="equipment")
    got = by_id(res)
    # string fields where lists are expected are read as one-item lists
    assert "sector: Sport & recreation" in got["OPP-NZ-2026-09-0001"]["why"]
    assert "Canterbury" in got["OPP-NZ-2026-09-0001"]["why"]
    assert "open to clubs" in got["OPP-NZ-2026-09-0001"]["why"]
    assert "grants from $1,000 to $20,000" in got["OPP-NZ-2026-09-0003"]["why"]
    assert score_fit(None, parse_fit_params({}, "nz"), today=TODAY) == []


# ---------------------------------------------------------------------------
# Real-pool shape and performance
# ---------------------------------------------------------------------------

def test_scoring_5000_rows_is_fast():
    rng = random.Random(1)
    rows = []
    for n in range(5000):
        rows.append(make_row(
            n,
            region=[rng.choice(["canterbury", "otago", "national", "waikato"])],
            tags=rng.sample(["sport", "youth", "arts", "community", "health"], 2),
            eligible_entities=rng.choice([[], ["club"], ["school"]]),
            deadline=dated(rng.randint(-10, 120)), deadline_state="dated",
            amount_max=rng.choice([None, 5_000, 50_000]),
            summary=f"Grant {n} for equipment and events in the community.",
            verified_at=f"2026-09-{rng.randint(1, 27):02d}T00:00:00Z",
        ))
    profile = parse_fit_params({"sector": "sport,youth", "region": "canterbury",
                                "status": "club", "size": "under-50k", "need": "equipment"}, "nz")
    start = time.perf_counter()
    res = score_fit(rows, profile, today=TODAY)
    elapsed = time.perf_counter() - start
    assert res
    # Target is well under 100 ms on a laptop; the bound is generous for CI.
    assert elapsed < 0.5, f"scoring 5000 rows took {elapsed:.3f}s"
