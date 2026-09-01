# What a mid-market carrier needs, what Altis delivers, and every gap between

**Written 2026-08-16.** Every number here is measured and traceable to a script
in `validation/`. Nothing is projected, and where something is unmeasured this
document says so rather than estimating.

Companion docs: `DETECTION_LIMITS.md` (the experiments), `PROJECT_STATE.md`
(where the project is), `FEMA_DATA_REQUEST.md` (the data-access reality).

---

## 0. The one-paragraph summary

Altis today produces a **defensible regional severity signal** and **does not
produce a defensible per-property flood call.** Zip-level detected depth
correlates with claimed depth at **+0.366** and with paid amounts at **+0.537**.
At property level, against 2,999 USGS-labelled structures per event, every
signal tested lands between **AUC 0.499 and 0.552** — statistically
indistinguishable from guessing. The cause is physical and is now measured, not
inferred: at surveyed flood points the C-band radar return is **brighter** than
baseline (+0.39σ Brazos, +2.83σ Harvey) while the detector tests for darkening.

That single fact reorganises every gap below.

---

## 1. The commercial workflow, gap by gap

A CAT triage product has to answer eight questions. Here is each one, what we
can actually support, and what closing the gap requires.

### Gap 1 — "Did this specific property flood?" ❌ NOT SUPPORTED

**What the carrier needs:** a per-address flood / no-flood call reliable enough
to skip an inspection.

**What we measured** (`validation/extent_check.py`, USGS SIR 2018-5070 labels,
2,999 structures per event):

| signal | Brazos precision / recall / AUC | Harvey precision / recall / AUC |
|---|---|---|
| shipped detector | 0.0% / 0.0% / **0.499** | 75.0% (n=8, CI [41, 93]) / 0.5% / **0.503** |
| double-bounce (4e) | not measurable (rural) | 38.4% / 59.7% / **0.520** — flags 60% of book, **1.00× lift** |
| sub-pixel (4a) | not measured | 37.0% / 33.8% / **0.491** — **below** base rate |
| **dual-pol (4b)** | 44.0% / 2.0% / **0.503** | **51.2% / 3.8% / 0.508 — 1.33× lift, the only usable signal** |
| Sentinel-2 optical | 37.9% / 4.6% / **0.502** | 30.4% / 3.0% / **0.493** |
| HAND terrain only | 38.4% / 91.2% / **0.552** | 35.8% / 78.6% / **0.458** |
| learned ensemble (4d) | — / — / **0.471** (spatial CV) | — / — / **0.442** (spatial CV) |

Base rates: 36.5% Brazos, 38.3% Harvey.

**Why it fails:** `validation/gate_probe.py` decomposes it. Terrain gates are
innocent (28/28 and 63/63 marks pass slope and permanent-water). The radiometry
rejects everything because the signal has the **wrong sign**.

**How to close it, in order of expected effect:**

1. ~~**Enable double-bounce.**~~ **DONE AND REJECTED (§14).** Measured against
   the negative class it flags **60% of the book** at **38.4% precision** —
   identical to the 38.3% base rate. Its 9× recall advantage over high water
   marks was bought entirely by firing on most things. Stays disabled. The one
   survivor is **dual-pol: 51.2% precision [40.8, 61.4] on 86 structures, 1.33×
   lift** — narrow, already enabled, and the only signal an adjuster could act
   on today.
2. **Add a brightening branch to the primary mask.** Even with double-bounce
   enabled, `orbit_flood_mask` still requires darkening. The mask needs to be
   `darkening OR (urban AND brightening)`, not darkening alone. **Not built.**
3. **Move off C-band VV for urban.** L-band (ALOS-2/PALSAR-2, NISAR from 2025)
   penetrates canopy and behaves differently against buildings. NISAR data is
   free. **Not evaluated — this is the highest-ceiling unexplored option.**
4. **Interferometric coherence.** Flooding destroys phase coherence between
   passes even where amplitude is ambiguous. Sentinel-1 SLC supports it; GEE
   does not host SLC, so it needs ASF/ESA processing. **Not built, well-proven
   in literature.**

**Honest position for a sales conversation today:** we cannot make this call.
Saying so is the credibility asset; every carrier's actuary will test it.

---

### Gap 2 — "How deep?" ⚠️ MEASURED, AND BIASED LOW

**What we measured** (`validation/hwm_check.py`, surveyed USGS depths):

- Depth bias **−2.64 ft** (Brazos), **−2.67 ft** (Harvey).
- Bias **worsens with depth**: −0.77 ft at surveyed 0–1 ft, **−5.64 ft at ≥4 ft**.

The deeper the real flood, the more we miss — the opposite of the error profile
a severity product can tolerate, since deep floods drive the loss.

**How to close it:** depth is derived from water-surface elevation minus ground,
so it inherits the detection failure — if the mask misses the water, depth is
zero. **Gap 1 must close first.** After that, depth accuracy is bounded by DEM
vertical error (3DEP 1 m lidar is good; MERIT/Copernicus outside the US is not).

---

### Gap 3 — "How deep relative to the first floor?" ✅ BUILT, UNVALIDATED

Built and correct in principle (`pipeline/structures.py`): NSI foundation
heights, `depth_above_first_floor`. This is the quantity damage curves actually
consume, and most competitors skip it.

**Gap:** never validated against real first-floor elevations. NSI foundation
height is **modeled**, not surveyed.

**How to close:** elevation certificates. FEMA holds them for NFIP properties;
communities hold them locally. A sample of a few hundred, compared against NSI's
modeled `found_ht`, would quantify the error in a day's work. **Not attempted.**

---

### Gap 4 — "For how long?" ⚠️ BUILT, NEVER VALIDATED

Duration comes from post-window slices (`flood_s0/s1/s2`). At the marks these
were **all zero** — inherited from Gap 1.

**Gap:** duration is also revisit-bounded. With a 6–12 day repeat, "duration"
resolves to multiples of ~6 days, which is far coarser than the 24–72 hour
distinctions that drive mould/contents decisions.

**How to close:** honest disclosure of the resolution floor, plus commercial
SAR or gauge-hydrograph fusion. Stream gauges give continuous stage at high
temporal resolution and are free — **fusing gauge hydrographs with satellite
extent is a genuinely underexplored, cheap win.**

---

### Gap 5 — "What will it cost?" ⚠️ BUILT ON NATIONAL CURVES

`pipeline/severity.py` implements HAZUS-style depth-damage curves by occupancy,
stories and basement, with structure/contents split and uncertainty intervals.

**Gap:** the curves are **published national shapes, not fitted to any book.**
We hold 3,135 real paid Brazos claims and have never fitted to them.

**DONE — and the national curves were measurably wrong.** Fitted to 25,011
real Harvey NFIP claims (`validation/fit_damage_curves.py`). Held-out MAE, zips
disjoint between train and test:

| segment | fitted | shipped | improvement |
|---|---|---|---|
| RES1-1S | 19.17pp | 27.12pp | **+7.94pp** |
| RES1-2S | 16.93pp | 24.45pp | **+7.53pp** |
| RES3-multi | 13.93pp | 14.93pp | +1.01pp |
| NONRES | 12.79pp | 14.64pp | +1.84pp |

The national shapes are wrong in both directions: at 1 ft a single-storey home
actually lost **38.1%** of value against HAZUS's **16.0%**; at 12 ft it lost
51.8% against HAZUS's 68.8%.

**Shipped DISABLED** (`SEVERITY['use_fitted_curves'] = False`). Enabling it
mixes calibration scales — HAZUS measures physical damage fraction, the fit
measures *paid* fraction conditional on a claim being filed — and only RES1
no-basement could be fitted. That inverts two physically true invariants
(manufactured homes stop being more vulnerable; contents stops exceeding
structure at shallow depth), both caught by existing phase-3 tests.

**Remaining work to turn it on:** fit RES2, the basement variants and the
contents curves on the same paid-claims basis, then re-run the invariant tests.

---

### Gap 6 — "When did it flood / did you even see it?" ❌ NOT SURFACED

Sentinel-1 samples every 6–12 days. Ian (2022) was dropped as a demo precisely
because the satellite never observed the event.

**Gap:** the product does not tell the user whether the pass **caught the crest
or missed it.** A confident "no flood detected" from a satellite that flew four
days late is actively harmful — it is the failure mode most likely to produce a
wrongly denied claim.

**DONE — and it found something.** `pipeline/crest_timing.py` reads USGS
stream gauges (15-minute stage, free) and compares each gauge's crest against
the Sentinel-1 acquisition **instants** the detector used. Verdict is
`observed / partial / missed / unknown`, recorded in the run manifest with
`safe_to_remote_deny`.

Measured on our own events:

| event | verdict | detail |
|---|---|---|
| Brazos | **partial** (2/3 gauges) | Brazos at Richmond crested 55.19 ft at 2017-09-01T05:15Z; nearest pass was **−40.9 h** — we photographed the rising limb and looked away while water kept climbing |
| Harvey | **partial** (16/21) | the five missed are the north Houston bayous, which crested 27 Aug before the first usable pass |

Both correctly return `safe_to_remote_deny: False`. The Brazos result is a
partial explanation for the −2.64 ft depth bias in Gap 2, and it was invisible
before this existed.

`unknown` is never collapsed into `observed` — most of the world has no gauge
coverage, and network failure degrades to `unknown` too. **Remaining work:**
the flag is recorded but `triage_core` does not yet consume it, so Remote-Deny
is not literally blocked in the triage output.

---

### Gap 7 — "How confident are you?" ⚠️ PARTLY BUILT, MISCALIBRATED

Confidence scoring, abstention flags (`dpol_available`, `db_available`,
`hand_ft = -1`) and ±1σ depth intervals all exist. The abstention discipline is
genuinely strong — **it caught a wrong published result during this session**
(see §3 below).

**Gap:** the calibration underneath is fitted on zip labels and is unstable
(Brier 0.2336 ± 0.1734 across 267 splits; skill score −0.0039, i.e. no better
than predicting the base rate). Confidence numbers are currently decorative.

**How to close:** recalibrate on the per-property USGS labels now available
(68,624 Brazos + 42,739 Harvey structures). **Unblocked, not done.**

---

### Gap 8 — "Does it work where my book is?" ❌ TWO TEXAS SITES, ONE STORM

Both validated areas are Hurricane Harvey, 2017, southeast Texas, ~40 km apart.

**Gaps:**
- **No non-CONUS validation.** NSI is US-only, so the international path (HAND +
  global DEM, no structure inventory) is *structurally different code* and has
  never been validated. "Works anywhere" is not currently defensible.
- **No non-Gulf US validation.** Riverine flooding in the Midwest, snowmelt,
  ice-jam and flash-flood regimes are unexercised.
- **No storm-surge validation.** Surge behaves differently from riverine and is
  a large share of coastal exposure.
- **Cross-event transfer is measured and poor:** a model fit on Brazos scores
  AUC 0.524 on Harvey; fit on Harvey scores 0.528 on Brazos — barely above
  chance, on the *same storm, 40 km apart.* Generalisation is not established
  even locally.

**How to close:** the USGS SIR 2018-5070 release covers **five more river
basins** already downloaded-adjacent (Neches, Sabine, San Bernard, Pine Island
Bayou, coastal basins), each with mapped boundary + extent. That is five more
validation areas for the cost of a download, within the same storm. For
genuinely independent events: Copernicus EMS rapid mapping publishes
delineations for European floods, and the Dartmouth Flood Observatory archive
covers global events. **Cheap, unstarted.**

---

## 2. Ground-truth gaps (the meta-gap)

Everything above is limited by what we can measure against.

| asset | status | what it gives | what it cannot give |
|---|---|---|---|
| NFIP redacted claims | ✅ in repo | zip-level severity, paid amounts | 14 independent bits; no per-property anything |
| USGS high water marks (2,364) | ✅ in repo | measured depth at points, recall | **no negative class** → no precision |
| USGS mapped extent + boundary | ✅ in repo | 111k labelled structures, both classes | modeled (HWM-interpolated), riverine corridor only |
| Sugar Land field survey (310) | ⚠️ found, unused | 138 field-verified flooded addresses in the Brazos bbox | positives only; Fort Bend only |
| Texas GLO structure damage | ❌ 403 | field-assessed per-structure damage | permission-gated; **worth an access request** |
| HCAD disaster reappraisal (65,862) | ❌ not downloadable | per-parcel Harvey damage, independent | conflates flood/wind/roof; owner-reported; no negative class |
| Non-redacted NFIP | ❌ not available to us | address-level claims | ISAA is community-scoped; see FEMA_DATA_REQUEST.md |
| Carrier design-partner book | ❌ none yet | **the real answer** — address-level, both classes, our actual population | requires a commercial relationship |
| Adjuster feedback loop | ❌ table absent | per-property confirmations | requires live usage |
| FNOL photos + vision | ❌ not built | interior waterline, cross-check | requires live usage |

**Sources checked and rejected during this work, with the reason** (so nobody
re-runs them):

- **FEMA Building Damage Assessments Harvey** — every endpoint 404s. Was a live
  2017 operational service, not an archive.
- **FEMA/HARC Harvey Flood Depths Grid** — confirmed TIN-interpolated from
  HCFCD + USGS high water marks. Circular against our HWM validation.
- **HCFCD Harvey Peak Inundation** — independent (gauges + helicopter survey),
  but the Addicks/Barker portion is literally a **DEM elevation threshold**
  (≤109 ft / ≤102 ft), "results have not been field verified", and non-riverine
  flooding is explicitly excluded. Scoring terrain-derived features against a
  terrain threshold would be near-circular, and outside-polygon ≠ dry.
- **Zenodo PRIMo hydrodynamic model** — simulated, HWM-calibrated.

---

## 3. Process gaps — how wrong results get published

Two errors reached documentation during this session. Both are worth recording
because the fix is structural, not vigilance.

**Error 1: an abstention reported as a measurement.** `extent_check` scored
sub-pixel and double-bounce as "never fires — precision n/a, recall 0.0%". Both
ship **disabled**, so the pipeline writes a constant-zero band. They were never
computed. The repo already emits `db_available` for exactly this reason; the
check simply was not made.
**Fixed:** `abstained()` now refuses to score a constant-zero band and prints
NOT MEASURED. **Remaining gap:** `water_fraction` has **no `_available`
companion flag** — unlike dual-pol and double-bounce. That asymmetry should be
closed in `flood_detect.py`.

**Error 2: a verdict driven by a number too small to carry it.** The first
ensemble verdict printed "BETTER" for both events, comparing precision at the
shipped detector's alert volume — **2 predictions at Brazos, 16 at Harvey** —
while AUC was below chance.
**Fixed:** ranking quality on held-out geography is now the gate, and precision
is reported across top 1/5/10/20% with Wilson intervals attached.

**Standing gap:** there is no automated guard that a reported metric has enough
samples behind it. A `min_n` assertion in the reporting helpers would have
caught Error 2 mechanically.

---

## 4. Product and operational gaps (unmeasured, but real)

These have never been assessed and would surface immediately in a carrier
procurement review:

- **No SLA or turnaround guarantee.** A full 4,000-property run is ~25 minutes,
  dominated by NSI fetch and sampling. CAT triage demands a portfolio-scale
  answer within hours of a pass. Untested above 4,000 properties.
- **No policy-system integration.** Carriers work in Guidewire / Duck Creek /
  Origami. A CSV upload is a pilot, not a deployment.
- **No audit trail for a disputed claim.** Provenance manifests exist
  (`pipeline/provenance.py`) but there is no per-property "why did you say
  that" artifact an adjuster could put in a file.
- **SOC 2 clock has not started.** Named in PROJECT_STATE as open. Mid-market
  carriers ask for it in procurement.
- **No regulatory position.** If a triage output influences a claim decision,
  state DOI rules on claims practices apply. Unexamined.
- **Single-region deployment**, no DR, no load testing.

---

## 5. What to do next, ranked by value per hour

Ranked on measured evidence, not enthusiasm:

1. **Fit depth-damage curves to the 3,135 real paid claims** (Gap 5). Unblocked,
   data in repo, converts a national table into an underwritten number. Days.
2. **Ship the crest-observed flag** (Gap 6). Cheap, and it removes the most
   dangerous silent failure in the product.
3. **Finish the double-bounce verdict, then build the brightening branch**
   (Gap 1, steps 1–2). The only path with a measured reason to expect a gain.
4. **Recalibrate confidence on the 111k labelled structures** (Gap 7).
   Unblocked.
5. **Add the five remaining USGS reaches** (Gap 8). Five validation areas for
   the cost of a download.
6. **Request Texas GLO access; ask HCAD for the reappraisal extract** (§2).
   Both are real, independent, and gated on an email rather than a technique.
7. **Evaluate L-band / NISAR and InSAR coherence** (Gap 1, steps 3–4). Highest
   ceiling, largest effort, and the only options that attack the physics rather
   than the thresholds.
8. **Land a design-partner carrier** — the single highest-value item on this
   page, because it supplies the one dataset nothing else can: address-level
   outcomes on our actual population, including the insured-but-did-not-file
   negatives.

---

## 6. What is genuinely strong today

Not everything is a gap, and a buyer should hear this part too:

- **The measurement infrastructure is unusually good.** Point-level depth
  validation, a real negative class, spatial-block cross-validation,
  abstention contracts, dry-land controls, threshold sweeps reported in full.
  Most flood-analytics vendors cannot produce an AUC against surveyed truth at
  all.
- **The failure modes are known, quantified and written down.** Canopy, urban
  double-bounce, revisit gaps, and the sign error are all measured with the
  experiments attached.
- **Depth above first floor** is modelled properly, which is the quantity
  damage actually depends on.
- **The regional signal is real** (+0.366 / +0.537) and is a defensible product
  for portfolio-level severity ranking and early reserve-setting today.

The honest pitch is not "we tell you which houses flooded." It is: *"we tell
you which parts of your book were hit hardest, within hours, from free public
data — and we can show you exactly how accurate that is, which nobody else
will."*
