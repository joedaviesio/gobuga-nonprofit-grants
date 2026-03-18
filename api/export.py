"""Export draft in multiple formats, including filled DOCX templates (multi-tenant)."""

import json
import os
import re
from datetime import datetime, timezone
from copy import deepcopy

from api.case_manager import load_case, _case_dir, _uploads_dir
from api.tenant import org_evidence_dir


def _get_case_evidence(org_id: str, case: dict) -> list[dict]:
    """Load evidence items related to this case from the cycle evidence archive."""
    grant_brief = case.get("grant_brief", {})
    source_cycle = grant_brief.get("source_cycle")
    if not source_cycle:
        return []

    # Evidence is now stored in the cycle output dir
    from api.tenant import org_cycles_dir
    evidence_path = os.path.join(org_cycles_dir(org_id), source_cycle, "latest", "evidence.json")
    if not os.path.exists(evidence_path):
        return []

    with open(evidence_path) as f:
        all_evidence = json.load(f)

    if not all_evidence:
        return []

    related_ids = {
        ev.get("id") for ev in grant_brief.get("related_evidence", []) if ev.get("id")
    }

    opp_title = grant_brief.get("title", "").lower()
    title_words = set(opp_title.split()) - {"the", "of", "and", "for", "in", "a", "to"}

    matched = []
    seen_ids = set()
    for ev in all_evidence:
        ev_id = ev.get("id", "")
        if ev_id in seen_ids:
            continue

        if ev_id in related_ids:
            matched.append(ev)
            seen_ids.add(ev_id)
            continue

        if ev.get("type") in ("grant_opportunity", "analysis", "recommendation"):
            ev_title = ev.get("title", "").lower()
            ev_words = set(ev_title.split()) - {"the", "of", "and", "for", "in", "a", "to"}
            if len(title_words & ev_words) >= 2:
                matched.append(ev)
                seen_ids.add(ev_id)

    return matched


def _evidence_appendix_md(evidence: list[dict], source_cycle: str) -> str:
    """Format evidence items as a markdown appendix."""
    if not evidence:
        return ""

    parts = ["\n---\n\n## Sources & Evidence\n"]
    parts.append(f"*From grant cycle: {source_cycle}*\n")

    by_type: dict[str, list[dict]] = {}
    for ev in evidence:
        t = ev.get("type", "other")
        by_type.setdefault(t, []).append(ev)

    type_labels = {
        "grant_opportunity": "Grant Opportunities",
        "analysis": "Analysis",
        "recommendation": "Recommendations",
        "donor_intel": "Donor Intelligence",
        "deadline": "Deadlines",
    }

    for ev_type, items in by_type.items():
        label = type_labels.get(ev_type, ev_type.replace("_", " ").title())
        parts.append(f"### {label}\n")
        for ev in items:
            title = ev.get("title", "Untitled")
            parts.append(f"**{title}** ({ev.get('id', '')})")
            if ev.get("source_url"):
                parts.append(f"  Source: {ev['source_url']}")
            content = ev.get("content", "")
            if content:
                if len(content) > 500:
                    content = content[:500] + "..."
                parts.append(f"  {content}")
            if ev.get("severity"):
                parts.append(f"  Severity: {ev['severity']}")
            parts.append("")

    return "\n".join(parts)


def export_markdown(org_id: str, case_id: str) -> str | None:
    """Export the current draft as a markdown document."""
    case = load_case(org_id, case_id)
    if case is None:
        return None

    sections = case.get("draft", {}).get("sections", {})
    if not sections:
        return "# Draft\n\nNo sections drafted yet."

    parts = [f"# Grant Application: {case.get('grant_id', 'Unknown')}\n"]
    parts.append(f"**Case:** {case['case_id']}")
    parts.append(f"**Status:** {case['status']}")
    parts.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n")
    parts.append("---\n")

    for name, sec in sections.items():
        title = name.replace("_", " ").title()
        status = sec.get("status", "draft")
        content = sec.get("content", "")
        parts.append(f"## {title}\n")
        if status != "complete":
            parts.append(f"*Status: {status}*\n")
        parts.append(content)
        parts.append("")

    evidence = _get_case_evidence(org_id, case)
    source_cycle = case.get("grant_brief", {}).get("source_cycle", "")
    appendix = _evidence_appendix_md(evidence, source_cycle)
    if appendix:
        parts.append(appendix)

    return "\n".join(parts)


def export_json(org_id: str, case_id: str) -> dict | None:
    """Export the current draft as structured JSON."""
    case = load_case(org_id, case_id)
    if case is None:
        return None

    evidence = _get_case_evidence(org_id, case)
    return {
        "case_id": case["case_id"],
        "grant_id": case.get("grant_id"),
        "status": case["status"],
        "exported": datetime.now(timezone.utc).isoformat(),
        "sections": case.get("draft", {}).get("sections", {}),
        "evidence": [
            {
                "id": ev.get("id"),
                "type": ev.get("type"),
                "title": ev.get("title"),
                "source_url": ev.get("source_url"),
                "severity": ev.get("severity"),
                "content": ev.get("content", "")[:500],
            }
            for ev in evidence
        ],
    }


def _md_to_plain(text: str) -> str:
    """Strip markdown formatting to plain text for DOCX insertion."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*]\s+', '- ', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def export_docx(org_id: str, case_id: str, template_filename: str | None = None) -> str | None:
    """Export to DOCX. Returns the filepath of the generated DOCX."""
    from docx import Document
    from docx.shared import Pt, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    case = load_case(org_id, case_id)
    if case is None:
        return None

    sections = case.get("draft", {}).get("sections", {})
    case_dir = _case_dir(org_id, case_id)
    exports_dir = os.path.join(case_dir, "exports")
    os.makedirs(exports_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    if template_filename:
        filepath = _fill_docx_template(org_id, case_id, template_filename, sections, exports_dir, ts)
    else:
        filepath = _generate_fresh_docx(org_id, case_id, sections, exports_dir, ts, case)

    if filepath:
        case["exports"].append({
            "format": "docx",
            "filename": os.path.basename(filepath),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        from api.case_manager import _save
        _save(org_id, case)

    return filepath


def _fill_docx_template(org_id: str, case_id: str, template_filename: str, sections: dict, exports_dir: str, ts: str) -> str | None:
    """Fill an uploaded DOCX template with draft content."""
    from docx import Document

    template_path = os.path.join(_uploads_dir(org_id, case_id), template_filename)
    if not os.path.exists(template_path):
        return None

    doc = Document(template_path)

    form_content = sections.get("completed_application_form", {}).get("content", "")
    if not form_content:
        form_content = "\n\n".join(
            sec.get("content", "") for sec in sections.values() if sec.get("content")
        )

    field_map = _parse_form_content(form_content)
    _fill_document(doc, field_map)

    output_filename = f"filled-{template_filename.replace('.docx', '')}-{ts}.docx"
    output_path = os.path.join(exports_dir, output_filename)
    doc.save(output_path)
    return output_path


def _parse_form_content(content: str) -> dict:
    """Parse the bot's filled form content into a field->value mapping."""
    field_map = {}
    lines = content.split("\n")
    current_field = None
    current_value_lines = []
    current_section = None
    section_lines = []

    for line in lines:
        section_match = re.match(r'^###\s+(.+)', line)
        if section_match:
            if current_section:
                section_text = _md_to_plain("\n".join(section_lines).strip())
                if section_text:
                    field_map[current_section] = section_text
            current_section = section_match.group(1).strip().lower()
            section_lines = []
            if current_field:
                field_map[current_field] = _md_to_plain("\n".join(current_value_lines).strip())
                current_field = None
                current_value_lines = []
            continue

        field_match = re.match(r'\*\*(.+?):\*\*\s*(.*)', line)
        if field_match:
            if current_field:
                field_map[current_field] = _md_to_plain("\n".join(current_value_lines).strip())
            current_field = field_match.group(1).strip().lower()
            inline_val = field_match.group(2).strip()
            current_value_lines = [inline_val] if inline_val else []
        elif line.startswith("## ") or line.startswith("---"):
            if current_field:
                field_map[current_field] = _md_to_plain("\n".join(current_value_lines).strip())
                current_field = None
                current_value_lines = []
            if current_section:
                section_text = _md_to_plain("\n".join(section_lines).strip())
                if section_text:
                    field_map[current_section] = section_text
                current_section = None
                section_lines = []
        else:
            if current_field is not None:
                current_value_lines.append(line)
            if current_section is not None:
                section_lines.append(line)

    if current_field:
        field_map[current_field] = _md_to_plain("\n".join(current_value_lines).strip())
    if current_section:
        section_text = _md_to_plain("\n".join(section_lines).strip())
        if section_text:
            field_map[current_section] = section_text

    return field_map


def _normalize(text: str) -> str:
    return re.sub(r'[^a-z0-9\s]', '', text.lower()).strip()


def _find_best_match(label: str, field_map: dict) -> str | None:
    norm_label = _normalize(label)
    if not norm_label:
        return None

    for key, val in field_map.items():
        if _normalize(key) == norm_label:
            return val

    for key, val in field_map.items():
        norm_key = _normalize(key)
        if norm_key and (norm_key in norm_label or norm_label in norm_key):
            return val

    label_words = set(norm_label.split())
    best_score = 0
    best_val = None
    for key, val in field_map.items():
        key_words = set(_normalize(key).split())
        if not key_words:
            continue
        overlap = len(label_words & key_words) / max(len(label_words), len(key_words))
        if overlap > best_score and overlap >= 0.5:
            best_score = overlap
            best_val = val
    return best_val


def _fill_document(doc, field_map: dict):
    """Walk document tables and fill cells that match field labels."""
    for table in doc.tables:
        for row in table.rows:
            cells = row.cells
            for i, cell in enumerate(cells):
                cell_text = cell.text.strip()
                if not cell_text:
                    continue

                match = _find_best_match(cell_text, field_map)
                if match and match != cell_text:
                    if i + 1 < len(cells):
                        target = cells[i + 1]
                        if not target.text.strip() or target.text.strip() == cell_text:
                            for p in target.paragraphs:
                                p.text = ""
                            if target.paragraphs:
                                target.paragraphs[0].text = match
                    elif len(cell.paragraphs) > 1:
                        cell.paragraphs[-1].text = match

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if "[TO BE COMPLETED" in text or "[TBD]" in text:
            match = _find_best_match(text, field_map)
            if match:
                para.text = match


def _generate_fresh_docx(org_id: str, case_id: str, sections: dict, exports_dir: str, ts: str, case: dict) -> str:
    """Generate a fresh DOCX document from draft sections."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()

    title = doc.add_heading(f"Grant Application: {case.get('grant_id', 'Unknown')}", level=0)
    doc.add_paragraph(f"Case: {case['case_id']}")
    doc.add_paragraph(f"Status: {case['status']}")
    doc.add_paragraph(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    doc.add_paragraph("")

    for name, sec in sections.items():
        title_text = name.replace("_", " ").title()
        doc.add_heading(title_text, level=1)

        if sec.get("status") and sec["status"] != "complete":
            status_para = doc.add_paragraph(f"Status: {sec['status']}")
            status_para.runs[0].italic = True

        content = sec.get("content", "")
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                doc.add_paragraph("")
                continue

            if line.startswith("### "):
                doc.add_heading(_md_to_plain(line[4:]), level=3)
            elif line.startswith("## "):
                doc.add_heading(_md_to_plain(line[3:]), level=2)
            elif line.startswith("# "):
                doc.add_heading(_md_to_plain(line[2:]), level=1)
            elif line.startswith("- "):
                doc.add_paragraph(_md_to_plain(line[2:]), style="List Bullet")
            else:
                doc.add_paragraph(_md_to_plain(line))

    evidence = _get_case_evidence(org_id, case)
    if evidence:
        source_cycle = case.get("grant_brief", {}).get("source_cycle", "")
        doc.add_page_break()
        doc.add_heading("Sources & Evidence", level=1)
        doc.add_paragraph(f"From grant cycle: {source_cycle}")

        type_labels = {
            "grant_opportunity": "Grant Opportunities",
            "analysis": "Analysis",
            "recommendation": "Recommendations",
            "donor_intel": "Donor Intelligence",
            "deadline": "Deadlines",
        }
        by_type: dict[str, list[dict]] = {}
        for ev in evidence:
            t = ev.get("type", "other")
            by_type.setdefault(t, []).append(ev)

        for ev_type, items in by_type.items():
            label = type_labels.get(ev_type, ev_type.replace("_", " ").title())
            doc.add_heading(label, level=2)
            for ev in items:
                title = ev.get("title", "Untitled")
                doc.add_paragraph(f"{title} ({ev.get('id', '')})", style="List Bullet")
                if ev.get("source_url"):
                    doc.add_paragraph(f"Source: {ev['source_url']}")
                content = ev.get("content", "")
                if content:
                    if len(content) > 500:
                        content = content[:500] + "..."
                    doc.add_paragraph(content)

    output_filename = f"draft-{ts}.docx"
    output_path = os.path.join(exports_dir, output_filename)
    doc.save(output_path)
    return output_path


def export_to_file(org_id: str, case_id: str, fmt: str = "markdown", template_filename: str | None = None) -> str | None:
    """Export and save to the case directory. Returns the file path."""
    case = load_case(org_id, case_id)
    if case is None:
        return None

    case_dir = _case_dir(org_id, case_id)
    exports_dir = os.path.join(case_dir, "exports")
    os.makedirs(exports_dir, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    if fmt == "markdown":
        content = export_markdown(org_id, case_id)
        filename = f"draft-{ts}.md"
        filepath = os.path.join(exports_dir, filename)
        with open(filepath, "w") as f:
            f.write(content)
    elif fmt == "json":
        content = export_json(org_id, case_id)
        filename = f"draft-{ts}.json"
        filepath = os.path.join(exports_dir, filename)
        with open(filepath, "w") as f:
            json.dump(content, f, indent=2)
    elif fmt == "docx":
        return export_docx(org_id, case_id, template_filename)
    else:
        return None

    case["exports"].append({
        "format": fmt,
        "filename": filename,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    from api.case_manager import _save
    _save(org_id, case)

    return filepath
