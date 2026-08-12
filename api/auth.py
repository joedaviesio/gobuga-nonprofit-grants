"""Authentication — user/org model with bcrypt hashing and session tokens."""

import os
import secrets
import time
import uuid
from datetime import datetime, timezone

import bcrypt

from api.country_config import get_country
from api.store import load_json, save_json
from api.tenant import (
    ensure_org_dirs,
    ensure_platform_dirs,
    users_path,
    orgs_path,
    sessions_path,
    password_resets_path,
)

SESSION_TTL = 60 * 60 * 24 * 30  # 30 days
RESET_TTL = 60 * 15  # 15 minutes


# --- Low-level JSON store ---

def _load_json(path: str) -> dict | list:
    return load_json(path, default={})


def _save_json(path: str, data):
    ensure_platform_dirs()
    save_json(path, data)


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

def register(email: str, password: str, org_name: str, website_url: str = "") -> dict:
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
        "website_url": website_url.strip() if website_url.strip().startswith(("http://", "https://")) else "https://" + website_url.strip() if website_url.strip() else "",
        "plan": "free",
        "country": get_country(),
        "is_admin": False,
        "tailored_enabled": False,
        "setup_complete": False,
        "seeding_complete": False,
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


def verify_password(user_id: str, password: str) -> bool:
    """Verify a user's password by user_id. Used for cycle trigger confirmation."""
    users = _load_users()
    user = users.get(user_id)
    if not user:
        return False
    return bcrypt.checkpw(password.encode(), user["password_hash"].encode())


# --- Password resets ---

def _load_resets() -> dict:
    """Returns {token: reset_record}. Prunes expired/used entries."""
    data = _load_json(password_resets_path()) or {}
    now = time.time()
    valid = {k: v for k, v in data.items() if v.get("expires_at", 0) > now and not v.get("used")}
    if len(valid) != len(data):
        _save_json(password_resets_path(), valid)
    return valid


def _save_resets(resets: dict):
    _save_json(password_resets_path(), resets)


def request_password_reset(email: str) -> dict:
    """
    Generate a reset token and send email. Always returns {"ok": True}
    regardless of whether the email exists (prevents enumeration).
    """
    import os
    from api.email import send_reset_email

    email = email.strip().lower()
    user = _find_user_by_email(email)

    if user:
        token = secrets.token_urlsafe(32)
        resets = _load_resets()
        # Invalidate any existing unused tokens for this user
        resets = {k: v for k, v in resets.items() if v.get("user_id") != user["user_id"]}
        resets[token] = {
            "user_id": user["user_id"],
            "expires_at": time.time() + RESET_TTL,
            "used": False,
            "created": datetime.now(timezone.utc).isoformat(),
        }
        _save_resets(resets)

        app_url = os.environ.get("APP_URL", "http://localhost:3002").rstrip("/")
        reset_url = f"{app_url}/reset-password?token={token}"
        try:
            send_reset_email(email, reset_url)
        except Exception as e:
            print(f"[AUTH] Failed to send reset email to {email}: {e}")

    return {"ok": True}


def reset_password(token: str, new_password: str) -> dict:
    """
    Reset a user's password using a valid reset token.
    Raises ValueError on invalid/expired token or bad password.
    """
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters")

    resets = _load_resets()
    record = resets.get(token)
    if not record:
        raise ValueError("Invalid or expired reset link")

    if record.get("used"):
        raise ValueError("This reset link has already been used")

    if record.get("expires_at", 0) < time.time():
        raise ValueError("This reset link has expired")

    user_id = record["user_id"]
    users = _load_users()
    if user_id not in users:
        raise ValueError("Account not found")

    pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    users[user_id]["password_hash"] = pw_hash
    _save_users(users)

    # Mark token as used
    record["used"] = True
    _save_resets(resets)

    return {"ok": True}
