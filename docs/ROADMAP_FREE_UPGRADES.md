# Altis roadmap: free-data upgrades

Written 2026-11-08 at the end of the free-data upgrade session. Part A records
what was built and measured. Part B is the ordered plan for what comes next,
each item with its goal, method, data, the measurement that decides whether
it ships, and the main risk. Paid data and outreach are Part C, deliberately
last. Discipline from `docs/PROJECT_STATE.md` §6 applies throughout: never
quote a number that was not measured, report results that disappoint, and
never tune on the data used to judge.

---

## Part A. Done in this session (all on branch `claude/altis-flood-intelligence-1qzgvq`)

| Upgrade | What it does | Measured result | Files |
|---|---|---|---|
| River-gauge physics depth | NWS crest stage + NAVD88 datum gives peak water-surface elevation, interpolated along the real river channel (USGS NLDI) and differenced against USGS 1 m lidar ground. Reservoir pool gauges excluded. | Against USGS surveyed high-water marks. **Brazos:** water found at 7/18 sites (95% CI 20–61%) vs radar 0/18 (0–19%). **Harvey:** 16/48 (22–47%) vs radar 3/48 (1–17%), depth bias −0.3 ft vs −2.7 ft, r = 0.50. Mean absolute error ~3.4 ft, similar to radar. Harvey exposed the pool-gauge bug, so **Harvey is now development data; a clean third-event test is owed (B1).** | `backend/gauges.py`, `validation/gauge_depth_check.py`, `pipeline/05_gauge_depth.py`, `outputs/gauge_depth_check_*.json` |
| Gauge–radar conflict flag | Properties where the river crest implies ≥ 1 ft of water but radar saw ≤ 5%: likeliest missed claims. Shown in the drawer, claims grid and CSV export; triage class not changed automatically. | 462 Brazos and 170 Harvey properties flagged. Their precision is **not measured** (no dry ground truth). | `backend/database.py`, `PropertyDrawer.jsx` |
| Gauge depth in live analysis | Same physics attached to any uploaded US portfolio's live run; never fails the satellite run. | Wiring tested on Brazos points. | `backend/gauges.py::attach_gauge_depth_live` |
| Satellite pass tracker | Last and next expected Sentinel-1 pass for any area (Copernicus STAC, 12-day repeat per track), last clear Sentinel-2 pass, Earth Engine ingestion check. | Houston: median usable revisit ~7.5 days; next pass ~4 days out when measured. Earth Engine had the latest pass within about a day. This is the honest basis for any speed claim. | `backend/passes.py`, `DataFreshness.jsx` |
| Extent benchmark | Pixel precision/recall/IoU on Sen1Floods11 hand labels (446 chips, 11 countries), official splits, chip-bootstrap CIs. | Test: per-chip Otsu IoU **0.21** (precision 0.23), fixed −15 dB 0.44, dual-pol 0.51, learned LightGBM **0.63** (0.51–0.72). Never-seen country (Bolivia): Otsu 0.35, fixed 0.58, learned 0.61 (0.37–0.76): **no clear gain over the fixed rule there.** | `validation/s1f11_benchmark.py`, `outputs/s1f11_benchmark.json`, `outputs/models/s1_water_lgbm.joblib` |
| Pipeline's own threshold step on the same labels | Speckle filter + Otsu + the open-water range guard from `config.SAR`, replicated on the chips. | Test IoU **0.44** (0.31–0.56), precision 0.63; Bolivia IoU **0.69** (0.46–0.79), precision 0.84. The guard (fallback on 51/90 test chips) removes plain Otsu's over-detection. Learned model beats it on test, loses on Bolivia; CIs overlap: **no replacement justified yet.** | `validation/s1f11_guarded_otsu.py` |
| Flood event library | Global Flood Database (913 mapped floods), FEMA flood-type disasters since 2000 (436), each US event with real NFIP claim outcomes; every live run is appended. | Library and outcomes built from source; no predictive claim made. | `backend/event_library.py`, `pipeline/library/*.py`, `outputs/library/` |
| Analog search | Similar past floods by cause, location, season, size and duration, with reasons, excluding the queried event; similarity-weighted claim outcomes from analogs with NFIP data. | Qualitative only. For Brazos it returns Texas tropical storms (Fay, Ike, Claudette, Allison). **Not yet validated as a predictor (B4).** | `AnalogsCard.jsx` |
| Event watch + pre-event exposure | Active NHC storms with forecast cones, recent FEMA declarations, portfolio properties and insured value inside each cone; historical cone replay. | Harvey replay: advisory 8 cone missed Houston; from advisory 12 all 4,000 Harvey properties were inside the cone. | `backend/event_watch.py`, `WatchCard.jsx` |
| Data source registry | 40 free sources with use, status and licence summary; probe script; generated catalog. | 40/40 reachable on 2026-11-08. | `pipeline/data_sources.py`, `validation/source_probe.py`, `docs/DATA_SOURCES.md` |

Licence findings that matter for selling: the Global Flood Database and FABDEM
are **non-commercial (CC BY-NC)**; MERIT Hydro (used for HAND today) needs its
terms checked. `ALTIS_COMMERCIAL_SOURCES_ONLY=1` already removes the Global
Flood Database from the event library.

---

## Part B. Next, in priority order (all free)

### B1. A clean third-event test of the gauge method (highest priority)
- **Status (2026-11-08): DONE, PASSED** the pre-registered criteria on Hurricane Florence (`docs/PREREG_GAUGE_FLORENCE.md`): within the 3 km product rule, water found at 130/221 sites (59%, CI 52-65%), bias +1.0 ft. Depth magnitude skill is weak at 3 km (r = 0.04) and moderate within 1 km (r = 0.32). **Follow-ups:** (a) show depth as a band or 'water reached here' beyond 1 km; (b) run radar on a Florence sub-area for a head-to-head; (c) test a slope-aware WSE (water surface falls away from the channel across the floodplain) to fix the distance-growing overestimate, pre-registered on a fourth event.
- **Why:** gauge depth is the first method in the project to beat radar at surveyed sites, but Harvey was used to fix a bug, so only Brazos is a clean test. One more independent event decides whether this is a product feature or a Texas coincidence.
- **How:** pick a riverine US event with many USGS high-water marks and NWS gauges with NAVD88 datums, chosen *before* looking at results. Candidates: Hurricane Florence 2018 (NC rivers), 2016 Louisiana floods (Amite/Comite), Hurricane Ida 2021 (NJ/PA rivers), 2019 Missouri/Nebraska floods. Run `validation/gauge_depth_check.py` unchanged (add an `EVENTS` entry only), then `validation/hwm_check.py` for the radar comparison.
- **Ships if:** gauge recall beats radar recall with non-overlapping 95% CIs, and depth bias is within ±1.5 ft.
- **Risk:** coastal-surge events are out of scope (no river gauge controls surge). Pick river floods.

### B2. Measure the gauge-conflict flag's precision
- **Status (2026-11-08):** zip-level proxy run (`validation/gauge_conflict_zip_check.py`): **inconclusive**. Brazos flagged properties sit in *lower*-claim zips (1.3% vs 1.9%, rho = -0.19, p = 0.52, 14 zips); Harvey higher (5.1% vs 3.8%, rho = 0.24, p = 0.15). Zip resolution cannot settle a per-house flag; this needs per-address outcomes (Part C items 1-2). The UI keeps the 'unverified' wording.
- **Why:** 462 Brazos properties are flagged as possible missed claims. A carrier will ask how many are real.
- **How:** OpenFEMA NFIP claims are zip-level, so compare claim rates in flagged vs unflagged properties within the same zips (`validation/nfip_claims.py` already joins them). The FEMA ISAA non-redacted request (PROJECT_STATE §7) would allow per-address checking.
- **Ships if:** flagged properties show a clearly higher claim rate than unflagged ones in the same zips.

### B3. Put the learned water model into the real pipeline
- **Why:** on independent hand labels, per-chip Otsu over-detects badly (precision 0.23); the learned model is far better on the test split. Our pipeline uses a guarded Otsu with a pre-event baseline, so the gap may be smaller there, but the risk is real.
- **Status:** step (a) is done: the pipeline's guard fixes plain Otsu's over-detection (IoU 0.44 test / 0.69 Bolivia vs learned 0.63 / 0.61). The learned model is not yet a clear win; it needs more regions, especially cities, before it can replace the guard.
- **How:** (a) done (see Part A). (b) Train an Earth Engine-native classifier (`ee.Classifier.smileGradientTreeBoost`) on the same features computed in Earth Engine, so it runs inside the live pipeline as an extra vote. (c) Add pre-event change features once a dataset with pre-event imagery is available (Sen1Floods11 has none).
- **Ships if:** it improves precision on `validation/extent_check.py` (USGS mapped extent at Brazos/Harvey) without losing recall, and does not get worse on Bolivia.
- **Risk:** the Bolivia result says the gain may not transfer; train on more regions (add UrbanSARFloods for cities) before trusting it.

### B4. Validate analogs as a predictor (event-level backtest)
- **Status (2026-11-08):** backtest run (`validation/analog_backtest.py`, 231 FEMA events with >= 100 NFIP claims, leave-one-out): **analogs do not beat the naive baseline** on absolute error (payout error difference CI -0.04..+0.03 log10; share-paid -0.008..+0.011), though they rank events somewhat (Spearman 0.24 payout, 0.35 share paid). Cause alone is worse than baseline. Keep the 'evidence, not a forecast' label.
- **v2 DONE (2026-11-08, `docs/PREREG_ANALOGS_V2.md`):** constant dollars (CPI-U) + 7-day IMERG rainfall term, run once. Analog error 0.201 vs baseline 0.229 (median abs log10), diff CI -0.061..+0.025: **decision rule not met, analogs stay 'history, not a forecast'.** The product now shows payouts in today's dollars with the measured error band (typically 1.6x, 80% within 2.6x; baseline 1.7x). Next candidates, each a new pre-registration: storm wind/surge for coastal events, exposure-at-risk scale, pre-2000 events from the NFIP back-catalogue.
- **Why:** analog priors are shown as history. To call them a forecast we must show they predict.
- **How:** leave-one-event-out over the FEMA library: for each US event, predict median payout, share paid and claim count from its analogs found *without* it, and compare with a naive baseline (national median). Report error and rank correlation.
- **Ships if:** analogs beat the naive baseline with a CI that excludes zero improvement. Until then the UI keeps the "evidence, not a forecast" label.
- **Add:** a scale feature (rainfall total from IMERG/MRMS, storm intensity from NHC best track) so a catastrophic event stops matching modest ones.

### B5. Rainfall and river nowcast between passes
- **Why:** passes are ~7 days apart at Houston; gauges cover only rivers. Rain ponding and small creeks need rainfall.
- **How:** MRMS hourly rainfall (US) and GPM IMERG (global) accumulated over the event, plus National Water Model streamflow on every river reach (AWS, free) converted to stage with the NOAA HAND-FIM synthetic rating curves. Produce a "likely flooded" map before the first radar pass.
- **Ships if:** on Brazos and Harvey (and B1's event), the nowcast map's recall at high-water-mark sites beats radar alone, measured the same way.

### B6. Gauges and terrain outside the US
- **Why:** Lismore (Australia) has radar only.
- **How:** Australian Bureau of Meteorology Water Data (KiWIS, free, reachable): Wilsons River gauges with AHD datums; NSW lidar via ELVIS for ground. Same interpolation code (river lines from HydroSHEDS).
- **Ships if:** validated against any available surveyed flood marks for the 2022 event (NSW SES / council flood studies; needs a search).

### B7. Independent extent second opinion
- **Why:** carriers trust agreement between independent sources.
- **How:** Copernicus EMS rapid-mapping vectors (free, no account) for events with an activation; NASA OPERA DSWx-S1 once a free Earthdata login exists. Show "sources agree / disagree" per property.

### B8. Open-source path off Earth Engine
- **Why:** licence cost and dependency; carriers' security reviews.
- **How:** an imagery-provider interface with two implementations: Earth Engine (today) and ASF HyP3 RTC + Planetary Computer / Element 84 STAC. Re-run every validation script on the new path; numbers will change and must be re-reported.

### B9. Productisation of what exists
- Dispatch queue: surface gauge conflicts as their own lane.
- Audit PDF and catastrophe report: include gauge evidence, pass timing and analogs with their caveats.
- Monitor service: auto-queue a run when a storm cone covers a loaded portfolio or a gauge enters moderate flood.
- Run `validation/source_probe.py` in CI weekly; alert when a source breaks.

---

## Part B+. Loss calculation: next steps (free first)

1. **Carrier calibration pilot** (needs outreach): fit the same model on a carrier's
   own settled claims for one event; replaces NFIP assumptions and is the biggest
   accuracy gain available. Re-run `validation/loss_model_check.py` on their data.
2. **Re-test on reserved storms** (free): Ian 2022, Sally 2020, Michael 2018 were
   held back; run the unchanged model once, pre-registered, for a second independent
   check of the storm-total error and of storm calibration.
3. **Fix contents** (free): contents distributions are far more skewed and
   limit-capped; try a per-coverage structure and validate by leave-one-storm-out
   BEFORE any held-out look; until then contents stay flagged.
4. **Flood duration**: not in public claims; collect it in the carrier pilot, then
   replace the placeholder multiplier.
5. **Depth error end to end**: feed the model our own radar/gauge depth at USGS
   high-water-mark sites and report the combined error (today the dollar test uses
   true depth and the depth tests are separate).
6. **Private-market terms**: add coinsurance, ACV depreciation schedules, sub-limits
   and endorsements as optional per-policy fields once a carrier supplies examples.
7. **Claim propensity**: P(claim | depth) from NFIP policies-in-force (OpenFEMA
   NfipPolicies) to turn "if claimed" estimates into expected portfolio loss.

---

## Part C. Paid or outreach, only after Part B shows where the gaps are
1. Carrier pilot with claim outcomes (per-address labels: the single biggest unlock).
2. FEMA ISAA request for non-redacted NFIP claims (drafted in `docs/FEMA_DATA_REQUEST.md`).
3. Free vendor samples of high-resolution SAR (ICEYE, Capella, Umbra) to test urban detection, then paid tasking if it helps.
4. Earth Engine commercial licence (or B8 instead).
5. Commercial licences for non-commercial datasets if they stay in the product (Global Flood Database, FABDEM).

---

## How to reproduce Part A
```bash
python validation/gauge_depth_check.py --event brazos      # and --event harvey
python pipeline/05_gauge_depth.py --event brazos           # per-property gauge depth
python validation/s1f11_benchmark.py --data <sen1floods11 dir with S1Hand/ LabelHand/ splits/>
python pipeline/library/build_gfd.py && python pipeline/library/enrich_nfip.py
python pipeline/library/build_fema.py                      # resumable, ~1 h
python validation/source_probe.py && python pipeline/data_sources.py --md
```
Earth Engine credentials (`GEE_SERVICE_ACCOUNT_KEY_JSON`) are needed for the
lidar ground sampling and the Global Flood Database pull.
