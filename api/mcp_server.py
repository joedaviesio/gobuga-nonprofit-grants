# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""The Model Context Protocol endpoint: `POST /mcp`, keyless and read-only.

A thin adapter over `/api/v1`: three tools (`search_grants`, `get_grant`,
`fit_grants`) that call the same functions the HTTP routes call, so a tool
can never return a fact the API would not.

Transport: the Streamable HTTP transport, hand-written, in its stateless
subset (no MCP library).

- `POST /mcp` takes one JSON-RPC 2.0 message as `application/json`. A request
  gets its single JSON-RPC response as `application/json` (the specification
  allows this in place of an event stream); a notification or a response
  gets `202 Accepted` with no body.
- `GET /mcp` and `DELETE /mcp` are 405: no server-initiated stream, and no
  sessions to end. No `Mcp-Session-Id` is issued; one sent is ignored.
- Batches (a JSON array) are rejected with -32600: the current protocol
  revisions do not allow them.
- Transport problems (content type, `Accept`, body size, protocol version
  header) are HTTP errors with the `api.errors` envelope. A body that is not
  JSON, or not a single well-formed message, is an HTTP 400 carrying a
  JSON-RPC error (-32700 or -32600) with a null id.

Origin: the specification asks servers to check `Origin` against DNS
rebinding, an attack on servers bound to a private address (typically
localhost) that a hostile page can reach through the victim's browser. This
server is public, keyless and read-only and holds no cookies or credentials,
so a cross-origin caller can do nothing here that it could not do with curl.
Every origin is therefore valid and none is rejected; browser clients are let
in by `api.public_http.PublicCORSMiddleware`.

Tool results carry the payload twice: as `structuredContent` and as one text
block holding the same JSON, for clients of either protocol generation. A bad
argument value is a tool execution error (`isError: true`) naming the
parameter and listing the allowed values, so the model can correct itself;
an unknown tool or non-object arguments are JSON-RPC -32602. No error text
echoes the caller's input.
"""

import json
import traceback

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from api import metrics
from api.errors import PublicError
from api.fit import ENTITY_VOCAB, NEEDS, PARAM_ORDER, SIZE_BAND_REVENUE, SIZE_BANDS
from api.public_context import (
    JSON, MAX_OFFSET, MAX_Q_CHARS, Ctx, integer, list_body, request_context,
)
from api.public_data import AMOUNT_BANDS, DEADLINE_BUCKETS, ID_RE, LIST_STATUSES, SORTS
from api.public_http import json_bytes
from api.public_v1 import fit_body, fit_result, grant_record, search_rows
from api.rate_limit import check_public_rate_limit

router = APIRouter(dependencies=[Depends(check_public_rate_limit)])

MCP_PATH = "/mcp"

# Newest first. A client asking for one of these gets it; any other request
# gets the newest. 2025-11-25 adds nothing a stateless, tools-only server
# returning JSON must implement.
PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-11-25", "2025-06-18", "2025-03-26")

MAX_BODY_BYTES = 64 * 1024

# Tool results go into a model's context, so pages are smaller than the API's.
DEFAULT_LIMIT = 10
MAX_LIMIT = 50

# Long free text is shortened in list results; get_grant is always complete.
LIST_TEXT_CHARS = 300
LIST_TEXT_FIELDS = ("summary", "eligibility")
TRUNCATION_MARKER = " … [truncated; call get_grant for the full text]"

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_NO_STORE = {"Cache-Control": "no-store"}


class RpcError(Exception):
    """A JSON-RPC error for the message being handled."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(Exception):
    """A tool execution error: returned as a result with `isError: true`."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# --- Tool definitions -------------------------------------------------------------

_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    # True, the protocol's default. The tools read only GoBuga's own published
    # dataset and make no outbound call, but what they return (titles,
    # summaries, eligibility, excerpts) is text taken from third-party funder
    # websites. Clients use this hint to decide how far to trust a result;
    # claiming a closed world would invite more trust than that text earns.
    "openWorldHint": True,
}

_PAGING_HINTS = {
    "offset": f"an integer from 0 to {MAX_OFFSET}",
    "limit": f"an integer from 1 to {MAX_LIMIT}",
}

# Allowed-value text for parameters without an enum, used in tool errors.
_HINTS = {
    "q": f"text of at most {MAX_Q_CHARS} characters",
    "funder": "a funder slug, such as those in the funder.slug field of any record",
    "id": "a grant ID such as OPP-NZ-2026-08-0209, as returned by search_grants or fit_grants",
    **_PAGING_HINTS,
}


def _enum(values) -> dict:
    """An enum keyword, left out when the vocabulary is empty (an empty enum
    admits nothing)."""
    values = list(values)
    return {"enum": values} if values else {}


def _paging_props() -> dict:
    return {
        "offset": {"type": "integer", "minimum": 0, "maximum": MAX_OFFSET, "default": 0,
                   "description": "Results to skip, for the next page. Offsets never expire."},
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT,
                  "description": f"Results to return, 1 to {MAX_LIMIT}; default {DEFAULT_LIMIT}."},
    }


def _search_schema(ctx: Ctx) -> dict:
    cfg = ctx.cfg
    return {
        "type": "object",
        "properties": {
            "q": {"type": "string", "maxLength": MAX_Q_CHARS,
                  "description": "Free-text search: every word must appear in the title, funder, "
                                 "summary, eligibility or tags. Case and accents are ignored."},
            "tag": {"type": "array", "items": {"type": "string", **_enum(cfg.tags)},
                    "description": "Sector tag slugs; a grant must carry every one given."},
            "region": {"type": "string", **_enum(cfg.regions),
                       "description": "Region slug. Grants open nationally match every region; "
                                      "'national' returns national grants only."},
            "funder": {"type": "string",
                       "description": "Funder slug (the funder.slug field of a record), to list "
                                      "one funder's grants."},
            "amount": {"type": "string", **_enum(b[0] for b in AMOUNT_BANDS),
                       "description": f"Amount band in {cfg.currency}; matches grants whose "
                                      "stated range overlaps it."},
            "deadline": {"type": "string", **_enum(DEADLINE_BUCKETS),
                         "description": "Deadline bucket. "
                                        + "; ".join(f"{k}: {v}" for k, v in DEADLINE_BUCKETS.items())},
            "status": {"type": "string", **_enum(LIST_STATUSES), "default": "live",
                       "description": "live (default), closed, or all (live and closed)."},
            "sort": {"type": "string", **_enum(SORTS), "default": "deadline",
                     "description": "Order. " + "; ".join(f"{k}: {v}" for k, v in SORTS.items())},
            **_paging_props(),
        },
        "additionalProperties": False,
    }


def _grant_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string", "pattern": ID_RE.pattern,
                   "description": "The grant's id, as returned by search_grants or fit_grants, "
                                  "for example OPP-NZ-2026-08-0209."},
        },
        "required": ["id"],
        "additionalProperties": False,
    }


def _fit_schema(ctx: Ctx) -> dict:
    cfg = ctx.cfg
    sizes = "; ".join(
        f"{s}: {lo:,} to {hi:,}" if hi is not None else f"{s}: {lo:,} or more"
        for s, (lo, hi) in ((s, SIZE_BAND_REVENUE[s]) for s in SIZE_BANDS))
    return {
        "type": "object",
        "properties": {
            "sector": {"type": "array", "items": {"type": "string", **_enum(cfg.tags)},
                       "description": "What the organisation does, as sector tag slugs."},
            "region": {"type": "string", **_enum(cfg.regions),
                       "description": "Region the organisation works in. National grants "
                                      "count for every region."},
            "status": {"type": "string", **_enum(ENTITY_VOCAB),
                       "description": "The organisation's legal form or kind."},
            "size": {"type": "string", **_enum(SIZE_BANDS),
                     "description": f"Annual revenue band, in {cfg.currency}. {sizes}."},
            "need": {"type": "string", **_enum(NEEDS),
                     "description": "What the money is for."},
            **_paging_props(),
        },
        "additionalProperties": False,
    }


# Loose output schemas: only what every payload is sure to satisfy, so a
# strict client never rejects a true result.
_LIST_OUTPUT = {
    "type": "object",
    "properties": {
        "data": {"type": "array", "items": {"type": "object"}},
        "total": {"type": "integer"},
        "offset": {"type": "integer"},
        "limit": {"type": "integer"},
        "meta": {"type": "object"},
    },
    "required": ["data", "total", "offset", "limit", "meta"],
}
_FIT_OUTPUT = {
    **_LIST_OUTPUT,
    "properties": {**_LIST_OUTPUT["properties"],
                   "canonical_query": {"type": "string"}, "canonical_url": {"type": "string"},
                   "json_url": {"type": "string"}, "feed_url": {"type": "string"}},
    "required": [*_LIST_OUTPUT["required"], "canonical_url"],
}
_GRANT_OUTPUT = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "canonical_url": {"type": "string"},
                   "apply_url": {"type": "string"}},
    "required": ["id", "canonical_url", "apply_url"],
}


def _tool(name: str, title: str, description: str, input_schema: dict, output_schema: dict) -> dict:
    return {"name": name, "title": title, "description": description,
            "inputSchema": input_schema, "outputSchema": output_schema,
            "annotations": {"title": title, **_ANNOTATIONS}}


def tool_definitions(ctx: Ctx) -> list[dict]:
    """The three tools, with enums from this deployment's taxonomy."""
    where = ctx.cfg.country_label
    return [
        _tool(
            "search_grants", "Search grants",
            f"Search GoBuga's verified, published grant listings for {where} by keywords and "
            "filters (tag, region, funder, amount band, deadline window). Use it to find grants "
            "on a topic or to browse what is open. Returns {data, total, offset, limit, meta}; "
            "each item is a grant record with title, funder, deadline (with verified_at and the "
            "source excerpt), amount, region, tags, eligibility, summary, canonical_url (cite it) "
            "and apply_url (give it to the person). Live grants only unless status says "
            f"otherwise. Summary and eligibility over {LIST_TEXT_CHARS} characters are shortened; "
            "call get_grant for the full record.",
            _search_schema(ctx), _LIST_OUTPUT),
        _tool(
            "get_grant", "Get one grant",
            "Fetch one grant's complete record by id (from search_grants or fit_grants). Use it "
            "before relying on details such as eligibility or the deadline, or when the person "
            "asks about a specific grant. Returns every public field with provenance per fact. A "
            "closed grant carries related: live grants with the same tags. A grant that could "
            "not be re-verified carries notice; relay it.",
            _grant_schema(), _GRANT_OUTPUT),
        _tool(
            "fit_grants", "Rank grants for an organisation",
            f"Rank live grants in {where} for an organisation described by sector, region, legal "
            "status, annual revenue size and what the money is for. Use it when the person "
            "describes their organisation and asks what they could apply for. Deterministic: "
            "each item is a grant record plus score and why (the reasons it fits), best first. "
            "Also returns canonical_url, a shareable GoBuga page for this exact ranking, to cite "
            f"or give the person. Summary and eligibility over {LIST_TEXT_CHARS} characters are "
            "shortened; call get_grant for the full record.",
            _fit_schema(ctx), _FIT_OUTPUT),
    ]


def instructions(ctx: Ctx) -> str:
    return (
        f"GoBuga is a public, verified index of grant funding in {ctx.cfg.country_label}: "
        "search_grants finds grants, fit_grants ranks them for an organisation and get_grant "
        "returns one complete record. Every record carries a canonical_url to cite when you "
        "mention it and an apply_url to give the person, which leads to the funder's own page. "
        "Facts carry verified_at, when GoBuga last checked them against the funder's page; "
        "say when a deadline was verified and suggest the person confirm it with the funder."
    )


# --- Tool arguments ---------------------------------------------------------------

def _bad(param: str, schema: dict) -> ToolError:
    """The same message `/api/v1` gives for a bad value, allowed values from
    the tool's own schema."""
    prop = schema["properties"][param]
    allowed = (prop.get("enum") or prop.get("items", {}).get("enum")
               or [_HINTS.get(param, "a string")])
    return ToolError(f"Invalid value for '{param}'. Allowed values: {', '.join(allowed)}")


def _check_names(args: dict, schema: dict, tool: str) -> None:
    if any(k not in schema["properties"] for k in args):
        raise ToolError(f"Unknown parameter for {tool}. Parameters: "
                        + ", ".join(schema["properties"]))


def _text(args: dict, param: str, schema: dict) -> str | None:
    """A string argument as the HTTP route would receive it. A list of
    strings (for tag and sector) is joined with commas; null is absent."""
    value = args.get(param)
    if value is None or isinstance(value, str):
        return value
    if (schema["properties"][param].get("type") == "array" and isinstance(value, list)
            and all(isinstance(v, str) for v in value)):
        return ",".join(value)
    raise _bad(param, schema)


def _int(args: dict, param: str, default: int, lo: int, hi: int) -> int:
    """An integer argument, validated as `/api/v1` validates the query string.
    Accepts an integer, an integral float or a string of digits."""
    value = args.get(param)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, (int, str, type(None))):
        raise ToolError(f"Invalid value for '{param}'. Allowed values: {_PAGING_HINTS[param]}")
    return integer(param, None if value is None else str(value), default, lo, hi)


def _paging(args: dict) -> tuple[int, int]:
    return (_int(args, "offset", 0, 0, MAX_OFFSET),
            _int(args, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT))


def _clip(text):
    """Shorten long text at a word boundary and mark it."""
    if not isinstance(text, str) or len(text) <= LIST_TEXT_CHARS:
        return text
    cut = text[:LIST_TEXT_CHARS]
    space = cut.rfind(" ")
    if space >= LIST_TEXT_CHARS - 40:
        cut = cut[:space]
    return cut.rstrip() + TRUNCATION_MARKER


def listed(record: dict) -> dict:
    """A record as list results show it: summary and eligibility clipped."""
    return {**record, **{f: _clip(record.get(f)) for f in LIST_TEXT_FIELDS}}


# --- Tools ------------------------------------------------------------------------

def search_grants(ctx: Ctx, args: dict) -> dict:
    schema = _search_schema(ctx)
    _check_names(args, schema, "search_grants")
    raw = {k: _text(args, k, schema)
           for k in ("q", "tag", "region", "funder", "amount", "deadline", "status", "sort")}
    rows = search_rows(ctx, **raw)
    off, lim = _paging(args)
    body = list_body(rows, off, lim, ctx)
    body["data"] = [listed(ctx.record(r)) for r in body["data"]]
    return body


def get_grant(ctx: Ctx, args: dict) -> dict:
    schema = _grant_schema()
    _check_names(args, schema, "get_grant")
    opp_id = _text(args, "id", schema)
    if not opp_id or not ID_RE.match(opp_id):
        raise _bad("id", schema)
    record = grant_record(ctx, opp_id)
    if record is None:
        raise ToolError("No published grant has that 'id'. Allowed values: "
                        "an id returned by search_grants or fit_grants")
    return record


def fit_grants(ctx: Ctx, args: dict) -> dict:
    schema = _fit_schema(ctx)
    _check_names(args, schema, "fit_grants")
    raw = {k: _text(args, k, schema) for k in PARAM_ORDER}
    result = fit_result(ctx, **raw)
    off, lim = _paging(args)
    body = fit_body(ctx, result, off, lim)
    body["data"] = [listed(item) for item in body["data"]]
    return body


TOOLS = {"search_grants": search_grants, "get_grant": get_grant, "fit_grants": fit_grants}


def _tool_result(payload: dict) -> dict:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return {"content": [{"type": "text", "text": text}], "structuredContent": payload,
            "isError": False}


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


# --- JSON-RPC methods ---------------------------------------------------------------

def _initialize(params: dict, caller: dict) -> dict:
    requested = params.get("protocolVersion")
    if not isinstance(requested, str):
        raise RpcError(INVALID_PARAMS, "Invalid params: protocolVersion must be a string")
    ctx = request_context()
    return {
        "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "gobuga", "title": "GoBuga grants", "version": caller["version"]},
        "instructions": instructions(ctx),
    }


def _ping(params: dict, caller: dict) -> dict:
    return {}


def _tools_list(params: dict, caller: dict) -> dict:
    # One page holds every tool, so a cursor is ignored and none is returned.
    return {"tools": tool_definitions(request_context())}


def _tools_call(params: dict, caller: dict) -> dict:
    tool = TOOLS.get(params.get("name")) if isinstance(params.get("name"), str) else None
    if tool is None:
        raise RpcError(INVALID_PARAMS, "Unknown tool. Available tools: " + ", ".join(TOOLS))
    args = params.get("arguments")
    args = {} if args is None else args
    if not isinstance(args, dict):
        raise RpcError(INVALID_PARAMS, "Invalid params: arguments must be an object")
    ctx = request_context()
    # The recorded name is our own function's, never the caller's string.
    metrics.record_hit(f"mcp:{tool.__name__}", caller["user_agent"], caller["referrer"],
                       surface="mcp", country=ctx.country)
    try:
        return _tool_result(tool(ctx, args))
    except ToolError as exc:
        return _tool_error(exc.message)
    except PublicError as exc:  # a 400 from the shared /api/v1 validators
        return _tool_error(exc.message)


METHODS = {
    "initialize": _initialize,
    "ping": _ping,
    "tools/list": _tools_list,
    "tools/call": _tools_call,
}


# --- Messages ------------------------------------------------------------------------

def _reject_constant(name):
    raise ValueError(f"{name} is not JSON")


def parse_message(body: bytes):
    """The decoded JSON value, or RpcError(PARSE_ERROR)."""
    try:
        return json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise RpcError(PARSE_ERROR, "Parse error") from None


def _valid_id(value) -> bool:
    # MCP request ids are strings or integers, never null.
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def classify(message) -> str:
    """"request", "notification" or "response"; RpcError(INVALID_REQUEST)
    for anything else, including a batch."""
    if isinstance(message, list):
        raise RpcError(INVALID_REQUEST, "Invalid Request: batches are not supported")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise RpcError(INVALID_REQUEST, "Invalid Request")
    if "method" in message:
        if not isinstance(message["method"], str):
            raise RpcError(INVALID_REQUEST, "Invalid Request: method must be a string")
        if "id" not in message:
            return "notification"
        if not _valid_id(message["id"]):
            raise RpcError(INVALID_REQUEST, "Invalid Request: id must be a string or an integer")
        return "request"
    if _valid_id(message.get("id")) and ("result" in message) != ("error" in message):
        return "response"
    raise RpcError(INVALID_REQUEST, "Invalid Request")


def _error_body(msg_id, exc: RpcError) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": exc.code, "message": exc.message}}


def dispatch(message: dict, caller: dict) -> dict:
    """The JSON-RPC response to one request. Never raises."""
    msg_id = message["id"]
    try:
        handler = METHODS.get(message["method"])
        if handler is None:
            raise RpcError(METHOD_NOT_FOUND, "Method not found")
        params = message.get("params")
        params = {} if params is None else params
        if not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "Invalid params: params must be an object")
        return {"jsonrpc": "2.0", "id": msg_id, "result": handler(params, caller)}
    except RpcError as exc:
        return _error_body(msg_id, exc)
    except Exception as exc:  # noqa: BLE001 — logged; the client gets -32603 only
        print(f"[mcp] internal error: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        return _error_body(msg_id, RpcError(INTERNAL_ERROR, "Internal error"))


# --- HTTP ------------------------------------------------------------------------------

def _media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _check_content_type(request: Request) -> None:
    if _media_type(request.headers.get("content-type", "")) != JSON:
        raise PublicError(415, "unsupported_media_type", "Content-Type must be application/json")


def _check_accept(request: Request) -> None:
    """Clients are asked to accept both application/json and
    text/event-stream; this server only ever answers JSON, so it needs JSON
    (or a wildcard) to be acceptable. A missing header accepts anything."""
    accept = request.headers.get("accept")
    if accept is None or not accept.strip():
        return
    ranges = {_media_type(part) for part in accept.split(",")}
    if not ranges & {JSON, "application/*", "*/*"}:
        raise PublicError(406, "not_acceptable", "Accept must include application/json")


def _check_protocol_header(request: Request) -> None:
    # Absent means 2025-03-26, which is supported.
    value = request.headers.get("mcp-protocol-version")
    if value is not None and value.strip() not in PROTOCOL_VERSIONS:
        raise PublicError(400, "unsupported_protocol_version",
                          "Unsupported MCP-Protocol-Version. Supported: " + ", ".join(PROTOCOL_VERSIONS))


async def _read_body(request: Request) -> bytes:
    too_large = PublicError(413, "payload_too_large",
                            f"Request body must be at most {MAX_BODY_BYTES} bytes")
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise too_large
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


def _json(body: dict, status_code: int = 200) -> Response:
    return Response(content=json_bytes(body), status_code=status_code, media_type=JSON,
                    headers=_NO_STORE)


@router.post(MCP_PATH, include_in_schema=False)
async def mcp_post(request: Request):
    _check_content_type(request)
    _check_accept(request)
    body = await _read_body(request)
    try:
        message = parse_message(body)
        kind = classify(message)
    except RpcError as exc:
        # The input was not a message this server can take: an HTTP 400
        # carrying a JSON-RPC error with no id, as the transport allows.
        return _json(_error_body(None, exc), status_code=400)
    if not (kind == "request" and message["method"] == "initialize"):
        _check_protocol_header(request)
    if kind != "request":
        return Response(status_code=202, headers=_NO_STORE)
    caller = {
        "user_agent": request.headers.get("user-agent", ""),
        "referrer": request.headers.get("referer"),
        "version": request.app.version,
    }
    return _json(await run_in_threadpool(dispatch, message, caller))


@router.api_route(MCP_PATH, methods=["GET", "DELETE"], include_in_schema=False)
def mcp_not_allowed():
    """No server-initiated stream (GET) and no sessions to end (DELETE)."""
    raise HTTPException(405, "This MCP server is stateless: send JSON-RPC messages with POST",
                        headers={"Allow": "POST"})
