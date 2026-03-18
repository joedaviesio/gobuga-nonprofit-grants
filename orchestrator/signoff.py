"""Signoff handler — CLI for the human to record decisions (multi-tenant)."""

import json
import os
from datetime import date as date_type

from api.tenant import org_root


def _signoff_dir(org_id: str) -> str:
    return os.path.join(org_root(org_id), "signoffs")


def load_signoff(org_id: str, date: str) -> dict | None:
    """Load signoff for a given date, or None."""
    path = os.path.join(_signoff_dir(org_id), f"{date}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def save_signoff(org_id: str, date: str, decisions: dict):
    signoff_dir = _signoff_dir(org_id)
    os.makedirs(signoff_dir, exist_ok=True)
    path = os.path.join(signoff_dir, f"{date}.json")
    with open(path, "w") as f:
        json.dump(decisions, f, indent=2)


def interactive_signoff(org_id: str, cycle_date: str | None = None):
    """CLI signoff flow."""
    from api.tenant import org_cycles_dir
    if cycle_date is None:
        cycle_date = date_type.today().isoformat()

    report_path = os.path.join(org_cycles_dir(org_id), cycle_date, "latest", "report.md")
    if not os.path.exists(report_path):
        print(f"No report found for {cycle_date}. Run a cycle first.")
        return

    with open(report_path) as f:
        report = f.read()

    print(f"\n{'='*60}")
    print(f"  Daily Grant Brief — {cycle_date}")
    print(f"{'='*60}\n")
    print(report)
    print(f"\n{'='*60}")
    print("  SIGNOFF")
    print(f"{'='*60}\n")

    decisions = {"date": cycle_date, "items": []}

    while True:
        item = input("Action item to decide on (or 'done'): ").strip()
        if item.lower() == "done":
            break
        decision = input(f"  Decision for '{item}' [approve/reject/redirect]: ").strip().lower()
        note = input(f"  Note (optional): ").strip()
        decisions["items"].append({
            "item": item,
            "decision": decision,
            "note": note,
        })

    if decisions["items"]:
        save_signoff(org_id, cycle_date, decisions)
        print(f"\nSignoff saved.")
    else:
        print("\nNo decisions recorded.")
