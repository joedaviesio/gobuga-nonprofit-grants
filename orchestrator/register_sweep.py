"""Register sweep — visit every funder on a register, read its pages, keep
only what the page itself supports.

Replaces the agent sweep's search-and-hope with four plain stages:

  1. register  `platform/sources/<country>-register.json`: one record per
               funder with the URL of its funding page
  2. crawl     fetch that page and the grant-looking links on the same site
               (no model, no cost)
  3. extract   one model call per page lists the programmes on it and copies
               the sentence each fact was read from
  4. check     code, not a model: a fact is kept only if its sentence occurs
               on the page (the verify pass's own checks)

A row is born verified or not at all, so there is no separate verify pass.

Spend is counted as it goes and the run stops at `--budget` (USD), keeping
what it has. Nothing is published unless `--publish` is given.

`--crawl-only` stops after stage 2: it lists the pages each funder's crawl
chose, how each was reached and whether it passed the page filter, and makes
no model call and no Tavily call. It is free.

A country whose sites are not in English sets a `sweep` block in its config
(`platform/sources/<country>.json`). Every key is optional; without the
block the sweep is New Zealand's, unchanged:

  country_words    crawl by the `register` block's words (the page filter,
                   the links followed and the skip words) instead of the
                   English defaults; see orchestrator/register_words.py
  skip_link_words  with country_words: more words that mark a link not
                   worth following, beside the register's own
  language_paths   with country_words: path prefixes naming a language
                   (["ro", "ru", "en"]); when the funder's page sits under
                   one, links to the other languages' copies are not followed
  link_words       with country_words: more words that make a link worth
                   following (not a page worth reading): "voucher",
                   "postdoctorat", names of programmes without a grant word
  stale_years      with country_words: a link whose text names only years
                   this many years back or more is not followed (2: in
                   2026, "2020-2024")
  rank_strong_links   with country_words: links naming no past year, then
                   links holding a strong grant word, are followed first,
                   before the page cap is reached
  strict_links     with country_words: a followed link must also pass the
                   register's strict page rules (not a document, dated or
                   news path, or headline slug)
  fold_letters     ş/ș, ţ/ț and typographic quotes are one letter when an
                   excerpt or title is looked for on the page (the stored
                   text keeps the page's own letters), and title keys are
                   folded to plain Latin letters
  number_format    "ro": 200.000 and 200 000 are thousands, 1,5 is a
                   decimal, and mii, mil., milioane, тыс., млн multiply
  currencies       {"MDL": {"max": ..., "markers": [...]}, ...}: the model
                   names each amount's currency, and it is kept only when
                   one of its markers stands beside a stated figure in the
                   amount excerpt; otherwise the amount is left blank
  other_dollar_words  with currencies: words that make a "$" not USD, in the
                   excerpt; and for a bare "$" anywhere on the page
  other_dollar_tlds   with currencies: top-level domains (ca, nz, au) whose
                   sites' bare "$" is never taken as USD
  general_title    the title of a funder's unnamed scheme, "{funder}" filled
  not_a_grant_words   more title words that mark a loan or tender, unless
                   a grant_title_words word is in the title too
  grant_title_words   with not_a_grant_words: words that keep such a title
                   (and such a summary, for loan_text_words)
  loan_text_words  words that, in the model's summary or eligibility with
                   no grant_title_words word, mark a loan
  reject_kinds     kinds of money the model names (with the country prompt's
                   "kind") that are not published: loan, guarantee, other
  mixed_kind       "keep" (the default) or "reject": part grant, part loan
  rolling_words    more words that make a deadline rolling
  not_covered_region  a region slug the model may give for a programme
                   limited to an area not yet covered; a row given only
                   that region is rejected, as is one whose title names a
                   not_covered_words word, or whose eligibility excerpt
                   names one with no covered_with_words word
  not_covered_words   with not_covered_region
  covered_with_words  with not_covered_region: words that take the area in
                   beside the rest of the country ("inclusiv", "включая")
  prefer_dedicated_pages  a row from a page linking to another is dropped
                   when that page's row names the same programme (see
                   prefer_dedicated_pages)
  title_stopwords  with prefer_dedicated_pages: title words that do not
                   tell programmes apart ("programul", "susținere")
  join_translations  one funder's Cyrillic-titled and Latin-titled rows with
                   the same closing date and amounts are one programme, kept
                   once under the Latin title (see join_translations)
  region_names     {slug: name}, shown to the model beside each slug
  prompt           the country's prompt wording: funder_site, language,
                   not_funding, amounts, region_rule

CLI:
    python -m orchestrator.register_sweep nz --tier 1 --limit 20 --budget 2
    python -m orchestrator.register_sweep nz --budget 40 --publish
    python -m orchestrator.register_sweep md --only "Ministerul Culturii" --crawl-only
"""

import tirith  # noqa: F401  — must come before anthropic; routes calls through local tirith proxy

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx

from api import tenant
from api.country_config import get_country_config
from api.funders import _to_ascii, slugify_funder, transliterate_cyrillic
from orchestrator import register_words as rw
from orchestrator.register_words import GRANT_WORDS, SKIP_LINK, _site, decode, pick_links  # noqa: F401
from orchestrator.tools import MAX_FETCH_REDIRECTS, UnsafeUrlError, assert_public_url
from orchestrator.verify_pass import (MIN_EXCERPT_CHARS, _day_in, _norm, _plausible_date, _verbatim,
                                      _year_conflicts)

MODEL = "claude-haiku-4-5-20251001"
# USD per million tokens. Deliberately the published Haiku 4.5 price, not the
# older figure in api/usage.py, so the budget guard errs on the safe side.
PRICE_IN, PRICE_OUT = 1.00, 5.00

MAX_PAGES_PER_FUNDER = 8
MAX_DEPTH = 2
MAX_PAGE_CHARS = 24000
MIN_PAGE_CHARS = 300
FETCH_TIMEOUT_S = 20
PER_HOST_DELAY_S = 1.0          # be polite to small trusts' servers
MAX_BYTES = 3_000_000
EXTRACT_CONCURRENCY = 6
CRAWL_CONCURRENCY = 12
MAX_AMOUNT = 5_000_000          # above this a figure is a fund total, not a grant
USER_AGENT = "GoBuga-GrantBot/0.2 (+https://gobuga.org/llms.txt)"
MAX_ANSWER_TOKENS = 12000
# The budget holds each call at its worst case: the longest answer, and its
# text at two characters a token, a cautious guess for Romanian and Russian
# (English runs nearer four). Should a page tokenise tighter still, the
# ceiling can be passed by that difference, within one call's cost.
WORST_CHARS_PER_TOKEN = 2.0
ESTIMATE_PROMPT_TOKENS = 1500   # --crawl-only's cost guide: the prompt, per page
ESTIMATE_ANSWER_TOKENS = 1500   # and a long answer, per page

# --- Register ------------------------------------------------------------------

def register_path(country: str) -> str:
    return os.path.join(tenant.platform_sources_dir(), f"{country}-register.json")


def load_register(country: str) -> list[dict]:
    """Funders with a usable `url`. Raises if the file is missing or malformed."""
    with open(register_path(country), encoding="utf-8") as f:
        data = json.load(f)
    funders = data["funders"] if isinstance(data, dict) else data
    out, seen = [], set()
    for f in funders:
        name, url = (f.get("name") or "").strip(), (f.get("url") or "").strip()
        if not name or not url.startswith(("http://", "https://")) or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "url": url, "tier": int(f.get("tier") or 2),
                    "category": f.get("category") or "other",
                    "regions": [r for r in (f.get("regions") or []) if isinstance(r, str)]})
    return out


def tier_one_names(country: str) -> list[str]:
    try:
        return [f["name"] for f in load_register(country) if f["tier"] == 1]
    except (OSError, ValueError, KeyError):
        return []


# --- Crawl -----------------------------------------------------------------------

class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def main_content(html: str) -> str:
    """The page without its furniture: the <main> or <article> element when
    there is one, else the page with navigation, header, footer and asides
    removed. Menus listing every fund otherwise crowd out the page's own text."""
    html = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    for tag in ("main", "article"):
        found = re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html, flags=re.S | re.I)
        body = " ".join(found)
        if len(re.sub(r"<[^>]+>", " ", body).split()) >= 60:
            html = body
            break
    return re.sub(r"<(nav|header|footer|aside|form)\b[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)


def html_to_text(html: str) -> str:
    html = main_content(html)
    html = re.sub(r"<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    import html as _html
    text = _html.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def pdf_to_text(data: bytes) -> str:
    import fitz  # pymupdf
    with fitz.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in list(doc)[:15])


_host_locks: dict[str, threading.Lock] = {}
_host_last: dict[str, float] = {}
_host_guard = threading.Lock()


def _wait_for_host(host: str) -> None:
    with _host_guard:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        gap = time.monotonic() - _host_last.get(host, 0.0)
        if gap < PER_HOST_DELAY_S:
            time.sleep(PER_HOST_DELAY_S - gap)
        _host_last[host] = time.monotonic()


def fetch_page(url: str, use_tavily: bool = True) -> dict:
    """{"url", "text", "links"} or {"url", "error"}. Follows redirects by hand
    so every hop is checked against the public-address guard. Without
    `use_tavily` a page that refuses a plain fetch stays refused."""
    try:
        current = url
        with httpx.Client(timeout=FETCH_TIMEOUT_S, follow_redirects=False,
                          headers={"User-Agent": USER_AGENT}) as client:
            for _ in range(MAX_FETCH_REDIRECTS + 1):
                assert_public_url(current)
                _wait_for_host(urlsplit(current).hostname or "")
                resp = client.get(current)
                if not resp.is_redirect:
                    break
                current = urljoin(current, resp.headers.get("location", ""))
            else:
                raise UnsafeUrlError("too many redirects")
            resp.raise_for_status()
            body = resp.content[:MAX_BYTES]
            kind = resp.headers.get("content-type", "").lower()
        if "pdf" in kind or current.lower().endswith(".pdf"):
            return {"url": current, "text": pdf_to_text(body)[:MAX_PAGE_CHARS], "links": []}
        html = body.decode(resp.encoding or "utf-8", errors="replace")
        parser = _Links()
        parser.feed(html)
        links = [(urldefrag(urljoin(current, h))[0], t) for h, t in parser.links if h]
        text = html_to_text(html)
        if len(text) < MIN_PAGE_CHARS:
            # Built in the browser by script: the plain fetch sees an empty shell.
            rendered = fetch_blocked(current, "page has no text without scripts", use_tavily)
            if not rendered.get("error"):
                rendered["links"] = rendered["links"] or links
                return rendered
            if rendered.get("extract_tried"):
                # The short page stands, but a caller counting spend must
                # still see that an extract was paid for.
                return {"url": current, "text": text[:MAX_PAGE_CHARS], "links": links,
                        "extract_tried": True, "extract_error": rendered["error"],
                        **({"extract_message": rendered["extract_message"]}
                           if "extract_message" in rendered else {})}
        return {"url": current, "text": text[:MAX_PAGE_CHARS], "links": links}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403, 406, 429, 503):
            return fetch_blocked(url, f"HTTP {exc.response.status_code}", use_tavily)
        return {"url": url, "error": f"HTTP {exc.response.status_code}"}
    except Exception as exc:  # noqa: BLE001 — one bad site must not stop the crawl
        return {"url": url, "error": f"{type(exc).__name__}: {exc}"[:200]}


SEARCH_CREDITS = {"extract": 0}
MARKDOWN_LINK = re.compile(r"\[([^\]]{1,200})\]\((https?://[^)\s]+)\)")


def fetch_blocked(url: str, why: str, use_tavily: bool = True) -> dict:
    """A page that refuses a plain fetch, read through Tavily's extract
    (one credit per five pages). Without a key, or without `use_tavily`,
    the refusal stands. `extract_tried` marks every answer from an
    attempt, failed or not."""
    if not use_tavily or not os.getenv("TAVILY_API_KEY"):
        return {"url": url, "error": why}
    try:
        from tavily import TavilyClient
        res = TavilyClient(api_key=os.environ["TAVILY_API_KEY"]).extract(urls=[url])
        raw = (res.get("results") or [{}])[0].get("raw_content") or ""
    except Exception as exc:  # noqa: BLE001
        # The message stays apart from the error, which is recorded as it
        # always was; the register build reads it to tell a 429 for too many
        # requests from one for credits used up.
        return {"url": url, "error": f"{why}; extract failed: {type(exc).__name__}",
                "extract_message": " ".join(str(exc).split())[:300], "extract_tried": True}
    SEARCH_CREDITS["extract"] += 1
    if not raw.strip():
        return {"url": url, "error": f"{why}; extract returned nothing", "extract_tried": True}
    links = [(urldefrag(urljoin(url, h))[0], t) for t, h in MARKDOWN_LINK.findall(raw)]
    text = re.sub(r"[ \t]+", " ", MARKDOWN_LINK.sub(r"\1", raw))
    return {"url": url, "text": text[:MAX_PAGE_CHARS], "links": links, "via": "tavily",
            "extract_tried": True}


def _language(url: str, languages) -> str | None:
    """The language an address's first path part names (/ro/, /ru/), or None."""
    first = next((p for p in urlsplit(url).path.split("/") if p), "").lower()
    return first if languages and first in languages else None


def _country_order(links: list[str], labels: dict, words: dict) -> list[str]:
    """With `stale_years`, a link whose text names only years at least that
    many years before `this_year` is dropped ("Program de Stat 2020-2023").
    Only the text counts: an address may carry a programme's first year
    ("...moldova-creativa-pentru-anii-2024" runs to 2028). With
    `rank_strong_links`, links naming no past year go first, then those
    holding a strong grant word, each in their order, so this year's call
    beats last year's press release before the page cap is reached."""
    fold = words["fold"]

    def years(h: str) -> list[int]:
        return [int(y) for y in re.findall(r"(?<!\d)20\d\d(?!\d)", labels.get(h, ""))]
    if words.get("stale_years"):
        links = [h for h in links
                 if not years(h) or max(years(h)) > words["this_year"] - words["stale_years"]]
    if words.get("rank_strong_links") and words.get("strong_words"):
        def key(h: str) -> tuple:
            said = f"{fold(labels.get(h, ''))} {fold(decode(urlsplit(h).path))}"
            past = bool(years(h)) and max(years(h)) < words["this_year"]
            return past, not words["strong_words"].search(said)
        links = sorted(links, key=key)
    return links


def crawl_funder(funder: dict, fetch=fetch_page, words: dict | None = None,
                 strict_links: bool = False) -> dict:
    """Pages worth reading for one funder, breadth first from its funding page.

    `words`, the country's register settings (register_words.register_settings),
    choose pages and links by the country's words instead of the English
    defaults; with `strict_links` a link must also pass its page rules.
    With `words`, a page is read once however it is reached: "www." or not,
    a redirect, a closing slash. `visits` records every address fetched,
    how it was reached and whether it passed the page filter, for
    --crawl-only."""
    seen, pages, errors, visits = {funder["url"].rstrip("/")}, [], [], []
    read: set[str] = set()
    frontier = [(funder["url"], 0, "registered page", None)]
    # Funding pages move. If the registered one is gone, or is a file or a
    # bot check rather than a page with links, start again from the home page.
    home = "{0.scheme}://{0.netloc}/".format(urlsplit(funder["url"]))
    tried_home = home.rstrip("/") in seen
    while (frontier or not tried_home) and len(pages) < MAX_PAGES_PER_FUNDER:
        if not frontier:
            if pages:
                break
            tried_home = True
            seen.add(home.rstrip("/"))
            frontier.append((home, 0, "home page, after the registered page gave nothing", None))
        url, depth, via, parent = frontier.pop(0)
        page = fetch(url)
        if page.get("error"):
            errors.append({"url": url, "error": page["error"]})
            visits.append({"url": url, "reached_by": via, "error": page["error"]})
            continue
        text = page["text"]
        grant_words = rw.has_grant_words(words, text) if words else GRANT_WORDS.search(text)
        kept = len(text) >= MIN_PAGE_CHARS and bool(grant_words)
        why_not = "too short" if len(text) < MIN_PAGE_CHARS else "no grant words"
        if words:
            parts = urlsplit(page["url"])
            address = f"{_site(parts.hostname)}{parts.path.rstrip('/')}?{parts.query}"
            if kept and address in read:
                kept, why_not = False, "already read"
            read.add(address)
        if kept:
            # `from`: the page whose link led here, for prefer_dedicated_pages.
            pages.append({"url": page["url"], "text": text, "from": parent})
        visits.append({"url": page["url"], "reached_by": via, "kept": kept, "chars": len(text),
                       **({} if kept else {"why_not": why_not}),
                       **({"through": "tavily"} if page.get("via") == "tavily" else {})})
        if depth < MAX_DEPTH:
            room = MAX_PAGES_PER_FUNDER * 2 - len(seen)
            if words:
                links = rw.country_pick_links(page, funder["url"], words, seen)
                if strict_links:
                    links = [h for h in links if not rw.page_problem(words, h)]
                start = _language(funder["url"], words.get("language_paths"))
                if start:
                    # The funder's page is in one language: its copies in
                    # the others (/ru/ beside /ro/) are the same calls again.
                    links = [h for h in links
                             if _language(h, words["language_paths"]) in (None, start)]
                links = _country_order(links, {h: t for h, t in page.get("links", [])}, words)
            else:
                links = pick_links(page, funder["url"], seen)
            labels = {h: t for h, t in page.get("links", [])}
            for href in links[:max(room, 0)]:
                seen.add(href.rstrip("/"))
                frontier.append((href, depth + 1,
                                 f"link \"{labels.get(href, '')[:80]}\" on {page['url']}",
                                 page["url"]))
    return {"funder": funder, "pages": pages, "errors": errors, "visits": visits}


# --- Extract ---------------------------------------------------------------------

SYSTEM_PROMPT = """You read one page from a New Zealand funder's website and list the grant \
programmes that page describes. Today is {today}. Answer with one JSON object and nothing else:

{{"programmes": [{{
  "title": "the programme's name as the page gives it",
  "deadline_state": "dated" | "rolling" | "closed" | "unknown",
  "deadline": "YYYY-MM-DD" or null,
  "deadline_excerpt": "..." or null,
  "amount_min": number or null,
  "amount_max": number or null,
  "amount_excerpt": "..." or null,
  "eligibility": "who can apply, in the page's own words, one or two sentences" or null,
  "eligibility_excerpt": "..." or null,
  "regions": ["region slugs from the allowed list"],
  "tags": ["tag slugs from the allowed list"],
  "summary": "two sentences, facts from the page only"
}}]}}

Rules:
- List a programme only if this page describes it: what it funds or who can apply. A bare \
link or a name in a menu is not a programme. A page with none returns {{"programmes": []}}.
- Many funders run one grants scheme with no name of its own ("apply for a grant"). If \
this page describes how to apply to the funder and names no programmes, list that one \
scheme with the title GENERAL and give its eligibility_excerpt.
- Only funding an organisation or person can apply for. Not tenders, loans, jobs or awards \
already given.
- dated: the page states the next closing date. rolling: the page says applications are \
taken at any time. closed: the page says the round is closed, or its only dates are past. \
unknown: the page gives no closing date; this is common and fine. Never guess or infer a date.
- Give eligibility_excerpt whenever the page says who can apply or what is funded; it is \
the evidence that the programme is real.
- Every excerpt is copied character for character from the page: one or two sentences, no \
paraphrase, no ellipsis. deadline_excerpt must contain the date, or the words that make it \
rolling or closed. amount_excerpt must contain the figures.
- Amounts are what one applicant can receive, in NZD, as plain numbers. Never the size of \
the whole fund. If the page gives only the fund's total, both amounts are null.
- Allowed regions: {regions}. Use "national" when the programme is open across the country.
- Allowed tags: {tags}. At most four."""

# For a country with a `sweep.prompt` block. New Zealand's is the one above,
# word for word; this one adds a currency per programme and asks for the
# summary and eligibility in the country's language.
COUNTRY_PROMPT = """You read one page from {funder_site} and list the grant \
programmes that page describes. Today is {today}. Answer with one JSON object and nothing else:

{{"programmes": [{{
  "title": "the programme's name exactly as the page gives it, in the page's language",
  "deadline_state": "dated" | "rolling" | "closed" | "unknown",
  "deadline": "YYYY-MM-DD" or null,
  "deadline_excerpt": "..." or null,
  "amount_min": number or null,
  "amount_max": number or null,
  "currency": {currency_codes} or null,
  "amount_excerpt": "..." or null,
  "eligibility": "who can apply, one or two sentences in {language}" or null,
  "eligibility_excerpt": "..." or null,
  "regions": ["region slugs from the allowed list"],
  "tags": ["tag slugs from the allowed list"],
  "summary": "two sentences in {language}, facts from the page only",
  "kind": "grant" | "subsidy" | "loan" | "guarantee" | "mixed" | "other",
  "kind_excerpt": "the sentence that shows what kind of money it is" or null
}}]}}

Rules:
- List a programme only if this page describes it: what it funds or who can apply. A bare \
link or a name in a menu is not a programme. A page with none returns {{"programmes": []}}.
- Many funders run one grants scheme with no name of its own ("apply for a grant"). If \
this page describes how to apply to the funder and names no programmes, list that one \
scheme with the title GENERAL and give its eligibility_excerpt.
- Only funding an organisation or person can apply for. {not_funding}
- kind: grant or subsidy is money that is not repaid; loan is credit repaid, usually with \
interest; guarantee backs someone's loan; mixed is part grant and part repayable; other is \
anything else (equity, prizes, services). Judge by what the page says, not by the \
programme's name. kind_excerpt is copied from the page.
- dated: the page states the next closing date. rolling: the page says applications are \
taken at any time. closed: the page says the round is closed, or its only dates are past. \
unknown: the page gives no closing date; this is common and fine. Never guess or infer a date.
- Give eligibility_excerpt whenever the page says who can apply or what is funded; it is \
the evidence that the programme is real.
- Every excerpt is copied character for character from the page, in the page's own \
language: one or two sentences, no paraphrase, no translation, no ellipsis. deadline_excerpt \
must contain the date, or the words that make it rolling or closed. amount_excerpt must \
contain the figures and the currency beside them.
- summary and eligibility are always written in {language}, whatever the language of the \
page. The title and every excerpt stay exactly as the page writes them.
- Amounts are what one applicant can receive, as plain numbers in the currency the page \
names beside them. Never the size of the whole fund. If the page gives only the fund's \
total, both amounts are null. {amounts}
- currency is the one the page names beside the amount: {currency_names}. If the page names \
no currency beside the amount, or another currency, give null for currency and both \
amounts. Never convert or guess a currency.
- Allowed regions: {regions}. Use "national" when the programme is open across the country. \
{region_rule}
- Allowed tags: {tags}. At most four."""


def system_prompt(cfg, today) -> str:
    """The prompt for `cfg`'s country: New Zealand's unless the country has
    a `sweep.prompt` block."""
    sweep = cfg.sweep or {}
    wording = sweep.get("prompt")
    if not wording:
        return SYSTEM_PROMPT.format(today=today.isoformat(), regions=", ".join(cfg.regions),
                                    tags=", ".join(cfg.tags))
    names = sweep.get("region_names") or {}
    currencies = sweep.get("currencies") or {}
    return COUNTRY_PROMPT.format(
        today=today.isoformat(), funder_site=wording["funder_site"], language=wording["language"],
        not_funding=wording.get("not_funding", ""), amounts=wording.get("amounts", ""),
        region_rule=wording.get("region_rule", ""),
        currency_codes=" | ".join(f'"{c}"' for c in currencies),
        currency_names=", ".join(f"{c} ({v['label']})" if v.get("label") else c
                                 for c, v in currencies.items()),
        regions=", ".join(f"{r} ({names[r]})" if r in names else r for r in cfg.regions),
        tags=", ".join(cfg.tags))


class Budget:
    """Counts spend; `spent >= cap` stops new work. A page's model call is
    first reserved at its worst case (`reserve`), under the lock, so the
    calls in flight together can never take the spend past the cap."""

    def __init__(self, cap_usd: float):
        self.cap, self.spent, self.reserved = cap_usd, 0.0, 0.0
        self.tokens_in = self.tokens_out = self.calls = 0
        self._lock = threading.Lock()

    def add(self, tokens_in: int, tokens_out: int, reserved: float = 0.0) -> None:
        with self._lock:
            self.tokens_in += tokens_in
            self.tokens_out += tokens_out
            self.calls += 1
            self.spent += tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT
            self.reserved -= reserved

    def reserve(self, usd: float) -> bool:
        """Hold `usd` for a call about to be made, or False when what is
        spent and held already leaves no room for it."""
        with self._lock:
            if self.spent + self.reserved + usd > self.cap:
                return False
            self.reserved += usd
            return True

    def release(self, usd: float) -> None:
        with self._lock:
            self.reserved -= usd

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.cap

    def summary(self) -> dict:
        return {"cap_usd": self.cap, "spent_usd": round(self.spent, 4), "calls": self.calls,
                "input_tokens": self.tokens_in, "output_tokens": self.tokens_out}


_client = None


def anthropic_ask(system: str, user: str) -> tuple[str, int, int]:
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=4)
    resp = _client.messages.create(model=MODEL, max_tokens=MAX_ANSWER_TOKENS, system=system,
                                   messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return text, resp.usage.input_tokens, resp.usage.output_tokens


def parse_json(raw: str):
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    try:
        return json.loads(raw[start:end + 1]) if start >= 0 and end > start else None
    except ValueError:
        return None


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if 0 < value <= MAX_AMOUNT else None


def _figures_in(excerpt: str, *amounts) -> bool:
    """Every stated amount appears in the excerpt as digits ("20,000", "20000", "$20k")."""
    digits = re.sub(r"[,\s]", "", excerpt.lower())
    for amount in (a for a in amounts if a is not None):
        whole = str(int(amount))
        thousands = f"{int(amount) // 1000}k" if amount % 1000 == 0 else None
        if whole not in digits and not (thousands and thousands in digits):
            return False
    return True


# Asked not to list these, the model sometimes does. They are not grants.
NOT_A_GRANT = re.compile(r"\b(loans?|tenders?|procurement|vacanc(y|ies)|sponsorship of us)\b",
                         re.IGNORECASE)

ROLLING_WORDS = re.compile(
    r"any ?time|year[- ]round|all year|throughout the year|ongoing|rolling|continuous|"
    r"no (closing|set|fixed) (date|deadline)|no deadline|always open|open all", re.IGNORECASE)


def title_on_page(title: str, page_norm: str) -> bool:
    """The title as written, or nearly every word of it, occurs on the page.
    Pages split a name across a heading and a line, or use a different dash."""
    if title.casefold() in page_norm:
        return True
    words = [w for w in re.findall(r"[^\W\d_]{4,}", title.casefold())]
    if len(words) < 2:
        return False
    found = sum(1 for w in words if w in page_norm)
    return found / len(words) >= 0.8


# --- A country's own rules (the `sweep` block) --------------------------------------

# Romanian pages write ş or ș, ţ or ț, and quote marks as they come. One
# character for one character, so a place on the folded page is the same
# place on the page.
_LETTER_VARIANTS = str.maketrans({"ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț", "„": '"', "“": '"',
                                  "”": '"', "«": '"', "»": '"', "’": "'", "‘": "'", "‚": "'"})


def fold_letters(text: str) -> str:
    return text.translate(_LETTER_VARIANTS)


_rules_cache: dict[int, tuple] = {}


def country_rules(cfg) -> dict:
    """`cfg.sweep`, compiled once per loaded config. Empty for a country
    without the block, which then gets New Zealand's rules."""
    hit = _rules_cache.get(id(cfg))
    if hit and hit[0] is cfg:
        return hit[1]
    sweep = cfg.sweep or {}

    def words(key: str):
        found = sweep.get(key) or []
        return re.compile(rw._pattern(found, _to_ascii), re.IGNORECASE) if found else None

    def markers(found: list[str]) -> str:
        # A marker that starts with a letter starts a word: "lei", not "salei".
        return "|".join(("(?<![a-z])" if _to_ascii(m)[:1].isalpha() else "") + re.escape(_to_ascii(m))
                        for m in sorted(found, key=len, reverse=True))
    currencies = {}
    for code, spec in (sweep.get("currencies") or {}).items():
        alt = markers(spec["markers"])
        currencies[code.upper()] = {"max": spec["max"],
                                    "before": re.compile(rf"(?:{alt})\s*$"),
                                    "after": re.compile(rf"\s*(?:de\s+)?(?:{alt})")}
    rules = {
        "fold_letters": bool(sweep.get("fold_letters")),
        "number_format": sweep.get("number_format"),
        "currencies": currencies,
        "other_dollar": words("other_dollar_words"),
        "general_title": sweep.get("general_title"),
        "join_translations": bool(sweep.get("join_translations")),
        "not_a_grant": words("not_a_grant_words"),
        "grant_title": words("grant_title_words"),
        "loan_text": words("loan_text_words"),
        # Kinds of money the model may name that are not published. "mixed"
        # (part grant, part repayable) is the owner's call: mixed_kind.
        "reject_kinds": set(sweep.get("reject_kinds") or [])
        | ({"mixed"} if sweep.get("mixed_kind") == "reject" else set()),
        "rolling": words("rolling_words"),
        "not_covered_region": sweep.get("not_covered_region"),
        "not_covered": words("not_covered_words"),
        "covered_with": words("covered_with_words"),
        # Words that name the US dollar itself, beside a bare "$".
        "usd_words": re.compile(markers([m for m in (sweep.get("currencies") or {})
                                         .get("USD", {}).get("markers", []) if m != "$"])
                                or r"(?!x)x"),
        "other_dollar_tlds": set(sweep.get("other_dollar_tlds") or []),
    } if sweep else {}
    _rules_cache[id(cfg)] = (cfg, rules)
    return rules


def title_key(title: str, fold: bool = False) -> str:
    """A title as the words of its dedupe key. Cyrillic is transliterated,
    so two Russian titles do not both become "". With `fold` (a country's
    fold_letters) Latin letters lose their diacritics too, so "finanțare"
    and "finantare" are one word; without, text with no Cyrillic keys
    exactly as it always has (NZ's "Kōkiri" is still "k kiri")."""
    text = _to_ascii(title) if fold else transliterate_cyrillic(title).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


class _OnPage:
    """Finds excerpts and titles on one page. Without fold_letters this is
    the verify pass's check, unchanged. With it, ş/ș, ţ/ț and quote marks
    are one letter, and what is kept is spelled as the page spells it."""

    def __init__(self, text: str, fold: bool):
        self.norm = _norm(text)
        self.fold = fold
        if fold:
            tidy = re.sub(r"\s+", " ", text).strip()
            lower = tidy.casefold()
            # Only where casefolding keeps every letter in its place (it
            # does for Romanian and Russian) can the page's own letters be
            # read back; elsewhere the model's spelling is kept.
            self.page = tidy if len(lower) == len(tidy) else None
            self.key = fold_letters(lower)

    def _as_on_page(self, text: str, at: int) -> str:
        if self.page is None:
            return text
        page = self.page[at:at + len(text)]
        return "".join(p if m.casefold() != p.casefold() else m for m, p in zip(text, page))

    def verbatim(self, excerpt) -> str | None:
        if not self.fold:
            return _verbatim(excerpt, self.norm)
        if not isinstance(excerpt, str):
            return None
        tidy = re.sub(r"\s+", " ", excerpt).strip()
        at = self.key.find(fold_letters(tidy.casefold())) if len(tidy) >= MIN_EXCERPT_CHARS else -1
        return None if at < 0 else self._as_on_page(tidy, at)

    def title(self, title: str) -> str | None:
        """The title, spelled as on the page where the page has it whole,
        or None when it is not on the page."""
        if not self.fold:
            return title if title_on_page(title, self.norm) else None
        if not title_on_page(fold_letters(title), self.key):
            return None
        at = self.key.find(fold_letters(title.casefold()))
        return title if at < 0 else self._as_on_page(title, at)


# A figure as a Moldovan page writes it: 200.000, 200 000 or 20,000 (thousands),
# 1,5 or 1.5 (a decimal), then perhaps a word that multiplies it. Matched on
# text folded to plain Latin letters, where "тыс." is "tys." and a
# non-breaking space is a space.
_FIGURE_RO = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
    r"|\d{1,3}(?: \d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?!\d|[.,]\d)"
    r"(?:\s*(milioane|milion|million\w*|mil\.?|mln\.?|mii|mie|thousand|tysyach\w*|tys\.?|k)"
    r"(?![a-z]))?")
_MULTIPLIER = {"mii": 1e3, "mie": 1e3, "thousand": 1e3, "tys": 1e3, "tysyach": 1e3, "k": 1e3,
               "milioane": 1e6, "milion": 1e6, "million": 1e6, "mil": 1e6, "mln": 1e6}


def figures_ro(folded: str) -> list[tuple[float, int, int]]:
    """(value, start, end) of every figure in folded text: "1.500" is fifteen
    hundred, "1,5 mil." one and a half million."""
    out = []
    for m in _FIGURE_RO.finditer(folded):
        num = m.group(1).replace(" ", "")
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", num):
            num = num.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", num):
            num = num.replace(",", "")
        else:
            num = num.replace(",", ".")
        word = re.sub(r"\W", "", m.group(2) or "")
        mult = next((v for k, v in _MULTIPLIER.items() if word.startswith(k)), 1) if word else 1
        out.append((round(float(num) * mult, 2), m.start(), m.end()))
    return out


def _amount(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if value > 0 else None


def country_amounts(item: dict, amount_excerpt: str | None, rules: dict,
                    page_text: str = "", url: str = "") -> tuple:
    """(lo, hi, currency) for a country with `currencies`, or all None. The
    model names the currency; it is kept only when one of its markers stands
    beside a stated figure in the excerpt and no other currency's does. A
    figure the excerpt does not state, or an amount over the currency's cap,
    leaves all blank. So does a USD amount whose excerpt names another
    dollar, or that rests on a bare "$" (no "USD", "dolari" or the like in
    the excerpt) on a page that names another dollar anywhere or on a site
    under a top-level domain of a dollar that is not USD (.ca: the Canada
    Fund writes "$20k" and says CAD elsewhere). Nothing is converted or
    relabelled."""
    lo, hi = _amount(item.get("amount_min")), _amount(item.get("amount_max"))
    code = item.get("currency")
    code = code.strip().upper() if isinstance(code, str) else None
    spec = rules["currencies"].get(code)
    stated = [a for a in (lo, hi) if a is not None]
    if not (stated and spec and amount_excerpt) or (lo is not None and hi is not None and lo > hi):
        return None, None, None
    folded = _to_ascii(amount_excerpt)
    if rules["number_format"] == "ro":
        figures = figures_ro(folded)
        if not all(any(abs(v - a) < 0.5 for v, _, _ in figures) for a in stated):
            return None, None, None
    else:
        if not _figures_in(amount_excerpt, lo, hi):
            return None, None, None
        figures = [(float(re.sub(r"[^\d]", "", m.group())), m.start(), m.end())
                   for m in re.finditer(r"\d[\d,]*", folded)]
    beside = False
    for value, start, end in figures:
        if not any(abs(value - a) < 0.5 for a in stated):
            continue
        for other, s in rules["currencies"].items():
            if s["before"].search(folded[:start]) or s["after"].match(folded[end:]):
                if other != code:
                    return None, None, None
                beside = True
    if not beside or any(a > spec["max"] for a in stated):
        return None, None, None
    if code == "USD":
        other = rules["other_dollar"]
        if other and other.search(folded):
            return None, None, None
        if not rules["usd_words"].search(folded):
            tld = (urlsplit(url).hostname or "").rsplit(".", 1)[-1]
            if tld in rules["other_dollar_tlds"] or (other and other.search(_to_ascii(page_text))):
                return None, None, None
    return lo, hi, code


def not_covered(item: dict, title: str, eligibility_excerpt: str | None, rules: dict) -> bool:
    """The programme is limited to an area the country does not cover yet:
    the model gave the not-covered region and no other, its title names the
    area, or who can apply names it with no word that takes it in beside the
    rest ("din toată țara, inclusiv din regiunea transnistreană" is national).
    Such a row is not published, as national or at all. The region given
    beside others is dropped from the row, which stands."""
    if not rules.get("not_covered_region"):
        return False
    if [r for r in (item.get("regions") or []) if r] == [rules["not_covered_region"]]:
        return True
    names = rules["not_covered"]
    if not names:
        return False
    if names.search(_to_ascii(title)):
        return True
    who = _to_ascii(eligibility_excerpt or "")
    inclusive = rules["covered_with"]
    return bool(names.search(who)) and not (inclusive and inclusive.search(who))


def check_programme(item: dict, page: dict, funder: dict, cfg, today, now_iso: str) -> tuple[dict | None, str]:
    """A publishable row, or (None, reason). Code decides; the model only proposed."""
    if not isinstance(item, dict):
        return None, "not an object"
    rules = country_rules(cfg)
    title = " ".join(str(item.get("title") or "").split())
    on_page = _OnPage(page["text"], rules.get("fold_letters", False))
    if NOT_A_GRANT.search(title):
        return None, "not a grant"
    if rules.get("not_a_grant") and rules["not_a_grant"].search(_to_ascii(title)) and not (
            rules["grant_title"] and rules["grant_title"].search(_to_ascii(title))):
        # "Linia de credit" and "Garanții de credit" are not grants; a
        # subsidy is, and "Granturi și credite" names a grant.
        return None, "not a grant"
    kind = str(item.get("kind") or "").strip().lower()
    if kind in rules.get("reject_kinds", ()):
        # "FACEM Impact" names no loan; its page offers credit at 4-6%.
        return None, f"not a grant ({kind})"
    said = _to_ascii(f"{item.get('summary') or ''} {item.get('eligibility') or ''}")
    if rules.get("loan_text") and rules["loan_text"].search(said) and not (
            rules["grant_title"] and rules["grant_title"].search(said)):
        # The summary or who can apply speaks of credit, interest or
        # repayment and never of a grant or subsidy.
        return None, "not a grant (loan)"
    general = title.upper() == "GENERAL"
    if general:
        # The funder's one unnamed scheme. It has no title to find, so it
        # must be supported by who can apply.
        title = (rules["general_title"].format(funder=funder["name"]) if rules.get("general_title")
                 else f"{funder['name']} grants")
        if on_page.verbatim(item.get("eligibility_excerpt")) is None:
            return None, "nothing on the page supports the programme"
    else:
        found = on_page.title(title) if len(title) >= 4 else None
        if found is None:
            return None, "title not found on page"
        title = found
    state = item.get("deadline_state")
    if state not in ("dated", "rolling", "closed"):
        state = "unknown"
    excerpt = on_page.verbatim(item.get("deadline_excerpt"))
    if excerpt is None:
        if state == "closed":
            return None, "deadline excerpt not found on page"
        # No date the page supports. The programme is still kept if the page
        # supports the programme itself: who can apply, or what it pays.
        state = "unknown"
        excerpt = (on_page.verbatim(item.get("eligibility_excerpt"))
                   or on_page.verbatim(item.get("amount_excerpt")))
        if excerpt is None:
            return None, "nothing on the page supports the programme"

    if state == "rolling" and not ROLLING_WORDS.search(excerpt) and not (
            rules.get("rolling") and rules["rolling"].search(_to_ascii(excerpt))):
        # "Closes on the 10th of every month" is a repeating date, not an open
        # door. Without the words, no deadline is claimed; the sentence stays
        # on the row as its evidence.
        state = "unknown"

    deadline = None
    if state == "dated" or (state == "closed" and item.get("deadline")):
        deadline, why = _plausible_date(item.get("deadline"), {"first_seen": now_iso}, today)
        if deadline is not None and not _day_in(excerpt, deadline):
            deadline, why = None, "excerpt does not state the deadline date"
        if deadline is not None and _year_conflicts(excerpt, deadline):
            deadline, why = None, "excerpt states a different year from the deadline"
        if deadline is None and state == "dated":
            return None, why
    if state == "dated" and deadline < today:
        state = "closed"
    deadline_text = deadline.isoformat() if deadline else ("rolling" if state == "rolling" else "TBC")

    url = page["url"]
    provenance = ({} if state == "unknown" else
                  {"deadline": {"source_url": url, "verified_at": now_iso, "excerpt": excerpt}})
    amount_excerpt = on_page.verbatim(item.get("amount_excerpt"))
    if rules.get("currencies"):
        lo, hi, currency = country_amounts(item, amount_excerpt, rules, page["text"], page["url"])
        if lo is not None or hi is not None:
            provenance["amount"] = {"source_url": url, "verified_at": now_iso,
                                    "excerpt": amount_excerpt}
    else:
        currency = cfg.currency
        lo, hi = _number(item.get("amount_min")), _number(item.get("amount_max"))
        if lo is not None and hi is not None and lo > hi:
            lo = hi = None
        if amount_excerpt is None or not _figures_in(amount_excerpt, lo, hi):
            lo = hi = None   # an amount the page does not support is not published
        elif lo is not None or hi is not None:
            provenance["amount"] = {"source_url": url, "verified_at": now_iso, "excerpt": amount_excerpt}
    eligibility_excerpt = on_page.verbatim(item.get("eligibility_excerpt"))
    if eligibility_excerpt:
        provenance["eligibility"] = {"source_url": url, "verified_at": now_iso,
                                     "excerpt": eligibility_excerpt}
    if not_covered(item, title, eligibility_excerpt, rules):
        return None, "only for an area not yet covered"

    regions = [r for r in (item.get("regions") or []) if r in cfg.regions] or funder["regions"]
    tags = [t for t in (item.get("tags") or []) if t in cfg.tags][:4]
    slug = slugify_funder(funder["name"], cfg.slug)
    title_slug = title_key(title, rules.get("fold_letters", False)).replace(" ", "-")
    return {
        "country": cfg.slug, "title": title, "funder": funder["name"],
        "deadline": deadline_text,
        "deadline_state": {"dated": "dated", "rolling": "rolling-confirmed", "closed": "closed",
                           "unknown": "not-stated"}[state],
        "amount_min": lo, "amount_max": hi, "currency": currency,
        "region": regions, "tags": tags,
        "eligibility": (item.get("eligibility") or "").strip()[:600],
        "summary": (item.get("summary") or "").strip()[:900],
        "source_url": url, "evidence_ids": [], "notes": "",
        "first_seen": now_iso, "last_seen": now_iso,
        "dedupe_key": f"{slug}|{title_slug}|{deadline_text}".lower(),
        "verified_at": now_iso, "verified_by": f"register-sweep/{MODEL}",
        "source_excerpt": excerpt, "provenance": provenance,
        "funder_tier": funder["tier"], "funder_category": funder["category"],
    }, ""


def worst_case_usd(system: str, user: str) -> float:
    """What one call can cost at most: its text at WORST_CHARS_PER_TOKEN,
    and the longest answer it may give."""
    tokens_in = (len(system) + len(user)) / WORST_CHARS_PER_TOKEN
    return tokens_in / 1e6 * PRICE_IN + MAX_ANSWER_TOKENS / 1e6 * PRICE_OUT


def extract_page(page: dict, funder: dict, cfg, budget: Budget, ask, today, now_iso: str) -> dict:
    system = system_prompt(cfg, today)
    user = f"Funder: {funder['name']}\nPage address: {page['url']}\n\nPage text:\n{page['text']}"
    held = worst_case_usd(system, user)
    if budget.exhausted or not budget.reserve(held):
        return {"url": page["url"], "skipped": "budget reached", "rows": [], "rejected": []}
    try:
        raw, tin, tout = ask(system, user)
    except Exception as exc:  # noqa: BLE001
        budget.release(held)
        return {"url": page["url"], "error": f"{type(exc).__name__}: {exc}"[:200], "rows": [], "rejected": []}
    budget.add(tin, tout, reserved=held)
    answer = parse_json(raw)
    items = answer.get("programmes") if isinstance(answer, dict) else None
    if not isinstance(items, list):
        return {"url": page["url"], "error": "answer was not the expected JSON", "rows": [], "rejected": []}
    rows, rejected = [], []
    for item in items[:40]:
        row, reason = check_programme(item, page, funder, cfg, today, now_iso)
        if row:
            rows.append(row)
        else:
            rejected.append({"title": str((item or {}).get("title"))[:120] if isinstance(item, dict) else None,
                             "reason": reason})
    return {"url": page["url"], "rows": rows, "rejected": rejected}


# --- Run -------------------------------------------------------------------------

def dedupe_rows(rows: list[dict]) -> list[dict]:
    """One row per funder and title; a dated row beats a rolling one, then the
    row with more provenance."""
    best: dict[tuple, dict] = {}
    rank = {"dated": 4, "rolling-confirmed": 3, "closed": 2, "not-stated": 1}
    for row in rows:
        fold = country_rules(get_country_config(row.get("country"))).get("fold_letters", False)
        key = (row["funder"].lower(), title_key(row["title"], fold))
        score = (rank[row["deadline_state"]], len(row["provenance"]))
        if key not in best or score > best[key][0]:
            best[key] = (score, row)
    return join_translations([row for _, row in best.values()])


def _cyrillic(title: str) -> bool:
    letters = re.findall(r"[^\W\d_]", title)
    return bool(letters) and sum(1 for c in letters if "\u0400" <= c <= "\u04ff") > len(letters) / 2


def join_translations(rows: list[dict]) -> list[dict]:
    """For a country with `sweep.join_translations`: a funder that posts one
    call in Romanian and in Russian gives two rows no title key can join.
    When one funder has, for one closing date, exactly one Cyrillic-titled
    row and one Latin-titled row, with the same amounts and currency, they
    are one programme and the Latin-titled row is kept. Any other mix (two
    Russian calls and one Romanian on one date) is left alone."""
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        rules = country_rules(get_country_config(row.get("country")))
        if rules.get("join_translations") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["deadline"]):
            groups.setdefault((row["funder"].lower(), row["deadline"]), []).append(row)
    dropped = set()
    for group in groups.values():
        cyr = [r for r in group if _cyrillic(r["title"])]
        lat = [r for r in group if not _cyrillic(r["title"])]
        if len(cyr) == 1 and len(lat) == 1 and all(
                cyr[0][k] == lat[0][k] for k in ("amount_min", "amount_max", "currency")):
            dropped.add(id(cyr[0]))
    return [r for r in rows if id(r) not in dropped]


def _title_stems(title: str, stop: set[str]) -> set[str]:
    """A title's words, folded, hyphens closed up ("startup-urilor"), the
    country's stopwords left out, each cut to six letters so "integrarea"
    and "integrare", "lanțuri" and "lanțurile" are one word."""
    words = re.findall(r"[a-z0-9]+", _to_ascii(title).replace("-", ""))
    return {w[:6] for w in words if w not in stop}


def prefer_dedicated_pages(rows: list[dict], parents: dict, cfg) -> list[dict]:
    """With `sweep.prefer_dedicated_pages`: a row read from a page that
    links to another of the funder's pages is dropped when a row read from
    that linked page (or one further down) names the same programme: every
    word of the dedicated page's title is in the index row's. ODA's grants
    calendar lists "CREȘTEM IMM – tranziție digitală" and three more
    measures; the CREȘTEM IMM page describes the programme in full.

    The index row stands when it has what the dedicated row lacks: a
    closing date or an amount of its own. A sub-measure with nothing of its
    own ("Măsura 1", apply monthly) is the same call as the programme page,
    listed again; one with its own date or amount is a call a reader could
    miss if it were folded away."""
    sweep = cfg.sweep or {}
    if not sweep.get("prefer_dedicated_pages"):
        return rows
    stop = {_to_ascii(w) for w in sweep.get("title_stopwords") or []}

    def below(child: str | None, index: str) -> bool:
        seen = set()
        while child and child not in seen:
            seen.add(child)
            child = parents.get(child)
            if child == index:
                return True
        return False
    dropped = set()
    for row in rows:
        mine = _title_stems(row["title"], stop)
        for other in rows:
            if other is row or other["funder"] != row["funder"]:
                continue
            if not below(other["source_url"], row["source_url"]):
                continue
            theirs = _title_stems(other["title"], stop)
            own_date = row["deadline_state"] == "dated" and other["deadline_state"] != "dated"
            own_amount = (row["amount_min"] or row["amount_max"]) and not (
                other["amount_min"] or other["amount_max"])
            if theirs and theirs <= mine and not (own_date or own_amount):
                dropped.add(id(row))
                break
    return [r for r in rows if id(r) not in dropped]


def select_funders(country: str, tier: int | None = None, limit: int | None = None,
                   only: list[str] | None = None) -> list[dict]:
    funders = load_register(country)
    if tier:
        funders = [f for f in funders if f["tier"] == tier]
    if only:
        wanted = {o.lower() for o in only}
        funders = [f for f in funders if f["name"].lower() in wanted]
    if limit:
        funders = funders[:limit]
    return funders


def crawl_all(country: str, cfg, funders: list[dict], fetch, now=None) -> list[dict]:
    """Every funder crawled, by the country's words when its sweep block asks."""
    sweep = cfg.sweep or {}
    now = now or datetime.now(timezone.utc)
    words = rw.register_settings(country) if sweep.get("country_words") else None
    strict = bool(words and sweep.get("strict_links"))
    if words and sweep.get("skip_link_words"):
        # The sweep's own additions ("proiecte finanțate": past recipients),
        # beside the register's skip words, checked the same way.
        extra = rw._pattern(sweep["skip_link_words"], words["fold"])
        own = words["own_skip"]
        words = {**words, "own_skip": re.compile(
            f"{own.pattern}|{extra}" if own else extra, re.IGNORECASE)}
    if words and sweep.get("language_paths"):
        words = {**words, "language_paths": {p.lower() for p in sweep["language_paths"]}}
    if words and sweep.get("link_words"):
        # Words that name a programme on these sites without a grant word
        # ("Voucher cultural", "Programe de postdoctorat"): they choose a
        # link to follow, not a page to read.
        words = {**words, "link_words": re.compile(
            words["grant_words"].pattern + "|" + rw._pattern(sweep["link_words"], words["fold"]),
            re.IGNORECASE)}
    if words:
        words = {**words, "stale_years": sweep.get("stale_years"),
                 "rank_strong_links": bool(sweep.get("rank_strong_links")),
                 "this_year": now.astimezone(ZoneInfo(cfg.timezone)).year}
    with ThreadPoolExecutor(max_workers=CRAWL_CONCURRENCY) as pool:
        return list(pool.map(lambda f: crawl_funder(f, fetch, words, strict), funders))


def run(country: str, *, tier: int | None = None, limit: int | None = None, budget_usd: float = 5.0,
        only: list[str] | None = None, publish: bool = False, force: bool = False,
        forced_by: str | None = None, fetch=fetch_page, ask=anthropic_ask, now=None) -> dict:
    cfg = get_country_config(country)
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    today = now.astimezone(ZoneInfo(cfg.timezone)).date()
    month = today.strftime("%Y-%m")
    run_ts = now.strftime("%Y%m%dT%H%M%S")

    funders = select_funders(country, tier, limit, only)
    budget = Budget(budget_usd)
    print(f"[register_sweep] {country} {month}: {len(funders)} funders, budget ${budget_usd:.2f}")

    started = time.monotonic()
    crawled = crawl_all(country, cfg, funders, fetch, now)
    crawl_s = time.monotonic() - started
    jobs = [(c["funder"], p) for c in crawled for p in c["pages"]]
    print(f"[register_sweep] crawled {sum(len(c['pages']) for c in crawled)} pages "
          f"from {sum(1 for c in crawled if c['pages'])} funders in {crawl_s:.0f}s")

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=EXTRACT_CONCURRENCY) as pool:
        results = list(pool.map(
            lambda job: {**extract_page(job[1], job[0], cfg, budget, ask, today, now_iso),
                         "funder": job[0]["name"]}, jobs))
    extract_s = time.monotonic() - started

    rows = dedupe_rows([row for r in results for row in r["rows"]])
    rows = prefer_dedicated_pages(rows, {p["url"]: p.get("from") for c in crawled
                                         for p in c["pages"]}, cfg)
    for n, row in enumerate(sorted(rows, key=lambda r: (r["funder"], r["title"])), 1):
        row["id"] = f"OPP-{country.upper()}-{month}-{n:04d}"

    present = {r["funder"] for r in rows}
    by_tier = {}
    for t in sorted({f["tier"] for f in funders}):
        names = [f["name"] for f in funders if f["tier"] == t]
        by_tier[str(t)] = {
            "funders": len(names),
            "reached": sum(1 for c in crawled if c["funder"]["tier"] == t and c["pages"]),
            "present": sum(1 for n in names if n in present),
            "missing": sorted(n for n in names if n not in present),
        }
    reasons: dict[str, int] = {}
    for r in results:
        for rej in r["rejected"]:
            reasons[rej["reason"]] = reasons.get(rej["reason"], 0) + 1
    states: dict[str, int] = {}
    for row in rows:
        states[row["deadline_state"]] = states.get(row["deadline_state"], 0) + 1

    report = {
        "country": country, "month": month, "run_ts": run_ts, "model": MODEL,
        "funders": len(funders), "pages_read": len(jobs),
        "pages_skipped_for_budget": sum(1 for r in results if r.get("skipped")),
        "page_errors": [{"funder": r["funder"], "url": r["url"], "error": r["error"]}
                        for r in results if r.get("error")],
        "fetch_errors": [{"funder": c["funder"]["name"], **e} for c in crawled for e in c["errors"]],
        "funders_with_no_page": sorted(c["funder"]["name"] for c in crawled if not c["pages"]),
        "rows": len(rows), "rows_by_deadline_state": states,
        "rows_with_amount": sum(1 for r in rows if r["amount_min"] or r["amount_max"]),
        "proposed_and_rejected": sum(reasons.values()), "rejected_reasons": reasons,
        "coverage_by_tier": by_tier, "budget": budget.summary(),
        "pages_fetched_through_tavily": SEARCH_CREDITS["extract"],
        "seconds": {"crawl": round(crawl_s), "extract": round(extract_s)},
        "cost_per_row_usd": round(budget.spent / len(rows), 4) if rows else None,
    }

    run_dir = tenant.platform_run_dir(country, month, run_ts)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "verified.json"), "w", encoding="utf-8") as f:
        json.dump({"opportunities": rows}, f, indent=2, ensure_ascii=False)
    with open(os.path.join(run_dir, "register_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    with open(os.path.join(run_dir, "rejected.json"), "w", encoding="utf-8") as f:
        json.dump([{"funder": r["funder"], "url": r["url"], "rejected": r["rejected"]}
                   for r in results if r["rejected"]], f, indent=2, ensure_ascii=False)
    report["run_dir"] = run_dir

    if publish:
        from api.published import PublishBlocked, publish_pool
        try:
            out = publish_pool(country, month, rows, now=now, force=force, forced_by=forced_by,
                               run_ts=run_ts)
            report["publish"] = {"published": True, "counts": out["counts"]}
        except PublishBlocked as exc:
            report["publish"] = {"published": False, "missing": exc.missing}
    return report


def crawl_only(country: str, *, tier: int | None = None, limit: int | None = None,
               only: list[str] | None = None, fetch=None, now=None) -> dict:
    """Stage 2 alone: the pages each funder's crawl chose, how each was
    reached and whether it passed the page filter. No model is called, and
    Tavily is not used even with a key: a page that refuses a plain fetch is
    listed as refused. Written to `crawl-<ts>/crawl.json` beside the runs."""
    cfg = get_country_config(country)
    now = now or datetime.now(timezone.utc)
    month = now.astimezone(ZoneInfo(cfg.timezone)).date().strftime("%Y-%m")
    run_ts = now.strftime("%Y%m%dT%H%M%S")
    fetch = fetch or (lambda url: fetch_page(url, use_tavily=False))
    funders = select_funders(country, tier, limit, only)
    print(f"[register_sweep] {country} {month}: crawl only, {len(funders)} funders, "
          f"no model and no Tavily")
    crawled = crawl_all(country, cfg, funders, fetch, now)
    # A rough guide to what the paid run would cost: about 2.5 characters a
    # token (Romanian and Russian cost more than English), the prompt, and
    # a generous answer for each page.
    chars = sum(len(p["text"]) for c in crawled for p in c["pages"])
    pages = sum(len(c["pages"]) for c in crawled)
    estimate = ((chars / 2.5 + pages * ESTIMATE_PROMPT_TOKENS) / 1e6 * PRICE_IN
                + pages * ESTIMATE_ANSWER_TOKENS / 1e6 * PRICE_OUT)
    report = {
        "country": country, "month": month, "run_ts": run_ts, "crawl_only": True,
        "estimated_model_usd": round(estimate, 3),
        "funders": [{"name": c["funder"]["name"], "url": c["funder"]["url"],
                     "pages_to_read": [p["url"] for p in c["pages"]],
                     "visits": c["visits"]} for c in crawled],
        "pages_to_read": sum(len(c["pages"]) for c in crawled),
        "funders_with_no_page": sorted(c["funder"]["name"] for c in crawled if not c["pages"]),
    }
    out = os.path.join(tenant.platform_cycles_dir(country, month), f"crawl-{run_ts}")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "crawl.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    report["crawl_dir"] = out
    return report


def merge_runs(country: str, first: str, second: str) -> dict:
    """Write a third run directory holding both runs' rows, deduped and
    renumbered, with coverage counted against the whole register."""
    rows = []
    for d in (first, second):
        with open(os.path.join(d, "verified.json"), encoding="utf-8") as f:
            rows += json.load(f)["opportunities"]
    rows = dedupe_rows(rows)
    with open(os.path.join(first, "register_report.json"), encoding="utf-8") as f:
        base = json.load(f)
    with open(os.path.join(second, "register_report.json"), encoding="utf-8") as f:
        extra = json.load(f)
    month = base["month"]
    for n, row in enumerate(sorted(rows, key=lambda r: (r["funder"], r["title"])), 1):
        row["id"] = f"OPP-{country.upper()}-{month}-{n:04d}"
    present = {r["funder"] for r in rows}
    funders = load_register(country)
    tiers = {}
    for t in sorted({f["tier"] for f in funders}):
        names = [f["name"] for f in funders if f["tier"] == t]
        tiers[str(t)] = {"funders": len(names), "present": sum(1 for n in names if n in present),
                         "missing": sorted(n for n in names if n not in present)}
    states: dict[str, int] = {}
    for row in rows:
        states[row["deadline_state"]] = states.get(row["deadline_state"], 0) + 1
    run_ts = extra["run_ts"] + "M"
    out = tenant.platform_run_dir(country, month, run_ts)
    os.makedirs(out, exist_ok=True)
    report = {"country": country, "month": month, "run_ts": run_ts, "model": MODEL,
              "merged_from": [first, second], "rows": len(rows), "rows_by_deadline_state": states,
              "rows_with_amount": sum(1 for r in rows if r["amount_min"] or r["amount_max"]),
              "coverage_by_tier": tiers,
              "spent_usd": round(base["budget"]["spent_usd"] + extra["budget"]["spent_usd"], 4)}
    with open(os.path.join(out, "verified.json"), "w", encoding="utf-8") as f:
        json.dump({"opportunities": rows}, f, indent=2, ensure_ascii=False)
    with open(os.path.join(out, "register_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return {**report, "run_dir": out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Register sweep: visit every funder on the register.")
    ap.add_argument("country")
    ap.add_argument("--tier", type=int, choices=(1, 2))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", action="append", help="a funder name; may be repeated")
    ap.add_argument("--missing-from", help="a run directory: visit only the funders it has no row for")
    ap.add_argument("--merge-into", help="a run directory: add this run's rows to its rows")
    ap.add_argument("--budget", type=float, default=5.0, help="hard cap in USD (default 5)")
    ap.add_argument("--publish", action="store_true", help="publish the verified rows")
    ap.add_argument("--force", action="store_true", help="publish even if a tier 1 funder is missing")
    ap.add_argument("--by", help="who forced the publish")
    ap.add_argument("--crawl-only", action="store_true",
                    help="list the pages each funder's crawl chooses; no model, no Tavily, free")
    args = ap.parse_args(argv)
    only = args.only
    if args.missing_from:
        with open(os.path.join(args.missing_from, "verified.json"), encoding="utf-8") as f:
            have = {r["funder"] for r in json.load(f)["opportunities"]}
        only = [f["name"] for f in load_register(args.country) if f["name"] not in have]
    if args.crawl_only:
        if args.publish or args.merge_into:
            ap.error("--crawl-only reads no programmes, so it cannot --publish or --merge-into")
        report = crawl_only(args.country, tier=args.tier, limit=args.limit, only=only)
        for f in report["funders"]:
            print(f"\n{f['name']}  ({f['url']})")
            for v in f["visits"]:
                mark = ("READ " if v.get("kept") else "skip ") if "error" not in v else "ERROR"
                why = v.get("error") or v.get("why_not") or ""
                print(f"  {mark} {v['url']}\n        via {v['reached_by']}" + (f"; {why}" if why else ""))
        print(f"\n{report['pages_to_read']} pages would be read, for about "
              f"${report['estimated_model_usd']:.2f} of model time; no page for: "
              f"{', '.join(report['funders_with_no_page']) or 'none'}. Written to {report['crawl_dir']}")
        return 0
    report = run(args.country, tier=args.tier, limit=args.limit, only=only,
                 budget_usd=args.budget, publish=args.publish, force=args.force, forced_by=args.by)
    if args.merge_into:
        report["merged"] = merge_runs(args.country, args.merge_into, report["run_dir"])
    brief = {k: v for k, v in report.items() if k not in ("page_errors", "fetch_errors")}
    brief["page_errors"], brief["fetch_errors"] = len(report["page_errors"]), len(report["fetch_errors"])
    print(json.dumps(brief, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
