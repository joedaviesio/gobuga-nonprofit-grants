"""Org setup — generates org profile and tuned prompts from wizard data."""

import os
import shutil
from datetime import datetime, timezone

from api.tenant import (
    org_profile_path,
    org_prompts_dir,
    org_data_dir,
    ensure_org_dirs,
    PROJECT_ROOT,
)
from api.auth import update_org


TEMPLATES_DIR = os.path.join(PROJECT_ROOT, "templates", "prompts")


def setup_org(org_id: str, wizard_data: dict, fetch=None) -> dict:
    """
    Generate org profile and tuned prompts from sign-up (or settings) data.

    wizard_data keys, all optional:
        org_name, country, website, charitable_status, mission, org_status,
        sectors (list), geographies (list)

    A key left out keeps the org record's current value, so an empty dict is
    a valid submission: it regenerates the profile and prompts from what the
    org already has (the name given at registration) and marks setup complete.
    An empty org_name or country never overwrites the stored one.

    A website given here also becomes the org's `website_url`, the address
    that is scraped into the org's data for the tailored cycle and the bots.

    fetch: the page fetcher used for that scrape, `fetch({"url": ...}) -> str`.
    Defaults to orchestrator.tools.handle_web_fetch; tests pass a fake.

    Returns: {"org_id", "profile_generated", "prompts_generated"}
    """
    ensure_org_dirs(org_id)

    from api.auth import get_org as _get_org
    org_record = _get_org(org_id) or {}

    def _value(key: str, record_key: str | None = None, default=""):
        if key in wizard_data:
            return wizard_data[key]
        return org_record.get(record_key or key) or default

    org_name = (_value("org_name", "name") or "").strip() or (org_record.get("name") or "").strip()
    country = (_value("country") or "").strip() or (org_record.get("country") or "").strip()
    website = (_value("website") or "").strip()
    if website and not website.startswith(("http://", "https://")):
        website = "https://" + website
    charitable_status = (_value("charitable_status") or "").strip()
    mission = (_value("mission") or "").strip()
    org_status = _value("org_status")
    sectors = _value("sectors", default=[])
    geographies = _value("geographies", default=[])

    # 1. Generate org-profile.md
    profile = _generate_org_profile(
        org_name, country, website, charitable_status, mission, sectors, geographies
    )
    with open(org_profile_path(org_id), "w") as f:
        f.write(profile)

    # 2. Generate tuned prompts from templates
    prompts_dir = org_prompts_dir(org_id)
    os.makedirs(prompts_dir, exist_ok=True)

    org_summary = _build_org_summary(org_name, country, website, mission, charitable_status)
    sectors_text = "\n".join(f"- {s}" for s in sectors) if sectors else "- General nonprofit"
    geo_text = "\n".join(f"- {g}" for g in geographies) if geographies else f"- {country}"

    replacements = {
        "{{ORG_NAME}}": org_name,
        "{{ORG_SUMMARY}}": org_summary,
        "{{SECTORS}}": sectors_text,
        "{{GEOGRAPHIES}}": geo_text,
    }

    prompts_generated = []
    for template_name in ["grant_watcher.md", "grant_analyst.md", "grant_reporter.md"]:
        template_path = os.path.join(TEMPLATES_DIR, template_name)
        if os.path.exists(template_path):
            with open(template_path) as f:
                content = f.read()
            for placeholder, value in replacements.items():
                content = content.replace(placeholder, value)
            output_path = os.path.join(prompts_dir, template_name)
            with open(output_path, "w") as f:
                f.write(content)
            prompts_generated.append(template_name)

    # 3. Scrape the website if the org has one
    website_url = website or org_record.get("website_url", "")
    if website_url:
        try:
            if fetch is None:
                from orchestrator.tools import handle_web_fetch as fetch
            scraped = fetch({"url": website_url})
            scrape_path = os.path.join(org_data_dir(org_id), "org", "website-scrape.md")
            with open(scrape_path, "w") as f:
                f.write(f"# Website Scrape: {website_url}\n\n{scraped}\n")
        except Exception as e:
            print(f"[Setup] Website scrape failed for {website_url}: {e}")

    # 4. Update org record. `seeding_complete` is left alone: registration
    # sets it False and POST /api/org/seeding-complete sets it True, so a
    # later profile edit from settings no longer sends the org back to /seed.
    updates = {
        "name": org_name,
        "country": country,
        "website": website,
        "charitable_status": charitable_status,
        "org_status": org_status,
        "mission": mission,
        "sectors": sectors,
        "geographies": geographies,
        "setup_complete": True,
    }
    if website:
        updates["website_url"] = website
    update_org(org_id, updates)

    return {
        "org_id": org_id,
        "profile_generated": True,
        "prompts_generated": prompts_generated,
        "setup_complete": True,
    }


def _generate_org_profile(
    org_name: str,
    country: str,
    website: str,
    charitable_status: str,
    mission: str,
    sectors: list,
    geographies: list,
) -> str:
    """Generate the org-profile.md document from wizard data."""
    parts = [f"# {org_name}\n"]

    # Basic info
    info_lines = []
    if country:
        info_lines.append(f"**Country:** {country}")
    if website:
        info_lines.append(f"**Website:** {website}")
    if charitable_status:
        info_lines.append(f"**Status:** {charitable_status}")
    if info_lines:
        parts.append("\n".join(info_lines))
        parts.append("")

    # Mission
    if mission:
        parts.append("## Mission\n")
        parts.append(mission)
        parts.append("")

    # Sectors
    if sectors:
        parts.append("## Priority Funding Sectors\n")
        for s in sectors:
            parts.append(f"- {s}")
        parts.append("")

    # Geographies
    if geographies:
        parts.append("## Target Geographies\n")
        for g in geographies:
            parts.append(f"- {g}")
        parts.append("")

    # Workflow guidance (generic)
    parts.append("## Grant Application Guidance\n")
    parts.append("- Lead with the organisation's track record and impact")
    parts.append("- Emphasise alignment with funder priorities")
    parts.append("- Be specific about how funding will be used")
    parts.append("- Include measurable outcomes where possible")
    parts.append("")

    parts.append(f"*Profile generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}*")

    return "\n".join(parts)


def _build_org_summary(org_name: str, country: str, website: str, mission: str, charitable_status: str) -> str:
    """Build a compact org summary for prompt injection."""
    parts = [f"**{org_name}**"]
    if country:
        parts.append(f"Based in {country}.")
    if charitable_status:
        parts.append(f"Status: {charitable_status}.")
    if mission:
        parts.append(f"Mission: {mission}")
    if website:
        parts.append(f"Website: {website}")
    return " ".join(parts)
