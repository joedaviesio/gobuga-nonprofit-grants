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

CLI:
    python -m orchestrator.register_sweep nz --tier 1 --limit 20 --budget 2
    python -m orchestrator.register_sweep nz --budget 40 --publish
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
from api.funders import slugify_funder
from orchestrator.tools import MAX_FETCH_REDIRECTS, UnsafeUrlError, assert_public_url
from orchestrator.verify_pass import _day_in, _plausible_date, _verbatim, _year_conflicts, _norm

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

GRANT_WORDS = re.compile(
    r"grant|fund|funding|apply|application|scholarship|sponsorship|putea|pūtea|tahua|contestable",
    re.IGNORECASE)
SKIP_LINK = re.compile(
    r"login|sign-?in|news|media-release|annual-report|careers|vacanc|privacy|terms|contact|"
    r"recipient|approved|declined|successful|past-grant|grants-made|who-we.?ve-funded|"
    r"facebook|twitter|linkedin|instagram|youtube|mailto:|tel:|\.(jpg|jpeg|png|gif|zip|docx?|xlsx?)$",
    re.IGNORECASE)


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


def fetch_page(url: str) -> dict:
    """{"url", "text", "links"} or {"url", "error"}. Follows redirects by hand
    so every hop is checked against the public-address guard."""
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
            rendered = fetch_blocked(current, "page has no text without scripts")
            if not rendered.get("error"):
                rendered["links"] = rendered["links"] or links
                return rendered
        return {"url": current, "text": text[:MAX_PAGE_CHARS], "links": links}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403, 406, 429, 503):
            return fetch_blocked(url, f"HTTP {exc.response.status_code}")
        return {"url": url, "error": f"HTTP {exc.response.status_code}"}
    except Exception as exc:  # noqa: BLE001 — one bad site must not stop the crawl
        return {"url": url, "error": f"{type(exc).__name__}: {exc}"[:200]}


SEARCH_CREDITS = {"extract": 0}
MARKDOWN_LINK = re.compile(r"\[([^\]]{1,200})\]\((https?://[^)\s]+)\)")


def fetch_blocked(url: str, why: str) -> dict:
    """A page that refuses a plain fetch, read through Tavily's extract
    (one credit per five pages). Without a key, the refusal stands."""
    if not os.getenv("TAVILY_API_KEY"):
        return {"url": url, "error": why}
    try:
        from tavily import TavilyClient
        res = TavilyClient(api_key=os.environ["TAVILY_API_KEY"]).extract(urls=[url])
        raw = (res.get("results") or [{}])[0].get("raw_content") or ""
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "error": f"{why}; extract failed: {type(exc).__name__}"}
    SEARCH_CREDITS["extract"] += 1
    if not raw.strip():
        return {"url": url, "error": f"{why}; extract returned nothing"}
    links = [(urldefrag(urljoin(url, h))[0], t) for t, h in MARKDOWN_LINK.findall(raw)]
    text = re.sub(r"[ \t]+", " ", MARKDOWN_LINK.sub(r"\1", raw))
    return {"url": url, "text": text[:MAX_PAGE_CHARS], "links": links, "via": "tavily"}


def _site(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def pick_links(page: dict, home: str, seen: set[str], words=GRANT_WORDS, skip=SKIP_LINK) -> list[str]:
    """Same-site links whose text or address looks like a grant programme,
    best first. Pure: decided by rule, not by a model. `words` and `skip`
    let a country whose sites are not in English bring its own."""
    site = _site(urlsplit(home).hostname)
    scored = []
    for href, text in page.get("links", []):
        parts = urlsplit(href)
        if parts.scheme not in ("http", "https") or _site(parts.hostname) != site:
            continue
        key = href.rstrip("/")
        if key in seen or skip.search(href):
            continue
        score = 2 * len(words.findall(text)) + len(words.findall(parts.path))
        if score:
            scored.append((-score, len(href), href))
    out, picked = [], set()
    for _, _, href in sorted(scored):
        if href.rstrip("/") not in picked:
            picked.add(href.rstrip("/"))
            out.append(href)
    return out


def crawl_funder(funder: dict, fetch=fetch_page) -> dict:
    """Pages worth reading for one funder, breadth first from its funding page."""
    seen, pages, errors = {funder["url"].rstrip("/")}, [], []
    frontier = [(funder["url"], 0)]
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
            frontier.append((home, 0))
        url, depth = frontier.pop(0)
        page = fetch(url)
        if page.get("error"):
            errors.append({"url": url, "error": page["error"]})
            continue
        text = page["text"]
        if len(text) >= MIN_PAGE_CHARS and GRANT_WORDS.search(text):
            pages.append({"url": page["url"], "text": text})
        if depth < MAX_DEPTH:
            room = MAX_PAGES_PER_FUNDER * 2 - len(seen)
            for href in pick_links(page, funder["url"], seen)[:max(room, 0)]:
                seen.add(href.rstrip("/"))
                frontier.append((href, depth + 1))
    return {"funder": funder, "pages": pages, "errors": errors}


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


class Budget:
    """Counts spend; `spent >= cap` stops new work."""

    def __init__(self, cap_usd: float):
        self.cap, self.spent = cap_usd, 0.0
        self.tokens_in = self.tokens_out = self.calls = 0
        self._lock = threading.Lock()

    def add(self, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            self.tokens_in += tokens_in
            self.tokens_out += tokens_out
            self.calls += 1
            self.spent += tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT

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
    resp = _client.messages.create(model=MODEL, max_tokens=12000, system=system,
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


def check_programme(item: dict, page: dict, funder: dict, cfg, today, now_iso: str) -> tuple[dict | None, str]:
    """A publishable row, or (None, reason). Code decides; the model only proposed."""
    if not isinstance(item, dict):
        return None, "not an object"
    title = " ".join(str(item.get("title") or "").split())
    page_norm = _norm(page["text"])
    if NOT_A_GRANT.search(title):
        return None, "not a grant"
    general = title.upper() == "GENERAL"
    if general:
        # The funder's one unnamed scheme. It has no title to find, so it
        # must be supported by who can apply.
        title = f"{funder['name']} grants"
        if _verbatim(item.get("eligibility_excerpt"), page_norm) is None:
            return None, "nothing on the page supports the programme"
    elif len(title) < 4 or not title_on_page(title, page_norm):
        return None, "title not found on page"
    state = item.get("deadline_state")
    if state not in ("dated", "rolling", "closed"):
        state = "unknown"
    excerpt = _verbatim(item.get("deadline_excerpt"), page_norm)
    if excerpt is None:
        if state == "closed":
            return None, "deadline excerpt not found on page"
        # No date the page supports. The programme is still kept if the page
        # supports the programme itself: who can apply, or what it pays.
        state = "unknown"
        excerpt = (_verbatim(item.get("eligibility_excerpt"), page_norm)
                   or _verbatim(item.get("amount_excerpt"), page_norm))
        if excerpt is None:
            return None, "nothing on the page supports the programme"

    if state == "rolling" and not ROLLING_WORDS.search(excerpt):
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
    lo, hi = _number(item.get("amount_min")), _number(item.get("amount_max"))
    amount_excerpt = _verbatim(item.get("amount_excerpt"), page_norm)
    if lo is not None and hi is not None and lo > hi:
        lo = hi = None
    if amount_excerpt is None or not _figures_in(amount_excerpt, lo, hi):
        lo = hi = None   # an amount the page does not support is not published
    elif lo is not None or hi is not None:
        provenance["amount"] = {"source_url": url, "verified_at": now_iso, "excerpt": amount_excerpt}
    eligibility_excerpt = _verbatim(item.get("eligibility_excerpt"), page_norm)
    if eligibility_excerpt:
        provenance["eligibility"] = {"source_url": url, "verified_at": now_iso,
                                     "excerpt": eligibility_excerpt}

    regions = [r for r in (item.get("regions") or []) if r in cfg.regions] or funder["regions"]
    tags = [t for t in (item.get("tags") or []) if t in cfg.tags][:4]
    slug = slugify_funder(funder["name"], cfg.slug)
    title_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return {
        "country": cfg.slug, "title": title, "funder": funder["name"],
        "deadline": deadline_text,
        "deadline_state": {"dated": "dated", "rolling": "rolling-confirmed", "closed": "closed",
                           "unknown": "not-stated"}[state],
        "amount_min": lo, "amount_max": hi, "currency": cfg.currency,
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


def extract_page(page: dict, funder: dict, cfg, budget: Budget, ask, today, now_iso: str) -> dict:
    if budget.exhausted:
        return {"url": page["url"], "skipped": "budget reached", "rows": [], "rejected": []}
    system = SYSTEM_PROMPT.format(today=today.isoformat(), regions=", ".join(cfg.regions),
                                  tags=", ".join(cfg.tags))
    user = f"Funder: {funder['name']}\nPage address: {page['url']}\n\nPage text:\n{page['text']}"
    try:
        raw, tin, tout = ask(system, user)
    except Exception as exc:  # noqa: BLE001
        return {"url": page["url"], "error": f"{type(exc).__name__}: {exc}"[:200], "rows": [], "rejected": []}
    budget.add(tin, tout)
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
        key = (row["funder"].lower(), re.sub(r"[^a-z0-9]+", " ", row["title"].lower()).strip())
        score = (rank[row["deadline_state"]], len(row["provenance"]))
        if key not in best or score > best[key][0]:
            best[key] = (score, row)
    return [row for _, row in best.values()]


def run(country: str, *, tier: int | None = None, limit: int | None = None, budget_usd: float = 5.0,
        only: list[str] | None = None, publish: bool = False, force: bool = False,
        forced_by: str | None = None, fetch=fetch_page, ask=anthropic_ask, now=None) -> dict:
    cfg = get_country_config(country)
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    today = now.astimezone(ZoneInfo(cfg.timezone)).date()
    month = today.strftime("%Y-%m")
    run_ts = now.strftime("%Y%m%dT%H%M%S")

    funders = load_register(country)
    if tier:
        funders = [f for f in funders if f["tier"] == tier]
    if only:
        wanted = {o.lower() for o in only}
        funders = [f for f in funders if f["name"].lower() in wanted]
    if limit:
        funders = funders[:limit]
    budget = Budget(budget_usd)
    print(f"[register_sweep] {country} {month}: {len(funders)} funders, budget ${budget_usd:.2f}")

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=CRAWL_CONCURRENCY) as pool:
        crawled = list(pool.map(lambda f: crawl_funder(f, fetch), funders))
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
    args = ap.parse_args(argv)
    only = args.only
    if args.missing_from:
        with open(os.path.join(args.missing_from, "verified.json"), encoding="utf-8") as f:
            have = {r["funder"] for r in json.load(f)["opportunities"]}
        only = [f["name"] for f in load_register(args.country) if f["name"] not in have]
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
