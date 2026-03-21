# Deployment Guide — Railway

## Architecture

GoBuga uses **file-based JSON storage** with no external database. All data lives on the filesystem:

```
platform/               Shared (users, orgs, sessions)
orgs/{org_id}/          Per-tenant, isolated by org_id
  cases/                Grant application cases
  cycles/               Scan cycle reports + evidence
  data/org/             Uploaded org documents (fed into cycles)
  data/donors/          Donor reference data
  data/grants/          Grant reference data
  evidence/             Tamper-evident evidence store
  logs/                 Usage logs (per-date JSONL)
  prompts/              Generated AI prompts
  state/                Org state files (donor registry, etc.)
  org-profile.md        Generated org profile text
```

## Railway Setup

### Services

| Service | Command | Port |
|---------|---------|------|
| Backend | `uvicorn api.server:app --host 0.0.0.0 --port 8000` | 8000 |
| Frontend | `npm run build && npm start` | 3000 |

### Persistent Volume (required)

Attach a volume to the **backend service**. Without it, all data is lost on every deploy.

1. Create a volume in Railway (start with 1 GB, monitor growth)
2. Mount it at `/data`
3. Set env var: `GOBUGA_DATA_DIR=/data`

Then update `api/tenant.py` to read from this env var:

```python
DATA_ROOT = os.environ.get("GOBUGA_DATA_DIR", PROJECT_ROOT)
ORGS_DIR = os.path.join(DATA_ROOT, "orgs")
PLATFORM_DIR = os.path.join(DATA_ROOT, "platform")
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `TAVILY_API_KEY` | Yes | Web search for grant scanning |
| `GOBUGA_DATA_DIR` | Production | Base path for data (default: repo root) |
| `NEXT_PUBLIC_API_URL` | Yes (frontend) | Backend URL (e.g. `https://api.gobuga.com`) |
| `STRIPE_SECRET_KEY` | When billing is live | Stripe API key |
| `STRIPE_WEBHOOK_SECRET` | When billing is live | Stripe webhook signing secret |

## Multi-Tenancy

Multi-tenant works out of the box. Each org is fully isolated by directory:

- Auth creates `orgs/{org_id}/` on registration
- Every API request resolves the org via session token
- No org can access another org's directory (enforced by `get_current_org` dependency)
- Path traversal is blocked on file upload/download endpoints

## Constraints

### Single instance only

File-based JSON does not support concurrent writes. **Do not scale to multiple backend replicas.** `os.replace()` is atomic on POSIX for individual files, but two replicas writing to the same `orgs.json` simultaneously will cause data loss.

When you need horizontal scaling, migrate to PostgreSQL.

### Write safety

`api/store.py` uses atomic writes (temp file + `os.replace`):

- Prevents partial/corrupt JSON from crashes or interrupted writes
- Safe for a single server process with async FastAPI (GIL protects within-process)
- Not safe across multiple processes writing the same file

### What could still go wrong

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Volume fills up | Medium (grows with tenants) | Writes fail, data loss | Monitor disk usage, set alerts |
| Corrupt JSON from OOM kill | Low | Single file unreadable | Atomic writes protect against this in most cases; `load_json` returns default on decode error |
| Accidental volume deletion | Low | Total data loss | Regular backups (see below) |
| Railway container restart during write | Very low | Temp file left behind | Atomic write ensures main file is untouched; temp files are cleaned up |

## Backups

Railway volumes do not have automatic snapshots. Set up your own:

**Option 1: Cron job inside the container**

Add a daily backup script that tars the data directory and uploads to S3/R2:

```bash
tar czf /tmp/gobuga-backup-$(date +%Y%m%d).tar.gz /data
# Upload to S3, R2, or similar
```

**Option 2: Railway cron service**

Create a separate Railway service that runs on a schedule, mounts the same volume (read-only), and pushes a snapshot to object storage.

**What to back up:**
- `platform/` — user accounts, org records, sessions
- `orgs/` — all tenant data, cases, uploads, evidence

**Backup frequency:** Daily minimum. More frequent if you have paying users.

## Monitoring

Railway provides out of the box:
- Container logs (stdout/stderr)
- CPU and memory metrics
- Network traffic
- Deploy status

**Not covered (build later when needed):**
- JSON write success/failure rates
- Per-tenant storage growth
- Volume disk usage trending
- API error rates by endpoint
- Cycle completion rates

For now, the FastAPI global exception handler logs all errors to stderr, which Railway captures. Watch for `[ERROR]` lines in the Railway logs dashboard.

## Cost Estimate

| Component | Estimated Monthly |
|-----------|------------------|
| Railway backend (1 GB RAM) | ~$5 |
| Railway frontend | ~$5 |
| Persistent volume (1 GB) | ~$0.25 |
| Anthropic API (per tenant, Haiku) | ~$2-5/tenant/month |
| Tavily search (per tenant) | ~$1-3/tenant/month |
| **Total (10 tenants)** | **~$40-90/month** |

## Pre-Deploy Checklist

- [ ] Persistent volume created and mounted at `/data`
- [ ] `GOBUGA_DATA_DIR=/data` set on backend
- [ ] `ANTHROPIC_API_KEY` and `TAVILY_API_KEY` set on backend
- [ ] `NEXT_PUBLIC_API_URL` set on frontend pointing to backend URL
- [ ] CORS origins updated in `server.py` (replace `*` with actual frontend domain)
- [ ] Backup strategy in place
- [ ] `tenant.py` updated to read `GOBUGA_DATA_DIR`
- [ ] Test: register, setup, seed, run cycle, open case end-to-end
