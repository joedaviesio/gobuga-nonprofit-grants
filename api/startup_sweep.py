"""Sweep dispatch — shared logic for kicking a country sweep in the background.

Two callers:
- `maybe_seed_pool()` — FastAPI startup hook. Seeds the current-month pool on
  boot if it's missing (Railway gitignores `platform/cycles/` so a fresh
  container starts empty).
- `trigger_sweep()` — backs `POST /api/admin/run-sweep`, which a Railway cron
  hits monthly via curl. The endpoint is auth'd with `SWEEP_SECRET`.

Both paths funnel through `_dispatch_sweep`, which is guarded by a module
lock so two callers can't accidentally start the same (country, month) sweep
twice.
"""

import hmac
import os
import threading

from api.opportunities import current_month
from api.tenant import platform_latest_path


SEED_COUNTRIES = ("nz",)
SWEEP_SECRET = os.getenv("SWEEP_SECRET", "")

_active_sweeps: set[tuple[str, str]] = set()
_dispatch_lock = threading.Lock()


def _pool_exists(country: str, month: str) -> bool:
    return os.path.exists(
        os.path.join(platform_latest_path(country, month), "opportunities.json")
    )


def _run_sweep(country: str, month: str) -> None:
    # Imported lazily so the server can boot even if a sweep dependency is
    # transiently broken — the failure is logged, not fatal.
    from orchestrator.sweep import run_country_sweep
    try:
        print(f"[sweep] Starting sweep for {country} {month}")
        run_country_sweep(country, month)
        print(f"[sweep] Sweep complete for {country} {month}")
    except Exception as exc:
        print(f"[sweep] Sweep failed for {country} {month}: {exc}")
    finally:
        with _dispatch_lock:
            _active_sweeps.discard((country, month))


def _dispatch_sweep(country: str, month: str, force: bool = False) -> str:
    """Returns 'dispatched', 'skipped-pool-exists', or 'skipped-already-running'."""
    with _dispatch_lock:
        if (country, month) in _active_sweeps:
            return "skipped-already-running"
        if not force and _pool_exists(country, month):
            return "skipped-pool-exists"
        _active_sweeps.add((country, month))
    threading.Thread(
        target=_run_sweep,
        args=(country, month),
        name=f"sweep-{country}-{month}",
        daemon=True,
    ).start()
    return "dispatched"


def maybe_seed_pool() -> None:
    """Startup hook: dispatch sweep for any seed country whose pool is missing."""
    if os.environ.get("STARTUP_SWEEP_DISABLED") == "1":
        print("[startup_sweep] Disabled via STARTUP_SWEEP_DISABLED=1")
        return
    month = current_month()
    for country in SEED_COUNTRIES:
        result = _dispatch_sweep(country, month)
        print(f"[startup_sweep] {country} {month}: {result}")


def trigger_sweep(
    country: str | None = None,
    month: str | None = None,
    force: bool = False,
) -> dict:
    """Public dispatcher for `/api/admin/run-sweep`."""
    month = month or current_month()
    countries = [country] if country else list(SEED_COUNTRIES)
    results = {c: _dispatch_sweep(c, month, force=force) for c in countries}
    return {"month": month, "results": results}


def verify_sweep_secret(token: str) -> bool:
    if not SWEEP_SECRET:
        return False
    return hmac.compare_digest(token, SWEEP_SECRET)
