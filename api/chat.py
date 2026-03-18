"""Chat engine — Claude-backed chatbot with case context injection (multi-tenant)."""

import json
import os
import anthropic
from dotenv import load_dotenv

from api.case_manager import (
    load_case,
    append_message,
    update_section,
    _uploads_dir,
)
from api.tenant import org_profile_path, org_data_dir
from orchestrator.state_manager import load_state

from orchestrator.main import _extract_pdf, _extract_docx, _strip_html

load_dotenv()

MODEL_CHAT = "claude-sonnet-4-20250514"
MODEL_HEAVY = "claude-opus-4-20250514"


# --- Tools the chatbot can call ---

CHAT_TOOLS = [
    {
        "name": "update_section",
        "description": "Write or rewrite a section of the grant submission draft. Use this whenever the officer asks you to draft, revise, or fill in a section.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_name": {
                    "type": "string",
                    "description": "Section identifier (e.g. 'executive_summary', 'project_description', 'budget_justification')",
                },
                "content": {
                    "type": "string",
                    "description": "The full content of this section",
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "review", "complete", "blocked"],
                    "description": "Current status of this section",
                },
            },
            "required": ["section_name", "content"],
        },
    },
    {
        "name": "list_sections",
        "description": "List all current draft sections and their statuses.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "read_upload",
        "description": "Read the extracted text content of an uploaded document.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "The filename of the uploaded document to read",
                },
            },
            "required": ["filename"],
        },
    },
]


def _build_system_prompt(org_id: str, case: dict) -> str:
    """Build the chatbot system prompt with full case context."""
    parts = []

    # Role
    parts.append(
        "You are the Grants Submission Assistant. "
        "You help build grant applications using the organisation profile, donor information, "
        "and uploaded documents. You can draft and revise submission sections.\n"
        "When the user asks you to write or revise a section, use the update_section tool. "
        "Always be specific, professional, and aligned with the organisation's mission."
    )

    # Org profile
    profile_path = org_profile_path(org_id)
    if os.path.exists(profile_path):
        with open(profile_path) as f:
            parts.append(f"\n---\n## Organisation Profile\n{f.read()}")

    # Grant brief
    if case.get("grant_brief"):
        parts.append(
            "\n---\n## Grant Opportunity Brief\n```json\n"
            + json.dumps(case["grant_brief"], indent=2, default=str)
            + "\n```"
        )

    # Pipeline context
    pipeline = load_state(org_id, "grant_pipeline.json")
    if pipeline.get("opportunities"):
        parts.append(
            "\n---\n## Full Grant Pipeline\n```json\n"
            + json.dumps(pipeline["opportunities"][:10], indent=2, default=str)
            + "\n```"
        )

    # Donor registry
    donor_reg = load_state(org_id, "donor_registry.json")
    if donor_reg:
        parts.append(
            "\n---\n## Donor Registry\n```json\n"
            + json.dumps(donor_reg, indent=2, default=str)
            + "\n```"
        )

    # Current draft state
    draft = case.get("draft", {})
    if draft.get("sections"):
        draft_summary = "\n---\n## Current Draft\n"
        for name, sec in draft["sections"].items():
            status = sec.get("status", "unknown")
            content_preview = (sec.get("content") or "")[:200]
            draft_summary += f"\n### {name} [{status}]\n{content_preview}...\n"
        parts.append(draft_summary)

    # Uploaded documents list
    if case.get("uploads"):
        upload_list = "\n---\n## Uploaded Documents\n"
        for u in case["uploads"]:
            upload_list += f"- {u['filename']} ({u['type']}, {u.get('size_bytes', 0)} bytes)\n"
        upload_list += "\nUse the read_upload tool to read any of these."
        parts.append(upload_list)

    # Input data (org docs, donor docs)
    data_section = _load_data_context(org_id)
    if data_section:
        parts.append(data_section)

    return "\n".join(parts)


def _load_data_context(org_id: str) -> str:
    """Load key reference data from org data/ folders."""
    data_dir = org_data_dir(org_id)
    if not os.path.exists(data_dir):
        return ""

    sections = []
    for subfolder in ["org", "donors"]:
        folder = os.path.join(data_dir, subfolder)
        if not os.path.exists(folder):
            continue
        files_content = []
        for fname in sorted(os.listdir(folder)):
            filepath = os.path.join(folder, fname)
            if not os.path.isfile(filepath):
                continue
            ext = os.path.splitext(fname)[1].lower()
            try:
                if ext == ".pdf":
                    content = _extract_pdf(filepath, max_pages=10)
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
                if len(content) > 4000:
                    content = content[:4000] + "\n... [truncated]"
                files_content.append(f"#### {fname}\n```\n{content}\n```")
            except Exception:
                continue
        if files_content:
            sections.append(f"### {subfolder}/\n" + "\n\n".join(files_content))

    if not sections:
        return ""
    return "\n\n---\n## Reference Data\n\n" + "\n\n".join(sections)


def _extract_upload(org_id: str, case_id: str, filename: str) -> str:
    """Extract text from an uploaded file."""
    filepath = os.path.join(_uploads_dir(org_id, case_id), filename)
    if not os.path.exists(filepath):
        return f"File not found: {filename}"
    ext = os.path.splitext(filename)[1].lower()
    try:
        if ext == ".pdf":
            return _extract_pdf(filepath, max_pages=20)
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


def _handle_tool_call(tool_name: str, tool_input: dict, org_id: str, case_id: str, case: dict) -> str:
    """Execute a chatbot tool call and return the result."""
    if tool_name == "update_section":
        section = update_section(
            org_id, case_id,
            tool_input["section_name"],
            tool_input["content"],
            tool_input.get("status", "draft"),
        )
        if section:
            return json.dumps({"updated": True, "section": tool_input["section_name"], "status": section["status"]})
        return json.dumps({"error": "Failed to update section"})

    elif tool_name == "list_sections":
        draft = case.get("draft", {}).get("sections", {})
        summary = {}
        for name, sec in draft.items():
            summary[name] = {
                "status": sec.get("status", "unknown"),
                "length": len(sec.get("content") or ""),
            }
        return json.dumps(summary) if summary else '{"sections": "none yet"}'

    elif tool_name == "read_upload":
        text = _extract_upload(org_id, case_id, tool_input["filename"])
        if len(text) > 8000:
            text = text[:8000] + "\n... [truncated]"
        return text

    return f"Unknown tool: {tool_name}"


def chat(org_id: str, case_id: str, user_message: str, model: str = None) -> dict:
    """
    Process a chat message. Returns the assistant's response and any actions taken.
    """
    chat_model = model or MODEL_CHAT
    case = load_case(org_id, case_id)
    if case is None:
        return {"error": f"Case {case_id} not found"}

    # Save the user message
    append_message(org_id, case_id, "officer", user_message)

    # Build system prompt
    system_prompt = _build_system_prompt(org_id, case)

    # Build messages from conversation history (last 40 turns to stay in context)
    history = case.get("conversation", [])
    messages = []
    recent = history[-40:] if len(history) > 40 else history
    for msg in recent:
        role = "user" if msg["role"] == "officer" else "assistant"
        messages.append({"role": role, "content": msg["content"]})

    # Add the new user message
    messages.append({"role": "user", "content": user_message})

    # Deduplicate: if last two messages are both user, merge
    if len(messages) >= 2 and messages[-1]["role"] == "user" and messages[-2]["role"] == "user":
        messages = messages[:-1]

    client = anthropic.Anthropic()
    actions = []
    final_text = ""
    max_iterations = 10
    iterations = 0

    while iterations < max_iterations:
        iterations += 1

        response = client.messages.create(
            model=chat_model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=CHAT_TOOLS,
        )

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

        # Process tool calls
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tc in tool_calls:
            case = load_case(org_id, case_id)
            result = _handle_tool_call(tc.name, tc.input, org_id, case_id, case)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result,
            })
            actions.append({
                "type": tc.name,
                "input": tc.input,
            })

        messages.append({"role": "user", "content": tool_results})

    # Save assistant response
    append_message(org_id, case_id, "assistant", final_text, actions=actions if actions else None)

    return {
        "case_id": case_id,
        "response": final_text,
        "actions": actions,
    }
