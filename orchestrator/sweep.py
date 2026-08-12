"""Country sweep — once-a-month exhaustive grant collection per country.

Replaces the per-org cycle for discovery. Produces a single shared
`opportunities.json` at `platform/cycles/<country>/<month>/latest/`.

CLI:
    python -m orchestrator.sweep nz 2026-04           # real run
    python -m orchestrator.sweep nz 2026-04 --dry-run # plan only, no API calls
"""

import tirith  # noqa: F401  — must come before anthropic; routes calls through local tirith proxy

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from api.opportunities import (
    build_opportunities_from_watchers,
    latest_available_month,
    live_only,
    load_pool,
    merge_with_previous,
    previous_month,
)
from api.sources import (
    load_manifest,
    must_fetch_for_sector,
    seeds_for_sector,
    seeds_for_urban_centre,
    validate_coverage,
)
from api.country_config import get_country_config
from api.tenant import (
    ensure_platform_dirs,
    platform_cycles_dir,
    platform_latest_path,
    platform_run_dir,
)
from orchestrator.agent_runner import run_agent
from orchestrator.config import (
    ANALYST_ITERATIONS_COUNTRY,
    MODEL_ANALYST_COUNTRY,
    MODEL_REPORTER_COUNTRY,
    MODEL_WATCHER_COUNTRY,
    REPORTER_MAX_TOKENS_COUNTRY,
    WATCHER_ITERATIONS_COUNTRY,
)
from orchestrator.deadline_resolver import resolve_deadlines_for_rows
from orchestrator.sweep_evidence import load_sweep_evidence, make_sweep_org_id


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
PROMPTS_DIR = os.path.join(PROJECT_ROOT, "templates", "prompts")

WATCHER_PROMPT = os.path.join(PROMPTS_DIR, "grant_watcher_country.md")
URBAN_WATCHER_PROMPT = os.path.join(PROMPTS_DIR, "grant_watcher_urban.md")
ANALYST_PROMPT = os.path.join(PROMPTS_DIR, "grant_analyst_country.md")
REPORTER_PROMPT = os.path.join(PROMPTS_DIR, "grant_reporter_country.md")


# --- Prompt building -------------------------------------------------------

def _format_seed_sources(country: str, sector_id: str) -> tuple[str, str]:
    """Render two source lists for a sector worker's system prompt:

    1. MUST FETCH — must_appear funder URLs whose `sectors` includes this sector.
       The watcher is required to web_fetch every one of these before exiting.
    2. REFERENCE — every other seed source relevant to this sector or universal.
    """
    must = must_fetch_for_sector(country, sector_id)
    reference = seeds_for_sector(country, sector_id)
    must_urls = {f["url"] for f in must}
    must_lines = [
        f"- {f['name']} — {f['url']}"
        for f in must
    ]
    ref_lines = []
    for src in reference:
        if src["url"] in must_urls:
            continue
        line = f"- {src['name']} ({src['category']}) — {src['url']}"
        if src.get("scope") == "regional" and src.get("regions"):
            line += f"  [regions: {', '.join(src['regions'])}]"
        ref_lines.append(line)
    return "\n".join(must_lines) or "(none for this sector)", "\n".join(ref_lines) or "(none)"


def _render(template_path: str, vars: dict) -> str:
    with open(template_path) as f:
        text = f.read()
    for k, v in vars.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def _build_watcher_prompt(country: str, month: str, sector: dict, manifest: dict) -> str:
    must_fetch, reference = _format_seed_sources(country, sector["id"])
    return _render(WATCHER_PROMPT, {
        "COUNTRY": country,
        "COUNTRY_UPPER": country.upper(),
        "COUNTRY_LABEL": manifest.get("country_label", country.upper()),
        "MONTH": month,
        "SECTOR_ID": sector["id"],
        "SECTOR_LABEL": sector["label"],
        "TAGS": ", ".join(get_country_config(country).tags),
        "MUST_FETCH_SOURCES": must_fetch,
        "REFERENCE_SOURCES": reference,
    })


def _format_urban_seeds(country: str, centre: dict) -> tuple[str, str]:
    """For an urban centre worker: MUST FETCH = seeds whose `regions` overlap
    with the centre's regions; REFERENCE = same list rendered with category +
    region annotations for the model to pick from."""
    seeds = seeds_for_urban_centre(country, centre["regions"])
    if not seeds:
        return "(no seeds for this centre)", "(none)"
    must_lines = []
    ref_lines = []
    for src in seeds:
        line = f"- {src['name']} ({src['category']}) — {src['url']}"
        if src.get("regions"):
            line += f"  [regions: {', '.join(src['regions'])}]"
        # MUST FETCH = anything tagged with one of the centre's specific regions
        # (not universal aggregators)
        if src.get("regions"):
            must_lines.append(f"- {src['name']} — {src['url']}")
        else:
            ref_lines.append(line)
    return "\n".join(must_lines) or "(none)", "\n".join(ref_lines) or "(none)"


def _build_urban_watcher_prompt(country: str, month: str, centre: dict, manifest: dict) -> str:
    must_fetch, reference = _format_urban_seeds(country, centre)
    return _render(URBAN_WATCHER_PROMPT, {
        "COUNTRY": country,
        "COUNTRY_UPPER": country.upper(),
        "COUNTRY_LABEL": manifest.get("country_label", country.upper()),
        "MONTH": month,
        "CENTRE_ID": centre["id"],
        "CENTRE_LABEL": centre["label"],
        "CENTRE_REGIONS": ", ".join(centre["regions"]),
        "TAGS": ", ".join(get_country_config(country).tags),
        "MUST_FETCH_SOURCES": must_fetch,
        "REFERENCE_SOURCES": reference,
    })


def _build_analyst_prompt(country: str, month: str, manifest: dict, watcher_evidence: list[dict]) -> str:
    base = _render(ANALYST_PROMPT, {
        "COUNTRY": country,
        "COUNTRY_UPPER": country.upper(),
        "COUNTRY_LABEL": manifest.get("country_label", country.upper()),
        "MONTH": month,
        "TAGS": ", ".join(get_country_config(country).tags),
    })
    # Inline the watcher evidence so the analyst can see everything in one pass.
    ev_section = "\n\n---\n## Evidence from sector workers\n"
    for ev in watcher_evidence:
        ev_section += (
            f"\n### {ev.get('id')} — {ev.get('title', '')}\n"
            f"**Sector worker:** {ev.get('agent')}  "
            f"**Type:** {ev.get('type', 'unknown')}  "
            f"**Severity:** {ev.get('severity', 'unknown')}\n"
            f"**Source:** {ev.get('source_url', 'N/A')}\n"
            f"```\n{ev.get('content', '')}\n```\n"
        )
    return base + ev_section


def _build_reporter_prompt(country: str, month: str, manifest: dict, analyst_evidence: list[dict]) -> str:
    base = _render(REPORTER_PROMPT, {
        "COUNTRY": country,
        "COUNTRY_UPPER": country.upper(),
        "COUNTRY_LABEL": manifest.get("country_label", country.upper()),
        "MONTH": month,
        "TAGS": ", ".join(get_country_config(country).tags),
        "CURRENCY": manifest.get("currency", get_country_config(country).currency),
        "NOW": datetime.now(timezone.utc).isoformat(),
    })
    ev_section = "\n\n---\n## Analyst items (one row per item)\n"
    for ev in analyst_evidence:
        ev_section += (
            f"\n### {ev.get('id')} — {ev.get('title', '')}\n"
            f"**Source:** {ev.get('source_url', 'N/A')}\n"
            f"```\n{ev.get('content', '')}\n```\n"
        )
    return base + ev_section


# --- Agent config builders -------------------------------------------------

def _watcher_config(sector: dict, iterations: int = WATCHER_ITERATIONS_COUNTRY) -> dict:
    return {
        "id": f"grant_watcher_{sector['id']}",
        "name": f"Grant Watcher — {sector['label']}",
        "phase": 1,
        "depends_on": [],
        "model": MODEL_WATCHER_COUNTRY,
        "tools": ["web_fetch", "web_search", "save_evidence"],
        "max_iterations": iterations,
    }


def _urban_watcher_config(centre: dict, iterations: int = WATCHER_ITERATIONS_COUNTRY) -> dict:
    return {
        "id": f"grant_watcher_urban_{centre['id']}",
        "name": f"Grant Watcher — {centre['label']}",
        "phase": 1,
        "depends_on": [],
        "model": MODEL_WATCHER_COUNTRY,
        "tools": ["web_fetch", "web_search", "save_evidence"],
        "max_iterations": iterations,
    }


def _analyst_config() -> dict:
    return {
        "id": "grant_analyst_country",
        "name": "Grant Analyst (Country)",
        "phase": 2,
        "depends_on": [],
        "model": MODEL_ANALYST_COUNTRY,
        "tools": ["save_evidence"],
        "max_iterations": ANALYST_ITERATIONS_COUNTRY,
    }


def _reporter_config() -> dict:
    return {
        "id": "grant_reporter_country",
        "name": "Grant Reporter (Country)",
        "phase": 3,
        "depends_on": [],
        "model": MODEL_REPORTER_COUNTRY,
        "tools": [],
        "max_iterations": 1,
        "max_tokens": REPORTER_MAX_TOKENS_COUNTRY,
    }


# --- Plan + dry-run --------------------------------------------------------

def plan_sweep(country: str, month: str) -> dict:
    """Describe the work a sweep would do, without making any API calls."""
    manifest = load_manifest(country)
    config = get_country_config(country)
    sectors = config.sector_slices
    return {
        "country": country,
        "country_label": manifest.get("country_label", country.upper()),
        "month": month,
        "sector_workers": len(sectors),
        "sectors": [s["id"] for s in sectors],
        "watcher_iterations_per_worker": WATCHER_ITERATIONS_COUNTRY,
        "watcher_iterations_total_max": WATCHER_ITERATIONS_COUNTRY * len(sectors),
        "analyst_iterations": ANALYST_ITERATIONS_COUNTRY,
        "models": {
            "watcher": MODEL_WATCHER_COUNTRY,
            "analyst": MODEL_ANALYST_COUNTRY,
            "reporter": MODEL_REPORTER_COUNTRY,
        },
        "seed_source_count": len(manifest.get("seed_sources", [])),
        "must_appear_count": len(manifest.get("must_appear_funders", [])),
        "tags": config.tags,
        "output_dir": platform_cycles_dir(country, month),
    }


# --- The sweep -------------------------------------------------------------

def run_country_sweep(
    country: str,
    month: str,
    dry_run: bool = False,
    max_concurrent: int = 2,  # 2 keeps us under Anthropic's 450k/min input-token cap
    sectors_filter: list[str] | None = None,
    urban_centres_filter: list[str] | None = None,
    skip_urban: bool = False,
    skip_sectors: bool = False,
    watcher_iterations: int | None = None,
    promote_latest: bool = True,
) -> dict:
    """Run a monthly grant-collection sweep for one country.

    Returns a summary dict (paths, counts, coverage). On dry_run, returns
    only the plan and writes nothing.

    `sectors_filter`: list of sector ids to include (default: all from country config).
    `watcher_iterations`: per-worker iteration cap (default: WATCHER_ITERATIONS_COUNTRY).
    Both knobs exist for cheap end-to-end smoke tests before a full sweep.
    """
    config = get_country_config(country)
    iters = watcher_iterations if watcher_iterations is not None else WATCHER_ITERATIONS_COUNTRY
    sectors = (
        [s for s in config.sector_slices if s["id"] in set(sectors_filter)]
        if sectors_filter
        else config.sector_slices
    ) if not skip_sectors else []
    centres = (
        [c for c in config.urban_centre_slices if c["id"] in set(urban_centres_filter)]
        if urban_centres_filter
        else config.urban_centre_slices
    ) if not skip_urban else []
    if not sectors and not centres:
        raise ValueError("No workers selected (both sector and urban filters/skips empty).")

    plan = plan_sweep(country, month)
    plan["sector_workers"] = len(sectors)
    plan["sectors"] = [s["id"] for s in sectors]
    plan["urban_workers"] = len(centres)
    plan["urban_centres"] = [c["id"] for c in centres]
    plan["watcher_iterations_per_worker"] = iters
    plan["watcher_iterations_total_max"] = iters * (len(sectors) + len(centres))

    print(f"\n{'=' * 70}")
    print(f"  Country sweep — {plan['country_label']} ({country}) — {month}")
    print(f"{'=' * 70}")
    print(f"  Sector workers: {plan['sector_workers']}  ({', '.join(plan['sectors']) or 'none'})")
    print(f"  Urban workers:  {plan['urban_workers']}  ({', '.join(plan['urban_centres']) or 'none'})")
    print(f"  Iteration budget (watchers): up to {plan['watcher_iterations_total_max']}")
    print(f"  Models: watcher={plan['models']['watcher']}, "
          f"analyst={plan['models']['analyst']}, "
          f"reporter={plan['models']['reporter']}")
    print(f"  Seed sources: {plan['seed_source_count']}  "
          f"Spot-check funders: {plan['must_appear_count']}")
    print(f"  Output: {plan['output_dir']}")

    if dry_run:
        print("\n  [DRY RUN] No API calls made.")
        return {"dry_run": True, "plan": plan}

    ensure_platform_dirs()
    manifest = load_manifest(country)

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = platform_run_dir(country, month, run_ts)
    os.makedirs(run_dir, exist_ok=True)

    sweep_org = make_sweep_org_id(country, month, run_ts)
    cycle_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ---- Phase 1: parallel watchers (sector + urban) ------------------------
    total_workers = len(sectors) + len(centres)
    print(f"\n--- Phase 1: watchers — {total_workers} workers (parallel, max_concurrent={max_concurrent}) ---")
    watcher_outputs: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {}
        # Sector workers (national-scope by sector)
        for sector in sectors:
            cfg = _watcher_config(sector, iterations=iters)
            prompt = _build_watcher_prompt(country, month, sector, manifest)
            future = executor.submit(run_agent, sweep_org, cfg, prompt, cycle_date)
            futures[future] = cfg
        # Urban-centre workers (city-deep, all sectors)
        for centre in centres:
            cfg = _urban_watcher_config(centre, iterations=iters)
            prompt = _build_urban_watcher_prompt(country, month, centre, manifest)
            future = executor.submit(run_agent, sweep_org, cfg, prompt, cycle_date)
            futures[future] = cfg
        for future in as_completed(futures):
            cfg = futures[future]
            try:
                watcher_outputs[cfg["id"]] = future.result()
                print(f"  ✓ {cfg['name']}")
            except Exception as e:
                print(f"  ✗ {cfg['name']} failed: {e}")
                watcher_outputs[cfg["id"]] = {"raw_text": f"Error: {e}", "agent_id": cfg["id"]}

    watcher_evidence = [
        ev for ev in load_sweep_evidence(country, month, run_ts)
        if ev.get("agent", "").startswith("grant_watcher_")
    ]
    print(f"  Watcher evidence collected: {len(watcher_evidence)} items")

    # ---- Phase 2: deterministic build from watcher evidence ----------------
    # The watcher evidence is already structured (`Field: value` blocks). We
    # parse it directly. The lossy LLM analyst step is removed.
    print(f"\n--- Phase 2: build pool from watcher evidence ---")
    opportunities_raw = build_opportunities_from_watchers(
        watcher_evidence,
        country=country,
        month=month,
        currency=manifest.get("currency", "NZD"),
        now_iso=datetime.now(timezone.utc).isoformat(),
    )
    print(f"  ✓ Built {len(opportunities_raw)} rows from {len(watcher_evidence)} watcher evidence items")

    # ---- Phase 2.5: deadline resolution -------------------------------------
    print(f"\n--- Phase 2.5: deadline resolution ---")
    opportunities_raw, dl_stats = resolve_deadlines_for_rows(
        opportunities_raw, org_id_for_usage=sweep_org, cycle_date=cycle_date
    )
    print(f"  Resolved to date: {dl_stats['resolved_to_date']}, "
          f"rolling confirmed: {dl_stats['rolling_confirmed']}, "
          f"still TBC: {dl_stats['still_tbc']}, "
          f"fetch failures: {dl_stats['fetch_failures']}, "
          f"cost: ${dl_stats['cost_usd']:.4f}")

    # ---- Save outputs -------------------------------------------------------
    all_outputs = dict(watcher_outputs)
    with open(os.path.join(run_dir, "outputs.json"), "w") as f:
        json.dump(all_outputs, f, indent=2, default=str)

    # Cross-month merge to carry id/first_seen forward. Walk back past any
    # skipped months so continuity survives gaps in the sweep cadence.
    prev_month = latest_available_month(country, before=previous_month(month))
    prev_pool = load_pool(country, prev_month) if prev_month else []
    opportunities = merge_with_previous(opportunities_raw, prev_pool) if prev_pool else opportunities_raw
    opportunities = live_only(opportunities)

    with open(os.path.join(run_dir, "opportunities.json"), "w") as f:
        json.dump({"opportunities": opportunities}, f, indent=2)

    # Coverage telemetry
    coverage = validate_coverage(opportunities, country)
    coverage["sweep_run_ts"] = run_ts
    coverage["watcher_evidence_count"] = len(watcher_evidence)
    coverage["opportunities_total"] = len(opportunities)
    coverage["deadline_resolution"] = dl_stats
    with open(os.path.join(run_dir, "coverage.json"), "w") as f:
        json.dump(coverage, f, indent=2)

    # Human-readable report
    report_md = _render_report_md(plan, coverage, opportunities)
    with open(os.path.join(run_dir, "report.md"), "w") as f:
        f.write(report_md)

    # Update latest symlink (unless this is a non-promoting smoke test)
    if promote_latest:
        latest = platform_latest_path(country, month)
        if os.path.islink(latest):
            os.unlink(latest)
        elif os.path.isdir(latest):
            import shutil
            shutil.rmtree(latest)
        os.symlink(f"run-{run_ts}", latest)
    else:
        print(f"  [latest symlink left unchanged — non-promoting run]")

    print(f"\n--- Sweep complete ---")
    print(f"  Opportunities: {len(opportunities)}")
    print(f"  Spot-check: {coverage['present_count']}/{coverage['expected_count']} present, "
          f"{coverage['missing_count']} missing")
    if coverage['missing']:
        print(f"  Missing: {', '.join(coverage['missing'][:5])}"
              + (f" (+{len(coverage['missing']) - 5} more)" if len(coverage['missing']) > 5 else ""))
    print(f"  Output: {run_dir}")

    return {
        "run_ts": run_ts,
        "run_dir": run_dir,
        "opportunities": len(opportunities),
        "coverage": coverage,
    }


def _render_report_md(plan: dict, coverage: dict, opportunities: list[dict]) -> str:
    lines = [
        f"# {plan['country_label']} grant sweep — {plan['month']}",
        "",
        f"- Sectors swept: {plan['sector_workers']}",
        f"- Opportunities in pool: **{coverage['opportunities_total']}**",
        f"- Spot-check coverage: {coverage['present_count']}/{coverage['expected_count']}",
    ]
    if coverage['missing']:
        lines += ["", "## Missing must-appear funders", ""]
        for f in coverage['missing']:
            lines.append(f"- {f}")
    lines += ["", "## Opportunities", ""]
    for opp in opportunities:
        lines.append(f"### {opp.get('funder', '')} — {opp.get('title', '')}")
        meta = []
        if opp.get('deadline'):
            meta.append(f"Deadline: {opp['deadline']}")
        if opp.get('amount_max'):
            meta.append(f"Up to ${opp['amount_max']:,}")
        if opp.get('region'):
            meta.append(f"Region: {', '.join(opp['region'])}")
        if opp.get('tags'):
            meta.append(f"Tags: {', '.join(opp['tags'])}")
        if meta:
            lines.append(" | ".join(meta))
        if opp.get('summary'):
            lines.append("")
            lines.append(opp['summary'])
        if opp.get('source_url'):
            lines.append(f"\n[Source]({opp['source_url']})")
        lines.append("")
    return "\n".join(lines)


# --- CLI -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    dry_run = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) < 2:
        print("Usage: python -m orchestrator.sweep <country> <YYYY-MM> [--dry-run]")
        return 2
    country, month = args[0], args[1]
    run_country_sweep(country, month, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
