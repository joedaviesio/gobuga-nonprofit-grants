"""Controlled vocabulary for the search-box pivot.

Tags are emitted by the Reporter on each opportunity row and used by
the search/filter UI. Sectors slice the watcher into parallel workers
during a country sweep.
"""

# --- Tag vocabulary (per-opportunity labels) ---

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


def validate_tags(tags: list[str]) -> list[str]:
    """Drop unknown tags and dedupe, preserving order."""
    seen = set()
    out = []
    for t in tags or []:
        slug = t.strip().lower()
        if slug in _TAGS_SET and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


# --- Watcher sector slices (one parallel worker per slice) ---
# Each entry steers a Watcher run with its own seed-query angle.
# Tags listed are the controlled tags this slice tends to produce.

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


# --- Urban-centre slices (city-deep workers, all sectors) ---
# Run in parallel with sector workers. The sector workers find national-scope
# funders; urban workers find council, local-board, regional-foundation, and
# city-specific community-trust funds that national prompts tend to miss.
# `regions` is the set of region slugs whose seeds belong to this centre —
# used by `seeds_for_urban_centre` to filter the seed manifest.

# --- Sign-up sector labels → tag slugs ---
# The setup wizard uses friendly labels; this map translates them back to the
# controlled tag vocabulary so org sector picks can drive search relevance,
# "match my org" sort, and tier features. Keep in sync with
# `frontend/lib/sectors.ts:SECTORS`.

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


def org_sector_labels_to_tags(labels: list[str]) -> list[str]:
    """Map a list of sign-up sector labels to controlled tag slugs.
    Unknown labels are dropped silently — useful when reading legacy org
    records that used the old (pre-pivot) sector list."""
    out: list[str] = []
    seen: set[str] = set()
    for label in labels or []:
        tag = SECTOR_LABEL_TO_TAG.get(label.strip())
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


URBAN_CENTRE_SLICES: list[dict] = [
    {"id": "auckland", "label": "Auckland", "regions": ["auckland", "northland"]},
    {"id": "hamilton", "label": "Hamilton & Waikato", "regions": ["waikato"]},
    {"id": "tauranga", "label": "Tauranga & Bay of Plenty", "regions": ["bay-of-plenty"]},
    {"id": "wellington", "label": "Wellington", "regions": ["wellington", "manawatu-whanganui"]},
    {"id": "christchurch", "label": "Christchurch & Canterbury", "regions": ["canterbury", "marlborough", "tasman", "nelson", "west-coast"]},
    {"id": "dunedin", "label": "Dunedin & Otago", "regions": ["otago", "southland"]},
]
