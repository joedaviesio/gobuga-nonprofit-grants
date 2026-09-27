# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""Error envelope for the public surface.

Public routes (`/api/v1/*`, `/out/*`, `/mcp`, `/api/subscribe/*`,
`/api/internal/*`) return errors as

    {"error": {"code": "...", "message": "..."}}

Workspace routes keep FastAPI's `{"detail": ...}` bodies for 4xx, because the
existing frontend reads `detail`. Unhandled exceptions on any route get
`INTERNAL_ERROR_BODY` from the global handler in `api/server.py`.

Usage in a public router:

    from api.errors import PublicError
    raise PublicError(404, "not_found", "No grant with that ID")
"""

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/v1",
    "/out",
    "/mcp",
    "/api/subscribe",
    "/api/internal",
)

# `detail` stays alongside `error` because the existing frontend reads it.
INTERNAL_ERROR_BODY = {
    "error": {"code": "internal_error", "message": "Internal server error"},
    "detail": "Internal server error",
}

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    429: "rate_limited",
}


class PublicError(Exception):
    """Raise from a public route to return an error envelope."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def is_public_path(path: str) -> bool:
    """True for paths under a public prefix, on a segment boundary.

    `/out/x` and `/out` are public; `/outreach` is not.
    """
    return any(path == p or path.startswith(p + "/") for p in PUBLIC_PREFIXES)


async def _public_error_handler(request: Request, exc: PublicError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message))


async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Covers unknown paths and wrong methods under a public prefix (the router
    # raises these before any route runs). Workspace routes are untouched.
    if not is_public_path(request.url.path):
        return await http_exception_handler(request, exc)
    code = _STATUS_CODES.get(exc.status_code, f"http_{exc.status_code}")
    message = exc.detail if isinstance(exc.detail, str) else code
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(code, message),
        headers=getattr(exc, "headers", None),
    )


async def _validation_error_handler(request: Request, exc: RequestValidationError):
    if not is_public_path(request.url.path):
        return await request_validation_exception_handler(request, exc)
    # Name the offending field from our own schema; never echo the input.
    errors = exc.errors()
    where = ".".join(str(p) for p in errors[0].get("loc", ())) if errors else ""
    message = f"Invalid value for {where}" if where else "Invalid request"
    return JSONResponse(status_code=400, content=error_body("invalid_request", message))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PublicError, _public_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
