# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""ASGI middleware that counts requests to the public machine surface.

Requests under `/api/v1`, `/mcp` and `/out` are recorded with
`api.metrics.record_hit`. Everything else, including every workspace route,
passes straight through untouched.

Plain ASGI rather than `BaseHTTPMiddleware`, so streaming responses (the MCP
transport) are not buffered. The body is never read: only the path, the
User-Agent and the Referer headers. The hit is written after the response has
been sent, so the client never waits on the append.
"""

from fastapi.concurrency import run_in_threadpool

from api import metrics

# (prefix, surface). Matched on a segment boundary: `/out/x` counts,
# `/outreach` does not.
_SURFACE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/api/v1", "api"),
    ("/mcp", "mcp"),
    ("/out", "out"),
)


def surface_for(path: str) -> str | None:
    for prefix, surface in _SURFACE_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return surface
    return None


def _header(scope, name: bytes) -> str:
    for key, value in scope.get("headers", ()):
        if key == name:
            return value.decode("latin-1")
    return ""


class HitLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        surface = surface_for(path)
        if surface is None:
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            # File append off the event loop; record_hit never raises.
            await run_in_threadpool(
                metrics.record_hit,
                path,
                _header(scope, b"user-agent"),
                _header(scope, b"referer"),
                surface=surface,
            )
