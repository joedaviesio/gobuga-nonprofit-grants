"""Deterministic fit ranking for the public /fit surface.

Answers: "for an organisation described by five parameters, which published
grants fit, in what order, and why?" Called by the /fit pages, /api/v1/fit and
the fit_grants MCP tool, so it must be cheap and fully deterministic:

- no I/O beyond the country config (api.country_config), no network, no LLM,
  no randomness, and no clock unless `today` is omitted;
- the same rows, profile and `today` always give identical output, whatever
  the order of the input rows.

Public interface (see plan/2026-09-28-build-contract.md, "Phase 2 interface"):

    ENTITY_VOCAB, SIZE_BANDS, NEEDS
    parse_eligible_entities(text, country=None) -> list[str]
    parse_fit_params(params, country=None) -> FitProfile
    score_fit(rows, profile, *, today=None) -> list[{"row", "score", "why"}]

Exclusion is conservative: a row is dropped only on a confident mismatch
(its stated entity list leaves the requested status out, or it names only
known regions and none is the requested one), or when it is closed or stale.
Everything unknown scores neutrally.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Mapping
from zoneinfo import ZoneInfo

from api.country_config import get_country, get_country_config


# --- Vocabulary --------------------------------------------------------------

ENTITY_VOCAB: tuple[str, ...] = (
    "incorporated-society", "charitable-trust", "marae", "school", "club",
    "informal", "company", "individual", "local-authority", "iwi-hapu",
)
SIZE_BANDS: tuple[str, ...] = ("under-50k", "50k-250k", "250k-1m", "over-1m")
NEEDS: tuple[str, ...] = ("operating", "capital", "project", "event", "equipment")

# Parameter order in canonical fit URLs. Never reorder: cached URLs depend on it.
PARAM_ORDER: tuple[str, ...] = ("sector", "region", "status", "size", "need")


# --- Weights -----------------------------------------------------------------
# Scores are additive and only their relative size matters. Exclusions are not
# weights; they are fixed rules (see _exclusion). Tune here, not in the code.

WEIGHTS: dict[str, int] = {
    # Sector dominates: a row about what the organisation does beats a row
    # that is merely local or well-timed.
    "sector_first_tag": 40,     # the row carries at least one requested tag
    "sector_extra_tag": 10,     # each further requested tag the row carries
    "sector_no_overlap": -40,   # the row is tagged, but with none of them.
                                # Untagged rows score 0 (unknown, not mismatch)
    # Region: a fund for the requested region is more targeted (and usually
    # less contested) than a national one.
    "region_exact": 25,         # the row names the requested region
    "region_national": 15,      # the row is national in scope
    # Legal status, only when the row's entity list is specific (at most
    # SPECIFIC_ENTITY_LIST_MAX slugs); a broad list is not an invitation.
    "status_named": 20,
    # Amount against the organisation's size band (see SIZE_BAND_REVENUE).
    "amount_fits_size": 10,     # stated amount is plausible for the band
    "amount_too_large": -15,    # the smallest grant exceeds annual revenue
    "amount_too_small": -10,    # the largest grant is trivial for the band
    # Need: the listing's own text mentions the kind of cost requested.
    "need_mentioned": 8,
    # Deadline: something actionable soon ranks above open-ended rolling.
    "deadline_soon": 15,        # dated, MIN_LEAD_DAYS..SOON_DAYS days away
    "deadline_later": 8,        # dated, further away than SOON_DAYS
    "rolling_confirmed": 5,     # rolling, confirmed by the verify pass
    "rolling_unconfirmed": 2,   # legacy "rolling" string, never verified
    "deadline_too_close": 0,    # dated but under MIN_LEAD_DAYS away: no boost
}

MIN_LEAD_DAYS = 7              # fewer days than this: unrealistic to apply
SOON_DAYS = 45                 # up to this many days away counts as "soon"
SPECIFIC_ENTITY_LIST_MAX = 4   # longer entity lists are exclusions, not invitations

# Annual revenue range (low, high) per size band, in the country's currency.
# high is None for the open-ended top band.
SIZE_BAND_REVENUE: dict[str, tuple[int, int | None]] = {
    "under-50k": (0, 50_000),
    "50k-250k": (50_000, 250_000),
    "250k-1m": (250_000, 1_000_000),
    "over-1m": (1_000_000, None),
}

# A grant whose maximum is below this share of the band's lower revenue bound
# is "too small" to be worth the application. Keyed by need; None = no need.
SMALL_GRANT_SHARE: dict[str | None, float] = {
    "operating": 0.05, "capital": 0.05, "project": 0.02,
    "event": 0.01, "equipment": 0.01, None: 0.02,
}


class FitParamError(ValueError):
    """A fit parameter had a value outside the country's vocabulary."""

    def __init__(self, param: str, value: str, allowed):
        self.param = param
        self.value = value
        self.allowed = list(allowed)
        super().__init__(
            f"Invalid {param} {value!r}. Allowed values: {', '.join(self.allowed)}"
        )


# --- Text normalisation ------------------------------------------------------

@lru_cache(maxsize=16384)
def _fold(text: str) -> str:
    """Lower-case, strip diacritics (ă→a, ș→s, ū→u; й→и in Cyrillic),
    unify dashes and quotes, collapse whitespace."""
    t = text.lower()
    if not t.isascii():
        t = unicodedata.normalize("NFKD", t).translate(_FOLD_TABLE)
    return " ".join(t.split())


# Drops combining marks (the diacritics NFKD splits off) and unifies quotes
# and dashes. U+0300..U+036F covers Latin and Cyrillic marks, including the
# comma below of ș/ț and the breve of й.
_FOLD_TABLE = {cp: None for cp in range(0x300, 0x370)}
_FOLD_TABLE.update({ord("’"): "'", ord("–"): "-", ord("—"): "-"})


@lru_cache(maxsize=4096)
def _slugify(value: str) -> str:
    """'Incorporated Society' / 'incorporated_society' → 'incorporated-society'."""
    t = _fold(value).replace("'", "")
    t = re.sub(r"[\s_/]+", "-", t)
    return re.sub(r"-{2,}", "-", t).strip("-")


def _alternation(patterns: list[str], names: list[str] | None = None) -> str:
    """Folded alternation of patterns (optionally as named groups). When every
    pattern starts with a word boundary it is factored out: the engine then
    fails once at a non-boundary instead of once per alternative, which is
    most of the parser's speed."""
    folded = [_fold(p) for p in patterns]
    shared = all(p.startswith(r"\b") for p in folded)
    if shared:
        folded = [p[2:] for p in folded]
    names = names or [None] * len(folded)
    body = "|".join(f"(?P<{n}>{p})" if n else f"(?:{p})" for p, n in zip(folded, names))
    return rf"\b(?:{body})" if shared else body


def _compile(patterns: list[str]) -> re.Pattern | None:
    """One alternation, folded the same way as the text it will search."""
    return re.compile(_alternation(patterns)) if patterns else None


# --- Eligibility parser ------------------------------------------------------
# parse_eligible_entities turns free-text eligibility into ENTITY_VOCAB slugs.
# The scorer excludes a row whose non-empty list leaves out the requested
# status, so a returned list must be complete. The rules, in order:
#
# 1. Named types ("incorporated societies", "schools", "individuals") are
#    recognised per language. A text that names types and nothing else returns
#    exactly those.
# 2. Generic wording ("community organisations", "not-for-profit groups",
#    "registered charities", "ONG-uri", "НКО") is open-ended: it does not say
#    which legal forms qualify. A text containing it returns [] (unknown),
#    even if it also names some types, because the named ones are then
#    examples, not the whole list. Implicit exclusions ("not-for-profit"
#    implies "not a company") are not acted on.
# 3. A closure narrows generic wording back to named types. A parenthetical
#    of named types only defines the generic noun directly before it
#    ("organisations (charitable trust or incorporated society)"), so that
#    noun no longer counts as open-ended. A requirement ("incorporated under /
#    registered as / must be ..." followed by named types only) closes the
#    whole text: the result is the named types.
# 4. Explicit negation ("not open to individuals", "excluding schools",
#    "individuals are not eligible", "cu excepția", "кроме") removes types.
#    Generic wording plus a negation returns every type except the negated
#    ones, because the text does state who is out. A type both named and
#    negated stays in: the negation is about a subgroup ("athletes at
#    university outside the region do not qualify").
# 5. Nothing recognised returns [].

_ENTITY_PATTERNS: dict[str, dict[str, list[str]]] = {
    "en": {
        "incorporated-society": [r"\bincorporated societ(?:y|ies)\b", r"\bsocieties\b"],
        "charitable-trust": [r"\bcharitable trusts?\b", r"\btrusts\b",
                             r"\btrust boards?\b", r"\bfoundations\b"],
        "marae": [r"\bmarae\b"],
        "school": [r"\bschools?\b", r"\bschool-based\b", r"\bkura\b",
                   r"\bkindergartens?\b", r"\bearly childhood (?:centres?|services)\b"],
        "club": [r"\bclubs?\b"],
        "informal": [r"\bunincorporated (?:groups?|societ(?:y|ies)|associations?)\b",
                     r"\binformal groups?\b", r"\bunregistered groups?\b",
                     r"\bgroups? without (?:a )?legal (?:status|entity|structure)\b"],
        "company": [r"\bcompan(?:y|ies)\b", r"\bbusinesses\b", r"\bfor[- ]profits?\b",
                    r"\bcommercial (?:entities|organi[sz]ations|operators)\b"],
        "individual": [r"\bindividuals\b", r"\ban individual\b",
                       r"\bindividual (?:applicants?|people|persons)\b",
                       r"\bathletes?\b", r"\bcoaches\b", r"\bresearchers?\b",
                       r"\bartists\b"],
        "local-authority": [r"\blocal (?:authorit(?:y|ies)|councils?|government)\b",
                            r"\b(?:district|city|regional|territorial) councils?\b",
                            r"\bterritorial authorit(?:y|ies)\b", r"\bcouncils\b"],
        "iwi-hapu": [r"\biwi\b", r"\bhapu\b", r"\brunanga\b",
                     r"\bpost[- ]settlement governance entit(?:y|ies)\b"],
    },
    "ro": {
        "incorporated-society": [r"\basociati\w* obstest\w*", r"\bao\b"],
        "charitable-trust": [r"\bfundati(?:i|ile)\b", r"\bfundatii obstesti\b"],
        "school": [r"\bscol(?:i|ile|ala|a)\b", r"\blice(?:e|ele|ul|u)\b",
                   r"\bgimnazi(?:i|ile|ul|u)\b", r"\bgradinit\w*"],
        "club": [r"\bclub(?:uri|urile|ul)?\b"],
        "informal": [r"\bgrup\w* de initiativa\b", r"\bgrup\w* informal\w*"],
        "company": [r"\bcompanii(?:le)?\b", r"\bintreprinderi\w*", r"\bagent\w* economic\w*",
                    r"\bsrl\b", r"\bimm\b", r"\bfirme(?:le)?\b"],
        "individual": [r"\bpersoan\w* fizic\w*", r"\bsportivi(?:i)?\b",
                       r"\bcercetator\w*", r"\bartisti(?:i)?\b"],
        "local-authority": [r"\bautoritat\w* (?:publice )?locale\b", r"\bapl\b",
                            r"\bprimari(?:i|ile|a|e)\b", r"\bconsili\w* local\w*"],
    },
    "ru": {
        "incorporated-society": [r"\bобщественн\w* (?:объединени|организаци)\w*"],
        "charitable-trust": [r"\bфонды\b", r"\bблаготворительн\w* фонд\w*"],
        "school": [r"\bшкол(?:а|ы|ам|ами|ах)?\b", r"\bлице(?:и|ев|ям)\b",
                   r"\bгимнази(?:и|ям)\b", r"\bдетск\w* сад\w*"],
        "club": [r"\bклуб\w*"],
        "informal": [r"\bинициативн\w* групп\w*", r"\bнеформальн\w* групп\w*"],
        "company": [r"\bкомпани\w*", r"\bпредприяти\w*", r"\bэкономическ\w* агент\w*",
                    r"\bооо\b", r"\bмсп\b"],
        "individual": [r"\bфизическ\w* лиц\w*", r"\bспортсмен\w*", r"\bисследовател\w*"],
        "local-authority": [r"\bорган\w* местного (?:публичного )?(?:управления|самоуправления)\b",
                            r"\bапу\b", r"\bпримэри\w*", r"\bмэри(?:я|и)\b"],
    },
}

# Open-ended applicant nouns. Searched after named spans are blanked out, so
# the "groups" inside "unincorporated groups" does not count.
_GENERIC_PATTERNS: dict[str, list[str]] = {
    "en": [r"\borgani[sz]ations?\b", r"\bgroups?\b", r"\bentit(?:y|ies)\b",
           r"\bagenc(?:y|ies)\b", r"\binstitutions?\b", r"\bproviders?\b",
           r"\bdevelopers?\b", r"\bbodies\b", r"\bcharit(?:y|ies)\b", r"\bnonprofits\b",
           r"\bngos?\b", r"\bcollectives?\b", r"\benterprises?\b", r"\bassociations?\b",
           r"\bteams?\b", r"\bfundseekers\b"],
    "ro": [r"\borganizati\w*", r"\bong\b", r"\bong-uri\w*", r"\bosc\b", r"\bgrupuri\w*",
           r"\bentitat\w*", r"\binstituti\w*", r"\basociati\w*", r"\bpersoan\w* juridic\w*"],
    "ru": [r"\bорганизаци\w*", r"\bнко\b", r"\bнпо\b", r"\bгрупп\w*", r"\bучреждени\w*",
           r"\bобъединени\w*", r"\bассоциаци\w*", r"\bюридическ\w* лиц\w*"],
}

# Phrases that mention an entity word without naming an applicant. Blanked
# before anything else is matched.
_NEUTRAL_PATTERNS: dict[str, list[str]] = {
    "en": [r"\baffiliated with [^,;.()]*", r"\bcharities act\b",
           # a not-for-profit company is not what ENTITY_VOCAB means by
           # "company"; leave it out rather than invite commercial companies
           r"\bcompan(?:y|ies)(?: act)? \(nonprofit\)", r"\bnonprofit compan(?:y|ies)\b"],
    "ro": [],
    "ru": [],
}

# Negation that covers the whole clause, in either word order
# ("individuals are not eligible", "nu sunt eligibile persoanele fizice").
_NEGATION_CLAUSE: dict[str, list[str]] = {
    "en": [r"\b(?:is|are) not eligible\b", r"\bnot eligible\b", r"\bineligible\b",
           r"\bdo(?:es)? not qualify\b", r"\bcan ?not apply\b", r"\bmay not apply\b",
           r"\b(?:is|are) excluded\b", r"\bwill not be (?:funded|considered)\b"],
    "ro": [r"\bnu (?:sunt|este|e) eligibil\w*", r"\bneeligibil\w*",
           r"\bnu pot (?:aplica|participa|depune|solicita)\b", r"\bnu se finanteaza\b",
           r"\bsunt exclus\w*"],
    "ru": [r"\bне (?:могут|имеют права|допускаются|рассматриваются|принимаются)\b",
           r"\bисключаются\b"],
}

# Negation that covers only what follows it in the clause ("excluding schools").
_NEGATION_LEADING: dict[str, list[str]] = {
    "en": [r"\bnot open to\b", r"\bexcluding\b", r"\bexcludes\b", r"\bexcept\b",
           r"\bother than\b", r"\bnot including\b", r"\bnot\b", r"\bno\b"],
    "ro": [r"\bcu exceptia\b", r"\bexceptand\b", r"\bnu includ\w*"],
    "ru": [r"\bза исключением\b", r"\bкроме\b", r"\bисключая\b"],
}

# Clause separators beyond punctuation. A positive predicate ends a clause so
# "clubs are eligible and individuals are not eligible" negates only the second.
_CLAUSE_SPLIT: dict[str, list[str]] = {
    "en": [r"\bbut\b", r"\b(?:is|are) eligible\b", r"\b(?:may|can) apply\b"],
    "ro": [r"\bdar\b", r"\binsa\b", r"\b(?<!nu )(?:sunt|este) eligibil\w*",
           r"\b(?<!nu )pot aplica\b"],
    "ru": [r"\bно\b", r"\b(?<!не )могут (?:подавать|подать|участвовать)\b",
           r"\b(?<!не )имеют право\b"],
}

# Phrases after which the rest of the clause is a complete list of types.
_CLOSURE_TRIGGERS: dict[str, list[str]] = {
    "en": [r"\bincorporated (?:under|as)\b", r"\bregistered (?:under|as)\b",
           r"\bmust be\b"],
    "ro": [r"\binregistrat\w* ca\b", r"\bconstituit\w* ca\b", r"\btrebuie sa fie\b"],
    "ru": [r"\bзарегистрированн\w* как\b", r"\bдолжны быть\b"],
}


@dataclass(frozen=True)
class _ParserRules:
    entities: re.Pattern | None            # one named group per pattern
    entity_slugs: dict[str, str]           # group name -> slug
    generic: re.Pattern | None
    neutral: re.Pattern | None
    negation_clause: re.Pattern | None
    negation_leading: re.Pattern | None
    clause_split: re.Pattern
    closure: re.Pattern | None


@lru_cache(maxsize=8)
def _rules_for(langs: tuple[str, ...]) -> _ParserRules:
    def merged(table: dict[str, list[str]]) -> list[str]:
        return [p for lang in langs for p in table.get(lang, [])]

    # One named group per slug; each group is that slug's patterns as a
    # single alternation, so the text is scanned once for all types.
    groups, slugs = [], {}
    for i, slug in enumerate(ENTITY_VOCAB):
        pats = [p for lang in langs for p in _ENTITY_PATTERNS.get(lang, {}).get(slug, [])]
        if pats:
            slugs[f"g{i}"] = slug
            groups.append(_alternation(pats))
    return _ParserRules(
        entities=re.compile(_alternation(groups, list(slugs))) if groups else None,
        entity_slugs=slugs,
        generic=_compile(merged(_GENERIC_PATTERNS)),
        neutral=_compile(merged(_NEUTRAL_PATTERNS)),
        negation_clause=_compile(merged(_NEGATION_CLAUSE)),
        negation_leading=_compile(merged(_NEGATION_LEADING)),
        clause_split=re.compile(r"[;.()\n]|" + _alternation(merged(_CLAUSE_SPLIT))),
        closure=_compile(merged(_CLOSURE_TRIGGERS)),
    )


def _languages(country: str | None) -> tuple[str, ...]:
    """Languages whose patterns apply to this country's row text: the content
    language, the UI languages, and English (donor pages are often English)."""
    cfg = get_country_config(country)
    langs = [cfg.content_language, *cfg.ui_languages, "en"]
    return tuple(dict.fromkeys(l for l in langs if l in _ENTITY_PATTERNS))


def _blank(text: str, pattern: re.Pattern | None) -> str:
    if pattern is None:
        return text
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def _entity_spans(text: str, rules: _ParserRules) -> list[tuple[int, int, str]]:
    """Named-type matches in order, non-overlapping. The leftmost match wins,
    so "charitable trusts" is one span and the "trusts" inside it is not."""
    if rules.entities is None:
        return []
    return [(m.start(), m.end(), rules.entity_slugs[m.lastgroup])
            for m in rules.entities.finditer(text)]


def _without_spans(text: str, spans) -> str:
    """text with each span replaced by spaces (positions are preserved)."""
    parts, prev = [], 0
    for start, end, _ in spans:
        parts.append(text[prev:start])
        parts.append(" " * (end - start))
        prev = end
    parts.append(text[prev:])
    return "".join(parts)


# Words allowed between the negated types in "excluding schools, clubs or iwi".
_CONNECTORS = frozenset({"and", "or", "nor", "the", "a", "an", "any", "all", "other",
                         "si", "sau", "ori", "и", "или"})


def _only_connectors(gap: str) -> bool:
    return all(word in _CONNECTORS for word in re.findall(r"\w+", gap))


def _starts(pattern: re.Pattern | None, text: str) -> list[re.Match]:
    return list(pattern.finditer(text)) if pattern else []


def _scan(text: str, rules: _ParserRules) -> tuple[set, set, bool]:
    """Return (positive slugs, negated slugs, has open-ended generic wording).

    Each pattern runs once over the whole text; results are then read clause
    by clause. A clause-level negation negates every type in its clause. A
    leading negation negates the run of types directly after it ("excluding
    schools, clubs or marae"); the run ends at the first word that is not a
    connector, so "no outstanding grants and clubs" negates nothing."""
    spans = _entity_spans(text, rules)
    generic = [m.start() for m in _starts(rules.generic, _without_spans(text, spans))]
    clause_neg = [m.start() for m in _starts(rules.negation_clause, text)]
    leading = [(m.start(), m.end()) for m in _starts(rules.negation_leading, text)]

    bounds, prev = [], 0
    for m in rules.clause_split.finditer(text):
        bounds.append((prev, m.start()))
        prev = m.end()
    bounds.append((prev, len(text)))

    positive: set[str] = set()
    negated: set[str] = set()
    open_ended = False
    for a, b in bounds:
        inside = [s for s in spans if a <= s[0] < b]
        if any(a <= p < b for p in clause_neg):
            negated |= {slug for _, _, slug in inside}
            continue
        open_ended = open_ended or any(a <= p < b for p in generic)
        negated_idx: set[int] = set()
        for lead_start, lead_end in leading:
            if not a <= lead_start < b:
                continue
            end = lead_end
            for i, (start, stop, _) in enumerate(inside):
                if start < end:
                    continue
                if not _only_connectors(text[end:start]):
                    break
                negated_idx.add(i)
                end = stop
        for i, (_, _, slug) in enumerate(inside):
            (negated if i in negated_idx else positive).add(slug)
    return positive, negated, open_ended


def _names_only(chunk: str, rules: _ParserRules) -> set[str]:
    """Slugs named in chunk, or an empty set if it also has generic wording."""
    spans = _entity_spans(chunk, rules)
    if not spans:
        return set()
    if rules.generic and rules.generic.search(_without_spans(chunk, spans)):
        return set()
    return {slug for _, _, slug in spans}


def _blank_defined_generics(text: str, rules: _ParserRules) -> str:
    """Blank a generic noun that a parenthetical of named types directly
    defines: "organisations (charitable trust or incorporated society)".
    A parenthetical anywhere else defines nothing."""
    if rules.generic is None:
        return text
    for paren in re.finditer(r"\(([^()]*)\)", text):
        if not _names_only(paren.group(1), rules):
            continue
        before = text[:paren.start()].rstrip()
        for m in rules.generic.finditer(before):
            if m.end() == len(before):
                text = text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]
    return text


def _closure_slugs(text: str, rules: _ParserRules) -> set[str]:
    """Named types stated as a requirement: "incorporated under / registered
    as / must be" followed, to the end of the clause, by named types only."""
    found: set[str] = set()
    if rules.closure:
        for m in rules.closure.finditer(text):
            found |= _names_only(re.split(r"[;.\n]", text[m.end():], maxsplit=1)[0], rules)
    return found


_NONPROFIT = re.compile(r"\b(?:not[\s-]+for|non)[\s-]+profit")


@lru_cache(maxsize=16384)
def _parse_cached(text: str, langs: tuple[str, ...]) -> tuple[str, ...]:
    rules = _rules_for(langs)
    t = _NONPROFIT.sub("nonprofit", _fold(text))  # so "not" is never a negation here
    t = _blank(t, rules.neutral)
    t = _blank_defined_generics(t, rules)

    positive, negated, open_ended = _scan(t, rules)
    closed = _closure_slugs(t, rules)
    if closed:
        open_ended = False
        positive |= closed
    negated -= positive  # a named type negated for a subgroup stays eligible

    if negated and (open_ended or not positive):
        result = set(ENTITY_VOCAB) - negated
    elif open_ended:
        result = set()
    else:
        result = positive
    return tuple(slug for slug in ENTITY_VOCAB if slug in result)


def parse_eligible_entities(text: str, country: str | None = None) -> list[str]:
    """Free-text eligibility → ENTITY_VOCAB slugs, in vocabulary order.
    Returns [] when nothing is stated or the wording is open-ended."""
    if not isinstance(text, str) or not text.strip():
        return []
    return list(_parse_cached(text, _languages(country)))


# --- Fit parameters ----------------------------------------------------------

@dataclass(frozen=True)
class FitProfile:
    """An organisation described by the five fit parameters. Build it with
    parse_fit_params so every value is a validated slug."""
    country: str
    sector: tuple[str, ...] = ()
    region: str | None = None
    status: str | None = None
    size: str | None = None
    need: str | None = None

    def canonical_query(self) -> str:
        """Parameters in PARAM_ORDER, slugs only, empty ones omitted. Sector
        slugs are sorted and comma-joined. Slugs are URL-safe as they stand."""
        values = {
            "sector": ",".join(sorted(self.sector)),
            "region": self.region, "status": self.status,
            "size": self.size, "need": self.need,
        }
        return "&".join(f"{k}={values[k]}" for k in PARAM_ORDER if values[k])


def _raw_param(params: Mapping, key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = ",".join(str(v) for v in value if v is not None)
    value = str(value).strip()
    return value or None


def _pick(param: str, raw: str, lookup: dict[str, str], allowed) -> str:
    slug = lookup.get(_slugify(raw))
    if slug is None:
        raise FitParamError(param, raw, allowed)
    return slug


def parse_fit_params(params: Mapping[str, str] | None,
                     country: str | None = None) -> FitProfile:
    """Validate and normalise fit parameters against the country config.

    Accepts case, space, underscore and diacritic variants of a slug
    ("Incorporated Society", "incorporated_society"), sector labels from the
    config ("Sport & recreation"), and national-scope synonyms for region.
    Unknown values raise FitParamError. Keys other than the five are ignored.
    """
    params = params or {}
    cfg = get_country_config(country or get_country())

    sector: tuple[str, ...] = ()
    raw = _raw_param(params, "sector")
    if raw:
        lookup = {_slugify(t): t for t in cfg.tags}
        for label, tag in cfg.sector_label_to_tag.items():
            if tag in cfg.tags:
                lookup.setdefault(_slugify(label), tag)
        picked = {_pick("sector", item, lookup, cfg.tags)
                  for item in raw.split(",") if item.strip()}
        sector = tuple(sorted(picked))

    region = None
    raw = _raw_param(params, "region")
    if raw:
        lookup = {_slugify(r): r for r in cfg.regions}
        if "national" in cfg.regions:
            for synonym in cfg.national_scope_synonyms:
                lookup.setdefault(_slugify(synonym), "national")
        region = _pick("region", raw, lookup, cfg.regions)

    picked_simple = {}
    for key, vocab in (("status", ENTITY_VOCAB), ("size", SIZE_BANDS), ("need", NEEDS)):
        raw = _raw_param(params, key)
        picked_simple[key] = (
            _pick(key, raw, {v: v for v in vocab}, vocab) if raw else None
        )

    return FitProfile(country=cfg.slug, sector=sector, region=region, **picked_simple)


# --- Reasons (why[]) ---------------------------------------------------------
# Every reason is a fact read from one field of the row. Selected by the
# country's content_language. The ro and ru sets need a native speaker's review.

_WHY: dict[str, dict[str, str]] = {
    "en": {
        "open_to": "open to {entities}",
        "region": "{region}",
        "nationwide": "open nationwide",
        "sector": "sector: {sectors}",
        "need": "listing mentions {need}",
        "closes_in": "closes in {days}",
        "closes_today": "closes today",
        "too_close": "{closes}, likely too soon to apply",
        "rolling": "rolling deadline",
        "rolling_confirmed": "rolling deadline, confirmed",
        "amount_range": "grants from {min} to {max}",
        "amount_up_to": "grants up to {max}",
        "amount_from": "grants from {min}",
        "amount_exact": "grants of {min}",
    },
    "ro": {
        "open_to": "pot aplica: {entities}",
        "region": "{region}",
        "nationwide": "la nivel național",
        "sector": "domeniu: {sectors}",
        "need": "anunțul menționează {need}",
        "closes_in": "se închide peste {days}",
        "closes_today": "se închide astăzi",
        "too_close": "{closes}, probabil prea curând pentru a aplica",
        "rolling": "depunere continuă",
        "rolling_confirmed": "depunere continuă, confirmată",
        "amount_range": "granturi între {min} și {max}",
        "amount_up_to": "granturi de până la {max}",
        "amount_from": "granturi de la {min}",
        "amount_exact": "granturi de {min}",
    },
    "ru": {
        "open_to": "могут подать заявку: {entities}",
        "region": "{region}",
        "nationwide": "по всей стране",
        "sector": "направление: {sectors}",
        "need": "в объявлении упоминается: {need}",
        "closes_in": "закрывается через {days}",
        "closes_today": "закрывается сегодня",
        "too_close": "{closes}, вероятно, слишком мало времени для заявки",
        "rolling": "приём заявок на постоянной основе",
        "rolling_confirmed": "приём заявок на постоянной основе, подтверждено",
        "amount_range": "гранты от {min} до {max}",
        "amount_up_to": "гранты до {max}",
        "amount_from": "гранты от {min}",
        "amount_exact": "гранты в размере {min}",
    },
}

_ENTITY_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "incorporated-society": "incorporated societies", "charitable-trust": "charitable trusts",
        "marae": "marae", "school": "schools", "club": "clubs", "informal": "informal groups",
        "company": "companies", "individual": "individuals",
        "local-authority": "local authorities", "iwi-hapu": "iwi and hapū",
    },
    "ro": {
        "incorporated-society": "asociații obștești", "charitable-trust": "fundații",
        "marae": "marae", "school": "școli", "club": "cluburi", "informal": "grupuri informale",
        "company": "companii", "individual": "persoane fizice",
        "local-authority": "autorități publice locale", "iwi-hapu": "iwi și hapū",
    },
    "ru": {
        "incorporated-society": "общественные объединения", "charitable-trust": "фонды",
        "marae": "мараэ", "school": "школы", "club": "клубы", "informal": "неформальные группы",
        "company": "компании", "individual": "физические лица",
        "local-authority": "органы местного публичного управления", "iwi-hapu": "иви и хапу",
    },
}

_NEED_LABELS: dict[str, dict[str, str]] = {
    "en": {"operating": "operating costs", "capital": "capital works", "project": "projects",
           "event": "events", "equipment": "equipment"},
    "ro": {"operating": "costuri operaționale", "capital": "investiții capitale",
           "project": "proiecte", "event": "evenimente", "equipment": "echipamente"},
    "ru": {"operating": "операционные расходы", "capital": "капитальные вложения",
           "project": "проекты", "event": "мероприятия", "equipment": "оборудование"},
}

_NEED_PATTERNS: dict[str, dict[str, list[str]]] = {
    "en": {
        "operating": [r"\boperat(?:ing|ional) (?:costs?|expenses|funding|support)\b",
                      r"\bcore (?:costs|funding)\b", r"\brunning costs\b",
                      r"\boverheads?\b", r"\bsalar(?:y|ies)\b", r"\bwages\b"],
        "capital": [r"\bcapital\b", r"\bconstruction\b", r"\bfacilit(?:y|ies)\b",
                    r"\brenovat\w*", r"\brefurbish\w*"],
        "project": [r"\bprojects?\b"],
        "event": [r"\bevents?\b", r"\bfestivals?\b"],
        "equipment": [r"\bequipment\b"],
    },
    "ro": {
        "operating": [r"\bcosturi operationale\b", r"\bcheltuieli (?:de functionare|operationale|administrative)\b",
                      r"\bcosturi de functionare\b", r"\bsalarii\b"],
        "capital": [r"\binvestiti\w* capital\w*", r"\bconstructi\w*", r"\brenovar\w*",
                    r"\breabilitar\w*"],
        "project": [r"\bproiect\w*"],
        "event": [r"\bevenimen\w*", r"\bfestival\w*"],
        "equipment": [r"\bechipament\w*"],
    },
    "ru": {
        "operating": [r"\bоперационн\w* расход\w*", r"\bадминистративн\w* расход\w*",
                      r"\bзарплат\w*"],
        "capital": [r"\bкапитальн\w*", r"\bстроительств\w*", r"\bреконструкц\w*",
                    r"\bремонт\w*"],
        "project": [r"\bпроект\w*"],
        "event": [r"\bмероприяти\w*", r"\bфестивал\w*"],
        "equipment": [r"\bоборудовани\w*"],
    },
}

# A need keyword preceded closely by one of these is not counted.
_NEED_NEGATION = _compile([r"\b(?:not|no|excluding|except|nu|fara|cu exceptia|не|без|кроме)\b"])

# How a currency is written, per content language: (prefix, suffix).
_CURRENCY_AFFIX: dict[str, dict[str, tuple[str, str]]] = {
    "en": {"NZD": ("$", ""), "AUD": ("A$", ""), "USD": ("US$", ""), "EUR": ("€", ""),
           "GBP": ("£", "")},
    "ro": {"MDL": ("", " lei"), "EUR": ("", " €"), "USD": ("", " USD")},
    "ru": {"MDL": ("", " лей"), "EUR": ("", " €"), "USD": ("", " USD")},
}
_THOUSANDS_SEP: dict[str, str] = {"en": ",", "ro": ".", "ru": " "}


def _format_money(amount: float, currency: str, lang: str) -> str:
    grouped = f"{int(round(amount)):,}".replace(",", _THOUSANDS_SEP.get(lang, ","))
    prefix, suffix = _CURRENCY_AFFIX.get(lang, {}).get(currency, ("", f" {currency}"))
    return f"{prefix}{grouped}{suffix}"


def _format_days(n: int, lang: str) -> str:
    if lang == "ro":
        if n == 1:
            return "1 zi"
        return f"{n} zile" if n % 100 < 20 else f"{n} de zile"
    if lang == "ru":
        if n % 10 == 1 and n % 100 != 11:
            word = "день"
        elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            word = "дня"
        else:
            word = "дней"
        return f"{n} {word}"
    return "1 day" if n == 1 else f"{n} days"


def _region_label(slug: str) -> str:
    small = {"of", "and", "de", "si"}
    words = slug.split("-")
    return " ".join(w if (i and w in small) else w.capitalize() for i, w in enumerate(words))


# --- Row readers: tolerant of legacy and malformed rows ----------------------

def _as_str_list(value) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _as_amount(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) and amount > 0 else None  # 0 is not an amount


def _as_date(value) -> date | None:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _as_timestamp(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _row_entities(row: dict, country: str) -> list[str]:
    """Stored eligible_entities if present (empty = not stated); legacy rows
    without the field are parsed from their eligibility text, exactly as the
    publish step would have done."""
    if "eligible_entities" in row:
        return [s for s in _as_str_list(row.get("eligible_entities")) if s in ENTITY_VOCAB]
    return parse_eligible_entities(row.get("eligibility"), country)


def _deadline(row: dict, today: date) -> tuple[str, int | None]:
    """(kind, days_left). kind is dated, rolling-confirmed, rolling, closed or
    unknown. Legacy rows without deadline_state infer it from `deadline`."""
    state = row.get("deadline_state")
    raw = row.get("deadline")
    when = _as_date(raw)
    if state == "closed":
        return "closed", None
    if state == "rolling-confirmed":
        return "rolling-confirmed", None
    if state == "unresolved":
        return "unknown", None
    if when is None:
        if state is None and isinstance(raw, str) and raw.strip().lower() == "rolling":
            return "rolling", None
        return "unknown", None
    days = (when - today).days
    return ("closed", None) if days < 0 else ("dated", days)


def _region_slugs(row: dict, synonyms: frozenset[str]) -> list[str]:
    out = []
    for value in _as_str_list(row.get("region")):
        slug = _slugify(value)
        out.append("national" if slug in synonyms else slug)
    return out


# --- Scoring -----------------------------------------------------------------

@dataclass(frozen=True)
class _Context:
    """Everything score_fit derives once per call rather than once per row."""
    profile: FitProfile
    today: date
    lang: str
    langs: tuple[str, ...]
    currency: str
    regions: frozenset[str]
    national_synonyms: frozenset[str]
    sector_labels: dict[str, str]


def _context(profile: FitProfile, today) -> _Context:
    cfg = get_country_config(profile.country)
    if today is None:
        today = datetime.now(ZoneInfo(cfg.timezone)).date()
    elif isinstance(today, datetime):
        today = today.date()
    elif isinstance(today, str):
        today = date.fromisoformat(today[:10])
    labels: dict[str, str] = {}
    for label, tag in cfg.sector_label_to_tag.items():
        labels.setdefault(tag, label)  # first label listed for a tag wins
    return _Context(
        profile=profile,
        today=today,
        lang=cfg.content_language if cfg.content_language in _WHY else "en",
        langs=_languages(profile.country),
        currency=cfg.currency,
        regions=frozenset(cfg.regions),
        national_synonyms=frozenset(_slugify(s) for s in cfg.national_scope_synonyms),
        sector_labels=labels,
    )


def _score_row(row: dict, ctx: _Context):
    """Return (score, why) or None when the row is excluded."""
    if row.get("status") in ("closed", "stale"):
        return None
    kind, days = _deadline(row, ctx.today)
    if kind == "closed":
        return None

    profile, lang = ctx.profile, ctx.lang
    text = _WHY[lang]
    score = 0
    why: list[str] = []

    # Legal status. Excluded only when a stated list leaves the status out.
    if profile.status:
        entities = _row_entities(row, profile.country)
        if entities and profile.status not in entities:
            return None
        if entities and len(entities) <= SPECIFIC_ENTITY_LIST_MAX:
            score += WEIGHTS["status_named"]
            why.append(text["open_to"].format(
                entities=_ENTITY_LABELS[lang][profile.status]))

    # Region. Excluded only when every named region is a known one and none
    # is the requested region; unrecognised names are unknown, not mismatch.
    if profile.region:
        regions = _region_slugs(row, ctx.national_synonyms)
        named = [r for r in regions if r != "national"]
        if "national" in regions:
            score += WEIGHTS["region_exact" if profile.region == "national" else "region_national"]
            why.append(text["nationwide"])
        elif profile.region in named:
            score += WEIGHTS["region_exact"]
            why.append(text["region"].format(region=_region_label(profile.region)))
        elif named and all(r in ctx.regions for r in named):
            return None

    # Sector tags.
    if profile.sector:
        tags = {t.lower() for t in _as_str_list(row.get("tags"))}
        overlap = [t for t in profile.sector if t in tags]
        if overlap:
            score += WEIGHTS["sector_first_tag"] + WEIGHTS["sector_extra_tag"] * (len(overlap) - 1)
            why.append(text["sector"].format(
                sectors="; ".join(ctx.sector_labels.get(t, t) for t in overlap)))
        elif tags:
            score += WEIGHTS["sector_no_overlap"]

    # Deadline.
    if kind == "dated":
        closes = (text["closes_today"] if days == 0
                  else text["closes_in"].format(days=_format_days(days, lang)))
        if days < MIN_LEAD_DAYS:
            score += WEIGHTS["deadline_too_close"]
            why.append(text["too_close"].format(closes=closes))
        else:
            score += WEIGHTS["deadline_soon" if days <= SOON_DAYS else "deadline_later"]
            why.append(closes)
    elif kind == "rolling-confirmed":
        score += WEIGHTS["rolling_confirmed"]
        why.append(text["rolling_confirmed"])
    elif kind == "rolling":
        score += WEIGHTS["rolling_unconfirmed"]
        why.append(text["rolling"])

    # Amount. Unknown amount or currency scores 0; a size comparison needs the
    # row to be in the country's currency.
    lo, hi = _as_amount(row.get("amount_min")), _as_amount(row.get("amount_max"))
    if lo is not None and hi is not None and lo > hi:
        lo = hi = None  # contradictory: treat as unknown
    currency = row.get("currency")
    currency = currency.strip().upper() if isinstance(currency, str) else None
    if currency and (lo is not None or hi is not None):
        if profile.size and currency == ctx.currency:
            band_lo, band_hi = SIZE_BAND_REVENUE[profile.size]
            if lo is not None and band_hi is not None and lo > band_hi:
                score += WEIGHTS["amount_too_large"]
            elif hi is not None and band_lo and hi < band_lo * SMALL_GRANT_SHARE[profile.need]:
                score += WEIGHTS["amount_too_small"]
            else:
                score += WEIGHTS["amount_fits_size"]
        fmt = {"min": _format_money(lo, currency, lang) if lo is not None else "",
               "max": _format_money(hi, currency, lang) if hi is not None else ""}
        if lo is not None and hi is not None:
            key = "amount_exact" if lo == hi else "amount_range"
        else:
            key = "amount_up_to" if hi is not None else "amount_from"
        why.append(text[key].format(**fmt))

    # Need, from the listing's own words.
    if profile.need:
        blob = " ".join(v for v in (row.get("title"), row.get("summary"), row.get("eligibility"))
                        if isinstance(v, str))
        if _need_mentioned(blob, profile.need, ctx.langs):
            score += WEIGHTS["need_mentioned"]
            why.append(text["need"].format(need=_NEED_LABELS[lang][profile.need]))

    return float(score), why


@lru_cache(maxsize=64)
def _need_pattern(need: str, langs: tuple[str, ...]) -> re.Pattern | None:
    return _compile([p for lang in langs for p in _NEED_PATTERNS.get(lang, {}).get(need, [])])


@lru_cache(maxsize=16384)
def _need_mentioned(text: str, need: str, langs: tuple[str, ...]) -> bool:
    pattern = _need_pattern(need, langs)
    if pattern is None or not text:
        return False
    folded = _fold(text)
    for m in pattern.finditer(folded):
        if not _NEED_NEGATION.search(folded[max(0, m.start() - 25):m.start()]):
            return True
    return False


def score_fit(rows: list[dict], profile: FitProfile, *, today=None) -> list[dict]:
    """Rank rows for a profile: [{"row", "score", "why"}], best first.

    Closed and stale rows, and rows that confidently mismatch the profile,
    are left out. Ties break on verified_at (newer first), then id, then
    title and source_url, so the order never depends on the input order.
    """
    ctx = _context(profile, today)
    scored = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        result = _score_row(row, ctx)
        if result is None:
            continue
        score, why = result
        ts = _as_timestamp(row.get("verified_at"))
        key = (-score, ts is None, -(ts or 0.0),
               str(row.get("id") or ""), str(row.get("title") or ""),
               str(row.get("source_url") or ""))
        scored.append((key, {"row": row, "score": score, "why": why}))
    scored.sort(key=lambda item: item[0])
    return [entry for _, entry in scored]
