"""Daily LLM budget per organisation — the one workspace limit that stays.

With the workspace free and unlimited, this is the cost guard: an org may
start LLM work until it has used DAILY_LLM_CALL_BUDGET calls today. It is not
a tier; every account gets the same budget.

Counting reuses the usage log (api/usage_log.py) rather than a second ledger:
every Anthropic call made for an org is logged there with its `api_calls`
count, in a file per local day. The day is the deployment country's local
day (country config `timezone`), the same boundary the usage log files use,
so the budget resets at local midnight.

The check runs before work starts, so a single request that makes many calls
(a tailored cycle, Bot C filling many sections) can finish past the budget;
the next request is then refused. Sweep orgs are exempt.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from api.country_config import get_country_config
from api.usage_log import read_usage
from orchestrator.sweep_evidence import is_sweep_org_id

DEFAULT_DAILY_LLM_CALL_BUDGET = 200

BUDGET_EXHAUSTED_MESSAGE = (
    "Daily limit reached: your organisation has used today's allowance of AI "
    "requests. It resets tomorrow. Nothing is lost: your cases, drafts and "
    "uploads are all saved."
)


def daily_llm_call_budget() -> int:
    """The per-org daily cap, read from DAILY_LLM_CALL_BUDGET at call time.
    Unset or unparseable means the default; 0 or negative disables the guard."""
    raw = os.environ.get("DAILY_LLM_CALL_BUDGET", "").strip()
    if not raw:
        return DEFAULT_DAILY_LLM_CALL_BUDGET
    try:
        return int(raw)
    except ValueError:
        print(f"[llm_budget] Ignoring invalid DAILY_LLM_CALL_BUDGET={raw!r}")
        return DEFAULT_DAILY_LLM_CALL_BUDGET


def local_date(now: datetime | None = None) -> str:
    """YYYY-MM-DD in the country's timezone. `now` must be timezone-aware."""
    tz = ZoneInfo(get_country_config().timezone)
    return (now or datetime.now(tz)).astimezone(tz).strftime("%Y-%m-%d")


def llm_calls_today(org_id: str, now: datetime | None = None) -> int:
    """Sum of `api_calls` in the org's usage log for the local day."""
    return sum(int(r.get("api_calls") or 0) for r in read_usage(org_id, local_date(now)))


def check_llm_budget(org_id: str, now: datetime | None = None) -> dict:
    """Returns {allowed, used, limit, message}. `limit` is -1 when the guard
    is off or the org is exempt."""
    limit = daily_llm_call_budget()
    if limit <= 0 or is_sweep_org_id(org_id):
        return {"allowed": True, "used": 0, "limit": -1, "message": None}
    used = llm_calls_today(org_id, now)
    allowed = used < limit
    return {
        "allowed": allowed,
        "used": used,
        "limit": limit,
        "message": None if allowed else BUDGET_EXHAUSTED_MESSAGE,
    }
