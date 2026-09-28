"""Tailored Picks: an org's sign-up profile read as fit parameters, and the
published rows ranked with the public /fit scorer (api/org_fit.py)."""

import pytest

from api.fit import FitParamError, parse_fit_params, score_fit
from api.org_fit import org_fit_params, rank_for_org, validate_fit_fields

TODAY = "2026-09-29"


def row(n, **kw):
    base = {
        "id": f"OPP-NZ-2026-09-{n:04d}",
        "title": f"Programme {n}",
        "funder": f"Funder {n}",
        "deadline": "2026-12-01",
        "deadline_state": "dated",
        "region": ["national"],
        "tags": ["community"],
        "eligibility": "",
        "summary": "A fund.",
        "status": "live",
        "verified_at": "2026-09-20T00:00:00+00:00",
    }
    base.update(kw)
    return base


# --- Reading the profile -------------------------------------------------------

def test_sector_labels_become_their_slice_tags():
    params = org_fit_params({"sectors": ["Sport & recreation", "Community development & civic"]}, "nz")
    assert params["sector"] == ["civic", "community", "sport"]


def test_an_unknown_sector_label_is_skipped():
    assert "sector" not in org_fit_params({"sectors": ["Underwater basket weaving"]}, "nz")


def test_regions_come_from_country_prefixed_geographies():
    params = org_fit_params({"geographies": ["New Zealand > Bay of plenty", "New Zealand > Canterbury"]}, "nz")
    assert params["region"] == ["bay-of-plenty", "canterbury"]


@pytest.mark.parametrize("geos", [["New Zealand"], ["New Zealand > Otago", "New Zealand"], [], None])
def test_whole_country_or_nothing_puts_no_constraint_on_region(geos):
    assert "region" not in org_fit_params({"geographies": geos}, "nz")


def test_status_size_and_need_are_read_from_the_fit_fields():
    params = org_fit_params({"fit_status": "charitable-trust", "fit_size": "50k-250k",
                             "fit_need": "equipment"}, "nz")
    assert params == {"status": "charitable-trust", "size": "50k-250k", "need": "equipment"}


def test_an_empty_profile_has_no_params():
    assert org_fit_params({}, "nz") == {}


# --- Validating the fields -----------------------------------------------------

def test_validate_accepts_known_values_and_blanks():
    assert validate_fit_fields({"fit_status": "school", "fit_size": "", "other": "x"}, "nz") == {
        "fit_status": "school", "fit_size": ""}


@pytest.mark.parametrize("values", [{"fit_status": "cooperative"}, {"fit_size": "huge"},
                                    {"fit_need": "salaries"}, {"fit_status": "marae"}])
def test_validate_rejects_values_outside_the_vocabulary(values):
    # marae is a New Zealand-only entity, so Moldova rejects it
    country = "md" if values.get("fit_status") == "marae" else "nz"
    with pytest.raises(FitParamError):
        validate_fit_fields(values, country)


# --- Ranking --------------------------------------------------------------------

def test_one_region_ranks_exactly_like_public_fit():
    rows = [row(1, tags=["sport"]), row(2, region=["canterbury"]), row(3, region=["otago"]),
            row(4, tags=["arts"])]
    org = {"sectors": ["Sport & recreation"], "geographies": ["New Zealand > Canterbury"]}
    ours = rank_for_org(rows, org, "nz", today=TODAY)["results"]
    public = score_fit(rows, parse_fit_params({"sector": "sport", "region": "canterbury"}, "nz"), today=TODAY)
    assert [(e["row"]["id"], e["score"], e["why"]) for e in ours] == \
           [(e["row"]["id"], e["score"], e["why"]) for e in public]


def test_several_regions_keep_each_grants_best_score():
    rows = [row(1, region=["canterbury"]), row(2, region=["otago"]), row(3, region=["waikato"])]
    org = {"geographies": ["New Zealand > Canterbury", "New Zealand > Otago"]}
    ids = [e["row"]["id"] for e in rank_for_org(rows, org, "nz", today=TODAY)["results"]]
    assert sorted(ids) == ["OPP-NZ-2026-09-0001", "OPP-NZ-2026-09-0002"]  # Waikato-only is out


def test_whole_country_keeps_regional_grants():
    rows = [row(1, region=["canterbury"]), row(2)]
    ids = [e["row"]["id"] for e in rank_for_org(rows, {"geographies": ["New Zealand"]}, "nz",
                                                today=TODAY)["results"]]
    assert sorted(ids) == ["OPP-NZ-2026-09-0001", "OPP-NZ-2026-09-0002"]


def test_a_matching_sector_ranks_first():
    rows = [row(1, tags=["arts"]), row(2, tags=["sport"])]
    results = rank_for_org(rows, {"sectors": ["Sport & recreation"]}, "nz", today=TODAY)["results"]
    assert results[0]["row"]["id"] == "OPP-NZ-2026-09-0002"
    assert results[0]["why"]


def test_closed_rows_are_left_out():
    rows = [row(1), row(2, deadline="2026-01-01")]
    ids = [e["row"]["id"] for e in rank_for_org(rows, {}, "nz", today=TODAY)["results"]]
    assert ids == ["OPP-NZ-2026-09-0001"]


def test_ranking_does_not_depend_on_row_order():
    rows = [row(n, tags=["sport"] if n % 2 else ["arts"], region=["canterbury"] if n % 3 else ["national"])
            for n in range(1, 9)]
    org = {"sectors": ["Sport & recreation"], "geographies": ["New Zealand > Canterbury", "New Zealand > Otago"]}
    a = [e["row"]["id"] for e in rank_for_org(rows, org, "nz", today=TODAY)["results"]]
    b = [e["row"]["id"] for e in rank_for_org(rows[::-1], org, "nz", today=TODAY)["results"]]
    assert a == b


def test_a_stale_saved_value_is_ignored_rather_than_failing():
    rows = [row(1)]
    results = rank_for_org(rows, {"fit_status": "no-longer-a-thing"}, "nz", today=TODAY)["results"]
    assert [e["row"]["id"] for e in results] == ["OPP-NZ-2026-09-0001"]
