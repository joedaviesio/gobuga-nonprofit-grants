"""Report ingestion — parses cycle reports and extracts actionable opportunities (multi-tenant)."""

import json
import os
import re
from datetime import date as date_type, datetime, timezone

from api.tenant import org_cycles_dir


def list_report_dates(org_id: str) -> list[str]:
    """List all dates that have cycle reports."""
    cycles_dir = org_cycles_dir(org_id)
    if not os.path.exists(cycles_dir):
        return []
    dates = []
    for d in sorted(os.listdir(cycles_dir), reverse=True):
        report_path = os.path.join(cycles_dir, d, "latest", "report.md")
        if os.path.exists(report_path):
            dates.append(d)
    return dates


def load_report(org_id: str, cycle_date: str) -> dict | None:
    """Load a cycle report — raw markdown + parsed structure."""
    cycles_dir = org_cycles_dir(org_id)
    report_path = os.path.join(cycles_dir, cycle_date, "latest", "report.md")
    if not os.path.exists(report_path):
        return None

    with open(report_path) as f:
        raw = f.read()

    # Try to load structured output from the cycle
    structured = _load_structured_output(org_id, cycle_date)

    # Parse the markdown into sections
    parsed = _parse_report(raw)

    # Extract opportunities
    opportunities = structured.get("opportunities", []) if structured else []
    if not opportunities:
        opportunities = _extract_opportunities(org_id, raw, cycle_date)

    # Load evidence for this date
    evidence = _load_evidence_summary(org_id, cycle_date)

    # Fallback: if report parser found very few opportunities but evidence has many,
    # supplement from evidence directly
    grant_evidence = [e for e in evidence if e.get("type") == "grant_opportunity"]
    if len(opportunities) < 4 and len(grant_evidence) > len(opportunities):
        opportunities = _supplement_from_evidence(opportunities, grant_evidence, cycle_date)

    return {
        "date": cycle_date,
        "raw": raw,
        "sections": parsed,
        "opportunities": opportunities,
        "evidence_count": len(evidence),
        "evidence": evidence,
    }


def load_latest_report(org_id: str) -> dict | None:
    """Load the most recent report."""
    dates = list_report_dates(org_id)
    if not dates:
        return None
    return load_report(org_id, dates[0])


def _load_structured_output(org_id: str, cycle_date: str) -> dict | None:
    """Try to load structured JSON from the reporter's output."""
    cycles_dir = org_cycles_dir(org_id)
    outputs_path = os.path.join(cycles_dir, cycle_date, "latest", "outputs.json")
    if not os.path.exists(outputs_path):
        return None
    with open(outputs_path) as f:
        outputs = json.load(f)
    reporter = outputs.get("grant_reporter", {})
    return reporter.get("structured")


def _parse_report(raw: str) -> dict:
    """Split the markdown report into named sections."""
    sections = {}
    current_section = None
    current_lines = []

    for line in raw.split("\n"):
        if line.startswith("## "):
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = line[3:].strip()
            current_lines = []
        elif line.startswith("# "):
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = "_title"
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    return sections


def _is_expired(opp: dict, today: date_type) -> bool:
    """Check if an opportunity's deadline has passed."""
    desc = (opp.get("description") or "").lower()
    title = (opp.get("title") or "").lower()
    combined = f"{title} {desc}"
    if any(phrase in combined for phrase in (
        "no longer available", "deadline has passed", "deadline passed",
        "has passed", "expired", "closed",
    )):
        return True

    deadline_str = opp.get("deadline")
    if deadline_str:
        for fmt in ("%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y", "%Y-%m-%d"):
            try:
                deadline_date = datetime.strptime(deadline_str.strip(), fmt).date()
                return deadline_date < today
            except ValueError:
                continue

    return False


def _extract_opportunities(org_id: str, raw: str, cycle_date: str) -> list[dict]:
    """Extract grant opportunities from the report markdown."""
    opportunities = []
    lines = raw.split("\n")
    in_section = False
    current_opp = None

    for line in lines:
        if line.startswith("## Action Required") or line.startswith("## Top Opportunities"):
            in_section = True
            continue
        elif line.startswith("## "):
            in_section = False
            if current_opp:
                opportunities.append(current_opp)
                current_opp = None
            continue

        if not in_section:
            continue

        bold_match = re.match(r'\*\*(.+?)\*\*[:.]?\s*(.*)', line)
        if bold_match:
            if current_opp:
                opportunities.append(current_opp)

            title = bold_match.group(1).strip()
            description = bold_match.group(2).strip()

            priority = "medium"
            title_lower = title.lower()
            if "high priority" in title_lower or "apply" in title_lower or "today" in title_lower or "immediate" in title_lower:
                priority = "high"
            elif "investigate" in title_lower or "watch" in title_lower or "monitor" in title_lower:
                priority = "low"

            current_opp = {
                "title": title,
                "description": description,
                "priority": priority,
                "details": [],
                "deadline": None,
                "amount": None,
                "funder": None,
                "recommendation": None,
                "cycle_date": cycle_date,
            }
        elif current_opp and line.startswith("- "):
            detail = line[2:].strip()
            current_opp["details"].append(detail)

            detail_lower = detail.lower()
            if "deadline" in detail_lower:
                current_opp["deadline"] = detail.split(":")[-1].strip() if ":" in detail else detail
            elif "funding" in detail_lower or "$" in detail or "€" in detail or "usd" in detail_lower or "eur" in detail_lower:
                current_opp["amount"] = detail
            elif "recommend" in detail_lower:
                current_opp["recommendation"] = detail
        elif current_opp and line.strip():
            if current_opp["description"]:
                current_opp["description"] += " " + line.strip()
            else:
                current_opp["description"] = line.strip()

            if "deadline" in line.lower() or "closes" in line.lower():
                deadline_match = re.search(r'(\d{1,2}\s+\w+\s+\d{4})', line)
                if deadline_match:
                    current_opp["deadline"] = deadline_match.group(1)
            if "$" in line or "€" in line:
                amount_match = re.search(r'[\$€][\d,]+[KkMm]?', line)
                if amount_match:
                    current_opp["amount"] = amount_match.group(0)

    if current_opp:
        opportunities.append(current_opp)

    # Generate IDs, attach source URLs, and detect expired opportunities
    evidence = _load_evidence_summary(org_id, cycle_date)
    today = date_type.today()
    for i, opp in enumerate(opportunities):
        opp["id"] = f"OPP-{cycle_date}-{i+1:03d}"
        opp["source_urls"] = _match_evidence_urls(opp, evidence)
        opp["expired"] = _is_expired(opp, today)

    return opportunities


def _match_evidence_urls(opp: dict, evidence: list[dict]) -> list[dict]:
    """Find evidence items with source URLs that relate to this opportunity."""
    opp_title_lower = opp.get("title", "").lower()
    opp_desc_lower = opp.get("description", "").lower()
    opp_text = opp_title_lower + " " + opp_desc_lower

    matched = []
    seen_urls = set()
    for ev in evidence:
        url = ev.get("source_url", "")
        if not url or url in seen_urls:
            continue
        ev_title_lower = ev.get("title", "").lower()
        opp_words = set(opp_text.split())
        ev_words = set(ev_title_lower.split())
        meaningful_opp = {w for w in opp_words if len(w) > 3}
        meaningful_ev = {w for w in ev_words if len(w) > 3}
        overlap = len(meaningful_opp & meaningful_ev)
        if overlap >= 2:
            matched.append({
                "title": ev.get("title", ""),
                "url": url,
                "type": ev.get("type", ""),
            })
            seen_urls.add(url)

    return matched


def _supplement_from_evidence(existing_opps: list[dict], grant_evidence: list[dict], cycle_date: str) -> list[dict]:
    """Build opportunity entries from evidence items when report parser finds few."""
    existing_titles = set()
    for opp in existing_opps:
        existing_titles.add(opp.get("title", "").lower())
        for d in opp.get("details", []):
            existing_titles.add(d.lower()[:40])

    supplemented = list(existing_opps)
    seen_titles = set()

    for ev in grant_evidence:
        title = ev.get("title", "")
        if not title:
            continue

        title_lower = title.lower()
        if title_lower in seen_titles:
            continue
        skip = False
        for et in existing_titles:
            title_words = set(title_lower.split())
            et_words = set(et.split())
            meaningful = {w for w in title_words if len(w) > 3}
            overlap = len(meaningful & et_words)
            if overlap >= 3:
                skip = True
                break
        if skip:
            seen_titles.add(title_lower)
            continue

        seen_titles.add(title_lower)
        url = ev.get("source_url", "")
        severity = ev.get("severity", "")

        priority = "medium"
        if severity == "high":
            priority = "high"
        elif severity == "low" or severity == "info":
            priority = "low"

        opp = {
            "title": title,
            "description": "",
            "priority": priority,
            "details": [],
            "deadline": None,
            "amount": None,
            "funder": None,
            "recommendation": None,
            "cycle_date": cycle_date,
            "source_urls": [{"title": title, "url": url, "type": "grant_opportunity"}] if url else [],
        }
        supplemented.append(opp)

    for i, opp in enumerate(supplemented):
        opp["id"] = f"OPP-{cycle_date}-{i+1:03d}"

    return supplemented


def _load_evidence_summary(org_id: str, cycle_date: str) -> list[dict]:
    """Load evidence items for this cycle date (summary only)."""
    cycles_dir = org_cycles_dir(org_id)
    evidence_path = os.path.join(cycles_dir, cycle_date, "latest", "evidence.json")
    if not os.path.exists(evidence_path):
        return []
    with open(evidence_path) as f:
        items = json.load(f)
    return [
        {
            "id": item.get("id"),
            "title": item.get("title", ""),
            "type": item.get("type", ""),
            "severity": item.get("severity", ""),
            "source_url": item.get("source_url", ""),
        }
        for item in items
    ]
