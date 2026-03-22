"""
Test script: cycle pipeline — Watcher → Analyst → Reporter evidence handoff.

Validates the fix for the bug where Watcher evidence was not passed to the
Analyst, causing empty reports.

Use cases:
  1. Watcher evidence files are created in the correct directory
  2. Analyst system prompt contains "Evidence From Prior Phases" section
  3. Analyst prompt includes evidence content, not just raw_text
  4. Reporter receives both Watcher and Analyst outputs
  5. Final report contains actual opportunity names from evidence
  6. Agent runner accumulates text across iterations (not just last turn)

Usage:
    python3 tests/test_cycle_pipeline.py
    python3 tests/test_cycle_pipeline.py --case 1
"""

import os
import sys
import json
import importlib

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

PASS = 0
FAIL = 0


def log(msg, indent=0):
    print("  " * indent + msg)


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        log(f"[PASS] {label}")
    else:
        FAIL += 1
        log(f"[FAIL] {label}" + (f"  — {detail}" if detail else ""))
    return condition


# ---------------------------------------------------------------------------
# Case 1 — Evidence files land in the correct directory
# ---------------------------------------------------------------------------
def case_1():
    log("\n=== Case 1: Evidence directory structure ===")
    from orchestrator.evidence import save_evidence, load_evidence_for_date
    from api.tenant import org_evidence_dir, ensure_org_dirs

    org_id = "_test_pipeline_ev"
    test_date = "2099-01-01"
    ensure_org_dirs(org_id)

    item = {
        "title": "Test Grant Alpha",
        "type": "grant_opportunity",
        "severity": "high",
        "content": "A test grant worth $50,000.",
        "source_url": "https://example.com/grant-alpha",
    }
    record = save_evidence(org_id, "grant_watcher", test_date, item)

    ev_dir = os.path.join(org_evidence_dir(org_id), test_date, "grant_watcher")
    check("Evidence directory created", os.path.isdir(ev_dir))

    ev_file = os.path.join(ev_dir, f"{record['id']}.json")
    check("Evidence file exists", os.path.isfile(ev_file))

    loaded = load_evidence_for_date(org_id, test_date)
    check("Evidence loadable by date", len(loaded) >= 1)
    check("Evidence has correct title", any(e.get("title") == "Test Grant Alpha" for e in loaded))
    check("Evidence has SHA-256 hash", "hash" in record and len(record["hash"]) == 64)

    # Cleanup
    import shutil
    shutil.rmtree(os.path.join(PROJECT_ROOT, "orgs", org_id), ignore_errors=True)


# ---------------------------------------------------------------------------
# Case 2 — Analyst prompt contains Evidence From Prior Phases
# ---------------------------------------------------------------------------
def case_2():
    log("\n=== Case 2: Analyst prompt includes evidence section ===")
    from orchestrator.main import _load_prompt
    from orchestrator.evidence import save_evidence
    from api.tenant import ensure_org_dirs, org_prompts_dir
    import shutil

    org_id = "_test_pipeline_prompt"
    test_date = "2099-01-02"
    ensure_org_dirs(org_id)

    # Copy prompt templates so _load_prompt can find them
    prompts_dir = org_prompts_dir(org_id)
    template_dir = os.path.join(PROJECT_ROOT, "templates", "prompts")
    for fname in ["grant_watcher.md", "grant_analyst.md", "grant_reporter.md"]:
        src = os.path.join(template_dir, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(prompts_dir, fname))

    # Save evidence as if Watcher already ran
    save_evidence(org_id, "grant_watcher", test_date, {
        "title": "DOC Community Fund",
        "type": "grant_opportunity",
        "severity": "high",
        "content": "DOC Community Fund opens 31 March, $9.2M available.",
        "source_url": "https://doc.govt.nz/community-fund",
    })
    save_evidence(org_id, "grant_watcher", test_date, {
        "title": "Fonterra Wetland Grants",
        "type": "grant_opportunity",
        "severity": "high",
        "content": "Fonterra offering $750k over 3 years for wetland restoration.",
        "source_url": "https://landcare.org.nz/fonterra",
    })

    # Build analyst config
    analyst_config = {
        "id": "grant_analyst",
        "name": "Grant Analyst",
        "prompt_file": "grant_analyst.md",
        "depends_on": ["grant_watcher"],
        "tools": ["save_evidence"],
        "model": "claude-haiku-4-5-20251001",
        "max_iterations": 8,
    }

    prior_outputs = {
        "grant_watcher": {
            "raw_text": "Found several Canterbury-specific opportunities.",
            "agent_id": "grant_watcher",
        }
    }

    prompt = _load_prompt(org_id, analyst_config, {}, prior_outputs, test_date)

    check("Prompt contains 'Evidence From Prior Phases'",
          "Evidence From Prior Phases" in prompt)
    check("Prompt contains DOC Community Fund evidence",
          "DOC Community Fund" in prompt)
    check("Prompt contains Fonterra evidence",
          "Fonterra Wetland Grants" in prompt)
    check("Prompt contains evidence source URL",
          "doc.govt.nz" in prompt)
    check("Prompt contains evidence content text",
          "$9.2M available" in prompt)

    # Cleanup
    shutil.rmtree(os.path.join(PROJECT_ROOT, "orgs", org_id), ignore_errors=True)


# ---------------------------------------------------------------------------
# Case 3 — Analyst gets evidence content, not just raw_text
# ---------------------------------------------------------------------------
def case_3():
    log("\n=== Case 3: Evidence content injected, not just raw_text ===")
    from orchestrator.main import _load_prompt
    from orchestrator.evidence import save_evidence
    from api.tenant import ensure_org_dirs, org_prompts_dir
    import shutil

    org_id = "_test_pipeline_content"
    test_date = "2099-01-03"
    ensure_org_dirs(org_id)

    prompts_dir = org_prompts_dir(org_id)
    template_dir = os.path.join(PROJECT_ROOT, "templates", "prompts")
    for fname in ["grant_analyst.md"]:
        src = os.path.join(template_dir, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(prompts_dir, fname))

    save_evidence(org_id, "grant_watcher", test_date, {
        "title": "Unique Grant Opportunity XYZ-789",
        "type": "grant_opportunity",
        "severity": "medium",
        "content": "Detailed description of grant XYZ-789 worth $25,000 for community projects.",
        "source_url": "https://example.com/xyz-789",
    })

    # Simulate watcher output that is just a fragment (the bug scenario)
    prior_outputs = {
        "grant_watcher": {
            "raw_text": "Excellent findings! Let me save these opportunities:",
            "agent_id": "grant_watcher",
        }
    }

    analyst_config = {
        "id": "grant_analyst",
        "name": "Grant Analyst",
        "prompt_file": "grant_analyst.md",
        "depends_on": ["grant_watcher"],
        "tools": ["save_evidence"],
        "model": "claude-haiku-4-5-20251001",
        "max_iterations": 8,
    }

    prompt = _load_prompt(org_id, analyst_config, {}, prior_outputs, test_date)

    # The raw_text is empty fluff — but evidence content should be present
    check("Prompt contains evidence title",
          "Unique Grant Opportunity XYZ-789" in prompt)
    check("Prompt contains evidence dollar amount",
          "$25,000" in prompt)
    check("Prompt contains evidence source URL",
          "example.com/xyz-789" in prompt)
    check("Prompt still includes raw_text section",
          "Outputs From Prior Phases" in prompt)
    check("raw_text fragment is in prompt",
          "Excellent findings" in prompt)

    shutil.rmtree(os.path.join(PROJECT_ROOT, "orgs", org_id), ignore_errors=True)


# ---------------------------------------------------------------------------
# Case 4 — Reporter receives both Watcher and Analyst outputs
# ---------------------------------------------------------------------------
def case_4():
    log("\n=== Case 4: Reporter gets both prior phase outputs ===")
    from orchestrator.main import _load_prompt
    from orchestrator.evidence import save_evidence
    from api.tenant import ensure_org_dirs, org_prompts_dir
    import shutil

    org_id = "_test_pipeline_reporter"
    test_date = "2099-01-04"
    ensure_org_dirs(org_id)

    prompts_dir = org_prompts_dir(org_id)
    template_dir = os.path.join(PROJECT_ROOT, "templates", "prompts")
    for fname in ["grant_reporter.md"]:
        src = os.path.join(template_dir, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(prompts_dir, fname))

    # Save evidence from both watcher and analyst
    save_evidence(org_id, "grant_watcher", test_date, {
        "title": "Watcher Found Grant",
        "type": "grant_opportunity",
        "severity": "high",
        "content": "Grant from watcher phase.",
        "source_url": "https://example.com/watcher",
    })
    save_evidence(org_id, "grant_analyst", test_date, {
        "title": "Analyst Scored Grant",
        "type": "assessment",
        "severity": "high",
        "content": "Analyst assessment: APPLY recommended.",
        "source_url": "https://example.com/analyst",
    })

    reporter_config = {
        "id": "grant_reporter",
        "name": "Grant Reporter",
        "prompt_file": "grant_reporter.md",
        "depends_on": ["grant_watcher", "grant_analyst"],
        "tools": [],
        "model": "claude-haiku-4-5-20251001",
        "max_iterations": 1,
    }

    prior_outputs = {
        "grant_watcher": {"raw_text": "Found one high-priority grant.", "agent_id": "grant_watcher"},
        "grant_analyst": {"raw_text": "Scored: APPLY recommended.", "agent_id": "grant_analyst"},
    }

    prompt = _load_prompt(org_id, reporter_config, {}, prior_outputs, test_date)

    check("Reporter prompt includes watcher raw_text",
          "Found one high-priority grant" in prompt)
    check("Reporter prompt includes analyst raw_text",
          "Scored: APPLY recommended" in prompt)
    check("Reporter prompt includes watcher evidence",
          "Watcher Found Grant" in prompt)
    check("Reporter prompt includes analyst evidence",
          "Analyst Scored Grant" in prompt)

    shutil.rmtree(os.path.join(PROJECT_ROOT, "orgs", org_id), ignore_errors=True)


# ---------------------------------------------------------------------------
# Case 5 — Report contains opportunity names from evidence
# ---------------------------------------------------------------------------
def case_5():
    log("\n=== Case 5: Report text references evidence (structural check) ===")

    # Check that the _load_prompt function injects evidence titles and content
    # so the reporter can reference them. We verify the prompt structure, not
    # actual LLM output (that requires a live call).
    from orchestrator.main import _load_prompt
    from orchestrator.evidence import save_evidence
    from api.tenant import ensure_org_dirs, org_prompts_dir
    import shutil

    org_id = "_test_pipeline_report"
    test_date = "2099-01-05"
    ensure_org_dirs(org_id)

    prompts_dir = org_prompts_dir(org_id)
    template_dir = os.path.join(PROJECT_ROOT, "templates", "prompts")
    for fname in ["grant_reporter.md"]:
        src = os.path.join(template_dir, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(prompts_dir, fname))

    titles = [
        "DOC Community Fund 2026",
        "Fonterra Wetland Partnership",
        "Westpac Water Care Project",
    ]
    for i, title in enumerate(titles):
        save_evidence(org_id, "grant_watcher", test_date, {
            "title": title,
            "type": "grant_opportunity",
            "severity": ["high", "high", "medium"][i],
            "content": f"Details for {title}.",
            "source_url": f"https://example.com/{i}",
        })

    reporter_config = {
        "id": "grant_reporter",
        "name": "Grant Reporter",
        "prompt_file": "grant_reporter.md",
        "depends_on": ["grant_watcher", "grant_analyst"],
        "tools": [],
        "model": "claude-haiku-4-5-20251001",
        "max_iterations": 1,
    }
    prior_outputs = {
        "grant_watcher": {"raw_text": "Scanned successfully.", "agent_id": "grant_watcher"},
        "grant_analyst": {"raw_text": "Analysis complete.", "agent_id": "grant_analyst"},
    }

    prompt = _load_prompt(org_id, reporter_config, {}, prior_outputs, test_date)

    for title in titles:
        check(f"Reporter prompt contains '{title}'", title in prompt)

    check("Reporter prompt contains severity labels",
          "high" in prompt.lower() and "medium" in prompt.lower())

    shutil.rmtree(os.path.join(PROJECT_ROOT, "orgs", org_id), ignore_errors=True)


# ---------------------------------------------------------------------------
# Case 6 — Agent runner accumulates text across iterations
# ---------------------------------------------------------------------------
def case_6():
    log("\n=== Case 6: Agent runner text accumulation ===")

    # Verify the agent_runner code accumulates text instead of overwriting
    runner_path = os.path.join(PROJECT_ROOT, "orchestrator", "agent_runner.py")
    with open(runner_path) as f:
        source = f.read()

    # The fix changed `final_text = "\n".join(text_parts)` to
    # `final_text += ("\n" if final_text else "") + "\n".join(text_parts)`
    check("Agent runner uses += for text accumulation",
          "+=" in source and "final_text +=" in source,
          "Expected 'final_text +=' pattern for accumulation")
    check("Agent runner does not overwrite final_text",
          'final_text = "\\n".join(text_parts)' not in source,
          "Found old overwrite pattern — text will be lost between iterations")


# ---------------------------------------------------------------------------

def main():
    cases = {1: case_1, 2: case_2, 3: case_3, 4: case_4, 5: case_5, 6: case_6}

    selected = None
    if "--case" in sys.argv:
        idx = sys.argv.index("--case")
        if idx + 1 < len(sys.argv):
            selected = int(sys.argv[idx + 1])

    if selected:
        if selected in cases:
            cases[selected]()
        else:
            print(f"Unknown case {selected}. Available: {list(cases.keys())}")
            sys.exit(1)
    else:
        for fn in cases.values():
            fn()

    log(f"\n{'='*40}")
    log(f"PASS: {PASS}  FAIL: {FAIL}")
    log(f"{'='*40}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
