#!/usr/bin/env python3
"""Monthly country sweep — invoked by Railway cron.

Runs `run_country_sweep` for the current calendar month (UTC). Idempotent: if
the current month's pool already exists, exits without spending API budget.
This makes the cron safe to retry and safe to run alongside the startup hook
without doubling up.

Usage (Railway cron start command):
    python scripts/run_monthly_sweep.py
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


def main() -> int:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    for country in COUNTRIES:
        pool_path = os.path.join(platform_latest_path(country, month), "opportunities.json")
        if os.path.exists(pool_path):
            print(f"[monthly_sweep] Pool already exists for {country} {month} — skipping")
            continue
        print(f"[monthly_sweep] Running sweep for {country} {month}")
        run_country_sweep(country, month)
        print(f"[monthly_sweep] Sweep complete for {country} {month}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
