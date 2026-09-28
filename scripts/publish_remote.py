#!/usr/bin/env python3
"""Publish a local sweep's verified rows to a live deployment.

The sweep runs here; the public dataset lives on the server's volume. This
sends the run's `verified.json` to `POST /api/admin/publish`, where the rows
go through the same gate as any publish.

A dry run by default: it reports what would be published and changes nothing.
`--confirm` publishes. The secret is read from the SWEEP_SECRET environment
variable, never from the command line.

Usage:
    SWEEP_SECRET=... python scripts/publish_remote.py <run_dir> --site https://gobuga.org
    SWEEP_SECRET=... python scripts/publish_remote.py <run_dir> --site https://gobuga.org --confirm
    ... --confirm --force --by "Joe"     # publish although a tier 1 funder is missing
"""

import argparse
import json
import os
import sys

import httpx


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--site", required=True, help="the deployment, e.g. https://gobuga.org")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--by")
    args = ap.parse_args(argv)
    secret = os.environ.get("SWEEP_SECRET", "")
    if not secret:
        print("Set SWEEP_SECRET in the environment first.", file=sys.stderr)
        return 2
    with open(os.path.join(args.run_dir, "verified.json"), encoding="utf-8") as f:
        rows = json.load(f)["opportunities"]
    with open(os.path.join(args.run_dir, "register_report.json"), encoding="utf-8") as f:
        report = json.load(f)
    body = {"month": report["month"], "run_ts": report["run_ts"], "rows": rows,
            "force": args.force, "forced_by": args.by, "dry_run": not args.confirm}
    print(f"{'Publishing' if args.confirm else 'Dry run:'} {len(rows)} rows for {report['month']} "
          f"to {args.site}")
    resp = httpx.post(f"{args.site.rstrip('/')}/api/admin/publish", json=body, timeout=120,
                      headers={"Authorization": f"Bearer {secret}"})
    try:
        out = resp.json()
    except ValueError:
        out = {"body": resp.text[:300]}
    out.pop("held", None)
    print(resp.status_code, json.dumps(out, indent=2, ensure_ascii=False))
    if not args.confirm and resp.status_code == 200:
        print("Nothing was changed. Add --confirm to publish.")
    return 0 if resp.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
