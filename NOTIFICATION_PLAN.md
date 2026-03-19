# Notification System Implementation Plan

## Phase 1: Replace `alert()` with Toast Library

Install `sonner` (lightweight, works with Next.js App Router).

### Files to change

- `frontend/package.json` — add `sonner`
- `frontend/app/layout.tsx` — add `<Toaster />` provider
- `frontend/app/settings/page.tsx` — replace `alert()` on lines 40, 53 with `toast.error()`
- `frontend/app/case/[id]/page.tsx` — replace `alert()` on line 178 with `toast.error()`
- `frontend/app/page.tsx` — replace `alert()` on lines 389, 400 with `toast.error()`

### Add success toasts for silent actions

- `frontend/app/case/[id]/page.tsx` — toast on status change (submitted, accepted, etc.)
- `frontend/app/page.tsx` — toast on case creation success
- `frontend/app/settings/page.tsx` — toast on settings save

---

## Phase 2: Standardize Error/Success Feedback

Remove per-page `error` state variables and replace with toast calls where appropriate. Keep inline errors only for form validation (login, register, setup).

### Files to change

- `frontend/app/seed/page.tsx` — use toast for seed result feedback
- `frontend/app/setup/page.tsx` — keep inline for form validation, add toast for API errors
- `frontend/app/case/[id]/page.tsx` — consolidate `statusMessage` usage, use toast for one-off errors

---

## Phase 3: Server-Side Notification Model

### Backend

- Create `api/notifications.py` with:
  - `Notification` dataclass: `id`, `org_id`, `user_id`, `type`, `title`, `body`, `read`, `created_at`, `link`
  - Types: `new_grant`, `case_status_change`, `cycle_complete`, `deadline_approaching`, `export_ready`
  - Storage in tenant JSON file under `notifications` key
- Add endpoints in `api/server.py`:
  - `GET /api/notifications` — list notifications for current user (paginated)
  - `PATCH /api/notifications/{id}/read` — mark as read
  - `POST /api/notifications/read-all` — mark all as read
  - `GET /api/notifications/unread-count` — for badge display

### Frontend

- Create `frontend/lib/notifications.ts` — API client functions
- Create `frontend/app/notification-bell.tsx` — bell icon with unread count badge, dropdown panel
- Add notification bell to the app header/nav in `frontend/app/layout.tsx` or `frontend/app/page.tsx`
- Remove localStorage-based unread tracking from `frontend/app/page.tsx` (lines 252-256, 306-323)

### Emit notifications from existing flows

- `api/server.py` cycle completion (`/api/cycle/run`) — emit `cycle_complete` and `new_grant` per opportunity
- `api/case_manager.py` status changes — emit `case_status_change`

---

## Phase 4: Real-Time Updates via SSE

Replace polling in cycle execution with Server-Sent Events.

### Backend

- Add SSE endpoint `GET /api/cycle/stream` in `api/server.py`
  - Yield events: `phase_update`, `opportunity_found`, `cycle_complete`, `cycle_error`
  - Use `StreamingResponse` with `text/event-stream` content type

### Frontend

- `frontend/app/page.tsx` — replace `setInterval` polling (lines 423-455) with `EventSource` connection to `/api/cycle/stream`
- Remove timeout protection logic (line ~450) — SSE handles connection lifecycle
- Keep polling as fallback if SSE connection fails

---

## Phase 5: Email Notifications

### Backend

- Add `postmark` or `resend` to `requirements.txt`
- Create `api/email.py`:
  - `send_notification_email(to, subject, body)` helper
  - HTML email templates for: new grant match, deadline reminder, weekly digest
- Add email preferences to org/user settings in tenant data
- Create `api/scheduler.py`:
  - Daily check for approaching deadlines (7 days, 3 days, 1 day)
  - Weekly digest of new grants found
  - Emit `deadline_approaching` notifications

### Frontend

- `frontend/app/settings/page.tsx` — add notification preferences section:
  - Toggle email notifications on/off
  - Choose frequency: instant, daily digest, weekly digest
  - Select notification types to receive

---

## Phase 6: Move Unread Tracking Server-Side

This is handled by Phase 3 — once server-side notifications exist, the localStorage tracking in `page.tsx` becomes redundant. Remove it and rely on the `/api/notifications/unread-count` endpoint instead.
