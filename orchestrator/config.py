"""Bot configuration — the only file you edit to add/remove agents."""

# --- Per-org cycle (legacy) ---
MODEL_INSPECTOR = "claude-haiku-4-5-20251001"
MODEL_ANALYST = "claude-haiku-4-5-20251001"
MODEL_REPORTER = "claude-haiku-4-5-20251001"

# --- Country sweep (search-box pivot) ---
# Heavier than the per-org cycle: more iterations, all Haiku for now.
# (Originally specced Sonnet on Analyst; switched to Haiku to keep first-run
# spend tight. Revisit if dedupe/normalisation quality is poor.)
MODEL_WATCHER_COUNTRY = "claude-haiku-4-5-20251001"
MODEL_ANALYST_COUNTRY = "claude-haiku-4-5-20251001"
MODEL_REPORTER_COUNTRY = "claude-haiku-4-5-20251001"

WATCHER_ITERATIONS_COUNTRY = 16  # per sector worker; 11 sectors → ~176 total max
ANALYST_ITERATIONS_COUNTRY = 8
REPORTER_MAX_TOKENS_COUNTRY = 16384  # the pool can be large

AGENTS = [
    # Phase 1 — Watcher (parallel, cheap)
    {
        "id": "grant_watcher",
        "name": "Grant Watcher",
        "prompt_file": "prompts/grant_watcher.md",
        "phase": 1,
        "depends_on": [],
        "model": MODEL_INSPECTOR,
        "tools": ["web_fetch", "web_search", "save_evidence"],
        "max_iterations": 16,
    },
    # Phase 2 — Analyst (depends on watcher output)
    {
        "id": "grant_analyst",
        "name": "Grant Analyst",
        "prompt_file": "prompts/grant_analyst.md",
        "phase": 2,
        "depends_on": ["grant_watcher"],
        "model": MODEL_ANALYST,
        "tools": ["save_evidence"],
        "max_iterations": 10,
    },
    # Phase 3 — Reporter (compiles brief)
    {
        "id": "grant_reporter",
        "name": "Grant Reporter",
        "prompt_file": "prompts/grant_reporter.md",
        "phase": 3,
        "depends_on": ["grant_watcher", "grant_analyst"],
        "model": MODEL_REPORTER,
        "max_tokens": 4096,
        "tools": [],
        "max_iterations": 1,
    },
]

# Group agents by phase for the orchestrator
def agents_by_phase():
    phases = {}
    for agent in AGENTS:
        phases.setdefault(agent["phase"], []).append(agent)
    return dict(sorted(phases.items()))
