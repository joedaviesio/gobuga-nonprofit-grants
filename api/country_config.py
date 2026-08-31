"""Country configuration — single source of truth for per-country settings.

Every deployment is one country, identified by the GOBUGA_COUNTRY env var.
Country config is loaded from platform/sources/{country}.json, extended with
fields for taxonomy, tiers, timezone, and language. This module replaces
scattered NZ-specific constants with a unified, per-country config system.

Usage:
    from api.country_config import get_country, get_country_config

    country = get_country()                 # reads GOBUGA_COUNTRY env var
    config  = get_country_config()          # loads + caches for that country
    config  = get_country_config("md")      # or for a specific country
"""

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache

from api.tenant import platform_sources_path


def get_country() -> str:
    """Deployment's country slug, from GOBUGA_COUNTRY env var (default: nz)."""
    return os.environ.get("GOBUGA_COUNTRY", "nz")


# --- Defaults (NZ backwards-compat) ------------------------------------------
# These are used when the country JSON doesn't include the extended fields yet.
# They mirror the previous hardcoded constants from taxonomy.py / funders.py.

_DEFAULT_TIMEZONE = "Pacific/Auckland"
_DEFAULT_CONTENT_LANGUAGE = "en"
_DEFAULT_UI_LANGUAGES: list[str] = ["en"]

_DEFAULT_NATIONAL_SCOPE_SYNONYMS: list[str] = [
    "national", "nationwide", "new zealand", "all of nz", "across new zealand",
]

_DEFAULT_TAGS: list[str] = [
    "community", "sport", "civic", "arts", "environment", "education",
    "health", "youth", "maori", "pasifika", "research", "capability",
    "infrastructure", "business", "women", "disability", "climate",
]

_DEFAULT_SECTOR_SLICES: list[dict] = [
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

_DEFAULT_URBAN_CENTRE_SLICES: list[dict] = [
    {"id": "auckland", "label": "Auckland", "regions": ["auckland", "northland"]},
    {"id": "hamilton", "label": "Hamilton & Waikato", "regions": ["waikato"]},
    {"id": "tauranga", "label": "Tauranga & Bay of Plenty", "regions": ["bay-of-plenty"]},
    {"id": "wellington", "label": "Wellington", "regions": ["wellington", "manawatu-whanganui"]},
    {"id": "christchurch", "label": "Christchurch & Canterbury", "regions": ["canterbury", "marlborough", "tasman", "nelson", "west-coast"]},
    {"id": "dunedin", "label": "Dunedin & Otago", "regions": ["otago", "southland"]},
]

_DEFAULT_SECTOR_LABEL_TO_TAG: dict[str, str] = {
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

_DEFAULT_FUNDER_ALIASES: dict[str, list[str]] = {
    "Sport NZ": [
        "sport nz", "sport new zealand",
        "sport new zealand (managed by sport canterbury)",
        "sport new zealand (managed by sport otago)",
        "sport new zealand (managed by sport waikato)",
        "sport new zealand (managed by sport auckland)",
        "sport new zealand (managed by sport bay of plenty)",
        "sport new zealand (managed by sport bop)",
    ],
    "Health Research Council of New Zealand": [
        "health research council", "health research council of new zealand",
        "hrc", "hrc nz",
    ],
    "Department of Internal Affairs": [
        "department of internal affairs", "dia", "internal affairs",
    ],
    "Te Puni Kōkiri": [
        "te puni kokiri", "te puni kōkiri", "tpk",
    ],
    "Toi Foundation": [
        "toi foundation", "toi foundation (formerly tsb community trust)",
        "tsb community trust",
    ],
    "Royal Society Te Apārangi": [
        "royal society te aparangi", "royal society te apārangi",
        "royal society of new zealand",
    ],
    "Lottery Grants Board": [
        "lottery grants board", "lotto grants board", "nz lottery grants board",
    ],
    "Foundation North": [
        "foundation north", "asb community trust",
    ],
    "Eastern & Central Community Trust": [
        "eastern & central community trust",
        "eastern and central community trust", "ecct",
    ],
    "Ministry of Education": [
        "ministry of education",
        "education workforce (ministry of education)", "moe",
    ],
}

_DEFAULT_TIERS: dict = {
    "scanner": {
        "label": "Grant Scanner",
        "price_monthly": 0,
        "opportunities_per_cycle": {"high": 2, "medium": 2, "low": 1},
        "max_open_cases": 3,
        "chat_messages_per_case": 5,
        "bots_bcd": False,
        "export_docx": False,
        "model": "claude-haiku-4-5-20251001",
    },
    "officer": {
        "label": "Grant Officer",
        "price_monthly": 9,
        "opportunities_per_cycle": None,
        "max_open_cases": -1,
        "chat_messages_per_case": -1,
        "bots_bcd": True,
        "export_docx": True,
        "model": "claude-sonnet-5",
    },
}


# --- CountryConfig -----------------------------------------------------------

@dataclass(frozen=True)
class CountryConfig:
    slug: str
    country_label: str
    currency: str
    timezone: str
    content_language: str
    ui_languages: list[str]  # first entry is the default UI language
    regions: list[str]
    national_scope_synonyms: list[str]
    tags: list[str]
    sector_slices: list[dict]
    urban_centre_slices: list[dict]
    sector_label_to_tag: dict[str, str]
    funder_aliases: dict[str, list[str]]
    tiers: dict = field(default_factory=dict)


@lru_cache(maxsize=8)
def get_country_config(country: str | None = None) -> CountryConfig:
    """Load and cache the country config. Falls back to NZ defaults for
    any fields not present in the JSON manifest."""
    country = country or get_country()
    path = platform_sources_path(country)

    raw: dict = {}
    if os.path.exists(path):
        with open(path) as f:
            raw = json.load(f)

    return CountryConfig(
        slug=country,
        country_label=raw.get("country_label", country.upper()),
        currency=raw.get("currency", "NZD"),
        timezone=raw.get("timezone", _DEFAULT_TIMEZONE),
        content_language=raw.get("content_language", _DEFAULT_CONTENT_LANGUAGE),
        ui_languages=raw.get("ui_languages", _DEFAULT_UI_LANGUAGES),
        regions=raw.get("regions", []),
        national_scope_synonyms=raw.get(
            "national_scope_synonyms", _DEFAULT_NATIONAL_SCOPE_SYNONYMS
        ),
        tags=raw.get("tags", _DEFAULT_TAGS),
        sector_slices=raw.get("sector_slices", _DEFAULT_SECTOR_SLICES),
        urban_centre_slices=raw.get("urban_centre_slices", _DEFAULT_URBAN_CENTRE_SLICES),
        sector_label_to_tag=raw.get("sector_label_to_tag", _DEFAULT_SECTOR_LABEL_TO_TAG),
        funder_aliases=raw.get("funder_aliases", _DEFAULT_FUNDER_ALIASES),
        tiers=raw.get("tiers", _DEFAULT_TIERS),
    )


def clear_config_cache():
    """For tests — clear the lru_cache so config reloads."""
    get_country_config.cache_clear()
