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

8. **Real NFIP claims validation + calibration** (needs internet, no key):
   ```bash
   python validation/accuracy_check.py --event harvey --event ian
   ```
   Fetches real **NFIP Redacted Claims** (OpenFEMA v3) for each event's date-of-loss
   window, compares them against Altis's output by zip code, fits a calibrated
   flood-probability map on a zip-grouped hold-out, and writes:

   - `outputs/validation_{event}.md` — correlation report, zip-level detail,
     precision/recall by triage category, and the stated limitations
   - `outputs/calibration_{event}.json` — the fitted calibrator plus held-out
     Brier score and expected calibration error

   **Why claims and not Individual Assistance registrants.** This replaced the
   old IA ground truth in Phase 0, for three reasons. IA registrants are
   self-selected federal aid applicants (a carrier's book is close to the
   opposite population); the records carry only a binary flood flag, so there
   was no depth to correlate against; and Hurricane Ian is *not in the IA
   Housing Registrants table at all* — the endpoint returns `count: 0` for
   DR-4673, so Ian could not be validated. NFIP claims fix all three: they are
   settled insurance claims carrying a reported water depth and the dollars
   actually paid on building and contents.

   Two things worth knowing about the data:

   - **ZIPs come from coordinates, not addresses.** Point-in-polygon against
     Census ZCTA boundaries in Earth Engine. The previous address-regex
     approach found no ZIP for 300 of 1000 Harvey properties and mistook street
     numbers for ZIPs on others ("10005 Main Street, TX" → lower Manhattan).
   - **`waterDepth` has a unit ambiguity and the report states it.** FEMA
     documents the field as inches but notes some records were entered in feet,
     and the feet branch dominates. Values ≤ 15 are read as feet, above that as
     inches, and every report prints the resulting split rather than hiding it.

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

   Lismore is intentionally unsupported here: NFIP is US-only, and there is no
   equivalent open Australian claims dataset to validate against.

   To ship the result to production, commit the generated
   `outputs/calibration_*.json` and redeploy — `outputs/` is copied into the
   Docker image, so the deployed backend picks it up automatically.

   Any adjuster verdicts submitted in step 2 are merged first and override the
   coarse zip-level FEMA label, since a human verdict is per-house truth.

### Before you quote an accuracy number

Read **`docs/DETECTION_LIMITS.md`** first. Two things in it change how the
existing outputs should be read:

- The committed **Harvey** run detects **0 of 1000** properties as flooded, and
  Ian effectively 1 of 1000. Lismore (open floodplain) detects 266. The cause is
  measured and it is terrain, not code: C-band VV cannot see flooding under
  dense tree canopy, and water among buildings double-bounces *brighter*. No
  Sentinel-1 scene on either orbit — including 29 and 30 August, Meyerland's
  flood peak — shows a flood signature.
- Because every Harvey property scores 0.0, any calibrator fitted on it is
  constant, and its Brier score is the base-rate variance `p(1-p)` **by
  construction**. The previously reported **Brier 0.0239** is exactly a 2.45%
  base rate. It is not an accuracy result and should not be quoted as one.
  `run_calibration()` now refuses this case and writes no calibrator file.

### What the detector measures (Phases 1–2)

These apply to every run — the batch pipeline and live on-demand analysis both
go through the same `pipeline/flood_detect.py`. Each one degrades on its own:
if the data isn't there, the pipeline says so in the manifest / `signal_status`
and falls back, rather than silently substituting a default.

- **Multi-temporal baseline.** Instead of comparing the post-event scene to a
  single pre-event composite, the detector builds a per-pixel mean and standard
  deviation from ~12 months of same-orbit Sentinel-1 scenes and flags pixels
  that are anomalously dark relative to *their own* history (z ≤ −2σ), AND in
  the open-water backscatter range. One unrepresentative pre-event scene can no
  longer swing a call, and naturally noisy pixels are held to a proportionally
  higher bar. Falls back to the single-composite method below 8 baseline scenes.
- **HAND replaces the relative-elevation heuristic.** The DEM-hydrology vote
  now uses Height Above Nearest Drainage (MERIT Hydro, global, ~90m) instead of
  "elevation minus the minimum within a 300–600m circle". On flat coastal
  terrain the old measure was nearly meaningless — almost every parcel sits
  within a few feet of its neighbourhood minimum — so the vote abstained
  exactly where it was needed most. A missing HAND value abstains; it is never
  read as 0, which would mean "at the drainage line".
- **Cross-orbit stacking.** Every orbit with post-event coverage now
  contributes, instead of only the dominant one. Ascending and descending
  scenes are never merged into one composite (different incidence geometry);
  each gets its own Otsu threshold and its own baseline, and only the finished
  boolean masks are combined. This directly shrinks the revisit gap.
- **Depth above first floor (Phase 2).** Depth-damage curves take depth above
  the first floor; the detector measures depth above ground. The difference is
  the foundation height, which comes from the USACE **National Structure
  Inventory** (`found_ht`, plus foundation type, stories, occupancy and
  structure/contents value). This is a systematic, signed bias, not noise: a
  home on a 5.25 ft pier foundation with 4 ft of water around it has a dry
  living space, and was previously scored as damaged. NSI is CONUS-only and its
  heights are *modelled*, not surveyed — where it's unavailable,
  `depth_above_ffe_ft` is `None` rather than a guess, and
  `first_floor_source` records which.
- **Footprint-constrained sampling (Phase 2).** Sampling snaps to the matched
  structure at Sentinel-1's native 10m spacing instead of averaging a fixed 50m
  circle at 30m. The median Harvey-area structure footprint is ~2,570 sqft — an
  8.7m equal-area radius — so the old buffer averaged the target building
  together with roughly 33× its own area of street, yard and neighbouring
  parcels. USA Structures footprint *polygons* are the ideal input but are
  served from an Esri host that isn't reachable here, so NSI's per-structure
  footprint **area** drives an equal-area circle instead. That approximation is
  labelled as such in the manifest.

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
