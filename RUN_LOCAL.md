# Running the Altis MVP locally (demo guide)

This is the exact sequence to get the full product running on your machine and
walk an investor or a carrier through it. Two processes: a FastAPI backend
(port 8000) and the Vite/React frontend (port 5173, which proxies `/api` to the
backend). The demo works **with no Google Earth Engine, no FEMA network, and no
API keys** other than a free Mapbox token for the globe.

---

## 0. Prerequisites

- **Python 3.10+** and **Node 18+** (`python3 --version`, `node --version`)
- A free **Mapbox token** — https://account.mapbox.com (free tier is 50k map
  loads/month, far more than a demo needs)

## 1. Backend — one-time setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Start the backend

```bash
# from the repo root, with the venv active
uvicorn backend.main:app --reload --port 8000
```

You should see:

```
  Loaded 1000 properties for harvey
  Loaded 1000 properties for ian
✓ Altis backend ready at http://localhost:8000
```

Leave this running. Quick check in another terminal:
`curl http://localhost:8000/api/health` → `{"status":"ok", ...}`.

## 3. Frontend — one-time setup

```bash
cd frontend
npm install
cp .env.example .env
# edit .env and paste your token:
#   VITE_MAPBOX_TOKEN=pk.your_real_token
```

## 4. Start the frontend

```bash
# inside frontend/
npm run dev
```

Open **http://localhost:5173**. The globe should render and slowly rotate.

---

## 5. The demo script (≈4 minutes)

1. **Pick an event.** Sidebar → **Events** → *Hurricane Harvey*. The globe flies
   to Houston and ~1,000 properties load, color-coded by triage decision. The
   KPI bar shows properties / dispatch / remote-resolved / estimated savings.
   Red **Dispatch** pins glow and grow as you zoom; addresses label up close.

2. **Open a property.** Click any pin → the right drawer shows the SAR
   before/after, depth (± uncertainty), the confidence gauge, the
   **"Why this decision"** breakdown, and the **Adjuster Verdict** widget.
   Click 👎 *Disagree*, pick a corrected class, add a note, **Submit** — that
   writes to the human-in-the-loop ground-truth table.

3. **Work the dispatch queue.** Sidebar → **Dispatch Queue**. This is the
   ordered worklist — severity × coverage, not a flat list. #1 is what the CAT
   team hits first. Click a row to inspect it; **Open grid →** for the full table.

4. **Claims data grid.** Top-right **Claims Grid** (or *Open grid* from the
   queue). Sort by any column, filter by triage or search, tick rows, and
   **Export selected** to CSV. This is the manager's spreadsheet-native view.

5. **Upload a portfolio.** Top-right **Upload Portfolio**. Drag in
   `samples/demo_portfolio_harvey.csv` (deliberately messy headers like
   "Policy No", "TIV"). Altis fuzzy-maps the columns and shows a
   **review-and-confirm** screen — override anything, then **Confirm & Geocode**.
   The globe flies to the geocoded book. Sidebar → **Analysis** →
   *Analyze against Hurricane Harvey* to score the portfolio against the flood
   data, then revisit the **Dispatch Queue** in *portfolio* mode.

6. **Audit report + validation.** Sidebar → **Reports**. **Download audit PDF**
   for a carrier-ready document (methodology, scene sources + dates, triage
   table, top dispatch priorities, FEMA precision/recall). The reliability chart
   appears once you've generated calibration (step 8, optional).

7. **Operations / the always-on loop.** Sidebar → **Operations** shows the
   monitor → pipeline run queue. Click **+ Queue run** to enqueue one manually,
   and advance its status (queued → running → complete).

### Optional, for the full story

8. **Real FEMA validation + calibration** (needs internet, no key):
   ```bash
   python validation/accuracy_check.py --event harvey --event ian
   ```
   Fetches real FEMA Individual Assistance records for each disaster, compares
   them against Altis's output by zip code, fits a calibrated flood-probability
   map on a zip-grouped hold-out, and writes:

   - `outputs/validation_{event}.md` — correlation report, zip-level detail,
     precision/recall by triage category, and the stated limitations
   - `outputs/calibration_{event}.json` — the fitted calibrator plus held-out
     Brier score and expected calibration error

   **These files are what turn the confidence badge into a defensible number.**
   Once they exist, Altis replays the fitted calibrator at inference time and
   every analysed property gains a `flood_probability`: a calibrated P(flooded)
   anchored to FEMA ground truth, shown in the property drawer, the claims
   grid, and the Guidewire/Duck Creek exports. It is deliberately kept separate
   from `confidence_score` (which is decision confidence, not a probability),
   and it is simply absent until this step has been run — Altis never invents
   the number.

   Live analysis has no FEMA disaster of its own, so it borrows a fitted
   event's calibrator and reports which one it used. Control the preference
   order with `CALIBRATION_EVENT_ORDER` (default `harvey,ian`).

   Lismore is intentionally unsupported here: FEMA is US-only, and there is no
   equivalent open Australian per-zip assistance dataset to validate against.

   To ship the result to production, commit the generated
   `outputs/calibration_*.json` and redeploy — `outputs/` is copied into the
   Docker image, so the deployed backend picks it up automatically.

   Any adjuster verdicts submitted in step 2 are merged first and override the
   coarse zip-level FEMA label, since a human verdict is per-house truth.

9. **Live event detection** (needs internet, no key):
   ```bash
   python monitor/monitor.py            # one pass; --loop to run continuously
   ```
   Detected NHC/USGS flood events are auto-queued as pipeline runs and appear in
   the **Operations** panel.

---

## 6. Run the tests

```bash
pytest -q                 # backend / pipeline logic (110+ tests)
cd frontend && npm run build   # production build sanity check
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Globe is black / "Set VITE_MAPBOX_TOKEN" in console | Put a real token in `frontend/.env`, restart `npm run dev` |
| Frontend loads but no data | Make sure the backend is running on port 8000 (the dev server proxies `/api` to it) |
| Portfolio upload "could not geocode" | The Census geocoder needs outbound internet; addresses must be real US addresses |
| PDF/report 404 | Generate calibration first (step 8) for the validation section; the rest of the PDF works regardless |
