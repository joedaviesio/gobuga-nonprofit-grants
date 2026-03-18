"""Authentication — user/org model with bcrypt hashing and session tokens."""

import json
import os
import secrets
import time
import uuid
from datetime import datetime, timezone

import bcrypt

from api.tenant import (
    ensure_org_dirs,
    ensure_platform_dirs,
    users_path,
    orgs_path,
    sessions_path,
)

SESSION_TTL = 60 * 60 * 24 * 30  # 30 days


# --- Low-level JSON store ---

def _load_json(path: str) -> dict | list:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _save_json(path: str, data):
    ensure_platform_dirs()
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# --- Users ---

def _load_users() -> dict:
    """Returns {user_id: user_record}."""
    return _load_json(users_path()) or {}


def _save_users(users: dict):
    _save_json(users_path(), users)


def _find_user_by_email(email: str) -> dict | None:
    users = _load_users()
    for uid, user in users.items():
        if user["email"].lower() == email.lower():
            return {**user, "user_id": uid}
    return None


# --- Orgs ---

def _load_orgs() -> dict:
    """Returns {org_id: org_record}."""
    return _load_json(orgs_path()) or {}


def _save_orgs(orgs: dict):
    _save_json(orgs_path(), orgs)


def get_org(org_id: str) -> dict | None:
    orgs = _load_orgs()
    org = orgs.get(org_id)
    if org:
        return {**org, "org_id": org_id}
    return None


def update_org(org_id: str, updates: dict) -> dict | None:
    orgs = _load_orgs()
    if org_id not in orgs:
        return None
    orgs[org_id].update(updates)
    orgs[org_id]["updated"] = datetime.now(timezone.utc).isoformat()
    _save_orgs(orgs)
    return {**orgs[org_id], "org_id": org_id}


# --- Sessions ---

def _load_sessions() -> dict:
    """Returns {token: session_record}."""
    data = _load_json(sessions_path())
    if not data:
        return {}
    # Prune expired
    now = time.time()
    valid = {k: v for k, v in data.items() if v.get("expires_at", 0) > now}
    if len(valid) != len(data):
        _save_json(sessions_path(), valid)
    return valid


def _save_sessions(sessions: dict):
    _save_json(sessions_path(), sessions)


# --- Public API ---

def register(email: str, password: str, org_name: str) -> dict:
    """
    Register a new user and org.
    Returns {"token", "user_id", "org_id", "expires_in"}.
    Raises ValueError on duplicate email.
    """
    email = email.strip().lower()
    if _find_user_by_email(email):
        raise ValueError("An account with this email already exists")

    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    # Create org
    org_id = str(uuid.uuid4())[:8]
    orgs = _load_orgs()
    now = datetime.now(timezone.utc).isoformat()
    orgs[org_id] = {
        "name": org_name.strip(),
        "plan": "free",
        "setup_complete": False,
        "created": now,
        "updated": now,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
    }
    _save_orgs(orgs)

    # Create org directory structure
    ensure_org_dirs(org_id)

    # Create user
    user_id = str(uuid.uuid4())[:8]
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    users = _load_users()
    users[user_id] = {
        "email": email,
        "password_hash": pw_hash,
        "org_id": org_id,
        "role": "admin",
        "created": now,
    }
    _save_users(users)

    # Create session
    token = secrets.token_urlsafe(32)
    sessions = _load_sessions()
    sessions[token] = {
        "user_id": user_id,
        "org_id": org_id,
        "expires_at": time.time() + SESSION_TTL,
        "created": now,
    }
    _save_sessions(sessions)

    return {
        "token": token,
        "user_id": user_id,
        "org_id": org_id,
        "org_name": org_name.strip(),
        "setup_complete": False,
        "expires_in": SESSION_TTL,
    }


def login(email: str, password: str) -> dict:
    """
    Authenticate and return a session token.
    Returns {"token", "user_id", "org_id", "expires_in"}.
    Raises ValueError on bad credentials.
    """
    email = email.strip().lower()
    user = _find_user_by_email(email)
    if not user:
        raise ValueError("Invalid email or password")

    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        raise ValueError("Invalid email or password")

    org = get_org(user["org_id"])

    token = secrets.token_urlsafe(32)
    sessions = _load_sessions()
    sessions[token] = {
        "user_id": user["user_id"],
        "org_id": user["org_id"],
        "expires_at": time.time() + SESSION_TTL,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    _save_sessions(sessions)

    return {
        "token": token,
        "user_id": user["user_id"],
        "org_id": user["org_id"],
        "org_name": org["name"] if org else "",
        "setup_complete": org.get("setup_complete", False) if org else False,
        "expires_in": SESSION_TTL,
    }


def verify_session(token: str) -> dict | None:
    """
    Verify a session token. Returns {"user_id", "org_id"} or None.
    """
    if not token:
        return None
    sessions = _load_sessions()
    session = sessions.get(token)
    if not session:
        return None
    if session.get("expires_at", 0) < time.time():
        # Expired — clean up
        del sessions[token]
        _save_sessions(sessions)
        return None
    return {
        "user_id": session["user_id"],
        "org_id": session["org_id"],
    }


def logout(token: str):
    """Invalidate a session token."""
    sessions = _load_sessions()
    sessions.pop(token, None)
    _save_sessions(sessions)
