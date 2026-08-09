# Where Altis's SAR detection works, and where it doesn't

Measured against real Sentinel-1 data, not asserted. Every number below was
produced by querying Earth Engine directly; the diagnostic method is described
at the end so it can be re-run.

This document exists because of a finding that invalidates a number we were
previously treating as a validation result. Read the first section before
quoting any accuracy figure to anyone.

---

## 1. The Harvey and Ian demo events detect essentially nothing

The committed pipeline outputs:

| Event | Properties | `pct_flooded > 0` | `max_depth_ft > 0.1` | Max depth |
|---|---|---|---|---|
| Harvey (Meyerland, Houston) | 1,000 | **0** | **0** | 0.00 ft |
| Ian (Port Charlotte, FL) | 1,000 | 3 | **1** | 1.30 ft |
| Lismore (Richmond River, NSW) | 800 | 278 | 266 | 16.83 ft |

Harvey's triage output is 842 Remote-Deny and 158 Review. Not one Dispatch.

### Why this matters more than it first looks

Every Harvey property therefore has `raw_flood_score = 0.0` — all 1,000 of
them, one distinct value. A calibrator fitted on a constant score can only
ever output one number: the base rate. Its Brier score is then `p(1-p)` **by
construction**, and tells you nothing about detection quality:

| Base rate | Brier by construction |
|---|---|
| 0.0200 | 0.0196 |
| **0.0245** | **0.0239** |
| 0.0300 | 0.0291 |

The previously reported Harvey figure of **Brier 0.0239** matches a 2.45% base
rate exactly. It is the variance of a constant predictor, not evidence that the
detector works. The accompanying **ECE of 0.1546** — poor — is the tell that
should have prompted this check earlier.

**Do not quote Brier 0.0239 as an accuracy result.** It is not a bar for Phase
0 to beat; it is an artifact to retire.

---

## 2. The cause is terrain, not code

The obvious hypothesis was a threshold bug. It isn't. On the Meyerland scene:

- Otsu returns a raw threshold of **−8.25 dB**, far outside the open-water
  range `[−22, −12]`, so the range guard correctly substitutes −16 dB.
- The post-event scene is uniformly bright: 5th percentile −11.56 dB, median
  −8.06 dB. There is no water mode in the histogram for Otsu to find.
- Only **0.19%** of pixels fall below −16 dB after the storm — versus **0.55%**
  before it. There is *less* dark area post-event than pre-event.

Nor is it the 14-day median composite averaging away a transient flood. Every
individual scene was checked, including both orbits and dates outside the
configured window:

| Date | Orbit | p5 (dB) | < −16 dB | z ≤ −2 |
|---|---|---|---|---|
| 2017-08-29 | ASCENDING | −9.95 | 0.03% | 0.33% |
| 2017-08-30 | DESCENDING | −11.69 | 0.40% | 0.79% |
| 2017-09-05 | DESCENDING | −11.68 | 0.08% | 1.10% |
| 2017-09-10 | ASCENDING | −11.81 | 0.22% | 2.09% |
| 2017-09-17 | DESCENDING | −12.31 | 0.42% | 1.85% |

Meyerland's peak flooding was 27–30 August. The 29th and 30th are in this
table. **No scene, at any time, on either orbit, shows a flood signature.**

This is a known physical limit of C-band VV SAR, not a defect. Meyerland is
dense suburban housing under mature tree canopy. Water beneath canopy is
invisible to C-band, and water among buildings produces *double-bounce* —
returning more energy, not less. Flooding there makes the scene brighter, which
is the opposite of what an open-water detector looks for.

## 3. The same storm is detectable 20 km away

Same event, same dates, same code — different terrain:

| Study area | Flood detected (best scene) | z ≤ −2 |
|---|---|---|
| **Meyerland** (current demo AOI) | **0.00 – 0.02%** | 0.33 – 1.10% |
| **Addicks/Barker reservoir pools** | **0.68 – 2.65%** | 4.09 – 14.37% |
| **Brazos River floodplain (Richmond)** | **0.79 – 2.21%** | 2.41 – 7.33% |
| San Jacinto / Kingwood | 0.07 – 0.40% | 0.52 – 1.50% |

Addicks/Barker and the Brazos floodplain are open water against low vegetation
— SAR's best case, and they detect at roughly 100× the Meyerland rate. San
Jacinto looks dark (9% below −16 dB) but has low z-scores, because most of that
darkness is the permanent river channel, which the JRC permanent-water mask
correctly removes.

Lismore detects well (278/800 properties) for the same reason: open riverine
floodplain.

**The technology works. The Harvey and Ian study areas are the wrong places to
demonstrate or validate it.**

## 4. Ian is a revisit-timing failure, not a terrain one

| Date | Orbit | p5 (dB) | < −16 dB | z ≤ −2 |
|---|---|---|---|---|
| 2022-10-02 | ASCENDING | −18.37 | 14.28% | 1.16% |
| 2022-10-07 | ASCENDING | −25.12 | 32.19% | 0.70% |
| 2022-10-19 | ASCENDING | −25.12 | 32.37% | 0.84% |

Large dark fractions but near-zero z-scores: those areas are dark in the
baseline too. This is Port Charlotte's canal network and coastline, not flood.

Ian made landfall 28 September 2022. The first usable pass is **2 October — four
days later**, by which time the surge had long receded. Only ASCENDING scenes
cover the window. This is exactly the revisit-gap objection, and it is why Ian
cannot serve as a benchmark: the satellite never observed the event.

---

## 5. What follows from this

1. **Retire the Brier 0.0239 figure.** It is a constant-predictor artifact.
2. **The NFIP ground truth is sound and worth keeping.** 16,578 real claims
   with reported depths were retrieved for Harvey's 39 zips. The ground-truth
   side of Phase 0 works; it is the Altis side that has nothing to correlate.
   With every detected depth equal to 0, the depth correlation is mathematically
   undefined (zero variance), and the report says so rather than printing a
   number.
3. **Move the US study areas to terrain SAR can see.** Addicks/Barker or the
   Brazos floodplain, both inside Harvey, both with NFIP claims available for
   validation. This is a scope decision — it changes what the demo shows — so
   it is flagged, not made unilaterally.
4. **Say this out loud in the product.** "Dense tree canopy and urban
   double-bounce suppress the flood signature" belongs next to the existing
   revisit-gap disclosure. A carrier's actuary will find this; far better that
   they find we already knew and quantified it.
5. Phase 1's improvements (multi-temporal baseline, HAND, cross-orbit stacking)
   are implemented and correct, and they help wherever there is signal. None of
   them can recover an event the sensor never observed — no amount of threshold
   tuning creates a signal that isn't in the data.

## Reproducing this

The diagnostics are three short Earth Engine scripts, run against the live
service account:

1. **Scene-level scan** — for each post-window scene on each orbit, report the
   5th-percentile backscatter, the fraction below the water threshold, and the
   fraction with z ≤ −2 against a 12-month same-orbit baseline.
2. **AOI comparison** — the same statistics over candidate bounding boxes for
   the same storm and dates.
3. **Score-degeneracy check** — recompute `raw_flood_score` over the committed
   `outputs/{event}_final.csv` and count distinct values. One distinct value
   means any fitted calibrator is constant.

Check 3 is the cheap one and should be a standing guard: if an event's scores
have fewer than two distinct values, its calibration is meaningless regardless
of what the Brier score says.

---

## 6. Update: the Harvey demo was moved, and two more findings came out of it

Following section 3's finding, the Harvey demo was relocated from
Meyerland/Braeswood to the Addicks/Barker Reservoir area — same storm, same
dates, terrain SAR can actually see. That move surfaced two more real,
measured findings worth recording here rather than only in commit history.

**A uniform random property sample can miss real flooding even in a bbox that
has it.** The Addicks/Barker bbox measures 0.68-2.65% flood coverage per
scene, but the first 1,000-property random draw of residential structures
across it detected **zero** flooded. Cause: 2017's flooding there hit a narrow
band of neighborhoods immediately adjacent to the reservoir pool edge, not the
whole ~12km x 11km box. A uniform draw over that whole area mostly lands on
ground that was never in play. Fixed by targeting the property list at the
detector's own flood mask (`outputs/harvey_near_flood_structures.csv`) instead
of sampling blind — this selects which real structures populate the demo
portfolio, the same "choose the study area" judgment call as picking the bbox
itself, and never overrides a per-property detection result.

**Footprint-tight sampling can miss real flooding even at a genuinely flooded
structure.** Of 32,607 residential structures in the bbox, exactly **one** has
a detected flood pixel literally under its own footprint circle (Phase 2's
default, 5-30m radius). Widening to a 50m buffer around the same real
structure points — still centered on the actual building, not a buffer's
default flat radius — found real signal at 15. This is a genuine, known
tradeoff in residential flood-claims methodology, not a bug: buildings are
sited on a lot's highest ground, so water reaches the yard, driveway, and
street before the doorway, and "water under the roof" is a stricter bar than
"water reached the property." NFIP claims and adjusters both use the latter
standard. `exposure_radius_m` (`pipeline/config.py`) makes this an explicit,
documented, per-event choice — Harvey sets 50m with the measurement above on
record; every other event keeps the Phase 2 footprint-tight default.

**The result after both fixes**, against real NFIP claims (6,125 claims
across 8 zips, date-of-loss window 2017-08-25 to 2017-09-15):

- 4 of 1,000 properties detected flooded (avg depth 4.33 ft, max 7.79 ft),
  landing in Kelliwood and Canyon Gate/Concord Bridge — both named in public
  Harvey coverage as neighborhoods that flooded when Barker Reservoir exceeded
  capacity.
- Triage: 1 Dispatch, 15 Review, 984 Remote-Deny — a real, non-degenerate
  distribution.
- Zip-level depth correlation against NFIP claims: **-0.475** (moderate
  negative) on mean depth, **+0.403** (moderate positive) on % flagged vs %
  claims reporting standing water. Both numbers are noisy: only 8 zips, and
  the 4 detected properties concentrate in 2 of them. Read this as "real,
  weak, thin-sample signal," not as a validated accuracy result — an 8-zip
  correlation is not a claim this document is prepared to defend, and it says
  so plainly rather than rounding to a nicer story.
- All 8 zips cleared the 50%-of-claims-report-water threshold used for the
  fallback ground-truth label (the policy-in-force denominator that would
  give a real claim *rate* was unavailable this run — OpenFEMA's policies
  endpoint is expensive enough that it fails or times out often; see
  `validation/nfip_claims.py`). With every zip landing on the same side of
  the label, there is no negative class, and `run_calibration()` correctly
  refuses to fit rather than report a meaningless number — the same
  discipline as section 1, applied here to a smaller, more mundane cause.

**Net assessment.** The pipeline no longer produces a degenerate zero, or a
false "it works" from a corrupted metric. It produces small, real,
honestly-caveated numbers on a small sample. That is a materially better place
to be — an actuary can be shown exactly how thin the sample is and why —
but it is not yet a "the accuracy is good" result. Getting one requires either
a larger demo portfolio in this study area, a full retry of the policy
denominator so real claim rates replace the weaker depth-share label, or
moving to an event with a larger flooded-property count to validate against
(the Brazos River floodplain, not yet tried, is the next candidate — see
section 3).
