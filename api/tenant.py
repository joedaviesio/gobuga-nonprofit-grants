"""Multi-tenant path resolution — every org-scoped path goes through here."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
_DATA_ROOT = os.environ.get("GOBUGA_DATA_DIR", PROJECT_ROOT)
ORGS_DIR = os.path.join(_DATA_ROOT, "orgs")
PLATFORM_DIR = os.path.join(_DATA_ROOT, "platform")


# --- Org-scoped paths ---

def org_root(org_id: str) -> str:
    return os.path.join(ORGS_DIR, org_id)


def org_cases_dir(org_id: str) -> str:
    return os.path.join(org_root(org_id), "cases")


def org_cycles_dir(org_id: str) -> str:
    return os.path.join(org_root(org_id), "cycles")


def org_evidence_dir(org_id: str) -> str:
    return os.path.join(org_root(org_id), "evidence")


def org_state_dir(org_id: str) -> str:
    return os.path.join(org_root(org_id), "state")


def org_logs_dir(org_id: str) -> str:
    return os.path.join(org_root(org_id), "logs")


def org_data_dir(org_id: str) -> str:
    return os.path.join(org_root(org_id), "data")


def org_prompts_dir(org_id: str) -> str:
    return os.path.join(org_root(org_id), "prompts")


def org_profile_path(org_id: str) -> str:
    return os.path.join(org_root(org_id), "org-profile.md")


# --- Platform-level paths ---

def platform_dir() -> str:
    return PLATFORM_DIR


def users_path() -> str:
    return os.path.join(PLATFORM_DIR, "users.json")


def orgs_path() -> str:
    return os.path.join(PLATFORM_DIR, "orgs.json")


def sessions_path() -> str:
    return os.path.join(PLATFORM_DIR, "sessions.json")


def password_resets_path() -> str:
    return os.path.join(PLATFORM_DIR, "password_resets.json")


# --- Directory creation ---

def ensure_org_dirs(org_id: str) -> str:
    """Create the full directory structure for an org. Returns org root path."""
    root = org_root(org_id)
    dirs = [
        root,
        org_cases_dir(org_id),
        org_cycles_dir(org_id),
        org_evidence_dir(org_id),
        org_state_dir(org_id),
        org_logs_dir(org_id),
        os.path.join(org_data_dir(org_id), "org"),
        os.path.join(org_data_dir(org_id), "donors"),
        os.path.join(org_data_dir(org_id), "grants"),
        org_prompts_dir(org_id),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    return root


def ensure_platform_dirs():
    """Create platform directory structure."""
    os.makedirs(PLATFORM_DIR, exist_ok=True)
