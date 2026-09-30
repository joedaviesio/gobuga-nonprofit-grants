"""Funder name canonicalisation.

Watchers and analysts emit funder names in inconsistent forms — e.g.
"Sport New Zealand (managed by Sport Canterbury)" and "Sport NZ" are the
same funder. This module collapses variants to a canonical form before the
dedupe key is computed, so duplicates merge.

Alias data now lives in the per-country config (api/country_config.py).
"""

import re
import unicodedata
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


# Cyrillic to Latin, for slugs only. Russian and the Moldovan Cyrillic
# alphabet; anything else is folded by NFKD or dropped.
_CYRILLIC = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "ӂ": "dzh",
})


_CYRILLIC_RUN = re.compile(r"[\u0400-\u04ff]+")  # the Cyrillic block


def transliterate_cyrillic(text: str) -> str:
    """Cyrillic letters to lowercase Latin, everything else untouched, so a
    title key keeps a Russian title's words instead of dropping them all.
    Text with no Cyrillic comes back exactly as it went in: New Zealand's
    keys, macrons and all, do not change."""
    return _CYRILLIC_RUN.sub(lambda m: m.group().lower().translate(_CYRILLIC), text)


def _to_ascii(text: str) -> str:
    """Lowercase Latin letters and digits for a slug: diacritics folded
    ("Fundația" -> "fundatia", "Kōkiri" -> "kokiri"), Cyrillic transliterated.
    Without this a non-ASCII name lost letters and a Cyrillic one slugged to
    nothing, so every Cyrillic funder looked like the same funder."""
    text = text.lower().translate(_CYRILLIC)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def slugify_funder(name: str, country: str | None = None) -> str:
    """Lowercase, hyphenate, drop punctuation — for dedupe keys and URLs."""
    canonical = canonicalise_funder(name, country=country)
    s = _to_ascii(canonical)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")
