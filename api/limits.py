"""Plan enforcement — tier limits and model selection."""

from api.auth import get_org
from api.usage_log import count_cycles_this_month
from api.case_manager import list_cases


# --- Plan definitions ---

PLANS = {
    "free": {
        "label": "Free",
        "price_monthly": 0,
        "cycles_per_month": 2,
        "active_cases": 1,
        "chat_messages_per_case": 5,
        "bots_bcd": False,
        "export_docx": False,
        "evidence_retention_days": 30,
        "model": "claude-haiku-4-5-20251001",
    },
    "starter": {
        "label": "Starter",
        "price_monthly": 49,
        "cycles_per_month": 30,
        "active_cases": 5,
        "chat_messages_per_case": 50,
        "bots_bcd": True,
        "export_docx": True,
        "evidence_retention_days": 90,
        "model": "claude-haiku-4-5-20251001",
    },
    "professional": {
        "label": "Professional",
        "price_monthly": 149,
        "cycles_per_month": 30,
        "active_cases": -1,  # unlimited
        "chat_messages_per_case": -1,  # unlimited
        "bots_bcd": True,
        "export_docx": True,
        "evidence_retention_days": 365,
        "model": "claude-sonnet-4-20250514",
    },
}


def get_plan(org_id: str) -> dict:
    """Get the plan config for an org."""
    org = get_org(org_id)
    plan_key = org.get("plan", "free") if org else "free"
    return PLANS.get(plan_key, PLANS["free"])


def get_plan_key(org_id: str) -> str:
    """Get the plan key (free/starter/professional) for an org."""
    org = get_org(org_id)
    return org.get("plan", "free") if org else "free"


# --- Limit checks ---

def check_cycle_limit(org_id: str) -> dict:
    """Check if the org can run another cycle this month. Returns {allowed, used, limit, message}."""
    plan = get_plan(org_id)
    limit = plan["cycles_per_month"]
    used = count_cycles_this_month(org_id)
    allowed = used < limit
    return {
        "allowed": allowed,
        "used": used,
        "limit": limit,
        "message": f"Cycle limit reached ({used}/{limit}). Upgrade for daily scanning." if not allowed else None,
    }


def check_case_limit(org_id: str) -> dict:
    """Check if the org can create another active case. Returns {allowed, active, limit, message}."""
    plan = get_plan(org_id)
    limit = plan["active_cases"]
    if limit == -1:
        return {"allowed": True, "active": 0, "limit": -1, "message": None}

    cases = list_cases(org_id)
    active = len([c for c in cases if c["status"] == "open"])
    allowed = active < limit
    return {
        "allowed": allowed,
        "active": active,
        "limit": limit,
        "message": f"Active case limit reached ({active}/{limit}). Upgrade for more cases." if not allowed else None,
    }


def check_chat_limit(org_id: str, case_id: str) -> dict:
    """Check if the case has chat messages remaining. Returns {allowed, used, limit, message}."""
    plan = get_plan(org_id)
    limit = plan["chat_messages_per_case"]
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
        "message": f"Chat limit reached ({user_messages}/{limit} messages). Upgrade for more." if not allowed else None,
    }


def check_feature_access(org_id: str, feature: str) -> dict:
    """Check if the org has access to a feature. Returns {allowed, message}."""
    plan = get_plan(org_id)
    plan_key = get_plan_key(org_id)

    feature_checks = {
        "bots_bcd": plan.get("bots_bcd", False),
        "export_docx": plan.get("export_docx", False),
    }

    allowed = feature_checks.get(feature, True)
    return {
        "allowed": allowed,
        "plan": plan_key,
        "message": f"This feature requires a paid plan. You're on {plan['label']}." if not allowed else None,
    }


# --- Model selection ---

def get_model_for_org(org_id: str) -> str:
    """Get the AI model for this org's plan tier."""
    plan = get_plan(org_id)
    return plan["model"]


def get_model_overrides(org_id: str) -> dict:
    """Get model overrides for the cycle runner based on plan tier."""
    model = get_model_for_org(org_id)
    return {
        "grant_watcher": model,
        "grant_analyst": model,
        "grant_reporter": "claude-haiku-4-5-20251001",  # Reporter always uses Haiku (cheap)
    }
