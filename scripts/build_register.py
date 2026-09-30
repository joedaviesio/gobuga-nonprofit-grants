#!/usr/bin/env python3
"""Build the funder register: `platform/sources/<country>-register.json`.

Three steps, each resumable from the file the step before it wrote:

  1. candidates  a model lists the funders it knows, one category at a time
  2. check       each funder's website is fetched; one that does not answer
                 is dropped to `unresolved` (the defence against a made-up name)
  3. resolve     the funding page is chosen by rule from the links on the
                 home page, or by one Tavily search limited to that site

Costs real money (a few model calls, one search per unresolved funder), so it
asks for `--confirm`. Without it, it prints what it would do. Every paid call,
model or Tavily, counts toward the one `--budget`.

The funder categories, the tier 1 threshold and the words that mark a funding
page come from the `register` block of the country's config
(`platform/sources/<country>.json`), read by orchestrator/register_words.py,
which the sweep shares. Optional keys in that block:

  skip_link_words        more words that mark a link not worth following
  name_stopwords         words too common to show a page names the funder
  fold_words             words match at the start of a word, in text and
                         addresses folded to plain Latin letters (for sites
                         not in English); skip words are then checked in
                         the link's text as well as its address
  weak_skip_link_words   with fold_words: skip words that do not drop a link
                         whose text names a grant ("cariera", but not
                         "Granturi pentru dezvoltarea carierei")
  dedupe_known_by_site   one known funder per site, and a candidate on a known
                         funder's site is merged into it, not added
  shared_hosts           hosts where one known funder's site may hold another
                         body's pages: never merged by site
  exclude                funders ruled out; a candidate naming one, as a whole
                         word or acronym, is dropped before any resolve spend
  tier_one_from_manifest tier 1 only for the manifest's must_appear_funders,
                         whatever the model says, since the register's tier 1
                         becomes the publish gate
  merge_new_by_site      two new funders on one site are merged when they
                         reach the same page or their names agree, once the
                         words in name_stopwords and merge_name_stopwords
                         are set aside ("Konrad Adenauer Foundation" and
                         "Konrad Adenauer Stiftung")
  merge_name_stopwords   with merge_new_by_site: words that tell two names
                         on one site apart no better than a translation does
  strict_pages           with fold_words: a funding page is refused when its
                         address is a document, a dated or news path, or a
                         slug as long as a headline; a link, a guessed page
                         and a search result each need a strong_grant_words
                         word (a path may use strong_path_words too); and a
                         site found by search must carry the funder's name
  strong_grant_words     with strict_pages: words that show a page is about
                         applying for funding, not just about money
  strong_path_words      with strict_pages: more such words, trusted in an
                         address only ("how-to-apply", not "Apply now")
  not_funding_path_words with strict_pages: a path part starting with one of
                         these marks a page that is not a funding page
                         (news, stories, publications), unless the part
                         names funding itself
  not_funding_path_parts with strict_pages: words with a funding sense too
                         (media, events, contact): only a whole path part,
                         and not beside a part naming funding or a programme
  programme_path_words   with strict_pages: path words that excuse such a part
                         (/programmes/media)
  merge_kind_words       with merge_new_by_site: words naming a kind of body
                         (primăria, raional); two names with no kind word in
                         common are never merged
  aggregator_hosts       with strict_pages: grant listing sites a search may
                         find that are never a funder's own
  host_generic_words     with strict_pages: name words too common to show a
                         host is the funder's ("development", "women")

Usage:
    python scripts/build_register.py nz --confirm [--budget 3] [--only-category council]
"""

import tirith  # noqa: F401  — must come before anthropic

import argparse
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from api.country_config import get_country_config  # noqa: E402
from api.funders import _to_ascii, canonicalise_funder  # noqa: E402
from api.sources import register_path  # noqa: E402
from orchestrator import register_sweep as rs  # noqa: E402
# The country's words and the page rules live beside the sweep, which uses
# them too; they are importable from here under their old names.
from orchestrator.register_words import (  # noqa: E402, F401
    COUNTRY_SLUG, DOCUMENT, HEADLINE_WORDS, LONG_SLUG_WORDS, MONTH, REQUIRED, YEAR,
    _check_country, _config_error, _pattern, _skipped, _strings, funding_links, has_grant_words,
    page_problem, register_settings, strong_path, strong_text)
from orchestrator.register_words import country_pick_links as pick_links  # noqa: E402, F401

LIST_MODEL = "claude-sonnet-5"
LIST_PRICE_IN, LIST_PRICE_OUT = 3.00, 15.00
TAVILY_PRICE = 0.008            # USD per credit: one search, or one page extract
ASKS = 2                        # the model's list differs a little each time
BUDGET_REACHED = "budget reached"

# Tavily answers HTTP 429 both for too many requests a minute and for a plan
# out of credits; its SDK raises UsageLimitExceededError for either. The
# first passes in seconds, so the call waits and tries again; the second
# does not, so a message that says so is not retried.
TAVILY_TRIES = 4
TAVILY_WAIT_S = 4.0             # the first wait; doubled each time, with jitter
TAVILY_MAX_WAIT_S = 45.0        # in all, for one call
RATE_LIMITED = "UsageLimitExceededError"
# The SDK raises ForbiddenError for HTTP 403, 432 (plan limit) and 433
# (pay-as-you-go limit): the credits are used up, or the key may not call.
# Either way no later call will do better, so the run stops using Tavily.
PLAN_LIMIT = "ForbiddenError"
LIMIT_ERRORS = (RATE_LIMITED, PLAN_LIMIT, "TavilyStopped")
OUT_OF_CREDITS = re.compile(r"credit|quota|upgrade|billing|pay.as.you.go|plan'?s? (set )?"
                            r"(usage )?limit|usage limit", re.IGNORECASE)
TOO_FAST = re.compile(r"rate|too many|per minute|excessive|slow down", re.IGNORECASE)
_out_of_credits = threading.Event()


class TavilyStopped(Exception):
    """Raised in place of a Tavily call once Tavily has said the plan or
    credits are used up: the call would fail, so it is not made."""


def is_excluded(settings: dict, name: str, country: str) -> bool:
    """The candidate names a funder the owner ruled out, by its own name or
    the name its alias leads to."""
    exclude = settings["exclude"]
    if not exclude:
        return False
    names = {name, canonicalise_funder(name, country)}
    return any(exclude.search(_to_ascii(n)) for n in names)


def site_is_funders(settings: dict, url: str, funder: dict) -> bool:
    """A site found by search belongs to the funder when it is the site the
    listing gave; never when it is a grants aggregator; otherwise when its
    host carries the funder's name, as one of:

      the acronym as a host label or the start of one: fee.md for the
        Energy Efficiency Fund (FEE), but not coffee.md
      a distinctive name word starting or ending a host label: cahul.md for
        Consiliul Raional Cahul, crungheni.md for Ungheni, civilspace.eu
        for the Civil Society Development Foundation
      two distinctive name words anywhere in the host

    A distinctive word is not one of name_stopwords or host_generic_words:
    "development" alone does not make developmentaid.org UNDP's, nor
    "women" womenfund.org UN Women's. A place name is distinctive, so a
    council whose only other words are generic is found by its town."""
    site = _site_of(url)
    if site and site == _site_of(funder.get("website")):
        return True
    if not site or site in settings["aggregator_hosts"]:
        return False
    labels = [lab for lab in re.split(r"[.-]", site) if lab and lab != "www"]
    words = [w for w in _name_words(funder["name"]) if w not in settings["host_generic_words"]]
    acronyms = [a.lower() for a in re.findall(r"\b[A-Z]{3,}\b", funder["name"])]

    def at_edge(w: str) -> bool:
        return any(lab.startswith(w) or lab.endswith(w) for lab in labels)
    host = "".join(labels)
    return (any(lab.startswith(a) for a in acronyms for lab in labels)
            or any(at_edge(w) for w in words)
            or sum(1 for w in set(words) if w in host) >= 2)


class Budget(rs.Budget):
    """The sweep's budget, also counting the listing model and Tavily, so one
    cap covers every paid call the build makes."""

    def __init__(self, cap_usd: float):
        super().__init__(cap_usd)
        self.searches = self.extracts = 0

    def add_listing(self, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            self.tokens_in += tokens_in
            self.tokens_out += tokens_out
            self.calls += 1
            self.spent += tokens_in / 1e6 * LIST_PRICE_IN + tokens_out / 1e6 * LIST_PRICE_OUT

    def add_search(self) -> None:
        with self._lock:
            self.searches += 1
            self.spent += TAVILY_PRICE

    def add_extract(self) -> None:
        with self._lock:
            self.extracts += 1
            self.spent += TAVILY_PRICE


def _pause(seconds: float) -> None:
    time.sleep(seconds)


def _wait(attempt: int, waited: float) -> float:
    """How long to wait before try `attempt + 2`, or 0 to give up: doubling
    from TAVILY_WAIT_S, each wait jittered so the resolve threads do not all
    come back in the same second, and never more than TAVILY_MAX_WAIT_S in
    all."""
    if attempt + 1 >= TAVILY_TRIES or _out_of_credits.is_set():
        return 0.0
    return min(TAVILY_WAIT_S * 2 ** attempt * random.uniform(0.5, 1.0),
               TAVILY_MAX_WAIT_S - waited)


def _describe(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {message}"[:200] if message else type(exc).__name__


def _too_fast(exc: Exception) -> bool:
    """A 429 worth waiting out. One whose message says the credits or the
    plan are used up is not, nor a ForbiddenError (432, 433); after either
    no other call is made."""
    if type(exc).__name__ == PLAN_LIMIT:
        _out_of_credits.set()
        return False
    if type(exc).__name__ != RATE_LIMITED:
        return False
    message = str(exc)
    if OUT_OF_CREDITS.search(message) and not TOO_FAST.search(message):
        _out_of_credits.set()
        return False
    return True


def tavily_call(call, *args, **kwargs):
    """One Tavily call, tried again after a wait while Tavily says too many
    requests. Whatever else goes wrong, or the last try's error, is raised
    to the caller. A call that fails is not paid for, so the caller counts
    one only once it has an answer."""
    waited = 0.0
    for attempt in range(TAVILY_TRIES):
        if _out_of_credits.is_set():
            raise TavilyStopped("Tavily said the plan or credits are used up earlier in this run")
        try:
            return call(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            wait = _wait(attempt, waited) if _too_fast(exc) else 0.0
            if wait <= 0:
                raise
            waited += wait
            _pause(wait)


def fetch(url: str, budget: Budget, failed: list | None = None) -> dict:
    """rs.fetch_page, counting a Tavily extract for a page that refuses a
    plain fetch or has no text without scripts only when the extract
    brought the page back: Tavily bills only a successful extraction, and
    an attempt that failed (a 429, or nothing returned) costs nothing.

    The sweep's fetch keeps only the error's class name, so an extract
    turned away as too many requests is told by that name; the page is
    fetched again after a wait, as a search is. Callers check the budget
    first. An extract Tavily turned away for good is added to `failed`, so
    the funder is tried again on a re-run."""
    waited = 0.0
    for attempt in range(TAVILY_TRIES):
        page = rs.fetch_page(url)
        if page.get("via") == "tavily":
            budget.add_extract()
        why = page.get("extract_error") or page.get("error") or ""
        if f"extract failed: {PLAN_LIMIT}" in why:
            _out_of_credits.set()
        limited = any(f"extract failed: {e}" in why for e in LIMIT_ERRORS)
        wait = _wait(attempt, waited) if f"extract failed: {RATE_LIMITED}" in why else 0.0
        if wait <= 0:
            if limited and failed is not None:
                failed.append("extract")
            return page
        waited += wait
        _pause(wait)
    return page


def write_json(path: str, data) -> None:
    """Written beside `path` and renamed into place, so a run stopped halfway
    never leaves half a file to be read as a whole one."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


PROMPT = """List funders in {country} of this kind: {what}.

Be complete: list every funder of this kind that you are confident exists and gives grants \
an organisation or person can apply for. The name is what matters. Never invent a web \
address: give null for any address you are not sure of, and it will be looked up.

Answer with one JSON object and nothing else:
{{"funders": [{{"name": "official name", "website": "https://... or null", "funding_url": \
"https://... the page about applying for funding, or null if unsure", "regions": ["slugs"], \
"tier": 1 or 2}}]}}

regions: slugs from this list only: {regions}. Use ["national"] for a funder open nationwide.
tier 1: {tier_one}. tier 2: everyone else."""


def list_prompt(settings: dict, category: str, regions: list[str]) -> str:
    return PROMPT.format(country=settings["country"], what=settings["categories"][category],
                         regions=", ".join(regions), tier_one=settings["tier_one"])


def list_candidates(country: str, asks: list[str], budget: Budget, settings: dict,
                    out: list[dict], asked: dict[str, int]) -> None:
    """Ask the model once per entry of `asks`, adding to `out` and counting
    each finished ask in `asked`, so a run stopped halfway can carry on."""
    import anthropic
    client = anthropic.Anthropic(max_retries=4)
    cfg = get_country_config(country)
    for cat in asks:
        if budget.exhausted:
            print(f"[register] budget reached before {cat}")
            break
        with client.messages.stream(
            model=LIST_MODEL, max_tokens=16000,
            # A list to recall, not a problem to reason through. Without
            # thinking the whole allowance is left for the JSON answer.
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": list_prompt(settings, cat, cfg.regions)}],
        ) as stream:
            resp = stream.get_final_message()
        budget.add_listing(resp.usage.input_tokens, resp.usage.output_tokens)
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        parsed = rs.parse_json(text)
        found = parsed.get("funders") if isinstance(parsed, dict) else None
        for f in found if isinstance(found, list) else []:
            if isinstance(f, dict) and f.get("name"):
                out.append({"name": str(f["name"]).strip(), "category": cat,
                            "website": str(f.get("website") or "").strip(),
                            "funding_url": f.get("funding_url"),
                            "regions": [r for r in (f.get("regions") or []) if r in cfg.regions],
                            "tier": 1 if f.get("tier") == 1 else 2})
        # A reply cut off, refused or unreadable is not a finished ask: the
        # next run asks again. An empty list that ended normally is finished.
        if resp.stop_reason != "end_turn" or not isinstance(found, list):
            print(f"[register] {cat}: ask not finished (stop reason {resp.stop_reason}, "
                  f"{len(found) if isinstance(found, list) else 0} funders read); "
                  f"it will be asked again")
            continue
        asked[cat] = asked.get(cat, 0) + 1
        print(f"[register] {cat}: {len(found)} listed, ${budget.spent:.2f} spent")


# Only NZ's candidates file was written in the first format, a bare list
# with no country in it; any other country's must say whose it is.
BARE_LIST_COUNTRIES = {"nz"}


def load_candidates(path: str, country: str, categories) -> tuple[list[dict], dict[str, int]]:
    """Candidates from an earlier run, and how many asks each category has
    had. In the bare-list format a category found in the file counts as
    fully asked, and one missing from it is listed again. Candidates of a
    category no longer configured are dropped."""
    if not os.path.exists(path):
        return [], {}

    def refuse(why: str) -> SystemExit:
        return SystemExit(f"[register] {path}: {why}. Move it aside to list afresh.")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise refuse(f"cannot be read ({type(exc).__name__})") from exc
    if isinstance(data, list):
        if country not in BARE_LIST_COUNTRIES:
            raise refuse(f"an old-format list with no country, not known to be {country}'s")
        candidates, asked = data, {c.get("category"): ASKS for c in data if isinstance(c, dict)}
    elif isinstance(data, dict):
        if data.get("country") != country:
            raise refuse(f"it holds {data.get('country')!r}'s candidates, not {country!r}'s")
        candidates, asked = data.get("candidates"), data.get("asked")
    else:
        raise refuse("not a candidates file")
    if not isinstance(candidates, list) or not isinstance(asked, dict) or not all(
            isinstance(c, dict) and isinstance(c.get("name"), str) for c in candidates):
        raise refuse("missing its candidates or its count of asks")
    kept = [c for c in candidates if c.get("category") in categories]
    if len(kept) < len(candidates):
        print(f"[register] dropped {len(candidates) - len(kept)} candidates of categories "
              f"no longer configured")
    return kept, {c: n for c, n in asked.items() if c in categories and isinstance(n, int)}


def load_progress(path: str, country: str) -> dict[str, dict]:
    """Funders resolved by an earlier run that the budget stopped, by key, so
    a re-run does not pay for them again. Refused like the candidates file
    when it is another country's or unreadable."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"[register] {path}: cannot be read ({type(exc).__name__}). "
                         f"Move it aside to resolve afresh.") from exc
    if not isinstance(data, dict) or data.get("country") != country or not isinstance(
            data.get("resolved"), dict):
        raise SystemExit(f"[register] {path}: not the resolve progress for {country!r}. "
                         f"Move it aside to resolve afresh.")
    return data["resolved"]


def funder_key(name: str, country: str) -> str:
    return rs.slugify_funder(name, country)


def dedupe_candidates(candidates: list[dict], country: str) -> list[dict]:
    seen, unique = set(), []
    for c in candidates:
        key = rs.slugify_funder(re.sub(r"\([^)]*\)|\b(limited|ltd|the)\b", " ", c["name"],
                                       flags=re.I), country)
        if key and key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def known_funders(country: str, by_site: bool = False) -> list[dict]:
    """Funders whose funding page is already known to be real: the manifest's
    seed sources, and the source pages of the last swept pool."""
    from api.opportunities import latest_available_month, load_pool
    from api.sources import load_manifest
    cfg = get_country_config(country)
    manifest = load_manifest(country)
    tier_one = {f["name"].lower() for f in manifest.get("must_appear_funders", [])}
    out = {}
    for s in manifest.get("seed_sources", []) + manifest.get("must_appear_funders", []):
        if s.get("category") == "aggregator" or not s.get("url"):
            continue
        regions = [r for r in (s.get("regions") or []) if r in cfg.regions]
        if s.get("scope") == "national" and not regions:
            regions = ["national"]
        out.setdefault(s["name"].lower(), {
            "name": s["name"], "category": s.get("category") or "other", "website": s["url"],
            "url": s["url"], "regions": regions, "resolved_by": "known",
            "tier": 1 if s["name"].lower() in tier_one else 2})
    month = latest_available_month(country)
    pages: dict[str, dict] = {}
    for row in (load_pool(country, month) if month else []):
        name, url = (row.get("funder") or "").strip(), row.get("source_url") or ""
        if not name or not url.startswith("http"):
            continue
        entry = pages.setdefault(name.lower(), {"name": name, "urls": {}, "regions": set()})
        entry["urls"][url] = entry["urls"].get(url, 0) + 1
        entry["regions"].update(r for r in (row.get("region") or []) if r in cfg.regions)
    for key, entry in pages.items():
        best = max(entry["urls"], key=entry["urls"].get)
        out.setdefault(key, {"name": entry["name"], "category": "other", "website": best,
                             "url": best, "regions": sorted(entry["regions"]),
                             "resolved_by": "known", "tier": 1 if key in tier_one else 2})
    if not by_site:
        return list(out.values())
    # One funder per site: the first name kept, with the best tier of any dropped.
    sites: dict[str, dict] = {}
    for f in out.values():
        key = rs._site(urlsplit(f["url"]).hostname)
        if key in sites:
            sites[key]["tier"] = min(sites[key]["tier"], f["tier"])
        else:
            sites[key] = f
    return list(sites.values())


def _site_of(url) -> str:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return ""
    return rs._site(urlsplit(url).hostname)


def known_sites(known, settings: dict) -> dict[str, dict]:
    """Known funders by site, for merging a candidate found on one. A shared
    host (one known funder's pages among other bodies') is left out: two
    funders there are not taken to be one."""
    out = {}
    for k in known:
        for url in (k.get("url"), k.get("website")):
            site = _site_of(url)
            if site and site not in settings["shared_hosts"]:
                out.setdefault(site, k)
    return out


def same_page(a: str, b: str) -> bool:
    """One page, whatever the www., the scheme or a closing slash."""
    pa, pb = urlsplit(a), urlsplit(b)
    return (rs._site(pa.hostname), pa.path.rstrip("/"), pa.query) == (
        rs._site(pb.hostname), pb.path.rstrip("/"), pb.query)


def _merge_words(settings: dict, name: str, acronyms: bool = True) -> set[str]:
    stop = settings["merge_stopwords"]
    return set(_name_words(name, stop)) | ({
        a.lower() for a in re.findall(r"\b[A-Z]{3}\b", name) if a.lower() not in stop}
        if acronyms else set())


def kinds_differ(settings: dict, a: str, b: str) -> bool:
    """Each name says what kind of body it is, and they have no kind word in
    common: "Consiliul Raional Cahul" and "Primăria Cahul" are a raion
    council and a town hall. "Primăria Municipiului Bălți" and "Primăria
    Bălți" are one town hall."""
    kinds = settings["merge_kind_words"]

    def kind(name: str) -> set[str]:
        return set(re.findall(r"[^\W\d_]+", _to_ascii(name))) & kinds
    ka, kb = kind(a), kind(b)
    return bool(ka and kb) and not ka & kb


def names_agree(settings: dict, a: str, b: str, exact: bool = False) -> bool:
    """The distinctive words of one name are all in the other, once the
    country's stopwords and merge_name_stopwords are set aside: "Konrad
    Adenauer Foundation Moldova" and "Konrad Adenauer Stiftung Moldova",
    "MAIB Foundation" and "Moldova Agroindbank (MAIB) Community Grants".
    Not "U.S. Embassy Chisinau Democracy Commission Small Grants Program"
    and "U.S. Embassy Chisinau Public Affairs Section Small Grants": two
    programmes, each a way in for applicants. Acronyms count as words.
    With `exact`, the words must be the same, not one set inside the other;
    a three-letter acronym given by one name only ("Black Sea Trust (BST)")
    does not count against it."""
    if kinds_differ(settings, a, b):
        return False
    x, y = _merge_words(settings, a), _merge_words(settings, b)
    if not (x and y):
        return False
    if not exact:
        return x <= y or y <= x
    short_a, short_b = x - _merge_words(settings, a, False), y - _merge_words(settings, b, False)
    return x - short_a == y - short_b and bool(x - short_a) and (
        not (short_a and short_b) or bool(short_a & short_b))


def same_funder(settings: dict, others: list[dict], c: dict, by_page: bool = False,
                shared: bool = False, page: str = "url") -> dict | None:
    """The first of `others` (funders on c's site, in listing order) that
    is c. A known funder's aliases count as its names.

    On a site of its own: its name agrees, or with `by_page` it reached the
    same page. On a shared host, where one ministry or agency hosts many
    bodies: its words are exactly c's ("European Union (Delegation to
    Moldova)" and "European Union Delegation to Moldova (EU grants)"), or it
    reached the same page, not the host's home page, and the two names
    share a distinctive word ("British Embassy Chisinau (UK Government
    Bilateral Programme)" and "Chevening / British Embassy Chisinau Small
    Grants"). A name inside a longer one is not enough there: German
    Marshall Fund and Black Sea Trust share gmfus.org, and a generic page
    reached by two UN agencies is not one agency's. `page` names the field
    holding each one's page: "website" before resolve."""
    for o in others:
        names = [o["name"]] + list(o.get("aliases", []))
        if any(kinds_differ(settings, n, c["name"]) for n in names):
            continue
        one_page = bool(by_page and o.get(page) and c.get(page)
                        and same_page(o[page], c[page]))
        if not shared:
            if one_page or any(names_agree(settings, n, c["name"]) for n in names):
                return o
        elif any(names_agree(settings, n, c["name"], exact=True) for n in names) or (
                one_page and urlsplit(c[page]).path.strip("/") and any(
                    _merge_words(settings, n) & _merge_words(settings, c["name"])
                    for n in names)):
            return o
    return None


def retry_later(result: dict) -> bool:
    """A result reached after a Tavily call gave up: a re-run tries again."""
    return bool(result.get("search_failed")) or any(
        e in str(result.get("error", "")) for e in LIMIT_ERRORS)


def tavily_find(name: str, site: str, terms: str, budget: Budget, accept=None) -> str | None:
    """The first result on the site, or with `accept` (strict_pages) the
    first on the site that `accept` takes."""
    from tavily import TavilyClient
    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    res = tavily_call(client.search, query=f"{name} {terms}", max_results=5,
                      include_domains=[site])
    budget.add_search()
    for r in res.get("results", []):
        if rs._site(urlsplit(r.get("url", "")).hostname) == site and (
                accept is None or accept(r)):
            return r["url"]
    return None


def search_result_is_funding(settings: dict, result: dict) -> bool:
    """Under strict_pages a search result stands only when its address is
    not refused and its path or title names funding in strong words. Its
    snippet is not enough: a news story about a grant says "grant" too."""
    url = result.get("url", "")
    if _skipped(settings, url, result.get("title") or ""):
        # The owner's skip words hold for a search result as for a link:
        # "Rezultatele concursului de granturi 2025" is not a call.
        return False
    return not page_problem(settings, url) and (
        strong_path(settings, url) or strong_text(settings, result.get("title") or ""))


def _name_words(name: str, stop=frozenset()) -> list[str]:
    return [w for w in re.findall(r"[^\W\d_]{4,}", _to_ascii(name)) if w not in stop]


PICK_PROMPT = """Which of these search results is the official website of the {country} \
funder "{name}"? Its own site only: not a news story, a directory, a grant-writing service, \
a council page about other funders, or a different organisation with a similar name.

{results}

Answer with one JSON object and nothing else: {{"index": number or null}}"""


def tavily_site(name: str, country: str, budget: Budget) -> str | None:
    """The funder's own site, when the guessed address does not answer.

    A cheap model picks among the search results; the caller then checks that
    the page it picked names the funder, so a wrong pick is dropped."""
    from tavily import TavilyClient
    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    res = tavily_call(client.search, query=f'"{name}" {country}', max_results=8)
    budget.add_search()
    results = [r for r in res.get("results", []) if r.get("url")]
    if not results or budget.exhausted:
        return None
    listing = "\n".join(f"{i}. {r['url']} | {(r.get('title') or '')[:90]} | "
                        f"{(r.get('content') or '')[:160]}" for i, r in enumerate(results))
    raw, tin, tout = rs.anthropic_ask("You answer with JSON only.", PICK_PROMPT.format(
        country=country, name=name, results=listing))
    budget.add(tin, tout)
    index = (rs.parse_json(raw) or {}).get("index")
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(results):
        return results[index]["url"]
    return None


def names_funder(page: dict, name: str, stop=frozenset()) -> bool:
    """The page carries every distinctive word of the funder's name, each as
    a whole word: "Rata" is not in "Strata", nor "ADA" in "Canada".

    Both sides are folded to plain Latin letters: Romanian pages mix ș and ş
    or drop the diacritics, and a Cyrillic name may be written either way."""
    text = set(re.findall(r"[^\W\d_]+", _to_ascii(rs._norm(page.get("text", "")))))
    core = re.sub(r"\([^)]*\)", " ", name)
    words = _name_words(core, stop) or re.findall(r"[^\W\d_]{3,}", _to_ascii(core))
    return bool(words) and all(w in text for w in words)


def resolve(funder: dict, use_search: bool, settings: dict, budget: Budget) -> dict:
    """Fill `url` (the funding page) and `resolved_by`, or `error`. Every
    paid step first checks the budget; once it is reached the funder is left
    unresolved with that reason. A result reached after a Tavily call gave
    up is marked `search_failed`, so it is not saved as done: a re-run tries
    that funder again."""
    failed: list[str] = []
    out = _resolve(funder, use_search, settings, budget, failed)
    return {**out, "search_failed": True} if failed else out


def _resolve(funder: dict, use_search: bool, settings: dict, budget: Budget,
             failed: list[str]) -> dict:
    strict = settings["strict_pages"]
    stopped = {**funder, "error": BUDGET_REACHED}
    if budget.exhausted:
        return stopped
    home = (fetch(funder["website"], budget, failed) if funder["website"].startswith("http")
            else {"error": "no address given"})
    if home.get("error") and use_search:
        if budget.exhausted:
            return stopped
        try:
            found = tavily_site(funder["name"], settings["country"], budget)
        except Exception as exc:  # noqa: BLE001
            found = None
            failed.append("site search")
            print(f"[register] site search failed for {funder['name']}: {_describe(exc)}")
        if found and strict and not site_is_funders(settings, found, funder):
            # A page that happens to mention the funder, on someone else's
            # site: a tyre dealer's article is not the Energy Efficiency Fund.
            return {**funder, "error": f"search found {_site_of(found)}, not the funder's site"}
        if found:
            root = "{0.scheme}://{0.netloc}/".format(urlsplit(found))
            tries = (root,) if page_problem(settings, found) else (found, root)
            for candidate in tries:
                if budget.exhausted:
                    return stopped
                page = fetch(candidate, budget, failed)
                if not page.get("error") and names_funder(page, funder["name"],
                                                          settings["name_stopwords"]):
                    home = page
                    break
            else:
                return {**funder, "error": "search found no site that names the funder"}
        elif budget.exhausted:
            return stopped
    if home.get("error"):
        return {**funder, "error": home["error"]}
    site = rs._site(urlsplit(home["url"]).hostname)
    guess = funder.get("funding_url")
    if isinstance(guess, str) and rs._site(urlsplit(guess).hostname) == site and not (
            page_problem(settings, guess) or (strict and _skipped(settings, guess, ""))):
        if budget.exhausted:
            return stopped
        page = fetch(guess, budget, failed)
        text = page.get("text", "")
        # Under strict_pages one broad stem ("sprijin", "fonduri") is not
        # enough, and a guess that redirects to a news story is refused.
        good = (strong_text(settings, text) and not page_problem(settings, page.get("url", ""))
                and not _skipped(settings, page.get("url", ""), "")
                if strict else has_grant_words(settings, text))
        if not page.get("error") and good:
            return {**funder, "url": page["url"], "resolved_by": "model"}
    links = funding_links(settings, home)
    if links:
        return {**funder, "url": links[0], "resolved_by": "link"}
    if use_search:
        if budget.exhausted:
            return stopped
        accept = (lambda r: search_result_is_funding(settings, r)) if strict else None
        try:
            found = tavily_find(funder["name"], site, settings["search_terms"], budget, accept)
        except Exception as exc:  # noqa: BLE001
            found = None
            failed.append("search")
            print(f"[register] search failed for {funder['name']}: {_describe(exc)}")
        if found:
            return {**funder, "url": found, "resolved_by": "search"}
    if has_grant_words(settings, home.get("text", "")) and not page_problem(settings, home["url"]):
        return {**funder, "url": home["url"], "resolved_by": "home"}
    return {**funder, "error": "no funding page found"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("country")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--budget", type=float, default=3.0)
    ap.add_argument("--only-category", action="append")
    ap.add_argument("--no-search", action="store_true", help="do not use Tavily search")
    ap.add_argument("--limit", type=int, help="resolve only the first N candidates")
    args = ap.parse_args(argv)
    settings = register_settings(args.country)
    # Categories differ by country, so they are checked here, not by argparse.
    unknown = [c for c in args.only_category or [] if c not in settings["categories"]]
    if unknown:
        ap.error(f"no category {', '.join(unknown)} for {args.country}; choose from "
                 f"{', '.join(settings['categories'])}")
    categories = args.only_category or list(settings["categories"])
    path = register_path(args.country)
    if not args.confirm:
        print(f"Would list funders in {len(categories)} categories with {LIST_MODEL}, check each "
              f"website, and write {path}. Budget ${args.budget:.2f}. Re-run with --confirm.")
        return 1
    keys = ["ANTHROPIC_API_KEY"] + ([] if args.no_search else ["TAVILY_API_KEY"])
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        raise SystemExit(f"[register] {', '.join(missing)} not set; nothing was spent")

    budget = Budget(args.budget)
    candidates_path = path.replace("-register.json", "-register-candidates.json")
    candidates, asked = ([], {}) if args.only_category else load_candidates(
        candidates_path, args.country, settings["categories"])
    asks = [c for c in categories for _ in range(ASKS - asked.get(c, 0))]
    if asks:
        try:
            list_candidates(args.country, asks, budget, settings, candidates, asked)
        finally:
            candidates = dedupe_candidates(candidates, args.country)
            if not args.only_category:
                write_json(candidates_path, {"country": args.country, "asked": asked,
                                             "candidates": candidates})
    else:
        print(f"[register] reusing {len(candidates)} candidates from {candidates_path}")
    short = [c for c in categories if asked.get(c, 0) < ASKS]
    if short:
        print(f"[register] listing not finished ({', '.join(short)}); the candidates so far "
              f"are saved. Re-run (with a larger --budget if it ran out) to list the rest.")
        return 1
    if settings["exclude"]:
        dropped = [c["name"] for c in candidates if is_excluded(settings, c["name"], args.country)]
        candidates = [c for c in candidates
                      if not is_excluded(settings, c["name"], args.country)]
        print(f"[register] {len(dropped)} candidates excluded by the owner's list"
              + (f": {', '.join(dropped)}" if dropped else ""))
    if args.limit:
        candidates = candidates[:args.limit]

    # A funder already known keeps its known page; the model's entry only
    # supplies the category and tier.
    from_manifest = settings["tier_one_from_manifest"]
    known = {k["name"].lower(): k
             for k in known_funders(args.country, settings["dedupe_known_by_site"])}
    slugs = {rs.slugify_funder(n, args.country): n for n in known}
    by_site = known_sites(known.values(), settings) if settings["dedupe_known_by_site"] else {}
    merges = []
    # Two new funders on one site are one funder when their names agree or
    # they reach the same page; the first listed keeps its name. On a shared
    # host the test is stricter (see same_funder), and a new funder may also
    # be merged into a known one there, never the other way round.
    merge_new = settings["merge_new_by_site"]
    new_by_site: dict[str, list[dict]] = {}
    known_shared: dict[str, list[dict]] = {}
    if merge_new:
        aliases = get_country_config(args.country).funder_aliases or {}
        for k in known.values():
            k["aliases"] = aliases.get(k["name"], [])
            for site in {_site_of(k.get("url")), _site_of(k.get("website"))}:
                if site in settings["shared_hosts"]:
                    known_shared.setdefault(site, []).append(k)

    def merge(match: dict, c: dict, why: str | None = None) -> None:
        match["category"] = c["category"]
        if not from_manifest:
            match["tier"] = min(match["tier"], c["tier"])
        match["regions"] = match["regions"] or c["regions"]
        if why:
            merges.append(f"{c['name']} -> {match['name']} ({why})")
            print(f"[register] merged {c['name']!r} into {match['name']!r}: {why}")

    def absorb(keep: dict, c: dict, why: str) -> None:
        if not from_manifest:
            keep["tier"] = min(keep["tier"], c["tier"])
        keep["regions"] = keep["regions"] or c["regions"]
        merges.append(f"{c['name']} -> {keep['name']} ({why})")
        print(f"[register] merged {c['name']!r} into {keep['name']!r}: {why}")

    todo = []
    for c in candidates:
        match = known.get(c["name"].lower()) or known.get(
            slugs.get(rs.slugify_funder(c["name"], args.country), ""))
        site = _site_of(c.get("website"))
        shared = site in settings["shared_hosts"]
        # On a shared host the listed address is a page, not a home page
        # ("undp.org/moldova"), so it counts as the page reached.
        on_known = (same_funder(settings, known_shared.get(site, []), c, by_page=shared,
                                shared=True, page="website") if merge_new and shared else None)
        twin = (same_funder(settings, new_by_site.get(site, []), c, by_page=shared,
                            shared=shared, page="website") if merge_new and site else None)
        if match:
            merge(match, c)
        elif site in by_site:
            # Merged before any resolve spend: its listed site is a known one.
            merge(by_site[site], c, f"listed site {site}")
        elif on_known:
            why = "same page" if same_page(on_known["website"], c["website"]) else "same name"
            merge(on_known, c, f"listed site {site}, shared host, {why}")
        elif twin:
            # Merged before any resolve spend too: the same funder, listed twice.
            why = "same name"
            if shared:
                why = "shared host, " + ("same page" if same_page(twin["website"], c["website"])
                                         else "same name")
            absorb(twin, c, f"listed site {site}, {why}")
        else:
            todo.append(c)
            if merge_new and site:
                new_by_site.setdefault(site, []).append(c)
    print(f"[register] {len(known)} funders already known, {len(todo)} to resolve")

    # Results are saved as they come, so a run the budget stops can resume
    # without paying for them again. A saved result the current rules refuse
    # (an address strict_pages turns away, or one reached after a Tavily call
    # gave up) is resolved again.
    progress_path = path.replace("-register.json", "-register-progress.json")
    progress = {} if args.only_category else load_progress(progress_path, args.country)
    stale = [k for k, r in progress.items() if not isinstance(r, dict) or retry_later(r) or (
        r.get("url") and page_problem(settings, r["url"]))]
    for k in stale:
        del progress[k]
    if stale:
        print(f"[register] {len(stale)} saved results refused by the current rules; "
              f"they are resolved again")
    saved = {funder_key(c["name"], args.country) for c in todo} & set(progress)
    left = [c for c in todo if funder_key(c["name"], args.country) not in saved]
    if saved:
        print(f"[register] {len(saved)} resolved by an earlier run, {len(left)} left")
    lock = threading.Lock()

    def work(c: dict) -> dict:
        r = resolve(c, not args.no_search, settings, budget)
        if r.get("error") != BUDGET_REACHED and not retry_later(r) and not args.only_category:
            with lock:
                progress[funder_key(c["name"], args.country)] = r
                write_json(progress_path, {"country": args.country, "resolved": progress})
        return r

    with ThreadPoolExecutor(max_workers=rs.CRAWL_CONCURRENCY) as pool:
        fresh = iter(list(pool.map(work, left)))
    # In the listing's order, saved or not, so which name a merge keeps does
    # not depend on what an earlier run finished.
    resolved = [progress[k] if k in saved else next(fresh)
                for k in (funder_key(c["name"], args.country) for c in todo)]
    # Funders a Tavily call gave up on (too many requests, or the plan or
    # credits used up) are not finished, merged or not.
    limited = sum(1 for r in resolved if retry_later(r))
    if by_site or known_shared:
        # A funding page found on a known funder's site is that funder's. On
        # a shared host, only when it is that funder by the stricter test.
        kept = []
        for r in resolved:
            site = _site_of(r.get("url"))
            on_known = (same_funder(settings, known_shared.get(site, []), r, by_page=True,
                                    shared=True) if site in known_shared else None)
            if site in by_site:
                merge(by_site[site], r, f"funding page on {site}")
            elif on_known:
                why = "same page" if same_page(on_known["url"], r["url"]) else "same name"
                merge(on_known, r, f"funding page on {site}, shared host, {why}")
            else:
                kept.append(r)
        resolved = kept
    if merge_new:
        # Two new funders whose pages are on one site, checked again now the
        # page is known: most were listed with no site, or different ones.
        kept, pages_by_site = [], {}
        for r in resolved:
            site = _site_of(r.get("url"))
            shared = site in settings["shared_hosts"]
            twin = (same_funder(settings, pages_by_site.get(site, []), r, by_page=True,
                                shared=shared) if site else None)
            if twin:
                why = "same page" if same_page(twin["url"], r["url"]) else "same name"
                absorb(twin, r, f"funding page on {site}, {'shared host, ' if shared else ''}{why}")
            else:
                kept.append(r)
                if site:
                    pages_by_site.setdefault(site, []).append(r)
        resolved = kept
    if not args.only_category:
        resolved = list(known.values()) + resolved
    if from_manifest:
        from api.sources import load_manifest
        must = {f["name"].lower() for f in load_manifest(args.country)["must_appear_funders"]}
        for r in resolved:
            r["tier"] = 1 if r["name"].lower() in must else 2
    funders = [{k: r[k] for k in ("name", "category", "tier", "regions", "url", "resolved_by")}
               for r in resolved if r.get("url")]
    unresolved = [{"name": r["name"], "category": r["category"], "website": r["website"],
                   "error": r["error"]} for r in resolved if not r.get("url")]
    funders.sort(key=lambda f: (f["tier"], f["category"], f["name"]))
    stopped = sum(1 for r in unresolved if r["error"] == BUDGET_REACHED)
    # A register short of funders the budget or Tavily cut off is not
    # written: once it exists, its tier 1 becomes the country's must-appear
    # list. The progress file stays, so a re-run pays only for those.
    written = None if (args.limit or args.only_category or stopped or limited) else path
    if written:
        write_json(path, {"country": args.country, "funders": funders, "unresolved": unresolved})
        if os.path.exists(progress_path):
            os.unlink(progress_path)
    by = {}
    for f in funders:
        by.setdefault(f["category"], [0, 0])[f["tier"] - 1] += 1
    if limited:
        print(f"[register] {limited} funders hit Tavily limits; re-run to resolve them "
              f"(the rest are saved in {progress_path}). If Tavily said the plan or "
              f"credits are used up, add credits first.")
    tavily_usd = (budget.searches + budget.extracts) * TAVILY_PRICE
    print(json.dumps({
        "candidates": len(candidates), "resolved": len(funders), "unresolved": len(unresolved),
        "stopped_by_budget": stopped, "stopped_by_tavily": limited,
        "resolved_by": {k: sum(1 for f in funders if f["resolved_by"] == k)
                        for k in ("known", "model", "link", "search", "site-search", "home")},
        "tier_1": sum(1 for f in funders if f["tier"] == 1),
        "merged_by_site": merges,
        "by_category_tier1_tier2": by,
        "spent_usd": round(budget.spent, 3), "model_usd": round(budget.spent - tavily_usd, 3),
        "model_calls": budget.calls, "tavily_searches": budget.searches,
        "tavily_extracts": budget.extracts, "tavily_usd": round(tavily_usd, 3),
        "written": written,
        "unresolved_sample": unresolved[:12],
    }, indent=2, ensure_ascii=False))
    return 0 if not (stopped or limited) else 1


if __name__ == "__main__":
    sys.exit(main())
