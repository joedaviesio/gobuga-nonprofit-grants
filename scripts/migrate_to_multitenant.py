#!/usr/bin/env python3
"""
Migrate an existing single-tenant GoBuga deployment to multi-tenant structure.

Usage:
    python scripts/migrate_to_multitenant.py --org-id ORG_ID --org-name "Org Name" --admin-email EMAIL --admin-password PASSWORD

This script:
1. Creates an org directory under orgs/{org_id}/
2. Copies cases/, cycles/, evidence/, state/, logs/, data/, prompts/ into it
3. Copies org-profile.md (or bowen.md) as the org profile
4. Creates platform/ with user and org records
5. Does NOT delete originals — copies only, script is idempotent
"""

import argparse
import json
import os
import shutil
import sys
import uuid

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def migrate(org_id: str, org_name: str, admin_email: str, admin_password: str):
    """Run the migration."""
    from api.tenant import (
        ensure_org_dirs,
        ensure_platform_dirs,
        org_root,
        org_cases_dir,
        org_cycles_dir,
        org_evidence_dir,
        org_state_dir,
        org_logs_dir,
        org_data_dir,
        org_prompts_dir,
        org_profile_path,
        users_path,
        orgs_path,
    )

    print(f"\n{'='*60}")
    print(f"  Migrating to multi-tenant")
    print(f"  Org ID: {org_id}")
    print(f"  Org Name: {org_name}")
    print(f"  Admin: {admin_email}")
    print(f"{'='*60}\n")

    # 1. Create org directory structure
    print("1. Creating org directory structure...")
    ensure_org_dirs(org_id)
    ensure_platform_dirs()

    # 2. Move data directories
    moves = [
        ("cases", org_cases_dir(org_id)),
        ("cycles", org_cycles_dir(org_id)),
        ("evidence", org_evidence_dir(org_id)),
        ("state", org_state_dir(org_id)),
        ("logs", org_logs_dir(org_id)),
        ("data", org_data_dir(org_id)),
        ("prompts", org_prompts_dir(org_id)),
    ]

    for src_name, dest_dir in moves:
        src_path = os.path.join(PROJECT_ROOT, src_name)
        if os.path.exists(src_path) and os.listdir(src_path):
            print(f"  Copying {src_name}/ → orgs/{org_id}/{src_name}/")
            for item in os.listdir(src_path):
                src_item = os.path.join(src_path, item)
                dest_item = os.path.join(dest_dir, item)
                if os.path.exists(dest_item):
                    print(f"    Skipping {item} (already exists)")
                    continue
                if os.path.isdir(src_item):
                    shutil.copytree(src_item, dest_item)
                else:
                    shutil.copy2(src_item, dest_item)
        else:
            print(f"  Skipping {src_name}/ (empty or not found)")

    # 3. Copy org profile (check common names)
    for profile_name in ["org-profile.md", "bowen.md", "profile.md"]:
        profile_src = os.path.join(PROJECT_ROOT, profile_name)
        if os.path.exists(profile_src):
            print(f"  Copying {profile_name} → org-profile.md")
            shutil.copy2(profile_src, org_profile_path(org_id))
            break

    # 4. Create platform records
    print("\n2. Creating platform records...")

    import bcrypt
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    user_id = str(uuid.uuid4())[:8]
    pw_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()

    # Users
    users = {}
    if os.path.exists(users_path()):
        with open(users_path()) as f:
            users = json.load(f)

    if not any(u.get("email") == admin_email for u in users.values()):
        users[user_id] = {
            "email": admin_email,
            "password_hash": pw_hash,
            "org_id": org_id,
            "role": "admin",
            "created": now,
        }
        with open(users_path(), "w") as f:
            json.dump(users, f, indent=2)
        print(f"  Created user: {admin_email} (id: {user_id})")
    else:
        print(f"  User {admin_email} already exists")

    # Orgs
    orgs = {}
    if os.path.exists(orgs_path()):
        with open(orgs_path()) as f:
            orgs = json.load(f)

    if org_id not in orgs:
        orgs[org_id] = {
            "name": org_name,
            "plan": "professional",
            "setup_complete": True,
            "created": now,
            "updated": now,
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
        }
        with open(orgs_path(), "w") as f:
            json.dump(orgs, f, indent=2)
        print(f"  Created org: {org_name} (id: {org_id}, plan: professional)")
    else:
        print(f"  Org {org_id} already exists")

    # 5. Update case files to include org_id
    print("\n3. Updating case records with org_id...")
    cases_dir = org_cases_dir(org_id)
    if os.path.exists(cases_dir):
        for case_name in os.listdir(cases_dir):
            case_path = os.path.join(cases_dir, case_name, "case.json")
            if os.path.exists(case_path):
                with open(case_path) as f:
                    case = json.load(f)
                if "org_id" not in case:
                    case["org_id"] = org_id
                    with open(case_path, "w") as f:
                        json.dump(case, f, indent=2)
                    print(f"  Updated {case_name}")

    print(f"\n{'='*60}")
    print(f"  Migration complete!")
    print(f"")
    print(f"  Login with:")
    print(f"    Email: {admin_email}")
    print(f"    Password: (as provided)")
    print(f"")
    print(f"  Original files are still in place.")
    print(f"  Once verified, you can remove the old top-level dirs:")
    print(f"    cases/ cycles/ evidence/ state/ logs/")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate single-tenant deployment to multi-tenant")
    parser.add_argument("--org-id", required=True, help="Org ID (e.g. 'my-nonprofit')")
    parser.add_argument("--org-name", required=True, help="Org display name")
    parser.add_argument("--admin-email", required=True, help="Admin email")
    parser.add_argument("--admin-password", required=True, help="Admin password")
    args = parser.parse_args()

    migrate(args.org_id, args.org_name, args.admin_email, args.admin_password)
