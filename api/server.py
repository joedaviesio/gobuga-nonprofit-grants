"""FastAPI server — multi-tenant case management + chatbot API."""

import json
import os
import traceback
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from api.auth import register, login, verify_session, logout, get_org, update_org
from api.case_manager import (
    create_case,
    load_case,
    update_case,
    list_cases,
    delete_case,
    get_draft,
    update_section,
    add_upload,
    get_conversation,
)
from api.chat import chat
from api.export import export_markdown, export_json, export_pdf, export_xlsx, export_to_file
from api.reports import list_report_dates, load_report, load_latest_report
from api.bots import (
    bot_a_generate_summary,
    bot_b_parse_document,
    bot_c_fill_sections,
    bot_d_analyze_gaps,
    bot_d_process_answer,
    bot_d_process_answer_stream,
    ingest_file_to_databank,
)
from api.databank import get_entries, add_entry, delete_entry, format_databank_for_prompt
from api.usage_log import read_usage, usage_summary

app = FastAPI(
    title="GoBuga Grants API",
    description="Multi-tenant grant scanning and submission platform",
    version="0.2.0",
)

_app_url = os.environ.get("APP_URL", "http://localhost:3002")
_allowed_origins = [
    _app_url,
    # Always allow localhost for development
    "http://localhost:3002",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redirect HTTP → HTTPS in production (when APP_URL is https)
if _app_url.startswith("https"):
    from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
    app.add_middleware(HTTPSRedirectMiddleware)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions so CORS headers are still sent."""
    tb = traceback.format_exc()
    print(f"[ERROR] {request.url}: {exc}\n{tb}")
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )


# --- Auth middleware ---

def _extract_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""


async def get_current_org(request: Request) -> str:
    """FastAPI dependency: verify auth and return org_id."""
    token = _extract_token(request)
    session = verify_session(token)
    if not session:
        raise HTTPException(401, "Invalid or expired session")
    return session["org_id"]


async def get_current_session(request: Request) -> dict:
    """FastAPI dependency: verify auth and return full session {user_id, org_id}."""
    token = _extract_token(request)
    session = verify_session(token)
    if not session:
        raise HTTPException(401, "Invalid or expired session")
    return session


# --- Auth endpoints (public) ---

class RegisterRequest(BaseModel):
    email: str
    password: str
    org_name: str
    website_url: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/register")
def api_register(req: RegisterRequest):
    """Register a new user and org."""
    try:
        result = register(req.email, req.password, req.org_name, req.website_url)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/auth/login")
def api_login(req: LoginRequest):
    """Authenticate and return a session token."""
    try:
        result = login(req.email, req.password)
        return result
    except ValueError as e:
        raise HTTPException(401, str(e))


@app.post("/api/auth/verify")
def api_verify(request: Request):
    """Verify a session token is still valid."""
    token = _extract_token(request)
    session = verify_session(token)
    if not session:
        raise HTTPException(401, "Invalid or expired session")
    org = get_org(session["org_id"])
    from api.limits import get_tier_key, get_tier, get_cycle_timer
    tier_key = get_tier_key(session["org_id"])
    tier = get_tier(session["org_id"])
    cycle_timer = get_cycle_timer(session["org_id"])
    return {
        "valid": True,
        "user_id": session["user_id"],
        "org_id": session["org_id"],
        "org_name": org["name"] if org else "",
        "setup_complete": org.get("setup_complete", False) if org else False,
        "seeding_complete": org.get("seeding_complete", True) if org else True,
        "tier": tier_key,
        "tier_label": tier["label"],
        "cycle_timer": cycle_timer,
    }


@app.post("/api/auth/logout")
def api_logout(request: Request):
    """Invalidate a session token."""
    token = _extract_token(request)
    logout(token)
    return {"ok": True}


# --- Org setup ---

class OrgSetupRequest(BaseModel):
    org_name: str
    country: str
    website: str = ""
    charitable_status: str = ""
    mission: str = ""
    org_status: str = ""
    sectors: list[str] = []
    geographies: list[str] = []


@app.post("/api/org/setup")
def api_org_setup(req: OrgSetupRequest, org_id: str = Depends(get_current_org)):
    """Save org profile from onboarding wizard and generate configs."""
    from api.org_setup import setup_org
    try:
        result = setup_org(org_id, req.model_dump())
        return result
    except Exception as e:
        print(f"[Setup error] {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"Setup failed: {e}")


class OrgUpdateRequest(BaseModel):
    name: str | None = None
    country: str | None = None
    website: str | None = None
    sectors: list[str] | None = None
    geographies: list[str] | None = None


@app.patch("/api/org/profile")
def api_org_update(req: OrgUpdateRequest, org_id: str = Depends(get_current_org)):
    """Update org profile fields and regenerate profile/prompts."""
    org = get_org(org_id)
    if not org:
        raise HTTPException(404, "Org not found")

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    # Merge with existing org data for regeneration
    merged = {**org, **updates}
    update_org(org_id, updates)

    # Regenerate profile and prompts with merged data
    try:
        from api.org_setup import setup_org
        setup_org(org_id, {
            "org_name": merged.get("name", ""),
            "country": merged.get("country", ""),
            "website": merged.get("website", ""),
            "charitable_status": merged.get("charitable_status", ""),
            "mission": merged.get("mission", ""),
            "org_status": merged.get("org_status", ""),
            "sectors": merged.get("sectors", []),
            "geographies": merged.get("geographies", []),
        })
    except Exception as e:
        print(f"[Profile update] Regeneration failed: {e}\n{traceback.format_exc()}")

    return {**get_org(org_id), "profile_text": ""}


@app.get("/api/org/profile")
def api_org_profile(org_id: str = Depends(get_current_org)):
    """Get org info and profile."""
    org = get_org(org_id)
    if not org:
        raise HTTPException(404, "Org not found")

    from api.tenant import org_profile_path
    profile_text = ""
    profile_path = org_profile_path(org_id)
    if os.path.exists(profile_path):
        with open(profile_path) as f:
            profile_text = f.read()

    return {
        **org,
        "profile_text": profile_text,
    }


@app.get("/api/org/usage")
def api_org_usage(org_id: str = Depends(get_current_org), date: str = None):
    """Get usage summary for the org."""
    return usage_summary(org_id, date)


ALLOWED_DOC_TYPES = [
    "annual-reports",
    "mission-statements",
    "organisational-reviews",
    "previous-applications",
    "financial-statements",
    "general",
]


@app.post("/api/org/upload")
async def api_org_upload(
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    org_id: str = Depends(get_current_org),
):
    """Upload an org document for seeding."""
    if doc_type not in ALLOWED_DOC_TYPES:
        raise HTTPException(400, f"Invalid doc_type. Must be one of: {ALLOWED_DOC_TYPES}")
    from api.tenant import org_data_dir
    org_dir = os.path.join(org_data_dir(org_id), "org")
    os.makedirs(org_dir, exist_ok=True)
    safe_filename = file.filename.replace("/", "_").replace("\\", "_")
    dest = os.path.join(org_dir, f"{doc_type}_{safe_filename}")
    if not Path(dest).resolve().is_relative_to(Path(org_dir).resolve()):
        raise HTTPException(400, "Invalid filename")
    contents = await file.read()
    with open(dest, "wb") as f:
        f.write(contents)
    return {"filename": f"{doc_type}_{safe_filename}", "size": len(contents), "doc_type": doc_type}


@app.get("/api/org/uploads")
def api_org_uploads(org_id: str = Depends(get_current_org)):
    """List uploaded org documents."""
    from api.tenant import org_data_dir
    org_dir = os.path.join(org_data_dir(org_id), "org")
    if not os.path.isdir(org_dir):
        return []
    files = []
    for name in sorted(os.listdir(org_dir)):
        path = os.path.join(org_dir, name)
        if os.path.isfile(path):
            files.append({"filename": name, "size": os.path.getsize(path)})
    return files


@app.delete("/api/org/uploads/{filename}")
def api_org_delete_upload(filename: str, org_id: str = Depends(get_current_org)):
    """Delete an uploaded org document."""
    from api.tenant import org_data_dir
    org_dir = os.path.join(org_data_dir(org_id), "org")
    filepath = os.path.join(org_dir, filename)
    if not Path(filepath).resolve().is_relative_to(Path(org_dir).resolve()):
        raise HTTPException(400, "Invalid filename")
    if not os.path.exists(filepath):
        raise HTTPException(404, f"File not found: {filename}")
    os.remove(filepath)
    return {"deleted": filename}


@app.post("/api/org/seeding-complete")
def api_seeding_complete(org_id: str = Depends(get_current_org)):
    """Mark org seeding as complete."""
    update_org(org_id, {"seeding_complete": True})
    return {"ok": True}


# --- Tier management ---

@app.post("/api/org/toggle-tier")
def api_toggle_tier(org_id: str = Depends(get_current_org)):
    """Toggle between scanner and officer tiers (dev toggle)."""
    from api.limits import toggle_tier, get_tier, get_cycle_timer
    new_tier_key = toggle_tier(org_id)
    tier = get_tier(org_id)
    cycle_timer = get_cycle_timer(org_id)
    return {
        "tier": new_tier_key,
        "tier_label": tier["label"],
        "cycle_timer": cycle_timer,
    }


@app.get("/api/org/tier")
def api_get_tier(org_id: str = Depends(get_current_org)):
    """Get current tier info and cycle timer."""
    from api.limits import get_tier_key, get_tier, get_cycle_timer, can_trigger_cycle
    tier_key = get_tier_key(org_id)
    tier = get_tier(org_id)
    cycle_timer = get_cycle_timer(org_id)
    can_trigger = can_trigger_cycle(org_id)
    return {
        "tier": tier_key,
        "tier_label": tier["label"],
        "cycle_timer": cycle_timer,
        "can_trigger_cycle": can_trigger["allowed"],
        "trigger_message": can_trigger["message"],
    }


class TriggerCycleRequest(BaseModel):
    password: str


@app.post("/api/org/trigger-cycle")
def api_trigger_cycle(req: TriggerCycleRequest, session: dict = Depends(get_current_session)):
    """Trigger a new cycle with password verification."""
    from api.limits import can_trigger_cycle, trigger_cycle
    from api.auth import verify_password

    org_id = session["org_id"]
    user_id = session["user_id"]

    # Verify password
    if not verify_password(user_id, req.password):
        raise HTTPException(403, "Invalid password")

    # Check if cycle can be triggered
    check = can_trigger_cycle(org_id)
    if not check["allowed"]:
        raise HTTPException(409, check["message"])

    # Trigger the cycle
    timer = trigger_cycle(org_id)
    return {"ok": True, "cycle_timer": timer}


# --- Request models ---

class CreateCaseRequest(BaseModel):
    grant_id: str
    grant_brief: dict = {}
    officer: str = "grants_officer"


class UpdateCaseRequest(BaseModel):
    status: str | None = None
    signoff: dict | None = None
    officer: str | None = None
    grant_brief: dict | None = None


class ChatRequest(BaseModel):
    message: str


class UpdateSectionRequest(BaseModel):
    content: str
    status: str = "draft"


class ExportRequest(BaseModel):
    format: str = "markdown"
    template_filename: str | None = None


class SignoffRequest(BaseModel):
    approved: bool
    note: str = ""
    signer: str = "grants_officer"


# --- Case CRUD ---

@app.get("/api/cases")
def api_list_cases(org_id: str = Depends(get_current_org)):
    return list_cases(org_id)


@app.post("/api/cases")
def api_create_case(req: CreateCaseRequest, org_id: str = Depends(get_current_org)):
    from api.limits import check_case_limit
    limit_check = check_case_limit(org_id)
    if not limit_check["allowed"]:
        raise HTTPException(403, limit_check["message"])
    case = create_case(org_id, req.grant_id, req.grant_brief, req.officer)
    return case


@app.get("/api/cases/{case_id}")
def api_get_case(case_id: str, org_id: str = Depends(get_current_org)):
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    return case


@app.patch("/api/cases/{case_id}")
def api_update_case(case_id: str, req: UpdateCaseRequest, org_id: str = Depends(get_current_org)):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    # Reopening a case counts against the open-case limit
    if updates.get("status") == "open":
        from api.limits import check_case_limit
        existing = load_case(org_id, case_id)
        if existing and existing.get("status") != "open":
            limit_check = check_case_limit(org_id)
            if not limit_check["allowed"]:
                raise HTTPException(403, limit_check["message"])
    case = update_case(org_id, case_id, updates)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    return case


@app.delete("/api/cases/{case_id}")
def api_delete_case(case_id: str, org_id: str = Depends(get_current_org)):
    if not delete_case(org_id, case_id):
        raise HTTPException(404, f"Case {case_id} not found")
    return {"deleted": case_id}


# --- Chat ---

@app.post("/api/cases/{case_id}/chat")
def api_chat(case_id: str, req: ChatRequest, org_id: str = Depends(get_current_org)):
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    result = chat(org_id, case_id, req.message)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.get("/api/cases/{case_id}/conversation")
def api_get_conversation(case_id: str, org_id: str = Depends(get_current_org)):
    conv = get_conversation(org_id, case_id)
    if conv is None:
        raise HTTPException(404, f"Case {case_id} not found")
    return conv


# --- Uploads ---

@app.post("/api/cases/{case_id}/upload")
async def api_upload(
    case_id: str,
    file: UploadFile = File(...),
    file_type: str = Form("document"),
    org_id: str = Depends(get_current_org),
):
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    file_bytes = await file.read()
    record = add_upload(org_id, case_id, file.filename, file_bytes, file_type)
    if record is None:
        raise HTTPException(500, "Failed to save upload")
    return record


# --- Draft ---

@app.get("/api/cases/{case_id}/draft")
def api_get_draft(case_id: str, org_id: str = Depends(get_current_org)):
    draft = get_draft(org_id, case_id)
    if draft is None:
        raise HTTPException(404, f"Case {case_id} not found")
    return draft


@app.patch("/api/cases/{case_id}/draft/{section_name}")
def api_update_section(case_id: str, section_name: str, req: UpdateSectionRequest, org_id: str = Depends(get_current_org)):
    section = update_section(org_id, case_id, section_name, req.content, req.status)
    if section is None:
        raise HTTPException(404, f"Case {case_id} not found")
    return section


# --- Export ---

@app.post("/api/cases/{case_id}/export")
def api_export(case_id: str, req: ExportRequest, org_id: str = Depends(get_current_org)):
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")

    FILE_FORMATS = {"docx", "doc", "pdf", "xlsx", "xls"}

    if req.format == "markdown":
        content = export_markdown(org_id, case_id)
        filepath = export_to_file(org_id, case_id, "markdown")
        return {"format": "markdown", "content": content, "file": filepath}
    elif req.format == "json":
        content = export_json(org_id, case_id)
        filepath = export_to_file(org_id, case_id, "json")
        return {"format": "json", "content": content, "file": filepath}
    elif req.format in FILE_FORMATS:
        filepath = export_to_file(org_id, case_id, req.format, template_filename=req.template_filename)
        if filepath is None:
            raise HTTPException(500, f"{req.format.upper()} export failed")
        return {"format": req.format, "file": filepath, "filename": os.path.basename(filepath)}
    else:
        raise HTTPException(400, f"Unsupported format: {req.format}. Use 'markdown', 'json', 'pdf', 'docx', 'doc', 'xlsx', or 'xls'.")


# --- Download ---

@app.get("/api/cases/{case_id}/download/{filename}")
def api_download(case_id: str, filename: str, org_id: str = Depends(get_current_org)):
    from api.case_manager import _case_dir
    exports_dir = os.path.join(_case_dir(org_id, case_id), "exports")
    filepath = os.path.join(exports_dir, filename)
    if not Path(filepath).resolve().is_relative_to(Path(exports_dir).resolve()):
        raise HTTPException(400, "Invalid filename")
    if not os.path.exists(filepath):
        raise HTTPException(404, f"File not found: {filename}")
    return FileResponse(filepath, filename=filename)


# --- Signoff ---

@app.post("/api/cases/{case_id}/signoff")
def api_signoff(case_id: str, req: SignoffRequest, org_id: str = Depends(get_current_org)):
    from datetime import datetime, timezone
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")

    signoff = {
        "approved": req.approved,
        "note": req.note,
        "signer": req.signer,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    new_status = "approved" if req.approved else "rejected"
    result = update_case(org_id, case_id, {"signoff": signoff, "status": new_status})
    return result


# --- Reports & Dashboard ---

@app.get("/api/reports")
def api_list_reports(org_id: str = Depends(get_current_org)):
    return list_report_dates(org_id)


@app.get("/api/reports/latest")
def api_latest_report(org_id: str = Depends(get_current_org)):
    report = load_latest_report(org_id)
    if report is None:
        raise HTTPException(404, "No reports found")
    from api.limits import filter_opportunities_for_tier
    report["opportunities"] = filter_opportunities_for_tier(report.get("opportunities", []), org_id)
    return report


@app.get("/api/reports/{cycle_date}")
def api_get_report(cycle_date: str, org_id: str = Depends(get_current_org)):
    report = load_report(org_id, cycle_date)
    if report is None:
        raise HTTPException(404, f"No report for {cycle_date}")
    from api.limits import filter_opportunities_for_tier
    report["opportunities"] = filter_opportunities_for_tier(report.get("opportunities", []), org_id)
    return report


class CreateCaseFromOpportunity(BaseModel):
    opportunity_id: str
    cycle_date: str


@app.post("/api/reports/open-case")
def api_open_case_from_opportunity(req: CreateCaseFromOpportunity, org_id: str = Depends(get_current_org)):
    """Create a case directly from a report opportunity."""
    from api.limits import check_case_limit
    limit_check = check_case_limit(org_id)
    if not limit_check["allowed"]:
        raise HTTPException(403, limit_check["message"])

    report = load_report(org_id, req.cycle_date)
    if report is None:
        raise HTTPException(404, f"No report for {req.cycle_date}")

    opp = None
    for o in report.get("opportunities", []):
        if o["id"] == req.opportunity_id:
            opp = o
            break
    if opp is None:
        raise HTTPException(404, f"Opportunity {req.opportunity_id} not found")

    grant_brief = {
        "title": opp.get("title", ""),
        "description": opp.get("description", ""),
        "deadline": opp.get("deadline"),
        "amount": opp.get("amount"),
        "funder": opp.get("funder"),
        "priority": opp.get("priority", "medium"),
        "recommendation": opp.get("recommendation"),
        "details": opp.get("details", []),
        "source_urls": opp.get("source_urls", []),
        "source_cycle": req.cycle_date,
        "opportunity_id": req.opportunity_id,
    }

    related_evidence = []
    opp_title_lower = opp.get("title", "").lower()
    for ev in report.get("evidence", []):
        if ev.get("type") in ("grant_opportunity", "analysis", "recommendation"):
            ev_title_lower = ev.get("title", "").lower()
            title_words = set(opp_title_lower.split())
            ev_words = set(ev_title_lower.split())
            if len(title_words & ev_words) >= 2:
                related_evidence.append(ev)
    grant_brief["related_evidence"] = related_evidence

    grant_id = opp.get("title", "unknown").lower()
    grant_id = "-".join(grant_id.split()[:5])
    import re
    grant_id = re.sub(r'[^a-z0-9-]', '', grant_id)

    case = create_case(org_id, grant_id, grant_brief)
    return case


# --- Cycle Runner ---

_cycle_state = {
    "running": False,
    "org_id": None,
    "started_at": None,
    "completed_at": None,
    "error": None,
    "phase": None,
}


class RunCycleRequest(BaseModel):
    date: str | None = None


@app.post("/api/cycle/run")
def api_run_cycle(req: RunCycleRequest, org_id: str = Depends(get_current_org)):
    if _cycle_state["running"]:
        raise HTTPException(409, "A cycle is already running")

    from orchestrator.main import run_cycle
    from datetime import datetime, timezone
    import threading

    cycle_date = req.date

    _cycle_state["running"] = True
    _cycle_state["org_id"] = org_id
    _cycle_state["started_at"] = datetime.now(timezone.utc).isoformat()
    _cycle_state["completed_at"] = None
    _cycle_state["error"] = None
    _cycle_state["phase"] = "starting"

    # Determine model overrides based on plan
    from api.limits import get_model_overrides
    model_overrides = get_model_overrides(org_id)

    def _on_phase(phase: str):
        _cycle_state["phase"] = phase

    def _run():
        try:
            run_cycle(org_id, cycle_date, model_overrides=model_overrides, on_phase=_on_phase)
            _cycle_state["phase"] = "complete"
            _cycle_state["completed_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            _cycle_state["error"] = str(e)
            print(f"Cycle error: {e}")
        finally:
            _cycle_state["running"] = False

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"status": "started", "date": cycle_date or "today"}


@app.get("/api/cycle/status")
def api_cycle_status(org_id: str = Depends(get_current_org)):
    """Check cycle run status."""
    from datetime import date as date_type

    if _cycle_state["running"]:
        return {
            "status": "running",
            "started_at": _cycle_state["started_at"],
            "phase": _cycle_state.get("phase"),
        }

    if _cycle_state["error"]:
        return {
            "status": "error",
            "error": _cycle_state["error"],
        }

    if _cycle_state["completed_at"]:
        today = date_type.today().isoformat()
        report = load_report(org_id, today)
        opps = len(report["opportunities"]) if report else 0
        return {
            "status": "complete",
            "date": today,
            "completed_at": _cycle_state["completed_at"],
            "opportunities": opps,
        }

    return {"status": "idle"}


# --- Bots ---

@app.post("/api/cases/{case_id}/bot/summary")
def api_bot_summary(case_id: str, org_id: str = Depends(get_current_org)):
    """Bot A: Generate tailored grant summary."""
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    try:
        result = bot_a_generate_summary(org_id, case_id)
    except Exception as e:
        print(f"[Bot A error] {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"Bot A failed: {e}")
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


class ParseDocumentRequest(BaseModel):
    filename: str


@app.post("/api/cases/{case_id}/bot/parse")
def api_bot_parse(case_id: str, req: ParseDocumentRequest, org_id: str = Depends(get_current_org)):
    """Bot B: Parse submission document to extract sections."""
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    try:
        result = bot_b_parse_document(org_id, case_id, req.filename)
    except Exception as e:
        print(f"[Bot B error] {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"Bot B failed: {e}")
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/cases/{case_id}/bot/fill")
def api_bot_fill(case_id: str, org_id: str = Depends(get_current_org)):
    """Bot C: Fill all parsed sections from the data bank."""
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    try:
        result = bot_c_fill_sections(org_id, case_id)
    except Exception as e:
        print(f"[Bot C error] {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"Bot C failed: {e}")
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/cases/{case_id}/bot/parse-and-fill")
def api_bot_parse_and_fill(case_id: str, req: ParseDocumentRequest, org_id: str = Depends(get_current_org)):
    """Bot B + C chained: Parse submission doc then auto-fill sections."""
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    try:
        parse_result = bot_b_parse_document(org_id, case_id, req.filename)
        if "error" in parse_result:
            raise HTTPException(400, parse_result["error"])
        fill_result = bot_c_fill_sections(org_id, case_id)
        if "error" in fill_result:
            raise HTTPException(400, fill_result["error"])
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Bot B+C error] {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"Bot B+C failed: {e}")
    parse_usage = parse_result.get("usage", {})
    fill_usage = fill_result.get("usage", {})
    combined_usage = {
        "input_tokens": parse_usage.get("input_tokens", 0) + fill_usage.get("input_tokens", 0),
        "output_tokens": parse_usage.get("output_tokens", 0) + fill_usage.get("output_tokens", 0),
        "api_calls": parse_usage.get("api_calls", 0) + fill_usage.get("api_calls", 0),
        "cost_usd": round(parse_usage.get("cost_usd", 0) + fill_usage.get("cost_usd", 0), 4),
        "model": fill_usage.get("model", parse_usage.get("model", "")),
        "breakdown": {"bot_b": parse_usage, "bot_c": fill_usage},
    }
    return {
        "case_id": case_id,
        "parsed": parse_result["sections_found"],
        "filled": fill_result["filled"],
        "needs_review": fill_result["needs_review"],
        "sections": fill_result["sections"],
        "usage": combined_usage,
    }


@app.post("/api/cases/{case_id}/bot/questions")
def api_bot_questions(case_id: str, org_id: str = Depends(get_current_org)):
    """Bot D: Analyze gaps and generate questions."""
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    try:
        result = bot_d_analyze_gaps(org_id, case_id)
    except Exception as e:
        print(f"[Bot D error] {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"Bot D failed: {e}")
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


class AnswerRequest(BaseModel):
    message: str


@app.post("/api/cases/{case_id}/bot/answer")
def api_bot_answer(case_id: str, req: AnswerRequest, org_id: str = Depends(get_current_org)):
    """Bot D: Process user's answer and save to data bank."""
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    try:
        result = bot_d_process_answer(org_id, case_id, req.message)
    except Exception as e:
        print(f"[Bot D answer error] {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"Bot D failed: {e}")
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/cases/{case_id}/bot/answer/stream")
def api_bot_answer_stream(case_id: str, req: AnswerRequest, org_id: str = Depends(get_current_org)):
    """Bot D: Streaming version — process user's answer with SSE text chunks."""
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    return StreamingResponse(
        bot_d_process_answer_stream(org_id, case_id, req.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class IngestFileRequest(BaseModel):
    filename: str


@app.post("/api/cases/{case_id}/bot/ingest")
def api_bot_ingest(case_id: str, req: IngestFileRequest, org_id: str = Depends(get_current_org)):
    """Ingest uploaded file into the data bank."""
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    try:
        result = ingest_file_to_databank(org_id, case_id, req.filename)
    except Exception as e:
        print(f"[Ingest error] {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"Ingest failed: {e}")
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


# --- Data Bank ---

@app.get("/api/cases/{case_id}/databank")
def api_get_databank(case_id: str, category: str = None, org_id: str = Depends(get_current_org)):
    """Get all data bank entries for a case."""
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    return get_entries(org_id, case_id, category)


class DatabankEntryRequest(BaseModel):
    key: str
    value: str
    source: str = "user_input"
    category: str = "general"


@app.post("/api/cases/{case_id}/databank")
def api_add_databank_entry(case_id: str, req: DatabankEntryRequest, org_id: str = Depends(get_current_org)):
    """Manually add an entry to the data bank."""
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    entry = add_entry(org_id, case_id, req.key, req.value, req.source, req.category)
    return entry


@app.delete("/api/cases/{case_id}/databank/{key}")
def api_delete_databank_entry(case_id: str, key: str, org_id: str = Depends(get_current_org)):
    """Delete a data bank entry."""
    case = load_case(org_id, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    if not delete_entry(org_id, case_id, key):
        raise HTTPException(404, f"Entry '{key}' not found")
    return {"deleted": key}


# --- Usage Logs ---

@app.get("/api/usage")
def api_usage_summary(date: str = None, org_id: str = Depends(get_current_org)):
    """Get usage summary for a given NZ date (defaults to today)."""
    return usage_summary(org_id, date)


@app.get("/api/usage/log")
def api_usage_log(date: str = None, org_id: str = Depends(get_current_org)):
    """Get raw usage records for a given NZ date."""
    return read_usage(org_id, date)


# --- Billing ---

class CheckoutRequest(BaseModel):
    plan: str  # "starter" or "professional"


@app.post("/api/billing/checkout")
def api_billing_checkout(req: CheckoutRequest, org_id: str = Depends(get_current_org)):
    """Create a Stripe Checkout session for plan upgrade."""
    from api.billing import create_checkout_session
    try:
        result = create_checkout_session(org_id, req.plan)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Billing error: {e}")


@app.get("/api/billing/portal")
def api_billing_portal(org_id: str = Depends(get_current_org)):
    """Get Stripe Customer Portal URL."""
    from api.billing import create_portal_session
    try:
        result = create_portal_session(org_id)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Billing error: {e}")


@app.post("/api/billing/webhook")
async def api_billing_webhook(request: Request):
    """Handle Stripe webhook events (no auth required — verified by signature)."""
    from api.billing import handle_webhook
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        result = handle_webhook(payload, sig_header)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


# --- Health (public) ---

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "gobuga-grants-api"}
