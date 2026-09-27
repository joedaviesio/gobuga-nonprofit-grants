"""The MCP endpoint: POST /mcp, Streamable HTTP, stateless, three read-only tools.

In-process with TestClient against a fixture published dataset. Storage is
redirected to tmp_path, the request clock is pinned, and outbound sockets are
blocked so no test can reach a real model, mail or search API. No real MCP
client is exercised here: these tests speak the protocol by hand.
"""

import json
import re
import socket
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

# Importing the server imports modules that call load_dotenv(); import first
# so the fixture's delenv below wins.
from api import server  # noqa: E402
from api import mcp_server, metrics, public_context, public_stats, published, sources, tenant
from api.public_data import INTERNAL_FIELDS
from api.rate_limit import public_limiter

NOW = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)        # 14:00, 10 Oct in NZ
PUBLISH = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
BASE = "https://gobuga.test"
ACCEPT = "application/json, text/event-stream"
MARKER = "zz-echo-marker"   # put in bad input; must never come back

SECRET_ENV = ("ANTHROPIC_API_KEY", "RESEND_API_KEY", "TAVILY_API_KEY", "STRIPE_SECRET_KEY",
              "DIRECTOR_EMAIL", "INTERNAL_HIT_SECRET", "TRUSTED_PROXY_HOPS",
              "PUBLIC_RATE_LIMIT_PER_MIN")


def _no_network(*args, **kwargs):
    raise RuntimeError("network access is blocked in this test")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in SECRET_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setenv("STARTUP_SWEEP_DISABLED", "1")
    monkeypatch.setenv("APP_URL", BASE)
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setattr(sources, "must_appear_names", lambda country: [])
    monkeypatch.setattr(public_context, "now_utc", lambda: NOW)
    # Metrics lines land in the pinned month, so /api/v1/stats sees them.
    monkeypatch.setattr(metrics, "_now", lambda now: now or NOW)
    public_limiter.clear()
    public_stats.clear_cache()
    yield tmp_path
    public_limiter.clear()
    public_stats.clear_cache()


@pytest.fixture
def client():
    return TestClient(server.app, raise_server_exceptions=False)


LONG_SUMMARY = ("Youth sport equipment for clubs across the region. " * 12).strip()
LONG_ELIGIBILITY = ("Incorporated societies and charitable trusts based in Auckland. " * 8).strip()


def row(n, **kw):
    base = {
        "id": f"OPP-NZ-2026-10-{n:04d}",
        "country": "nz",
        "title": f"Programme {n}",
        "funder": f"Funder {n}",
        "deadline": "2026-11-30",
        "deadline_state": "dated",
        "amount_min": None,
        "amount_max": None,
        "currency": "NZD",
        "region": ["canterbury"],
        "tags": ["community"],
        "eligibility": "Incorporated societies",
        "summary": "A fund.",
        "source_url": f"https://funder{n}.example.nz/grants",
        "evidence_ids": [f"EV-{n:04d}"],
        "dedupe_key": f"funder-{n}|funder{n}.example.nz|{n}",
        "notes": "internal note",
        "unresolved_reason": "internal reason",
        "first_seen": f"2026-09-{10 + n:02d}T00:00:00+00:00",
        "last_seen": "2026-10-04T20:00:00+00:00",
        "verified_at": "2026-10-04T22:00:00+00:00",
        "verified_by": "verify-pass/test",
        "source_excerpt": "Applications close soon.",
    }
    base.update(kw)
    return base


def oid(n):
    return f"OPP-NZ-2026-10-{n:04d}"


def fixture_rows():
    return [
        row(1, funder="Foundation North", deadline="2026-10-25", region=["auckland"],
            tags=["community", "youth", "sport"], amount_min=5000, amount_max=20000,
            summary=LONG_SUMMARY, eligibility=LONG_ELIGIBILITY),
        row(2, funder="Sport NZ", deadline="2026-12-20", tags=["sport"],
            amount_min=50000, amount_max=200000),
        row(3, title="Māori arts fund", funder="ASB Community Trust", deadline="rolling",
            deadline_state="rolling-confirmed", region=["national"], tags=["arts", "community"],
            amount_min=1000),
        row(4, funder="Sport New Zealand", deadline="2026-09-01", deadline_state="closed",
            tags=["sport", "youth"]),
        row(5, funder="Stale Fund", deadline="rolling", deadline_state="rolling-confirmed",
            verified_at="2026-07-01T00:00:00+00:00", tags=["sport"]),
    ]


LIVE = [oid(1), oid(2), oid(3)]
CLOSED = oid(4)
STALE = oid(5)


def _notifier(*args):
    raise AssertionError("publish should not be blocked")


@pytest.fixture
def dataset():
    published.publish_pool("nz", "2026-10", fixture_rows(), now=PUBLISH, notifier=_notifier)


# --- Helpers ---------------------------------------------------------------------------

def post(client, message, headers=None):
    body = message if isinstance(message, (bytes, str)) else json.dumps(message)
    return client.post("/mcp", content=body, headers={
        "Content-Type": "application/json", "Accept": ACCEPT, **(headers or {})})


def rpc(client, method, params=None, msg_id=1, headers=None):
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return post(client, message, headers)


def call(client, tool, args=None):
    r = rpc(client, "tools/call", {"name": tool, "arguments": {} if args is None else args})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == 1 and "error" not in body, body
    return body["result"]


def ok(client, tool, args=None):
    result = call(client, tool, args)
    assert result["isError"] is False, result
    [block] = result["content"]
    assert block["type"] == "text"
    assert json.loads(block["text"]) == result["structuredContent"]
    return result["structuredContent"]


def tool_error(client, tool, args):
    result = call(client, tool, args)
    assert result["isError"] is True, result
    assert "structuredContent" not in result
    [block] = result["content"]
    assert block["type"] == "text"
    return block["text"]


def http_query(args):
    """Tool arguments as the equivalent /api/v1 query string parameters."""
    out = {}
    for k, v in args.items():
        out[k] = ",".join(v) if isinstance(v, list) else str(v)
    return out


def clipped(record):
    return mcp_server.listed(record)


def tools_by_name(client):
    r = rpc(client, "tools/list")
    assert r.status_code == 200
    return {t["name"]: t for t in r.json()["result"]["tools"]}


# --- Session -----------------------------------------------------------------------------

def test_full_session(client, dataset):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "test", "version": "0"}})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"
    assert r.headers["cache-control"] == "no-store"
    assert "mcp-session-id" not in r.headers
    body = r.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 1
    result = body["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "gobuga"
    assert result["serverInfo"]["title"] and result["serverInfo"]["version"] == server.app.version
    text = result["instructions"]
    for needle in ("canonical_url", "apply_url", "verified_at", "New Zealand"):
        assert needle in text

    header = {"MCP-Protocol-Version": "2025-06-18"}
    r = post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, header)
    assert r.status_code == 202 and r.content == b""

    r = rpc(client, "tools/list", msg_id="two", headers=header)
    assert r.status_code == 200 and r.json()["id"] == "two"
    assert [t["name"] for t in r.json()["result"]["tools"]] == ["search_grants", "get_grant", "fit_grants"]

    r = rpc(client, "tools/call", {"name": "get_grant", "arguments": {"id": oid(2)}},
            msg_id=3, headers=header)
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["id"] == oid(2)
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_ping(client):
    r = rpc(client, "ping", msg_id=7)
    assert r.json() == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_session_id_is_ignored_and_never_issued(client, dataset):
    r = rpc(client, "tools/list", headers={"Mcp-Session-Id": "abc-123"})
    assert r.status_code == 200 and "mcp-session-id" not in r.headers


def test_methods_work_without_initialize(client, dataset):
    assert ok(client, "search_grants")["total"] == 3


# --- Version negotiation ---------------------------------------------------------------------

@pytest.mark.parametrize("asked,answered", [
    ("2025-03-26", "2025-03-26"), ("2025-06-18", "2025-06-18"), ("2025-11-25", "2025-11-25"),
    ("2024-11-05", "2025-11-25"), ("2099-01-01", "2025-11-25"), ("nonsense", "2025-11-25"),
])
def test_version_negotiation(client, asked, answered):
    r = rpc(client, "initialize", {"protocolVersion": asked, "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "0"}})
    assert r.json()["result"]["protocolVersion"] == answered


@pytest.mark.parametrize("params", [{}, {"protocolVersion": 20250618}, {"protocolVersion": None}])
def test_initialize_without_a_version_string_is_invalid_params(client, params):
    r = rpc(client, "initialize", params)
    assert r.status_code == 200 and r.json()["error"]["code"] == -32602


@pytest.mark.parametrize("version", ["2025-03-26", "2025-06-18", "2025-11-25", " 2025-06-18 "])
def test_supported_protocol_header_is_accepted(client, version):
    assert rpc(client, "tools/list", headers={"MCP-Protocol-Version": version}).status_code == 200


@pytest.mark.parametrize("version", ["2024-11-05", "garbage", ""])
def test_unsupported_protocol_header_is_400(client, version):
    r = rpc(client, "tools/list", headers={"MCP-Protocol-Version": version})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_protocol_version"
    assert "2025-06-18" in r.json()["error"]["message"]
    r = post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"MCP-Protocol-Version": version})
    assert r.status_code == 400


def test_initialize_ignores_the_protocol_header(client):
    # Negotiation happens in the body; the header applies to later requests.
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18"},
            headers={"MCP-Protocol-Version": "1999-01-01"})
    assert r.status_code == 200 and r.json()["result"]["protocolVersion"] == "2025-06-18"


# --- search_grants ---------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    {}, {"tag": ["community"]}, {"tag": "sport,youth", "status": "all"}, {"region": "auckland"},
    {"region": "national", "sort": "amount"}, {"status": "all", "limit": 50},
    {"status": "closed"}, {"q": "YOUTH equipment"}, {"q": "maori"}, {"deadline": "rolling"},
    {"deadline": "30d"}, {"amount": "10k-50k"}, {"funder": "foundation-north"},
    {"sort": "recency", "offset": 1, "limit": 1}, {"offset": 99}, {"tag": [], "q": ""},
    {"region": None, "limit": "2"},
])
def test_search_matches_the_http_route(client, dataset, args):
    got = ok(client, "search_grants", args)
    query = {"limit": "10", **http_query({k: v for k, v in args.items() if v is not None})}
    expected = client.get("/api/v1/opportunities", params=query).json()
    expected["data"] = [clipped(r) for r in expected["data"]]
    assert got == expected


def test_search_defaults(client, dataset):
    body = ok(client, "search_grants")
    assert body["limit"] == 10 and body["offset"] == 0
    assert [r["id"] for r in body["data"]] == [oid(1), oid(2), oid(3)]
    assert body["meta"] == {"published_at": PUBLISH.isoformat(), "sweep_month": "2026-10"}


def test_list_results_keep_citation_fields(client, dataset):
    for tool, args in (("search_grants", {"status": "all"}), ("fit_grants", {"sector": ["sport"]})):
        for record in ok(client, tool, args)["data"]:
            assert record["canonical_url"] == f"{BASE}/grants/{record['id']}"
            assert record["apply_url"] == f"{BASE}/out/{record['id']}"
            assert record["attribution"] == {"source": "GoBuga", "url": record["canonical_url"],
                                             "retrieved_at": "2026-10-10",
                                             "licence": "attribution-required"}
            assert "verified_at" in record


def test_list_results_truncate_long_text_and_get_grant_does_not(client, dataset):
    [first] = [r for r in ok(client, "search_grants")["data"] if r["id"] == oid(1)]
    for field, full in (("summary", LONG_SUMMARY), ("eligibility", LONG_ELIGIBILITY)):
        text = first[field]
        assert text.endswith(mcp_server.TRUNCATION_MARKER)
        kept = text[:-len(mcp_server.TRUNCATION_MARKER)]
        assert len(kept) <= mcp_server.LIST_TEXT_CHARS and full.startswith(kept)
        assert len(kept) >= mcp_server.LIST_TEXT_CHARS - 40   # cut at a word, not far back
    fitted = next(r for r in ok(client, "fit_grants", {"sector": ["youth"]})["data"] if r["id"] == oid(1))
    assert fitted["summary"] == first["summary"]
    full = ok(client, "get_grant", {"id": oid(1)})
    assert full["summary"] == LONG_SUMMARY and full["eligibility"] == LONG_ELIGIBILITY
    # Short text is untouched.
    short = next(r for r in ok(client, "search_grants")["data"] if r["id"] == oid(2))
    assert short["summary"] == "A fund."


def test_clip_edges():
    n = mcp_server.LIST_TEXT_CHARS
    assert mcp_server._clip("x" * n) == "x" * n
    assert mcp_server._clip("x" * (n + 1)) == "x" * n + mcp_server.TRUNCATION_MARKER
    assert mcp_server._clip(None) is None
    assert mcp_server.listed({"id": "a"}) == {"id": "a", "summary": None, "eligibility": None}


@pytest.mark.parametrize("param,value", [
    ("tag", ["sport", "notatag"]), ("tag", "sport,notatag"), ("tag", [1]), ("region", "mars"),
    ("region", MARKER), ("region", 5), ("funder", "nobody"), ("funder", "../etc"),
    ("amount", "huge"), ("deadline", "7d"), ("status", "stale"), ("sort", "random"),
    ("offset", -1), ("offset", "abc"), ("offset", 1_000_001), ("limit", 0), ("limit", 51),
    ("limit", True), ("limit", 1.5), ("limit", [10]), ("q", "x" * 201), ("q", {"a": 1}),
])
def test_search_bad_argument_is_a_tool_error(client, dataset, param, value):
    text = tool_error(client, "search_grants", {param: value})
    assert f"'{param}'" in text and "Allowed values" in text
    assert MARKER not in text and "notatag" not in text and "nobody" not in text


def test_search_bad_value_lists_allowed_values(client, dataset):
    text = tool_error(client, "search_grants", {"deadline": "soon"})
    assert all(b in text for b in ("30d", "90d", "rolling", "dated"))
    text = tool_error(client, "search_grants", {"region": "mars"})
    assert "auckland" in text and "canterbury" in text
    text = tool_error(client, "search_grants", {"limit": 51})
    assert "an integer from 1 to 50" in text


def test_unknown_argument_is_a_tool_error_without_echo(client, dataset):
    text = tool_error(client, "search_grants", {MARKER: "x"})
    assert "Unknown parameter" in text and MARKER not in text
    assert "region" in text and "limit" in text


# --- get_grant --------------------------------------------------------------------------------

@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
def test_get_grant_matches_the_http_route(client, dataset, n):
    assert ok(client, "get_grant", {"id": oid(n)}) == client.get(f"/api/v1/opportunities/{oid(n)}").json()


def test_get_grant_closed_carries_related(client, dataset):
    rec = ok(client, "get_grant", {"id": CLOSED})
    assert rec["status"] == "closed" and rec["closed_at"]
    assert [r["id"] for r in rec["related"]] == [oid(1), oid(2)]


def test_get_grant_stale_carries_notice(client, dataset):
    rec = ok(client, "get_grant", {"id": STALE})
    assert rec["status"] == "stale" and rec["notice"]
    assert "related" not in ok(client, "get_grant", {"id": oid(1)})


@pytest.mark.parametrize("args", [
    {}, {"id": ""}, {"id": None}, {"id": 5}, {"id": "a" * 200}, {"id": "../" + MARKER},
    {"id": MARKER + " x"}, {"id": ["OPP-NZ-2026-10-0001"]},
])
def test_get_grant_bad_id_is_a_tool_error(client, dataset, args):
    text = tool_error(client, "get_grant", args)
    assert "'id'" in text and "Allowed values" in text and MARKER not in text


def test_get_grant_unknown_id_is_a_tool_error(client, dataset):
    text = tool_error(client, "get_grant", {"id": "OPP-NZ-2099-01-0001"})
    assert "'id'" in text and "search_grants" in text and "2099" not in text


# --- fit_grants --------------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    {}, {"sector": ["sport"]}, {"sector": "Youth,sport", "region": "Auckland"},
    {"sector": ["arts"], "status": "incorporated-society", "size": "under-50k", "need": "equipment"},
    {"region": "canterbury", "offset": 1, "limit": 1}, {"need": "project", "limit": 50},
])
def test_fit_matches_the_http_route(client, dataset, args):
    got = ok(client, "fit_grants", args)
    query = {"limit": "10", **http_query(args)}
    expected = client.get("/api/v1/fit", params=query).json()
    expected["data"] = [clipped(r) for r in expected["data"]]
    assert got == expected


def test_fit_carries_score_why_and_its_own_url(client, dataset):
    body = ok(client, "fit_grants", {"sector": ["sport"], "region": "auckland"})
    assert body["data"][0]["id"] == oid(1)
    assert all(isinstance(r["score"], float) and r["why"] for r in body["data"])
    assert body["canonical_query"] == "sector=sport&region=auckland"
    assert body["canonical_url"] == f"{BASE}/fit?sector=sport&region=auckland"
    assert body["json_url"] == f"{BASE}/fit.json?sector=sport&region=auckland"
    assert body["feed_url"] == f"{BASE}/fit/feed.xml?sector=sport&region=auckland"
    assert STALE not in json.dumps(body) and CLOSED not in json.dumps(body)


@pytest.mark.parametrize("param,value", [
    ("sector", ["sport", "space"]), ("sector", "sport,space"), ("sector", [MARKER]),
    ("region", "mars"), ("status", "alien"), ("size", "huge"), ("need", "yacht"),
    ("need", 3), ("offset", "x"), ("limit", 500),
])
def test_fit_bad_argument_is_a_tool_error(client, dataset, param, value):
    text = tool_error(client, "fit_grants", {param: value})
    assert f"'{param}'" in text and "Allowed values" in text
    assert MARKER not in text and "space" not in text and "yacht" not in text


# --- Empty state -----------------------------------------------------------------------------

def test_empty_dataset(client):
    assert set(tools_by_name(client)) == {"search_grants", "get_grant", "fit_grants"}
    search = ok(client, "search_grants", {"tag": ["sport"]})
    assert search == {"data": [], "total": 0, "offset": 0, "limit": 10,
                      "meta": {"published_at": None, "sweep_month": None}}
    fit = ok(client, "fit_grants", {"sector": ["sport"]})
    assert fit["data"] == [] and fit["total"] == 0 and fit["canonical_url"] == f"{BASE}/fit?sector=sport"
    text = tool_error(client, "get_grant", {"id": oid(1)})
    assert "'id'" in text


# --- Internal fields -------------------------------------------------------------------------

@pytest.mark.parametrize("tool,args", [
    ("search_grants", {"status": "all", "limit": 50}), ("fit_grants", {}),
    ("get_grant", {"id": oid(1)}), ("get_grant", {"id": CLOSED}), ("get_grant", {"id": STALE}),
])
def test_internal_fields_never_appear(client, dataset, tool, args):
    r = rpc(client, "tools/call", {"name": tool, "arguments": args})
    text = r.text
    for field in INTERNAL_FIELDS:
        assert f'"{field}"' not in text and f'\\"{field}\\"' not in text
    for leak in ("EV-000", "internal note", "internal reason", "funder-1|"):
        assert leak not in text


# --- Schemas ---------------------------------------------------------------------------------

SCHEMA_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum",
                   "minimum", "maximum", "maxLength", "pattern", "default", "description"}
PY_TYPES = {"object": dict, "array": list, "string": str, "integer": int}


def validate(instance, schema, where="$"):
    """The subset of JSON Schema these tools use. Returns a list of errors."""
    errors = []
    t = schema.get("type")
    if t and (not isinstance(instance, PY_TYPES[t]) or isinstance(instance, bool)):
        return [f"{where}: expected {t}"]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{where}: not in enum")
    if "minimum" in schema and instance < schema["minimum"]:
        errors.append(f"{where}: below minimum")
    if "maximum" in schema and instance > schema["maximum"]:
        errors.append(f"{where}: above maximum")
    if "maxLength" in schema and len(instance) > schema["maxLength"]:
        errors.append(f"{where}: too long")
    if "pattern" in schema and not re.search(schema["pattern"], instance):
        errors.append(f"{where}: pattern")
    if t == "object":
        props = schema.get("properties", {})
        errors += [f"{where}: missing {k}" for k in schema.get("required", []) if k not in instance]
        if schema.get("additionalProperties") is False:
            errors += [f"{where}: extra {k}" for k in instance if k not in props]
        for k, v in instance.items():
            if k in props:
                errors += validate(v, props[k], f"{where}.{k}")
    if t == "array" and "items" in schema:
        for i, v in enumerate(instance):
            errors += validate(v, schema["items"], f"{where}[{i}]")
    return errors


def check_schema(schema, *, described=False):
    """A structurally valid schema: known keywords with well-formed values."""
    assert set(schema) <= SCHEMA_KEYWORDS, set(schema) - SCHEMA_KEYWORDS
    assert schema["type"] in PY_TYPES
    if described:
        assert isinstance(schema.get("description"), str) and schema["description"]
    if "enum" in schema:
        enum = schema["enum"]
        assert isinstance(enum, list) and enum and len(set(enum)) == len(enum)
        assert all(isinstance(v, PY_TYPES[schema["type"]]) for v in enum)
    if "pattern" in schema:
        re.compile(schema["pattern"])
    for key in ("minimum", "maximum", "maxLength"):
        if key in schema:
            assert isinstance(schema[key], int) and not isinstance(schema[key], bool)
    if schema["type"] == "object":
        props = schema.get("properties", {})
        assert set(schema.get("required", [])) <= set(props)
        for prop in props.values():
            check_schema(prop, described=described)
    if "items" in schema:
        check_schema(schema["items"])
    if "default" in schema:
        assert validate(schema["default"], schema) == []


def test_tool_definitions_are_well_formed(client):
    tools = tools_by_name(client)
    for name, tool in tools.items():
        assert set(tool) == {"name", "title", "description", "inputSchema", "outputSchema", "annotations"}
        assert re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name)
        assert tool["title"] and len(tool["description"]) > 100
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        # Every property of an input schema is described; the root need not be.
        for prop in schema["properties"].values():
            check_schema(prop, described=True)
        check_schema({k: v for k, v in schema.items()})
        check_schema(tool["outputSchema"])
        assert tool["annotations"] == {"title": tool["title"], "readOnlyHint": True,
                                       "destructiveHint": False, "idempotentHint": True,
                                       "openWorldHint": True}
    assert tools["get_grant"]["inputSchema"]["required"] == ["id"]


@pytest.mark.parametrize("country", ["nz", "md"])
def test_enums_match_the_taxonomy(client, monkeypatch, country):
    monkeypatch.setenv("GOBUGA_COUNTRY", country)
    tax = client.get("/api/v1/taxonomy").json()
    tools = tools_by_name(client)
    search = tools["search_grants"]["inputSchema"]["properties"]
    fit = tools["fit_grants"]["inputSchema"]["properties"]
    tags = [t["slug"] for t in tax["tags"]]
    assert search["tag"]["items"]["enum"] == tags == fit["sector"]["items"]["enum"]
    assert search["region"]["enum"] == tax["regions"] == fit["region"]["enum"]
    assert search["amount"]["enum"] == [b["slug"] for b in tax["amount_bands"]]
    assert search["deadline"]["enum"] == [b["slug"] for b in tax["deadline_buckets"]]
    assert search["status"]["enum"] == tax["list_statuses"]
    assert search["sort"]["enum"] == [s["slug"] for s in tax["sorts"]]
    assert fit["status"]["enum"] == tax["entity_vocab"]
    assert fit["size"]["enum"] == [b["slug"] for b in tax["size_bands"]]
    assert fit["need"]["enum"] == tax["needs"]
    assert tax["currency"] in search["amount"]["description"]
    assert set(fit) == set(tax["fit_params"]) | {"offset", "limit"}


@pytest.mark.parametrize("with_data", [True, False])
def test_results_satisfy_their_output_schemas(client, request, with_data):
    if with_data:
        request.getfixturevalue("dataset")
    tools = tools_by_name(client)
    cases = [("search_grants", {"status": "all"}), ("search_grants", {"offset": 50}),
             ("fit_grants", {}), ("fit_grants", {"sector": ["sport"]})]
    if with_data:
        cases += [("get_grant", {"id": oid(n)}) for n in (1, 4, 5)]
    for tool, args in cases:
        payload = ok(client, tool, args)
        assert validate(payload, tools[tool]["outputSchema"]) == [], (tool, args)


def test_happy_arguments_satisfy_input_schemas(client):
    tools = tools_by_name(client)
    for tool, args in (("search_grants", {"tag": ["sport"], "region": "auckland", "limit": 5}),
                       ("fit_grants", {"sector": ["sport"], "status": "club", "size": "under-50k"}),
                       ("get_grant", {"id": oid(1)})):
        assert validate(args, tools[tool]["inputSchema"]) == []


# --- JSON-RPC and transport errors ------------------------------------------------------------

@pytest.mark.parametrize("body", [
    b"{not json", b"", b"\xff\xfe", b'{"jsonrpc": "2.0", "id": 1, "method": "ping", "x": NaN}',
    b'\xef\xbb\xbf{"jsonrpc":"2.0","id":1,"method":"ping"}',
])
def test_parse_error(client, body):
    r = post(client, body)
    assert r.status_code == 400
    assert r.json() == {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}


@pytest.mark.parametrize("message", [
    [{"jsonrpc": "2.0", "id": 1, "method": "ping"}], [], b'"hello"', 5, None,
    {"id": 1, "method": "ping"}, {"jsonrpc": "1.0", "id": 1, "method": "ping"},
    {"jsonrpc": "2.0", "id": 1, "method": 5}, {"jsonrpc": "2.0", "id": None, "method": "ping"},
    {"jsonrpc": "2.0", "id": True, "method": "ping"}, {"jsonrpc": "2.0", "id": 1.5, "method": "ping"},
    {"jsonrpc": "2.0", "id": {"a": 1}, "method": "ping"}, {"jsonrpc": "2.0", "id": 1},
    {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {"code": 1, "message": "x"}},
    {"jsonrpc": "2.0", "result": {}},
])
def test_invalid_request(client, message):
    r = post(client, message)
    assert r.status_code == 400
    body = r.json()
    assert body["id"] is None and body["error"]["code"] == -32600


def test_batch_is_rejected_by_name(client):
    r = post(client, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    assert "batch" in r.json()["error"]["message"]


@pytest.mark.parametrize("method", ["resources/list", "prompts/list", "tools/delete", "", "PING",
                                    "notifications/initialized"])
def test_method_not_found(client, method):
    r = rpc(client, method, msg_id="abc")
    assert r.status_code == 200
    assert r.json() == {"jsonrpc": "2.0", "id": "abc",
                        "error": {"code": -32601, "message": "Method not found"}}
    assert method == "" or method not in r.json()["error"]["message"]


@pytest.mark.parametrize("params", [
    [1, 2], "x", {"name": "delete_everything"}, {"name": MARKER}, {}, {"name": 5},
    {"name": "search_grants", "arguments": ["q", "x"]}, {"name": "search_grants", "arguments": "q=x"},
])
def test_invalid_params(client, dataset, params):
    r = rpc(client, "tools/call", params)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 1 and body["error"]["code"] == -32602
    assert MARKER not in r.text and "delete_everything" not in r.text


def test_arguments_may_be_omitted_or_null(client, dataset):
    for params in ({"name": "search_grants"}, {"name": "search_grants", "arguments": None}):
        r = rpc(client, "tools/call", params)
        assert r.json()["result"]["structuredContent"]["total"] == 3


def test_internal_error_is_32603_without_detail(client, dataset, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internals")
    monkeypatch.setattr(mcp_server, "search_rows", boom)
    r = rpc(client, "tools/call", {"name": "search_grants", "arguments": {}}, msg_id=9)
    assert r.status_code == 200
    assert r.json() == {"jsonrpc": "2.0", "id": 9, "error": {"code": -32603, "message": "Internal error"}}
    assert "secret" not in r.text


@pytest.mark.parametrize("message", [
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
    {"jsonrpc": "2.0", "method": "notifications/something-new"},
    {"jsonrpc": "2.0", "id": 4, "result": {}},
    {"jsonrpc": "2.0", "id": "x", "error": {"code": -1, "message": "no"}},
])
def test_notifications_and_responses_get_202(client, message):
    r = post(client, message)
    assert r.status_code == 202 and r.content == b""


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded",
                                          "application/jsonx", ""])
def test_wrong_content_type_is_415(client, content_type):
    r = client.post("/mcp", content=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
                    headers={"Content-Type": content_type, "Accept": ACCEPT})
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"


def test_content_type_parameters_are_allowed(client):
    r = post(client, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
             {"Content-Type": "Application/JSON; charset=utf-8"})
    assert r.status_code == 200


@pytest.mark.parametrize("accept,status", [
    ("text/html", 406), ("text/event-stream", 406), ("garbage", 406), (";;;", 406),
    ("application/json", 200), ("*/*", 200), ("application/*;q=0.9", 200), ("", 200),
])
def test_accept_header(client, accept, status):
    r = post(client, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Accept": accept})
    assert r.status_code == status
    if status == 406:
        assert r.json()["error"]["code"] == "not_acceptable"


def test_oversize_body_is_413(client):
    big = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping",
                      "params": {"pad": "x" * mcp_server.MAX_BODY_BYTES}})
    r = post(client, big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_oversize_streamed_body_is_413(client):
    def chunks():
        for _ in range(20):
            yield b"x" * 8192
    r = client.post("/mcp", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_body_at_the_limit_is_read(client):
    message = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": ""}}
    pad = mcp_server.MAX_BODY_BYTES - len(json.dumps(message))
    message["params"]["pad"] = "x" * pad
    body = json.dumps(message)
    assert len(body) == mcp_server.MAX_BODY_BYTES
    assert post(client, body).status_code == 200


@pytest.mark.parametrize("body", [
    b'{"a":' * 5000 + b"1" + b"}" * 5000, b"[" * 5000 + b"]" * 5000,
    b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_grant","arguments":{"id":{}}}}',
    b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_grants","arguments":{"limit":1e400}}}',
    b'{"jsonrpc":"2.0","id":99999999999999999999999,"method":"ping"}',
    '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_grants","arguments":{"q":"\\ud800"}}}'.encode(),
])
def test_malformed_bodies_never_500(client, dataset, body):
    r = post(client, body)
    assert r.status_code < 500
    assert r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("method", ["GET", "DELETE", "HEAD"])
def test_get_and_delete_are_405(client, method):
    r = client.request(method, "/mcp", headers={"Accept": "text/event-stream"})
    assert r.status_code == 405
    assert r.headers["allow"] == "POST"
    if method != "HEAD":
        assert r.json()["error"]["code"] == "method_not_allowed"


# --- CORS ------------------------------------------------------------------------------------

def test_mcp_preflight_from_any_origin(client):
    r = client.options("/mcp", headers={
        "Origin": "https://agent.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type, mcp-protocol-version"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "*"
    assert "POST" in r.headers["access-control-allow-methods"]
    assert "MCP-Protocol-Version" in r.headers["access-control-allow-headers"]
    assert "Content-Type" in r.headers["access-control-allow-headers"]
    assert "access-control-allow-credentials" not in r.headers


def test_mcp_post_from_any_origin(client, dataset):
    r = rpc(client, "tools/list", headers={"Origin": "https://agent.example"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"
    assert "Retry-After" in r.headers["access-control-expose-headers"]
    assert "access-control-allow-credentials" not in r.headers
    r = post(client, b"{", {"Origin": "https://agent.example"})
    assert r.status_code == 400 and r.headers["access-control-allow-origin"] == "*"


def test_api_and_workspace_cors_unchanged(client):
    r = client.options("/api/v1/opportunities", headers={
        "Origin": "https://agent.example", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 204 and "POST" not in r.headers["access-control-allow-methods"]
    assert "MCP-Protocol-Version" not in r.headers["access-control-allow-headers"]
    pre = client.options("/api/cases", headers={
        "Origin": "https://agent.example", "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 400 and "access-control-allow-origin" not in pre.headers
    for path in ("/mcpx", "/mcp/x"):
        pre = client.options(path, headers={
            "Origin": "https://agent.example", "Access-Control-Request-Method": "POST"})
        assert pre.headers.get("access-control-allow-origin") != "*"


# --- Metrics and rate limiting ---------------------------------------------------------------

def _lines(tmp_path):
    path = tmp_path / "platform" / "metrics" / "nz" / "2026-10.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_tool_calls_are_recorded_by_tool(client, dataset, isolated):
    rpc(client, "initialize", {"protocolVersion": "2025-06-18"})
    rpc(client, "tools/list")
    call(client, "search_grants", {"tag": ["sport"]})
    call(client, "get_grant", {"id": "OPP-NZ-2099-01-0001"})   # a tool error still counts
    call(client, "fit_grants", {})
    call(client, "fit_grants", {"region": "mars"})
    rpc(client, "tools/call", {"name": "evil_tool"})           # protocol error: no tool line
    lines = [l for l in _lines(isolated) if l.get("surface") == "mcp"]
    assert [l["path"] for l in lines if l["path"].startswith("mcp:")] == [
        "mcp:search_grants", "mcp:get_grant", "mcp:fit_grants", "mcp:fit_grants"]
    assert sum(1 for l in lines if l["path"] == "/mcp") == 7   # one transport line per POST
    assert all(l["kind"] == "hit" and "ua" not in l for l in lines)   # testclient is not a bot

    usage = client.get("/api/v1/stats").json()["usage"]
    assert usage["mcp_calls"] == {"total": 4, "by_tool": {
        "search_grants": 1, "get_grant": 1, "fit_grants": 2}}


def test_rate_limit_applies(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_RATE_LIMIT_PER_MIN", "3")
    for _ in range(3):
        assert rpc(client, "ping").status_code == 200
    r = rpc(client, "ping")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1


def test_rate_limit_is_shared_with_the_api(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_RATE_LIMIT_PER_MIN", "2")
    assert client.get("/api/v1/taxonomy").status_code == 200
    assert rpc(client, "ping").status_code == 200
    assert rpc(client, "ping").status_code == 429


# --- Discovery ---------------------------------------------------------------------------------

def test_index_points_to_mcp(client):
    body = client.get("/api/v1").json()
    assert body["mcp_url"] == f"{BASE}/mcp"
    [entry] = [e for e in body["endpoints"] if e["path"] == "/mcp"]
    assert entry["method"] == "POST"
    assert {"page": None, "public": "/mcp", "backend": "/mcp", "format": "mcp"} in body["url_scheme"]


def test_mcp_is_not_in_the_openapi_document(client):
    assert "/mcp" not in client.get("/api/v1/openapi.json").json()["paths"]
