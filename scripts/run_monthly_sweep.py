#!/usr/bin/env python3
"""Country sweep for the current month — run by the director, never by a cron.

Full sweeps run every two months per country and cost real money, so this
script refuses to run without `--confirm`: without it, it prints what it
would do and exits non-zero. (The file keeps its old name so existing
references still resolve.)

Runs `run_country_sweep` for the current calendar month (UTC). Idempotent: if
the current month's pool already exists, it skips without spending API budget.

Usage:
    python scripts/run_monthly_sweep.py            # show the plan, exit 1
    python scripts/run_monthly_sweep.py --confirm  # run the sweep
"""

import os
import sys
from datetime import datetime, timezone

from api.country_config import get_country
from api.tenant import platform_latest_path
from orchestrator.sweep import run_country_sweep


# Each deployment sweeps its own country (GOBUGA_COUNTRY env var), matching
# the behaviour of the /api/admin/run-sweep endpoint.
COUNTRIES = (get_country(),)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    pending = []
    for country in COUNTRIES:
        pool_path = os.path.join(platform_latest_path(country, month), "opportunities.json")
        if os.path.exists(pool_path):
            print(f"[sweep] Pool already exists for {country} {month} — skipping")
            continue
        pending.append(country)

    if "--confirm" not in argv:
        for country in pending:
            print(f"[sweep] Would run a full paid sweep for {country} {month} "
                  f"and publish the verified rows.")
        print("[sweep] Not run: a full sweep costs real money and needs the director's "
              "sign-off. Re-run with --confirm.")
        return 1

    for country in pending:
        print(f"[sweep] Running sweep for {country} {month}")
        run_country_sweep(country, month)
        print(f"[sweep] Sweep complete for {country} {month}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
