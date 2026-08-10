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
