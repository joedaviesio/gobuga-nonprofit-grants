"""Tamper-evident evidence store. Each item gets a SHA-256 hash (multi-tenant)."""

import hashlib
import json
import os
from datetime import datetime, timezone

from api.tenant import org_evidence_dir


def _index_path(org_id: str) -> str:
    return os.path.join(org_evidence_dir(org_id), "index.json")


def _load_index(org_id: str):
    path = _index_path(org_id)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def _save_index(org_id: str, index):
    ev_dir = org_evidence_dir(org_id)
    os.makedirs(ev_dir, exist_ok=True)
    with open(_index_path(org_id), "w") as f:
        json.dump(index, f, indent=2)


def save_evidence(org_id: str, agent_id: str, date: str, item: dict) -> dict:
    """Save an evidence item. Returns the record with id and hash."""
    ev_dir = org_evidence_dir(org_id)
    day_dir = os.path.join(ev_dir, date, agent_id)
    os.makedirs(day_dir, exist_ok=True)

    index = _load_index(org_id)
    ev_num = len(index) + 1
    ev_id = f"EV-{ev_num:04d}"

    record = {
        "id": ev_id,
        "agent": agent_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "date": date,
        **item,
    }

    content_str = json.dumps(record, sort_keys=True)
    record["hash"] = hashlib.sha256(content_str.encode()).hexdigest()

    filepath = os.path.join(day_dir, f"{ev_id}.json")
    with open(filepath, "w") as f:
        json.dump(record, f, indent=2)

    index.append({
        "id": ev_id,
        "agent": agent_id,
        "date": date,
        "type": item.get("type", "unknown"),
        "title": item.get("title", ""),
        "hash": record["hash"],
    })
    _save_index(org_id, index)

    return record


def load_evidence_for_date(org_id: str, date: str) -> list[dict]:
    """Load all evidence items for a given date."""
    ev_dir = org_evidence_dir(org_id)
    day_dir = os.path.join(ev_dir, date)
    if not os.path.exists(day_dir):
        return []

    items = []
    for root, _, files in os.walk(day_dir):
        for fname in sorted(files):
            if fname.endswith(".json"):
                with open(os.path.join(root, fname)) as f:
                    items.append(json.load(f))
    return items
