"""Case Data Bank — flat knowledge store per case (multi-tenant).

Each case has a databank.json that holds typed knowledge chunks.
Bots read from and write to this store. Uploads, user answers, org docs,
and grant brief details all get distilled into entries here.
"""

import os
from datetime import datetime, timezone
from typing import Optional

from api.store import load_json, save_json
from api.case_manager import _case_dir, load_case

# Sources that can populate the databank
SOURCES = {"org_profile", "upload", "user_input", "grant_brief", "bot_extract"}


def _databank_path(org_id: str, case_id: str) -> str:
    return os.path.join(_case_dir(org_id, case_id), "databank.json")


def _load_databank(org_id: str, case_id: str) -> dict:
    path = _databank_path(org_id, case_id)
    return load_json(path, default={"entries": []})


def _save_databank(org_id: str, case_id: str, databank: dict):
    path = _databank_path(org_id, case_id)
    save_json(path, databank)


def _next_id(databank: dict) -> str:
    existing = [e["id"] for e in databank["entries"] if e.get("id")]
    if not existing:
        return "DB-0001"
    nums = []
    for eid in existing:
        try:
            nums.append(int(eid.split("-")[1]))
        except (IndexError, ValueError):
            pass
    seq = max(nums, default=0) + 1
    return f"DB-{seq:04d}"


def add_entry(org_id: str, case_id: str, key: str, value: str, source: str = "bot_extract",
              category: str = "general") -> Optional[dict]:
    """Add or update a databank entry. If key exists, updates the value."""
    databank = _load_databank(org_id, case_id)

    # Check for existing entry with same key — update it
    for entry in databank["entries"]:
        if entry["key"] == key:
            entry["value"] = value
            entry["source"] = source
            entry["category"] = category
            entry["updated"] = datetime.now(timezone.utc).isoformat()
            _save_databank(org_id, case_id, databank)
            return entry

    # New entry
    entry = {
        "id": _next_id(databank),
        "key": key,
        "value": value,
        "source": source,
        "category": category,
        "added": datetime.now(timezone.utc).isoformat(),
    }
    databank["entries"].append(entry)
    _save_databank(org_id, case_id, databank)
    return entry


def add_entries_bulk(org_id: str, case_id: str, entries: list[dict]) -> list[dict]:
    """Add multiple entries at once. Each dict needs key, value, and optionally source/category."""
    results = []
    for e in entries:
        result = add_entry(
            org_id,
            case_id,
            e["key"],
            e["value"],
            e.get("source", "bot_extract"),
            e.get("category", "general"),
        )
        if result:
            results.append(result)
    return results


def get_entries(org_id: str, case_id: str, category: str = None) -> list[dict]:
    """Get all databank entries, optionally filtered by category."""
    databank = _load_databank(org_id, case_id)
    entries = databank["entries"]
    if category:
        entries = [e for e in entries if e.get("category") == category]
    return entries


def get_entry(org_id: str, case_id: str, key: str) -> Optional[dict]:
    """Get a single entry by key."""
    databank = _load_databank(org_id, case_id)
    for entry in databank["entries"]:
        if entry["key"] == key:
            return entry
    return None


def delete_entry(org_id: str, case_id: str, key: str) -> bool:
    """Remove an entry by key."""
    databank = _load_databank(org_id, case_id)
    before = len(databank["entries"])
    databank["entries"] = [e for e in databank["entries"] if e["key"] != key]
    if len(databank["entries"]) < before:
        _save_databank(org_id, case_id, databank)
        return True
    return False


def format_databank_for_prompt(org_id: str, case_id: str) -> str:
    """Format the databank as a readable string for bot system prompts."""
    entries = get_entries(org_id, case_id)
    if not entries:
        return "The case data bank is empty. No information has been collected yet."

    # Group by category
    by_cat: dict[str, list[dict]] = {}
    for e in entries:
        cat = e.get("category", "general")
        by_cat.setdefault(cat, []).append(e)

    parts = ["## Case Data Bank\n"]
    for cat, items in sorted(by_cat.items()):
        parts.append(f"### {cat.replace('_', ' ').title()}")
        for item in items:
            parts.append(f"- **{item['key']}:** {item['value']}")
        parts.append("")

    return "\n".join(parts)


def seed_from_grant_brief(org_id: str, case_id: str, grant_brief: dict) -> list[dict]:
    """Seed the databank with entries from the grant brief."""
    entries = []
    field_map = {
        "title": ("grant_title", "grant"),
        "funder": ("funder_name", "grant"),
        "amount": ("funding_amount", "grant"),
        "deadline": ("submission_deadline", "grant"),
        "description": ("grant_description", "grant"),
        "priority": ("priority_level", "grant"),
        "recommendation": ("analyst_recommendation", "grant"),
    }
    for field, (key, category) in field_map.items():
        val = grant_brief.get(field)
        if val:
            entries.append({
                "key": key,
                "value": str(val),
                "source": "grant_brief",
                "category": category,
            })

    # Objectives
    for i, obj in enumerate(grant_brief.get("objectives", []), 1):
        entries.append({
            "key": f"grant_objective_{i}",
            "value": obj,
            "source": "grant_brief",
            "category": "grant",
        })

    # Target groups
    for i, tg in enumerate(grant_brief.get("target_groups", []), 1):
        entries.append({
            "key": f"target_group_{i}",
            "value": tg,
            "source": "grant_brief",
            "category": "grant",
        })

    if entries:
        return add_entries_bulk(org_id, case_id, entries)
    return []


def seed_from_org_profile(org_id: str, case_id: str, org_text: str) -> list[dict]:
    """Seed databank with org profile info. Bot A will enrich this."""
    entries = [
        {
            "key": "org_profile_full",
            "value": org_text[:4000],
            "source": "org_profile",
            "category": "organisation",
        }
    ]
    return add_entries_bulk(org_id, case_id, entries)
