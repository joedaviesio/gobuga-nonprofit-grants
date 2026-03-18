"""Generates the opening case brief — the first thing the officer sees when a case opens."""

import json


def generate_case_brief(case: dict) -> str:
    """
    Build a structured markdown brief from the case's grant_brief data.
    This is the opening message in the chat — gives the officer a clear picture
    of the opportunity before they start working.
    """
    brief = case.get("grant_brief", {})
    parts = []

    # Title
    title = brief.get("title", case.get("grant_id", "Grant Opportunity"))
    parts.append(f"# {title}\n")

    # Key facts table
    facts = []
    if brief.get("funder"):
        facts.append(f"**Funder:** {brief['funder']}")
    if brief.get("amount"):
        facts.append(f"**Amount:** {brief['amount']}")
    if brief.get("deadline"):
        facts.append(f"**Deadline:** {brief['deadline']}")
    if brief.get("priority"):
        priority = brief["priority"]
        label = {"high": "HIGH — Immediate action", "medium": "MEDIUM — Worth pursuing", "low": "LOW — Monitor"}.get(priority, priority)
        facts.append(f"**Priority:** {label}")
    if brief.get("reference"):
        facts.append(f"**Reference:** {brief['reference']}")
    if brief.get("language"):
        facts.append(f"**Language:** {brief['language']}")
    if brief.get("grants_available"):
        facts.append(f"**Grants available:** {brief['grants_available']}")
    if brief.get("source_cycle"):
        facts.append(f"**Source:** Cycle {brief['source_cycle']}")

    if facts:
        parts.append(" | ".join(facts[:4]))
        if len(facts) > 4:
            parts.append(" | ".join(facts[4:]))
        parts.append("")

    # Description
    if brief.get("description"):
        parts.append(f"{brief['description']}\n")

    # Objectives
    objectives = brief.get("objectives", [])
    if objectives:
        parts.append("## Objectives\n")
        for obj in objectives:
            parts.append(f"- {obj}")
        parts.append("")

    # Target groups
    targets = brief.get("target_groups", [])
    if targets:
        parts.append("## Target Groups\n")
        for t in targets:
            parts.append(f"- {t}")
        parts.append("")

    # Key requirements
    reqs = brief.get("key_requirements", [])
    if reqs:
        parts.append("## Key Requirements\n")
        for r in reqs:
            parts.append(f"- {r}")
        parts.append("")

    # Details (from report extraction)
    details = brief.get("details", [])
    if details:
        parts.append("## Details\n")
        for d in details:
            parts.append(f"- {d}")
        parts.append("")

    # Analyst assessment
    assessment = brief.get("analyst_assessment", {})
    if assessment:
        parts.append("## Assessment\n")
        for k, v in assessment.items():
            label = k.replace("_", " ").title()
            parts.append(f"- **{label}:** {v}")
        parts.append("")

    # Source links
    source_urls = brief.get("source_urls", [])
    related_evidence = brief.get("related_evidence", [])

    # Collect all URLs from both sources
    links = []
    seen = set()
    for item in source_urls:
        url = item.get("url", "")
        if url and url not in seen:
            links.append({"title": item.get("title", ""), "url": url, "type": item.get("type", "")})
            seen.add(url)
    for ev in related_evidence:
        url = ev.get("source_url", "")
        if url and url not in seen:
            links.append({"title": ev.get("title", ""), "url": url, "type": ev.get("type", "")})
            seen.add(url)

    if links:
        parts.append("## Sources & References\n")
        for link in links:
            label = link["title"] or link["url"]
            parts.append(f"- [{label}]({link['url']})")
        parts.append("")
    elif related_evidence:
        parts.append("## Related Evidence\n")
        for ev in related_evidence:
            parts.append(f"- [{ev.get('type', '')}] {ev.get('title', '')}")
        parts.append("")

    # Recommendation
    if brief.get("recommendation"):
        parts.append(f"## Recommendation\n\n{brief['recommendation']}\n")

    # Next steps prompt
    parts.append("---\n")
    parts.append("**Ready to work on this application.** You can:")
    parts.append("- Upload the application form and I'll fill it in")
    parts.append("- Ask me to draft specific sections (executive summary, project description, budget)")
    parts.append("- Upload a project plan and I'll extract the key information")
    parts.append("- Ask questions about the opportunity or donor")

    return "\n".join(parts)
