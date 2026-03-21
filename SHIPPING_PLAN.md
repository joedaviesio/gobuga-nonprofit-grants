# Shipping Plan: Go Live → Payments

Target: ship free tier ASAP, wire in Stripe payments early while real users are onboarding.

---

## Phase 1 — Ship Free Tier (Go Live)

Ship what works today. Every user starts on Grant Scanner (free).

### What's already working
- Registration + login + org setup wizard
- Cycle runner with opportunity generation
- Case creation (gated to 1 open case for scanner)
- Opportunity filtering by tier (2H, 2M, 1L for scanner)
- Chat with cases
- Markdown export
- Settings page with tier display

### Pre-launch checklist
- [ ] Deploy to production (domain, SSL, env vars)
- [ ] Verify cycle runner works end-to-end in prod
- [ ] Remove the dev tier toggle from settings (or hide behind feature flag)
- [ ] Set `APP_URL` env var to production URL
- [ ] Smoke test: register → setup → trigger cycle → view opportunities → open case → chat → export

### What free users CAN do
- Run 1 cycle per week (7-day timer)
- See up to 5 opportunities per cycle
- Open 1 case at a time
- Chat (5 messages per case — **wire this before launch**)
- Export as markdown

### What free users CANNOT do (already gated or trivially gated)
- Open multiple cases (enforced in `api/server.py`)
- DOCX export (function exists, not exposed to scanner)
- Parse & Fill bots (function exists, not exposed to scanner)
- Unlimited chat (limit function exists, **not yet enforced**)

### Enforcement to wire before launch

These checks exist in `api/limits.py` but aren't called yet:

| Check | Endpoint | Status |
|-------|----------|--------|
| `check_case_limit` | `POST /api/cases` | Enforced |
| `check_case_limit` | `POST /api/reports/open-case` | Enforced |
| `check_chat_limit` | `POST /api/cases/{id}/chat` | **Not enforced** |
| `check_feature_access("bots_bcd")` | Bot B/C endpoints | **Not enforced** |
| `check_feature_access("export_docx")` | DOCX export endpoint | **Not enforced** |
| `filter_opportunities_for_tier` | Reports endpoints | Enforced |

Wire the three missing checks before launch. They're one-liners — the functions already exist.

---

## Phase 2 — Wire Stripe (Week 1–2 Post-Launch)

Your Stripe backend is already built (`api/billing.py`). This phase is about connecting the frontend and testing.

### Stripe setup
- [ ] Create Stripe account (or verify test mode is ready)
- [ ] Create two products in Stripe Dashboard:
  - **Grant Officer Monthly** — $49 NZD/mo recurring
  - (Future: annual plan if needed)
- [ ] Copy price IDs to env vars: `STRIPE_STARTER_PRICE_ID`, `STRIPE_PROFESSIONAL_PRICE_ID`
- [ ] Set `STRIPE_SECRET_KEY` (test key first, then live)
- [ ] Set up webhook endpoint in Stripe Dashboard → `https://yourdomain.com/api/billing/webhook`
- [ ] Set `STRIPE_WEBHOOK_SECRET` from the webhook signing secret

### Backend — already done
These endpoints exist and work:
- `POST /api/billing/checkout` — creates Stripe Checkout session, returns URL
- `GET /api/billing/portal` — returns Stripe customer portal URL
- `POST /api/billing/webhook` — handles `checkout.session.completed`, `subscription.updated`, `subscription.deleted`

### Frontend — needs building
- [ ] **Upgrade button** on settings page (replaces dev toggle)
  - Calls `createCheckout("starter")` → redirects to Stripe Checkout
  - Only shown for scanner tier
- [ ] **Success page** at `/upgrade/success` — post-checkout landing
  - Re-verify session to pick up new tier
  - Show confirmation, redirect to dashboard
- [ ] **Manage billing** button on settings page (for officer tier)
  - Calls `getBillingPortal()` → redirects to Stripe portal
  - User can update card, view invoices, cancel
- [ ] **Upgrade prompts** at limit boundaries:
  - Case creation blocked → "Upgrade to open unlimited cases"
  - Chat limit reached → "Upgrade for unlimited conversation"
  - Export/bot gated → "Available on Grant Officer plan"

### Testing Stripe

**Local development:**
```bash
# Install Stripe CLI
brew install stripe/stripe-cli/stripe

# Login to your Stripe account
stripe login

# Forward webhooks to local server
stripe listen --forward-to localhost:8102/api/billing/webhook

# Copy the webhook signing secret it prints → use as STRIPE_WEBHOOK_SECRET locally
```

**Test cards (use in Stripe Checkout):**
| Card | Scenario |
|------|----------|
| `4242 4242 4242 4242` | Success |
| `4000 0000 0000 0002` | Decline |
| `4000 0000 0000 3220` | 3D Secure required |
| `4000 0000 0000 9995` | Insufficient funds |

Any future date for expiry, any 3 digits for CVC.

**Manual trigger (verify webhook handler):**
```bash
stripe trigger checkout.session.completed
stripe trigger customer.subscription.deleted
```

**What to verify:**
1. Checkout flow: click upgrade → Stripe Checkout → success page → tier flips to officer
2. Portal flow: click manage → Stripe portal → update card / cancel
3. Cancellation: cancel in portal → webhook fires → tier reverts to scanner
4. Case gate: scanner can't open >1 case, officer can open unlimited
5. Idempotency: replay a webhook → no duplicate state changes
6. Failed payment: card declines → user stays on scanner, sees error

---

## Phase 3 — Harden (Week 2–3)

### Webhook resilience
- [ ] Verify webhook signature validation is working (already in `billing.py`)
- [ ] Add idempotency: check if subscription ID already processed before updating org
- [ ] Handle `invoice.payment_failed` — notify user their card failed, grace period before downgrade

### Grace period on downgrade
- Don't instantly revoke access on `subscription.deleted`
- Add a `downgrade_at` field — give 3 days grace
- Existing open cases stay accessible but no new cases can be created

### Usage tracking
- [ ] Log tier changes in `api/usage_log.py` (already exists)
- [ ] Track conversion: scanner → checkout started → checkout completed

### Edge cases to handle
- User registers, immediately upgrades before setup wizard completes
- User has open cases when downgraded — cases stay but can't open new ones
- Multiple browser tabs: session should reflect tier change after webhook
- Stripe customer created but checkout abandoned — no orphan records

---

## File Reference

| File | Role | Status |
|------|------|--------|
| `api/billing.py` | Stripe checkout, portal, webhook handlers | Built |
| `api/limits.py` | Tier definitions, limit checks, cycle timer | Built (3 checks not wired) |
| `api/tenant.py` | Org record with `stripe_customer_id`, `stripe_subscription_id` | Built |
| `api/server.py` | Billing endpoints registered, case gating | Built (missing 3 enforcement points) |
| `api/usage_log.py` | Usage tracking | Built |
| `api/auth.py` | Org record creation with plan field | Built |
| `frontend/lib/api.ts` | `createCheckout`, `getBillingPortal` functions | Built |
| `frontend/app/settings/page.tsx` | Tier display, dev toggle | Needs upgrade UI |
| `frontend/app/upgrade/success/page.tsx` | Post-checkout success page | Not built |

---

## Timeline

| Week | Milestone |
|------|-----------|
| 0 | Ship free tier — enforce all limits, deploy, smoke test |
| 1 | Stripe test mode — upgrade button, success page, webhook testing |
| 2 | Stripe live mode — real cards, real money, monitor first upgrades |
| 3 | Harden — grace periods, failed payment handling, usage tracking |
