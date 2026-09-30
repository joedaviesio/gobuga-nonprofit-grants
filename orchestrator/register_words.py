"""The words that mark a funding page, per country, and the rules that use them.

Shared by the register build (scripts/build_register.py), which picks each
funder's funding page, and the register sweep (orchestrator/register_sweep.py),
which reads that page and the links it follows. The keys of a country's
`register` block are described in scripts/build_register.py.

A country without `fold_words` gets the English defaults below, unchanged.
"""

import os
import re
from urllib.parse import unquote, urlsplit

from api.country_config import get_country_config
from api.funders import _to_ascii
from api.tenant import platform_sources_dir, platform_sources_path

GRANT_WORDS = re.compile(
    r"grant|fund|funding|apply|application|scholarship|sponsorship|putea|pūtea|tahua|contestable",
    re.IGNORECASE)
SKIP_LINK = re.compile(
    r"login|sign-?in|news|media-release|annual-report|careers|vacanc|privacy|terms|contact|"
    r"recipient|approved|declined|successful|past-grant|grants-made|who-we.?ve-funded|"
    r"facebook|twitter|linkedin|instagram|youtube|mailto:|tel:|\.(jpg|jpeg|png|gif|zip|docx?|xlsx?)$",
    re.IGNORECASE)

COUNTRY_SLUG = re.compile(r"[a-z]{2,8}")  # as api/metrics.py
REQUIRED = ("categories", "tier_one", "grant_words", "search_terms")

# A register URL that is a file, not a page: it has no links to follow and
# goes stale with the call it was written for.
DOCUMENT = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|odt|ods|rtf|zip|rar)$|/wp-content/uploads/|"
                      r"/sites/default/files/", re.IGNORECASE)
YEAR = re.compile(r"(19|20)\d\d")
MONTH = re.compile(r"0?[1-9]|1[0-2]")
HEADLINE_WORDS = 10             # a slug this long is a headline, whatever it says
LONG_SLUG_WORDS = 7             # this long, a headline unless it names funding


def _site(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def decode(address: str) -> str:
    """Percent-decoded until it stops changing: some sites encode twice."""
    for _ in range(3):
        plain = unquote(address)
        if plain == address:
            break
        address = plain
    return address


def pick_links(page: dict, home: str, seen: set[str], words=GRANT_WORDS, skip=SKIP_LINK,
               fold=None) -> list[str]:
    """Same-site links whose text or address looks like a grant programme,
    best first. Pure: decided by rule, not by a model. `words`, `skip` and
    `fold` let a country whose sites are not in English bring its own."""
    site = _site(urlsplit(home).hostname)
    scored = []
    for href, text in page.get("links", []):
        parts = urlsplit(href)
        if parts.scheme not in ("http", "https") or _site(parts.hostname) != site:
            continue
        key = href.rstrip("/")
        address, path = href, parts.path
        if fold:
            # Decoded and folded to plain Latin letters, so a Cyrillic or
            # percent-encoded address reads like any other.
            address, path, text = fold(decode(href)), fold(decode(parts.path)), fold(text)
        if key in seen or skip.search(address):
            continue
        score = 2 * len(words.findall(text)) + len(words.findall(path))
        if score:
            scored.append((-score, len(href), href))
    out, picked = [], set()
    for _, _, href in sorted(scored):
        if href.rstrip("/") not in picked:
            picked.add(href.rstrip("/"))
            out.append(href)
    return out


# --- A country's register words ---------------------------------------------------

def _config_error(country: str, message: str) -> SystemExit:
    return SystemExit(f"[register] {platform_sources_path(country)}: {message}")


def _check_country(country: str) -> None:
    """A slug, and a config file of exactly that name. On a case-blind disk
    `MD` would read md.json and then write over another country's files."""
    if not isinstance(country, str) or not COUNTRY_SLUG.fullmatch(country):
        raise SystemExit(f"[register] {country!r} is not a country slug (2 to 8 lowercase letters)")
    if f"{country}.json" not in os.listdir(platform_sources_dir()):
        raise SystemExit(f"[register] no {country}.json in {platform_sources_dir()}")


def _strings(reg: dict, key: str, country: str) -> list[str]:
    value = reg.get(key, [])
    if not isinstance(value, list) or not all(isinstance(w, str) and w.strip() for w in value):
        raise _config_error(country, f"register.{key} must be a list of non-empty strings")
    return value


def _pattern(words: list[str], fold) -> str:
    """One alternation of the words. Folded words match only at the start of
    a word ("grant" not in "migrant"), and a space or hyphen in one matches
    a space, hyphen or underscore, as in an address."""
    if not fold:
        return "|".join(map(re.escape, words))
    parts = [r"[\s_-]+".join(map(re.escape, re.split(r"[\s_-]+", fold(w).strip())))
             for w in words]
    if not all(parts):
        raise ValueError("a word folds to nothing")
    return r"(?<![^\W_])(?:" + "|".join(parts) + ")"


def register_settings(country: str) -> dict:
    """The country's `register` block, checked and ready to use. Stops with a
    message naming what is wrong, before any call is paid for."""
    _check_country(country)
    cfg = get_country_config(country)
    reg = cfg.register or {}
    if not isinstance(reg, dict):
        raise _config_error(country, "register must be an object")
    missing = [k for k in REQUIRED if not reg.get(k)]
    if missing:
        raise SystemExit(f"[register] no register {', '.join(missing)} for {country!r}: add them "
                         f"to the \"register\" block of {platform_sources_path(country)}")
    categories = reg["categories"]
    if not isinstance(categories, dict) or not all(
            isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
            for k, v in categories.items()):
        raise _config_error(country, "register.categories must map names to descriptions")
    for key in ("tier_one", "search_terms"):
        if not isinstance(reg[key], str) or not reg[key].strip():
            raise _config_error(country, f"register.{key} must be a non-empty string")
    for key in ("fold_words", "dedupe_known_by_site", "tier_one_from_manifest",
                "merge_new_by_site", "strict_pages", "keep_known_categories"):
        if not isinstance(reg.get(key, False), bool):
            raise _config_error(country, f"register.{key} must be true or false")
    grant_words = _strings(reg, "grant_words", country)
    skip_words = _strings(reg, "skip_link_words", country)
    weak_words = _strings(reg, "weak_skip_link_words", country)
    stopwords = _strings(reg, "name_stopwords", country)
    excluded = _strings(reg, "exclude", country)
    shared_hosts = _strings(reg, "shared_hosts", country)
    merge_stopwords = _strings(reg, "merge_name_stopwords", country)
    strong_words = _strings(reg, "strong_grant_words", country)
    path_words = _strings(reg, "strong_path_words", country)
    not_funding = _strings(reg, "not_funding_path_words", country)
    not_funding_parts = _strings(reg, "not_funding_path_parts", country)
    programme_words = _strings(reg, "programme_path_words", country)
    kind_words = _strings(reg, "merge_kind_words", country)
    aggregators = _strings(reg, "aggregator_hosts", country)
    host_generic = _strings(reg, "host_generic_words", country)
    trusted = _strings(reg, "trusted_hosts", country)
    dated_words = _strings(reg, "dated_path_words", country)
    about_words = _strings(reg, "about_path_words", country)
    fold = _to_ascii if reg.get("fold_words") else None
    if weak_words and not fold:
        raise _config_error(country, "register.weak_skip_link_words needs fold_words")
    strict = reg.get("strict_pages", False)
    if strict and not (fold and strong_words):
        raise _config_error(country,
                            "register.strict_pages needs fold_words and strong_grant_words")
    own_skip = weak_skip = strong = path_strong = not_funding_part = None
    not_funding_whole = programme = phrases = dated = about = None
    try:
        words = re.compile(_pattern(grant_words, fold), re.IGNORECASE)
        skip = SKIP_LINK
        if skip_words and fold:
            # Kept apart from the English list: in fold mode a link's text is
            # checked too, and "Contact us to apply" is not a contact page.
            own_skip = re.compile(_pattern(skip_words, fold), re.IGNORECASE)
        elif skip_words:
            skip = re.compile(skip.pattern + "|" + _pattern(skip_words, fold), re.IGNORECASE)
        if weak_words:
            weak_skip = re.compile(_pattern(weak_words, fold), re.IGNORECASE)
        # Whole words only, at both ends: "Sida" is not in "Sidanova".
        exclude = (re.compile(_pattern(excluded, _to_ascii) + r"(?![^\W_])", re.IGNORECASE)
                   if excluded else None)
        if strict:
            strong = re.compile(_pattern(strong_words, fold), re.IGNORECASE)
            path_strong = re.compile(_pattern(strong_words + path_words, fold), re.IGNORECASE)
            if not_funding:
                # A whole path part, or its first words: "news", "news-and-events",
                # "press-releases", but not "newsletter".
                not_funding_part = re.compile(
                    "^" + _pattern(not_funding, fold) + r"(?![^\W_])", re.IGNORECASE)
            if not_funding_parts:
                # Words with a funding sense too ("media-grants", "contact-grants"):
                # only a path part that is the word and nothing else.
                not_funding_whole = re.compile(
                    "^" + _pattern(not_funding_parts, fold) + "$", re.IGNORECASE)
            if programme_words:
                programme = re.compile(_pattern(programme_words, fold), re.IGNORECASE)
            # Strong words of more than one word ("apel deschis", "call for
            # proposals"): a slug holding one is a call's title, however long.
            multi = [w for w in strong_words if len(re.split(r"[\s_-]+", w.strip())) > 1]
            if multi:
                phrases = re.compile(_pattern(multi, fold), re.IGNORECASE)
            if dated_words:
                dated = re.compile(_pattern(dated_words, fold), re.IGNORECASE)
            if about_words:
                about = re.compile("^" + _pattern(about_words, fold) + r"(?![^\W_])", re.IGNORECASE)
    except ValueError as exc:
        raise _config_error(country, f"register words: {exc}") from exc
    return {
        "country": cfg.country_label,
        "categories": dict(categories),
        "tier_one": reg["tier_one"],
        "grant_words": words,
        "skip_link": skip,
        "own_skip": own_skip,
        "weak_skip": weak_skip,
        "fold": fold,
        "search_terms": reg["search_terms"],
        # Folded like the names they are checked against, so "Fundația" and
        # "Fundatia" are one word.
        "name_stopwords": {_to_ascii(w) for w in stopwords},
        "dedupe_known_by_site": reg.get("dedupe_known_by_site", False),
        "shared_hosts": {_site(h) for h in shared_hosts},
        "exclude": exclude,
        "tier_one_from_manifest": reg.get("tier_one_from_manifest", False),
        "merge_new_by_site": reg.get("merge_new_by_site", False),
        "keep_known_categories": reg.get("keep_known_categories", False),
        # Words naming a kind of body ("primăria", "raional") are not set
        # aside: a town hall and a raion council of one town are two funders.
        "merge_stopwords": ({_to_ascii(w) for w in stopwords + merge_stopwords}
                            - {_to_ascii(w) for w in kind_words}),
        "merge_kind_words": {_to_ascii(w) for w in kind_words},
        "strict_pages": strict,
        "strong_words": strong,
        "path_strong_words": path_strong,
        "not_funding_part": not_funding_part,
        "not_funding_whole": not_funding_whole,
        "programme_path": programme,
        "strong_phrases": phrases,
        "dated_path": dated,
        "about_path": about,
        "aggregator_hosts": {_site(h) for h in aggregators},
        "host_generic_words": {_to_ascii(w) for w in stopwords + host_generic},
        "trusted_hosts": {_site(h) for h in trusted},
    }


def has_grant_words(settings: dict, text: str) -> bool:
    fold = settings["fold"]
    return bool(settings["grant_words"].search(fold(text) if fold else text))


def _skipped(settings: dict, href: str, text: str) -> bool:
    """The country's own skip words, in the link's address or its text. A
    weak one gives way when the text names a grant. A strong one does not:
    "Concursul pentru ocuparea funcției publice" has a grant word, concurs."""
    fold = settings["fold"]
    address, label = fold(decode(href)), fold(text)
    own, weak = settings["own_skip"], settings["weak_skip"]
    if own and (own.search(address) or own.search(label)):
        return True
    if weak and (weak.search(address) or weak.search(label)):
        return not settings["grant_words"].search(label)
    return False


def country_pick_links(page: dict, home: str, settings: dict,
                       seen: set[str] | None = None) -> list[str]:
    """pick_links with the country's words. A country without fold_words
    gets exactly the sweep's rule. `seen` defaults to the home page alone."""
    seen = {home.rstrip("/")} if seen is None else seen
    if not settings["fold"]:
        return pick_links(page, home, seen, settings["grant_words"], settings["skip_link"])
    links = [(h, t) for h, t in page.get("links", []) if not _skipped(settings, h, t)]
    return pick_links({**page, "links": links}, home, seen, settings["grant_words"],
                      settings["skip_link"], settings["fold"])


def page_problem(settings: dict, url: str) -> str | None:
    """Why `url` cannot be a funding page, judged by its address alone, or
    None. Only with strict_pages; otherwise every address may be one.

    A document. A dated folder: a year followed by a month (/2026/02/), or
    a year with no funding word before it (/topics/2025/...), but not
    /granturi/2026/apel-deschis. A path part naming news, stories or the
    like, unless the part names funding itself; the ambiguous words (media,
    events, resources, press, contact) only as a whole part, and not after
    or before a part naming funding or a programme (/programmes/media,
    /resources/grants). A last part as long as a headline, counting words
    of three letters or more, so "de", "a" and "si" do not make a call's
    title a headline; seven such words stand only when one names funding."""
    if not settings["strict_pages"]:
        return None
    fold = settings["fold"]
    path = fold(decode(urlsplit(url).path))
    if DOCUMENT.search(path):
        return "a document"
    parts = [p for p in path.split("/") if p]
    strong = settings["path_strong_words"]
    programme = settings["programme_path"]

    def funding_part(p: str) -> bool:
        return bool(strong.search(p) or (programme and programme.search(p)))
    dated, about = settings.get("dated_path"), settings.get("about_path")
    for i, p in enumerate(parts[:-1]):
        # A year is a funding page's round when a part before it names
        # funding, a programme or a competition: /programe/2026/granturi,
        # /concursuri/2026/apel-de-propuneri.
        if YEAR.fullmatch(p) and (MONTH.fullmatch(parts[i + 1]) or not any(
                funding_part(q) or (dated and dated.search(q)) for q in parts[:i])):
            return "a dated path"
    marker, whole = settings["not_funding_part"], settings["not_funding_whole"]
    for i, p in enumerate(parts):
        if strong.search(p):
            continue
        if about and about.search(p) and any(strong.search(q) for q in parts[i + 1:]):
            # A section of the site, not news: /despre-noi/granturi.
            continue
        if marker and marker.search(p):
            return "a news or other non-funding path"
        if whole and whole.search(p) and not any(
                funding_part(q) for j, q in enumerate(parts) if j != i):
            return "a news or other non-funding path"
    slug = re.sub(r"\.(html?|aspx?|php)$", "", parts[-1]) if parts else ""
    words = re.findall(r"[^\W\d_]{3,}", slug)
    phrases = settings.get("strong_phrases")
    if phrases and phrases.search(slug):
        # A call's own title: "apel-deschis-pentru-organizatiile-...".
        return None
    if len(words) >= HEADLINE_WORDS or (
            len(words) >= LONG_SLUG_WORDS and not strong.search(slug)):
        return "an article slug"
    return None


def strong_text(settings: dict, text: str) -> bool:
    """Words that show a page is about applying for funding, not about
    money in general: "finanțare", not "Direcția finanțe"."""
    return bool(settings["strong_words"].search(settings["fold"](text or "")))


def strong_path(settings: dict, url: str) -> bool:
    fold = settings["fold"]
    return bool(settings["path_strong_words"].search(fold(decode(urlsplit(url).path))))


def funding_links(settings: dict, home: dict) -> list[str]:
    """pick_links, keeping under strict_pages only a link whose address is
    not refused and whose text or path names funding in strong words. One
    grant stem in a news slug no longer ties with a real "Granturi" page,
    and "Apply for an emergency travel document" is not a call."""
    links = country_pick_links(home, home["url"], settings)
    if not settings["strict_pages"]:
        return links
    texts: dict[str, list[str]] = {}
    for href, text in home.get("links", []):
        texts.setdefault(href.rstrip("/"), []).append(text)
    return [h for h in links if not page_problem(settings, h) and (
        strong_path(settings, h) or any(strong_text(settings, t)
                                        for t in texts.get(h.rstrip("/"), [])))]
