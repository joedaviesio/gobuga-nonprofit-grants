#!/usr/bin/env python3
"""Ping the backend's `/api/admin/run-sweep` endpoint.

Used by the Railway monthly-sweep cron service. Reads `API_URL` and
`SWEEP_SECRET` from env, POSTs an auth'd request, prints the JSON
response, and exits non-zero on any HTTP error so Railway flags the
deploy as failed.
"""

import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    api_url = os.environ["API_URL"].rstrip("/")
    secret = os.environ["SWEEP_SECRET"]
    req = urllib.request.Request(
        f"{api_url}/api/admin/run-sweep",
        method="POST",
        headers={"Authorization": f"Bearer {secret}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"[ping_sweep] {resp.status} {resp.read().decode()}")
            return 0
    except urllib.error.HTTPError as exc:
        print(f"[ping_sweep] HTTP {exc.code}: {exc.read().decode()}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
