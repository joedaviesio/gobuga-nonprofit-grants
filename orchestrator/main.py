"""Cycle engine — runs the daily loop (multi-tenant)."""

import json
import os
from datetime import date as date_type
from concurrent.futures import ThreadPoolExecutor, as_completed

from orchestrator.config import agents_by_phase, AGENTS
from orchestrator.agent_runner import run_agent
from orchestrator.state_manager import load_all_state, save_state
from orchestrator.evidence import load_evidence_for_date
from orchestrator.signoff import load_signoff

from api.tenant import org_root, org_cycles_dir, org_data_dir, org_prompts_dir
from api.file_extraction import (
    extract_text,
    extract_pdf,
    extract_docx,
    extract_doc,
    extract_xlsx,
    extract_xls,
    strip_html,
)

# Backward-compatible aliases so existing ``from orchestrator.main import _extract_*``
# statements elsewhere continue to work during migration.
_extract_pdf = extract_pdf
_extract_docx = extract_docx
_extract_doc = extract_doc
_extract_xlsx = extract_xlsx
_extract_xls = extract_xls
_strip_html = strip_html


def _load_prompt(org_id: str, agent_config: dict, state: dict, prior_outputs: dict, date: str) -> str:
    """Build the full system prompt for an agent."""
    # Load prompt from org-specific prompts dir, fall back to templates
    prompt_filename = os.path.basename(agent_config["prompt_file"])
    prompt_path = os.path.join(org_prompts_dir(org_id), prompt_filename)
    if not os.path.exists(prompt_path):
        # Fall back to base templates
        project_root = os.path.dirname(os.path.dirname(__file__))
        prompt_path = os.path.join(project_root, "templates", "prompts", prompt_filename)

    with open(prompt_path) as f:
        role_brief = f.read()

    # Load previous signoff if it exists
    signoff = load_signoff(org_id, date)

    parts = [
        role_brief,
        "\n\n---\n## Current State\n```json\n" + json.dumps(state, indent=2, default=str) + "\n```",
    ]

    if signoff:
        parts.append("\n\n---\n## Previous Signoff Decisions\n```json\n" + json.dumps(signoff, indent=2) + "\n```")

    # Include outputs from dependency agents
    deps = agent_config.get("depends_on", [])
    if deps:
        dep_section = "\n\n---\n## Outputs From Prior Phases\n"
        for dep_id in deps:
            if dep_id in prior_outputs:
                dep_section += f"\n### {dep_id}\n```\n{prior_outputs[dep_id].get('raw_text', 'No output')}\n```\n"
        parts.append(dep_section)

        # Include evidence saved by dependency agents
        dep_evidence = load_evidence_for_date(org_id, date)
        dep_agent_ids = set(deps)
        relevant = [e for e in dep_evidence if e.get("agent") in dep_agent_ids]
        if relevant:
            ev_section = "\n\n---\n## Evidence From Prior Phases\n"
            for ev in relevant:
                ev_section += (
                    f"\n### {ev.get('id', 'unknown')} — {ev.get('title', 'Untitled')}\n"
                    f"**Agent:** {ev.get('agent')} | **Severity:** {ev.get('severity', 'unknown')} | **Type:** {ev.get('type', 'unknown')}\n"
                    f"**Source:** {ev.get('source_url', 'N/A')}\n"
                    f"```\n{ev.get('content', 'No content')}\n```\n"
                )
            parts.append(ev_section)

    # Include input data files
    data_section = _load_input_data(org_id)
    if data_section:
        parts.append(data_section)

    return "\n".join(parts)


def _load_input_data(org_id: str) -> str:
    """Load all text-based and HTML files from org data/ subfolders into a context section."""
    data_dir = org_data_dir(org_id)
    if not os.path.exists(data_dir):
        return ""

    SUPPORTED = {".md", ".txt", ".json", ".csv", ".html", ".htm",
                 ".pdf", ".docx", ".doc", ".xlsx", ".xls"}
    MAX_PER_FILE = 8000
    SKIP_DIRS = {"archive"}

    sections = []
    for dirpath, dirnames, filenames in sorted(os.walk(data_dir)):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, data_dir)
        if rel_dir == ".":
            continue

        files_content = []
        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED:
                continue
            filepath = os.path.join(dirpath, fname)
            try:
                content = extract_text(filepath)
                if not content:
                    continue
                if len(content) > MAX_PER_FILE:
                    content = content[:MAX_PER_FILE] + f"\n... [truncated, {len(content)} chars total]"
                files_content.append(f"#### {fname}\n```\n{content}\n```")
            except Exception:
                continue

        if files_content:
            sections.append(f"### {rel_dir}/\n" + "\n\n".join(files_content))

    if not sections:
        return ""

    return "\n\n---\n## Input Data\n\n" + "\n\n".join(sections)


def run_cycle(org_id: str, cycle_date: str | None = None, model_overrides: dict | None = None, on_phase=None):
    """Run a full cycle for the given org and date."""
    if cycle_date is None:
        cycle_date = date_type.today().isoformat()

    print(f"\n{'='*60}")
    print(f"  Grant Cycle — {cycle_date} — org: {org_id}")
    print(f"{'='*60}\n")

    state = load_all_state(org_id)
    phases = agents_by_phase()
    all_outputs = {}

    # Apply model overrides if provided (e.g. for tier-based model selection)
    agents = []
    for phase_num, phase_agents in phases.items():
        for agent_cfg in phase_agents:
            cfg = dict(agent_cfg)
            if model_overrides and cfg["id"] in model_overrides:
                cfg["model"] = model_overrides[cfg["id"]]
            agents.append((phase_num, cfg))

    # Re-group by phase
    phase_groups = {}
    for phase_num, cfg in agents:
        phase_groups.setdefault(phase_num, []).append(cfg)

    phase_names = {1: "watcher", 2: "analyst", 3: "reporter"}

    for phase_num in sorted(phase_groups.keys()):
        phase_agents = phase_groups[phase_num]
        print(f"--- Phase {phase_num} ---")

        if on_phase:
            on_phase(phase_names.get(phase_num, f"phase_{phase_num}"))

        if phase_num == 1:
            with ThreadPoolExecutor(max_workers=len(phase_agents)) as executor:
                futures = {}
                for agent_cfg in phase_agents:
                    prompt = _load_prompt(org_id, agent_cfg, state, all_outputs, cycle_date)
                    future = executor.submit(run_agent, org_id, agent_cfg, prompt, cycle_date)
                    futures[future] = agent_cfg

                for future in as_completed(futures):
                    agent_cfg = futures[future]
                    try:
                        output = future.result()
                        all_outputs[agent_cfg["id"]] = output
                        print(f"  ✓ {agent_cfg['name']} complete")
                    except Exception as e:
                        print(f"  ✗ {agent_cfg['name']} failed: {e}")
                        all_outputs[agent_cfg["id"]] = {"raw_text": f"Error: {e}", "agent_id": agent_cfg["id"]}

            # Retry watcher once if it produced zero evidence
            watcher_evidence = load_evidence_for_date(org_id, cycle_date)
            watcher_ids = {cfg["id"] for cfg in phase_agents}
            watcher_items = [e for e in watcher_evidence if e.get("agent") in watcher_ids]
            if not watcher_items:
                print("  ⚠ Watcher produced zero evidence — retrying once...")
                for agent_cfg in phase_agents:
                    try:
                        prompt = _load_prompt(org_id, agent_cfg, state, all_outputs, cycle_date)
                        output = run_agent(org_id, agent_cfg, prompt, cycle_date)
                        all_outputs[agent_cfg["id"]] = output
                        print(f"  ✓ {agent_cfg['name']} retry complete")
                    except Exception as e:
                        print(f"  ✗ {agent_cfg['name']} retry failed: {e}")
        else:
            for agent_cfg in phase_agents:
                print(f"  Running {agent_cfg['name']}...")
                try:
                    prompt = _load_prompt(org_id, agent_cfg, state, all_outputs, cycle_date)
                    output = run_agent(org_id, agent_cfg, prompt, cycle_date)
                    all_outputs[agent_cfg["id"]] = output
                    print(f"  ✓ {agent_cfg['name']} complete")
                except Exception as e:
                    print(f"  ✗ {agent_cfg['name']} failed: {e}")
                    all_outputs[agent_cfg["id"]] = {"raw_text": f"Error: {e}", "agent_id": agent_cfg["id"]}

    # Save the report
    if on_phase:
        on_phase("saving")
    reporter_output = all_outputs.get("grant_reporter", {})
    report_text = reporter_output.get("raw_text", "No report generated.")

    # Archive cycle
    from datetime import datetime, timezone
    run_ts = datetime.now(timezone.utc).strftime("%H%M%S")
    cycles_dir = org_cycles_dir(org_id)
    cycle_dir = os.path.join(cycles_dir, cycle_date, f"run-{run_ts}")
    os.makedirs(cycle_dir, exist_ok=True)

    with open(os.path.join(cycle_dir, "report.md"), "w") as f:
        f.write(report_text)

    with open(os.path.join(cycle_dir, "outputs.json"), "w") as f:
        json.dump(all_outputs, f, indent=2, default=str)

    evidence = load_evidence_for_date(org_id, cycle_date)
    with open(os.path.join(cycle_dir, "evidence.json"), "w") as f:
        json.dump(evidence, f, indent=2)

    # Write a "latest" symlink
    latest_dir = os.path.join(cycles_dir, cycle_date, "latest")
    if os.path.islink(latest_dir):
        os.unlink(latest_dir)
    elif os.path.isdir(latest_dir):
        import shutil
        shutil.rmtree(latest_dir)
    os.symlink(f"run-{run_ts}", latest_dir)

    print(f"\n--- Cycle Complete ---")
    print(f"Report: {cycle_dir}/report.md")
    print(f"Evidence items: {len(evidence)}")

    return report_text
