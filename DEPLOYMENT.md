# Altis — permanent hosting plan

This is the path from "runs on my laptop" to "a URL I can send an investor or a
carrier, that stays up." It's written as a concrete recommendation with a fast
path you can stand up in an afternoon, plus what to harden before real customer
data lands.

## Architecture at a glance

```
         ┌─────────────────────┐        ┌──────────────────────────┐
 users → │  Frontend (static)  │  /api  │   Backend (FastAPI)      │
         │  React build on CDN │ ─────► │   uvicorn/gunicorn        │
         └─────────────────────┘        │   + SQLite→Postgres       │
                                        │   + GEE service account   │
                                        └────────────┬─────────────┘
                                                     │
                              ┌──────────────────────┴───────────┐
                              │  Monitor (scheduled job / cron)   │
                              │  NHC + USGS → enqueue pipeline run │
                              └───────────────────────────────────┘
```

Three deployable units, each independent:

1. **Frontend** — a static Vite build (`frontend/dist`). Pure CDN hosting.
2. **Backend** — the FastAPI app (`backend/main.py`). A container.
3. **Monitor** — `monitor/monitor.py`, run on a schedule (it shares the backend
   DB, so it just needs the same database connection).

---

## Recommended fast path (afternoon)

**Frontend → Vercel** (or Netlify / Cloudflare Pages)
- Project root `frontend/`, build `npm run build`, output `dist/`.
- Env var `VITE_MAPBOX_TOKEN`.
- Set the API base: either host the backend at the same domain behind `/api`
  (Vercel rewrite) or point the frontend at the backend URL. Today the frontend
  calls relative `/api/...`; add a rewrite so `/api/*` proxies to the backend
  origin, or set a `VITE_API_BASE` and read it in `services/api.js`.

**Backend → Render** (or Fly.io / Railway)
- Deploy the repo as a **Docker web service** (Dockerfile below).
- Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.
- Attach a **persistent disk** for `altis.db` and `outputs/` (SQLite is fine for
  a pilot; see "When to move off SQLite").
- Env: `GEE_PROJECT`, and a GEE **service-account** JSON (see below).

**Monitor → Render Cron Job** (or Fly Machines schedule / GitHub Actions)
- Command: `python monitor/monitor.py` on an hourly schedule.
- Same persistent disk / database as the backend so queued runs show up in the
  Operations panel.

That's a live, shareable product. Everything else below is hardening.

---

## Dockerfile (backend)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY pipeline/ pipeline/
COPY validation/ validation/
COPY monitor/ monitor/
COPY outputs/ outputs/
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## render.yaml (backend + monitor, sketch)

```yaml
services:
  - type: web
    name: altis-api
    env: docker
    plan: starter
    disk:
      name: altis-data
      mountPath: /app/outputs
      sizeGB: 1
    envVars:
      - key: GEE_PROJECT
        value: altis-mvp
  - type: cron
    name: altis-monitor
    env: docker
    schedule: "0 * * * *"          # hourly
    dockerCommand: python monitor/monitor.py
```

---

## Google Earth Engine in production

Local dev uses interactive `earthengine authenticate`. A server can't do that —
use a **service account**:

1. Create a GCP service account, enable the Earth Engine API, register it for EE.
2. Download its JSON key; provide it to the container as a secret file or env var.
3. Initialize with the service account instead of the default credentials in
   `backend/gee_service.py` (`ee.ServiceAccountCredentials(...)`).

GEE is optional — without it the app serves synthetic SAR thumbnails and skips
the live flood raster, so a demo never hard-depends on it. Turn it on when you
want real tiles for a specific customer region.

## When to move off SQLite

SQLite on a persistent disk is genuinely fine for a single-box pilot. Move to
**managed Postgres** (Render/Neon/Supabase/RDS) when any of these is true:
multiple backend instances, concurrent writers, or you need backups/PITR for
real customer portfolios. The DB layer is isolated in `backend/database.py`
(plus the small reads in `accuracy_check.py`), so this is a contained change —
swap `sqlite3` calls for SQLAlchemy + a `DATABASE_URL`. The table definitions
(`portfolios`, `portfolio_properties`, `analysis_results`, `pending_uploads`,
`adjuster_feedback`, `pipeline_runs`) port directly.

## Object storage for imagery

Cached SAR thumbnails (`cache/sar/`) and generated tiles should move to **S3 /
Cloudflare R2 / GCS** once you have more than a demo's worth, rather than the
container disk. Serve them via signed URLs or a CDN.

## Hardening checklist before real customer data

- [ ] **Auth** — put the app behind login (Auth0/Clerk/Cognito). There is no
      auth today; it's a single-tenant demo. Portfolios are customer PII.
- [ ] **Multi-tenancy** — scope portfolios/feedback/runs by an `org_id`.
- [ ] **CORS** — `backend/main.py` currently allows `*`; lock to your frontend
      origin.
- [ ] **Secrets** — Mapbox token, GEE key, DB URL via the platform's secret
      store, never in the repo.
- [ ] **HTTPS + custom domain** — both Vercel and Render provide this free.
- [ ] **Backups** — automated DB snapshots once on Postgres.
- [ ] **Observability** — request logging + error tracking (Sentry) and uptime
      checks on `/api/health`.
- [ ] **Rate limiting** on the upload/geocode and report endpoints.

## Rough monthly cost (pilot scale)

| Piece | Service | Cost |
|---|---|---|
| Frontend | Vercel / Cloudflare Pages | $0 (hobby) |
| Backend | Render starter web service | ~$7 |
| Monitor | Render cron | $0–$1 |
| Postgres (when needed) | Neon / Render | $0–$20 |
| Mapbox | free tier (50k loads/mo) | $0 |
| **Total** | | **~$10–30/mo** |

GEE is free for noncommercial/standard usage; commercial usage at scale is a
separate Google licensing conversation, not an infra cost.
