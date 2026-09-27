# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""In-memory, per-client rate limiting for the public machine surface.

A generalisation of `_check_public_rate_limit` in `api/server.py`: a sliding
one-minute window per client, held in process memory and reset on restart.
Two differences from that limiter:

- The client address is read from `X-Forwarded-For`, counting from the right
  by `TRUSTED_PROXY_HOPS` (default 1). The backend sits behind Railway's edge,
  and JSON twins also pass through the Next.js server, so `request.client.host`
  is a proxy. Each trusted proxy appends the address it saw, so the entry
  `hops` places from the right was written by our own outermost proxy and
  cannot be forged by a client adding entries on the left.
- Memory is bounded: at most `max_clients` buckets, least recently seen evicted.

The address is only ever a dictionary key in this process. It is never
logged, stored or returned (doctrine 9).

Requests carrying the `INTERNAL_HIT_SECRET` bearer (the frontend's own
server-side renders) are exempt.
"""

import hmac
import ipaddress
import math
import os
import time
from collections import OrderedDict, deque
from threading import Lock

from fastapi import Request
from starlette.exceptions import HTTPException

PUBLIC_LIMIT_PER_MIN = 120
MAX_CLIENTS = 10_000
WINDOW_SECONDS = 60.0


def trusted_proxy_hops() -> int:
    """`TRUSTED_PROXY_HOPS`, read per request. Bad or negative values give 1."""
    raw = os.environ.get("TRUSTED_PROXY_HOPS", "1").strip()
    return int(raw) if raw.isdigit() else 1


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def client_address(request: Request, hops: int | None = None) -> str:
    """The client's address as seen by our outermost trusted proxy.

    With `hops` trusted proxies, the client is the `hops`-th entry from the
    right of `X-Forwarded-For`. Falls back to the socket peer when hops is 0,
    when the header is missing or shorter than `hops` (the request did not
    come through every proxy), or when that entry is not an IP address.
    """
    hops = trusted_proxy_hops() if hops is None else hops
    peer = request.client.host if request.client else "unknown"
    if hops <= 0:
        return peer
    # Several X-Forwarded-For headers are one list, in order (RFC 7230 3.2.2).
    entries = [e.strip() for h in request.headers.getlist("x-forwarded-for") for e in h.split(",")]
    entries = [e for e in entries if e]
    if len(entries) < hops:
        return peer
    candidate = entries[-hops]
    return candidate if _valid_ip(candidate) else peer


def is_internal(request: Request) -> bool:
    """True when the request carries the valid INTERNAL_HIT_SECRET bearer."""
    secret = os.environ.get("INTERNAL_HIT_SECRET", "")
    if not secret:
        return False
    auth = request.headers.get("authorization", "")
    token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
    return hmac.compare_digest(token.encode("utf-8"), secret.encode("utf-8"))


class RateLimiter:
    """Sliding-window counter per key, with a bounded number of keys."""

    def __init__(self, limit: int, window: float = WINDOW_SECONDS, max_clients: int = MAX_CLIENTS,
                 clock=time.monotonic):
        self.limit = limit
        self.window = window
        self.max_clients = max_clients
        self.clock = clock
        self._buckets: OrderedDict[str, deque] = OrderedDict()
        self._lock = Lock()

    def hit(self, key: str) -> float | None:
        """Count one request. Returns None if allowed, else seconds to wait."""
        now = self.clock()
        cutoff = now - self.window
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = self._buckets[key] = deque()
                while len(self._buckets) > self.max_clients:
                    self._buckets.popitem(last=False)
            else:
                self._buckets.move_to_end(key)
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return max(bucket[0] + self.window - now, 0.0)
            bucket.append(now)
            return None

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()

    def __len__(self) -> int:
        return len(self._buckets)


public_limiter = RateLimiter(PUBLIC_LIMIT_PER_MIN)


def check_public_rate_limit(request: Request) -> None:
    """FastAPI dependency: 429 envelope with Retry-After when over the limit.

    Raised as an HTTPException so the public error handler in `api/errors.py`
    turns it into `{"error": {"code": "rate_limited", ...}}` and keeps the
    Retry-After header.
    """
    if is_internal(request):
        return
    wait = public_limiter.hit(client_address(request))
    if wait is not None:
        raise HTTPException(
            429,
            "Too many requests; slow down and retry after the Retry-After interval",
            headers={"Retry-After": str(max(1, math.ceil(wait)))},
        )
