"""api.metrics: classifiers, privacy rules, JSONL storage, monthly counters,
and the request-logging middleware.

Pure-Python and in-process; the data dir is a pytest tmp_path.
"""

import json
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.tenant as tenant
from api import metrics, server

NOW = datetime(2026, 9, 28, 10, 30, tzinfo=timezone.utc)
HUMAN_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
GPTBOT_UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GOBUGA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setenv("STARTUP_SWEEP_DISABLED", "1")
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    return tmp_path


def _lines(data_dir, country="nz", month="2026-09"):
    path = data_dir / "platform" / "metrics" / country / f"{month}.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# --- classify_agent ---

@pytest.mark.parametrize("ua, expected", [
    (GPTBOT_UA, "gptbot"),
    ("Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)", "gptbot"),
    ("Mozilla/5.0 (compatible; ChatGPT-User/1.0; +https://openai.com/bot)", "gptbot"),
    ("Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)", "claudebot"),
    ("Claude-User/1.0", "claudebot"),
    ("Claude-SearchBot/1.0", "claudebot"),
    ("anthropic-ai", "claudebot"),
    ("Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)", "perplexitybot"),
    ("Mozilla/5.0 (compatible; Perplexity-User/1.0)", "perplexitybot"),
    ("Google-Extended", "google-extended"),
    ("CCBot/2.0 (https://commoncrawl.org/faq/)", "ccbot"),
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "googlebot"),
    ("Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", "bingbot"),
    ("Mozilla/5.0 (compatible; YandexBot/3.0)", "other-bot"),
    ("SomeCrawler/1.0", "other-bot"),
    ("my-spider", "other-bot"),
    ("curl/8.4.0", "other-bot"),
    ("python-requests/2.32", "other-bot"),
    ("", "other-bot"),
    (None, "other-bot"),
    (HUMAN_UA, "human"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36", "human"),
])
def test_classify_agent(ua, expected):
    assert metrics.classify_agent(ua) == expected


# --- classify_role ---

@pytest.mark.parametrize("ua, expected", [
    (GPTBOT_UA, "training"),
    ("Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)", "search"),
    ("Mozilla/5.0 (compatible; ChatGPT-User/1.0; +https://openai.com/bot)", "fetch"),
    ("Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)", "training"),
    ("Claude-User/1.0", "fetch"),
    ("Claude-SearchBot/1.0", "search"),
    ("anthropic-ai", "training"),
    ("Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)", "search"),
    ("Mozilla/5.0 (compatible; Perplexity-User/1.0)", "fetch"),
    ("Google-Extended", "training"),
    ("CCBot/2.0 (https://commoncrawl.org/faq/)", "training"),
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "search"),
    ("Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", "search"),
    ("Mozilla/5.0 (compatible; YandexBot/3.0)", None),
    ("curl/8.4.0", None),
    ("", None),
    (None, None),
    (HUMAN_UA, None),
])
def test_classify_role(ua, expected):
    assert metrics.classify_role(ua) == expected


# --- classify_referrer ---

@pytest.mark.parametrize("ref, expected", [
    (None, "direct"),
    ("", "direct"),
    ("   ", "direct"),
    ("https://chatgpt.com/", "assistant"),
    ("https://chat.openai.com/c/abc", "assistant"),
    ("https://www.perplexity.ai/search?q=grants", "assistant"),
    ("https://claude.ai/chat/123", "assistant"),
    ("https://gemini.google.com/app", "assistant"),
    ("https://copilot.microsoft.com/", "assistant"),
    ("chatgpt.com", "assistant"),
    ("https://www.google.com/", "search"),
    ("https://www.google.co.nz/", "search"),
    ("https://google.com.au/search", "search"),
    ("https://www.bing.com/search?q=x", "search"),
    ("https://duckduckgo.com/", "search"),
    ("https://yandex.ru/search/?text=granturi", "search"),
    ("https://yandex.md/", "search"),
    ("https://mail.google.com/mail/u/0/", "email"),
    ("https://outlook.live.com/mail/0/", "email"),
    ("https://e.mail.ru/inbox/", "email"),
    ("https://example.org/newsletter?utm_medium=email&utm_source=x", "email"),
    ("https://example.org/page", "other"),
    ("https://docs.google.com/document/d/1", "other"),
    # Hostile referrers: the assistant name appears, but not as the host.
    ("https://evil.example/?x=chatgpt.com", "other"),
    ("https://chatgpt.com.evil.example/", "other"),
    ("https://notchatgpt.com/", "other"),
    ("https://evil.example/claude.ai/", "other"),
    ("https://chatgpt.com@evil.example/", "other"),
    ("https://google.com.evil.example/", "other"),
    ("http://[::1", "other"),  # unparseable
])
def test_classify_referrer(ref, expected):
    assert metrics.classify_referrer(ref) == expected


# --- record_hit and the privacy rules ---

def test_human_hit_stores_no_user_agent_and_no_ip(data_dir):
    metrics.record_hit("/api/v1/opportunities?q=secret", HUMAN_UA, "https://example.org/private",
                       surface="api", now=NOW)
    [line] = _lines(data_dir)
    assert line == {
        "kind": "hit", "surface": "api", "ts": NOW.isoformat(),
        "path": "/api/v1/opportunities", "agent": "human", "ref": "other",
    }
    raw = (data_dir / "platform" / "metrics" / "nz" / "2026-09.jsonl").read_text()
    assert "Safari" not in raw
    assert "example.org" not in raw  # host kept only for assistant/search
    assert "secret" not in raw  # query string dropped


def test_bot_hit_stores_raw_user_agent(data_dir):
    metrics.record_hit("/grants/OPP-1", GPTBOT_UA, None, surface="page", now=NOW)
    [line] = _lines(data_dir)
    assert line["agent"] == "gptbot"
    assert line["ua"] == GPTBOT_UA
    assert line["ref"] == "direct"
    assert "ref_host" not in line


def test_assistant_and_search_referrers_keep_host_only(data_dir):
    metrics.record_hit("/grants/OPP-1", HUMAN_UA, "https://chatgpt.com/c/private-thread-id",
                       surface="page", now=NOW)
    metrics.record_hit("/grants/OPP-1", HUMAN_UA, "https://www.google.co.nz/search?q=my+club",
                       surface="page", now=NOW)
    a, s = _lines(data_dir)
    assert (a["ref"], a["ref_host"]) == ("assistant", "chatgpt.com")
    assert (s["ref"], s["ref_host"]) == ("search", "www.google.co.nz")
    raw = (data_dir / "platform" / "metrics" / "nz" / "2026-09.jsonl").read_text()
    assert "private-thread-id" not in raw
    assert "my+club" not in raw


def test_email_campaign_tag_on_landing_path_wins(data_dir):
    metrics.record_hit("/changes?utm_medium=email", HUMAN_UA, None, surface="page", now=NOW)
    [line] = _lines(data_dir)
    assert line["ref"] == "email"
    assert line["path"] == "/changes"


def test_clickout_line(data_dir):
    metrics.record_clickout("OPP-NZ-1", "Rata Foundation", "https://claude.ai/chat/x", HUMAN_UA,
                            now=NOW)
    [line] = _lines(data_dir)
    assert line == {
        "kind": "clickout", "surface": "out", "ts": NOW.isoformat(), "path": "/out/OPP-NZ-1",
        "agent": "human", "ref": "assistant", "ref_host": "claude.ai",
        "opp_id": "OPP-NZ-1", "funder": "Rata Foundation",
    }


def test_long_fields_are_capped(data_dir):
    metrics.record_hit("/" + "a" * 5000, "bot/" + "b" * 5000, None, surface="page", now=NOW)
    [line] = _lines(data_dir)
    assert len(line["path"]) == metrics.MAX_PATH_CHARS
    assert len(line["ua"]) == metrics.MAX_UA_CHARS


def test_country_and_month_pick_the_file(data_dir):
    metrics.record_hit("/x", HUMAN_UA, None, surface="page", country="md",
                       now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert len(_lines(data_dir, "md", "2026-10")) == 1
    assert _lines(data_dir) == []


# --- never raises ---

def test_record_hit_never_raises(data_dir, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(metrics, "_append", boom)
    metrics.record_hit("/x", HUMAN_UA, None, surface="page")
    metrics.record_clickout("OPP-1", "F", None, HUMAN_UA)
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    assert all(l.startswith("[metrics]") and "disk full" in l for l in out)


def test_record_hit_bad_surface_or_country_is_dropped(data_dir, capsys):
    metrics.record_hit("/x", HUMAN_UA, None, surface="nope", now=NOW)
    metrics.record_hit("/x", HUMAN_UA, None, surface="page", country="../../etc", now=NOW)
    assert _lines(data_dir) == []
    assert len(capsys.readouterr().out.strip().splitlines()) == 2
    assert not (data_dir / "etc").exists()


@pytest.mark.parametrize("country, month", [("../x", "2026-09"), ("nz", "2026-13"),
                                            ("nz", "../../x"), ("", "2026-09")])
def test_metrics_path_rejects_malformed(data_dir, country, month):
    with pytest.raises(ValueError):
        metrics.metrics_path(country, month)


# --- concurrency ---

def test_concurrent_appends_do_not_interleave(data_dir):
    def worker(n):
        for i in range(50):
            metrics.record_hit(f"/api/v1/t{n}/{i}", GPTBOT_UA, None, surface="api", now=NOW)
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = _lines(data_dir)  # json.loads on every line: none torn
    assert len(lines) == 16 * 50


# --- monthly_counters ---

def test_counters_missing_file(data_dir):
    c = metrics.monthly_counters("nz", "2026-09")
    assert c["clickouts_total"] == 0
    assert c["hits_by_surface"] == {}
    assert c["top_clickout_grants"] == []


def test_bot_hit_stores_role(data_dir):
    metrics.record_hit("/grants/OPP-1", "ChatGPT-User/1.0", None, surface="page", now=NOW)
    metrics.record_hit("/grants/OPP-1", "curl/8", None, surface="page", now=NOW)
    metrics.record_hit("/grants/OPP-1", HUMAN_UA, None, surface="page", now=NOW)
    fetch, script, person = _lines(data_dir)
    assert fetch["agent"] == "gptbot" and fetch["role"] == "fetch"
    assert script["agent"] == "other-bot" and "role" not in script
    assert "role" not in person


@pytest.mark.parametrize("path, referrer, ref, host", [
    ("/grants/OPP-1?utm_source=chatgpt.com", None, "assistant", "chatgpt.com"),
    ("/grants/OPP-1?utm_source=ChatGPT.com", "https://example.org/", "assistant", "chatgpt.com"),
    ("/grants/OPP-1?utm_source=www.perplexity.ai", None, "assistant", "www.perplexity.ai"),
    # A referrer that names a source wins over the tag.
    ("/grants/OPP-1?utm_source=chatgpt.com", "https://www.google.com/", "search", "www.google.com"),
    ("/grants/OPP-1?utm_source=chatgpt.com", "https://claude.ai/chat/1", "assistant", "claude.ai"),
    ("/grants/OPP-1?utm_source=chatgpt.com&utm_medium=email", None, "email", None),
    # Only the fixed assistant hosts; nothing a client chose is stored.
    ("/grants/OPP-1?utm_source=newsletter", None, "direct", None),
    ("/grants/OPP-1?utm_source=chatgpt.com.evil.example", None, "direct", None),
    ("/grants/OPP-1?q=chatgpt.com", None, "direct", None),
])
def test_assistant_utm_source(data_dir, path, referrer, ref, host):
    metrics.record_hit(path, HUMAN_UA, referrer, surface="page", now=NOW)
    [line] = _lines(data_dir)
    assert line["path"] == "/grants/OPP-1"
    assert line["ref"] == ref
    assert line.get("ref_host") == host


def test_counters_role_from_old_lines(data_dir):
    """Lines written before roles were recorded are classed from their UA."""
    path = data_dir / "platform" / "metrics" / "nz"
    path.mkdir(parents=True)
    old = {"kind": "hit", "surface": "page", "ts": NOW.isoformat(), "path": "/grants/OPP-1",
           "agent": "gptbot", "ref": "direct"}
    (path / "2026-09.jsonl").write_text("".join(json.dumps(l) + "\n" for l in (
        {**old, "ua": GPTBOT_UA},
        {**old, "ua": "OAI-SearchBot/1.0"},
        {**old, "ua": "ChatGPT-User/1.0", "role": "bogus"},
        {**old, "ua": 5},
        {**old, "agent": "other-bot", "ua": "curl/8"},
    )))
    c = metrics.monthly_counters("nz", "2026-09")
    assert c["hits_by_agent"] == {"gptbot": 4, "other-bot": 1}
    assert c["hits_by_agent_role"] == {"gptbot": {"training": 1, "search": 1, "fetch": 1}}


def test_counters_empty_file(data_dir):
    path = data_dir / "platform" / "metrics" / "nz"
    path.mkdir(parents=True)
    (path / "2026-09.jsonl").write_text("")
    assert metrics.monthly_counters("nz", "2026-09")["hits_by_agent"] == {}


def test_counters_skip_corrupt_lines(data_dir):
    metrics.record_hit("/api/v1/fit", GPTBOT_UA, None, surface="api", now=NOW)
    path = data_dir / "platform" / "metrics" / "nz" / "2026-09.jsonl"
    with open(path, "a") as f:
        f.write('{"kind": "hit", "surface": "api", "pa\n')   # torn line
        f.write("not json at all\n")
        f.write("[1, 2, 3]\n")                                # JSON, not an object
        f.write('{"kind": "hit", "surface": ["x"], "agent": 5}\n')  # odd types
        f.write("\n")
    metrics.record_hit("/api/v1/fit", GPTBOT_UA, None, surface="api", now=NOW)
    c = metrics.monthly_counters("nz", "2026-09")
    assert c["api_calls_by_prefix"] == {"/api/v1/fit": 2}
    assert c["hits_by_surface"] == {"api": 2, "unknown": 1}


def test_counters_aggregate(data_dir):
    rh, rc = metrics.record_hit, metrics.record_clickout
    rh("/grants/OPP-1", GPTBOT_UA, None, surface="page", now=NOW)
    rh("/grants/OPP-2", HUMAN_UA, "https://chatgpt.com/", surface="page", now=NOW)
    rh("/api/v1/opportunities", "ClaudeBot/1.0", None, surface="api", now=NOW)
    rh("/api/v1/opportunities/OPP-1", "curl/8", None, surface="api", now=NOW)
    rh("/api/v1/stats.json", HUMAN_UA, "https://www.bing.com/", surface="api", now=NOW)
    rh("/api/v1", HUMAN_UA, None, surface="api", now=NOW)
    rh("/mcp", "Claude-User", None, surface="mcp", now=NOW)
    rh("mcp:search_grants", "Claude-User", None, surface="mcp", now=NOW)
    rh("mcp:search_grants", "Claude-User", None, surface="mcp", now=NOW)
    rh("mcp:get_grant", "Claude-User", None, surface="mcp", now=NOW)
    rc("OPP-1", "Rata Foundation", "https://claude.ai/", HUMAN_UA, now=NOW)
    rc("OPP-1", "Rata Foundation", None, HUMAN_UA, now=NOW)
    rc("OPP-2", "Lotteries", "https://www.google.com/", GPTBOT_UA, now=NOW)
    # A different month is not counted.
    rh("/grants/OPP-9", HUMAN_UA, None, surface="page", now=datetime(2026, 8, 31, tzinfo=timezone.utc))

    c = metrics.monthly_counters("nz", "2026-09")
    assert c["country"] == "nz" and c["month"] == "2026-09"
    assert c["hits_by_surface"] == {"page": 2, "api": 4, "mcp": 4}
    assert c["hits_by_agent"] == {"gptbot": 1, "human": 3, "claudebot": 5, "other-bot": 1}
    assert c["hits_by_agent_role"] == {"gptbot": {"training": 1},
                                       "claudebot": {"training": 1, "fetch": 4}}
    assert c["hits_by_referrer"] == {"direct": 8, "assistant": 1, "search": 1}
    assert c["clickouts_total"] == 3
    assert c["clickouts_by_referrer"] == {"assistant": 1, "direct": 1, "search": 1}
    assert c["clickouts_by_agent"] == {"human": 2, "gptbot": 1}
    assert c["top_clickout_grants"] == [["OPP-1", 2], ["OPP-2", 1]]
    assert c["top_clickout_funders"] == [["Rata Foundation", 2], ["Lotteries", 1]]
    assert c["api_calls_by_prefix"] == {"/api/v1/opportunities": 2, "/api/v1/stats": 1, "/api/v1": 1}
    assert c["mcp_calls_by_tool"] == {"search_grants": 2, "get_grant": 1}


def test_counters_default_country_and_month(data_dir):
    metrics.record_hit("/x", HUMAN_UA, None, surface="page")
    c = metrics.monthly_counters()
    assert c["country"] == "nz"
    assert c["hits_by_surface"] == {"page": 1}


def test_counters_reject_malformed_month(data_dir):
    with pytest.raises(ValueError):
        metrics.monthly_counters("nz", "../../platform/users")


# --- middleware ---

@pytest.fixture
def client(data_dir):
    return TestClient(server.app, raise_server_exceptions=False)


@pytest.mark.parametrize("path, surface", [
    ("/api/v1", "api"),
    ("/api/v1/opportunities", "api"),
    ("/mcp", "mcp"),
    ("/out/OPP-NZ-1", "out"),
])
def test_middleware_records_public_surface(client, data_dir, path, surface):
    client.get(path, headers={"user-agent": HUMAN_UA, "referer": "https://claude.ai/chat/x"})
    files = list((data_dir / "platform" / "metrics" / "nz").glob("*.jsonl"))
    [line] = [json.loads(l) for f in files for l in f.read_text().splitlines()]
    assert line["surface"] == surface
    assert line["path"] == path
    assert line["agent"] == "human"
    assert line["ref"] == "assistant"
    assert "ua" not in line
    assert "testclient" not in json.dumps(line)  # the client address is never stored


@pytest.mark.parametrize("path", ["/api/health", "/api/v10", "/outreach", "/mcpx", "/api/cases",
                                  "/api/internal/hit"])
def test_middleware_ignores_other_paths(client, data_dir, path):
    client.get(path)
    assert not (data_dir / "platform" / "metrics").exists()


def test_middleware_ignores_preflight(client, data_dir):
    client.options("/api/v1/opportunities", headers={
        "Origin": "http://localhost:3002", "Access-Control-Request-Method": "GET"})
    assert not (data_dir / "platform" / "metrics").exists()


def test_middleware_survives_metrics_failure(client, data_dir, monkeypatch):
    monkeypatch.setattr(metrics, "_append", lambda *a: (_ for _ in ()).throw(OSError("x")))
    r = client.get("/api/health")
    assert r.status_code == 200
    r = client.get("/api/v1/anything")
    assert r.status_code == 404  # route does not exist yet; request unaffected
