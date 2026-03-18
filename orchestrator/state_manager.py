"""Reads/writes shared JSON state files (multi-tenant)."""

import json
import os

from api.tenant import org_state_dir


def load_state(org_id: str, filename: str) -> dict:
    path = os.path.join(org_state_dir(org_id), filename)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(org_id: str, filename: str, data: dict):
    state_dir = org_state_dir(org_id)
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_all_state(org_id: str) -> dict:
    """Load all state files into a single dict keyed by filename."""
    state_dir = org_state_dir(org_id)
    os.makedirs(state_dir, exist_ok=True)
    state = {}
    for fname in os.listdir(state_dir):
        if fname.endswith(".json"):
            state[fname.replace(".json", "")] = load_state(org_id, fname)
    return state
