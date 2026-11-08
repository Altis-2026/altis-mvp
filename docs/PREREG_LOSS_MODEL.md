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
