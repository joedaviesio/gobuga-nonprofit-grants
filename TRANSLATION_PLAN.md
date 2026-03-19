# GoBuga — Language Translation Plan

## Context

gobuga is a multi-tenant grant scanning platform for nonprofits. Currently all UI and content is English-only with no i18n infrastructure. Nonprofits operate globally, so translation support would expand reach to non-English-speaking organizations and enable grant discovery across languages.

---

## Phase 1: Frontend Internationalization (i18n)

**Goal**: Extract all hardcoded English strings and enable locale switching in the UI.

### Steps

1. **Install `next-intl`** — the standard i18n library for Next.js App Router
2. **Create message catalogs** — extract all UI strings into JSON files under `frontend/messages/`
   - `en.json` (English — baseline)
   - `fr.json`, `es.json`, `pt.json` (initial target languages for nonprofit reach)
3. **Wrap the Next.js layout** with `NextIntlClientProvider` and configure middleware for locale detection
4. **Replace all hardcoded strings** in pages (`login`, `register`, `setup`, `case/[id]`, dashboard) with `useTranslations()` calls
5. **Add a language switcher** component to the top navigation bar
6. **Store user locale preference** in their profile (persist via API)

### Key Files to Modify

- `frontend/app/layout.tsx` — provider setup
- `frontend/app/login/page.tsx`, `register/page.tsx`, `setup/page.tsx`, `case/[id]/page.tsx` — string extraction
- `frontend/lib/api.ts` — send `Accept-Language` header with requests
- `frontend/next.config.ts` — add `next-intl` plugin config

---

## Phase 2: Backend API Localization

**Goal**: Return localized error messages and system content from the API.

### Steps

1. **Add a locale middleware** to FastAPI that reads `Accept-Language` header
2. **Create a `locales/` directory** in `api/` with JSON translation files for server-side messages (errors, notifications, email templates)
3. **Wrap all user-facing response strings** through a `t()` helper that resolves by locale
4. **Localize email/export content** — exported grant applications and any notifications should respect the org's language preference

### Key Files to Modify

- `api/server.py` — middleware for locale detection
- `api/auth.py`, `api/case_manager.py`, `api/export.py` — wrap response messages

---

## Phase 3: Grant Content Translation

**Goal**: Translate discovered grant opportunities so users can read them in their preferred language.

### Steps

1. **Add an on-demand translation endpoint** — `POST /api/translate` that accepts text + target locale
2. **Use Claude API** (already integrated) for high-quality contextual translation of grant descriptions, requirements, and eligibility criteria
3. **Cache translations** — store translated content alongside originals in the org's data directory to avoid repeated API calls
4. **Show a "Translate" button** on grant opportunity cards and case detail pages
5. **Preserve original text** — always keep the source language version accessible for accuracy verification

### Architecture

```
User clicks "Translate" → Frontend sends text + target locale
  → Backend calls Claude with translation prompt
  → Cache result in org data store
  → Return translated content
```

### Prompt Strategy

Use a system prompt optimized for grant/nonprofit domain terminology:

```
Translate the following grant opportunity text to {language}.
Preserve all proper nouns, organization names, monetary amounts,
and dates exactly as written. Use formal nonprofit sector terminology.
```

---

## Phase 4: Multilingual Grant Scanning

**Goal**: Discover grant opportunities published in non-English languages.

### Steps

1. **Extend the orchestrator's search queries** to include translated search terms in target languages
2. **Add language detection** to scanned grant content (use a lightweight library like `langdetect`)
3. **Tag opportunities with source language** in metadata
4. **Auto-translate summaries** during the scanning pipeline so all users see results in their preferred language
5. **Support multilingual keyword matching** — match org sectors/focus areas against grants regardless of language

### Key Files to Modify

- `orchestrator/main.py` — add multilingual search terms
- `orchestrator/config.py` — add language configuration per org

---

## Phase 5: Application Output Translation

**Goal**: Generate grant applications in the language required by the funder.

### Steps

1. **Detect required submission language** from grant requirements
2. **Add a "target language" field** to case configuration
3. **Use Claude to translate completed application sections** into the funder's required language
4. **Export bilingual documents** — side-by-side original and translated versions for internal review
5. **Update `api/export.py`** to support multilingual document generation

---

## Technology Choices

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Frontend i18n | `next-intl` | Native Next.js App Router support, lightweight |
| Backend i18n | Custom JSON + helper | Simple, no heavy framework needed for API messages |
| Content translation | Claude API (Sonnet) | Already integrated, high quality for domain-specific text |
| Language detection | `langdetect` (Python) | Lightweight, sufficient for identifying source language |
| Translation cache | File-based JSON | Consistent with existing data storage pattern |

## Priority & Sequencing

| Phase | Effort | Impact | Priority |
|-------|--------|--------|----------|
| 1 — Frontend i18n | Medium | High | Start here |
| 2 — Backend localization | Low | Medium | Do with Phase 1 |
| 3 — Grant content translation | Medium | High | Next |
| 4 — Multilingual scanning | High | High | After Phase 3 |
| 5 — Application output | Medium | Medium | Last |

## Risks & Mitigations

- **Translation quality for legal/compliance content**: Always show original alongside translation; add disclaimer that translations are AI-generated
- **API costs**: Claude translation calls add cost — mitigate with aggressive caching and only translating on user request
- **RTL language support**: If Arabic/Hebrew needed later, will require Tailwind RTL plugin and layout adjustments
- **String extraction effort**: Phase 1 touches every frontend page — do incrementally, page by page
