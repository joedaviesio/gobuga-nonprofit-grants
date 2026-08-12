"""Funder name canonicalisation.

Watchers and analysts emit funder names in inconsistent forms — e.g.
"Sport New Zealand (managed by Sport Canterbury)" and "Sport NZ" are the
same funder. This module collapses variants to a canonical form before the
dedupe key is computed, so duplicates merge.

Alias data now lives in the per-country config (api/country_config.py).
"""

import re
from functools import lru_cache

from api.country_config import get_country, get_country_config


def _build_lookup(aliases: dict[str, list[str]]) -> dict[str, str]:
    """Build a flat alias_lower -> canonical lookup from an aliases dict."""
    lookup: dict[str, str] = {}
    for canonical, variants in aliases.items():
        lookup[canonical.strip().lower()] = canonical
        for alias in variants:
            lookup[alias.strip().lower()] = canonical
    return lookup


@lru_cache(maxsize=8)
def _get_lookup(country: str) -> dict[str, str]:
    """Cached flat lookup for a country's funder aliases."""
    config = get_country_config(country)
    return _build_lookup(config.funder_aliases)


_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*")


def _strip_parenthetical(name: str) -> str:
    """Remove trailing parenthetical suffixes like '(managed by …)'."""
    return _PARENTHETICAL.sub(" ", name).strip()


def canonicalise_funder(raw_name: str, country: str | None = None) -> str:
    """Map a raw funder name to its canonical form.
    Returns the original name (trimmed) if no alias matches."""
    if not raw_name:
        return ""
    lookup = _get_lookup(country or get_country())
    raw = raw_name.strip()
    key = raw.lower()
    if key in lookup:
        return lookup[key]
    stripped = _strip_parenthetical(raw)
    skey = stripped.lower()
    if skey in lookup:
        return lookup[skey]
    return stripped or raw


def slugify_funder(name: str, country: str | None = None) -> str:
    """Lowercase, hyphenate, drop punctuation — for dedupe keys."""
    canonical = canonicalise_funder(name, country=country)
    s = canonical.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")
