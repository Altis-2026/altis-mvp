# Altis MVP — Complete Local Testing Guide

Every test below uses real code paths — no mocks. Expected results are stated
so you know what "working" looks like. Total time: ~30 minutes.

## 0. Get current and restart

```bash
cd ~/altis-mvp
git pull origin claude/wizardly-volta-bj8wdd
pip install -r requirements.txt
```

Terminal 1 (leave running):
```bash
cd ~/altis-mvp
uvicorn backend.main:app --reload --port 8000
```
Wait for: `Loaded 800 properties for lismore` + `✓ Altis backend ready`.
(If "Address already in use": `lsof -i :8000`, `kill -9 <PIDs>`, retry.)

Terminal 2 (leave running):
```bash
cd ~/altis-mvp/frontend
npm install
npm run dev
```
Open the printed `http://localhost:5173` (or 5174) URL.

---

## 1. Pre-baked events (globe demo)

1. Open the Events panel → you should now see **three** events.
2. Click **Northern Rivers Floods** → globe flies to NSW, ~800 pins appear.
   Expect ~240 red Dispatch pins along the river floodplain, realistic depths
   (max ~16.8 ft). **This is your demo centerpiece** — riverine flooding is
   SAR's strongest case.
3. Click **Hurricane Ian** → a dashed orange **storm track** appears with
   category-colored fix points (toggle it with the 🌀 button, bottom-right).
   Note: the property pins for Ian/Harvey now show honest results — mostly
   dry/Review. That's correct: surge receded before the next satellite pass
   (Ian) and dense-urban rainfall hides street water (Harvey). Say exactly
   that in a demo; it's a credibility feature, not a bug.
4. Click the **FEMA zones** toggle (bottom-right) while over Florida/Texas →
   FEMA flood-zone polygons paint over the map (US only; nothing over
   Australia, by design).

## 2. Upload: standard CSV (public-adjuster format)

File: `samples/demo_portfolio_public_adjuster_clients.csv` (61 properties)

1. Upload → mapping preview should auto-detect: Claim Number → policy number,
   Loss Address → address, Dwelling Coverage → coverage amount, Lat/Lon.
2. Confirm → you land on the Analysis panel with a pulsing "Next step" banner,
   and the globe flies to a dashed gold box labeled `PORTFOLIO — 61 PROPERTIES`.
3. Click any pin **before** analyzing → drawer says "Not analyzed yet" (no
   fake 0% numbers).

## 3. Live satellite analysis

1. In the Analysis panel, set date **2022-02-28**, click **Run** (1–3 min —
   it now also pulls VH cross-pol, rainfall, NDVI, and duration slices).
2. Expect: ~50 Dispatch. The **Portfolio Exposure** card appears: policies in
   zone, TIV at risk, estimated gross loss.
3. Click a red pin. The drawer should show:
   - Event Rainfall (~12–16 in — the real Feb 2022 deluge)
   - Est. Inundation Duration (~5–9 days)
   - Dual-Pol Cross-Check: "VH confirms ✓"
   - Estimated Claim Severity: central $ + range
   - Some properties: green "Subrogation Candidate" box
4. Dispatch queue, data grid, audit PDF, and chat all work against these
   results as before.

## 4. Pre-event risk scan (no storm needed)

With the portfolio loaded (analyzed or not): Analysis panel → **Pre-event
risk scan → Scan** (~20–40s). Expect a 1–5 score distribution strip and the
top-5 riskiest properties. This is the 365-day underwriting view.

## 5. Upload: Excel with carrier-style headers (US)

File: `samples/test_us_houston_claims.xlsx` (12 real Houston parcels)

1. Upload → mapping must auto-detect: Claim ID → policy number, Dwelling
   Limit → coverage amount, Lat/Long → coordinates. (This file previously
   broke the auto-mapper; it's now a regression test.)
2. Confirm → run live analysis with date **2017-08-27** (Harvey).
3. Expect honest results: mostly dry/Review with urban flags — Houston is
   SAR's hardest case. **FEMA Flood Zone rows populate in the drawer** (US
   property + SFHA flags) — that's what this file is really testing.

## 6. Upload: messy CSV, no coordinates (geocoding test)

File: `samples/test_messy_headers_no_coords.csv` (8 Lismore CBD businesses)

1. Upload → the sloppy headers (`Pol #`, `Street Addr`, `Town`, `St`,
   `Post Code`, `TIV ($)`) must all auto-map correctly.
2. Confirm → watch the backend terminal print `Geocoding 8 addresses...` —
   this exercises the Mapbox geocoder since the file has no lat/lon.
3. Run live analysis (2022-02-28). CBD properties should mostly come back
   **Review** with urban flags — the honest answer for a dense town center.

## 7. Upload: PDF portfolio

File: `samples/test_pdf_portfolio.pdf` (8-row policy schedule table)

1. Upload the PDF directly → the table is extracted and mapped like a CSV.
2. Confirm → analyze (2022-02-28) → same flood results as the matching CSV
   rows. This proves the "carrier sends us whatever they have" story.

## 8. Reports

- **Audit PDF** (event): drawer/reports panel button as before.
- **Catastrophe report** (portfolio): after a live analysis, open
  `http://localhost:8000/api/portfolio/<PORTFOLIO_ID>/cat-report/live`
  — downloads the reinsurance-format PDF (exposure, est. loss, methodology).
  The portfolio ID is shown in the sidebar (8 characters, e.g. `A1B2C3D4`).

## 9. Persistence

Restart the backend (Ctrl-C in terminal 1, run uvicorn again), reselect the
portfolio in the sidebar, and its saved live results — including severity $,
rainfall, zones — reload from the database.

---

## 10. Intelligence layer (flags · floor depth · synthetic revisit · pre-landfall)

Full technical write-up: `docs/INTELLIGENCE_LAYER.md`.

1. **Harvey → KPI bar.** You should see a new tile, **Wrongful Denials Prevented: 577**.
   Remote-Deny drops from 842 to 265 because those properties are held back to Review.
2. **Click any orange (Review) pin in Harvey.** The drawer opens with **Held back:
   Remote-Deny → Review** and an ALERT, *Transient flooding likely missed by SAR*.
   It quotes ~750 mm of rain, height above drainage, and "radar pass came 1.5 days
   after the peak". Click **Evidence**, then **Confirm** or **Dismiss**; the verdict
   saves as a label.
3. **Ian → any pin.** You should see *Hurricane wind + possible undetected flooding*
   (≈105–110 kt open-terrain) and the 4-day radar lag (Sentinel-1 didn't image
   Port Charlotte until Oct 2).
4. **Lismore → a red (Dispatch) pin.** Check three sections:
   - **Water vs. finished floor**: the schematic shows the floor line, the water at
     the satellite pass, and the higher routed peak.
   - **Synthetic revisit**: the hydrograph peaks Feb 28 and the orange line marks the
     Mar 2 satellite pass. The fit CSI appears in the caption.
   - **Event context**: *Uninhabitable (planning)* gives a day range and hotel vs
     long-let.
5. **Evidence pack.** Click **⬇ Forensic evidence pack (PDF)** at the bottom of the
   intel section. You get a 4-page PDF with the site map, lineage, limitations, and a
   SHA-256 input digest in the footer.
6. **3D.** Enter 3D inspect mode on Lismore at street zoom. Houses show a white
   finished-floor band, the dark-blue observed water, and a faint light-blue shell
   where the router says the peak stood higher.
7. **Pre-landfall panel** (new sidebar icon under Analysis). With an event selected,
   drag the rainfall slider: the band counts and the ranked list update instantly.
   Tick **Colour map pins by flood probability**. Open **Model card** to see the
   leave-one-event-out validation. With a portfolio loaded, click **Score vs 7-day
   forecast** (live Open-Meteo).
8. **Portfolio deep analysis.** On a live-analysed portfolio, open a property and
   click **Run deep analysis** (1–3 min). The flags, floor depth and (if SAR saw
   enough flooding) the routed hydrograph appear for every property.
9. **API spot-checks:**
   `GET /api/validation/nfip/harvey` · `GET /api/events/lismore/intel` ·
   `POST /api/triage/route {"event_id":"lismore","property_id":"NRIV-00010"}` ·
   `POST /api/triage/silent/harvey {"fnol_property_ids":[]}`.

## Known honest limitations (say these in demos, don't hide them)

- **Storm surge**: recedes within hours; if no satellite pass catches it, we
  say dry. Riverine/rainfall flooding (days of standing water) is our case.
- **Dense urban**: buildings hide street water from radar — we route to
  Review with an urban flag rather than guessing.
- **FEMA zones/NFHL**: US-only dataset.
- **Loss estimates**: depth-damage reserving aid, not adjusted claims.
- **Storm tracks**: available for the named demo hurricanes; arbitrary live
  events don't draw a fabricated track.
