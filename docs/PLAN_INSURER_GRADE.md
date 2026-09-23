# Altis — Plan to Insurer-Grade

*Prepared 2026-09-23. Sources: ICEYE, "Harnessing SAR Technology for Efficient Flood
Claim Triage…" (15 Jun 2023); ICEYE Open Data programme; Umbra Open Data (AWS Registry
of Open Data); Inunda (arXiv:2607.09614); and a read of every module currently in this
repo.*

---

## 0. What I actually read, and what it tells us

The ICEYE blog is not a technology post. It is a **workflow post**. It names six places
where a flood-depth layer changes an insurer's day:

| # | Workflow ICEYE names | What the data has to answer |
|---|---|---|
| 1 | **FNOL triage routing** | Which claims go to desk / field / fast-pay — especially with surge temp staff who can't judge |
| 2 | **Emergency payments** | Does this customer need cash today, and is their story consistent with the water? |
| 3 | **Emergency accommodation** | How long will this home be uninhabitable? (hotel → long-let, early) |
| 4 | **Payment without a site visit** | Can we settle on remote evidence alone? |
| 5 | **Fraud / outliers** | Claim outside the flood extent, or severity that doesn't match the depth |
| 6 | **Late-reported claims** | Who *should* have flooded but hasn't called? Proactive outreach |

That is the whole buyer conversation, and it is worth being blunt about our position:

**We cannot beat ICEYE on data.** They own a constellation with sub-daily revisit and
they fly it at the flood peak. We use Sentinel-1, which passes every 6–12 days and will
often miss the peak entirely. Competing on "better imagery" is a losing race we have no
capital to run.

**We can beat them on what happens between the passes, and on what happens at the
building.** ICEYE sells an *observation*. Every one of those six workflows actually needs
an *inference about a specific insured structure at a specific hour* — peak depth above
the finished floor, how long it stayed there, whether it is even this policy's peril.
That inference is physics plus structure, not pixels, and it is buildable by two people
with free data.

So the strategy is: **stop selling a flood map, start selling a defensible per-property
answer with its reasoning attached.**

---

## 1. Honest audit — what exists today

Working and tested (233 backend tests, 30 frontend):

- Sentinel-1 flood detection with Otsu thresholding, speckle filter, VV/VH cross-check,
  JRC permanent-water masking (`pipeline/flood_detect.py`)
- CHIRPS event rainfall already summed per event (`flood_detect.py:407`)
- FEMA depth-damage curves and $ claim ranges (`pipeline/severity.py`)
- Depth uncertainty propagated in quadrature (`pipeline/uncertainty.py`)
- Isotonic/Platt calibration machinery, Brier + ECE + reliability curves
  (`pipeline/calibration.py`) — **built but starved of labels**
- NHC storm tracks (`backend/storm_tracks.py`) — **loaded but only drawn on the map**
- Real OSM building footprints, 98% join rate, measured roof geometry via Solar API
  (`pipeline/building_context.py`, `backend/buildings.py`, `backend/solar.py`)
- 3D terrain-draped extruded buildings with water surface (`frontend/src/components/Globe.jsx`)
- Adjuster feedback capture incl. first-floor type and storey count (`backend/database.py`)
- PDF event + CAT reports (`backend/reporting.py`), provenance tracking (`pipeline/provenance.py`)

The gap is not capability. It is that **three assets are already in the repo and doing
nothing**: storm tracks, CHIRPS rainfall, and the calibration module. Phases A–C below
mostly turn on things we already paid for.

Not built at all: the hydraulic router (Phase 1 of the original spec) and pre-landfall
forecast triage (Phase 3).

---

## 2. The four differentiators, graded honestly

### A. Synthetic Revisit — **BUILD. This is the flagship.**

The Bates et al. (2010) local-inertial router from the original spec, calibrated so its
simulated extent matches the SAR mask we *do* observe. Once it matches on the observation
date, you can run it forward and backward in time and get depth on the hours SAR never saw
— including the peak.

Why this is the single highest-value thing we can build:

- It attacks the one weakness we actually have (revisit) with the one resource we
  actually have (compute).
- It is the **only** way to answer ICEYE workflow #3 (accommodation duration). Duration
  is a time integral. One snapshot cannot produce it. A router can.
- It converts every downstream number from "depth when the satellite happened to look"
  to "peak depth" — which is what the damage curve actually wants.
- It makes us *more* useful on Sentinel-1 than a competitor is on a raw ICEYE tile,
  because they are still selling a snapshot.

Language discipline, carried over from the original spec: we say **"hydraulically
consistent"**, never "more accurate." We have not validated per-property depth against
ground truth and must not imply we have. Every routed output ships with the calibration
residual against the observed SAR mask, and that residual goes in the UI.

Deliverable: `pipeline/hydraulic_route.py` — CFL-bounded (`dt = C·dx/√(g·h_max)`, C=0.4),
Froude≤1 face cap, mass-conservative flux/depth sweeps, Manning's n by land cover,
calibrated by shifting inflow volume until simulated wet/dry best matches the SAR mask
(IoU objective).

### B. Structure-Aware Depth — **BUILD. Second priority, and it is cheap.**

Today `max_depth_ft` is depth above *terrain*. An adjuster does not care about terrain.
They care about **depth above the finished floor**, because that is the number the
depth-damage curve was authored against and the number that decides contents loss.

We already hold every input:

```
depth_above_floor = water_surface_elev
                  − ground_elev_under_footprint   (terrain, per-footprint not per-pixel)
                  − first_floor_offset            (slab / crawlspace / pier / basement)
```

`first_floor_type` is *already a captured field* in `adjuster_feedback`. Solar API already
gives us measured roof elevation. We publish the offset table with citations (FEMA P-259
elevation conventions) and let the adjuster override it per property — their override
becomes a label, which feeds §3.

This single change moves us from "a flood map with addresses on it" to "a structure
model." It is maybe 400 lines.

### C. Forensic Evidence Pack (PDF) — **BUILD. Low risk, disproportionate sales value.**

`backend/reporting.py` already emits PDFs. Extend to a **per-property** pack that a
claims file can hold and a litigator can survive:

- Pre/post SAR chips with acquisition timestamps and orbit IDs
- The depth stack: terrain → water surface → finished floor, drawn to scale
- Router hydrograph (depth vs. time) with the SAR observation marked on it, and the
  calibration residual stated
- Every flag raised, with the evidence that raised it
- Full provenance chain from `pipeline/provenance.py`: dataset IDs, processing dates,
  thresholds used, code version
- An explicit **limitations** section. Counter-intuitively this is the most persuasive
  page in the document to a technical buyer, and its absence is what makes vendor output
  smell like marketing.

### D. Actionable Playbook API — **BUILD, but scope it down and rename it.**

The honest version is not a "playbook." It is: **the six ICEYE workflows, each as an
endpoint returning a decision plus its reasons.** No new science — it's composition over
A–C.

```
POST /api/triage/route         → desk | field | fast-pay, with reasons      (#1)
POST /api/triage/emergency     → advance-payment recommendation             (#2)
GET  /api/triage/habitability  → days-uninhabitable estimate from hydrograph (#3)
POST /api/triage/consistency   → claim-vs-observation consistency score     (#5)
GET  /api/triage/silent        → expected-loss properties with no FNOL      (#6)
```

Workflow #4 (settle with no site visit) we expose as *inputs to* that decision, never as
the decision. We are not going to tell a carrier to pay a claim unseen on our say-so, and
any buyer serious enough to matter will respect the line.

---

## 3. The five adjuster flags — what's real on free data

| Flag | Data | Status | Note |
|---|---|---|---|
| **Wind vs. Water** | NHC track already in `storm_tracks.py` + our depth | ✅ free, build now | The #1 coverage dispute in every US hurricane. Wind is usually covered, flood usually isn't. Radius-of-max-wind vs. peak depth timing is genuinely decisive, and we already load the track. |
| **Flash-Flood Miss** | CHIRPS already in pipeline | ✅ free, build now | High rainfall + no SAR-detected standing water = water came and went between passes. Today we silently call that "not flooded." That is our worst false-negative mode and we can flag it in a day. |
| **Pre-Existing Pooling** | JRC occurrence already used for masking | ✅ free, build now | Property sits on ground wet >N% of years → some of this "flood" is baseline. Directly feeds fraud/outlier scoring (#5). |
| **Inaccessible Property** | OSM road network + flood mask | ✅ free, build now | Graph-cut the road network against the extent: is there a dry path from the nearest arterial? Drives *dispatch*, which is the thing a CAT manager loses sleep over. Nobody in this market is doing it. |
| **Silent/Late Claim** | our flood set vs. carrier FNOL list | ⚠️ needs carrier data | ICEYE workflow #6 and one of the highest-ROI items commercially — but it is a set difference against claims we don't have. Build it as an upload diff: they give us FNOLs, we return the "should have called" list. Honest framing: our side is ready, the value unlocks at pilot. |

Four of five run on data already in the repo.

---

## 4. Umbra & ICEYE open data — the honest read

I browsed the Umbra bucket directly: 82 task folders, real flood taskings (e.g.
`Texas_Floods_2025-08/` with 39 scene collections), CC-BY 4.0, no-auth S3, STAC catalogued,
12.5cm azimuth × 50cm range spotlight. ICEYE now runs an equivalent open programme (map
browser, STAC, and AWS Registry — SLC/GRD/COG, no registration).

What they are **not**: a Sentinel-1 replacement. Coverage is opportunistic — neither
archive covers Harvey, Ian, or Lismore, our three demo events. You cannot build a product
on an archive that may or may not have looked at your customer's flood.

What they **are**, and this is worth real effort:

1. **A validation corpus.** Where a 50cm Umbra/ICEYE scene overlaps a Sentinel-1 flood we
   detected, we can measure our 10m extent against sub-metre truth and publish the
   agreement number. Right now our confidence scores are *asserted*. This makes one of
   them *measured*. For a buyer, "we validated against 50cm SAR on N events" is worth more
   than any feature on this list.
2. **A demo asset.** A 50cm spotlight chip next to our 10m mask is the single most
   convincing slide we could put in front of a carrier.
3. **A confirmation tier**, clearly labelled as opportunistic, where coverage happens to
   exist.

### On building an ML model — still no, and here is the sharper reason

Restating the prior assessment because it hasn't changed: `adjuster_feedback` does not yet
hold enough labels to train anything that beats the physics. But the deeper point is that
**a flood model is not a learning problem, it is a physics problem with a calibration
layer on top** — and `pipeline/calibration.py` is already the right shape for that layer.
It is built, tested, and starved.

So the ML work is: feed it. Every flag override, every first-floor-type correction, every
agree/disagree is a label. Ship §2B and the flags, run one pilot, and the calibrator will
have something real to fit. Training a neural net on 60 Houston addresses would be
theatre, and a technical buyer would catch it.

---

## 5. Sequenced plan

**Phase A — Turn on what we already paid for** *(fastest value per line of code)*
1. Wind-vs-Water flag (storm tracks → decision)
2. Flash-Flood Miss flag (CHIRPS → decision)
3. Pre-Existing Pooling flag (JRC occurrence → decision)
4. Inaccessible Property flag (OSM road graph-cut)
5. Flags surfaced in `PropertyDrawer`, each with an adjuster override that writes a label

**Phase B — Structure-Aware Depth**
6. Per-footprint ground elevation (not per-pixel)
7. First-floor offset table + per-property override
8. `depth_above_floor` threaded through severity, damage curves, and triage class
9. Re-point the 3D water surface at finished-floor depth so the visual matches the number

**Phase C — Synthetic Revisit (the flagship)**
10. `pipeline/hydraulic_route.py` — Bates-2010 local-inertial solver
11. SAR-mask calibration loop (IoU objective), residual reported everywhere
12. Peak depth + hydrograph per property
13. Habitability duration from the hydrograph (ICEYE workflow #3)

**Phase D — Evidence & API**
14. Forensic per-property PDF pack
15. The five triage endpoints
16. Silent-claim FNOL diff (upload-based)

**Phase E — Validation**
17. Umbra/ICEYE STAC overlap search against our detections
18. Measured agreement statistics, published in the PDF and the UI

Phases A and B are independent and can interleave. C depends on nothing but is the
largest single build. D depends on B and C. E can start any time.

---

## 6. Things I am deliberately *not* proposing

- **Buying tasking.** No budget, and it puts us in ICEYE's race.
- **A trained ML depth model.** Not enough labels; physics is better here and we'd be
  caught.
- **"More accurate than X" claims.** We have no per-property ground truth. Phase E is how
  that changes, and not before.
- **Auto-settlement.** We inform the decision. We don't make it.
- **A fraud *score*.** We surface *outliers with reasons*. Scoring people is a regulated
  activity and a liability we should not volunteer for.

---

## 7. What I need from you

Approve, cut, or reorder. My recommendation is **A → B → C → D → E**: Phase A is nearly
free and makes the demo visibly smarter this week, Phase B is what makes us a structure
model rather than a map, and Phase C is the thing nobody else is doing.

Once approved I will work straight through without further questions, committing to
`claude/altis-physics-depth-034vaw` as each item lands, with tests for every phase.
