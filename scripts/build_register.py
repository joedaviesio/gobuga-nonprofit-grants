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

Usage:
    python scripts/build_register.py nz --confirm [--budget 3] [--only-category council]
"""

import tirith  # noqa: F401  — must come before anthropic

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from api.country_config import get_country_config  # noqa: E402
from api.sources import register_path  # noqa: E402
from orchestrator import register_sweep as rs  # noqa: E402

LIST_MODEL = "claude-sonnet-5"
LIST_PRICE_IN, LIST_PRICE_OUT = 3.00, 15.00
TAVILY_PRICE = 0.008

CATEGORIES = {
    "government": "central government departments, ministries and Crown entities that run "
                  "contestable funds open to community organisations, iwi, schools or clubs "
                  "(list each funder once, not each fund)",
    "council": "every territorial authority and regional council: all 67 city and district "
               "councils and all 11 regional councils",
    "community-trust": "the 12 community trusts formed from the trust banks",
    "community-foundation": "community foundations (members of Community Foundations of "
                            "Aotearoa New Zealand)",
    "energy-trust": "energy, power and lines consumer trusts that make community grants",
    "gaming": "class 4 gaming societies that distribute grants to the community",
    "sports-trust": "regional sports trusts, and national sport funders",
    "licensing-trust": "licensing trusts and their charitable foundations",
    "philanthropic": "private, family and statutory philanthropic trusts and foundations that "
                     "accept applications, including funds administered by Perpetual Guardian "
                     "and Public Trust (list the fund, with the administrator's site if it has "
                     "no site of its own)",
    "corporate": "corporate foundations and company community-grant programmes",
    "maori": "iwi, rūnanga, Māori trusts and Māori-focused funders that make grants",
}

PROMPT = """List funders in New Zealand of this kind: {what}.

Only funders you are confident exist and that give grants an organisation or person can \
apply for. Do not invent names or web addresses; leave out any you are unsure of. Aim to be \
complete for this kind.

Answer with one JSON object and nothing else:
{{"funders": [{{"name": "official name", "website": "https://...", "funding_url": \
"https://... the page about applying for funding, or null if unsure", "regions": ["slugs"], \
"tier": 1 or 2}}]}}

regions: slugs from this list only: {regions}. Use ["national"] for a funder open nationwide.
tier 1: a funder that distributes more than about NZD 5 million a year, or is the main \
funder for its region. tier 2: everyone else."""


def list_candidates(country: str, categories: list[str], budget: rs.Budget) -> list[dict]:
    import anthropic
    client = anthropic.Anthropic(max_retries=4)
    cfg = get_country_config(country)
    out = []
    for cat in categories:
        if budget.exhausted:
            print(f"[register] budget reached before {cat}")
            break
        with client.messages.stream(
            model=LIST_MODEL, max_tokens=16000,
            messages=[{"role": "user", "content": PROMPT.format(
                what=CATEGORIES[cat], regions=", ".join(cfg.regions))}],
        ) as stream:
            resp = stream.get_final_message()
        with budget._lock:
            budget.calls += 1
            budget.spent += (resp.usage.input_tokens / 1e6 * LIST_PRICE_IN
                             + resp.usage.output_tokens / 1e6 * LIST_PRICE_OUT)
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        found = (rs.parse_json(text) or {}).get("funders") or []
        for f in found:
            if isinstance(f, dict) and f.get("name") and f.get("website"):
                out.append({"name": str(f["name"]).strip(), "category": cat,
                            "website": str(f["website"]).strip(),
                            "funding_url": f.get("funding_url"),
                            "regions": [r for r in (f.get("regions") or []) if r in cfg.regions],
                            "tier": 1 if f.get("tier") == 1 else 2})
        print(f"[register] {cat}: {len(found)} listed, ${budget.spent:.2f} spent")
    return out


def tavily_find(name: str, site: str) -> str | None:
    from tavily import TavilyClient
    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    res = client.search(query=f"{name} grants funding how to apply", max_results=5,
                        include_domains=[site])
    for r in res.get("results", []):
        if rs._site(urlsplit(r.get("url", "")).hostname) == site:
            return r["url"]
    return None


def resolve(funder: dict, use_search: bool) -> dict:
    """Fill `url` (the funding page) and `resolved_by`, or `error`."""
    home = rs.fetch_page(funder["website"])
    if home.get("error"):
        return {**funder, "error": home["error"]}
    site = rs._site(urlsplit(home["url"]).hostname)
    guess = funder.get("funding_url")
    if isinstance(guess, str) and rs._site(urlsplit(guess).hostname) == site:
        page = rs.fetch_page(guess)
        if not page.get("error") and rs.GRANT_WORDS.search(page.get("text", "")):
            return {**funder, "url": page["url"], "resolved_by": "model"}
    links = rs.pick_links(home, home["url"], {home["url"].rstrip("/")})
    if links:
        return {**funder, "url": links[0], "resolved_by": "link"}
    if use_search:
        try:
            found = tavily_find(funder["name"], site)
        except Exception as exc:  # noqa: BLE001
            found = None
            print(f"[register] search failed for {funder['name']}: {type(exc).__name__}")
        if found:
            return {**funder, "url": found, "resolved_by": "search", "searched": True}
    if rs.GRANT_WORDS.search(home.get("text", "")):
        return {**funder, "url": home["url"], "resolved_by": "home", "searched": use_search}
    return {**funder, "error": "no funding page found", "searched": use_search}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("country")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--budget", type=float, default=3.0)
    ap.add_argument("--only-category", action="append", choices=list(CATEGORIES))
    ap.add_argument("--no-search", action="store_true", help="do not use Tavily")
    ap.add_argument("--limit", type=int, help="resolve only the first N candidates")
    args = ap.parse_args(argv)
    categories = args.only_category or list(CATEGORIES)
    path = register_path(args.country)
    if not args.confirm:
        print(f"Would list funders in {len(categories)} categories with {LIST_MODEL}, check each "
              f"website, and write {path}. Budget ${args.budget:.2f}. Re-run with --confirm.")
        return 1

    budget = rs.Budget(args.budget)
    candidates_path = path.replace("-register.json", "-register-candidates.json")
    if os.path.exists(candidates_path) and not args.only_category:
        candidates = json.load(open(candidates_path, encoding="utf-8"))
        print(f"[register] reusing {len(candidates)} candidates from {candidates_path}")
    else:
        candidates = list_candidates(args.country, categories, budget)
        seen, unique = set(), []
        for c in candidates:
            key = c["name"].lower()
            if key not in seen:
                seen.add(key)
                unique.append(c)
        candidates = unique
        if not args.only_category:
            with open(candidates_path, "w", encoding="utf-8") as f:
                json.dump(candidates, f, indent=2, ensure_ascii=False)
    if args.limit:
        candidates = candidates[:args.limit]

    with ThreadPoolExecutor(max_workers=rs.CRAWL_CONCURRENCY) as pool:
        resolved = list(pool.map(lambda c: resolve(c, not args.no_search), candidates))
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
                        for k in ("model", "link", "search", "home")},
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
