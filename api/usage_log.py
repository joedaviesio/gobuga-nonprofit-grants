"""Usage logger — records every Claude API call with NZ timestamps (multi-tenant)."""

import json
import os
import fcntl
from datetime import datetime
from zoneinfo import ZoneInfo

from api.tenant import org_logs_dir

NZ = ZoneInfo("Pacific/Auckland")


def _ensure_dir(org_id: str):
    os.makedirs(org_logs_dir(org_id), exist_ok=True)


def log_usage(
    org_id: str,
    caller: str,
    usage: dict,
    case_id: str | None = None,
    cycle_date: str | None = None,
    extra: dict | None = None,
):
    """
    Append one usage record to org logs/usage-YYYY-MM-DD.jsonl (NZ date).

    org_id:     the organisation
    caller:     e.g. "bot_a", "bot_b", "grant_watcher", "chat"
    usage:      dict with input_tokens, output_tokens, api_calls, cost_usd, model
    case_id:    optional case reference
    cycle_date: optional cycle date for orchestrator runs
    extra:      any additional metadata
    """
    _ensure_dir(org_id)

    now_nz = datetime.now(NZ)
    record = {
        "ts": now_nz.isoformat(),
        "org_id": org_id,
        "caller": caller,
        "model": usage.get("model", ""),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "api_calls": usage.get("api_calls", 0),
        "cost_usd": usage.get("cost_usd", 0),
    }
    if case_id:
        record["case_id"] = case_id
    if cycle_date:
        record["cycle_date"] = cycle_date
    if extra:
        record.update(extra)

    logs_dir = org_logs_dir(org_id)
    logfile = os.path.join(logs_dir, f"usage-{now_nz.strftime('%Y-%m-%d')}.jsonl")
    line = json.dumps(record) + "\n"

    with open(logfile, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)
        fcntl.flock(f, fcntl.LOCK_UN)


def read_usage(org_id: str, date_str: str | None = None) -> list[dict]:
    """Read usage records for a given NZ date (defaults to today)."""
    if date_str is None:
        date_str = datetime.now(NZ).strftime("%Y-%m-%d")
    logs_dir = org_logs_dir(org_id)
    logfile = os.path.join(logs_dir, f"usage-{date_str}.jsonl")
    if not os.path.exists(logfile):
        return []
    records = []
    with open(logfile) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def usage_summary(org_id: str, date_str: str | None = None) -> dict:
    """Aggregate usage for a given NZ date."""
    records = read_usage(org_id, date_str)
    summary = {
        "date": date_str or datetime.now(NZ).strftime("%Y-%m-%d"),
        "org_id": org_id,
        "total_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "by_caller": {},
    }
    for r in records:
        summary["total_calls"] += r.get("api_calls", 0)
        summary["total_input_tokens"] += r.get("input_tokens", 0)
        summary["total_output_tokens"] += r.get("output_tokens", 0)
        summary["total_cost_usd"] += r.get("cost_usd", 0)

        caller = r.get("caller", "unknown")
        if caller not in summary["by_caller"]:
            summary["by_caller"][caller] = {
                "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
            }
        bc = summary["by_caller"][caller]
        bc["calls"] += r.get("api_calls", 0)
        bc["input_tokens"] += r.get("input_tokens", 0)
        bc["output_tokens"] += r.get("output_tokens", 0)
        bc["cost_usd"] += r.get("cost_usd", 0)

    summary["total_cost_usd"] = round(summary["total_cost_usd"], 4)
    for bc in summary["by_caller"].values():
        bc["cost_usd"] = round(bc["cost_usd"], 4)

    return summary


def count_cycles_this_month(org_id: str) -> int:
    """Count how many cycles have been logged this month."""
    now_nz = datetime.now(NZ)
    year_month = now_nz.strftime("%Y-%m")
    logs_dir = org_logs_dir(org_id)
    if not os.path.exists(logs_dir):
        return 0

    count = 0
    for fname in os.listdir(logs_dir):
        if not fname.startswith(f"usage-{year_month}"):
            continue
        logfile = os.path.join(logs_dir, fname)
        with open(logfile) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record.get("caller") == "grant_reporter" and record.get("cycle_date"):
                    count += 1
    return count
