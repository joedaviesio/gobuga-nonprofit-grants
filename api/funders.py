"""Funder name canonicalisation.

Watchers and analysts emit funder names in inconsistent forms — e.g.
"Sport New Zealand (managed by Sport Canterbury)" and "Sport NZ" are the
same funder. This module collapses variants to a canonical form before the
dedupe key is computed, so duplicates merge.
"""

import re


# Map of canonical name -> list of aliases (lowercased, normalised).
# Add entries as new variants surface in real sweeps.
_ALIASES: dict[str, list[str]] = {
    "Sport NZ": [
        "sport nz",
        "sport new zealand",
        "sport new zealand (managed by sport canterbury)",
        "sport new zealand (managed by sport otago)",
        "sport new zealand (managed by sport waikato)",
        "sport new zealand (managed by sport auckland)",
        "sport new zealand (managed by sport bay of plenty)",
        "sport new zealand (managed by sport bop)",
    ],
    "Health Research Council of New Zealand": [
        "health research council",
        "health research council of new zealand",
        "hrc",
        "hrc nz",
    ],
    "Department of Internal Affairs": [
        "department of internal affairs",
        "dia",
        "internal affairs",
    ],
    "Te Puni Kōkiri": [
        "te puni kokiri",
        "te puni kōkiri",
        "tpk",
    ],
    "Toi Foundation": [
        "toi foundation",
        "toi foundation (formerly tsb community trust)",
        "tsb community trust",
    ],
    "Royal Society Te Apārangi": [
        "royal society te aparangi",
        "royal society te apārangi",
        "royal society of new zealand",
    ],
    "Lottery Grants Board": [
        "lottery grants board",
        "lotto grants board",
        "nz lottery grants board",
    ],
    "Foundation North": [
        "foundation north",
        "asb community trust",
    ],
    "Eastern & Central Community Trust": [
        "eastern & central community trust",
        "eastern and central community trust",
        "ecct",
    ],
    "Ministry of Education": [
        "ministry of education",
        "education workforce (ministry of education)",
        "moe",
    ],
}


# Pre-compute a flat lookup: alias_lower -> canonical
_LOOKUP: dict[str, str] = {}
for canonical, aliases in _ALIASES.items():
    _LOOKUP[canonical.strip().lower()] = canonical
    for alias in aliases:
        _LOOKUP[alias.strip().lower()] = canonical


_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*")


def _strip_parenthetical(name: str) -> str:
    """Remove trailing parenthetical suffixes like '(managed by …)'."""
    return _PARENTHETICAL.sub(" ", name).strip()


def canonicalise_funder(raw_name: str) -> str:
    """Map a raw funder name to its canonical form.
    Returns the original name (trimmed) if no alias matches."""
    if not raw_name:
        return ""
    raw = raw_name.strip()
    key = raw.lower()
    if key in _LOOKUP:
        return _LOOKUP[key]
    stripped = _strip_parenthetical(raw)
    skey = stripped.lower()
    if skey in _LOOKUP:
        return _LOOKUP[skey]
    return stripped or raw


def slugify_funder(name: str) -> str:
    """Lowercase, hyphenate, drop punctuation — for dedupe keys."""
    canonical = canonicalise_funder(name)
    s = canonical.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")
