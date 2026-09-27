"""scripts/citation_audit.py: the citation matcher, response parsers, the run
loop with fake adapters, provider selection, the dry run and the batteries.

No network: every adapter here is a fake, and the dry run is checked to build
no client at all.
"""

import json
import os

import pytest

import api.tenant as tenant
from scripts import citation_audit as ca


# --- match_citations ---

@pytest.mark.parametrize("urls, cited, rank", [
    ([], False, None),
    (["https://gobuga.org/grants/OPP-1"], True, 1),
    (["https://www.gobuga.org/"], True, 1),
    (["https://md.gobuga.org/grants/OPP-MD-1"], True, 1),
    (["https://GoBuga.org/x"], True, 1),
    (["https://a.example/1", "https://a.example/2", "https://gobuga.org/"], True, 2),
    (["https://a.example/", "https://b.example/", "https://gobuga.org/", "https://gobuga.org/x"], True, 3),
    (["https://notgobuga.org/"], False, None),
    (["https://gobuga.org.evil.example/"], False, None),
    (["https://evil.example/?ref=gobuga.org"], False, None),
    (["https://evil.example/gobuga.org"], False, None),
    (["not a url", "", "https://gobuga.org/"], True, 1),
])
def test_match_citations(urls, cited, rank):
    m = ca.match_citations(urls)
    assert (m["cited"], m["rank"]) == (cited, rank)


def test_match_citations_domains_are_distinct_and_ordered():
    m = ca.match_citations([
        "https://www.b.example/1", "https://a.example/", "https://b.example/2", "https://gobuga.org/",
    ])
    assert m["domains"] == ["b.example", "a.example", "gobuga.org"]
    assert m["rank"] == 3


# --- response parsers ---

def test_anthropic_cited_urls():
    content = [
        {"type": "server_tool_use", "name": "web_search", "input": {"query": "q"}},
        {"type": "web_search_tool_result", "content": [{"type": "web_search_result", "url": "https://uncited.example/"}]},
        {"type": "text", "text": "Intro.", "citations": None},
        {"type": "text", "text": "A", "citations": [
            {"type": "web_search_result_location", "url": "https://a.example/"},
            {"type": "web_search_result_location", "url": "https://gobuga.org/grants/1"},
        ]},
    ]
    assert ca.anthropic_cited_urls(content) == ["https://a.example/", "https://gobuga.org/grants/1"]


def test_openai_cited_urls():
    body = {"output": [
        {"type": "web_search_call", "status": "completed"},
        {"type": "message", "content": [{"type": "output_text", "text": "...", "annotations": [
            {"type": "url_citation", "url": "https://a.example/"},
            {"type": "file_citation", "file_id": "f"},
            {"type": "url_citation", "url": "https://gobuga.org/"},
        ]}]},
    ]}
    assert ca.openai_cited_urls(body) == ["https://a.example/", "https://gobuga.org/"]


def test_perplexity_cited_urls():
    assert ca.perplexity_cited_urls({"citations": ["https://a.example/", 3]}) == ["https://a.example/"]
    assert ca.perplexity_cited_urls({"search_results": [{"url": "https://b.example/"}, {}]}) == ["https://b.example/"]
    assert ca.perplexity_cited_urls({}) == []


# --- adapters with injected fakes ---

class _Block:
    def __init__(self, d):
        self.d = d

    def model_dump(self):
        return self.d


class _Resp:
    def __init__(self, blocks, stop_reason):
        self.content = [_Block(b) for b in blocks]
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _FakeAnthropic:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def _cite(url):
    return {"type": "text", "text": "x", "citations": [{"type": "web_search_result_location", "url": url}]}


def test_anthropic_provider_sends_web_search_and_resumes_pause_turn():
    fake = _FakeAnthropic([
        _Resp([_cite("https://a.example/")], "pause_turn"),
        _Resp([_cite("https://gobuga.org/")], "end_turn"),
    ])
    p = ca.AnthropicProvider("key", client=fake)
    answer = p.ask("What grants?")
    assert answer.urls == ["https://a.example/", "https://gobuga.org/"]
    assert answer.error is None
    first, second = fake.messages.calls
    assert first["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}]
    assert first["messages"] == [{"role": "user", "content": "What grants?"}]
    assert second["messages"][-1]["role"] == "assistant"


def test_anthropic_provider_flags_refusal():
    fake = _FakeAnthropic([_Resp([], "refusal")])
    assert ca.AnthropicProvider("key", client=fake).ask("q").error == "refusal"


class _FakeHttp:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def post(self, url, headers=None, json=None):
        self.calls.append((url, headers, json))
        body = self.body

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return body
        return R()


def test_openai_provider():
    http = _FakeHttp({"output": [{"type": "message", "content": [{"annotations": [
        {"type": "url_citation", "url": "https://gobuga.org/"}]}]}]})
    answer = ca.OpenAIProvider("sk-test", http=http).ask("q")
    assert answer.urls == ["https://gobuga.org/"]
    url, headers, body = http.calls[0]
    assert url == ca.OpenAIProvider.URL
    assert headers == {"Authorization": "Bearer sk-test"}
    assert body["tools"] == [{"type": "web_search"}]


def test_perplexity_provider():
    http = _FakeHttp({"citations": ["https://a.example/"]})
    answer = ca.PerplexityProvider("pplx-test", http=http).ask("q")
    assert answer.urls == ["https://a.example/"]
    assert http.calls[0][2]["messages"] == [{"role": "user", "content": "q"}]


# --- run_audit with fake adapters ---

class _Fake(ca.Provider):
    def __init__(self, name, urls=None, raises=None):
        self.name, self.model, self.urls, self.raises = name, f"{name}-model", urls or [], raises
        self.asked = []

    def ask(self, question):
        self.asked.append(question)
        if self.raises:
            raise self.raises
        return ca.Answer(urls=self.urls)


QUESTIONS = [
    {"id": "nz-01", "language": "en", "text": "Q1"},
    {"id": "nz-02", "language": "en", "text": "Q2"},
]


def test_run_audit_records_every_pair_and_survives_failure():
    good = _Fake("good", urls=["https://a.example/", "https://gobuga.org/x"])
    bad = _Fake("bad", raises=RuntimeError("rate limited"))
    report = ca.run_audit(QUESTIONS, [good, bad], country="nz")
    assert good.asked == ["Q1", "Q2"] and bad.asked == ["Q1", "Q2"]
    assert len(report["results"]) == 4
    r0 = report["results"][0]
    assert (r0["provider"], r0["cited"], r0["rank"]) == ("good", True, 2)
    assert r0["domains"] == ["a.example", "gobuga.org"]
    r1 = report["results"][1]
    assert r1["cited"] is False and r1["error"] == "RuntimeError: rate limited"
    assert report["summary"] == {
        "good": {"asked": 2, "errors": 0, "cited": 2, "best_rank": 2},
        "bad": {"asked": 2, "errors": 2, "cited": 0, "best_rank": None},
    }


def test_report_written_under_platform_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    from datetime import datetime, timezone
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    path = ca.report_path("md", now)
    assert path == str(tmp_path / "platform" / "metrics" / "citation" / "md-2026-09-28.json")
    ca.write_report({"ok": 1}, path)
    assert json.loads(open(path).read()) == {"ok": 1}


# --- provider selection and dry run ---

def test_available_providers_by_key():
    ready, skipped = ca.available_providers({"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": ""})
    assert [name for name, _, _ in ready] == ["openai"]
    assert skipped == ["anthropic (no ANTHROPIC_API_KEY)", "perplexity (no PERPLEXITY_API_KEY)"]


def test_dry_run_makes_no_call_and_builds_no_client(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbidden(*a, **k):
        raise AssertionError("dry run built a client")
    for cls in (ca.AnthropicProvider, ca.OpenAIProvider, ca.PerplexityProvider):
        monkeypatch.setattr(cls, "__init__", forbidden)
    monkeypatch.setattr(ca, "run_audit", forbidden)

    assert ca.main(["--country", "nz", "--dry-run", "--limit", "3"]) == 0
    out = capsys.readouterr().out
    assert "questions=3" in out
    assert "providers: anthropic, perplexity" in out
    assert "skipping openai (no OPENAI_API_KEY)" in out
    assert "estimated calls: 6" in out
    assert "nz-03" in out and "nz-04" not in out


# --- batteries ---

@pytest.mark.parametrize("country", ["nz", "md"])
def test_battery_shape(country):
    qs = ca.load_battery(country)
    assert len(qs) == 20
    assert len({q["id"] for q in qs}) == 20
    assert all(q["text"].strip() for q in qs)
    assert not any("gobuga" in q["text"].lower() for q in qs)


def test_battery_languages():
    assert {q["language"] for q in ca.load_battery("nz")} == {"en"}
    md = [q["language"] for q in ca.load_battery("md")]
    assert md.count("ro") == 10 and md.count("ru") == 10


def test_every_country_config_has_a_battery():
    sources = os.path.join(tenant.PLATFORM_CONFIG_DIR, "sources")
    for name in os.listdir(sources):
        if name.endswith(".json"):
            assert os.path.exists(os.path.join(ca.BATTERY_DIR, name))
