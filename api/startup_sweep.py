"""Startup hook: seed the current-month opportunity pool if it's missing.

`platform/cycles/` is gitignored, so a fresh deploy (e.g. Railway) starts with
an empty pool — the search box and anon landing feed would show zero rows
until someone manually triggered a sweep. This hook detects that case at
FastAPI startup and kicks `run_country_sweep` on a daemon thread so the API
serves immediately while the pool builds (~10 min). Once the month has been
swept, the hook is a no-op on every subsequent boot.
"""

import os
import threading

from api.opportunities import current_month
from api.tenant import platform_latest_path


SEED_COUNTRIES = ("nz",)


def _pool_exists(country: str, month: str) -> bool:
    return os.path.exists(
        os.path.join(platform_latest_path(country, month), "opportunities.json")
    )


def _run_sweep(country: str, month: str) -> None:
    # Imported lazily so the server can boot even if a sweep dependency is
    # transiently broken — the failure is logged, not fatal.
    from orchestrator.sweep import run_country_sweep
    try:
        print(f"[startup_sweep] Starting background sweep for {country} {month}")
        run_country_sweep(country, month)
        print(f"[startup_sweep] Sweep complete for {country} {month}")
    except Exception as exc:
        print(f"[startup_sweep] Sweep failed for {country} {month}: {exc}")


def maybe_seed_pool() -> None:
    """For each seed country, dispatch a background sweep if the pool is missing."""
    if os.environ.get("STARTUP_SWEEP_DISABLED") == "1":
        print("[startup_sweep] Disabled via STARTUP_SWEEP_DISABLED=1")
        return
    month = current_month()
    for country in SEED_COUNTRIES:
        if _pool_exists(country, month):
            continue
        threading.Thread(
            target=_run_sweep,
            args=(country, month),
            name=f"startup-sweep-{country}-{month}",
            daemon=True,
        ).start()
        print(
            f"[startup_sweep] Pool missing for {country} {month}; "
            f"sweep dispatched to background thread"
        )
