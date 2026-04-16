#!/usr/bin/env python3
"""Scan every cycle run on the volume and categorize success/failure.

Usage:
    railway ssh "python3 scripts/cycle_health.py"
"""

import json
import os
import glob


DATA_ROOT = os.environ.get("GOBUGA_DATA_DIR", "/data")


def classify(cycle_dir: str) -> tuple[int, int, str]:
    ev_path = os.path.join(cycle_dir, "evidence.json")
    rp_path = os.path.join(cycle_dir, "report.md")

    ev_count = 0
    opp_count = 0
    if os.path.exists(ev_path):
        try:
            ev = json.load(open(ev_path))
            ev_count = len(ev)
            opp_count = sum(1 for e in ev if e.get("type") == "grant_opportunity")
        except Exception:
            pass

    report = ""
    if os.path.exists(rp_path):
        report = open(rp_path).read()

    blocked = (
        "BLOCKED" in report
        or "CRITICAL ISSUE" in report
        or "No Data to Report" in report
    )

    if opp_count >= 1 and not blocked:
        status = "OK"
    elif opp_count == 0:
        status = "watcher-empty"
    else:
        status = "reporter-blocked"

    return ev_count, opp_count, status


def main():
    pattern = os.path.join(DATA_ROOT, "orgs", "*", "cycles", "*", "run-*")
    rows = []
    for cycle_dir in sorted(glob.glob(pattern)):
        parts = cycle_dir.split("/")
        org = parts[-4]
        date = parts[-2]
        run = parts[-1]
        ev_count, opp_count, status = classify(cycle_dir)
        rows.append((date, run, org[:8], opp_count, ev_count, status))

    for r in rows:
        print(f"{r[0]}  {r[1]}  org={r[2]}  opps={r[3]:2d}  ev={r[4]:2d}  {r[5]}")

    total = len(rows)
    ok = sum(1 for r in rows if r[5] == "OK")
    we = sum(1 for r in rows if r[5] == "watcher-empty")
    rb = sum(1 for r in rows if r[5] == "reporter-blocked")

    print(f"\nTotal: {total}")
    if total:
        print(f"  OK:               {ok}  ({100*ok/total:.0f}%)")
        print(f"  Watcher-empty:    {we}  ({100*we/total:.0f}%)")
        print(f"  Reporter-blocked: {rb}  ({100*rb/total:.0f}%)")


if __name__ == "__main__":
    main()
