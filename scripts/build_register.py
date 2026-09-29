#!/usr/bin/env python3
"""Build the funder register: `platform/sources/<country>-register.json`.

Three steps, each resumable from the file the step before it wrote:

  1. candidates  a model lists the funders it knows, one category at a time
  2. check       each funder's website is fetched; one that does not answer
                 is dropped to `unresolved` (the defence against a made-up name)
  3. resolve     the funding page is chosen by rule from the links on the
                 home page, or by one Tavily search limited to that site

Costs real money (a few model calls, one search per unresolved funder), so it
asks for `--confirm`. Without it, it prints what it would do.

The funder categories, the tier 1 threshold and the words that mark a funding
page come from the `register` block of the country's config
(`platform/sources/<country>.json`).

Usage:
    python scripts/build_register.py nz --confirm [--budget 3] [--only-category council]
"""

import tirith  # noqa: F401  — must come before anthropic

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from api.country_config import get_country_config  # noqa: E402
from api.funders import _to_ascii  # noqa: E402
from api.sources import register_path  # noqa: E402
from api.tenant import platform_sources_path  # noqa: E402
from orchestrator import register_sweep as rs  # noqa: E402

LIST_MODEL = "claude-sonnet-5"
LIST_PRICE_IN, LIST_PRICE_OUT = 3.00, 15.00
TAVILY_PRICE = 0.008

REQUIRED = ("categories", "tier_one", "grant_words", "search_terms")


def register_settings(country: str) -> dict:
    """The country's `register` block, checked and ready to use. Stops with a
    message naming what is missing, before any call is paid for."""
    cfg = get_country_config(country)
    reg = cfg.register or {}
    missing = [k for k in REQUIRED if not reg.get(k)]
    if missing:
        raise SystemExit(f"[register] no register {', '.join(missing)} for {country!r}: add them "
                         f"to the \"register\" block of {platform_sources_path(country)}")
    skip = rs.SKIP_LINK
    if reg.get("skip_link_words"):
        skip = re.compile(skip.pattern + "|" + "|".join(map(re.escape, reg["skip_link_words"])),
                          re.IGNORECASE)
    return {
        "country": cfg.country_label,
        "categories": dict(reg["categories"]),
        "tier_one": reg["tier_one"],
        "grant_words": re.compile("|".join(map(re.escape, reg["grant_words"])), re.IGNORECASE),
        "skip_link": skip,
        "search_terms": reg["search_terms"],
        # Folded like the names they are checked against, so "Fundația" and
        # "Fundatia" are one word.
        "name_stopwords": {_to_ascii(w) for w in reg.get("name_stopwords", [])},
    }


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


def list_candidates(country: str, categories: list[str], budget: rs.Budget,
                    settings: dict) -> list[dict]:
    import anthropic
    client = anthropic.Anthropic(max_retries=4)
    cfg = get_country_config(country)
    out = []
    # The model's list differs a little each time it is asked, so each
    # category is asked twice and the answers joined.
    for cat in [c for c in categories for _ in range(2)]:
        if budget.exhausted:
            print(f"[register] budget reached before {cat}")
            break
        with client.messages.stream(
            model=LIST_MODEL, max_tokens=16000,
            messages=[{"role": "user", "content": list_prompt(settings, cat, cfg.regions)}],
        ) as stream:
            resp = stream.get_final_message()
        with budget._lock:
            budget.calls += 1
            budget.spent += (resp.usage.input_tokens / 1e6 * LIST_PRICE_IN
                             + resp.usage.output_tokens / 1e6 * LIST_PRICE_OUT)
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        found = (rs.parse_json(text) or {}).get("funders") or []
        for f in found:
            if isinstance(f, dict) and f.get("name"):
                out.append({"name": str(f["name"]).strip(), "category": cat,
                            "website": str(f.get("website") or "").strip(),
                            "funding_url": f.get("funding_url"),
                            "regions": [r for r in (f.get("regions") or []) if r in cfg.regions],
                            "tier": 1 if f.get("tier") == 1 else 2})
        print(f"[register] {cat}: {len(found)} listed, ${budget.spent:.2f} spent")
    return out


def known_funders(country: str) -> list[dict]:
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
    return list(out.values())


def tavily_find(name: str, site: str, terms: str) -> str | None:
    from tavily import TavilyClient
    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    res = client.search(query=f"{name} {terms}", max_results=5, include_domains=[site])
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


def tavily_site(name: str, country: str, budget: rs.Budget | None = None) -> str | None:
    """The funder's own site, when the guessed address does not answer.

    A cheap model picks among the search results; the caller then checks that
    the page it picked names the funder, so a wrong pick is dropped."""
    from tavily import TavilyClient
    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    res = client.search(query=f'"{name}" {country}', max_results=8)
    results = [r for r in res.get("results", []) if r.get("url")]
    if not results:
        return None
    listing = "\n".join(f"{i}. {r['url']} | {(r.get('title') or '')[:90]} | "
                        f"{(r.get('content') or '')[:160]}" for i, r in enumerate(results))
    raw, tin, tout = rs.anthropic_ask("You answer with JSON only.", PICK_PROMPT.format(
        country=country, name=name, results=listing))
    if budget is not None:
        budget.add(tin, tout)
    index = (rs.parse_json(raw) or {}).get("index")
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(results):
        return results[index]["url"]
    return None


def names_funder(page: dict, name: str, stop=frozenset()) -> bool:
    """The page carries every distinctive word of the funder's name.

    Both sides are folded to plain Latin letters: Romanian pages mix ș and ş
    or drop the diacritics, and a Cyrillic name may be written either way."""
    text = _to_ascii(rs._norm(page.get("text", "")))
    core = re.sub(r"\([^)]*\)", " ", name)
    words = _name_words(core, stop) or re.findall(r"[^\W\d_]{3,}", _to_ascii(core))
    return bool(words) and all(w in text for w in words)


BUDGET: rs.Budget | None = None


def resolve(funder: dict, use_search: bool, settings: dict) -> dict:
    """Fill `url` (the funding page) and `resolved_by`, or `error`."""
    words = settings["grant_words"]
    home = (rs.fetch_page(funder["website"]) if funder["website"].startswith("http")
            else {"error": "no address given"})
    if home.get("error") and use_search:
        try:
            found = tavily_site(funder["name"], settings["country"], BUDGET)
        except Exception as exc:  # noqa: BLE001
            found = None
            print(f"[register] site search failed for {funder['name']}: {type(exc).__name__}")
        funder = {**funder, "searched": True}
        if found:
            root = "{0.scheme}://{0.netloc}/".format(urlsplit(found))
            for candidate in (found, root):
                page = rs.fetch_page(candidate)
                if not page.get("error") and names_funder(page, funder["name"],
                                                          settings["name_stopwords"]):
                    home = page
                    break
            else:
                return {**funder, "error": "search found no site that names the funder"}
    if home.get("error"):
        return {**funder, "error": home["error"]}
    site = rs._site(urlsplit(home["url"]).hostname)
    guess = funder.get("funding_url")
    if isinstance(guess, str) and rs._site(urlsplit(guess).hostname) == site:
        page = rs.fetch_page(guess)
        if not page.get("error") and words.search(page.get("text", "")):
            return {**funder, "url": page["url"], "resolved_by": "model"}
    links = rs.pick_links(home, home["url"], {home["url"].rstrip("/")}, words,
                          settings["skip_link"])
    if links:
        return {**funder, "url": links[0], "resolved_by": "link"}
    if use_search:
        try:
            found = tavily_find(funder["name"], site, settings["search_terms"])
        except Exception as exc:  # noqa: BLE001
            found = None
            print(f"[register] search failed for {funder['name']}: {type(exc).__name__}")
        if found:
            return {**funder, "url": found, "resolved_by": "search", "searched": True}
    if words.search(home.get("text", "")):
        return {**funder, "url": home["url"], "resolved_by": "home", "searched": use_search}
    return {**funder, "error": "no funding page found", "searched": use_search}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("country")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--budget", type=float, default=3.0)
    ap.add_argument("--only-category", action="append")
    ap.add_argument("--no-search", action="store_true", help="do not use Tavily")
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

    global BUDGET
    budget = BUDGET = rs.Budget(args.budget)
    candidates_path = path.replace("-register.json", "-register-candidates.json")
    if os.path.exists(candidates_path) and not args.only_category:
        candidates = json.load(open(candidates_path, encoding="utf-8"))
        print(f"[register] reusing {len(candidates)} candidates from {candidates_path}")
    else:
        candidates = list_candidates(args.country, categories, budget, settings)
        seen, unique = set(), []
        for c in candidates:
            key = rs.slugify_funder(re.sub(r"\([^)]*\)|\b(limited|ltd|the)\b", " ", c["name"],
                                           flags=re.I), args.country)
            if key and key not in seen:
                seen.add(key)
                unique.append(c)
        candidates = unique
        if not args.only_category:
            with open(candidates_path, "w", encoding="utf-8") as f:
                json.dump(candidates, f, indent=2, ensure_ascii=False)
    if args.limit:
        candidates = candidates[:args.limit]

    # A funder already known keeps its known page; the model's entry only
    # supplies the category and tier.
    known = {k["name"].lower(): k for k in known_funders(args.country)}
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
        resolved = list(pool.map(lambda c: resolve(c, not args.no_search, settings), todo))
    if not args.only_category:
        resolved = list(known.values()) + resolved
    searches = sum(1 for r in resolved if r.get("searched"))
    funders = [{k: r[k] for k in ("name", "category", "tier", "regions", "url", "resolved_by")}
               for r in resolved if r.get("url")]
    unresolved = [{"name": r["name"], "category": r["category"], "website": r["website"],
                   "error": r["error"]} for r in resolved if not r.get("url")]
    funders.sort(key=lambda f: (f["tier"], f["category"], f["name"]))
    if not args.limit and not args.only_category:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"country": args.country, "funders": funders, "unresolved": unresolved},
                      f, indent=2, ensure_ascii=False)
            f.write("\n")
    by = {}
    for f in funders:
        by.setdefault(f["category"], [0, 0])[f["tier"] - 1] += 1
    print(json.dumps({
        "candidates": len(candidates), "resolved": len(funders), "unresolved": len(unresolved),
        "resolved_by": {k: sum(1 for f in funders if f["resolved_by"] == k)
                        for k in ("known", "model", "link", "search", "site-search", "home")},
        "tier_1": sum(1 for f in funders if f["tier"] == 1),
        "by_category_tier1_tier2": by,
        "model_usd": round(budget.spent, 3), "tavily_searches": searches,
        "tavily_usd_if_paid": round(searches * TAVILY_PRICE, 2),
        "written": None if (args.limit or args.only_category) else path,
        "unresolved_sample": unresolved[:12],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
