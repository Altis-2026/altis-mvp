# Pre-registration: conformal intervals on the dollar estimate

Written and committed BEFORE any reserved storm (Ian 2022, Sally 2020,
Michael 2018) was downloaded or scored.

## Question
The loss calculation reports a claim-level 10th to 90th percentile band. On
Florence it contained the actual payment 77.5% of the time (nominal 80%). Does a
split-conformal widening, calibrated on storms the model was NOT fitted on,
give bands whose real coverage on a NEW storm matches the stated level?

This tests the uncertainty band only. It is conditional on a claim being filed
and is fed each claim's own recorded depth (depth-detection error is a separate
problem, measured elsewhere).

## Data
- **Calibration:** the four held-out storms of `docs/PREREG_LOSS_MODEL.md`
  (Florence, Imelda, Ida, Helene). The model was not fitted on them.
- **Test (never read before the run):** Ian 2022, Sally 2020, Michael 2018.
  Up to 30,000 claims per storm in API id order, same usable-claim filter and
  the same four cells as the loss check (scenario x depth basis).

## Method (fixed)
1. For each claim, the model's band [p10, p90] under scenario `full` (all terms
   known) or `min` (only the limit known), interior or exterior depth basis.
2. Normalised conformalised-quantile-regression score:
   s = max(p10 - y, y - p90) / w, with w = max(p90 - p10, 1000 USD).
3. For nominal level L in {0.80, 0.90}: q = the ceil((n+1)L)/n empirical
   quantile of s over the pooled calibration claims of that cell. Widened band:
   [max(0, p10 - q w), min(limit, p90 + q w)]. For L = 0.90 the starting band is
   still [p10, p90] (the score absorbs the difference).
4. Apply the stored q to each test storm unchanged. No tuning on test storms.

## Pass criteria (decided now)
- **P1** At L = 0.80, coverage pooled over the three test storms lies in
  [0.75, 0.85] in every cell (4 of 4).
- **P2** Per-storm coverage at L = 0.80 is at least 0.70 in at least 9 of the 12
  storm x cell results (no storm badly under-covered).
- **P3** Conformal coverage is closer to 0.80 than the raw band's pooled
  coverage in at least 3 of 4 cells, unless the raw band is already within 0.02.
- **P4** At L = 0.90, pooled coverage lies in [0.85, 0.95] in at least 3 of 4
  cells.
- **Reported, not gating:** coverage by depth bin, median band width as a share
  of the limit, and leave-one-storm-out coverage on the four calibration storms.

## Decision rules
- All of P1 to P4 pass: the product may call the band "calibrated to settled
  claims on past storms".
- Otherwise: the product keeps the band but labels it "typical range, not a
  guarantee" and the report lists exactly which criteria failed.
- Whatever happens, the numbers are published as measured. Amendments are allowed
  only before any test storm is scored.

## Known limits (stated in advance)
- Coverage is over claims that were filed, not over all properties at risk.
- Storm-to-storm shifts (claims practice, regional costs) can break coverage;
  P2 exists to expose that.
- The test storms are all Gulf/Atlantic hurricanes. Other flood types are not
  tested.
