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

---

## 7. Brazos River, 4,000 properties, 15 zips — the first result worth reading

This is the run that section 6 asked for: a second, independent study area
(river-crest flooding rather than reservoir release), a plain uniform random
sample rather than a flood-targeted one, 4,000 properties instead of 1,000,
and 15 zips instead of 8.

**Ground truth:** 3,135 real NFIP claims, date of loss 2017-08-27 to
2017-09-20, across the 15 zips the portfolio touches. Depth-unit split 2,823
feet / 310 inches / 2 invalid.

### Zip-level agreement is real and consistent

| Comparison (by zip, n=15) | Correlation |
|---|---|
| Altis mean depth vs **mean claimed water depth** | **+0.366** |
| Altis mean depth vs **median claimed depth** | **+0.373** |
| Altis mean depth vs **mean paid building claim** | **+0.537** |
| Altis % flagged vs % claims reporting standing water | +0.105 |

All four point the same way, which matters more than any single figure. The
strongest is against **dollars actually paid** — the number a carrier cares
about — and the depth correlations agree with each other, which is what you
would expect from real signal rather than noise.

Compare with section 6's Harvey result, where the two depth metrics disagreed
in sign on 8 zips. That was thin-sample noise; this is not.

### Property-level discrimination is NOT there, and the report says so

The calibration now fits, because the corrected denominator finally produced a
label with two classes (832 flooded-truth of 3,980, versus 4,000 of 4,000
before the fix):

- **Expected calibration error: 0.0134** — genuinely well calibrated. When it
  says 20%, it means 20%.
- **Brier skill score: −0.037** — it does **not** beat a constant predictor.

Both are true at once and neither should be dropped. The model's probabilities
are honest; they just carry almost no discriminating information, because
**only 22 of 3,980 properties (0.6%) have any flood signal at all**. There is
very little for a calibrator to separate.

### The number that actually matters commercially: recall

Detection rate is 16 of 4,000 sampled (0.40%). Scaled to the 170,484
residential structures in the study area, that extrapolates to roughly **680
detected properties against 3,135 filed claims — on the order of 20% recall.**

That estimate is a **lower bound**, and deliberately quoted as one: the zips
extend past the study bbox, so the claim count includes losses outside the
area analysed, which drags the ratio down. The true figure is higher, but not
by an order of magnitude.

**So: in open riverine floodplain — SAR's best case — Altis finds something
like a fifth to a third of the properties that actually filed flood claims.**

That is a real, useful product and it is not "replace the adjuster". It is
"here are several hundred properties we are confident about on day one,
ranked, with depth and a dollar range" — against a book where thousands filed.
The honest positioning is triage acceleration and early reserve-setting, not
exhaustive coverage.

### What would move recall

In rough order of expected effect:

1. **Sub-pixel / partial-water detection.** The binary Otsu mask requires a
   30m pixel to read as water. A suburban lot that is half flooded often does
   not clear that bar. Fractional-water unmixing is the standard answer and is
   not implemented here.
2. **More post-event scenes.** Two scenes on the primary orbit for Harvey.
   Sentinel-1C/D have since restored 6-day revisit, and cross-orbit stacking
   (already built) helps more when there are more passes to stack.
3. **The learned fourth vote (Phase 4).** NFIP claims give millions of
   labelled outcomes; a model over depth, HAND, foundation type and occupancy
   could flag properties the physics-based mask misses.
4. Commercial SAR at 6-24h revisit, for events where Sentinel misses the peak
   entirely.

---

## 8. Two areas, opposite results — and why that is the most useful finding here

Harvey's widened box was validated the same way: 4,000 properties, uniform
random, **55 zips**, against **24,219 real NFIP claims**. Both events, same
code, same storm, same week.

| | **Brazos** (open floodplain) | **Harvey** (west Houston) |
|---|---|---|
| Zips / claims | 15 / 3,135 | 55 / 24,219 |
| Depth vs mean claimed depth | **+0.366** | **−0.011** |
| Depth vs median claimed depth | **+0.373** | **−0.017** |
| Depth vs mean paid claim | **+0.537** | **+0.012** |
| Properties detected | 16 (0.4%) | 44 (1.1%) |
| Urban-flagged properties | 79% | 86% |

**The two areas disagree, and Harvey detected MORE while correlating LESS.**
That combination is the tell, and chasing it produced the sharpest result in
this document.

### 100% of Harvey's detections are contradicted by the optical sensor

| | Detections | Contradicted by Sentinel-2 | Mean optical water at detections |
|---|---|---|---|
| Harvey | 44 | **44 (100%)** | **0.0000** |
| Brazos | 16 | 14 (88%) | 0.0122 |

Every single Harvey detection sits where Sentinel-2 sees no water at all. In
86%-urban terrain that is the signature of SAR artifact — radar shadow and
double-bounce between buildings mimicking the low backscatter of open water —
not of flooding the optical sensor happened to miss. It is the same physics as
section 2, now measured on the detector's own output rather than on raw scenes.

A −0.011 correlation is exactly what a set of false positives should produce.
The zip-level agreement in Brazos and its absence in Harvey are the same fact
seen twice.

### What the system did about it: the part that actually matters

Triage output across all 8,000 properties in both areas:

| | Dispatch | Review | Remote-Deny |
|---|---|---|---|
| Harvey | **0** | 108 | 3,892 |
| Brazos | **1** | 111 | 3,888 |

**Harvey dispatched nobody.** The ensemble's optical cross-check and
disagreement logic caught all 44 false positives and refused to commit to a
single one — without anyone tuning it for this, and before any of the analysis
above was done. That is the multi-sensor design doing exactly the job it exists
to do.

The one Dispatch in 8,000 properties is Brazos BRZ-00447:

- 7.96 ft depth, 87.1% of the sampled area flooded
- Sentinel-2 **confirms** standing water (optical 0.109, versus 0.0000 across
  every Harvey detection)
- Confidence 97%

Deep water, near-total coverage, two independent sensors agreeing. That is the
only property in either study area that clears the bar, and it should be.

### The honest reading

Stated plainly, because it cuts both ways:

1. **Where SAR works, it works, and the numbers hold up.** Open riverine
   floodplain gives +0.37 depth agreement and +0.54 against dollars paid,
   across 15 zips and 3,135 claims.
2. **Where it doesn't, the system knows.** It produced zero dispatches in the
   terrain where its own detections were unreliable. It did not need to be told
   that terrain was hard; the optical vote worked it out.
3. **Coverage is the real limitation, not correctness.** One Dispatch and ~220
   Reviews against 27,000 filed claims across both areas. The system is tuned
   to near-total precision at the cost of recall.

That tuning is a choice, not a law, and it is the main dial to revisit. For a
CAT team, `Review` is a worklist, not a rejection — ~2.7% of each portfolio
routed to human attention is a usable product. But nobody should describe the
current configuration as finding most flooded properties. It finds a few it is
very sure about, and correctly declines everywhere else.

**For the pitch:** lead with riverine and open-terrain events, show the
Brazos numbers, and state the urban-canopy limit before a carrier's engineer
finds it. The zero-dispatch result in Harvey is a feature worth showing
deliberately — a system that declines to guess is worth more to a claims
manager than one that confidently sends adjusters to dry houses.

---

## 9. Sub-pixel water fraction: implemented, measured, and switched off

Section 7 ranked sub-pixel water detection as the **highest-expected-value**
change for recall. It was built, run end to end, and measured. It does not
work, and this section exists so nobody spends that money twice.

### The reasoning going in was sound

Recall was the binding constraint, and the mechanism was blunt: the flood mask
is binary per pixel, so a property's exposure is the mean of an all-or-nothing
mask over ~9 pixels at 30m. If no single pixel clears the open-water
threshold, the property scores **exactly zero** — which is what happened to
3,978 of 4,000 Brazos properties and 3,948 of 4,000 Harvey ones. A calibrator
handed a column that is 99.4% one identical value has nothing to work with,
which is why calibration came out well-calibrated but with negative skill.

Sub-pixel unmixing addresses that directly. Backscatter mixes linearly in
POWER by area fraction, and the Phase 1 baseline already gives a per-pixel dry
endmember, so `f = (σ_dry − σ_obs) / (σ_dry − σ_water)` inverts a genuinely
half-flooded pixel back to ~0.5 instead of discarding it as "not water".

### Mechanically it did exactly what it was supposed to

| | Binary | Sub-pixel |
|---|---|---|
| Properties with nonzero signal | 22 (0.55%) | **1,441 (36%)** |
| Distinct score values | 23 | **602** |

A 65× increase in graded properties. The consistency check also passed: on the
22 properties the strict mask flagged, mean water fraction was 0.517 — the
confident cases do read as strongly wet.

### And it bought nothing

| Metric | Binary | Sub-pixel |
|---|---|---|
| Depth correlation vs claims | +0.366 | **+0.366** |
| Brier | 0.1714 | **0.1712** |
| Brier skill score | −0.0366 | **−0.0354** |

The direct test settles it. As a standalone predictor of whether a property's
zip actually flooded, the water fraction scores:

- **AUC 0.4862** — where 0.5 is *no information whatsoever*
- **Mann-Whitney p = 0.92**
- Nonzero on **36.9%** of dry-truth properties versus **33.5%** of
  flooded-truth ones — marginally *more* common where there was no flood

### Why — the part worth keeping

Harvey dropped on the order of 50 inches of rain across the basin. **Saturated
soil darkens C-band SAR in the same direction, and at a similar magnitude, as
shallow standing water.** The loose significance gate required to recover
partial inundation is necessarily loose enough to admit soil moisture — and
after an event of this scale, soil moisture is everywhere, in flooded and
unflooded zips alike. That is exactly the flat, uninformative 36% we measured.

Tightening the gate back toward the binary detector's threshold just recreates
the binary detector. There is no setting in between that separates the two,
because **single-polarisation amplitude at 30m does not carry the information
needed to distinguish wet ground from standing water.** This is a property of
the measurement, not of the tuning.

### What was done about it

`SUBPIXEL['enabled']` is **False**. The code and its tests are kept, not
deleted — the physics is correct (the unmixing recovers known fractions
exactly, and a test proves the tempting dB-domain shortcut would be wrong), and
the method is sound wherever the confound is absent: dual-pol or polarimetric
data, finer resolution, or events without basin-wide antecedent rainfall.
Re-enabling is one line plus a re-validation.

Shipping it enabled would have multiplied apparent sensitivity by 65 while
adding zero accuracy. That is the most dangerous kind of change — it looks
like progress in every dashboard.

### What this implies for the remaining recall ideas

Section 7's list needs reordering now that its top item is eliminated:

1. **Dual-polarisation (VV+VH) partial-water detection.** VH responds
   differently to soil moisture than to specular water surfaces, which is
   precisely the confound that killed the single-pol attempt. The pipeline
   already loads VH for the dual-pol cross-check, so the data is in hand.
2. **A learned fourth vote (Phase 4).** With NFIP claims as labels, a model
   over depth, HAND, foundation type, occupancy and *both* polarisations can
   learn the wet-soil/standing-water boundary empirically rather than by a
   hand-set threshold. This is now the most promising path.
3. **More post-event scenes.** Harvey had two on the primary orbit; a median
   over a 14-day window actively averages away transient flooding.
   Sentinel-1C/D have since restored 6-day revisit.
4. Commercial SAR at 6-24h revisit for events where Sentinel misses the peak.

Note that 1 and 2 both attack the *same* confound this section identified.
That is the useful thing a negative result buys: the next attempt is aimed at
a known obstacle instead of a guess.

---

## 10. Point-level ground truth, at last — and it says recall is near zero

Every section above this one was written against ZIP-level NFIP claims. That
ceiling is documented in `docs/PROJECT_STATE.md` §2: the 3,980-row Brazos
validation set carries **14 independent bits of information**, because NFIP
redacts claims to ZIP resolution and every property in a ZIP therefore shares
one label. Three detector improvements (Phases 4a, 4d, 4e) were built,
measured, and shelved as "not proven" against that ceiling — never shown to
fail, merely never shown to work.

This section is the first measurement in the project that does not have that
problem.

### The data

USGS Short-Term Network high water marks, event 180 ("2017 Harvey"): **2,364
GPS-tagged points** where a field crew measured, in feet, how far the water
rose above the ground surface. Free, public, no agreement needed. 1,171 carry
a usable `height_above_gnd`.

This is strictly better ground truth than NFIP claims for the question we care
about, on three counts:

- **Point-level.** Each mark is its own observation, at its own coordinates.
  No ZIP aggregation, no pseudo-replication.
- **A measured depth, not a binary label.** It validates `max_depth_ft`
  directly. There is no flood/no-flood threshold to argue about.
- **Independent of us.** Surveyed by USGS in 2017, published since.

`height_above_gnd == 0` is treated as MISSING, not as zero depth. All 974 such
Harvey marks carry a surveyed `elev_ft` and 670 are debris lines, which cannot
be deposited at precisely 0.00 ft above ground — it is an unfilled optional
field. Counting them as zeros would have manufactured ~900 fake "no flooding
here" points out of a dataset that contains none. The drop count prints on
every run.

### What it can and cannot settle

**It cannot measure precision.** Every HWM is a place that flooded. The dataset
contains no surveyed dry points and therefore defines no negative class. A
detector that returned "10 ft everywhere" would score perfect recall here.
Precision still has to come from somewhere else.

**It measures recall and depth accuracy at known-flooded locations**, which is
exactly the quantity section 7 could only estimate indirectly.

**An HWM is the PEAK stage; Sentinel-1 samples an instant** every 6–12 days.
Where a pass missed the crest, a correct detector still reads low, so
underestimate is the expected result, not automatically an error. For Brazos
the crest at Richmond (~1 September) is bracketed by passes on 30 August and
5 September, so the timing excuse is weakest exactly where the result is worst.

**Marks cluster by survey site.** Several marks a few metres apart are one
observation of the detector, so sites — not marks — are the independent unit.
Recall bounds below are Clopper-Pearson over sites, and correlation CIs are
bootstrapped by resampling sites.

### The result

`python validation/hwm_check.py brazos --sweep`

| radius | Brazos recall (28 marks / 18 sites) | Harvey recall (63 marks / 48 sites) |
|--------|-------------------------------------|-------------------------------------|
| 10 m   | 0.0% (0/28) | 0.0% (0/63) |
| 20 m   | 0.0% (0/28) | 0.0% (0/63) |
| 30 m   | 0.0% (0/28) | 0.0% (0/63) |
| 50 m   | 0.0% (0/28) | 4.8% (3/63) |
| 100 m  | 0.0% (0/28) | 11.1% (7/63) |

The whole sweep is shown rather than its best point, per the discipline in
PROJECT_STATE §6.

**Brazos — open riverine floodplain, SAR's best case, the study area this repo
chose precisely because it is the most favourable setting available:**

- Site-level recall **0 of 18 sites. 95% CI [0.0%, 18.5%]** (Clopper-Pearson).
- Depth bias **−2.64 ft**, MAE 2.64 ft, RMSE 3.32 ft.
- Bias grows with the depth that matters: −0.77 ft at surveyed 0–1 ft,
  **−5.64 ft at surveyed 4 ft and deeper** (n=7, 4 sites). The deeper the real
  flood, the more of it we miss — the opposite of the failure mode a triage
  product can tolerate.
- Not explained by permanent-water masking: 20 of the 28 marks are not near
  permanent water and still read exactly zero.
- Not explained by sampling geometry: identical at every radius from 10 m to
  100 m.
- Not a sampling bug: `hand_ft` returns real varied terrain (1.09–17.34 ft),
  `rel_elev_ft` is populated at all 28, and dual-pol reports available at all
  28. The image sampled correctly and contained no water at these points.

**Harvey — dense urban, already known to be the weak case:**

- 3 of 63 marks at the 50 m headline radius — site-level **3 of 48 sites, 95%
  CI [1.3%, 17.2%]**. At 10–30 m it is 0 of 48 sites, 95% CI [0.0%, 7.4%].
- Correlation between detected and surveyed depth: Pearson **r = −0.072
  (p = 0.58)**, Spearman r = +0.078 (p = 0.54). No relationship.
- The apparent recall gain at 100 m is not signal. Recall rises to 11.1%, but
  among detected marks the error gets *worse* — MAE 5.62 ft, and bias flips
  from −0.78 ft to +2.58 ft. A wider buffer is finding unrelated water
  somewhere in a 100 m circle, not the water at the mark. This is precisely
  what sweeping a parameter is for.

### Why this is consistent with everything above, and what it changes

It is not a surprise given the base rate: the full Brazos pipeline detects
flooding at **22 of 4,000 properties (0.55%)**. If HWM points behaved like
average properties, the expected count at 28 marks would be 0.15. Zero is what
a 0.55% detector produces.

What changes is what we can *say*. Section 7 extrapolated "roughly 20% recall"
from ZIP claim counts and was careful to call it a lower bound. The direct
measurement puts the 95% upper bound at **18.5% in the best-case study area**,
and the point estimate at zero. The extrapolated figure was optimistic, and the
route that produced it — scaling a detection rate against claims in ZIPs that
extend beyond the study bbox — should not be quoted again now that a direct
measurement exists.

It also explains why Phases 4a, 4d and 4e could never be validated. They were
tuned and tested on top of a detector that fires at 0.55% of properties and 0%
of surveyed flood points. There was almost nothing there for them to improve,
and the ZIP labels could not have revealed it.

**The honest headline is unchanged in direction and sharper in size:** Altis's
ZIP-level severity ranking correlates with what adjusters recorded and paid
(+0.366 / +0.537, section 7), and its per-property flood call is not yet
supportable. What is new is that the second half is now measured at point
level against surveyed depths, not inferred from aggregate labels.

### What this makes worth doing next

The binding constraint is **detection recall in the base SAR mask**, not the
votes layered on top of it. Every idea in section 9's list is still aimed at
that, and now there is a test with enough power to adjudicate them: 66
independent survey sites across the two areas, with measured depths, runnable
in minutes.

The immediate consequence is that `hwm_check.py` — not the ZIP correlation —
should be the gate any future detector change has to pass. A change that moves
site recall off zero at Brazos is real. A change that improves a ZIP
correlation built on 14 bits is not evidence.

### Reproducing this

```bash
python validation/hwm_check.py brazos --sweep    # ~6 min
python validation/hwm_check.py harvey --sweep    # ~6 min
```

Writes `outputs/hwm_check_<event>.csv` (one row per mark, carrying the raw
detector columns so a zero can be explained rather than only reported) and
`outputs/hwm_check_<event>.json` (summaries, the detector's provenance, and the
caveats above). The raw USGS response is cached in
`outputs/usgs_hwm_event180.json`, so the check runs with no network.

---

## 11. Why the detector sees nothing: it is looking for the wrong sign

§10 established near-zero point-level recall. This section is the diagnosis,
and it changes what to build next.

### First, the obvious explanation was tested and is wrong

The leading hypothesis was temporal: `load_sar_composite` returns
`collection.median()` over the whole post-event window, so a pixel flooded on
one of Brazos's three DESCENDING passes (30 Aug, 5 Sep, 11 Sep, against a ~1 Sep
crest) would have a DRY median and be invisible.

`validation/per_pass_probe.py` holds everything else identical — same baseline,
slope mask, permanent water, range-guarded Otsu, z-threshold — and changes only
whether the mask is computed on the median or on each scene separately, unioned.

| | flagged share of bbox | marks detected | sites |
|---|---|---|---|
| Brazos median (current) | 0.348% | 0/28 | 0/18 |
| Brazos per-pass union | **3.062%** | **0/28** | **0/18** |
| Harvey median (current) | 2.039% | 1/63 | 1/48 |
| Harvey per-pass union | 2.470% | 1/63 | 1/48 |

Per-pass union flags **8.8× more of the Brazos bbox and finds nothing extra.**
Not one individual pass detects any of the 28 surveyed flood points. The median
was never the problem, and switching to per-pass would have bought a large
precision loss for zero recall — which is exactly what a dry-land control is
for, and why one is built into the probe.

### The gate that actually closes

`validation/gate_probe.py` decomposes the zero into the specific gate
responsible. `orbit_flood_mask` requires a pixel to pass all four.

| gate | Brazos (28 marks) | Harvey (63 marks) |
|---|---|---|
| SLOPE < 5° | **28/28 pass** | **63/63 pass** |
| not JRC permanent water | **28/28 pass** | **63/63 pass** |
| ABSOLUTE: VV < Otsu | 0/28 pass | 4/63 pass |
| CHANGE: z ≤ −2.0σ | 1/28 pass | 6/63 pass |

**The terrain gates are innocent, and that matters for how §10 should be read.**
USGS surveys high water marks where a mark survives and can be reached — bridge
abutments, channel banks, walls — so slope exclusion and permanent-water
exclusion were plausible reasons §10's recall figure might describe *where USGS
surveys* rather than the detector. Neither fires on a single mark. Mean slope
is 1.96° (Brazos) and 1.75° (Harvey). Every mark sits on flat, floodable,
non-permanent-water ground. **§10's recall figure stands as written.**

The radiometry rejects them, and the direction is the whole finding:

| | Brazos | Harvey |
|---|---|---|
| mean VV at marks | −10.19 dB | −7.56 dB |
| mean VV baseline at marks | −10.57 dB | −10.11 dB |
| **mean VV z-score at marks** | **+0.39σ** | **+2.83σ** |
| Otsu threshold VV must fall below | −16.00 dB | −16.00 dB |

At places that demonstrably flooded, the C-band return is **brighter** than
that pixel's own 12-month baseline — at Harvey by nearly three sigma. An
open-water detector tests for darkening. It is looking for the wrong sign.

Worth recording alongside: the Otsu range guard fires on both events (raw Otsu
−10.75 dB at Brazos, outside the open-water range, so the −16.00 dB fallback is
used). A histogram whose natural split sits at −10.75 dB has no water mode in
it at all. The guard is doing its job; there is simply nothing dark to find.

### The physics this points at

Open water darkens C-band because a smooth surface reflects specularly away
from the sensor. That is real, and it is why the detector works at Lismore.
But it requires water that is **open, calm, and unobstructed**. Where water
stands among buildings, fences, trees and vegetation — i.e. anywhere people
live — it instead forms dihedral corner reflectors with vertical surfaces and
returns MORE energy. The wetter ground beneath a canopy also raises volume
scattering rather than lowering it.

Harvey's +2.83σ is that effect at full strength in dense suburb. Brazos's
+0.39σ is the same effect roughly cancelling the darkening in mixed terrain.

### Re-testing the shelved phases, now that the test has power

Phases 4a, 4b and 4e were shelved against zip labels carrying 14 bits.
`validation/phase4_probe.py` re-measures them at the marks, reporting each
signal's recall **and** the fraction of the whole bbox it fires on, because
HWMs have no negative class and recall alone would rank a detector that fires
everywhere first. `lift = recall / bbox-fired-fraction`; 1.0 means no
information.

**Brazos — open riverine (28 marks, 18 sites):**

| signal | marks | sites | bbox fired | lift |
|---|---|---|---|---|
| open water (ships today) | 0/28 | 0/18 | 0.24% | 0.00 |
| sub-pixel, Phase 4a (disabled) | 6/28 | 3/18 | 5.71% | **3.75** |
| dual-pol, Phase 4b | 0/28 | 0/18 | 0.40% | 0.00 |
| double-bounce, Phase 4e (disabled) | 0/28 | 0/18 | 0.02% | 0.00 |
| Sentinel-2 optical (cross-check only) | 7/28 | 3/18 | 3.84% | **6.50** |

**Harvey — dense urban, the product's weakest zone (63 marks, 48 sites):**

| signal | marks | sites | bbox fired | lift |
|---|---|---|---|---|
| open water (ships today) | 3/63 | 3/48 | 1.69% | 2.82 |
| sub-pixel, Phase 4a (disabled) | 12/63 | 11/48 | 8.28% | 2.30 |
| dual-pol, Phase 4b | 6/63 | 6/48 | 3.94% | 2.41 |
| **double-bounce, Phase 4e (DISABLED)** | **28/63** | **20/48** | 10.27% | **4.33** |
| Sentinel-2 optical (cross-check only) | 6/63 | 6/48 | 2.08% | 4.59 |

**Double-bounce finds 28 of 63 surveyed urban flood marks against 3 for the
detector that ships — nine times the recall for six times the bbox coverage —
and it is currently switched off.** It was disabled on a zip-label measurement
(§ config.py DOUBLE_BOUNCE) that could not have detected it.

Its near-zero firing at Brazos (0.02% of bbox) is correct behaviour, not a
failure: its urban-built gate excludes rural floodplain by design.

Sentinel-2 optical has the best lift on both events despite being used only as
a veto today. That is worth noting precisely because it is not a SAR variant —
it is the one signal here that cannot share SAR's blind spot.

### What this does and does not license

It does **not** license enabling anything. Every mark is a place that flooded,
so none of these numbers is a precision measurement, and `lift` uses the study
bbox as a dry-land proxy rather than real dry ground truth. A signal can raise
recall purely by firing more often, and three of these do fire much more often.

That is what §12 is for.

---

## 12. Per-property precision, at last — and nothing discriminates

§10 measured recall against surveyed points. §11 explained the zero. This
section is the first measurement in the project with **both classes**, and it
is the one to quote.

### The ground truth

USGS SIR 2018-5070 (doi:10.5066/F7VH5N3N) publishes, per mapped Harvey river
reach, the flood inundation extent **and the mapped area boundary** — the domain
within which USGS delineated it. Inside the boundary and outside the extent is
not unlabelled ground; it is ground USGS mapped and found dry.

Labelling USACE National Structure Inventory structures by which polygon
contains them gives, inside our own study bboxes:

| | structures labelled | flooded | dry |
|---|---|---|---|
| Brazos (upper Brazos reach) | 68,624 | 25,062 | 43,562 |
| Harvey (San Jacinto reach) | 42,739 | 16,389 | 26,350 |

Structures OUTSIDE the mapped boundary are dropped, never called dry —
absence of mapping is not evidence of dryness, and counting it as such would
manufacture negatives and inflate both specificity and precision.

**Coverage caveat that must travel with any Harvey number from this section:**
the San Jacinto reach covers only the northern part of the HARVEY bbox (≈29.88°N
and up), *not* the Addicks/Barker reservoir pools that study area was chosen
for. Harvey numbers here describe riverine floodplain in north Harris County.

### Brazos: 2,999 structures, 36.5% base rate

| signal | precision | recall | specificity | AUC | precision lift |
|---|---|---|---|---|---|
| shipped detector (`max_depth_ft > 0.1`) | 0.0% | 0.0% | 99.9% | 0.499 | 0.00× |
| sub-pixel, Phase 4a | **NOT MEASURED — abstained** | — | — | — | — |
| dual-pol, Phase 4b | 44.0% | 2.0% | 98.5% | 0.503 | 1.21× |
| double-bounce, Phase 4e | **NOT MEASURED — abstained** | — | — | — | — |
| Sentinel-2 optical | 37.9% | 4.6% | 95.7% | 0.502 | 1.04× |
| HAND terrain only, ≤10 ft | 38.4% | 91.2% | 15.8% | 0.552 | 1.05× |

**Every AUC lands between 0.499 and 0.552. Nothing achieves per-property
discrimination.**

> **CORRECTION (see §14).** The sub-pixel and double-bounce rows above
> originally read "never fires", which is wrong and was published in error.
> Both features ship **disabled** in `config.py`, and
> `build_flood_depth_image` then writes a CONSTANT ZERO band for each. Scored
> naively that is indistinguishable from a detector that ran and saw nothing.
> They were never evaluated in this run. `extent_check.py` now refuses to
> score an abstaining band and prints NOT MEASURED instead; the real numbers
> are in §14.

### The HAND row is a trap, and it is worth spelling out

At a glance the HAND row reads as a discovery: a static terrain layer that
never observes the storm beats every SAR signal on recall and AUC, which sounds
like an argument for making it the primary signal.

It is not. `validation/hand_arch_probe.py` was written to test exactly that
proposal, and it fails on its own terms:

| rule | flags | precision | recall | lift |
|---|---|---|---|---|
| label EVERY structure flooded | 100.0% | 36.5% | 100% | 1.00× |
| HAND ≤ 10 ft | 86.8% | 38.4% | 91.2% | 1.05× |

HAND ≤ 10 ft says "yes" to 86.8% of structures. Its 91.2% recall is a property
of that permissiveness, not of skill, and its precision sits **1.9 points above
labelling every structure flooded.**

No threshold rescues it. The whole sweep, not its best point:

| threshold | flags | precision | recall | lift |
|---|---|---|---|---|
| ≤1 ft | 13.6% | 39.5% | 14.7% | 1.08× |
| ≤2 ft | 29.6% | 38.7% | 31.4% | 1.06× |
| ≤3 ft | 43.1% | 39.0% | 46.0% | 1.07× |
| **≤5 ft** | 64.0% | **40.8%** | 71.5% | **1.12×** |
| ≤7.5 ft | 79.2% | 39.4% | 85.5% | 1.08× |
| ≤10 ft | 86.8% | 38.4% | 91.2% | 1.05× |
| ≤15 ft | 94.1% | 37.4% | 96.4% | 1.03× |
| ≤20 ft | 97.4% | 37.0% | 98.6% | 1.01× |
| ≤30 ft | 99.8% | 36.6% | 100% | 1.00× |

Best lift anywhere: 1.12×.

### And SAR confirmation makes the candidate set worse

The proposed architecture — HAND selects candidates, SAR confirms or downgrades
within them — was measured directly:

| rule | precision | recall |
|---|---|---|
| HAND ≤ 10 ft alone | 38.4% | 91.2% |
| HAND ≤ 10 ft **AND** any SAR/optical fires | **34.7%** | **4.7%** |

Intersecting with SAR costs **3.7 points of precision and 86 points of recall.**
A confirm-or-downgrade stage cannot help when the confirming signal is not
merely uninformative but slightly anti-correlated inside the candidate set.
**The architecture change is not justified, and this is why — measured, before
anything was restructured.**

### What this means for the product

The commercial claim this was meant to support — *"this specific house at 123
Main St definitely flooded, skip the inspection"* — **is not supportable today,
at either study area, by any signal in the pipeline.** That is now a
measurement against 68,624 and 42,739 labelled structures, not an inference
from 14 zip codes.

What survives is the zip-level severity ranking (§7: +0.366 depth, +0.537 paid
claim). Altis can say which areas were hit harder. It cannot yet say which
house.

The binding constraint is unchanged from §11 and is physical, not a tuning
problem: at surveyed flood points the C-band return is *brighter* than
baseline, and the detector tests for darkening. Threshold work cannot fix a
sign error.

---

## 13. Harvey per-property result — double-bounce could not be judged, and here's why

`validation/extent_check.py harvey`, 2,999 USGS-labelled structures (San
Jacinto reach, 38.3% base rate — see §12's coverage caveat: northern part of
the bbox only, not Addicks/Barker):

| signal | precision | recall | AUC | lift |
|---|---|---|---|---|
| shipped detector | 68.8% (n=16, 95% CI [41%, 89%]) | 1.0% | 0.504 | 1.79× |
| sub-pixel, Phase 4a | **NOT MEASURED — abstained** | — | — | — |
| dual-pol, Phase 4b | 54.9% | 4.3% | 0.511 | 1.43× |
| **double-bounce, Phase 4e** | **NOT MEASURED — abstained** | — | — | — |
| Sentinel-2 optical | 30.4% | 3.0% | 0.493 | 0.79× |
| HAND ≤10 ft | 35.8% | 78.6% | **0.458** | 0.93× |

> **CORRECTION (see §14).** This section originally concluded that
> double-bounce "fired on zero of 2,999 structures" and attributed it to the
> San Jacinto reach being the wrong terrain. **Both halves were wrong.**
> Double-bounce ships disabled, so the band was a constant zero and was never
> computed. And the terrain claim does not hold either: **2,362 of the 2,999
> structures (79%) are urban-flagged.** This reach does contain the built-up
> terrain double-bounce targets. The real measurement is in §14.

Two things worth flagging on their own:

- **HAND is actively anti-correlated at Harvey** (AUC 0.458, lift 0.93× —
  *worse* than labelling everything flooded). Combined with §12's Brazos AUC
  of 0.552, HAND's apparent skill is not a stable property of the signal; it
  swings by terrain. Nothing here supports treating HAND as a reliable
  candidate-set generator anywhere.
- **The shipped detector's precision (68.8%) is the best number on this page**,
  but it is built on 11 true positives out of 1,150 flooded structures — 1.0%
  recall, 95% CI on precision is [41%, 89%]. It is not a product; a signal that
  fires on 16 of 2,999 structures cannot carry a triage workflow. It is a
  genuinely promising thread for future work: on the rare pixel where this
  detector fires, it may be worth real confidence — but n=16 is nowhere near
  enough to act on.

### What would actually let double-bounce be judged

Nothing in the FEMA/USGS Harvey release maps the Addicks/Barker corridor as
inundation extent + boundary the way it does the Brazos and San Jacinto
reaches (checked — not available). Judging double-bounce needs either a
different authoritative source for that specific area, or the adjuster
feedback loop (PROJECT_STATE §7 item 1) once the product has real usage there.

---

## 14. Double-bounce measured against a negative class: it does NOT ship

§13 reported double-bounce as "never fires" at Harvey. That was an abstention,
not a measurement — it ships disabled, so the pipeline wrote a constant-zero
band (see the corrections in §12/§13). Re-run with
`extent_check.py harvey --enable-candidates`, on the same 2,999 USGS-labelled
structures, **2,362 of which (79%) are urban-flagged** — this reach does
contain the built-up terrain double-bounce targets.

**Harvey, 2,999 structures, 38.3% base rate:**

| signal | precision (95% CI) | flags | recall | AUC | precision lift |
|---|---|---|---|---|---|
| shipped detector | 75.0% [40.9, 92.9] | 8 (0%) | 0.5% | 0.503 | 1.96× |
| **dual-pol (4b)** | **51.2% [40.8, 61.4]** | 86 (3%) | 3.8% | 0.508 | **1.33×** |
| **double-bounce (4e)** | **38.4% [36.2, 40.7]** | **1,790 (60%)** | 59.7% | 0.520 | **1.00×** |
| sub-pixel (4a) | 37.0% [34.1, 40.0] | 1,051 (35%) | 33.8% | 0.491 | 0.97× |
| HAND ≤10 ft | 35.5% [33.7, 37.4] | 2,520 (84%) | 77.8% | 0.451 | 0.93× |
| Sentinel-2 optical | 27.1% [19.6, 36.2] | 107 (4%) | 2.5% | 0.492 | 0.71× |

### The verdict, on the rule set before the numbers were seen

The standing rule was: **double-bounce ships only if its precision clearly
beats the shipped detector's — not merely its recall.** It does not.
**38.4% against 75.0%, and its precision is identical to the 38.3% base rate.**

**DOUBLE-BOUNCE STAYS DISABLED.**

### Why this is the most important result in this document

Against high water marks (§11), double-bounce looked like a decisive win: 28/63
marks and 20/48 sites at Harvey, against 3/63 for the shipped detector — nine
times the recall, "lift 4.33". It was the strongest positive signal in the
whole project.

With a negative class, that evaporates. It flags **1,790 of 2,999 structures —
60% of the book** — and among those it is right 38.4% of the time, which is
exactly what you get by labelling every structure flooded. Its recall was
bought entirely by firing on most things.

This is precisely the failure §10 warned about in advance: *"a detector that
returned '10 ft everywhere' would score perfect recall"* on a positives-only
dataset. Double-bounce is a mild version of that detector, and only a real
negative class could reveal it. **HWM-derived "lift" is not a substitute for
precision** — the bbox-fraction denominator used in §11 is not the same
quantity as a base rate over structures, and it flattered double-bounce by
roughly 4×.

Sub-pixel (4a) fails the same way and is now measured rather than abstained:
precision 37.0% against a 38.3% base rate — **below** it — while flagging 35%
of the book.

### What survives

**Dual-pol (Phase 4b) is the only detector here with precision meaningfully
above the base rate at a usable alert volume:** 51.2% [40.8, 61.4] on 86
flagged structures, a 1.33× lift. It is enabled today. Its recall is 3.8%,
so it is a *narrow, moderately-trustworthy* signal, not a portfolio answer —
but of everything tested it is the only one an adjuster could act on, and it is
the one Phase 4b's physics predicted (VV and VH must corroborate, which is what
stops it from firing on 60% of the map).

The shipped detector's 75.0% precision rests on 8 flagged structures — 95% CI
[40.9, 92.9]. Directionally promising, far too small to act on.

**Nothing here changes §12's conclusion.** Every AUC remains between 0.45 and
0.52. No signal delivers per-property discrimination; dual-pol delivers a small
number of moderately reliable positives, which is a different and much smaller
claim.
