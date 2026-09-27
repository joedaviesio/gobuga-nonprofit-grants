# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""HTTP plumbing for the public machine surface: caching, Atom, CORS.

- `cached_response` serialises a body once, stamps `ETag`, `Last-Modified`
  and `Cache-Control`, and answers `If-None-Match` / `If-Modified-Since`
  with 304.
- `atom_feed` builds an Atom 1.0 document with ElementTree, so every title
  and summary is escaped, and strips characters XML 1.0 cannot carry.
- `PublicCORSMiddleware` makes `/api/v1/*` readable from any origin without
  credentials, leaving the workspace's origin allow-list untouched.
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime

from fastapi import Request, Response

DATA_MAX_AGE = 600    # the dataset changes at publish and at local midnight
STATS_MAX_AGE = 60    # counters move with every request

ATOM_NS = "http://www.w3.org/2005/Atom"


# --- Conditional GET ------------------------------------------------------------

def _etag_matches(header: str, etag: str) -> bool:
    if header.strip() == "*":
        return True
    tags = [t.strip() for t in header.split(",")]
    return any((t[2:] if t.startswith("W/") else t) == etag for t in tags)


def _not_modified_since(header: str, last_modified: datetime) -> bool:
    try:
        since = parsedate_to_datetime(header)
    except (TypeError, ValueError, IndexError):
        return False
    if since is None:
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return last_modified.replace(microsecond=0) <= since


def cached_response(request: Request, content: bytes, *, media_type: str,
                    last_modified: datetime, max_age: int = DATA_MAX_AGE,
                    headers: dict | None = None) -> Response:
    """A 200 with validators, or a 304 when the client's copy is current.

    The ETag is a hash of the exact body, so it moves whenever anything the
    body depends on moves: the published dataset, today's date (statuses and
    "closes in" change at local midnight), APP_URL. `If-None-Match` wins over
    `If-Modified-Since` when both are sent (RFC 9110 13.2.2).
    """
    etag = '"' + hashlib.sha256(content).hexdigest()[:32] + '"'
    out = {
        "ETag": etag,
        "Last-Modified": format_datetime(last_modified.astimezone(timezone.utc), usegmt=True),
        "Cache-Control": f"public, max-age={max_age}",
        **(headers or {}),
    }
    inm = request.headers.get("if-none-match")
    ims = request.headers.get("if-modified-since")
    if (inm is not None and _etag_matches(inm, etag)) or (
            inm is None and ims and _not_modified_since(ims, last_modified)):
        return Response(status_code=304, headers=out)
    return Response(content=content, media_type=media_type, headers=out)


def json_bytes(body) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# --- Atom -----------------------------------------------------------------------

# Characters XML 1.0 forbids even when escaped (C0 controls except tab, LF, CR;
# lone surrogates; U+FFFE/U+FFFF). ElementTree would write them and produce a
# document no parser accepts.
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def xml_text(value) -> str:
    return _XML_INVALID.sub("", "" if value is None else str(value))


def _sub(parent, tag: str, text=None, **attrs):
    el = ET.SubElement(parent, f"{{{ATOM_NS}}}{tag}", {k: xml_text(v) for k, v in attrs.items()})
    if text is not None:
        el.text = xml_text(text)
    return el


def atom_feed(*, feed_id: str, title: str, updated: str, self_url: str, alternate_url: str,
              entries: list[dict]) -> bytes:
    """Atom 1.0. Each entry: id, title, updated, link, and optional summary."""
    ET.register_namespace("", ATOM_NS)
    feed = ET.Element(f"{{{ATOM_NS}}}feed")
    _sub(feed, "id", feed_id)
    _sub(feed, "title", title)
    _sub(feed, "updated", updated)
    _sub(_sub(feed, "author"), "name", "GoBuga")
    _sub(feed, "link", rel="self", href=self_url, type="application/atom+xml")
    _sub(feed, "link", rel="alternate", href=alternate_url, type="text/html")
    for entry in entries:
        el = _sub(feed, "entry")
        _sub(el, "id", entry["id"])
        _sub(el, "title", entry["title"] or "")
        _sub(el, "updated", entry["updated"])
        _sub(el, "link", rel="alternate", href=entry["link"], type="text/html")
        if entry.get("summary"):
            _sub(el, "summary", entry["summary"])
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)


def atom_time(value: datetime) -> str:
    """RFC 3339 in UTC, as Atom requires."""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- CORS -----------------------------------------------------------------------

PUBLIC_CORS_PREFIX = "/api/v1"
_CORS_ALLOW_HEADERS = "Accept, Accept-Language, Content-Type, If-None-Match, If-Modified-Since"
_CORS_EXPOSE_HEADERS = "ETag, Last-Modified, Retry-After, X-Robots-Tag"


def _is_public_cors_path(path: str) -> bool:
    return path == PUBLIC_CORS_PREFIX or path.startswith(PUBLIC_CORS_PREFIX + "/")


class PublicCORSMiddleware:
    """`Access-Control-Allow-Origin: *` for `/api/v1/*`, and nothing else.

    Must be added after (so it runs outside) the app's CORSMiddleware, whose
    origin allow-list would otherwise reject a third-party preflight with a
    400. Under `/api/v1` it answers preflights itself (GET and HEAD only, no
    credentials) and rewrites the response's CORS headers to the wildcard.
    Every other path goes straight through to the workspace's CORSMiddleware.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _is_public_cors_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        names = {k.lower() for k, _ in scope.get("headers", ())}
        if (scope.get("method") == "OPTIONS" and b"origin" in names
                and b"access-control-request-method" in names):
            await Response(status_code=204, headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": _CORS_ALLOW_HEADERS,
                "Access-Control-Max-Age": "86400",
            })(scope, receive, send)
            return

        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if not k.lower().startswith(b"access-control-")]
                headers += [(b"access-control-allow-origin", b"*"),
                            (b"access-control-expose-headers", _CORS_EXPOSE_HEADERS.encode())]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cors)
