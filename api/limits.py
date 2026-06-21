"""Plan enforcement — scanner/officer tier limits and model selection."""

from api.auth import get_org, update_org
from api.case_manager import list_cases


# --- Tier definitions ---

TIERS = {
    "scanner": {
        "label": "Grant Scanner",
        "price_monthly": 0,
        "opportunities_per_cycle": {
            "high": 2,
            "medium": 2,
            "low": 1,
        },
        "max_open_cases": 3,
        "chat_messages_per_case": 5,
        "bots_bcd": False,
        "export_docx": False,
        "model": "claude-haiku-4-5-20251001",
    },
    "officer": {
        "label": "Grant Officer",
        "price_monthly": 9,
        "opportunities_per_cycle": None,  # unlimited
        "max_open_cases": -1,  # unlimited
        "chat_messages_per_case": -1,  # unlimited
        "bots_bcd": True,
        "export_docx": True,
        "model": "claude-sonnet-4-20250514",
    },
}

# Map old plan keys to new tier keys for backwards compat
_PLAN_TO_TIER = {
    "free": "scanner",
    "starter": "officer",
    "professional": "officer",
}


def get_tier_key(org_id: str) -> str:
    """Get the tier key (scanner/officer) for an org."""
    org = get_org(org_id)
    plan = org.get("plan", "free") if org else "free"
    return _PLAN_TO_TIER.get(plan, plan if plan in TIERS else "scanner")


def get_tier(org_id: str) -> dict:
    """Get the tier config for an org."""
    return TIERS.get(get_tier_key(org_id), TIERS["scanner"])


def toggle_tier(org_id: str) -> str:
    """Activate starter (officer) tier. Downgrades are handled by Stripe webhooks."""
    update_org(org_id, {"plan": "starter"})
    return "officer"


# --- Opportunity filtering ---

def filter_opportunities_for_tier(opportunities: list, org_id: str) -> list:
    """Filter opportunities based on tier limits.
    Scanner: prefers 2 high, 2 medium, 1 low — but always returns up to 5
    by backfilling from remaining opportunities if a priority bucket is short.
    Officer: all opportunities.
    """
    tier = get_tier(org_id)
    limits = tier["opportunities_per_cycle"]
    if limits is None:
        return opportunities  # officer gets all

    total_cap = sum(limits.values())

    # Pass 1: fill each priority bucket up to its cap
    counts = {"high": 0, "medium": 0, "low": 0}
    filtered = []
    remaining = []
    for opp in opportunities:
        priority = opp.get("priority", "medium")
        cap = limits.get(priority, 0)
        if counts.get(priority, 0) < cap:
            filtered.append(opp)
            counts[priority] = counts.get(priority, 0) + 1
        else:
            remaining.append(opp)

    # Pass 2: backfill from remaining opportunities if under total cap
    for opp in remaining:
        if len(filtered) >= total_cap:
            break
        filtered.append(opp)

    return filtered


# --- Cycle timer ---

def get_cycle_timer(org_id: str) -> dict | None:
    """Get the current cycle timer state. Returns {triggered_at, expires_at, remaining_seconds} or None."""
    from datetime import datetime, timezone, timedelta
    org = get_org(org_id)
    if not org:
        return None
    triggered_at = org.get("cycle_triggered_at")
    if not triggered_at:
        return None
    triggered = datetime.fromisoformat(triggered_at)
    expires = triggered + timedelta(days=7)
    now = datetime.now(timezone.utc)
    remaining = max(0, (expires - now).total_seconds())
    return {
        "triggered_at": triggered_at,
        "expires_at": expires.isoformat(),
        "remaining_seconds": int(remaining),
        "expired": remaining <= 0,
    }


def trigger_cycle(org_id: str) -> dict:
    """Trigger a new cycle. Returns the timer state."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    update_org(org_id, {"cycle_triggered_at": now})
    return get_cycle_timer(org_id)


def can_trigger_cycle(org_id: str) -> dict:
    """Check if the org can trigger a new cycle. Returns {allowed, message, timer}."""
    timer = get_cycle_timer(org_id)
    if timer is None:
        return {"allowed": True, "message": None, "timer": None}
    if timer["expired"]:
        return {"allowed": True, "message": None, "timer": timer}
    return {
        "allowed": False,
        "message": "Cycle already active. Next cycle available when timer expires.",
        "timer": timer,
    }


# --- Tailored Opportunities (paid tier feature) ---

def check_tailored_access(org_id: str) -> dict:
    """Gate for the Tailored Opportunities button. Composes:
      1. Tier check — must be Officer
      2. Toggle check — org.tailored_enabled must be True
      3. Cooldown check — 7-day timer from last trigger must be expired

    Returns {allowed: bool, reason: str|None, message: str, timer: dict|None}.
    Reasons (when not allowed): 'upgrade' | 'disabled' | 'cooldown'.
    """
    tier_key = get_tier_key(org_id)
    if tier_key != "officer":
        return {
            "allowed": False,
            "reason": "upgrade",
            "message": "Tailored Opportunities is an Officer-tier feature.",
            "timer": None,
        }
    org = get_org(org_id) or {}
    if not org.get("tailored_enabled"):
        return {
            "allowed": False,
            "reason": "disabled",
            "message": "Tailored Opportunities is off — enable it in settings to start running weekly cycles.",
            "timer": None,
        }
    timer = get_cycle_timer(org_id)
    if timer and not timer["expired"]:
        return {
            "allowed": False,
            "reason": "cooldown",
            "message": "Next cycle available when the 7-day timer expires.",
            "timer": timer,
        }
    return {
        "allowed": True,
        "reason": None,
        "message": None,
        "timer": timer,
    }


def set_tailored_enabled(org_id: str, enabled: bool) -> dict:
    """Officer-only setter for `org.tailored_enabled`."""
    tier_key = get_tier_key(org_id)
    if tier_key != "officer":
        raise PermissionError("Tailored Opportunities is Officer-tier only.")
    update_org(org_id, {"tailored_enabled": bool(enabled)})
    return {
        "tailored_enabled": bool(enabled),
        "tier": tier_key,
    }


# --- Case limit checks ---

def check_case_limit(org_id: str) -> dict:
    """Check if the org can open another case. Returns {allowed, active, limit, message}."""
    tier = get_tier(org_id)
    limit = tier["max_open_cases"]
    if limit == -1:
        return {"allowed": True, "active": 0, "limit": -1, "message": None}

    cases = list_cases(org_id)
    active = len([c for c in cases if c["status"] == "open"])
    allowed = active < limit
    return {
        "allowed": allowed,
        "active": active,
        "limit": limit,
        "message": f"Case limit reached ({active}/{limit}). Upgrade to Officer for unlimited cases." if not allowed else None,
    }


def check_chat_limit(org_id: str, case_id: str) -> dict:
    """Check if the case has chat messages remaining."""
    tier = get_tier(org_id)
    limit = tier["chat_messages_per_case"]
    if limit == -1:
        return {"allowed": True, "used": 0, "limit": -1, "message": None}

    from api.case_manager import load_case
    case = load_case(org_id, case_id)
    if not case:
        return {"allowed": False, "used": 0, "limit": limit, "message": "Case not found"}

    user_messages = len([m for m in case.get("conversation", []) if m["role"] == "officer"])
    allowed = user_messages < limit
    return {
        "allowed": allowed,
        "used": user_messages,
        "limit": limit,
        "message": f"Chat limit reached ({user_messages}/{limit}). Upgrade to Officer for unlimited." if not allowed else None,
    }


def check_feature_access(org_id: str, feature: str) -> dict:
    """Check if the org has access to a feature."""
    tier = get_tier(org_id)
    tier_key = get_tier_key(org_id)

    feature_checks = {
        "bots_bcd": tier.get("bots_bcd", False),
        "export_docx": tier.get("export_docx", False),
    }

    allowed = feature_checks.get(feature, True)
    return {
        "allowed": allowed,
        "tier": tier_key,
        "message": f"This feature requires Grant Officer. You're on {tier['label']}." if not allowed else None,
    }


# --- Model selection ---

def get_model_for_org(org_id: str) -> str:
    """Get the AI model for this org's tier."""
    tier = get_tier(org_id)
    return tier["model"]


def get_model_overrides(org_id: str) -> dict:
    """Get model overrides for the cycle runner based on tier."""
    model = get_model_for_org(org_id)
    return {
        "grant_watcher": model,
        "grant_analyst": model,
        "grant_reporter": "claude-haiku-4-5-20251001",
    }
