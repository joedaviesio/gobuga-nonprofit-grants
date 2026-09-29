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
(`platform/sources/<country>.json`). Optional keys in that block:

  skip_link_words        more words that mark a link not worth following
  name_stopwords         words too common to show a page names the funder
  fold_words             words match at the start of a word, in text and
                         addresses folded to plain Latin letters (for sites
                         not in English); skip words are then checked in
                         the link's text as well as its address
  weak_skip_link_words   with fold_words: skip words that do not drop a link
                         whose text names a grant ("cariera", but not
                         "Granturi pentru dezvoltarea carierei")
  dedupe_known_by_site   one known funder per site

Usage:
    python scripts/build_register.py nz --confirm [--budget 3] [--only-category council]
"""

import tirith  # noqa: F401  — must come before anthropic

import argparse
import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from api.country_config import get_country_config  # noqa: E402
from api.funders import _to_ascii  # noqa: E402
from api.sources import register_path  # noqa: E402
from api.tenant import platform_sources_dir, platform_sources_path  # noqa: E402
from orchestrator import register_sweep as rs  # noqa: E402

LIST_MODEL = "claude-sonnet-5"
LIST_PRICE_IN, LIST_PRICE_OUT = 3.00, 15.00
TAVILY_PRICE = 0.008            # USD per credit: one search, or one page extract
ASKS = 2                        # the model's list differs a little each time
BUDGET_REACHED = "budget reached"

COUNTRY_SLUG = re.compile(r"[a-z]{2,8}")  # as api/metrics.py
REQUIRED = ("categories", "tier_one", "grant_words", "search_terms")


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
    for key in ("fold_words", "dedupe_known_by_site"):
        if not isinstance(reg.get(key, False), bool):
            raise _config_error(country, f"register.{key} must be true or false")
    grant_words = _strings(reg, "grant_words", country)
    skip_words = _strings(reg, "skip_link_words", country)
    weak_words = _strings(reg, "weak_skip_link_words", country)
    stopwords = _strings(reg, "name_stopwords", country)
    fold = _to_ascii if reg.get("fold_words") else None
    if weak_words and not fold:
        raise _config_error(country, "register.weak_skip_link_words needs fold_words")
    own_skip = weak_skip = None
    try:
        words = re.compile(_pattern(grant_words, fold), re.IGNORECASE)
        skip = rs.SKIP_LINK
        if skip_words and fold:
            # Kept apart from the English list: in fold mode a link's text is
            # checked too, and "Contact us to apply" is not a contact page.
            own_skip = re.compile(_pattern(skip_words, fold), re.IGNORECASE)
        elif skip_words:
            skip = re.compile(skip.pattern + "|" + _pattern(skip_words, fold), re.IGNORECASE)
        if weak_words:
            weak_skip = re.compile(_pattern(weak_words, fold), re.IGNORECASE)
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
    }


def has_grant_words(settings: dict, text: str) -> bool:
    fold = settings["fold"]
    return bool(settings["grant_words"].search(fold(text) if fold else text))


def _skipped(settings: dict, href: str, text: str) -> bool:
    """The country's own skip words, in the link's address or its text. A
    weak one gives way when the text names a grant. A strong one does not:
    "Concursul pentru ocuparea funcției publice" has a grant word, concurs."""
    fold = settings["fold"]
    address, label = fold(rs.decode(href)), fold(text)
    own, weak = settings["own_skip"], settings["weak_skip"]
    if own and (own.search(address) or own.search(label)):
        return True
    if weak and (weak.search(address) or weak.search(label)):
        return not settings["grant_words"].search(label)
    return False


def pick_links(page: dict, home: str, settings: dict) -> list[str]:
    """rs.pick_links with the country's words. A country without fold_words
    gets exactly the sweep's rule."""
    seen = {home.rstrip("/")}
    if not settings["fold"]:
        return rs.pick_links(page, home, seen, settings["grant_words"], settings["skip_link"])
    links = [(h, t) for h, t in page.get("links", []) if not _skipped(settings, h, t)]
    return rs.pick_links({**page, "links": links}, home, seen, settings["grant_words"],
                         settings["skip_link"], settings["fold"])


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


def fetch(url: str, budget: Budget) -> dict:
    """rs.fetch_page, counting any Tavily extract it tried for a page that
    refuses a plain fetch or has no text without scripts, whether or not
    the extract brought text back. Callers check the budget first."""
    page = rs.fetch_page(url)
    if page.get("extract_tried"):
        budget.add_extract()
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
            messages=[{"role": "user", "content": list_prompt(settings, cat, cfg.regions)}],
        ) as stream:
            resp = stream.get_final_message()
        budget.add_listing(resp.usage.input_tokens, resp.usage.output_tokens)
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        found = (rs.parse_json(text) or {}).get("funders") or []
        for f in found:
            if isinstance(f, dict) and f.get("name"):
                out.append({"name": str(f["name"]).strip(), "category": cat,
                            "website": str(f.get("website") or "").strip(),
                            "funding_url": f.get("funding_url"),
                            "regions": [r for r in (f.get("regions") or []) if r in cfg.regions],
                            "tier": 1 if f.get("tier") == 1 else 2})
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


def tavily_find(name: str, site: str, terms: str, budget: Budget) -> str | None:
    from tavily import TavilyClient
    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    res = client.search(query=f"{name} {terms}", max_results=5, include_domains=[site])
    budget.add_search()
    for r in res.get("results", []):
        if rs._site(urlsplit(r.get("url", "")).hostname) == site:
            return r["url"]
    return None


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
    res = client.search(query=f'"{name}" {country}', max_results=8)
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
    unresolved with that reason."""
    stopped = {**funder, "error": BUDGET_REACHED}
    if budget.exhausted:
        return stopped
    home = (fetch(funder["website"], budget) if funder["website"].startswith("http")
            else {"error": "no address given"})
    if home.get("error") and use_search:
        if budget.exhausted:
            return stopped
        try:
            found = tavily_site(funder["name"], settings["country"], budget)
        except Exception as exc:  # noqa: BLE001
            found = None
            print(f"[register] site search failed for {funder['name']}: {type(exc).__name__}")
        if found:
            root = "{0.scheme}://{0.netloc}/".format(urlsplit(found))
            for candidate in (found, root):
                if budget.exhausted:
                    return stopped
                page = fetch(candidate, budget)
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
    if isinstance(guess, str) and rs._site(urlsplit(guess).hostname) == site:
        if budget.exhausted:
            return stopped
        page = fetch(guess, budget)
        if not page.get("error") and has_grant_words(settings, page.get("text", "")):
            return {**funder, "url": page["url"], "resolved_by": "model"}
    links = pick_links(home, home["url"], settings)
    if links:
        return {**funder, "url": links[0], "resolved_by": "link"}
    if use_search:
        if budget.exhausted:
            return stopped
        try:
            found = tavily_find(funder["name"], site, settings["search_terms"], budget)
        except Exception as exc:  # noqa: BLE001
            found = None
            print(f"[register] search failed for {funder['name']}: {type(exc).__name__}")
        if found:
            return {**funder, "url": found, "resolved_by": "search"}
    if has_grant_words(settings, home.get("text", "")):
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
              f"are saved. Re-run with a larger --budget to list the rest.")
        return 1
    if args.limit:
        candidates = candidates[:args.limit]

    # A funder already known keeps its known page; the model's entry only
    # supplies the category and tier.
    known = {k["name"].lower(): k
             for k in known_funders(args.country, settings["dedupe_known_by_site"])}
    slugs = {rs.slugify_funder(n, args.country): n for n in known}
    todo = []
    for c in candidates:
        match = known.get(c["name"].lower()) or known.get(
            slugs.get(rs.slugify_funder(c["name"], args.country), ""))
        if match:
            match["category"], match["tier"] = c["category"], min(match["tier"], c["tier"])
            match["regions"] = match["regions"] or c["regions"]
        else:
            todo.append(c)
    print(f"[register] {len(known)} funders already known, {len(todo)} to resolve")

    with ThreadPoolExecutor(max_workers=rs.CRAWL_CONCURRENCY) as pool:
        resolved = list(pool.map(lambda c: resolve(c, not args.no_search, settings, budget), todo))
    if not args.only_category:
        resolved = list(known.values()) + resolved
    funders = [{k: r[k] for k in ("name", "category", "tier", "regions", "url", "resolved_by")}
               for r in resolved if r.get("url")]
    unresolved = [{"name": r["name"], "category": r["category"], "website": r["website"],
                   "error": r["error"]} for r in resolved if not r.get("url")]
    funders.sort(key=lambda f: (f["tier"], f["category"], f["name"]))
    stopped = sum(1 for r in unresolved if r["error"] == BUDGET_REACHED)
    # A register short of funders the budget cut off is not written: once it
    # exists, its tier 1 becomes the country's must-appear list.
    written = None if (args.limit or args.only_category or stopped) else path
    if written:
        write_json(path, {"country": args.country, "funders": funders, "unresolved": unresolved})
    by = {}
    for f in funders:
        by.setdefault(f["category"], [0, 0])[f["tier"] - 1] += 1
    tavily_usd = (budget.searches + budget.extracts) * TAVILY_PRICE
    print(json.dumps({
        "candidates": len(candidates), "resolved": len(funders), "unresolved": len(unresolved),
        "stopped_by_budget": stopped,
        "resolved_by": {k: sum(1 for f in funders if f["resolved_by"] == k)
                        for k in ("known", "model", "link", "search", "site-search", "home")},
        "tier_1": sum(1 for f in funders if f["tier"] == 1),
        "by_category_tier1_tier2": by,
        "spent_usd": round(budget.spent, 3), "model_usd": round(budget.spent - tavily_usd, 3),
        "model_calls": budget.calls, "tavily_searches": budget.searches,
        "tavily_extracts": budget.extracts, "tavily_usd": round(tavily_usd, 3),
        "written": written,
        "unresolved_sample": unresolved[:12],
    }, indent=2, ensure_ascii=False))
    return 0 if not stopped else 1


if __name__ == "__main__":
    sys.exit(main())
