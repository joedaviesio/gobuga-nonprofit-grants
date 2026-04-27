"""Platform-tenant evidence store for country sweeps.

Mirrors `orchestrator/evidence.py` but writes to
`platform/cycles/<country>/<month>/run-<ts>/evidence/` instead of the
per-org directory.

Sweep `org_id`s use a special prefix `_sweep:<country>:<month>:<run_ts>`
so the existing tool dispatcher can route to the right backend without
changing the agent runner.
"""

import hashlib
import json
import os
from datetime import datetime, timezone

from api.tenant import platform_run_dir


SWEEP_ORG_PREFIX = "_sweep:"


def is_sweep_org_id(org_id: str) -> bool:
    return org_id.startswith(SWEEP_ORG_PREFIX)


def parse_sweep_org_id(org_id: str) -> tuple[str, str, str]:
    """`_sweep:<country>:<month>:<run_ts>` -> (country, month, run_ts)."""
    if not is_sweep_org_id(org_id):
        raise ValueError(f"Not a sweep org_id: {org_id!r}")
    parts = org_id[len(SWEEP_ORG_PREFIX):].split(":")
    if len(parts) != 3:
        raise ValueError(f"Malformed sweep org_id: {org_id!r}")
    return parts[0], parts[1], parts[2]


def make_sweep_org_id(country: str, month: str, run_ts: str) -> str:
    return f"{SWEEP_ORG_PREFIX}{country}:{month}:{run_ts}"


def _evidence_dir(country: str, month: str, run_ts: str) -> str:
    return os.path.join(platform_run_dir(country, month, run_ts), "evidence")


def _index_path(country: str, month: str, run_ts: str) -> str:
    return os.path.join(_evidence_dir(country, month, run_ts), "index.json")


def _load_index(country: str, month: str, run_ts: str) -> list:
    path = _index_path(country, month, run_ts)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def _save_index(country: str, month: str, run_ts: str, index: list) -> None:
    ev_dir = _evidence_dir(country, month, run_ts)
    os.makedirs(ev_dir, exist_ok=True)
    with open(_index_path(country, month, run_ts), "w") as f:
        json.dump(index, f, indent=2)


def save_sweep_evidence(org_id: str, agent_id: str, date: str, item: dict) -> dict:
    """Save an evidence item to the platform sweep store. Same record shape
    as `orchestrator.evidence.save_evidence` so downstream code can be agnostic."""
    country, month, run_ts = parse_sweep_org_id(org_id)
    ev_dir = _evidence_dir(country, month, run_ts)
    agent_dir = os.path.join(ev_dir, agent_id)
    os.makedirs(agent_dir, exist_ok=True)

    index = _load_index(country, month, run_ts)
    ev_num = len(index) + 1
    ev_id = f"EV-{ev_num:04d}"

    record = {
        "id": ev_id,
        "agent": agent_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "date": date,
        **item,
    }
    record["hash"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()

    with open(os.path.join(agent_dir, f"{ev_id}.json"), "w") as f:
        json.dump(record, f, indent=2)

    index.append({
        "id": ev_id,
        "agent": agent_id,
        "date": date,
        "type": item.get("type", "unknown"),
        "title": item.get("title", ""),
        "hash": record["hash"],
    })
    _save_index(country, month, run_ts, index)

    return record


def load_sweep_evidence(country: str, month: str, run_ts: str) -> list[dict]:
    """Load every evidence item written during a sweep run."""
    ev_dir = _evidence_dir(country, month, run_ts)
    if not os.path.exists(ev_dir):
        return []
    items = []
    for root, _, files in os.walk(ev_dir):
        for fname in sorted(files):
            if fname.endswith(".json") and fname != "index.json":
                with open(os.path.join(root, fname)) as f:
                    items.append(json.load(f))
    return items
