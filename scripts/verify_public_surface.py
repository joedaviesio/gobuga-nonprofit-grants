#!/usr/bin/env python3
"""Check the public web surface end to end, the way a crawler sees it.

Given a running backend and frontend (for instance over the development
fixture from `scripts/dev_seed_public_fixture.py`), fetches every public
route with plain HTTP, no JavaScript, and asserts:

- status codes, including real 404s;
- that each grant's facts are in the HTML body as text;
- that no page contains the fixture's hostile title unescaped;
- that each JSON twin equals the backend's response for the same query;
- that `Accept: application/json` returns JSON and a browser's Accept, or
  curl's `*/*`, returns HTML;
- that each JSON-LD block parses and agrees with the record and the page;
- that a stale grant is noindex and absent from the sitemap and every list;
- that a closed grant is present and marked closed;
- that /out/{id} redirects to the stored funder URL;
- that robots.txt, sitemap.xml and llms.txt are well formed (and that the
  curl lines in llms.txt work);
- that a page is the same whoever asks (browser or curl user agent);
- with --next-dir, that made-up grant IDs and funder slugs are 404s that
  leave nothing in Next's page cache;
- with --data-dir, that page hits reach the backend's metrics log, with the
  path and no IP address, and that JSON twins are not double counted.

Standard library only. Reads nothing but HTTP responses (and, with
--data-dir, the throwaway metrics log). Exit status 1 if any check fails.

Usage:
    python scripts/verify_public_surface.py --frontend http://localhost:3057 \\
        --backend http://localhost:8157 [--data-dir /tmp/fixture] [--next-dir frontend/.next] [--country nz]
"""

import argparse
import glob
import json
import os
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOSTILE_RAW = "</script><script>alert(1)</script>"
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
CURL_UA = "curl/8.6.0"
NAMED_CRAWLERS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot",
                  "Claude-User", "PerplexityBot", "Perplexity-User", "Google-Extended", "CCBot",
                  "Googlebot", "Bingbot"]

# A few words of page furniture per language (frontend/lib/public-i18n.ts).
WORDS = {
    "en": {"stale": "could not be re-verified", "closed": "Closed", "region": "Region"},
    "ro": {"stale": "nu a putut fi verificat", "closed": "Închis", "region": "Regiune"},
    "ru": {"stale": "не удалось перепроверить", "closed": "Закрыт", "region": "Регион"},
}

RESULTS = {"pass": 0, "fail": 0}


def check(ok: bool, what: str, detail: str = "") -> bool:
    RESULTS["pass" if ok else "fail"] += 1
    print(f"{'PASS' if ok else 'FAIL'}  {what}" + (f"  [{detail}]" if detail and not ok else ""))
    return ok


# --- HTTP ---------------------------------------------------------------------

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = build_opener(_NoRedirect)


class Response:
    def __init__(self, status: int, headers, body: bytes):
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip()


def get(url: str, accept: str = "*/*", ua: str = CURL_UA) -> Response:
    req = Request(url, headers={"Accept": accept, "User-Agent": ua})
    for attempt in range(3):
        try:
            with _opener.open(req, timeout=30) as res:
                return Response(res.status, res.headers, res.read())
        except HTTPError as err:
            return Response(err.code, err.headers, err.read())
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1)
    raise RuntimeError("unreachable")


# --- HTML ---------------------------------------------------------------------

class Page(HTMLParser):
    """Visible text, <time datetime>, meta/link tags, JSON-LD blocks, <html lang>."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.times: list[str] = []
        self.metas: list[dict] = []
        self.links: list[dict] = []
        self.jsonld: list[str] = []
        self.lang = None
        self.h1: list[str] = []
        self._skip = 0
        self._ld = None
        self._in_h1 = False
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag == "h1":
            self._in_h1 = True
            self.h1.append("")
        elif tag == "script":
            self._skip += 1
            if a.get("type") == "application/ld+json":
                self._ld = ""
        elif tag in ("style", "template"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self._skip -= 1
            if self._ld is not None:
                self.jsonld.append(self._ld)
                self._ld = None
        elif tag in ("style", "template"):
            self._skip -= 1
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, data):
        if self._ld is not None:
            self._ld += data
            return
        if self._skip:
            return
        self.text_parts.append(data)
        if self._in_h1:
            self.h1[-1] += data

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.text_parts))

    def meta(self, name: str) -> str | None:
        for m in self.metas:
            if m.get("name") == name:
                return m.get("content")
        return None

    def link(self, rel: str, type_: str | None = None) -> str | None:
        for link in self.links:
            if link.get("rel") == rel and (type_ is None or link.get("type") == type_):
                return link.get("href")
        return None


def digits(text: str) -> str:
    """Text with thousands separators removed, so 20000 matches "$20,000" or "20 000 MDL"."""
    return re.sub(r"(?<=\d)[,.\u00a0\u202f ](?=\d{3}\b)", "", text)


# --- Checks -------------------------------------------------------------------

def all_records(backend: str) -> list[dict]:
    out, offset = [], 0
    while True:
        body = get(f"{backend}/api/v1/opportunities?status=all&limit=100&offset={offset}").json()
        out.extend(body["data"])
        offset += 100
        if offset >= body["total"]:
            return out


def local_day(iso: str, tz: str) -> str:
    """The YYYY-MM-DD an ISO timestamp fell on in the country's timezone."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(tz)).date().isoformat()


def check_grant_page(fe: str, rec: dict, index: dict) -> None:
    rid = rec["id"]
    res = get(f"{fe}/grants/{rid}")
    if not check(res.status == 200, f"grant {rid} ({rec['status']}) is 200", str(res.status)):
        return
    page = Page(res.text)
    text = page.text
    facts = {
        "title": rec["title"],
        "funder": rec["funder"]["name"],
        "id": rid,
        "eligibility": rec["eligibility"] or None,
        "summary": " ".join((rec["summary"] or "").split()) or None,
    }
    missing = [k for k, v in facts.items() if v and " ".join(v.split()) not in text]
    check(not missing, f"grant {rid}: title, funder, ID, eligibility, summary are text in the HTML",
          ", ".join(missing))
    amounts = [v for v in (rec["amount"]["min"], rec["amount"]["max"]) if v]
    missing_amounts = [a for a in amounts if str(int(a)) not in digits(text)]
    check(not missing_amounts, f"grant {rid}: every stated amount is in the text", str(missing_amounts))
    if rec["deadline"]["state"] == "dated" and rec["deadline"]["date"]:
        check(rec["deadline"]["date"] in page.times, f"grant {rid}: deadline date is a <time datetime>")
    if rec["deadline"]["verified_at"]:
        day = local_day(rec["deadline"]["verified_at"], index["timezone"])
        check(day in page.times, f"grant {rid}: deadline verification date is a <time datetime>", day)
        verified_pos = res.text.lower().find(f'datetime="{day}"')  # React writes dateTime=
        # Prominent: before the facts list, next to the deadline.
        check(0 < verified_pos < res.text.find("<dl"), f"grant {rid}: verification date is shown near the top")
    for region in rec["region"]:
        label = " ".join(w if i and w in ("of", "and", "de", "si") else w.capitalize()
                         for i, w in enumerate(region.split("-")))
        check(label in text, f"grant {rid}: region {region} is shown", label)
    outs = re.findall(rf'<a [^>]*href="/out/{re.escape(rid)}"[^>]*>', res.text)
    check(len(outs) == 1 and 'rel="nofollow"' in outs[0],
          f"grant {rid}: one click-out link through /out with rel=nofollow", str(outs))
    canonical = page.link("canonical")
    check(canonical == rec["canonical_url"], f"grant {rid}: canonical is the bare grant URL", str(canonical))
    check(page.link("alternate", "application/json") == rec["json_url"], f"grant {rid}: JSON alternate link")
    robots = page.meta("robots") or ""
    if rec["status"] == "stale":
        check("noindex" in robots, f"grant {rid}: stale grant is noindex", robots)
        check(WORDS[index["lang"]]["stale"] in text, f"grant {rid}: stale grant says it could not be re-verified")
        check(f"/out/{rid}" in res.text, f"grant {rid}: stale grant still links to the funder")
    else:
        check("noindex" not in robots, f"grant {rid}: indexable", robots)
    if rec["status"] == "closed":
        check(WORDS[index["lang"]]["closed"] in text, f"grant {rid}: marked closed")
        full = get(f"{index['backend']}/api/v1/opportunities/{rid}").json()
        related = full.get("related") or []
        check(all(r["id"] in res.text for r in related), f"grant {rid}: lists its {len(related)} related live grants")
    # JSON-LD agrees with the record and states nothing the page does not.
    lds = [json.loads(block) for block in page.jsonld]
    grants = [ld for ld in lds if ld.get("@type") == "MonetaryGrant"]
    if check(len(grants) == 1, f"grant {rid}: one MonetaryGrant JSON-LD block that parses"):
        ld = grants[0]
        ok = (ld.get("identifier") == rid and ld.get("name") == rec["title"]
              and ld.get("url") == rec["canonical_url"]
              and (ld.get("funder") or {}).get("name") == rec["funder"]["name"])
        amount = ld.get("amount") or {}
        if rec["amount"]["max"]:
            ok = ok and amount.get("maxValue") == rec["amount"]["max"]
        if rec["deadline"]["state"] == "dated":
            ok = ok and amount.get("validThrough") == rec["deadline"]["date"]
        check(ok, f"grant {rid}: JSON-LD agrees with the record", json.dumps(ld)[:200])
        check(ld.get("name", "") in page.h1[0] if page.h1 else False, f"grant {rid}: JSON-LD name is the visible <h1>")
    check(len(page.h1) == 1, f"grant {rid}: exactly one <h1>", str(len(page.h1)))


def check_twins(fe: str, be: str, sample: dict) -> None:
    gid, slug, month = sample["grant"], sample["funder"], sample["month"]
    fit_q = sample["fit_query"]
    pairs = [
        (f"/grants/{gid}.json", f"/api/v1/opportunities/{gid}"),
        ("/grants.json?tag=community&status=all", "/api/v1/opportunities?tag=community&status=all"),
        ("/grants.json", "/api/v1/opportunities"),
        (f"/fit.json?{fit_q}", f"/api/v1/fit?{fit_q}"),
        ("/funders.json", "/api/v1/funders"),
        (f"/funders/{slug}.json", f"/api/v1/funders/{slug}"),
        ("/changes.json", "/api/v1/changes"),
    ]
    for public, backend in pairs:
        a, b = get(fe + public), get(be + backend)
        check(a.status == 200 and a.content_type == "application/json" and a.json() == b.json(),
              f"twin {public} equals {backend}", f"{a.status} {a.content_type}")
    # Stats counters move with every request, so compare the stable parts.
    for public, backend in (("/stats.json", "/api/v1/stats"), (f"/stats/{month}.json", f"/api/v1/stats/{month}")):
        a, b = get(fe + public), get(be + backend)
        ok = a.status == 200 and a.content_type == "application/json"
        if ok:
            ja, jb = a.json(), b.json()
            ok = ja.get("dataset") == jb.get("dataset") and ja["usage"]["month"] == jb["usage"]["month"]
        check(ok, f"twin {public} matches {backend} (dataset and month)")
    for public, backend in ((f"/fit/feed.xml?{fit_q}", f"/api/v1/fit/feed.xml?{fit_q}"),
                            ("/changes/feed.xml", "/api/v1/changes/feed.xml")):
        a, b = get(fe + public), get(be + backend)
        ok = a.status == 200 and "atom" in a.content_type and a.body == b.body
        if ok:
            ET.fromstring(a.body)
        check(ok, f"feed {public} equals {backend} and parses as XML", a.content_type)
    # /mcp reaches the backend's MCP endpoint (POST only): same answer both ways.
    rpc = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    answers = []
    for url in (f"{fe}/mcp", f"{be}/mcp"):
        req = Request(url, data=rpc, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            "User-Agent": CURL_UA})
        try:
            with _opener.open(req, timeout=30) as res:
                answers.append((res.status, res.read()))
        except HTTPError as err:
            answers.append((err.code, err.read()))
    check(answers[0] == answers[1], "POST /mcp through the frontend reaches the backend's MCP endpoint",
          f"{answers[0][0]} vs {answers[1][0]}")
    # The ID is not swallowed by the .json suffix, and an unknown one is the API's 404.
    missing = get(f"{fe}/grants/OPP-NOPE-0000.json")
    check(missing.status == 404 and missing.content_type == "application/json", "unknown twin is a JSON 404")


def check_negotiation(fe: str, sample: dict) -> None:
    gid, slug = sample["grant"], sample["funder"]
    for path, twin in ((f"/grants/{gid}", f"/grants/{gid}.json"), ("/grants", "/grants.json"),
                       (f"/funders/{slug}", f"/funders/{slug}.json"), ("/changes", "/changes.json"),
                       (f"/fit?{sample['fit_query']}", f"/fit.json?{sample['fit_query']}")):
        js = get(fe + path, accept="application/json")
        ok = js.status == 200 and js.content_type == "application/json" and js.json() == get(fe + twin).json()
        check(ok, f"{path} with Accept: application/json returns the twin", js.content_type)
        check("accept" in (js.headers.get("vary") or "").lower(), f"{path}: JSON response has Vary: Accept")
        for accept, label in ((BROWSER_ACCEPT, "a browser's Accept"), ("*/*", "Accept: */*")):
            h = get(fe + path, accept=accept, ua=BROWSER_UA)
            check(h.status == 200 and h.content_type == "text/html", f"{path} with {label} returns HTML", h.content_type)


def check_lists_exclude(fe: str, be: str, stale_ids: list[str], sitemap_text: str) -> None:
    listed = [
        get(f"{fe}/grants.json?status=all&limit=100").text,
        get(f"{fe}/grants?status=all&limit=100").text,
        get(f"{fe}/grants").text,
        get(f"{fe}/fit.json?limit=100").text,
        get(f"{fe}/fit?limit=100").text,
        get(f"{fe}/changes").text,
        get(f"{fe}/changes/feed.xml").text,
        get(f"{fe}/funders").text,
        get(f"{be}/api/v1/ids").text,
        get(f"{fe}/").text,
    ]
    for funder in get(f"{fe}/funders.json").json()["data"]:
        listed.append(get(f"{fe}/funders/{funder['slug']}").text)
    for sid in stale_ids:
        check(sid not in sitemap_text, f"stale {sid} is not in the sitemap")
        check(all(sid not in body for body in listed), f"stale {sid} is in no list, feed or fit result")


def check_discovery(fe: str, records: list[dict], stale_ids: list[str], index: dict) -> str:
    base = index["base_url"].rstrip("/")
    robots = get(f"{fe}/robots.txt")
    ok = robots.status == 200 and robots.content_type == "text/plain"
    text = robots.text
    check(ok, "robots.txt is 200 text/plain", robots.content_type)
    check(all(f"User-Agent: {c}" in text for c in NAMED_CRAWLERS), "robots.txt names every AI and search crawler")
    for line in ("Disallow: /out/", "Disallow: /api/", "Allow: /api/v1/", "Disallow: /workspace", "Allow: /",
                 f"Sitemap: {base}/sitemap.xml"):
        check(line in text, f"robots.txt has '{line}'")
    check("gobuga.org" not in text or base.endswith("gobuga.org"), "robots.txt uses the deployment's own origin")

    sm = get(f"{fe}/sitemap.xml")
    check(sm.status == 200 and "xml" in sm.content_type, "sitemap.xml is 200 XML", sm.content_type)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text for el in ET.fromstring(sm.body).findall("s:url/s:loc", ns)]
    check(all(loc.startswith(base + "/") for loc in locs), "every sitemap URL is on the deployment's origin")
    check(not any("?" in loc for loc in locs), "no filtered or paginated URL in the sitemap")
    listed = {r["id"] for r in records if r["status"] in ("live", "closed")}
    check(listed <= {loc.rsplit("/", 1)[-1] for loc in locs}, "every live and closed grant is in the sitemap")
    for path in ("/", "/grants", "/funders", "/fit", "/changes", "/stats", "/subscribe"):
        check(f"{base}{path}" in locs, f"sitemap lists {path}")

    llms = get(f"{fe}/llms.txt")
    text = llms.text
    check(llms.status == 200 and llms.content_type == "text/plain", "llms.txt is 200 text/plain")
    check(text.startswith("# GoBuga\n"), "llms.txt starts with its H1")
    check(all(f"{base}{row['public']}" in text for row in index["url_scheme"]),
          "llms.txt lists every public path of the URL scheme")
    check(f"{base}/mcp" in text, "llms.txt names the MCP endpoint")
    check(index["licence"]["summary"] in text, "llms.txt carries the licence line")
    check("gobuga.org" not in text or base.endswith("gobuga.org"), "llms.txt uses the deployment's own origin")
    curls = re.findall(r"^curl -s (?:-H '([^']+)' )?'([^']+)'$", text, flags=re.M)
    check(len(curls) == 3, "llms.txt has three curl lines", str(len(curls)))
    for header, url in curls:
        accept = header.split(":", 1)[1].strip() if header else "*/*"
        res = get(url.replace(base, fe, 1), accept=accept)
        check(res.status == 200 and res.content_type == "application/json", f"llms.txt example works: {url}")
    return sm.text


def check_pages(fe: str, sample: dict, lang: str) -> None:
    routes = {
        "/": 200, "/grants": 200, "/grants?tag=community": 200, "/grants?region=national": 200,
        "/grants?offset=20": 200, "/grants?q=%3Cscript%3E": 200, "/funders": 200,
        f"/funders/{sample['funder']}": 200, "/fit": 200, f"/fit?{sample['fit_query']}": 200,
        "/fit?region=atlantis": 200, "/changes": 200, "/stats": 200, f"/stats/{sample['month']}": 200,
        "/stats/2999-01": 404, "/stats/garbage": 404, "/subscribe": 200, "/subscribe?state=sent": 200,
        "/grants/OPP-NOPE-0000": 404, "/funders/no-such-funder": 404, "/nope": 404,
        "/ui-en/grants": 404, "/ui-auto": 404, "/grants-search": 404, "/grant-fit/x": 404,
        f"/grants/{sample['grant']}/fit": 404,
        "/login": 200, "/workspace": 200, "/privacy": 200, "/terms": 200,
    }
    for path, want in routes.items():
        res = get(fe + path)
        check(res.status == want, f"GET {path} is {want}", str(res.status))
        if res.content_type == "text/html":
            check(HOSTILE_RAW not in res.text and "<script>alert(1)" not in res.text,
                  f"GET {path}: no unescaped hostile markup")
            if want == 404:
                missing = Page(res.text)
                check(len(missing.h1) == 1 and 'id="__next_error__"' not in res.text,
                      f"GET {path}: the 404 page's text is in the HTML, not left to JavaScript")
                check("noindex" in (missing.meta("robots") or ""), f"GET {path}: the 404 page is noindex")
            if want == 200 and path not in ("/login", "/workspace", "/privacy", "/terms"):
                check(res.headers.get("set-cookie") is None, f"GET {path}: sets no cookie")
                third_party = re.findall(r'(?:src|href)="(https?://[^"]+)"', head_of(res.text))
                check(not [u for u in third_party if not u.startswith(fe)],
                      f"GET {path}: <head> loads nothing from another origin", ", ".join(third_party[:3]))
                page = Page(res.text)
                check(page.lang == lang, f"GET {path}: <html lang=\"{lang}\">", str(page.lang))
                check(len(page.h1) == 1, f"GET {path}: exactly one <h1>", str(len(page.h1)))
                check('href="#main"' in res.text and 'id="main"' in res.text, f"GET {path}: skip link")
                check(page.link("canonical") is not None, f"GET {path}: has a canonical link")
    bad = Page(get(f"{fe}/fit?region=atlantis").text).text
    check(WORDS[lang]["region"] in bad, "a bad fit value names the field in a plain message")
    grants = get(f"{fe}/grants").text
    for facet in ("tag=", "region=", "amount=", "deadline=", "status=closed"):
        check(f'href="/grants?{facet}' in grants, f"/grants links facets for {facet.rstrip('=')}")
    lds = [json.loads(b) for b in Page(grants).jsonld]
    check(any(ld.get("@type") == "Dataset" and ld.get("distribution") for ld in lds),
          "/grants carries a Dataset JSON-LD block with downloads")
    fit = get(f"{fe}/fit?{sample['fit_query']}")
    canonical = Page(fit.text).link("canonical")
    check(canonical is not None and "sector=" in canonical, "/fit sets a canonical fit URL", str(canonical))
    swapped = get(f"{fe}/fit?{'&'.join(reversed(sample['fit_query'].split('&')))}")
    check(Page(swapped.text).link("canonical") == canonical, "a fit URL in another order has the same canonical")
    check("/fit/feed.xml?" in fit.text and "/fit.json?" in fit.text, "/fit shows its feed and JSON URLs")
    check(f"/grants/{sample['fit_grant']}?" in fit.text, "/fit links grants carrying the fit parameters")
    reached = get(f"{fe}/grants/{sample['fit_grant']}?{sample['fit_query']}")
    rp = Page(reached.text)
    check(reached.status == 200 and rp.link("canonical", None) == sample["fit_grant_canonical"],
          "a grant reached from a fit URL keeps the bare canonical")
    check(any(reason in rp.text for reason in sample["fit_why"]), "a grant reached from a fit URL shows why it fits")


def head_of(body: str) -> str:
    """The <head>: metadata must be here for every reader, not streamed into the body."""
    return body[:body.find("</head>")]


def check_languages(fe: str, config: dict, sample: dict) -> None:
    """Every UI language the country offers renders with its own <html lang>,
    self-canonical URL and hreflang alternates; the default carries no lang=."""
    langs = config["ui_languages"]
    default = config["content_language"]
    path = f"/grants/{sample['grant']}"
    for lang in langs:
        url = path if lang == default else f"{path}?lang={lang}"
        page = Page(get(fe + url).text)
        check(page.lang == lang, f"{url}: <html lang=\"{lang}\">", str(page.lang))
        canonical = page.link("canonical") or ""
        check(canonical.endswith(url), f"{url}: canonical is its own language's URL", canonical)
        alternates = {link.get("hreflang") for link in page.links if link.get("rel") == "alternate"}
        if len(langs) > 1:
            check(set(langs) | {"x-default"} <= alternates, f"{url}: hreflang alternates for every language")
            check(f"lang={lang}" in get(fe + url).text or lang == default,
                  f"{url}: internal links carry lang={lang}")
        else:
            check(not (alternates - {None}), f"{url}: no hreflang on a one-language site")
    other = next((code for code in ("en", "ro", "ru") if code not in langs), None)
    if other:
        page = Page(get(f"{fe}{path}?lang={other}").text)
        check(page.lang == default, f"?lang={other} (not offered) falls back to {default}", str(page.lang))


def check_same_for_everyone(fe: str, sample: dict) -> None:
    for path in ("/", "/grants", f"/grants/{sample['grant']}", "/funders", "/changes",
                 f"/fit?{sample['fit_query']}", "/grants?tag=community", "/subscribe"):
        a_body = get(fe + path, accept=BROWSER_ACCEPT, ua=BROWSER_UA).text
        b_body = get(fe + path).text
        check(Page(a_body).text == Page(b_body).text, f"{path}: a browser and curl get the same text")
        for body, who in ((a_body, "a browser"), (b_body, "curl")):
            head = head_of(body)
            check("<title>" in head and 'rel="canonical"' in head and 'name="description"' in head,
                  f"{path}: title, description and canonical are in <head> for {who}")


def check_out(fe: str, records: list[dict]) -> None:
    for rec in records[:3] + [r for r in records if r["status"] == "closed"][:1]:
        res = get(f"{fe}/out/{rec['id']}")
        check(res.status == 302 and res.headers.get("location") == rec["source_url"],
              f"/out/{rec['id']} redirects to the stored funder URL", f"{res.status} {res.headers.get('location')}")
    check(get(f"{fe}/out/OPP-NOPE-0000").status == 404, "/out/ for an unknown ID is 404")


def check_no_cache_fill(fe: str, next_dir: str) -> None:
    """Made-up IDs and slugs get a 404 and leave nothing in Next's page cache."""
    nonce = uuid.uuid4().hex[:10].upper()
    paths = [f"/grants/OPP-FAKE-{nonce}-{n}" for n in range(3)] + [f"/funders/fake-{nonce.lower()}"]
    for path in paths:
        check(get(fe + path).status == 404, f"GET {path} is 404")
    written = glob.glob(os.path.join(next_dir, "server", "app", "**", f"*{nonce}*"), recursive=True)
    written += glob.glob(os.path.join(next_dir, "server", "app", "**", f"*{nonce.lower()}*"), recursive=True)
    check(not written, "made-up IDs and slugs write nothing to the page cache", ", ".join(written[:3]))


def check_hits(fe: str, data_dir: str, country: str) -> None:
    nonce = uuid.uuid4().hex[:12]
    page_ua, twin_ua = f"GPTBot/1.1 (verify-page-{nonce})", f"GPTBot/1.1 (verify-twin-{nonce})"
    get(f"{fe}/grants?utm_source=verify-{nonce}", ua=page_ua)
    get(f"{fe}/grants.json", ua=twin_ua)
    get(f"{fe}/workspace", ua=page_ua)
    time.sleep(2)
    lines = []
    for path in glob.glob(os.path.join(data_dir, "platform", "metrics", country, "*.jsonl")):
        with open(path, encoding="utf-8") as f:
            lines += [json.loads(line) for line in f if nonce in line]
    pages = [ln for ln in lines if f"verify-page-{nonce}" in ln.get("ua", "")]
    twins = [ln for ln in lines if f"verify-twin-{nonce}" in ln.get("ua", "")]
    check(len(pages) == 1 and pages[0]["surface"] == "page" and pages[0]["path"] == "/grants",
          "a public page hit is reported once, as a page, path without the query", json.dumps(pages))
    check(not any(k in ln for ln in lines for k in ("ip", "client", "cookie")), "no IP or cookie is recorded")
    check(len(twins) == 1 and twins[0]["surface"] == "api", "a JSON twin is counted once, by the backend",
          json.dumps(twins))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frontend", required=True)
    ap.add_argument("--backend", required=True)
    ap.add_argument("--data-dir", help="The backend's throwaway GOBUGA_DATA_DIR, to check page-hit reporting")
    ap.add_argument("--next-dir", help="The frontend's .next directory, to check made-up IDs are not cached")
    ap.add_argument("--country", default="nz")
    args = ap.parse_args(argv)
    fe, be = args.frontend.rstrip("/"), args.backend.rstrip("/")

    index = get(f"{be}/api/v1").json()
    index["backend"] = be
    config = get(f"{be}/api/public/country-config").json()
    index["timezone"] = config.get("timezone") or "UTC"
    index["lang"] = config["content_language"]
    lang = config["content_language"]
    records = all_records(be)
    listed_ids = {r["id"] for r in records}
    # Stale rows appear in no list; find them by walking the published ID space.
    stale = []
    for rec_id in {r["id"].rsplit("-", 1)[0] for r in records}:
        for n in range(1, 60):
            cand = f"{rec_id}-{n:04d}"
            if cand in listed_ids:
                continue
            res = get(f"{be}/api/v1/opportunities/{cand}")
            if res.status == 200 and res.json()["status"] == "stale":
                stale.append(res.json())
    check(len(stale) >= 1, "the dataset has at least one stale grant to check", str(len(stale)))
    check(any(r["status"] == "closed" for r in records), "the dataset has at least one closed grant")

    live = [r for r in records if r["status"] == "live"]
    funders = get(f"{be}/api/v1/funders").json()["data"]
    tags = get(f"{be}/api/v1/taxonomy").json()
    fit_query = f"sector={tags['tags'][0]['slug']}&status={tags['entity_vocab'][0]}"
    fit_body = get(f"{be}/api/v1/fit?{fit_query}").json()
    first = fit_body["data"][0] if fit_body["data"] else live[0]
    sample = {
        "grant": live[0]["id"],
        "funder": funders[0]["slug"],
        "month": get(f"{be}/api/v1/stats").json()["usage"]["month"],
        "fit_query": fit_query,
        "fit_grant": first["id"],
        "fit_grant_canonical": first["canonical_url"],
        "fit_why": first.get("why") or ["-"],
    }

    print(f"# {len(records)} listed grants ({len(live)} live), {len(stale)} stale, {len(funders)} funders; "
          f"country {config['country']}, language {lang}\n")
    print("## Pages and status codes")
    check_pages(fe, sample, lang)
    print("\n## Grant pages")
    for rec in records + stale:
        check_grant_page(fe, rec, index)
    print("\n## JSON twins and feeds")
    check_twins(fe, be, sample)
    print("\n## Content negotiation")
    check_negotiation(fe, sample)
    print("\n## Discovery files")
    sitemap_text = check_discovery(fe, records, [s["id"] for s in stale], index)
    print("\n## Stale grants are listed nowhere")
    check_lists_exclude(fe, be, [s["id"] for s in stale], sitemap_text)
    print("\n## Click-outs")
    check_out(fe, records)
    print("\n## Languages")
    check_languages(fe, config, sample)
    print("\n## Same page for everyone")
    check_same_for_everyone(fe, sample)
    if args.next_dir:
        print("\n## No cache fill from made-up URLs")
        check_no_cache_fill(fe, args.next_dir)
    if args.data_dir:
        print("\n## Page-hit reporting")
        check_hits(fe, args.data_dir, config["country"])
    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed")
    return 1 if RESULTS["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
