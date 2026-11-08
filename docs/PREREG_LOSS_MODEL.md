# Pre-registration: carrier-grade loss calculation

Written and committed 2026-11-08 BEFORE any held-out storm was downloaded or scored.

## Question
Given a property's flood depth, does Altis's dollar calculation reproduce what
the insurer actually paid, and does it beat what carriers do today for an
initial reserve (an average per claim)? This tests the DOLLAR CALCULATION only,
fed each claim's own recorded depth. It does NOT test flood detection (depth
error is a separate, already-measured problem) and it is conditional on a claim
being filed (not take-up or claim propensity).

## Data (free, OpenFEMA NfipClaims v3; every record is a real settled claim)
- **Train/explore (fit and rule discovery only):** Hurricane Matthew 2016,
  Louisiana floods Aug 2016, Hurricane Harvey 2017, Hurricane Irma 2017.
- **Held out, never read before the run:** Hurricane Florence 2018, Tropical
  Storm Imelda 2019, Hurricane Ida 2021, Hurricane Helene 2024. Time-forward:
  all start after every training storm.
- Up to 30,000 claims per event in API id order (not random; the true totals
  are recorded). Reserve storms not used here (Ian 2022, Sally 2020, Michael
  2018) are kept for any follow-up round.

## The model (fixed; fitted on training storms only)
1. Damage ratio (ACV damage / ACV value) distribution by depth bin and segment
   from `interiorWaterDepth` (above first floor) and `exteriorWaterDepth`
   (above ground), inches. FEMA's mixed-unit `waterDepth` is not used.
2. Policy terms (`pipeline/policy_terms.py`): FEMA deductible codes; ACV-basis
   pays damage - deductible; RC-basis pays damage * (1 + theta*(RC/ACV-1)) -
   deductible with theta fitted on training (0.335); capped at the limit; no
   coinsurance penalty (not visible in training claims).
3. Expected payment = average over the damage distribution pushed through the
   terms. Fixed ahead of the test: 40 quantile points, min 150 claims per cell.

## Test population
Building claims in each held-out storm with: property value and replacement
cost > $10k, coverage > 0, known deductible code, valuation basis R or A, a
recorded payment, a recorded damage amount, and a recorded depth of 1 to 240
inches. Interior-depth and exterior-depth populations are scored separately,
giving 8 event x basis cells. Actual = max(0, amountPaidOnBuildingClaim) (zero
payments stay in).

## Methods compared
- **full**: all policy fields known (ACV, RC, limit, deductible, basis).
- **lite**: limit, deductible and basis known; values estimated from the limit.
- **min**: only the limit known; deductible, basis, values assumed.
- **noterms**: ablation, the same damage model with no deductible, valuation or limit.
- **shipped**: what the MVP did before: generic national curve x coverage, no terms.
- **avg**: the usual initial-reserve method: mean paid per claim over the
  training storms, CPI-adjusted to the storm's date, applied to every claim.

## Metrics (per cell)
- Aggregate ratio = sum(predicted) / sum(actual) (1.0 is perfect).
- nMAE = sum|predicted - actual| / sum(actual) (claim-level error).
- Spearman rank correlation; share of actuals inside the model's claim-level
  10th-90th percentile band.
- 95% CIs by bootstrap over counties (1,000 resamples, seed 0).

## Pass criteria (all three required)
- **C1** full: aggregate within +/-20% of actual in >= 75% of cells (6 of 8).
- **C2** full: nMAE below `avg` in every cell, with the bootstrap CI of the
  difference entirely below 0 in >= 75% of cells.
- **C3** full: aggregate closer to 1.0 than `shipped` in >= 7 of 8 cells.
Not gated, always reported: lite, min, noterms, contents, band coverage.

## Rules
Run once. No change to bins, quantile count, minimum cell size, theta, segments
or filters after seeing results; any change is a new pre-registration scored on
the reserve storms. Results reported whether they pass or not.

---

## Amendment 1 (written before any held-out storm was scored)

The held-out storms (Florence, Imelda, Ida, Helene) were downloaded but had not
been scored or inspected when this amendment was committed. Everything below
was learned from the TRAINING storms only.

**What happened.** The scoring script crashed twice (blank CPI month, a mixed
type county column) before printing any result. A dry-run mode was added that
runs the whole script on a training storm (Irma) so bugs surface without
touching the test. That dry run showed the original model over-predicting
Irma's total by 2.2x *in-sample*, which cannot be a real result. Cause: the
model treated damage as a fixed share of value, but at the same depth damage
falls from 49% of value for sub-$100k homes to 17% for $1-3M buildings.

**Changes made to the model (training data only).**
1. Structure chosen by leave-one-storm-out inside the training set
   (`validation/dev_loss_structure.py`, rule fixed in advance: lowest mean
   |aggregate ratio - 1|). Value brackets: 0.746. Power-law value
   normalisation (damage ratio ~ value^-c, c = 0.39-0.43, fitted on training):
   0.504. **Power-law chosen.**
2. Even then the aggregate swings 0.57x to 1.70x between storms given the
   true depth (Harvey under, Irma and Matthew over), uniformly across property
   type and size. Flood duration, the obvious candidate, is not recorded in
   public NFIP claims (the field is empty), so it cannot be fitted. A
   storm-level scale is therefore unexplained by the public data.

**Changes to the criteria.**
- **C1 (uncalibrated aggregate within +/-20%) is demoted from gating to
  reported.** The training storms already show it will fail; it is kept in the
  report as the stated limitation, not hidden.
- **New C4 (gating): storm calibration.** Learn the storm's scale from k
  randomly chosen early-settled claims of the same storm (k = 50, 200, 1000),
  apply it to the remaining claims, repeat 200 random draws (seed 0). Pass:
  at k = 200, median absolute aggregate error <= 15% AND 90th percentile <=
  30%, in >= 75% of cells. Limitation: random early claims are optimistic;
  real early closures skew small.
- Overall pass now requires C2, C3 and C4. Reported always: C1, lite, min,
  noterms, contents, band coverage.
- The model is the training-storm fit in `outputs/loss_model.json` with
  `structure: power`. No further changes before the scored run.

---

## Result (single scored run, 2026-11-08, after Amendment 1 was committed in a47e426d)

Output: `outputs/loss_model_check.json`. Four held-out storms x two depth bases =
8 cells; 3,530 to 22,686 real claims per cell. Every claim is fed its own
recorded depth, so this isolates the DOLLAR CALCULATION from flood detection.

| Storm / depth basis | Claims | Model total vs paid | Shipped before | Avg-reserve | Claim-level error: model / avg / shipped |
|---|---|---|---|---|---|
| Florence, above first floor | 7,055 | 1.14x | 0.69x | 1.24x | 0.80 / 0.96 / 0.84 |
| Florence, above ground | 11,391 | 1.33x | 1.19x | 1.48x | 0.92 / 1.17 / 0.93 |
| Imelda, above first floor | 3,530 | 0.88x | 0.30x | 1.09x | 0.46 / 0.61 / 0.75 |
| Imelda, above ground | 4,577 | 0.86x | 0.52x | 1.09x | 0.50 / 0.65 / 0.63 |
| Ida, above first floor | 7,409 | 0.93x | 0.62x | 1.00x | 0.63 / 0.74 / 0.89 |
| Ida, above ground | 15,255 | 1.19x | 1.13x | 1.40x | 0.83 / 1.07 / 1.01 |
| Helene, above first floor | 11,227 | 0.73x | 0.55x | 0.63x | 0.58 / 0.63 / 0.80 |
| Helene, above ground | 22,686 | 0.85x | 1.02x | 0.73x | 0.72 / 0.70 / 1.02 |

(Claim-level error = sum of absolute errors / total paid, lower is better.)

**Pre-registered criteria**

| Criterion | Result | Verdict |
|---|---|---|
| C1 uncalibrated total within +/-20% (reported, not gating) | 6 of 8 cells (range 0.73x to 1.33x) | reported |
| C2 claim-level error below average reserve in every cell, CI below 0 in >= 6 of 8 | 7 of 8 cells; CI below 0 in 7 of 8. Misses: Helene above ground, 0.72 vs 0.70 (CI -0.03 to +0.05, a tie) | **FAIL** (by one tied cell) |
| C3 total closer to 1.0 than the shipped method in >= 7 of 8 | 5 of 8 | **FAIL** |
| C4 after 200 settled claims: median error <= 15% and p90 <= 30% in >= 6 of 8 | 8 of 8 (median 3-7%, worst p90 15%) | **PASS** |

**Overall: not met** (C2 and C3 failed). Nothing was adjusted afterwards.

**Plain reading**
- Mean total error across the 8 cells: model 17.6%, usual average-reserve 24.3%,
  previously shipped method 33.3%. The model is better on average and on
  claim-level error (8 of 8 cells vs the shipped method, 7 of 8 vs the average
  reserve) but the shipped method happens to land closer in 3 cells, and
  Florence/Ida are statistically tied with it at the claim level.
- Uncalibrated, the total for a storm can still be off by about +/-30%.
- **Learning the storm's scale from settled claims works:** after 50 settled
  claims the total is typically within 9%, after 200 within 6%, after 1,000
  within 2.5% (worst-case 90th percentile 31%, 15%, 7.5%).
- **Individual claims remain rough:** claim-level error 46% to 92% of dollars,
  rank correlation 0.33 to 0.60. It estimates a book well; it does not
  replace an adjuster on any single claim.
- Policy data matters: with only the limit known ("min") totals run 5% to 25%
  lower than with full policy fields ("full"); the assumption-labelled
  fallbacks (values from coverage) systematically under-predict.
- The 10th-90th percentile claim band held 70% to 90% of actuals (nominal 80%).
- **Scope:** given the true depth. Real use adds the flood-depth error measured
  elsewhere, and it is conditional on a claim being filed.
