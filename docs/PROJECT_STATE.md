# Altis — Project State

**Purpose of this file:** everything a new session needs to resume work without
re-deriving it. Read this first. Written 2026-08-10, updated same day with the
ground-truth sourcing task below.

Companion documents:
- `docs/DETECTION_LIMITS.md` — the measured record of what the detector can and
  cannot see, with the experiments behind each claim.
- `pipeline/config.py` — every tuning decision, each with the evidence for it
  written above the values. The `SUBPIXEL` and `DOUBLE_BOUNCE` blocks in
  particular record failed phases in full.

---

## 0. IF THIS IS A FRESH SANDBOX SET UP TO FIX THE NETWORK PROBLEM

Skip straight to **§7 item 0** — that is the task this sandbox exists for. The
prior sandbox's egress proxy blocked essentially every external content domain
(FEMA, USGS, ArcGIS, HydroShare, ScienceBase all refused WebFetch), which
blocked verifying and downloading real per-property flood ground truth. If you
have normal outbound internet access, go execute that section now; the rest of
this file is background for after that task lands.

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

**What this means in plain terms.** Altis produces a *regionally* meaningful
flood-severity signal: it can tell you which zip codes in a portfolio were hit
harder, and it correlates with what adjusters actually recorded and paid. It
**cannot** currently make a defensible per-house flood/no-flood call, and the
present ground truth could not prove it either way if it could.

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

**0. Source real per-property/per-point flood ground truth. Do this FIRST,
before writing any new detector code.** This is the task the network-capable
sandbox exists for. A prior session's egress proxy blocked every relevant
domain, so these are unverified leads from search-result snippets, not
confirmed live data — the first job is verifying and downloading, not
building.

Why this matters more than anything else in this file: §2 explains that every
accuracy number so far is capped by 14 independent zip codes. Four phases
(4a, 4d, 4e) were built, measured, and could not be validated — not
necessarily because they don't work, but because the ground truth has almost
no statistical power to tell a good detector from a bad one. This task is
what breaks that ceiling.

Leads, in priority order:

  a. **USGS High Water Marks, Hurricane Harvey.** ~2,123 GPS-tagged points
     with surveyed water height above land surface, 22 TX counties + 3 LA
     parishes. Public, free, no agreement needed. Start at the USGS Flood
     Event Viewer (`stn.wim.usgs.gov`, event "HarveyAug2017") and the
     ScienceBase data release (search "Data Used to Characterize Peak
     Streamflows and Flood Inundation... Hurricane Harvey ScienceBase" if the
     direct link has moved). This is POINT-LEVEL MEASURED DEPTH, not a binary
     label — better than what we have, because it validates `depth_ft`
     directly instead of a flooded/not-flooded call. Filter to points falling
     inside the BRAZOS and HARVEY bboxes in `pipeline/config.py`. Even a few
     hundred points landing inside our study areas would be a real upgrade
     over 14 zips.
     Action: download, check how many points fall inside our two bboxes,
     write a `validation/hwm_check.py` that compares our `depth_ft` output at
     each HWM point's coordinates against the surveyed value — direct
     regression, no zip aggregation, no label threshold to argue about.

  b. **FEMA "Building Damage for Harvey."** Search-result snippets describe a
     FEMA ArcGIS Hub dataset with **per-building damage classification
     (1=undamaged through 5=destroyed) covering 68,000+ structures** in
     Harris County. If this is still live and has coordinates per record,
     THIS IS THE PER-PROPERTY GROUND TRUTH THE PROJECT HAS BEEN MISSING —
     stop and prioritize it over everything else in this list if it checks
     out. Was at `gis-fema.hub.arcgis.com/datasets/building-damage-for-harvey`
     as of this writing; FEMA links rot, so search "FEMA Building Damage
     Harvey ArcGIS" if that 404s, and also check
     `respond-harvey-geoplatform.opendata.arcgis.com` (HIFLD's Harvey hub,
     130+ datasets) and `data.femadata.com/NationalDisasters/HurricaneHarvey/
     Data/DamageAssessments/`.
     Action: confirm it's live, confirm it has coordinates (not just county/
     zip aggregates), download it, check overlap with our BRAZOS/HARVEY
     bboxes, and if it holds up, this becomes the primary validation dataset
     — replacing zip-level NFIP correlation as the headline number.

  c. **FEMA flood depth grid.** A ~39GB modeled flood-depth raster covering
     the Harvey-affected area, referenced via HydroShare
     ("FEMA - Harvey Flood Depths Grid"). If real, this validates our depth
     map against a continuous surface — no sparse-point or zip-aggregation
     problem at all. Lower priority than (a) and (b) because of the size and
     because it's a MODELED product (FEMA's own hydraulic model), not a direct
     measurement — useful as a second opinion, not as strong as (a)'s surveyed
     points or (b)'s inspected buildings.

  d. **Non-redacted NFIP claims via FEMA data-sharing agreement.** The public
     OpenFEMA claims are zip-redacted by the Privacy Act; the full address-
     level version exists and FEMA does grant access through an Information
     Sharing Access Agreement (ISAA). Contact `OpenFEMA@fema.dhs.gov`. This is
     the slowest path (FEMA's timeline, needs a formal request from the
     company, not something a coding session can complete) but the highest
     ceiling long-term, since it's literally the same dataset already in use,
     just at full precision. Start this conversation in parallel with (a)-(c)
     rather than waiting on them.

  e. **Adjuster feedback loop and FNOL photo upload** (the two items originally
     listed here — kept as fallback/complementary, see item 1 below). These
     require the product to be live and generating real claims first, so
     they're slower to bear fruit than (a)-(d), which are ALREADY-COLLECTED
     historical data sitting in a government archive right now.

If (a) or (b) pan out, the very next step is re-running `dualpol_ablation.py`
and a new point-level equivalent of `double_bounce_probe.py` against the new
ground truth — both were shelved as "not proven," not "disproven," and this
is what actually settles it.

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

## 8b. Sandbox requirements for §7 item 0 (the ground-truth sourcing task)

The prior session's sandbox blocked outbound access to essentially every
content domain outside a small allowlist (search-engine queries worked;
fetching the actual result pages did not — FEMA, USGS, ArcGIS, HydroShare,
ScienceBase all returned an egress-blocked error). Whoever sets up the new
sandbox should confirm, before starting work:

- **General outbound HTTPS is allowed**, not just an allowlist of a few
  domains — the sourcing task needs to reach `.gov` sites (`usgs.gov`,
  `femadata.com`, `fema.gov`), `.arcgis.com` / ArcGIS Hub, `hydroshare.org`,
  `sciencebase.gov`, and whatever domains the search in §7-0 turns up next.
  If the environment only supports an allowlist, add those domains to it
  up front rather than discovering the block mid-task.
- **The repo and its git remote work as normal** — this task should still
  `git clone`/`git fetch` `Altis-2026/altis-mvp` on branch
  `claude/altis-flood-intelligence-1qzgvq` and commit/push there, same as
  every other session. Nothing about the ground-truth sourcing changes the
  git workflow.
- **Large file handling**: item (c) above is ~39GB. Confirm the sandbox has
  disk space for that, or plan to stream/subset it (e.g., fetch only the
  raster tiles overlapping the BRAZOS/HARVEY bboxes) rather than downloading
  the whole thing — check size and options before pulling it in full.
- No new GEE, GCP, or paid-API credentials are needed for this task — it's
  pure public-data download and comparison against outputs the pipeline
  already produces.

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

pytest tests/ -q      # 274 passing, 1 skipped
```
