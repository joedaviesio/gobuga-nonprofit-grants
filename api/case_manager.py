"""Case data model — CRUD for grant submission cases (multi-tenant)."""

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from api.case_codes import infer_geo_code, infer_sector_code
from api.store import load_json, save_json
from api.tenant import org_cases_dir


def _cases_dir(org_id: str) -> str:
    return org_cases_dir(org_id)


def _next_case_id(org_id: str, grant_brief: dict) -> str:
    """Generate next case ID with sector and geography codes.

    Format: CASE-{SECTOR}-{GEO}-{YY}{SEQ:03d}
    Example: CASE-EDU-NZ-26001
    """
    cases_dir = _cases_dir(org_id)
    os.makedirs(cases_dir, exist_ok=True)
    existing = [
        d for d in os.listdir(cases_dir)
        if os.path.isdir(os.path.join(cases_dir, d)) and d.startswith("CASE-")
    ]
    year = datetime.now(timezone.utc).year % 100
    year_str = str(year)
    if not existing:
        seq = 1
    else:
        nums = []
        for d in existing:
            suffix = d.split("-")[-1]  # e.g. "26003" or "262626003"
            # Strip the leading 2-digit year prefix(es) to recover the
            # actual sequence number.  Old IDs snowballed because the
            # year was prepended each time on top of the previous value.
            while suffix.startswith(year_str) and len(suffix) > len(year_str):
                suffix = suffix[len(year_str):]
            try:
                nums.append(int(suffix))
            except ValueError:
                pass
        seq = max(nums, default=0) + 1
    sector = infer_sector_code(grant_brief)
    geo = infer_geo_code(grant_brief)
    return f"CASE-{sector}-{geo}-{year}{seq:03d}"


def _case_dir(org_id: str, case_id: str) -> str:
    return os.path.join(_cases_dir(org_id), case_id)


def _case_path(org_id: str, case_id: str) -> str:
    return os.path.join(_case_dir(org_id, case_id), "case.json")


def _uploads_dir(org_id: str, case_id: str) -> str:
    return os.path.join(_case_dir(org_id, case_id), "uploads")


def _new_case(org_id: str, grant_id: str, grant_brief: dict, officer: str = "grants_officer") -> dict:
    """Create the default case structure."""
    case_id = _next_case_id(org_id, grant_brief)
    now = datetime.now(timezone.utc).isoformat()
    return {
        "case_id": case_id,
        "org_id": org_id,
        "grant_id": grant_id,
        "status": "open",
        "created": now,
        "updated": now,
        "officer": officer,
        "signoff": None,
        "grant_brief": grant_brief,
        "uploads": [],
        "draft": {
            "sections": {},
        },
        "parsed_sections": None,
        "conversation": [],
        "exports": [],
    }


def create_case(org_id: str, grant_id: str, grant_brief: dict, officer: str = "grants_officer",
                 auto_summary: bool = True) -> dict:
    """Create a new case and persist it. Optionally runs Bot A for a tailored summary."""
    case = _new_case(org_id, grant_id, grant_brief, officer)
    case_id = case["case_id"]
    os.makedirs(_case_dir(org_id, case_id), exist_ok=True)
    os.makedirs(_uploads_dir(org_id, case_id), exist_ok=True)
    _save(org_id, case)

    if auto_summary:
        try:
            from api.bots import bot_a_generate_summary
            bot_a_generate_summary(org_id, case_id)
            case = load_case(org_id, case_id)
        except Exception:
            from api.brief_generator import generate_case_brief
            brief_text = generate_case_brief(case)
            now = datetime.now(timezone.utc).isoformat()
            case["conversation"].append({
                "role": "assistant",
                "content": brief_text,
                "timestamp": now,
                "is_brief": True,
            })
            _save(org_id, case)

    return case


def _save(org_id: str, case: dict):
    case["updated"] = datetime.now(timezone.utc).isoformat()
    save_json(_case_path(org_id, case["case_id"]), case)


def load_case(org_id: str, case_id: str) -> Optional[dict]:
    path = _case_path(org_id, case_id)
    if not os.path.exists(path):
        return None
    return load_json(path)


def update_case(org_id: str, case_id: str, updates: dict) -> Optional[dict]:
    """Patch case metadata (status, signoff, etc). Not for draft or conversation."""
    case = load_case(org_id, case_id)
    if case is None:
        return None
    allowed = {"status", "signoff", "officer", "grant_brief", "grant_id"}
    for k, v in updates.items():
        if k in allowed:
            case[k] = v
    _save(org_id, case)
    return case


def list_cases(org_id: str) -> list[dict]:
    """List all cases (summary only)."""
    cases_dir = _cases_dir(org_id)
    os.makedirs(cases_dir, exist_ok=True)
    cases = []
    for d in sorted(os.listdir(cases_dir)):
        path = os.path.join(cases_dir, d, "case.json")
        if os.path.exists(path):
            case = load_json(path)
            cases.append({
                "case_id": case["case_id"],
                "grant_id": case["grant_id"],
                "status": case["status"],
                "created": case["created"],
                "updated": case["updated"],
                "officer": case["officer"],
                "sections_count": len(case.get("draft", {}).get("sections", {})),
                "uploads_count": len(case.get("uploads", [])),
            })
    return cases


def delete_case(org_id: str, case_id: str) -> bool:
    d = _case_dir(org_id, case_id)
    if os.path.exists(d):
        shutil.rmtree(d)
        return True
    return False


# --- Draft operations ---

def get_draft(org_id: str, case_id: str) -> Optional[dict]:
    case = load_case(org_id, case_id)
    if case is None:
        return None
    return case["draft"]


def update_section(org_id: str, case_id: str, section_name: str, content: str, status: str = "draft") -> Optional[dict]:
    case = load_case(org_id, case_id)
    if case is None:
        return None
    case["draft"]["sections"][section_name] = {
        "content": content,
        "status": status,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    _save(org_id, case)
    return case["draft"]["sections"][section_name]


# --- Upload operations ---

def _sanitize_filename(filename: str) -> str:
    """Replace spaces and problematic characters with underscores."""
    import re
    name = re.sub(r'[^\w\-.]', '_', filename)
    name = re.sub(r'_+', '_', name)
    return name


def add_upload(org_id: str, case_id: str, filename: str, file_bytes: bytes, file_type: str = "document") -> Optional[dict]:
    """Save an uploaded file and register it in the case."""
    case = load_case(org_id, case_id)
    if case is None:
        return None
    filename = _sanitize_filename(filename)
    uploads_dir = _uploads_dir(org_id, case_id)
    os.makedirs(uploads_dir, exist_ok=True)
    filepath = os.path.join(uploads_dir, filename)
    if not Path(filepath).resolve().is_relative_to(Path(uploads_dir).resolve()):
        raise ValueError("Invalid filename")
    with open(filepath, "wb") as f:
        f.write(file_bytes)
    upload_record = {
        "filename": filename,
        "uploaded": datetime.now(timezone.utc).isoformat(),
        "type": file_type,
        "size_bytes": len(file_bytes),
    }
    case["uploads"].append(upload_record)
    _save(org_id, case)
    return upload_record


# --- Conversation operations ---

def append_message(org_id: str, case_id: str, role: str, content: str, actions: list = None) -> Optional[dict]:
    """Add a message to the case conversation."""
    case = load_case(org_id, case_id)
    if case is None:
        return None
    msg = {
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if actions:
        msg["actions"] = actions
    case["conversation"].append(msg)
    _save(org_id, case)
    return msg


def get_conversation(org_id: str, case_id: str) -> Optional[list]:
    case = load_case(org_id, case_id)
    if case is None:
        return None
    return case["conversation"]
