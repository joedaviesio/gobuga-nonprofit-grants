# GoBuga Platform Wireframe

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        GOBUGA PLATFORM                              │
│                                                                     │
│  ┌──────────────────────┐         ┌──────────────────────────────┐  │
│  │   FRONTEND (Next.js) │ ──API── │      BACKEND (FastAPI)       │  │
│  │   localhost:3000      │ ──────→ │      localhost:8111          │  │
│  └──────────────────────┘         └──────────────────────────────┘  │
│                                            │                        │
│                              ┌─────────────┼─────────────┐         │
│                              ▼             ▼             ▼          │
│                        ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│                        │ Anthropic│ │  Tavily  │ │  Stripe  │     │
│                        │ Claude   │ │  Search  │ │ Billing  │     │
│                        └──────────┘ └──────────┘ └──────────┘     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Frontend Wireframes

### 1. Login Page (`/login`)

```
┌─────────────────────────────────────────────────────────────┐
│  🔷 GoBuga  Grant Scanner                        [Log out]  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                  ┌─────────────────────┐                    │
│                  │     Sign In         │                    │
│                  │                     │                    │
│                  │  Email              │                    │
│                  │  ┌─────────────────┐│                    │
│                  │  │                 ││                    │
│                  │  └─────────────────┘│                    │
│                  │                     │                    │
│                  │  Password           │                    │
│                  │  ┌─────────────────┐│                    │
│                  │  │                 ││                    │
│                  │  └─────────────────┘│                    │
│                  │                     │                    │
│                  │  [    Sign In     ] │                    │
│                  │                     │                    │
│                  │  No account?        │                    │
│                  │  Register here →    │                    │
│                  └─────────────────────┘                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2. Register Page (`/register`)

```
┌─────────────────────────────────────────────────────────────┐
│  🔷 GoBuga  Grant Scanner                                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                  ┌─────────────────────┐                    │
│                  │   Create Account    │                    │
│                  │                     │                    │
│                  │  Organisation Name  │                    │
│                  │  ┌─────────────────┐│                    │
│                  │  │                 ││                    │
│                  │  └─────────────────┘│                    │
│                  │                     │                    │
│                  │  Email              │                    │
│                  │  ┌─────────────────┐│                    │
│                  │  │                 ││                    │
│                  │  └─────────────────┘│                    │
│                  │                     │                    │
│                  │  Password           │                    │
│                  │  ┌─────────────────┐│                    │
│                  │  │                 ││                    │
│                  │  └─────────────────┘│                    │
│                  │                     │                    │
│                  │  [ Create Account ] │                    │
│                  │                     │                    │
│                  │  Have an account?   │                    │
│                  │  Sign in here →     │                    │
│                  └─────────────────────┘                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3. Setup Wizard (`/setup`)

```
┌─────────────────────────────────────────────────────────────┐
│  🔷 GoBuga  Grant Scanner                        [Log out]  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Step [1] ─── [2] ─── [3]                                   │
│   Basics    Sectors   Scan                                  │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  STEP 1: BASICS                                        │ │
│  │                                                        │ │
│  │  Organisation Name     Country                         │ │
│  │  ┌──────────────────┐  ┌──────────────────┐            │ │
│  │  │                  │  │ New Zealand    ▾ │            │ │
│  │  └──────────────────┘  └──────────────────┘            │ │
│  │                                                        │ │
│  │  Website               Charitable Status               │ │
│  │  ┌──────────────────┐  ┌──────────────────┐            │ │
│  │  │                  │  │ Registered    ▾ │            │ │
│  │  └──────────────────┘  └──────────────────┘            │ │
│  │                                                        │ │
│  │  Mission Statement                                     │ │
│  │  ┌──────────────────────────────────────────────┐      │ │
│  │  │                                              │      │ │
│  │  │                                              │      │ │
│  │  └──────────────────────────────────────────────┘      │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  STEP 2: SECTORS & GEOGRAPHIES                         │ │
│  │                                                        │ │
│  │  Priority Sectors (select all that apply)              │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │ │
│  │  │☑ Health  │ │☑ Educ.  │ │☐ Enviro.│ │☐ Housing│  │ │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘  │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │ │
│  │  │☐ Digital │ │☐ AI     │ │☐ Justice│ │☐ Youth  │  │ │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘  │ │
│  │                                                        │ │
│  │  Target Geographies                                    │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │ │
│  │  │☑ NZ     │ │☐ AU     │ │☐ UK     │ │☐ Global │  │ │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘  │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  STEP 3: FIRST SCAN                                    │ │
│  │                                                        │ │
│  │  Your profile is ready!                                │ │
│  │  We'll now run your first grant scan.                  │ │
│  │                                                        │ │
│  │  [  Complete Setup & Start Scanning  ]                 │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 4. Dashboard — Daily Brief View (`/`)

```
┌─────────────────────────────────────────────────────────────┐
│  🔷 GoBuga  Grant Scanner                        [Log out]  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [ Daily Brief ]  [ Cases ]  [ Run Cycle ]  [ Settings ]    │
│  ═══════════════                                            │
│                                                             │
│  Daily Grant Brief — 18 Mar 2026                            │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  ACTION REQUIRED                                       │ │
│  │  ──────────────────                                    │ │
│  │  Markdown-rendered urgent items from the report        │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  TOP OPPORTUNITIES                                     │ │
│  │  ──────────────────                                    │ │
│  │                                                        │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │ Community Development Fund       [HIGH] [APPLY]  │  │ │
│  │  │ Funder: XYZ Foundation                           │  │ │
│  │  │ Amount: $50,000 – $100,000                       │  │ │
│  │  │ Deadline: 30 Apr 2026                            │  │ │
│  │  │ • Requirement detail 1                           │  │ │
│  │  │ • Requirement detail 2                           │  │ │
│  │  │ Sources: [Link 1] [Link 2]                       │  │ │
│  │  │                              [ Open Case ]       │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  │                                                        │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │ Digital Inclusion Grant          [MED] [MONITOR] │  │ │
│  │  │ Funder: ABC Trust                                │  │ │
│  │  │ Amount: $20,000                                  │  │ │
│  │  │ Deadline: 15 Jun 2026                            │  │ │
│  │  │                              [ Open Case ]       │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  DONOR INTELLIGENCE                                    │ │
│  │  ──────────────────                                    │ │
│  │  Markdown-rendered donor news & insights               │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  PIPELINE UPDATE                                       │ │
│  │  ──────────────────                                    │ │
│  │  Status of active opportunities being tracked          │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  GAPS & RECOMMENDATIONS                                │ │
│  │  ──────────────────────                                │ │
│  │  Missing sectors/funders, suggested actions            │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 5. Dashboard — Cases View

```
┌─────────────────────────────────────────────────────────────┐
│  🔷 GoBuga  Grant Scanner                        [Log out]  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [ Daily Brief ]  [ Cases ]  [ Run Cycle ]  [ Settings ]    │
│                    ═════════                                │
│                                                             │
│  Active Cases                                               │
│                                                             │
│  [ Open ] [ Submitted ] [ Accepted ] [ Rejected ] [ Closed ]│
│  ════════                                                   │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  CASE-2026-0001                              [OPEN]    │ │
│  │  Community Development Fund                            │ │
│  │  Created: 18 Mar 2026                                  │ │
│  │  Sections: 3/8 complete                                │ │
│  │                                        [ View → ]      │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  CASE-2026-0002                              [OPEN]    │ │
│  │  Digital Inclusion Grant                               │ │
│  │  Created: 17 Mar 2026                                  │ │
│  │  Sections: 0/5 complete                                │ │
│  │                                        [ View → ]      │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 6. Dashboard — Run Cycle View

```
┌─────────────────────────────────────────────────────────────┐
│  🔷 GoBuga  Grant Scanner                        [Log out]  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [ Daily Brief ]  [ Cases ]  [ Run Cycle ]  [ Settings ]    │
│                               ═══════════                   │
│                                                             │
│  Grant Scan Cycle                                           │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Status: ● IDLE                                        │ │
│  │                                                        │ │
│  │  Start a new scan to discover grant opportunities      │ │
│  │  tailored to your organisation.                        │ │
│  │  Estimated time: 5–15 minutes.                         │ │
│  │                                                        │ │
│  │  [  Start New Scan  ]                                  │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  Previous Scans                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  2026-03-18  ●  12 opportunities found   [ View → ]   │ │
│  │  2026-03-17  ●   8 opportunities found   [ View → ]   │ │
│  │  2026-03-15  ●  15 opportunities found   [ View → ]   │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ── When running ──                                         │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Status: ● RUNNING                                     │ │
│  │                                                        │ │
│  │  Phase 1: Grant Watcher     ████████████░░  80%        │ │
│  │  Phase 2: Grant Analyst     ░░░░░░░░░░░░░░  waiting    │ │
│  │  Phase 3: Grant Reporter    ░░░░░░░░░░░░░░  waiting    │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 7. Case Detail Page (`/case/[id]`)

```
┌─────────────────────────────────────────────────────────────┐
│  🔷 GoBuga  Grant Scanner                        [Log out]  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ← Back to Cases                                            │
│                                                             │
│  CASE-2026-0001: Community Development Fund       [OPEN]    │
│  Officer: grants_officer                                    │
│                                                             │
│  ┌──────────────────────────┬───────────────────────────┐   │
│  │  GRANT BRIEF             │  ACTIONS                  │   │
│  │  ──────────              │  ────────                 │   │
│  │  Funder: XYZ Foundation  │  [ Upload File        ]   │   │
│  │  Amount: $50k–$100k      │  [ Upload Submission  ]   │   │
│  │  Deadline: 30 Apr 2026   │  [ Parse & Fill (B+C) ]   │   │
│  │  Description: ...        │  [ Ask Questions (D)  ]   │   │
│  │                          │  [ Export ▾           ]   │   │
│  │                          │    Markdown | JSON | DOCX │   │
│  │                          │                           │   │
│  │                          │  SIGNOFF                  │   │
│  │                          │  [ Approve ] [ Reject ]   │   │
│  └──────────────────────────┴───────────────────────────┘   │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  DRAFT SECTIONS                                        │ │
│  │  ──────────────                                        │ │
│  │                                                        │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │ Executive Summary                  [COMPLETE] ✓  │  │ │
│  │  │ Our organisation is dedicated to...              │  │ │
│  │  │                                     [ Edit ]     │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  │                                                        │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │ Project Description                [REVIEW] ⚠    │  │ │
│  │  │ The proposed project will...                     │  │ │
│  │  │ Review note: Needs budget figures                │  │ │
│  │  │                                     [ Edit ]     │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  │                                                        │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │ Budget Narrative                   [DRAFT]       │  │ │
│  │  │ (auto-filled, low confidence)                    │  │ │
│  │  │                                     [ Edit ]     │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  DATA BANK                                             │ │
│  │  ─────────                                             │ │
│  │  Key                    Value            Source         │ │
│  │  ───                    ─────            ──────         │ │
│  │  organization_name      Example NPO      org_profile   │ │
│  │  annual_budget          $500,000          upload        │ │
│  │  beneficiaries_count    2,500             user_input    │ │
│  │  mission_statement      We serve...       org_profile   │ │
│  │                                                        │ │
│  │  [ + Add Entry ]                                       │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  UPLOADS                                               │ │
│  │  ───────                                               │ │
│  │  📄 annual-report-2025.pdf       (1.2 MB)  18 Mar      │ │
│  │  📄 submission-form.docx         (245 KB)  18 Mar      │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  CHAT                                                  │ │
│  │  ────                                                  │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │  🤖 I've reviewed the grant brief. You'll need   │  │ │
│  │  │     to provide budget figures for Section 3.     │  │ │
│  │  │                                                  │  │ │
│  │  │  👤 Our total project budget is $75,000 with     │  │ │
│  │  │     $25k from existing funding.                  │  │ │
│  │  │                                                  │  │ │
│  │  │  🤖 Got it. I've updated the Budget Narrative    │  │ │
│  │  │     section with those figures.                  │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  │                                                        │ │
│  │  ┌──────────────────────────────────────┐ [ Send ]     │ │
│  │  │ Type a message...                    │              │ │
│  │  └──────────────────────────────────────┘              │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 8. Settings Page (`/settings`)

```
┌─────────────────────────────────────────────────────────────┐
│  🔷 GoBuga  Grant Scanner                        [Log out]  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [ Daily Brief ]  [ Cases ]  [ Run Cycle ]  [ Settings ]    │
│                                              ══════════     │
│                                                             │
│  Organisation Profile                                       │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Name: Example Nonprofit Organisation                  │ │
│  │  Country: New Zealand                                  │ │
│  │  Website: https://example.org.nz                       │ │
│  │  Sectors: Health, Education                            │ │
│  │  Geographies: New Zealand, Pacific Islands             │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  Plan & Billing                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Current Plan: Starter ($49/mo)                        │ │
│  │  Cycles used: 12 / 30 this month                       │ │
│  │  Active cases: 3 / 5                                   │ │
│  │                                                        │ │
│  │  [ Manage Billing ]  [ Upgrade to Professional ]       │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  Usage Summary                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Today: 1,234 input tokens / 567 output tokens         │ │
│  │  Cost: $0.02                                           │ │
│  │  API calls: 8                                          │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Backend Architecture

### API Layer (`api/server.py`)

```
FastAPI Application
│
├── Middleware
│   ├── CORS (allow all origins)
│   └── Auth Bearer Token validation
│
├── Auth Routes (/api/auth/*)
│   ├── POST /register        → Create user + org
│   ├── POST /login           → Authenticate, return token
│   ├── POST /verify          → Validate session
│   └── POST /logout          → Destroy session
│
├── Org Routes (/api/org/*)
│   ├── POST /setup           → Complete setup wizard
│   ├── GET  /profile         → Org profile
│   └── GET  /usage           → Usage summary
│
├── Case Routes (/api/cases/*)
│   ├── POST /                → Create case
│   ├── GET  /                → List cases
│   ├── GET  /{id}            → Get case detail
│   ├── PATCH /{id}           → Update case
│   ├── DELETE /{id}          → Delete case
│   ├── POST /{id}/upload     → Upload file
│   ├── POST /{id}/export     → Export case
│   ├── GET  /{id}/download/* → Download export
│   ├── POST /{id}/signoff    → Approve/reject
│   ├── GET  /{id}/draft      → Get sections
│   ├── PATCH /{id}/draft/*   → Update section
│   ├── GET  /{id}/databank   → Data bank entries
│   ├── POST /{id}/databank   → Add entry
│   └── DELETE /{id}/databank/*→ Remove entry
│
├── Bot Routes (/api/cases/{id}/bot/*)
│   ├── POST /summary         → Bot A: Grant summary
│   ├── POST /parse           → Bot B: Parse submission form
│   ├── POST /fill            → Bot C: Auto-fill sections
│   ├── POST /parse-and-fill  → Bot B + C chained
│   ├── POST /questions       → Bot D: Gap analysis
│   ├── POST /answer          → Bot D: Process answers
│   └── POST /ingest          → Ingest file to data bank
│
├── Chat Route
│   └── POST /api/cases/{id}/chat → Claude chat with tools
│
├── Report Routes (/api/reports/*)
│   ├── GET  /                → List cycle dates
│   ├── GET  /latest          → Latest report
│   ├── GET  /{date}          → Specific report
│   └── POST /open-case       → Case from opportunity
│
├── Cycle Routes (/api/cycle/*)
│   ├── POST /run             → Start scan (async)
│   └── GET  /status          → Poll progress
│
└── Billing Routes (/api/billing/*)
    ├── POST /checkout         → Stripe Checkout
    ├── GET  /portal           → Stripe Portal
    └── POST /webhook          → Stripe events
```

### Orchestrator (3-Bot Scan Cycle)

```
POST /api/cycle/run
        │
        ▼
┌──────────────────────────────────────────────────┐
│  ORCHESTRATOR (orchestrator/main.py)             │
│                                                  │
│  Phase 1 ─ GRANT WATCHER (parallel)             │
│  ┌────────────────────────────────────────────┐  │
│  │  Model: Claude Sonnet                      │  │
│  │  Tools: web_search, web_fetch, save_evidence│ │
│  │                                            │  │
│  │  → Search priority sectors                 │  │
│  │  → Search target geographies               │  │
│  │  → Scan major funder websites              │  │
│  │  → Save grant opportunities as evidence    │  │
│  └────────────────────────────────────────────┘  │
│            │                                     │
│            ▼                                     │
│  Phase 2 ─ GRANT ANALYST (parallel)              │
│  ┌────────────────────────────────────────────┐  │
│  │  Model: Claude Sonnet                      │  │
│  │  Tools: save_evidence                      │  │
│  │  Input: Phase 1 evidence                   │  │
│  │                                            │  │
│  │  → Score eligibility (0-10)                │  │
│  │  → Score mission alignment (0-10)          │  │
│  │  → Score capacity fit (0-10)               │  │
│  │  → Score strategic value (0-10)            │  │
│  │  → Recommend: APPLY / INVESTIGATE /        │  │
│  │               MONITOR / SKIP               │  │
│  └────────────────────────────────────────────┘  │
│            │                                     │
│            ▼                                     │
│  Phase 3 ─ GRANT REPORTER (sequential)           │
│  ┌────────────────────────────────────────────┐  │
│  │  Model: Claude Haiku                       │  │
│  │  Tools: none                               │  │
│  │  Input: Phase 1 + 2 evidence               │  │
│  │                                            │  │
│  │  → Compile daily brief                     │  │
│  │  → Structured opportunity JSON             │  │
│  │  → Action Required / Top Opportunities /   │  │
│  │    Donor Intel / Pipeline / Gaps            │  │
│  └────────────────────────────────────────────┘  │
│            │                                     │
│            ▼                                     │
│  Output: cycles/{date}/latest/                   │
│    ├── report.md                                 │
│    └── outputs.json                              │
└──────────────────────────────────────────────────┘
```

### Case Bot Workflow (4-Bot System)

```
User opens case from opportunity
        │
        ▼
┌──────────────────────────┐
│  BOT A: Summary          │
│  (auto on case create)   │
│  Seeds data bank from    │
│  org profile + brief     │
└──────────┬───────────────┘
           │
     ┌─────┴──────┐
     ▼            ▼
┌──────────┐  ┌──────────────────────────────┐
│ Upload   │  │  BOT D: Q&A Gap Analyzer     │
│ submission│  │  Identifies missing data     │
│ form     │  │  Asks targeted questions      │
└────┬─────┘  │  Saves answers to data bank  │
     │        └──────────────────────────────┘
     ▼
┌──────────────────────────┐
│  BOT B: Document Parser  │
│  Extracts section defs   │
│  from PDF/DOCX form      │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│  BOT C: Section Filler   │
│  Auto-fills sections     │
│  using data bank         │
│  Flags low confidence    │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│  CHAT: Claude Sonnet     │
│  Tools: update_section,  │
│    list_sections,        │
│    read_upload           │
│  Officer reviews & edits │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│  SIGNOFF                 │
│  Approve / Reject        │
│  Export (MD / JSON / DOCX)│
└──────────────────────────┘
```

### Multi-Tenant Data Model

```
platform/
├── users.json              ← All user accounts
├── orgs.json               ← All organisations
└── sessions.json           ← Active sessions (30d TTL)

orgs/
└── {org_id}/
    ├── data/
    │   ├── org/            ← org-profile.md
    │   ├── donors/         ← Donor-related files
    │   └── grants/         ← Grant documentation
    ├── prompts/            ← Tuned prompt templates
    │   ├── grant_watcher.md
    │   ├── grant_analyst.md
    │   └── grant_reporter.md
    ├── cases/
    │   └── {case_id}/
    │       ├── case.json   ← Case state
    │       ├── databank.json ← Knowledge entries
    │       ├── uploads/    ← Uploaded files
    │       └── exports/    ← Exported files
    ├── cycles/
    │   └── {YYYY-MM-DD}/
    │       └── latest/
    │           ├── report.md
    │           └── outputs.json
    ├── evidence/
    │   └── {date}/{agent}/{id}.json
    ├── state/              ← Shared state files
    └── logs/
        └── usage-{YYYY-MM-DD}.jsonl
```

### Plan Limits & Feature Gates

```
┌─────────────────┬──────────┬──────────┬──────────────┐
│ Feature         │   Free   │ Starter  │ Professional │
│                 │   $0/mo  │ $49/mo   │  $149/mo     │
├─────────────────┼──────────┼──────────┼──────────────┤
│ Scans / month   │    2     │   30     │     30       │
│ Active cases    │    1     │    5     │  Unlimited   │
│ Chat msgs/case  │    5     │   50     │  Unlimited   │
│ Bots B, C, D    │    ✗     │    ✓     │      ✓       │
│ DOCX export     │    ✗     │    ✓     │      ✓       │
│ Evidence retain  │  30 d   │  90 d    │   365 d      │
│ AI Model         │ Haiku   │ Haiku    │   Sonnet     │
├─────────────────┼──────────┼──────────┼──────────────┤
│ Stripe          │    —     │ checkout │   checkout   │
│                 │          │ + portal │   + portal   │
└─────────────────┴──────────┴──────────┴──────────────┘
```

### Authentication Flow

```
Register                        Login
   │                              │
   ▼                              ▼
POST /api/auth/register     POST /api/auth/login
   │                              │
   ├─ Create org (orgs.json)      ├─ Verify bcrypt hash
   ├─ Create user (users.json)    ├─ Create session token
   ├─ Create session token        ├─ Store in sessions.json
   └─ Return token                └─ Return token
                                      │
                    ┌─────────────────┘
                    ▼
              localStorage.setItem("gobuga_token", token)
                    │
                    ▼
              Every API call:
              Authorization: Bearer {token}
                    │
                    ▼
              get_current_session() middleware
                    │
                    ├─ Valid → resolve org_id → proceed
                    └─ Invalid/expired → 401 → redirect /login
```

### Request Flow (Frontend → Backend)

```
Browser (Next.js :3000)
    │
    │  fetch("/api/cases")
    │  Header: Authorization: Bearer {token}
    │
    ▼
Next.js Rewrites (next.config.ts)
    │
    │  /api/* → http://localhost:8111/api/*
    │
    ▼
FastAPI (:8111)
    │
    ├─ CORS middleware
    ├─ Bearer token → get_current_session()
    ├─ Org isolation → tenant.py path resolution
    ├─ Business logic (case_manager / bots / chat / reports)
    ├─ External calls (Anthropic / Tavily / Stripe)
    ├─ Usage logging → usage_log.py
    └─ JSON response → Browser
```
