# Altis — Project State

**Purpose of this file:** everything a new session needs to resume work without
re-deriving it. Read this first. Written 2026-08-10; updated 2026-08-16 when the
ground-truth sourcing task in §7 item 0 was executed and its result reordered
the priorities. **Start at §0.**

Companion documents:
- `docs/DETECTION_LIMITS.md` — the measured record of what the detector can and
  cannot see, with the experiments behind each claim.
- `pipeline/config.py` — every tuning decision, each with the evidence for it
  written above the values. The `SUBPIXEL` and `DOUBLE_BOUNCE` blocks in
  particular record failed phases in full.

---

## 0. THE NETWORK TASK IS DONE — READ THIS BEFORE §2 AND §3

§7 item 0 (source real per-property ground truth) **has been executed.** A
sandbox with working outbound access reached USGS, FEMA and ArcGIS, and the
result changes what this project can honestly claim. Read §2b and
`docs/DETECTION_LIMITS.md` §10 before quoting any accuracy number from §3.

In one line: **point-level ground truth now exists, and it says per-property
recall is near zero — 0 of 18 surveyed flood sites at Brazos, 95% CI
[0%, 18.5%].** The ZIP-level ceiling described in §2 is broken; the news it
delivered is bad, and it is the most useful measurement in the project.

Lead status, so nobody re-runs a dead search:

- **(a) USGS high water marks — LANDED.** 2,364 surveyed points for Harvey,
  cached at `outputs/usgs_hwm_event180.json`, wired into
  `validation/hwm_check.py`. This is now the project's primary validation.
- **(b) FEMA per-building damage — DEAD.** Every "FEMA Building Damage
  Assessments Harvey" service is retired; the ArcGIS item records survive but
  all endpoints 404 (`gis.fema.gov/REST/...`, `services1.arcgis.com/...`).
  It was a live 2017 operational service, not an archived dataset. Do not
  spend another session searching for it.
- **(c) FEMA/HARC flood depth grid — LIVE BUT NOT INDEPENDENT.**
  `FF_Harvey_Flood_Inundation_Depth` is served as an ArcGIS ImageServer (no
  39 GB download needed). But its own metadata says it was interpolated **from
  USGS HWM data and stream gage heights** — the same marks (a) already gives
  us, smoothed into a surface. It is not a second opinion, and validating
  against it would largely re-measure (a) with extra modelling error.
- **(d) Non-redacted NFIP via FEMA ISAA — RESEARCHED, and the earlier plan was
  wrong.** ISAAs are not processed by OpenFEMA; they run through the Regional
  Flood Insurance Liaison. Eligibility is NFIP-participating communities and
  their third-party contractors, for floodplain management / CRS / mitigation
  purposes — **Altis cannot apply as a vendor on its own behalf**, and FEMA
  states a policyholder address is Privacy Act PII. The realistic fastest route
  to address-level truth is a **design-partner carrier's own claims file**,
  which also uniquely supplies the insured-but-did-not-file negative class.
  Full write-up and a draft letter: `docs/FEMA_DATA_REQUEST.md`.

---

## 1. What Altis is

Satellite flood intelligence for P&C insurance — carriers, MGAs, and CAT
adjusting firms in the mid-market. A carrier uploads a portfolio; we return,
per property: was it flooded, how deep, how deep relative to its first floor,
for how long, a claim-severity range, and a triage recommendation
(Dispatch / Remote-Approve / Remote-Deny / Review).

The commercial thesis is not "we out-resolve ICEYE." It is: **most of the
actionable answer, from free public data, fast enough for CAT triage, with the
accuracy arithmetic shown rather than asserted.**

**Stack:** FastAPI backend (`backend/`), React/Vite frontend (`frontend/`),
Mapbox globe, Google Earth Engine for all imagery, OpenRouter (Claude Haiku)
for adjuster notes. Vercel (frontend) + Railway (backend, `DATA_DIR=/data`).

**Repo:** `Altis-2026/altis-mvp`. Working branch:
`claude/altis-flood-intelligence-1qzgvq`.

---

## 2. THE SINGLE MOST IMPORTANT FACT

**Ground truth is 14 independent zip codes. Not 4,000 properties.**

NFIP claims are published at zip resolution (`censusTract` is empty in v3;
lat/long is redacted to ~11 km). Every property in a zip therefore carries the
same label. The Brazos validation set is 3,980 rows carrying **14 independent
bits of information**. Everything beyond that is pseudo-replication.

Consequences, all of which have already bitten:

- Property-level AUC is essentially undefined. Within a zip all labels are
  identical, so AUC is decided entirely by how ~4 test zips happen to rank.
- A single train/test split is nearly meaningless. Across 267 grouped splits
  the Brier score is **0.2336 ± 0.1734** (p05 0.097, p95 0.627). Any single
  number quoted from one split — including the 0.1714 that was briefly a
  headline — is a draw from that distribution.
- A learned model will memorise zips rather than learn physics, and did
  (Phase 4d).
- **Three separate detector improvements have now been tested against these
  labels and none could be validated, including one that certainly works
  mechanically.** The labels cannot distinguish a good detector from a bad one.

**Any new work must confront this first.** Adding detector features and
validating them against zip labels is a treadmill; four phases have run on it.
The highest-value work in the project is now getting per-property ground truth
(§7, item 1).

---

## 2b. THE CEILING IS BROKEN — and the view from the other side is bad

§2 is still true about NFIP claims, but it is no longer the whole ground-truth
story. **USGS Short-Term Network high water marks** give 2,364 GPS-tagged
points for Harvey where a field crew measured, in feet, how far the water rose
above the ground. 1,171 carry a usable height. They are point-level, they are a
measured depth rather than a binary label, and they are independent of us.

`validation/hwm_check.py` samples the real detector at each mark and regresses
detected depth against surveyed depth. Full write-up and every caveat:
`docs/DETECTION_LIMITS.md` §10.

**The measurement, in the study area chosen because it is SAR's best case:**

| | Brazos (open riverine) | Harvey (dense urban) |
|---|---|---|
| Surveyed marks / independent sites | 28 / 18 | 63 / 48 |
| Site recall @ 10–30 m radius | **0 of 18** | **0 of 48** |
| Site recall @ 50 m (headline) | **0 of 18**, 95% CI [0%, 18.5%] | 3 of 48, 95% CI [1.3%, 17.2%] |
| Depth bias | **−2.64 ft** | −2.67 ft |
| Detected vs surveyed depth correlation | undefined (no detections) | **r = −0.072, p = 0.58** |

Bias worsens with depth: −0.77 ft at surveyed 0–1 ft, **−5.64 ft at 4 ft and
deeper.** The deeper the real flood, the more of it we miss.

**This is not a bug, and the obvious explanations were checked and eliminated:**
sampling returns real varied `hand_ft` (1.09–17.34) and `rel_elev_ft` at all 28
Brazos marks; 20 of 28 are not near permanent water and still read exactly
zero; the result is identical at every radius from 10 m to 100 m. It is simply
consistent with the base rate — the full Brazos pipeline detects flooding at
**22 of 4,000 properties (0.55%)**, so zero at 28 points is what that detector
produces.

**Three things follow, and they should govern the next session's priorities:**

1. **The binding constraint is base SAR recall**, not the votes layered on it.
   Phases 4a/4d/4e were tuned on top of a detector firing at 0.55%; there was
   almost nothing for them to improve.
2. **`hwm_check.py` is now the gate**, not the zip correlation. A change that
   moves site recall off zero at Brazos is real evidence. A change that
   improves a correlation built on 14 bits is not.
3. **The "roughly 20% recall" figure in DETECTION_LIMITS §7 should not be
   quoted again.** It was an extrapolation from zip claim counts, honestly
   labelled a lower bound at the time; the direct measurement puts the 95%
   upper bound at 18.5% with a point estimate of zero.

**What HWMs still cannot do:** every mark is a place that flooded, so the
dataset defines no negative class and **cannot measure precision or any
false-positive rate.** A detector returning "10 ft everywhere" would score
perfect recall against it. Precision still needs the §7 item 1 feedback loop.

---

## 3. Current honest accuracy

Measured at Brazos (Fort Bend County TX, Harvey, 4,000 properties, 15 zips,
3,135 real NFIP claims). This is the best-validated area.

| Measure | Value | Reading |
|---|---|---|
| Zip-level mean detected depth vs mean claimed depth | **+0.366** | Real, moderate. The defensible headline. |
| Zip-level mean paid claim vs mean detected depth | **+0.537** | Real, moderate. |
| Property-level calibrated Brier (267 splits) | **0.2336 ± 0.1734** | Enormous spread; unstable. |
| Brier skill score vs constant predictor | **−0.0039 ± 0.0096** | Does **not** beat predicting the base rate. |
| Property-level AUC, best detector | **~0.50** | No property-level discrimination. |
| **Point-level site recall vs surveyed USGS depths** | **0 of 18 sites, 95% CI [0%, 18.5%]** | **Directly measured (§2b). Not zip-aggregated.** |
| **Point-level depth bias vs surveyed USGS depths** | **−2.64 ft** (−5.64 ft at ≥4 ft) | **Directly measured (§2b).** |

**What this means in plain terms.** Altis produces a *regionally* meaningful
flood-severity signal: it can tell you which zip codes in a portfolio were hit
harder, and it correlates with what adjusters actually recorded and paid. It
**cannot** currently make a defensible per-house flood/no-flood call — and as
of §2b that is no longer an inference from weak labels, it is a direct
measurement against surveyed depths at 66 independent flood sites.

**Harvey (Addicks/Barker, urban Houston) is worse than Brazos** — near-zero
detection, and 100% of its 44 detections were optically contradicted. Dense
urban terrain is the weakest case.

---

## 4. What is built and working

**Detection core** (`pipeline/flood_detect.py`, shared by batch and live paths):
- Sentinel-1 VV, Otsu threshold with open-water range guard, speckle filter
- Multi-temporal baseline: 12-month per-pixel mean/stdDev, z-score change
  detection (replaces single before/after differencing)
- HAND (MERIT Hydro) terrain plausibility, replacing a neighbourhood-minimum
  heuristic that abstained constantly on flat coastal terrain
- Cross-orbit stacking (ASC + DESC, independent thresholds and baselines,
  union of masks) to shrink the revisit gap
- Dual-polarisation VV+VH corroboration — `min(evidence_vv, evidence_vh)` so a
  non-corroborating channel caps the score
- Sentinel-2 MNDWI optical cross-check when a cloud-free scene exists
- Inundation duration via post-window slices; CHIRPS rainfall; NDVI delta

**Structure and severity** (`pipeline/structures.py`, `pipeline/severity.py`):
- USACE NSI match (cKDTree, 60 m max) → foundation height, type, stories,
  occupancy, structure/contents value, footprint area
- **Depth above first floor**, not just above ground — the quantity every
  published depth-damage curve actually takes
- Multi-curve HAZUS-style severity by occupancy/stories/basement, duration
  multiplier, structure/contents split, uncertainty intervals

**Validation** (`validation/`):
- Real NFIP Redacted Claims v3 as truth, with the `waterDepth` feet/inches
  ambiguity handled explicitly and reported
- Zip-grouped holdout (train and test zips disjoint)
- `calibration.py::paired_candidate_comparison` — candidates scored on
  IDENTICAL splits so split noise cancels in the difference
- `dualpol_ablation.py`, `fit_ensemble.py`, `double_bounce_probe.py`

**Tests: 274 passing, 1 skipped.**

---

## 5. What was tried and FAILED — do not repeat these

Each was well-motivated, built completely, measured, and shelved. The code and
tests are retained; the numbers are in `config.py` above the relevant block.

**Phase 4a — Sub-pixel water fraction** (`SUBPIXEL`, disabled).
Linear unmixing in the power domain. Signal density rose 65× (22 → 1,441
properties). Accuracy: **AUC 0.4862, p=0.92** — slightly *more* common on
dry-truth properties. Cause: ~50 in of basin-wide rain saturated the soil, and
wet ground darkens C-band like shallow water. Single-pol amplitude at 30 m
cannot separate them.

**Phase 4b — Dual-polarisation** (`DUALPOL`, enabled).
The direct answer to 4a's confound. Standalone it clears chance: AUC 0.5078,
p=0.017, recall 4.1% vs the binary mask's 1.0% at comparable precision. But
wired into the coverage term via `max()` it made calibration slightly worse
(Brier +0.00065, ~7 SE) while improving ranking (AUC +0.0018, ~2 SE). Left
enabled; the ranking gain is real and the calibration cost is small.

**Phase 4d — Learned ensemble** (`pipeline/ensemble_model.py`, not wired in).
L2 logistic regression over 14 per-property features, zip-grouped CV.
**Lost on every metric: AUC 0.4107 vs 0.5012 hand-tuned.** Regularisation did
not help from L2=1 to L2=10,000. The fitted weights gave `optical_water_pct`
the largest coefficient with a **negative** sign — more visible water, less
likely flooded. That is zip memorisation, and it is what led to the §2 finding.

**Phase 4e — Urban double-bounce** (`DOUBLE_BOUNCE`, disabled).
Detect urban flooding by BRIGHTENING (water against a wall forms a dihedral
corner reflector), since the open-water detector only tests for darkening and
was therefore blind in exactly the urban zips holding the claims.
Mechanically it worked — zip 77450 went 0.000% → 73.1% detection, recall
0.8% → 14.5%. **Accuracy: AUC 0.4975 (p=0.73), precision 19.9% BELOW the 20.9%
base rate, zip Spearman +0.018 (p=0.95).** The encouraging group means (23.9%
flooded vs 14.6% dry) are one zip; drop it and they invert. A threshold sweep
found p=0.035 at one cut point, but eight were tested (adjusted p ≈ 0.28) and
significance was not monotone. It correlates with `urban` (+0.197) far more
than with flooding (+0.018).

**The pattern across 4a, 4d and 4e:** each produced abundant signal and no
accuracy, and each was measured against labels that could not have detected
accuracy if it were there. Density is not accuracy — and with 14 zips, the
test itself has almost no power.

---

## 6. Non-negotiable working discipline

Carried forward from the start of the project and reinforced repeatedly:

1. **Never fabricate or approximate a validation number.** If real data is not
   obtainable, say so rather than shipping a plausible-looking placeholder.
2. **Report the failures.** Every disabled feature above has its numbers
   recorded in `config.py`. This is the project's main credibility asset.
3. **Report the sweep, not just its best point.** The 26.4% precision figure in
   4e is meaningless without the eight thresholds that produced it.
4. **Abstain explicitly.** A 0 must never conflate "measured, none" with "not
   measured" — hence `dpol_available`, `db_available`, `optical_available`,
   `hand_ft = -1`.
5. **Never let a feature be validated on the thing it was selected for.** An
   earlier flood-targeted property sample (`near_flood_boost`) was removed for
   exactly this; the bbox was widened instead.
6. **Commit tooling before running it.** This session lost four in-flight runs
   to container resets; anything living only in scratchpad dies with them.

**Sourcing caveat:** the variance-explained figures (28%/14%) and the
duration-damage multiplier in the original roadmap came from search-result
summaries. `nature.com`, `sciencedirect.com` and `ncbi.nlm.nih.gov` are blocked
from this sandbox, so the papers were never read directly. **Verify before
quoting those numbers to an investor, actuary, or in marketing copy.**

**Scope decisions already made:** Lismore stays out of FEMA-based validation
(US-only data). Cloud Run/Cloud SQL migration deferred until a customer or
security review demands it. SOC 2 clock has not started — still open.

**GCP:** project `altis-mvp`, service account
`ee-pipeline@altis-mvp.iam.gserviceaccount.com`. Ignore any reference to
`kairos-altis` — unrelated. If a GEE key was ever pasted in plaintext into a
chat, treat it as compromised and confirm rotation before relying on it.

---

## 7. What to do next, in priority order

**0. Source real per-property/per-point flood ground truth. — DONE. See §0
and §2b.** Kept here so the priority ordering still reads, but do not re-run
this search: (a) USGS high water marks landed and are wired into
`validation/hwm_check.py`; (b) FEMA per-building damage assessments are dead,
every endpoint 404s; (c) the FEMA/HARC depth grid is live but is interpolated
from the same USGS marks, so it is not independent; (d) the FEMA ISAA route to
non-redacted NFIP claims is unstarted and still the long-term ceiling.

**The result reordered everything below it.** Point-level recall is 0 of 18
surveyed flood sites at Brazos (95% CI [0%, 18.5%]) and depth bias is −2.64 ft,
worsening to −5.64 ft at surveyed depths of 4 ft and above. The binding
constraint is base SAR detection recall, not the votes layered on top of it.

**0b. ROOT CAUSE FOUND: the detector tests for the wrong sign.** Full evidence
in `docs/DETECTION_LIMITS.md` §11.

The median-composite hypothesis this file previously listed first was TESTED
AND FALSIFIED. `validation/per_pass_probe.py` scores every individual pass and
unions them: it flags **8.8× more of the Brazos bbox (3.06% vs 0.35%) and finds
zero additional marks.** Not one pass sees these floods. Do not re-run this.

`validation/gate_probe.py` then found the real cause. The terrain gates are
innocent — 28/28 Brazos and 63/63 Harvey marks pass both slope and
permanent-water, on ground averaging under 2° — which also settles a worry
about §10: the recall figure is NOT an artifact of USGS surveying at bridges
and channel banks. The radiometry rejects them, because:

    mean VV z-score at surveyed flood points:  Brazos +0.39σ   Harvey +2.83σ

The return at places that demonstrably flooded is **brighter** than that
pixel's own 12-month baseline. An open-water detector tests for darkening.
Water standing among buildings, fences and vegetation forms corner reflectors
and returns MORE energy — which is the physics §1–§3 of DETECTION_LIMITS
already described for Meyerland, now measured at surveyed points.

**Re-tested against the marks, the shelved phases rank completely differently
than they did against zip labels** (`validation/phase4_probe.py`, reporting
recall alongside the share of the bbox each signal fires on, since HWMs have no
negative class):

  - **Double-bounce (Phase 4e), currently DISABLED, is the best SAR signal in
    dense urban by a wide margin: 28/63 marks and 20/48 sites at Harvey against
    3/63 for the detector that ships** — nine times the recall for six times the
    bbox coverage. It is inert at rural Brazos (0.02% of bbox) by design, since
    its urban-built gate excludes floodplain. This is the answer to the
    "confident calls in dense city blocks" problem, and it was switched off on
    a measurement that could not have detected it.
  - **Sub-pixel (Phase 4a), also disabled, has the best lift at Brazos** (6/28
    marks, 5.71% of bbox, lift 3.75) where the shipped detector scores zero.
  - **Sentinel-2 optical has the highest lift on BOTH events** (6.50 Brazos,
    4.59 Harvey) while being used only as a veto. It is the one signal that
    cannot share SAR's blind spot.

**Do not enable any of these on that evidence alone.** Every mark is a place
that flooded, so none of it is a precision measurement. That is what 0c fixed.

**0c. A REAL NEGATIVE CLASS NOW EXISTS — this was the missing half.**
USGS SIR 2018-5070 (doi:10.5066/F7VH5N3N) publishes, per mapped Harvey reach,
both the flood inundation extent AND the **mapped area boundary** — the domain
within which USGS delineated it. Inside the boundary and outside the extent is
not unlabelled ground; it is mapped-dry ground.

Clipped to the Brazos bbox that is **194.4 km² flooded against 194.7 km² dry**,
which labels **68,624 real NSI structures: 25,062 flooded, 43,562 dry.** Per
property, both classes, inside the study area we already analyse.

Committed as `outputs/usgs_brazos_extent.geojson`; scored by
`validation/extent_check.py`, which reports precision, recall, specificity and
AUC — and drops structures OUTSIDE the mapped boundary rather than calling them
dry, because absence of mapping is not evidence of dryness.

Caveat to carry: the extent is interpolated from water-surface elevations at
high water marks, not directly observed, so it is modelled truth and least
certain near the flood edge. `--edge-buffer` exists to re-report without that
band. The adjuster feedback loop and FNOL photos in item 1 remain the route to
truth that is observed rather than modelled.


**1. Per-property ground truth via product usage. This unblocks everything
else once the product has real users; item 0 is the faster path today.**
Every accuracy number in this project is capped by the 14-zip ceiling, and four
phases have now died against it. Two viable routes, neither requiring new data
spend:
   - **Adjuster feedback loop.** `validation/accuracy_check.py` already looks
     for an `adjuster_feedback` table and reports "no such table" today. Every
     human confirmation or correction is a per-property label — better than
     NFIP claims because it is not zip-aggregated. Building the ingestion path
     is cheap and is the single highest-leverage task in the project.
   - **FNOL photo upload + vision scoring.** Policyholders attach 2–3 phone
     photos when filing; the existing OpenRouter integration scores waterline
     height, flooring, visible damage. Gives interior signal no satellite can,
     and cross-checks the satellite depth. Disagreement between the two is
     exactly the case that should route to a human.

**2. Re-validate the shelved phases once real labels exist.** 4a, 4d and 4e are
all currently "not proven," not "disproven" — the test lacked power. Dual-pol
in particular deserves a second look; it is the only one that cleared chance.

**3. Flood-timing indicator.** Report whether the SAR pass likely caught the
crest or missed it, from data already pulled (CHIRPS daily rain + scene dates).
Cheap, and it converts a silent failure mode into a disclosed caveat. This is
the honest answer to "floods move at different speeds."

**4. A second non-Gulf validation site, and one non-CONUS.** Two Texas sites
from the same storm are not evidence the system works anywhere. The non-CONUS
run matters specifically because it exercises the HAND/global-DEM path with no
NSI — which is what "works anywhere in the world" actually depends on.

**5. Fit depth-damage curves to our own claims.** The severity curves are
currently published HAZUS-style shapes, not fitted to this book. We have real
paid amounts. This is the difference between "national tables say" and "claims
like this one actually cost."

**6. Deferred / paid tier.** Commercial SAR (ICEYE, Capella — ~1 m, sub-daily
revisit) and post-event aerial (Nearmap, Vexcel) are the honest answer for
time-critical sub-parcel work. Not to be built; a future purchasing decision.

---

## 8. Known infrastructure gotchas

- **`kill <pid>` on a backgrounded shell does not kill its python child.** An
  orphan once kept running, overwrote an output CSV, and wrote its traceback
  into a log another process had truncated — producing a traceback whose line
  numbers did not match any source. Use `setsid`, verify with
  `ps -p <pid> -o cmd=`, and give each run its own log file.
- **The container resets.** It happened four times in one session, each time
  reverting the checkout to the last-pushed commit and wiping the scratchpad.
  Push early; never leave a result only on local disk.
- **The NSI API truncates large responses** (`IncompleteRead`) roughly one
  attempt in three at county scale. `fetch_nsi_structures` now retries 3× with
  backoff. Without it the pipeline silently reports "0 matched", falls back to
  geocoded points, and writes a CSV that looks fine but has lost every Phase 2
  column.
- **A full `03_flood_pipeline.py` run is ~25 min** for 4,000 properties, mostly
  NSI fetch and sampling. Targeted probes (`double_bounce_probe.py`) answer
  detector questions in ~3 min and should be preferred for measurement.
- **Otsu must be precomputed** (`precompute_thresholds=True`) for batch runs or
  the whole-bbox histogram is recomputed on every sampling batch (30+ min stall).
- **OpenRouter failures fail identically for every batch.** `LLM_GIVE_UP_AFTER
  = 3` exists because 200 doomed calls once added ~40 min to a finished run.

---

## 8b. Sandbox notes (the ground-truth sourcing task is complete)

The network problem this section used to describe is **resolved** — a sandbox
with normal outbound HTTPS reached usgs.gov, arcgis.com and fema.gov without an
allowlist, and §7 item 0 was executed there. Kept for the practical notes:

- **No large download was needed after all.** The USGS marks are a single
  1.6 MB JSON, now cached in-repo at `outputs/usgs_hwm_event180.json`, so
  `hwm_check.py` runs offline. The ~39 GB FEMA depth grid was never worth
  fetching: it is served as an ArcGIS ImageServer (queryable without
  downloading) and is interpolated from these same marks anyway.
- **No new credentials were needed** — public data plus the existing GEE
  service account, which this sandbox reads from
  `GEE_SERVICE_ACCOUNT_KEY_JSON`.
- **`ee` can fail to import on a fresh container** with a `pyo3_runtime.
  PanicException` from `cryptography`'s Rust bindings. `pip install
  --force-reinstall cryptography` clears it.
- The git workflow is unchanged: clone `Altis-2026/altis-mvp`, work on the
  designated branch, commit and push.

---

## 9. How to reproduce the current numbers

```bash
# Full pipeline for an event (~25 min)
cd pipeline && python 03_flood_pipeline.py --event brazos
python 04_triage_notes.py --event brazos

# Validation against real NFIP claims
python validation/accuracy_check.py --event brazos --no-policies

# The paired-holdout comparison that resolved Phase 4b/4c
python validation/dualpol_ablation.py brazos --repeats 300

# The learned-ensemble comparison (Phase 4d)
python validation/fit_ensemble.py brazos --repeats 300

# The double-bounce measurement (Phase 4e, ~3 min)
python validation/double_bounce_probe.py brazos

# POINT-LEVEL validation against surveyed USGS high water marks (~6 min each).
# Measures recall and depth error. Cannot measure precision — no negative class.
python validation/hwm_check.py brazos --sweep
python validation/hwm_check.py harvey --sweep

# PER-PROPERTY precision AND recall against the USGS mapped flood extent.
# This is the gate a detector change now has to pass: 68,624 labelled
# structures, both classes. Runs offline from the committed GeoJSON.
python validation/extent_check.py --max-structures 3000
python validation/extent_check.py --edge-buffer 100    # drop the uncertain band

# Diagnostics behind DETECTION_LIMITS §11 (a few minutes each)
python validation/per_pass_probe.py brazos   # median vs per-pass — FALSIFIED
python validation/gate_probe.py brazos       # which gate closes, and why
python validation/phase4_probe.py harvey     # re-rank the shelved detectors

pytest tests/ -q      # 274 passing, 1 skipped
```
