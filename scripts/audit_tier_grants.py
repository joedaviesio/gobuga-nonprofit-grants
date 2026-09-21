#!/usr/bin/env python3
"""List every org on a paid tier that has no Stripe subscription.

Read-only by default: prints a table for the operator to decide per org.
Nobody is downgraded here.

Usage (on the Railway volume, per deployment):
    railway ssh -s gobuga-nonprofit-grants "python3 scripts/audit_tier_grants.py"

Locally against a copy of a volume:
    GOBUGA_DATA_DIR=/path/to/copy python3 scripts/audit_tier_grants.py
    python3 scripts/audit_tier_grants.py --data-dir /path/to/copy --all

Once the operator confirms an org holds Officer under a platform licence:
    python3 scripts/audit_tier_grants.py --mark-licence <org_id> [<org_id> ...]
which sets tier_source="licence" on those records (the only write this
script performs, and only when asked).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

PAID_PLANS = {"starter", "professional", "officer"}


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _latest_case_activity(orgs_dir: str, org_id: str) -> str | None:
    """Newest mtime under orgs/<id>/cases, as an ISO date, or None."""
    cases = os.path.join(orgs_dir, org_id, "cases")
    newest = 0.0
    for root, _dirs, files in os.walk(cases):
        for name in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, name)))
            except OSError:
                pass
    if not newest:
        return None
    return datetime.fromtimestamp(newest, tz=timezone.utc).strftime("%Y-%m-%d")


def _day(iso: str | None) -> str:
    return (iso or "")[:10] or "-"


def audit(data_dir: str, include_all: bool = False) -> list[dict]:
    """Return one row per paid-tier org. Without include_all, only orgs with
    no Stripe subscription id (the ones that need explaining)."""
    from api.limits import tier_source_for

    orgs = _load(os.path.join(data_dir, "platform", "orgs.json"))
    orgs_dir = os.path.join(data_dir, "orgs")
    rows = []
    for org_id, org in sorted(orgs.items(), key=lambda kv: kv[1].get("created", "")):
        plan = org.get("plan", "free")
        if plan not in PAID_PLANS:
            continue
        sub_id = org.get("stripe_subscription_id")
        if sub_id and not include_all:
            continue
        activity = max(
            filter(None, [_day(org.get("updated")) if org.get("updated") else None,
                          _latest_case_activity(orgs_dir, org_id)]),
            default="-",
        )
        rows.append({
            "org_id": org_id,
            "name": org.get("name", ""),
            "plan": plan,
            "tier_source": tier_source_for(org) or "UNEXPLAINED",
            "stripe_customer": "yes" if org.get("stripe_customer_id") else "no",
            "stripe_sub": "yes" if sub_id else "no",
            "created": _day(org.get("created")),
            "last_activity": activity,
        })
    return rows


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("No paid-tier orgs without a Stripe subscription.")
        return
    cols = ["org_id", "name", "plan", "tier_source", "stripe_customer", "stripe_sub", "created", "last_activity"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    line = "  ".join(c.ljust(widths[c]) for c in cols)
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))
    print(f"\n{len(rows)} org(s). UNEXPLAINED = paid tier with no Stripe subscription and no recorded source.")


def mark_licence(data_dir: str, org_ids: list[str]) -> None:
    import api.tenant as tenant
    tenant.PLATFORM_DIR = os.path.join(data_dir, "platform")
    from api.auth import get_org, update_org
    for org_id in org_ids:
        org = get_org(org_id)
        if not org:
            print(f"{org_id}: not found")
            continue
        update_org(org_id, {"tier_source": "licence"})
        print(f"{org_id} ({org.get('name', '')}): tier_source=licence")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=os.environ.get("GOBUGA_DATA_DIR", PROJECT_ROOT),
                    help="data root holding platform/ and orgs/ (default: $GOBUGA_DATA_DIR or repo root)")
    ap.add_argument("--all", action="store_true", help="include Stripe-backed paid orgs too")
    ap.add_argument("--json", action="store_true", help="print rows as JSON instead of a table")
    ap.add_argument("--mark-licence", nargs="+", metavar="ORG_ID",
                    help="set tier_source=licence on these orgs (write)")
    args = ap.parse_args(argv)

    if args.mark_licence:
        mark_licence(args.data_dir, args.mark_licence)
        return 0

    rows = audit(args.data_dir, include_all=args.all)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(f"Data dir: {args.data_dir}\n")
        print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
