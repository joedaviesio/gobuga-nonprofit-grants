"""Tailored Picks: the published grants ranked against one organisation.

The workspace's Tailored tab uses the same deterministic scorer as the public
/fit surface (`api.fit.score_fit`), over the same live published rows. The
only difference is where the five fit parameters come from: here they are
read from the org record the sign-up screen and settings write.

    sector  <- org["sectors"]      sector labels from the sign-up screen
    region  <- org["geographies"]  "New Zealand" or "New Zealand > Canterbury"
    status  <- org["fit_status"]   an entity slug (api.fit.entity_vocab_for)
    size    <- org["fit_size"]     a size band slug (api.fit.SIZE_BANDS)
    need    <- org["fit_need"]     a need slug (api.fit.NEEDS)

An org that works in several regions is scored once per region and each
grant keeps its best score. "The whole country", or no region at all, puts
no constraint on region: it says where the org works, not where it is based,
so it must not rule out a regional fund.

No I/O beyond the country config, no network, no LLM.
"""

from __future__ import annotations

from api.country_config import get_country_config
from api.fit import (
    NEEDS,
    SIZE_BANDS,
    FitParamError,
    FitProfile,
    _as_timestamp,
    _slugify,
    entity_vocab_for,
    parse_fit_params,
    score_fit,
)

# Org record keys for the three parameters the sign-up screen asks for
# directly, by fit parameter name.
FIT_FIELDS = {"status": "fit_status", "size": "fit_size", "need": "fit_need"}


def validate_fit_fields(values: dict, country: str) -> dict:
    """Check `fit_status`, `fit_size` and `fit_need` against the vocabulary.

    Returns the ones present, with "" meaning cleared. Raises FitParamError
    on an unknown value.
    """
    allowed = {"status": entity_vocab_for(country), "size": SIZE_BANDS, "need": NEEDS}
    out = {}
    for param, key in FIT_FIELDS.items():
        if key not in values or values[key] is None:
            continue
        value = str(values[key]).strip()
        if value and value not in allowed[param]:
            raise FitParamError(param, value, allowed[param])
        out[key] = value
    return out


def _sector_tags(labels, country: str) -> list[str]:
    """Tag slugs for the org's sector labels. A label from the country's
    sector list brings every tag of its slice; an unknown label is skipped."""
    cfg = get_country_config(country)
    by_slice = {_slugify(s["label"]): s.get("tags") or [s["id"]] for s in cfg.sector_slices}
    by_label = {_slugify(label): tag for label, tag in cfg.sector_label_to_tag.items()}
    tags = set(cfg.tags)
    out: set[str] = set()
    for label in labels or []:
        key = _slugify(str(label))
        found = by_slice.get(key) or ([by_label[key]] if key in by_label else [])
        if not found and key in tags:
            found = [key]
        out.update(t for t in found if t in tags)
    return sorted(out)


def _regions(geographies, country: str) -> list[str]:
    """Region slugs the org works in; empty for the whole country or none."""
    cfg = get_country_config(country)
    national = {_slugify(s) for s in cfg.national_scope_synonyms} | {_slugify(cfg.country_label)}
    known = {r for r in cfg.regions if r != "national"}
    out: list[str] = []
    for geo in geographies or []:
        place = str(geo).split(">")[-1]
        slug = _slugify(place)
        if slug in national:
            return []
        if slug in known and slug not in out:
            out.append(slug)
    return out


def org_fit_params(org: dict, country: str) -> dict:
    """The org's five fit parameters as slugs, empty ones omitted."""
    params: dict[str, object] = {}
    sector = _sector_tags(org.get("sectors"), country)
    if sector:
        params["sector"] = sector
    regions = _regions(org.get("geographies"), country)
    if regions:
        params["region"] = regions
    for param, key in FIT_FIELDS.items():
        value = (org.get(key) or "").strip()
        if value:
            params[param] = value
    return params


def _profiles(params: dict, country: str) -> list[FitProfile]:
    base = {k: v for k, v in params.items() if k in FIT_FIELDS}
    if params.get("sector"):
        base["sector"] = ",".join(params["sector"])
    profiles = []
    for region in params.get("region") or [None]:
        values = {**base, **({"region": region} if region else {})}
        try:
            profiles.append(parse_fit_params(values, country))
        except FitParamError:
            # A value saved before the vocabulary changed: rank without it.
            values = {k: v for k, v in values.items() if k in ("sector", "region")}
            profiles.append(parse_fit_params(values, country))
    return profiles


def rank_for_org(rows: list[dict], org: dict, country: str, *, today=None) -> dict:
    """Rank `rows` for the org: {"params", "results": [{row, score, why}]}.

    Deterministic: ties break on verified_at (newer first), then id, as in
    `score_fit`.
    """
    params = org_fit_params(org, country)
    best: dict[str, dict] = {}
    for profile in _profiles(params, country):
        for entry in score_fit(rows, profile, today=today):
            key = str(entry["row"].get("id") or "")
            if key not in best or entry["score"] > best[key]["score"]:
                best[key] = entry

    def order(entry):
        ts = _as_timestamp(entry["row"].get("verified_at"))
        return (-entry["score"], ts is None, -(ts or 0.0), str(entry["row"].get("id") or ""))

    return {"params": params, "results": sorted(best.values(), key=order)}
