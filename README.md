# Altis

**AI-powered flood damage triage for property & casualty insurers.**

Altis turns satellite SAR (synthetic aperture radar) imagery into a
property-level dispatch decision within hours of a flood event — before a
single adjuster sets foot on the ground. Upload a portfolio, point it at a
hurricane or flood event, and get back a ranked, explainable triage of every
property: who needs an adjuster dispatched today, who can be resolved
remotely, and how confident the model is in each call.

[![tests](https://img.shields.io/badge/tests-207%20backend%20%2B%2029%20frontend-brightgreen)]()
[![python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![node](https://img.shields.io/badge/node-18%2B-blue)]()

---

## Why this matters

After a major flood event, carriers face thousands of claims and a fixed
number of adjusters. The wrong triage means days of avoidable field visits
for properties that were barely touched, and missed dispatch priority for
properties that flooded badly. Altis collapses the gap between "the storm
just happened" and "we know who to send someone to" from days to hours, using
freely available satellite radar instead of waiting on ground reports.

## What it does

- **Before/after SAR flood detection** — pulls Sentinel-1 radar scenes,
  detects flood extent per property via speckle-filtered change detection.
- **Per-property triage** — Dispatch / Remote-Resolve / Review, with a depth
  estimate, an uncertainty interval, and a plain-English "why this decision"
  breakdown of every contributing factor.
- **Calibrated confidence** — isotonic/Platt-calibrated probabilities
  validated against real FEMA disaster declarations, with a precision/recall
  reliability report per event.
- **Severity-ranked dispatch queue** — properties ordered by
  severity × coverage exposure, not a flat list, so the highest-value field
  visits surface first.
- **Universal portfolio ingestion** — drop in a `.csv`, `.xlsx`, `.xls`, or
  `.pdf` with whatever column names the carrier already uses ("Policy No",
  "TIV", "Site Address"...). Altis fuzzy-maps the columns, standardizes
  addresses, and lets you review/correct the mapping before it commits and
  geocodes.
- **Claims data grid** — sortable, filterable, spreadsheet-native table view
  with bulk CSV export, for the manager who doesn't want to drive a globe.
- **Adjuster feedback loop** — a human can agree/disagree with any triage
  call and leave a correction note; that verdict feeds directly back into
  the calibration model as ground truth for the next run.
- **Audit-ready PDF report** — one click produces a carrier-ready document
  per event: methodology, satellite scene sources/dates, the full triage
  table, top dispatch priorities, and independent validation metrics.
- **Always-on monitor** — polls NHC/USGS for new flood events and
  auto-queues a pipeline run, visible in an Operations panel, closing the
  loop from "a hurricane just formed" to "a run is queued" without a human
  in the middle.
- **3D globe UI** — Mapbox GL globe with zoom-aware clustering, dispatch pins
  visually emphasized over remote-resolved ones, and address labels that
  appear only once you're zoomed in close enough to read them.
- **3D property inspect** — an optional mode that drapes real terrain and
  draws each property as a building: its actual OpenStreetMap footprint,
  extruded to an estimated height with a procedural gable roof, colored by
  triage decision, with the modeled flood depth rendered as water standing
  against its walls. A claims manager sees "water is 3.2 ft up this house"
  instead of reading it out of a column. Gated to neighborhood zoom — the flat
  pin/cluster view stays the default across a portfolio — and labeled in the
  UI as illustrative: footprints are real, heights are estimated, and neither
  is a structural survey.

## Architecture

```
         ┌─────────────────────┐        ┌──────────────────────────┐
 users → │  Frontend (static)  │  /api  │   Backend (FastAPI)      │
         │  React + Mapbox GL  │ ─────► │   uvicorn                │
         └─────────────────────┘        │   + SQLite                │
                                        │   + GEE (Sentinel-1 SAR)  │
                                        └────────────┬─────────────┘
                                                     │
                              ┌──────────────────────┴───────────┐
                              │  Monitor (scheduled job)          │
                              │  NHC + USGS → enqueue pipeline run │
                              └───────────────────────────────────┘
```

| Layer | Tech | Path |
|---|---|---|
| Frontend | React, Vite, Mapbox GL JS | `frontend/` |
| Backend API | FastAPI, SQLite | `backend/` |
| Flood detection pipeline | Google Earth Engine, Sentinel-1 SAR | `pipeline/` |
| Validation & calibration | FEMA declarations, isotonic/Platt | `validation/` |
| Always-on monitor | NHC/USGS pollers | `monitor/` |

GEE is optional for a demo — without service-account credentials the app
serves synthetic SAR thumbnails and skips the live flood raster, so nothing
hard-depends on it.

## Quickstart (local demo)

Full walkthrough, including a step-by-step ~4-minute demo script, sample
portfolios, and a troubleshooting table, lives in **[RUN_LOCAL.md](RUN_LOCAL.md)**.
Short version:

```bash
# backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000

# frontend (new terminal)
cd frontend
npm install
cp .env.example .env   # then paste a free Mapbox token into .env
npm run dev
```

Open **http://localhost:5173**.

## Deploying it permanently

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for the full plan — Vercel for the
frontend, Render (or Fly/Railway) for the backend + monitor cron, a
Dockerfile, a SQLite→Postgres migration path, and a pre-customer-data
hardening checklist. Rough pilot-scale cost: **$10–30/month**.

## API surface

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness check |
| `GET /api/events/{event_id}/properties` | Triaged properties for an event |
| `GET /api/events/{event_id}/dispatch-queue` | Severity×coverage-ranked worklist |
| `GET /api/events/{event_id}/report` | Audit-ready PDF |
| `GET /api/accuracy/{event_id}` | Calibration + precision/recall vs. FEMA |
| `POST /api/portfolio/upload` | Upload a `.csv/.xlsx/.xls/.pdf` portfolio (preview, no commit) |
| `POST /api/portfolio/{upload_id}/confirm` | Confirm column mapping → geocode + commit |
| `POST /api/portfolio/{portfolio_id}/analyze` | Score a portfolio against an event |
| `POST /api/property/{property_id}/feedback` | Adjuster agree/disagree + correction note |
| `GET /api/events/{event_id}/feedback` | Feedback summary for an event |
| `GET /api/runs` / `POST /api/runs` | Pipeline run queue (Operations panel) |
| `POST /api/runs/{run_id}/status` | Advance a queued run |
| `POST /api/buildings` | Building footprints + heights for the 3D inspect view |

## Testing

```bash
pytest -q                       # backend / pipeline logic  (207 tests)
cd frontend && npm test         # map + building geometry   (29 tests)
cd frontend && npm run build    # production build sanity check
```

207 backend tests across ingestion, triage, calibration, uncertainty, priority
ranking, feedback-to-ground-truth merging, the Round 6 API surface, and the
building-footprint join. The frontend tests run on node's built-in runner
(no test framework dependency) and cover the 3D inspect geometry.

## License

See [LICENSE](LICENSE).
