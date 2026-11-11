# How Altis calculates an insured loss, and how far to trust it

Written 2026-11-08. Method, the evidence behind every rule, what was tested on
held-out storms, and what is NOT claimed. Protocol and raw results:
`docs/PREREG_LOSS_MODEL.md`, `outputs/loss_model_check.json`.

## The calculation, step by step

1. **Water depth** at the property (radar, plus river-gauge physics where
   available), with an uncertainty range.
2. **Which depth:** depth above the first floor if the structure's first-floor
   height is known (USACE National Structure Inventory), else depth above ground.
3. **Damage distribution.** From 111,794 real settled NFIP claims
   (Matthew 2016, Louisiana 2016, Harvey 2017, Irma 2017) we learned how much
   of a building's actual cash value is damaged at each depth, as a full
   *distribution* (40 points), not one number. It depends on the building's
   value (damage as a share of value falls steeply with building size:
   power law, value^-0.43) and on structure type (single-family with or
   without basement, other).
4. **Policy terms** (`pipeline/policy_terms.py`), applied to every point of
   that distribution:
   - *Valuation:* actual-cash-value policies pay the damage; replacement-cost
     policies pay damage x (1 + 0.335 x (replacement cost / ACV - 1)).
   - *Deductible*, from the upload, or a labelled assumption.
   - *Limit:* payment is capped at the coverage limit.
5. **Expected payment** = the average over the distribution. This order
   matters: deductibles and limits are nonlinear, so the expected payment is
   not the payment at the average damage.
6. **Output:** mid estimate; low/high from the depth uncertainty; the 10th-90th
   percentile of a single claim at that depth; contents separately; and a
   record of which policy terms were provided and which were assumed.
7. **Storm calibration:** once some claims settle, paste the building payments
   in and Altis learns the storm's dollar scale and rescales the whole book.

## Where each rule comes from (nothing is a guess)

| Rule | Source |
|---|---|
| Deductible code meanings | FEMA OpenFEMA NfipClaims data dictionary |
| ACV claims pay damage minus deductible | holds exactly for 75% of below-limit ACV claims in the training storms |
| Replacement-cost uplift 0.335 | fitted on 40,430 replacement-cost claims (IQR 0.10 to 0.64) |
| Cap at the limit | 87% of claims whose loss exceeds limit + deductible paid at or below the limit |
| No coinsurance penalty | the NFIP 80% rule is not visible in the claims (uplift 0.331 vs 0.356 either side of 80%), so it is not hard-coded |
| Depth inputs | `interiorWaterDepth` and `exteriorWaterDepth` (clean, inches). FEMA's `waterDepth` mixes feet and inches and is not used |
| A recorded depth of 0 | means "unrecorded", not dry (those claims average 30% damage) |
| Missing policy fields | filled from training-storm medians (values = 0.896 x coverage, replacement cost = 1.25 x ACV, $1,250 deductible) and **always labelled assumed** |

## What was tested, and what happened

Scored once on four storms the model never saw (Florence 2018, Imelda 2019,
Ida 2021, Helene 2024), each claim fed its own recorded depth. Isolates the
dollar calculation from flood detection. 8 cells, 3,530 to 22,686 real claims.

| Measure | Altis | Usual average-reserve | Previously shipped |
|---|---|---|---|
| Mean error of the storm total (uncalibrated) | **17.6%** | 24.3% | 33.3% |
| Storm total, range over the 8 cells | 0.73x to 1.33x of paid | 0.63x to 1.48x | 0.30x to 1.19x |
| Claim-level error (sum of abs error / paid) | lower in 7 of 8 cells vs average-reserve, 8 of 8 vs shipped | | |

| Storm total after learning from settled claims | Typical error | Worst 90th percentile |
|---|---|---|
| 50 claims | 9% | 31% |
| 200 claims | 6% | 15% |
| 1,000 claims | 2.5% | 7.5% |

**Pre-registered criteria: not met.** C2 (claim-level error below the average
reserve in every cell) missed by one cell that was a statistical tie; C3
(storm total closer to the truth than the old method in 7 of 8 cells) was met
in 5. C4 (storm calibration works) passed 8 of 8. Full table and the amended
protocol: `docs/PREREG_LOSS_MODEL.md`.

## What this does NOT claim

- **Contents are not validated.** Uncalibrated, the contents estimate overshoots
  paid contents by 40% to 87% on the held-out storms (worse than an average
  guess). It is kept out of the headline total and flagged; settled claims
  repair it (5% to 9% after 200). The headline is the BUILDING loss.
- **It does not replace an adjuster on a single claim.** Individual claims are
  off by 46% to 92% of their dollars, rank correlation 0.33 to 0.60.
- **Uncalibrated, a storm total can be off by about 30%.** The unexplained part
  is a storm-wide factor (leave-one-storm-out on the training storms, given the
  true depth: Harvey's payments ran roughly 65-75% above the model's total,
  Irma's and Matthew's roughly 40% below). Flood duration might explain it but is not recorded in
  public claims, so it cannot be fitted. It is learned from settled claims.
- **It is conditional on a claim being filed.** It does not predict who files,
  fraud, supplemental or ICC payments, or subrogation recoveries.
- **Real use adds flood-depth error**, measured elsewhere (radar depth bias
  about 2.6 ft; gauge depth better near rivers). The test above feeds each claim
  its true depth.
- **NFIP-calibrated.** Private flood and homeowners policies have different
  terms. Pass the carrier's own terms; calibrate on its claims for firm numbers.
- **Early-settling claims skew small.** The calibration test drew claims at
  random, which is optimistic for the first few dozen real closures.
- Training and test storms are US hurricane/tropical-storm floods; other
  perils and regions are untested.

## What would make it carrier-grade

1. Calibrate on the carrier's own settled claims for a pilot event (replaces the
   NFIP assumptions, the biggest single improvement).
2. Per-policy terms in every upload (deductible, valuation, replacement cost,
   contents): without them totals ran 5% to 25% low in testing.
3. Add flood duration from the carrier's claims, once it is recorded.
4. Re-run the same test on a new set of storms (Ian 2022, Sally 2020, Michael
   2018 were reserved for this) and on the carrier's data.
