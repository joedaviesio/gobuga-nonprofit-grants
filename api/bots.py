"""Case bots A–D — specialized Haiku bots for the case workflow (multi-tenant).

Bot A: Summary Generator — produces tailored grant summary on case open
Bot B: Document Parser — extracts sections/questions from uploaded submission forms
Bot C: Section Filler — fills parsed sections from the case data bank
Bot D: Q&A Bot — identifies data bank gaps and asks the user targeted questions
"""

import json
import os
import anthropic
from dotenv import load_dotenv

from api.case_manager import load_case, append_message, _uploads_dir
from api.databank import (
    add_entry,
    add_entries_bulk,
    get_entries,
    format_databank_for_prompt,
    seed_from_grant_brief,
    seed_from_org_profile,
)
from api.tenant import org_profile_path, org_data_dir, org_state_dir
from api.usage_log import log_usage

load_dotenv()

MODEL = os.getenv("GOBUGA_BOT_MODEL", "claude-haiku-4-5-20251001")

# Pricing per million tokens (USD)
PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 12.00},
    "claude-opus-4-20250514": {"input": 15.00, "output": 60.00},
}


def _track_usage(response, usage: dict) -> dict:
    """Accumulate token usage from an API response."""
    input_tokens = getattr(response.usage, "input_tokens", 0)
    output_tokens = getattr(response.usage, "output_tokens", 0)
    usage["input_tokens"] += input_tokens
    usage["output_tokens"] += output_tokens
    usage["api_calls"] += 1
    return usage


def _calc_cost(usage: dict, model: str = None) -> dict:
    """Add cost calculation to usage dict."""
    m = model or MODEL
    prices = PRICING.get(m, PRICING["claude-haiku-4-5-20251001"])
    input_cost = (usage["input_tokens"] / 1_000_000) * prices["input"]
    output_cost = (usage["output_tokens"] / 1_000_000) * prices["output"]
    usage["cost_usd"] = round(input_cost + output_cost, 4)
    usage["model"] = m
    return usage


def _new_usage() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "api_calls": 0}


def _load_org_profile(org_id: str) -> str:
    """Load the organisation profile text."""
    path = org_profile_path(org_id)
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""


def _load_data_files(org_id: str, subfolder: str, max_per_file: int = 3000) -> str:
    """Load reference data files from org data/{subfolder}/."""
    from orchestrator.main import _extract_pdf, _extract_docx, _strip_html
    data_dir = os.path.join(org_data_dir(org_id), subfolder)
    if not os.path.exists(data_dir):
        return ""
    parts = []
    for fname in sorted(os.listdir(data_dir)):
        filepath = os.path.join(data_dir, fname)
        if not os.path.isfile(filepath):
            continue
        ext = os.path.splitext(fname)[1].lower()
        try:
            if ext == ".pdf":
                content = _extract_pdf(filepath, max_pages=5)
            elif ext == ".docx":
                content = _extract_docx(filepath)
            elif ext in {".html", ".htm"}:
                with open(filepath, errors="replace") as f:
                    content = _strip_html(f.read())
            elif ext in {".md", ".txt", ".json", ".csv"}:
                with open(filepath, errors="replace") as f:
                    content = f.read()
            else:
                continue
            if len(content) > max_per_file:
                content = content[:max_per_file] + "\n... [truncated]"
            parts.append(f"### {fname}\n{content}")
        except Exception:
            continue
    return "\n\n".join(parts)


def _load_state(org_id: str, filename: str) -> dict:
    """Load a state file for an org."""
    from orchestrator.state_manager import load_state
    return load_state(org_id, filename)


def _extract_upload_text(org_id: str, case_id: str, filename: str) -> str:
    """Extract text from an uploaded file."""
    from orchestrator.main import _extract_pdf, _extract_docx, _strip_html
    filepath = os.path.join(_uploads_dir(org_id, case_id), filename)
    if not os.path.exists(filepath):
        return f"File not found: {filename}"
    ext = os.path.splitext(filename)[1].lower()
    try:
        if ext == ".pdf":
            return _extract_pdf(filepath, max_pages=30)
        elif ext == ".docx":
            return _extract_docx(filepath)
        elif ext in {".html", ".htm"}:
            with open(filepath, errors="replace") as f:
                return _strip_html(f.read())
        else:
            with open(filepath, errors="replace") as f:
                return f.read()
    except Exception as e:
        return f"Error reading {filename}: {e}"


# ---------------------------------------------------------------------------
# Bot A — Summary Generator
# ---------------------------------------------------------------------------

def bot_a_generate_summary(org_id: str, case_id: str, model: str = None) -> dict:
    """
    Generate a tailored grant summary when a case is opened.
    Reads the data bank, donor registry, and org profile to produce
    an approach strategy with key info.
    """
    bot_model = model or MODEL
    case = load_case(org_id, case_id)
    if case is None:
        return {"error": f"Case {case_id} not found"}

    grant_brief = case.get("grant_brief", {})

    # Seed databank from grant brief + org profile
    seeded = seed_from_grant_brief(org_id, case_id, grant_brief)
    org_text = _load_org_profile(org_id)
    if org_text:
        seeded += seed_from_org_profile(org_id, case_id, org_text)

    # Build context
    donor_registry = _load_state(org_id, "donor_registry.json")
    databank_text = format_databank_for_prompt(org_id, case_id)
    donor_docs = _load_data_files(org_id, "donors")

    system_prompt = f"""You are a grant strategy advisor for the organisation described below.

Your task: produce a concise, actionable grant summary for the grants officer.
This summary should help them decide how to approach this application.

## Organisation Profile
{org_text[:3000]}

{databank_text}

## Donor Registry
{json.dumps(donor_registry, indent=2, default=str)[:3000]}

{f"## Donor Reference Docs{chr(10)}{donor_docs[:3000]}" if donor_docs else ""}

## Instructions
Write a tailored grant summary with these sections:
1. **Opportunity snapshot** — title, funder, amount, deadline in a compact block
2. **Why this fits** — 2-3 sentences on mission alignment and capacity fit
3. **Approach strategy** — how the org should position this application (angle, partnerships, key strengths to emphasise)
4. **Watch out for** — risks, eligibility concerns, or competition
5. **Recommended next steps** — 3-4 concrete actions

Be direct, specific to this grant. No generic advice. Write in English.
Keep it under 500 words."""

    usage = _new_usage()
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=bot_model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"Generate the tailored grant summary for: {grant_brief.get('title', 'this opportunity')}",
        }],
    )
    _track_usage(response, usage)

    summary = response.content[0].text

    # Save as the opening assistant message
    append_message(org_id, case_id, "assistant", summary, actions=[{"type": "bot_a_summary"}])

    result_usage = _calc_cost(usage, bot_model)
    log_usage(org_id, "bot_a", result_usage, case_id=case_id)
    return {
        "case_id": case_id,
        "summary": summary,
        "databank_seeded": len(seeded),
        "usage": result_usage,
    }


# ---------------------------------------------------------------------------
# Bot B — Document Parser
# ---------------------------------------------------------------------------

BOT_B_TOOLS = [
    {
        "name": "save_parsed_sections",
        "description": "Save the list of parsed sections/questions extracted from the submission document.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "description": "List of sections extracted from the document",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Short identifier like 'section_1' or 'org_name'",
                            },
                            "label": {
                                "type": "string",
                                "description": "The original section title or question from the form",
                            },
                            "field_type": {
                                "type": "string",
                                "enum": ["short_text", "long_text", "number", "date", "choice", "table"],
                                "description": "Type of response expected",
                            },
                            "max_words": {
                                "type": "integer",
                                "description": "Word limit if specified in the form, 0 if none",
                            },
                            "guidance": {
                                "type": "string",
                                "description": "Any guidance or sub-questions from the form for this section",
                            },
                        },
                        "required": ["id", "label", "field_type"],
                    },
                },
            },
            "required": ["sections"],
        },
    },
]


def bot_b_parse_document(org_id: str, case_id: str, filename: str, model: str = None) -> dict:
    """Parse an uploaded grant submission form to extract sections/questions."""
    bot_model = model or MODEL
    case = load_case(org_id, case_id)
    if case is None:
        return {"error": f"Case {case_id} not found"}

    doc_text = _extract_upload_text(org_id, case_id, filename)
    if doc_text.startswith("File not found") or doc_text.startswith("Error reading"):
        return {"error": doc_text}

    if len(doc_text) > 15000:
        doc_text = doc_text[:15000] + "\n... [truncated]"

    system_prompt = """You are a document parser specialising in grant application forms.

Your task: read the uploaded grant application form and extract every section,
question, and field that needs to be filled in.

## Instructions
1. Read the document carefully
2. Identify every field, question, or section that requires an answer
3. For each one, determine:
   - A short ID (e.g. 'org_legal_name', 'project_summary', 'budget_total')
   - The original label/question text exactly as written
   - The field type (short_text, long_text, number, date, choice, table)
   - Any word limit mentioned
   - Any guidance or sub-questions provided
4. Call the save_parsed_sections tool with the complete list
5. Preserve the ORDER they appear in the document

Be thorough — miss nothing. Include administrative fields (org name, address, etc.)
as well as narrative sections (project description, methodology, etc.)."""

    usage = _new_usage()
    client = anthropic.Anthropic()
    messages = [{
        "role": "user",
        "content": f"Parse this grant application form and extract all sections:\n\n{doc_text}",
    }]

    response = client.messages.create(
        model=bot_model,
        max_tokens=4096,
        system=system_prompt,
        messages=messages,
        tools=BOT_B_TOOLS,
    )
    _track_usage(response, usage)

    parsed_sections = []
    bot_text = ""

    for block in response.content:
        if block.type == "text":
            bot_text = block.text
        elif block.type == "tool_use" and block.name == "save_parsed_sections":
            parsed_sections = block.input.get("sections", [])

    if not parsed_sections:
        result_usage = _calc_cost(usage, bot_model)
        log_usage(org_id, "bot_b", result_usage, case_id=case_id, extra={"status": "no_sections"})
        return {
            "error": "Bot B failed to extract sections. Raw response saved.",
            "raw": bot_text,
            "usage": result_usage,
        }

    # Save parsed sections to case
    case = load_case(org_id, case_id)
    case["parsed_sections"] = {
        "source_file": filename,
        "parsed_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "sections": parsed_sections,
    }
    from api.case_manager import _save
    _save(org_id, case)

    # Also save section labels to databank for reference
    for sec in parsed_sections:
        add_entry(
            org_id, case_id,
            f"form_field:{sec['id']}",
            sec["label"],
            source="bot_extract",
            category="form_structure",
        )

    append_message(
        org_id, case_id, "assistant",
        f"I've parsed the submission form **{filename}** and found **{len(parsed_sections)} sections** to fill.\n\n"
        + "\n".join(f"- **{s['label']}** ({s['field_type']})" for s in parsed_sections),
        actions=[{"type": "bot_b_parse", "sections_found": len(parsed_sections)}],
    )

    result_usage = _calc_cost(usage, bot_model)
    log_usage(org_id, "bot_b", result_usage, case_id=case_id, extra={"sections_found": len(parsed_sections)})
    return {
        "case_id": case_id,
        "sections_found": len(parsed_sections),
        "sections": parsed_sections,
        "usage": result_usage,
    }


# ---------------------------------------------------------------------------
# Bot C — Section Filler
# ---------------------------------------------------------------------------

BOT_C_TOOLS = [
    {
        "name": "fill_section",
        "description": "Write the completed content for a single section of the grant application.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {
                    "type": "string",
                    "description": "The section ID to fill",
                },
                "content": {
                    "type": "string",
                    "description": "The completed content for this section",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "How confident you are this answer is accurate and complete",
                },
                "needs_review_note": {
                    "type": "string",
                    "description": "Note for the officer if this section needs human review (leave empty if confident)",
                },
            },
            "required": ["section_id", "content", "confidence"],
        },
    },
]


def bot_c_fill_sections(org_id: str, case_id: str, model: str = None) -> dict:
    """Fill all parsed sections using the case data bank."""
    bot_model = model or MODEL
    case = load_case(org_id, case_id)
    if case is None:
        return {"error": f"Case {case_id} not found"}

    parsed = case.get("parsed_sections", {})
    sections = parsed.get("sections", [])
    if not sections:
        return {"error": "No parsed sections found. Run Bot B first."}

    # Build context
    databank_text = format_databank_for_prompt(org_id, case_id)
    org_text = _load_org_profile(org_id)
    grant_brief = case.get("grant_brief", {})
    donor_registry = _load_state(org_id, "donor_registry.json")
    org_docs = _load_data_files(org_id, "org")

    # Format sections to fill
    section_lines = []
    for s in sections:
        line = f"- **{s['id']}**: {s['label']} (type: {s['field_type']}"
        if s.get("max_words"):
            line += f", max {s['max_words']} words"
        if s.get("guidance"):
            line += f", guidance: {s['guidance']}"
        line += ")"
        section_lines.append(line)
    sections_text = "\n".join(section_lines)

    system_prompt = f"""You are a grant application writer for the organisation described below.

Your task: fill in every section of this grant application form using the
information available in the case data bank and organisation profile.

## Organisation Profile
{org_text[:3000]}

{databank_text}

## Grant Brief
{json.dumps(grant_brief, indent=2, default=str)[:3000]}

## Donor Registry
{json.dumps(donor_registry, indent=2, default=str)[:2000]}

{f"## Organisation Documents{chr(10)}{org_docs[:3000]}" if org_docs else ""}

## Sections to Fill
{sections_text}

## Instructions
1. Fill EVERY section by calling the fill_section tool for each one
2. Use information from the data bank and org profile
3. For factual fields (name, address, registration number), use exact data
4. For narrative sections, write professionally and specifically — no generic filler
5. Align language with the funder's priorities and terminology
6. If you lack information for a section, still provide the best draft you can
   but set confidence to "low" and add a needs_review_note
7. Respect word limits if specified
8. Write in the language of the grant application (match the form's language)

Call fill_section for EACH section. Do them all."""

    usage = _new_usage()
    client = anthropic.Anthropic()
    messages = [{
        "role": "user",
        "content": "Fill all sections of the grant application form now.",
    }]

    filled = []
    needs_review = []
    max_iterations = 15
    iterations = 0

    while iterations < max_iterations:
        iterations += 1

        response = client.messages.create(
            model=bot_model,
            max_tokens=8192,
            system=system_prompt,
            messages=messages,
            tools=BOT_C_TOOLS,
        )
        _track_usage(response, usage)

        tool_calls = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append(block)

        if not tool_calls or response.stop_reason == "end_turn":
            break

        # Process fills
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tc in tool_calls:
            if tc.name == "fill_section":
                section_id = tc.input.get("section_id", "")
                content = tc.input.get("content", "")
                if not section_id or not content:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": "Error: missing section_id or content.",
                    })
                    continue
                confidence = tc.input.get("confidence", "medium")
                review_note = tc.input.get("needs_review_note", "")

                # Save to draft sections
                from api.case_manager import update_section
                status = "review" if confidence == "low" else "draft"
                update_section(org_id, case_id, section_id, content, status)

                filled.append({
                    "section_id": section_id,
                    "confidence": confidence,
                    "review_note": review_note,
                })
                if confidence == "low" or review_note:
                    needs_review.append(section_id)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": f"Section {section_id} saved successfully.",
                })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    # Summary message
    review_text = ""
    if needs_review:
        review_items = []
        for f in filled:
            if f["section_id"] in needs_review:
                note = f.get("review_note", "Low confidence")
                review_items.append(f"  - **{f['section_id']}**: {note}")
        review_text = "\n\nSections needing review:\n" + "\n".join(review_items)

    append_message(
        org_id, case_id, "assistant",
        f"I've filled **{len(filled)}/{len(sections)} sections** of the application.{review_text}\n\n"
        "You can review and edit any section, or export the completed submission as DOCX.",
        actions=[{"type": "bot_c_fill", "filled": len(filled), "needs_review": len(needs_review)}],
    )

    result_usage = _calc_cost(usage, bot_model)
    log_usage(org_id, "bot_c", result_usage, case_id=case_id, extra={"filled": len(filled), "needs_review": len(needs_review)})
    return {
        "case_id": case_id,
        "filled": len(filled),
        "needs_review": len(needs_review),
        "sections": filled,
        "usage": result_usage,
    }


# ---------------------------------------------------------------------------
# Bot D — Q&A Bot (Guided Information Gathering)
# ---------------------------------------------------------------------------

BOT_D_TOOLS = [
    {
        "name": "save_to_databank",
        "description": "Save a piece of information to the case data bank.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "A descriptive key for this information (e.g. 'project_duration', 'target_beneficiaries_count')",
                },
                "value": {
                    "type": "string",
                    "description": "The information to store",
                },
                "category": {
                    "type": "string",
                    "enum": ["organisation", "project", "budget", "team", "impact", "logistics", "grant", "general"],
                    "description": "Category for this information",
                },
            },
            "required": ["key", "value", "category"],
        },
    },
]


def bot_d_analyze_gaps(org_id: str, case_id: str, model: str = None) -> dict:
    """Analyze the data bank and grant requirements to identify missing info."""
    bot_model = model or MODEL
    case = load_case(org_id, case_id)
    if case is None:
        return {"error": f"Case {case_id} not found"}

    databank_text = format_databank_for_prompt(org_id, case_id)
    grant_brief = case.get("grant_brief", {})
    parsed = case.get("parsed_sections", {})
    sections = parsed.get("sections", [])

    sections_text = ""
    if sections:
        sections_text = "## Form Sections to Fill\n" + "\n".join(
            f"- {s['label']} ({s['field_type']})" for s in sections
        )

    system_prompt = f"""You are an information gathering assistant for a grant application.

Your task: look at what information we already have in the data bank, and what
the grant application requires, then ask the user targeted questions to fill gaps.

{databank_text}

## Grant Brief
{json.dumps(grant_brief, indent=2, default=str)[:2000]}

{sections_text}

## Instructions
1. Compare what we have vs what we need
2. Identify the most important missing information
3. Ask 3-5 specific, answerable questions
4. Prioritise information that would be hard to guess or infer
5. Group questions by category (organisation, project, budget, etc.)
6. Be conversational and clear — the officer should be able to answer quickly

Do NOT ask about things already in the data bank.
Do NOT ask generic questions — be specific to THIS grant."""

    usage = _new_usage()
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=bot_model,
        max_tokens=1500,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": "What information do we still need for this application?",
        }],
    )
    _track_usage(response, usage)

    response_text = response.content[0].text

    append_message(
        org_id, case_id, "assistant", response_text,
        actions=[{"type": "bot_d_questions"}],
    )

    result_usage = _calc_cost(usage, bot_model)
    log_usage(org_id, "bot_d", result_usage, case_id=case_id, extra={"action": "analyze_gaps"})
    return {
        "case_id": case_id,
        "response": response_text,
        "usage": result_usage,
    }


def bot_d_process_answer(org_id: str, case_id: str, user_message: str, model: str = None) -> dict:
    """Process a user's answer to Bot D's questions. Extract and save to data bank."""
    bot_model = model or MODEL
    case = load_case(org_id, case_id)
    if case is None:
        return {"error": f"Case {case_id} not found"}

    recent = case.get("conversation", [])[-10:]
    databank_text = format_databank_for_prompt(org_id, case_id)

    system_prompt = f"""You are an information gathering assistant for a grant application.

{databank_text}

## Instructions
1. Read the user's response and extract every piece of useful information
2. For each piece of information, call save_to_databank with an appropriate key and category
3. Then either:
   a. Ask follow-up questions if the answers were incomplete or raised new questions
   b. Confirm what was saved and ask if there's anything else they'd like to add
4. Be conversational and efficient"""

    messages = []
    for msg in recent:
        role = "user" if msg["role"] == "officer" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    # Deduplicate consecutive user messages
    if len(messages) >= 2 and messages[-1]["role"] == "user" and messages[-2]["role"] == "user":
        messages = messages[:-1]

    usage = _new_usage()
    client = anthropic.Anthropic()
    saved_count = 0
    final_text = ""
    max_iterations = 5
    iterations = 0

    while iterations < max_iterations:
        iterations += 1

        response = client.messages.create(
            model=bot_model,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
            tools=BOT_D_TOOLS,
        )
        _track_usage(response, usage)

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(block)

        if text_parts:
            final_text = "\n".join(text_parts)

        if not tool_calls or response.stop_reason == "end_turn":
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tc in tool_calls:
            if tc.name == "save_to_databank":
                add_entry(
                    org_id, case_id,
                    tc.input["key"],
                    tc.input["value"],
                    source="user_input",
                    category=tc.input.get("category", "general"),
                )
                saved_count += 1
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": f"Saved '{tc.input['key']}' to data bank.",
                })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    # Save conversation
    append_message(org_id, case_id, "officer", user_message)
    append_message(
        org_id, case_id, "assistant", final_text,
        actions=[{"type": "bot_d_answer", "saved_entries": saved_count}],
    )

    result_usage = _calc_cost(usage, bot_model)
    log_usage(org_id, "bot_d", result_usage, case_id=case_id, extra={"action": "process_answer", "saved_entries": saved_count})
    return {
        "case_id": case_id,
        "response": final_text,
        "saved_entries": saved_count,
        "usage": result_usage,
    }


# ---------------------------------------------------------------------------
# File ingestion (non-bot — "Upload a file" path)
# ---------------------------------------------------------------------------

def ingest_file_to_databank(org_id: str, case_id: str, filename: str, model: str = None) -> dict:
    """Extract text from an uploaded file and save structured info to the data bank."""
    bot_model = model or MODEL
    case = load_case(org_id, case_id)
    if case is None:
        return {"error": f"Case {case_id} not found"}

    doc_text = _extract_upload_text(org_id, case_id, filename)
    if doc_text.startswith("File not found") or doc_text.startswith("Error reading"):
        return {"error": doc_text}

    if len(doc_text) > 10000:
        doc_text = doc_text[:10000] + "\n... [truncated]"

    system_prompt = """You are a document analyst. Extract key facts and information
from this document that would be useful for a grant application.

For each piece of information, call save_to_databank with a descriptive key
and the appropriate category.

Categories: organisation, project, budget, team, impact, logistics, grant, general

Extract ALL useful facts — names, numbers, dates, descriptions, capacities, etc.
Be specific in your keys (e.g. 'annual_budget_2025' not just 'budget')."""

    usage = _new_usage()
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": f"Extract key information from this document ({filename}):\n\n{doc_text}"}]
    saved_count = 0
    max_iterations = 5
    iterations = 0

    while iterations < max_iterations:
        iterations += 1

        response = client.messages.create(
            model=bot_model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=BOT_D_TOOLS,
        )
        _track_usage(response, usage)

        tool_calls = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append(block)

        if not tool_calls or response.stop_reason == "end_turn":
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tc in tool_calls:
            if tc.name == "save_to_databank":
                add_entry(
                    org_id, case_id,
                    tc.input["key"],
                    tc.input["value"],
                    source="upload",
                    category=tc.input.get("category", "general"),
                )
                saved_count += 1
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": f"Saved '{tc.input['key']}' to data bank.",
                })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    summary = f"Extracted {saved_count} data entries from {filename}."

    append_message(
        org_id, case_id, "assistant", summary,
        actions=[{"type": "file_ingest", "filename": filename, "entries_added": saved_count}],
    )

    result_usage = _calc_cost(usage, bot_model)
    log_usage(org_id, "file_ingest", result_usage, case_id=case_id, extra={"filename": filename, "entries_added": saved_count})
    return {
        "case_id": case_id,
        "entries_added": saved_count,
        "summary": summary,
        "usage": result_usage,
    }
