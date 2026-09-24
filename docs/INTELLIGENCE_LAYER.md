# Altis Intelligence Layer — what it does, how it works, how it was checked

This layer sits on top of the Sentinel-1 triage result. The triage class answers
*what do we do with this claim*. The intelligence layer answers *what could make
that answer wrong, or expensive*, and it attaches the evidence to every answer.

Everything here runs on **free, key-less data**. Nothing needs a new paid API or
a new environment variable, and nothing needs Earth Engine.

| Source | Used for | Licence |
|---|---|---|
| AWS Terrain Tiles (USGS 3DEP in the US, SRTM elsewhere) | DEM, HAND, router terrain, site maps | open |
| NOAA/NWS Stage IV (radar + gauge) | US event rainfall | public domain |
| CHIRPS v2.0 daily COGs | rainfall elsewhere, and the susceptibility model | public domain |
| Copernicus Data Space STAC | exact Sentinel-1 pass times | open |
| JRC Global Surface Water v1.4 | historic surface-water occurrence | CC-BY 4.0 |
| OpenStreetMap (Overpass) | road network | ODbL |
| NHC HURDAT2 best track | hurricane wind radii; automatic storm detection back to 1851 | public domain |
| Open-Meteo forecast | 7-day rainfall forecast for pre-landfall scoring | CC-BY 4.0 |
| Sen1Floods11 | labels for the susceptibility model | CC-BY 4.0 |
| FEMA NFIP redacted claims | independent validation (zip level) | public domain |
| Umbra / ICEYE open SAR (STAC) | independent-validation search | CC-BY 4.0 |

---

## Phase A — Adjuster flags (`pipeline/flags.py`)

Each flag has a severity (`alert` / `caution` / `info`), a plain-English
explanation, the evidence that raised it, and a confirm/dismiss control in the
drawer. The verdict is stored in `flag_feedback` and becomes a label.

* **WIND_WATER** — peril allocation. Peak sustained wind at the property comes
  from a modified-Rankine vortex fitted, per quadrant and per 15-minute step, to
  NHC's analysed 34/50/64-kt radii. Rmax comes from the best track, or from
  Willoughby et al. (2006) when the track lacks it. A ×0.85 marine-to-open-terrain
  factor is applied. The flag reads *concurrent* (wind + water above the floor),
  *wind-driven*, or *hurricane wind + possible undetected flooding* when the
  transient flag says SAR can't be trusted. For live events the storm is found
  automatically in the full HURDAT2 archive (tested: Katrina/New Orleans,
  Sandy/NYC, Harvey, Ian).
* **TRANSIENT_MISS** — SAR reads dry, but there was heavy rain (or hurricane wind
  over ground ≤ 2–3 m above sea level), the property is low relative to drainage
  (HAND), and the first radar pass came ≥ 12 h after the peak. An alert **blocks
  remote denial** (Remote-Deny → Review, with the original class kept).
* **PRIOR_WATER** — JRC shows surface water at or near the property in many
  historical observations. This is a prior-loss / pre-existing-damage check.
* **ACCESS** — the OSM road graph is cut against the water surface (≥ 0.30 m is
  impassable, per NWS "Turn Around Don't Drown"). A property is *street flooded*,
  *cut off* (no dry route to an arterial leaving the area) or accessible. A road
  fragment that is disconnected even when dry is reported as unknown, never as a
  flood finding.
* **FLOOR_CLEAR** — water reached the lot but stayed below the finished floor.

**Real results on the demo events**
* **Harvey:** Sentinel-1's first pass came 36 h after Stage IV's peak rain day
  (712–800 mm over 3 days). 577 of 842 SAR-only remote-denials are held back.
* **Ian:** Sentinel-1 did not image Port Charlotte until **Oct 2, four days after
  landfall**. Estimated Cat-3/4 open-terrain winds everywhere; 871 denials held back.
* **Lismore:** 173 access flags (135 street-flooded, 38 cut off), and 418
  households likely displaced.

**Independent check against FEMA NFIP flood-insurance claims**
(`validation/nfip_flag_check.py`, zip level)
* Harvey: SAR-only triage would have remote-denied **592 properties in zip codes
  that filed 17,048 NFIP claims** during the event. The flag holds back **70%**
  of them. Thresholds were set before this comparison and were not re-tuned
  against it.
* Ian: the flag holds back 97% of the 684 SAR-only denials in flood-confirmed zips.
  It also holds 96% of the 55 in quiet zips. Ian's rain and surge exposure were
  extreme area-wide, and a quiet NFIP zip is only a weak proxy for "not flooded".
* Limitations: truth is zip level; NFIP take-up varies by zip; a claim count is
  not a flooded-home count.

## Phase B — Structure-aware depth (`pipeline/structure_depth.py`)

`depth_above_floor = depth_at_grade − first_floor_height`

Default first-floor heights come from HAZUS: slab 1 ft, crawlspace 3 ft, raised
5 ft, piers 7 ft, basement 4 ft. Each has a 1σ spread. An unknown foundation is
assumed to be slab with a wider uncertainty. When the adjuster records the
first-floor type in the drawer, that observation replaces the assumption. Damage
curves are fed depth above the floor, which is what they were built on.

In the 3D view, each house gets a white finished-floor band, so the water can be
read against the floor.

## Phase C — Synthetic revisit (`pipeline/hydraulic_route.py`, `backend/routing.py`)

This is a Bates et al. (2010) local-inertial 2D solver. The CFL limit is C = 0.4.
Every face carries a Froude ≤ 1 cap, and a mass limiter means no cell can export
more water than it holds. Tests hold the solver to exact invariants: a lake at
rest stays still, and mass is conserved to 1e-10 relative through a dam-break,
rain-filled basins and a sea boundary.

How the solver is set up:
* Rain-on-grid is forced by observed daily rainfall over the event's catchment
  (study box ± 0.35°).
* The open sea is held at sea level, and water that reaches it leaves the domain.
* The terrain is conditioned for a 4-neighbour model. Without this, diagonal-only
  valleys act as dams: before the fix, water stood 20 m deep in hill valleys.
  Channels are burned by at most 2 m, and artefact pits are filled.
* A single forcing multiplier *k* is calibrated. It maximises the Critical
  Success Index against the SAR-observed wet/dry state at the pass time.
* Anchoring: the SAR depth at the pass is ground truth. The router supplies the
  hydrograph's shape, so `depth(t) = max(0, sim(t) − sim(t_pass) + obs)`.

**Lismore result:** calibrated fit CSI 0.645 at the Mar 2 pass (256 hits, 10
misses, 131 false alarms, k = 1.44, 500 m grid). 263 of 266 flooded parcels peak
on **Feb 28**, which matches the real flood peak. The SAR pass came 2.5 days after
the peak, and the water then stood about 2.7 ft higher than what the satellite saw.

Per property it gives peak depth and time, hours wet, hours above the floor, and
a habitability planning band (`pipeline/habitability.py`). It is labelled
"hydraulically consistent", never "more accurate".

Harvey and Ian are not routed: SAR saw 0 and 1 flooded properties respectively,
so there is nothing to calibrate against. The system says so, and the transient
flag covers those events.

## Phase D — Evidence pack + insurer workflows

* `GET /api/property/{id}/evidence-pack?event_id=` gives a 4-page PDF: the
  decision and confidence factors, measurements, the water-vs-floor schematic,
  the routed hydrograph with the pass marked, flags with evidence, event
  context, a real site map (terrain + water + roads), full data lineage, a
  limitations section, and a SHA-256 digest of the inputs on every page. It never
  embeds synthetic imagery.
* The ICEYE workflows as decisions with reasons (`backend/triage_api.py`):
  `POST /api/triage/route` (#1), `/emergency` (#2), `GET /habitability` (#3),
  `/consistency` (#5, outliers with reasons, deliberately *not* a fraud score),
  `POST /api/triage/silent/{event}` (#6, expected losses with no FNOL).

## Phase E — Independent validation

* **NFIP** (above): the strongest independent check we have.
* **Umbra / ICEYE open SAR** (`backend/opendata.py`, `pipeline/validation_metrics.py`):
  the search reads both static STAC catalogs. It finds 176 Umbra + 2 ICEYE scenes
  in a test window where they exist, and **correctly reports zero overlap** for
  Harvey, Ian and Lismore (Umbra's STAC starts in 2023). The agreement harness
  (Otsu water mask on high-resolution amplitude → contingency table, CSI with a
  Wilson interval) is ready and tested for the first overlapping event.

## Phase F — Pre-landfall susceptibility

`pipeline/susceptibility.py`, `pipeline/gbdt.py`, `pipeline/06_train_susceptibility.py`

* The model is gradient-boosted trees (a pure-numpy implementation, checked
  against scikit-learn in tests). Features are HAND, slope, upstream area,
  distance to drainage, floodplain position at 500 m and 2 km, historic water,
  3-day and 7-day rain.
* It is trained on Sen1Floods11: 446 hand-labelled flood chips from 11 real
  events, with permanent water excluded. It is validated **leave-one-event-out**,
  so every skill figure comes from floods the model never saw.
* The UI has a rainfall slider. Each property carries a curve of probability
  against rainfall (forced monotone), so the slider is instant. The panel shows
  a book ranking, the insured value at high risk, and an option to colour the
  map by probability. Portfolios can be scored against the live Open-Meteo 7-day
  forecast.
* **Leave-one-event-out AUC 0.829 pooled** (445 chips; 2 skipped when the
  CHIRPS server dropped the connection). Per held-out event: Spain 0.91,
  Nigeria 0.90, USA 0.89, Sri Lanka 0.89, Pakistan 0.86, India 0.85,
  Ghana 0.79, Paraguay 0.73, Bolivia 0.64, Somalia 0.63.
* **Back-test on Altis's own data: Lismore AUC 0.868** against the
  radar-observed flooding. That event was never in training.
* Harvey and Ian: SAR-dry is not ground truth, so no AUC is claimed. Against
  raw NFIP claim counts per zip the Spearman is negative (−0.30 / −0.13). This
  is a weak test, because counts aren't normalised by homes or policies per
  zip and nearly every sampled zip flooded. It is reported, not hidden.

## Rebuilding the baked data

```bash
python pipeline/05_build_intel.py                 # flags, structure, wind, router (≈ 20 min)
python validation/nfip_flag_check.py harvey ian   # FEMA NFIP check
python pipeline/06_train_susceptibility.py        # model (≈ 45 min, downloads Sen1Floods11)
python pipeline/07_build_susceptibility.py        # hindcasts + back-tests + open-data search
```

The outputs (`outputs/*_intel.json`, `*_susceptibility.json`,
`nfip_validation_*.json`, `opendata_*.json`, `pipeline/models/susceptibility_v1.json`)
are committed, so the deployed backend serves them with no network work at
request time.
