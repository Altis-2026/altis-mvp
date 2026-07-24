# Altis — permanent hosting

A live URL you can put in a YC application, send a prospect, or click from
your phone — instead of two terminal windows on your laptop. Built to be
**portable by construction**: the backend is a single portable Docker image
with all config injected via environment variables, so moving to a different
host later (GCP, AWS, wherever the business ends up) is a redeploy, not a
rewrite.

## Architecture at a glance

```
         ┌─────────────────────┐        ┌──────────────────────────┐
 users → │  Frontend (static)  │  HTTPS │   Backend (Docker/FastAPI)│
         │  React build, Vercel│ ─────► │   Railway (or any Docker  │
         └─────────────────────┘        │   host — see Migrating)   │
                                        │   + SQLite on a volume    │
                                        │   + GEE service account   │
                                        └────────────┬─────────────┘
                                                     │
                              ┌──────────────────────┴───────────┐
                              │  Monitor (scheduled job / cron)   │
                              │  NHC + USGS → enqueue pipeline run │
                              └───────────────────────────────────┘
```

Two deployable units for the fast path (Monitor is optional, add later):

1. **Frontend** — a static Vite build (`frontend/dist`). Any static/CDN host.
2. **Backend** — `Dockerfile` at the repo root. Any host that runs a
   container and lets you set environment variables.

---

## Fast path: Railway (backend) + Vercel (frontend)

Picked for this stage because they're free/cheap, deploy from a git push, and
need zero infra experience — not because they're the only option. See
**Migrating to a different host** below for why this is easy to change.

### 1. Backend → Railway

1. [railway.app](https://railway.app) → New Project → **Deploy from GitHub
   repo** → select this repo, branch `claude/wizardly-volta-bj8wdd`.
2. Railway detects the root `Dockerfile` automatically — no build
   configuration needed.
3. **Settings → Networking** → Generate a public domain. Copy it; you'll need
   it for the frontend (Step 2).
4. **Settings → Health Check** → path `/api/health` (Railway will restart the
   container if this stops responding).
5. **Variables** — add:

   | Variable | Value | Notes |
   |---|---|---|
   | `OPENROUTER_API_KEY` | your key | powers the chat assistant |
   | `MAPBOX_TOKEN` | your token | worldwide geocoding |
   | `GEE_SERVICE_ACCOUNT_KEY_JSON` | the **entire contents** of your `ee-sa-key.json`, pasted as one value | the deploy-friendly form — see below |
   | `DATA_DIR` | `/data` | see Step 6 |
   | `ALLOWED_ORIGINS` | your Vercel URL, e.g. `https://altis.vercel.app` | locks CORS; add after Step 2 gives you the URL |
   | `DEMO_PASSWORD` | a password of your choice | optional — see **Access gate** below |

6. **Attach a volume** so the database and cached imagery survive redeploys:
   Settings → Volumes → mount path `/data`. Without this, every redeploy
   starts from an empty database (fine for a stateless demo, not fine once
   you're saving real portfolios between sessions).
7. Deploy. Watch the logs for `✓ Altis backend ready` and confirm
   `https://<your-railway-domain>/api/health` returns `{"status":"ok",...}`.

### 2. Frontend → Vercel

1. [vercel.com](https://vercel.com) → New Project → import this repo.
2. **Root Directory**: `frontend` (Vercel's own settings, not a file in the
   repo — set this in the project's dashboard).
3. Framework preset: Vite (auto-detected). `vercel.json` in `frontend/`
   already sets the build/output commands.
4. **Environment Variables**:

   | Variable | Value |
   |---|---|
   | `VITE_MAPBOX_TOKEN` | your Mapbox token |
   | `VITE_API_BASE_URL` | `https://<your-railway-domain>/api` |

5. Deploy. You now have a real URL.
6. Go back to Railway and set `ALLOWED_ORIGINS` to this exact Vercel URL
   (Step 1.5) if you skipped it before, then redeploy the backend.

That's a live, shareable product.

---

## Access gate (optional, recommended for a public link)

The app has no real multi-tenant login — see the hardening checklist below
for that. What it has instead, and what a public demo/YC link actually needs,
is a **single shared password**: set `DEMO_PASSWORD` on the backend and every
visitor sees a branded password screen before the app loads. Leave it unset
and the app behaves exactly as it does locally — no prompt, no friction.

This is a custom header (`X-Demo-Password`), not HTTP Basic Auth — deliberately, so the
browser never pops its own native login dialog on a stray request; the
frontend's `AccessGate` component owns the whole experience and every API
call in `frontend/src/services/api.js` attaches the header automatically once
you've entered the code once (cached in `sessionStorage` for the tab).

## Google Earth Engine in production

Two ways to supply the service-account key, both fully supported:

- **`GEE_SERVICE_ACCOUNT_KEY_JSON`** — the key file's raw JSON content in a
  single env var. This is the one to use on Railway/Render/Fly/Cloud
  Run/anywhere — pasting a secret value into a dashboard is universal;
  mounting a secret *file* is awkward or platform-specific.
- **`GEE_SERVICE_ACCOUNT_KEY`** (a file path) or `secrets/ee-sa-key.json` —
  unchanged, still what local dev uses.

Whichever is set wins (JSON-content takes priority). GEE is optional either
way — without it the app serves the pre-baked demo events and synthetic
preview imagery, and says so honestly via `GET /api/gee-status`, rather than
pretending live analysis works.

## Environment variables — full reference

| Variable | Required? | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | for chat | Claude Haiku via OpenRouter |
| `MAPBOX_TOKEN` | for geocoding | backend geocoder (frontend has its own `VITE_MAPBOX_TOKEN`) |
| `GEE_SERVICE_ACCOUNT_KEY_JSON` | for live analysis | raw JSON key content — the deploy-friendly form |
| `GEE_SERVICE_ACCOUNT_KEY` | alt. to above | file path — local dev |
| `DATA_DIR` | recommended in prod | where `altis.db` + cached thumbnails live; point at a mounted volume so they survive redeploys. Defaults to the repo directory (fine locally) |
| `ALLOWED_ORIGINS` | recommended in prod | comma-separated exact frontend origin(s); defaults to `*` |
| `DEMO_PASSWORD` | optional | turns on the shared-password gate; unset = fully open, same as today |
| `PORT` | set by most hosts automatically | which port uvicorn binds; defaults to 8000 |
| `VITE_MAPBOX_TOKEN` | frontend build | Mapbox GL JS token |
| `VITE_API_BASE_URL` | frontend build, prod only | the deployed backend's `/api` origin; local dev doesn't need this (Vite proxies `/api` to `localhost:8000`) |

---

## Migrating to a different host later

Nothing above is Railway-specific in a way that locks you in:

- The backend is **just the `Dockerfile`**. Any platform that runs a
  container and lets you set environment variables works identically:
  Render, Fly.io, AWS App Runner/ECS, Google Cloud Run, a bare VM with
  `docker run`. Point the new platform at the same repo/Dockerfile, copy the
  same env vars across, done.
- The frontend is **just a static build** (`npm run build` → `dist/`). Any
  static host works: Netlify, Cloudflare Pages, GCS+CDN, S3+CloudFront,
  Firebase Hosting. Set the same two `VITE_*` env vars at build time.
- Secrets are never baked into the image (`.dockerignore` excludes
  `.env`/`secrets/`) — moving hosts means re-entering the same values into a
  different dashboard, not extracting them from anywhere.
- The one piece of state that doesn't just "move" automatically is the
  SQLite file itself. Copy `altis.db` from the old volume to the new one, or
  — better, if you're migrating because you've outgrown the demo stage —
  take the move as the moment to do the Postgres migration below instead of
  carrying SQLite forward again.

If the destination is specifically **GCP** (a natural fit later, since Earth
Engine already runs on GCP infrastructure — same network, one IAM system,
and it's a recognized cloud for carrier security reviews): Cloud Run for the
backend (same Dockerfile, no changes needed), Cloud SQL for Postgres, GCS for
file storage, Secret Manager instead of dashboard env vars. This is a bigger
step than swapping PaaS providers — budget real time for it, and do it when
there's an actual enterprise pilot requiring it, not preemptively.

## When to move off SQLite

SQLite on a persistent volume is genuinely fine for a demo/pilot single
instance. Move to **managed Postgres** (Railway/Neon/Supabase/RDS) when any
of these is true: multiple backend instances, concurrent writers, or you
need backups/point-in-time recovery for real customer portfolios. The DB
layer is isolated in `backend/database.py` (plus small reads in
`accuracy_check.py`), so this is a contained change — swap `sqlite3` calls
for SQLAlchemy + a `DATABASE_URL`. The table definitions (`portfolios`,
`portfolio_properties`, `analysis_results`, `analysis_meta`,
`pending_uploads`, `adjuster_feedback`, `pipeline_runs`) port directly.

## Object storage for imagery

Cached SAR thumbnails (under `DATA_DIR/cache/sar/`) should move to **S3 /
Cloudflare R2 / GCS** once there's more than a demo's worth, rather than a
container volume. Serve them via signed URLs or a CDN.

## Hardening checklist before real customer data

- [x] **CORS** — locked to `ALLOWED_ORIGINS` when set (defaults open for
      local dev).
- [x] **Portable secrets** — GEE key, Mapbox token, OpenRouter key all
      injected via env vars, never baked into the image or committed.
- [x] **Public-link gate** — `DEMO_PASSWORD` shared-secret screen (see
      above). Not real auth — the next line is.
- [ ] **Real auth** — put the app behind login (Clerk/Auth0/Cognito) before
      any actual customer portfolio touches it. `DEMO_PASSWORD` is a demo
      convenience, not multi-tenant security.
- [ ] **Multi-tenancy** — scope portfolios/feedback/runs by an `org_id`.
- [ ] **HTTPS + custom domain** — both Vercel and Railway provide HTTPS free;
      add a custom domain when you have one.
- [ ] **Backups** — automated DB snapshots once on Postgres.
- [ ] **Observability** — request logging + error tracking (Sentry) and
      uptime checks on `/api/health`.
- [ ] **Rate limiting** on the upload/geocode/analyze-live/chat endpoints —
      each one costs real GEE/Mapbox/OpenRouter usage per call.

## Rough monthly cost (pilot scale)

| Piece | Service | Cost |
|---|---|---|
| Frontend | Vercel | $0 (hobby) |
| Backend | Railway | ~$5–20 (usage-based; near $0 idle) |
| Volume (SQLite + cache) | Railway | ~$1–2 |
| Postgres (when needed) | Neon / Railway | $0–25 |
| Mapbox | free tier (100k geocodes/mo) | $0 |
| **Total** | | **~$10–40/mo** |

GEE is free for noncommercial/standard usage; commercial usage at scale is a
separate Google licensing conversation, not an infra cost.

## Verifying a deployment (do this after every deploy)

1. `https://<backend>/api/health` → `{"status":"ok",...}`.
2. Load the frontend URL — the globe should populate with the three
   pre-baked events. If `DEMO_PASSWORD` is set, you should see the password
   screen first, and the wrong password should be rejected before it lets
   you in.
3. Upload one of the files in `samples/` and confirm the mapping/analysis
   settings screen appears — this exercises the frontend→backend connection
   end to end, not just static hosting.
4. `https://<backend>/api/gee-status` → confirms whether live satellite
   analysis is actually wired up (`live_analysis: true`) or running in
   demo-only mode.
