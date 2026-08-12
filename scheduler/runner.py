#!/usr/bin/env python3
"""
Per-org cycle scheduler — run via cron.

Usage:
    python -m scheduler.runner

Checks all paid orgs, triggers a daily cycle for any that haven't run today.
Stagger by org. Max 3 concurrent cycles.
"""

import tirith  # noqa: F401  — must come before anthropic; routes calls through local tirith proxy

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from api.country_config import get_country_config
from api.tenant import PLATFORM_DIR, org_cycles_dir
from api.auth import _load_orgs
from api.limits import get_model_overrides


MAX_CONCURRENT = 3


def _local_tz() -> ZoneInfo:
    """Return the deployment's local timezone from country config."""
    return ZoneInfo(get_country_config().timezone)


def _needs_cycle_today(org_id: str) -> bool:
    """Check if this org has already had a cycle today."""
    today = datetime.now(_local_tz()).strftime("%Y-%m-%d")
    cycles_dir = org_cycles_dir(org_id)
    today_dir = os.path.join(cycles_dir, today)
    # If there's already a latest symlink for today, skip
    if os.path.exists(os.path.join(today_dir, "latest")):
        return False
    return True


def _run_org_cycle(org_id: str, org_name: str):
    """Run a cycle for one org."""
    from orchestrator.main import run_cycle
    today = datetime.now(_local_tz()).strftime("%Y-%m-%d")
    model_overrides = get_model_overrides(org_id)
    print(f"  Starting cycle for {org_name} ({org_id})...")
    try:
        run_cycle(org_id, today, model_overrides=model_overrides)
        print(f"  Completed: {org_name}")
    except Exception as e:
        print(f"  FAILED: {org_name} — {e}")


def main():
    """Check all paid orgs and run cycles for those that need one."""
    print(f"\n{'='*60}")
    print(f"  GoBuga Scheduler — {datetime.now(_local_tz()).strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"{'='*60}\n")

    orgs = _load_orgs()
    if not orgs:
        print("No orgs found.")
        return

    # Filter to paid orgs that need a cycle
    eligible = []
    for org_id, org in orgs.items():
        plan = org.get("plan", "free")
        if plan == "free":
            continue
        if not org.get("setup_complete", False):
            continue
        if not _needs_cycle_today(org_id):
            print(f"  Skip {org.get('name', org_id)} — already ran today")
            continue
        eligible.append((org_id, org.get("name", org_id)))

    if not eligible:
        print("No orgs need a cycle today.")
        return

    print(f"Running cycles for {len(eligible)} orgs (max {MAX_CONCURRENT} concurrent):\n")

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as executor:
        futures = {}
        for org_id, org_name in eligible:
            future = executor.submit(_run_org_cycle, org_id, org_name)
            futures[future] = (org_id, org_name)

        for future in as_completed(futures):
            org_id, org_name = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"  Error for {org_name}: {e}")

    print(f"\n{'='*60}")
    print(f"  Scheduler complete")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
