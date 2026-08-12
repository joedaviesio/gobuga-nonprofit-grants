"""Controlled vocabulary for the search-box pivot.

Tags are emitted by the Reporter on each opportunity row and used by
the search/filter UI. Sectors slice the watcher into parallel workers
during a country sweep.

Per-country tag/sector/region data now lives in the country config
(api/country_config.py). The module-level constants below are kept as
NZ-specific defaults used by get_country_config() when the JSON manifest
doesn't include the extended fields. Direct imports of TAGS, SECTOR_SLICES,
etc. from this module still work for backward compatibility but callers
that need country-aware values should use get_country_config() instead.
"""

from api.country_config import get_country_config


# --- Tag vocabulary (per-opportunity labels) ---
# NZ defaults — canonical list now in country config JSON.

TAGS: list[str] = [
    "community",
    "sport",
    "civic",
    "arts",
    "environment",
    "education",
    "health",
    "youth",
    "maori",
    "pasifika",
    "research",
    "capability",
    "infrastructure",
    "business",
    "women",
    "disability",
    "climate",
]

_TAGS_SET = set(TAGS)


def validate_tags(tags: list[str], country: str | None = None) -> list[str]:
    """Drop unknown tags and dedupe, preserving order.
    When country is given, validates against that country's tag vocabulary."""
    if country:
        valid = set(get_country_config(country).tags)
    else:
        valid = _TAGS_SET
    seen: set[str] = set()
    out: list[str] = []
    for t in tags or []:
        slug = t.strip().lower()
        if slug in valid and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


# --- Watcher sector slices (one parallel worker per slice) ---
# NZ defaults — canonical list now in country config JSON.

SECTOR_SLICES: list[dict] = [
    {"id": "community", "label": "Community development & civic", "tags": ["community", "civic"]},
    {"id": "sport", "label": "Sport & recreation", "tags": ["sport"]},
    {"id": "arts", "label": "Arts, culture & heritage", "tags": ["arts"]},
    {"id": "environment", "label": "Environment & climate", "tags": ["environment", "climate"]},
    {"id": "education", "label": "Education & research", "tags": ["education", "research"]},
    {"id": "health", "label": "Health & wellbeing", "tags": ["health"]},
    {"id": "youth", "label": "Youth & children", "tags": ["youth"]},
    {"id": "maori_pasifika", "label": "Māori & Pasifika", "tags": ["maori", "pasifika"]},
    {"id": "infrastructure", "label": "Capability & infrastructure", "tags": ["capability", "infrastructure"]},
    {"id": "business", "label": "Business, innovation & enterprise", "tags": ["business"]},
    {"id": "inclusion", "label": "Disability, women & inclusion", "tags": ["disability", "women"]},
]


# --- Sign-up sector labels → tag slugs ---
# NZ defaults — canonical map now in country config JSON.

SECTOR_LABEL_TO_TAG: dict[str, str] = {
    "Arts, culture & heritage": "arts",
    "Business, innovation & enterprise": "business",
    "Capability building": "capability",
    "Civic & local democracy": "civic",
    "Climate": "climate",
    "Community development": "community",
    "Disability services": "disability",
    "Education": "education",
    "Environment": "environment",
    "Health & wellbeing": "health",
    "Infrastructure": "infrastructure",
    "Māori-led kaupapa": "maori",
    "Pasifika": "pasifika",
    "Research": "research",
    "Sport & recreation": "sport",
    "Women & gender equity": "women",
    "Youth & children": "youth",
}


def org_sector_labels_to_tags(labels: list[str], country: str | None = None) -> list[str]:
    """Map a list of sign-up sector labels to controlled tag slugs.
    Uses the country config's label-to-tag map when country is given.
    Unknown labels are dropped silently — useful when reading legacy org
    records that used the old (pre-pivot) sector list."""
    label_map = (
        get_country_config(country).sector_label_to_tag
        if country
        else SECTOR_LABEL_TO_TAG
    )
    out: list[str] = []
    seen: set[str] = set()
    for label in labels or []:
        tag = label_map.get(label.strip())
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


# --- Urban-centre slices ---
# NZ defaults — canonical list now in country config JSON.

URBAN_CENTRE_SLICES: list[dict] = [
    {"id": "auckland", "label": "Auckland", "regions": ["auckland", "northland"]},
    {"id": "hamilton", "label": "Hamilton & Waikato", "regions": ["waikato"]},
    {"id": "tauranga", "label": "Tauranga & Bay of Plenty", "regions": ["bay-of-plenty"]},
    {"id": "wellington", "label": "Wellington", "regions": ["wellington", "manawatu-whanganui"]},
    {"id": "christchurch", "label": "Christchurch & Canterbury", "regions": ["canterbury", "marlborough", "tasman", "nelson", "west-coast"]},
    {"id": "dunedin", "label": "Dunedin & Otago", "regions": ["otago", "southland"]},
]
