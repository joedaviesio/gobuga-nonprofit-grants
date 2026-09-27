#!/usr/bin/env python3
"""Citation audit: do the major assistants cite GoBuga when asked about grants?

Asks each configured assistant each question in the country's battery
(`scripts/citation_battery/<country>.json`) with web search on, and records per
question and assistant whether a GoBuga domain is among the cited sources, at
what rank, and the full list of cited domains.

Output: `<platform_dir>/metrics/citation/<country>-<YYYY-MM-DD>.json`, plus a
short printed summary.

Providers are chosen by which API keys are set; one with no key is skipped
with a note:
    ANTHROPIC_API_KEY   Claude, server-side web search tool
    OPENAI_API_KEY      OpenAI Responses API, web_search tool
    PERPLEXITY_API_KEY  Perplexity Sonar (searches by default)

Model overrides: CITATION_AUDIT_ANTHROPIC_MODEL, CITATION_AUDIT_OPENAI_MODEL,
CITATION_AUDIT_PERPLEXITY_MODEL.

A real run costs money: one paid call per question per provider, each with
web search. It needs the director's sign-off.

Usage:
    python3 scripts/citation_audit.py --dry-run                 # plan only, no calls
    python3 scripts/citation_audit.py --country md --dry-run
    python3 scripts/citation_audit.py --limit 3                 # first 3 questions
"""

import tirith  # noqa: F401  — must come before anthropic; routes calls through local tirith proxy

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

BATTERY_DIR = os.path.join(PROJECT_ROOT, "scripts", "citation_battery")
GOBUGA_DOMAINS: tuple[str, ...] = ("gobuga.org", "md.gobuga.org")
HTTP_TIMEOUT_S = 180.0


# --- Citation matching (pure) ---

def domain_of(url: str) -> str | None:
    """Lowercased host of a URL without a leading `www.`; None if unparseable."""
    try:
        host = urlsplit(url.strip()).hostname
    except (ValueError, AttributeError):
        return None
    if not host:
        return None
    host = host.rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _is_gobuga(domain: str, domains: tuple[str, ...]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in domains)


def match_citations(urls: list[str], domains: tuple[str, ...] = GOBUGA_DOMAINS) -> dict:
    """Does this ordered list of cited URLs cite GoBuga, and at what rank?

    Rank is the 1-based position of the first GoBuga domain among the distinct
    cited domains, in order of first appearance, so three links to one funder's
    site count as one source. Returns {"cited", "rank", "domains"}.
    """
    ordered: list[str] = []
    for url in urls:
        d = domain_of(url)
        if d and d not in ordered:
            ordered.append(d)
    rank = next((i + 1 for i, d in enumerate(ordered) if _is_gobuga(d, domains)), None)
    return {"cited": rank is not None, "rank": rank, "domains": ordered}


# --- Provider adapters ---

@dataclass
class Answer:
    urls: list[str] = field(default_factory=list)  # cited sources, in order
    error: str | None = None


class Provider:
    """One assistant. Subclasses implement `ask`."""

    name = ""
    model = ""

    def ask(self, question: str) -> Answer:
        raise NotImplementedError


def anthropic_cited_urls(content: list[dict]) -> list[str]:
    """Cited URLs, in order, from Messages API content blocks (as dicts)."""
    urls = []
    for block in content:
        if block.get("type") != "text":
            continue
        for c in block.get("citations") or []:
            if c.get("url"):
                urls.append(c["url"])
    return urls


class AnthropicProvider(Provider):
    name = "anthropic"
    MAX_CONTINUATIONS = 3  # pause_turn resumptions for long searches

    def __init__(self, api_key: str, client=None):
        self.model = os.environ.get("CITATION_AUDIT_ANTHROPIC_MODEL", "claude-opus-5")
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
        self.client = client

    def ask(self, question: str) -> Answer:
        messages = [{"role": "user", "content": question}]
        content: list[dict] = []
        for _ in range(1 + self.MAX_CONTINUATIONS):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
                messages=messages,
            )
            blocks = [b.model_dump() for b in resp.content]
            content.extend(blocks)
            if resp.stop_reason != "pause_turn":
                break
            messages = messages + [{"role": "assistant", "content": resp.content}]
        error = "refusal" if resp.stop_reason == "refusal" else None
        return Answer(urls=anthropic_cited_urls(content), error=error)


def openai_cited_urls(body: dict) -> list[str]:
    """Cited URLs, in order, from a Responses API body."""
    urls = []
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            for ann in part.get("annotations") or []:
                if ann.get("type") == "url_citation" and ann.get("url"):
                    urls.append(ann["url"])
    return urls


class OpenAIProvider(Provider):
    name = "openai"
    URL = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str, http=None):
        self.model = os.environ.get("CITATION_AUDIT_OPENAI_MODEL", "gpt-5")
        self.api_key = api_key
        if http is None:
            import httpx
            http = httpx.Client(timeout=HTTP_TIMEOUT_S)
        self.http = http

    def ask(self, question: str) -> Answer:
        resp = self.http.post(
            self.URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "tools": [{"type": "web_search"}], "input": question},
        )
        resp.raise_for_status()
        return Answer(urls=openai_cited_urls(resp.json()))


def perplexity_cited_urls(body: dict) -> list[str]:
    """Cited URLs, in order, from a Sonar chat completion body."""
    if body.get("citations"):
        return [u for u in body["citations"] if isinstance(u, str)]
    return [r["url"] for r in body.get("search_results") or [] if r.get("url")]


class PerplexityProvider(Provider):
    name = "perplexity"
    URL = "https://api.perplexity.ai/chat/completions"

    def __init__(self, api_key: str, http=None):
        self.model = os.environ.get("CITATION_AUDIT_PERPLEXITY_MODEL", "sonar")
        self.api_key = api_key
        if http is None:
            import httpx
            http = httpx.Client(timeout=HTTP_TIMEOUT_S)
        self.http = http

    def ask(self, question: str) -> Answer:
        resp = self.http.post(
            self.URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "messages": [{"role": "user", "content": question}]},
        )
        resp.raise_for_status()
        return Answer(urls=perplexity_cited_urls(resp.json()))


PROVIDERS: tuple[tuple[str, str, type], ...] = (
    ("anthropic", "ANTHROPIC_API_KEY", AnthropicProvider),
    ("openai", "OPENAI_API_KEY", OpenAIProvider),
    ("perplexity", "PERPLEXITY_API_KEY", PerplexityProvider),
)


def available_providers(env=None) -> tuple[list[tuple[str, str, type]], list[str]]:
    """(provider specs whose key is set, names skipped for want of a key).

    Nothing is constructed here, so a dry run never builds a client.
    """
    env = os.environ if env is None else env
    ready, skipped = [], []
    for name, key_var, cls in PROVIDERS:
        (ready if env.get(key_var) else skipped).append((name, key_var, cls))
    return ready, [f"{name} (no {key_var})" for name, key_var, _ in skipped]


# --- Battery and run ---

def load_battery(country: str) -> list[dict]:
    """[{id, language, text}] for the country."""
    with open(os.path.join(BATTERY_DIR, f"{country}.json"), encoding="utf-8") as f:
        return json.load(f)["questions"]


def run_audit(questions: list[dict], providers: list[Provider], *, country: str, now=None) -> dict:
    """Ask every provider every question. A failed call is recorded, not raised."""
    now = now or datetime.now(timezone.utc)
    results = []
    for q in questions:
        for p in providers:
            try:
                answer = p.ask(q["text"])
            except Exception as exc:  # noqa: BLE001 — one failure must not end the run
                answer = Answer(error=f"{type(exc).__name__}: {exc}")
            match = match_citations(answer.urls)
            results.append({
                "question_id": q["id"],
                "language": q["language"],
                "question": q["text"],
                "provider": p.name,
                "model": p.model,
                "cited": match["cited"],
                "rank": match["rank"],
                "domains": match["domains"],
                "urls": answer.urls,
                "error": answer.error,
            })
    return {
        "country": country,
        "run_at": now.isoformat(),
        "gobuga_domains": list(GOBUGA_DOMAINS),
        "question_count": len(questions),
        "providers": [{"name": p.name, "model": p.model} for p in providers],
        "summary": summarise(results),
        "results": results,
    }


def summarise(results: list[dict]) -> dict:
    """Per provider: asked, answered, cited, errors and the best rank seen."""
    out: dict[str, dict] = {}
    for r in results:
        s = out.setdefault(r["provider"], {"asked": 0, "errors": 0, "cited": 0, "best_rank": None})
        s["asked"] += 1
        if r["error"]:
            s["errors"] += 1
        if r["cited"]:
            s["cited"] += 1
            if s["best_rank"] is None or r["rank"] < s["best_rank"]:
                s["best_rank"] = r["rank"]
    return out


def report_path(country: str, now: datetime) -> str:
    from api.tenant import platform_dir
    return os.path.join(platform_dir(), "metrics", "citation", f"{country}-{now:%Y-%m-%d}.json")


def write_report(report: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def print_plan(country: str, questions: list[dict], ready: list, skipped: list[str]) -> None:
    print(f"[citation_audit] country={country} questions={len(questions)}")
    for q in questions:
        print(f"  {q['id']} [{q['language']}] {q['text']}")
    names = [name for name, _, _ in ready]
    print(f"[citation_audit] providers: {', '.join(names) or 'none'}")
    for note in skipped:
        print(f"[citation_audit] skipping {note}")
    print(f"[citation_audit] estimated calls: {len(questions) * len(names)}")


def main(argv=None) -> int:
    from api.country_config import get_country

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--country", default=None, help="country slug (default: GOBUGA_COUNTRY or nz)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; make no network call")
    ap.add_argument("--limit", type=int, default=None, help="only the first N questions")
    args = ap.parse_args(argv)

    country = args.country or get_country()
    questions = load_battery(country)
    if args.limit is not None:
        questions = questions[: max(args.limit, 0)]
    ready, skipped = available_providers()

    print_plan(country, questions, ready, skipped)
    if args.dry_run:
        print("[citation_audit] dry run: no calls made")
        return 0
    if not ready:
        print("[citation_audit] no provider has a key; nothing to do", file=sys.stderr)
        return 1

    providers = [cls(os.environ[key_var]) for _, key_var, cls in ready]
    now = datetime.now(timezone.utc)
    report = run_audit(questions, providers, country=country, now=now)
    path = report_path(country, now)
    write_report(report, path)

    for name, s in report["summary"].items():
        print(f"[citation_audit] {name}: cited {s['cited']}/{s['asked']}, "
              f"best rank {s['best_rank']}, errors {s['errors']}")
    print(f"[citation_audit] wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
