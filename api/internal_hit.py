# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""POST /api/internal/hit — the frontend reports a page hit.

Crawlers fetch pages from Next.js, not from this backend, so the frontend
forwards each public page request here and it is recorded with
`surface="page"`.

Guarded by the `INTERNAL_HIT_SECRET` env var, sent as `Authorization: Bearer
<secret>` and compared in constant time. When the variable is unset or empty
the endpoint answers 404, as if it did not exist. The secret is read on every
request, not at import, so it can be rotated or set in tests.

Body: `{"path": "/grants/OPP-...", "user_agent": "...", "referrer": "..."}`.
The body is read with a hard byte cap and each field has its own length cap;
anything over is a 400 envelope.
"""

import hmac
import json
import os

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ValidationError

from api import metrics
from api.errors import PublicError

router = APIRouter()

MAX_PATH_CHARS = 2048
MAX_UA_CHARS = 1024
MAX_REFERRER_CHARS = 2048
# Comfortably above the three field caps plus JSON overhead.
MAX_BODY_BYTES = 8192


class HitRequest(BaseModel):
    path: str
    user_agent: str = ""
    referrer: str | None = None


def _check_secret(request: Request) -> None:
    secret = os.environ.get("INTERNAL_HIT_SECRET", "")
    if not secret:
        raise PublicError(404, "not_found", "Not found")
    auth = request.headers.get("authorization", "")
    token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
    if not hmac.compare_digest(token.encode("utf-8"), secret.encode("utf-8")):
        raise PublicError(401, "unauthorized", "Invalid or missing secret")


async def _read_capped_body(request: Request) -> bytes:
    """Read the body, stopping as soon as it passes MAX_BODY_BYTES."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise PublicError(400, "body_too_large", f"Body exceeds {MAX_BODY_BYTES} bytes")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise PublicError(400, "body_too_large", f"Body exceeds {MAX_BODY_BYTES} bytes")
    return bytes(body)


def _parse(body: bytes) -> HitRequest:
    try:
        hit = HitRequest.model_validate(json.loads(body))
    except (ValueError, ValidationError):
        # json.JSONDecodeError and UnicodeDecodeError are both ValueErrors.
        raise PublicError(400, "invalid_request", "Body must be JSON with a string 'path'")
    for field, value, cap in (
        ("path", hit.path, MAX_PATH_CHARS),
        ("user_agent", hit.user_agent, MAX_UA_CHARS),
        ("referrer", hit.referrer or "", MAX_REFERRER_CHARS),
    ):
        if len(value) > cap:
            raise PublicError(400, "field_too_long", f"'{field}' exceeds {cap} characters")
    if not hit.path.startswith("/"):
        raise PublicError(400, "invalid_path", "'path' must start with '/'")
    return hit


@router.post("/api/internal/hit")
async def internal_hit(request: Request):
    _check_secret(request)
    hit = _parse(await _read_capped_body(request))
    # File append off the event loop; record_hit never raises.
    await run_in_threadpool(
        metrics.record_hit, hit.path, hit.user_agent, hit.referrer, surface="page"
    )
    return {"ok": True}
