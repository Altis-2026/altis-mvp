# Altis Accuracy Validation — Hurricane Ian

**Ground truth source:** OpenFEMA NFIP Redacted Claims v3 (date of loss 2022-09-28 to 2022-10-15)

**Study area:** Charlotte County, FL

**Ground-truth label:** share of NFIP claims reporting standing water >= 50.0% (no policy denominator available - weaker label)

## Summary

- Zip codes compared: **11**
- NFIP claims in comparison: **849**
- Altis properties in comparison: **1,000**

## Correlation Metrics

The headline number is the first one: Altis's satellite-derived mean depth against the mean water depth adjusters recorded on settled insurance claims, by zip. Both sides are continuous, which is a materially stronger test than the binary agreement the previous Individual Assistance ground truth could support.

- **Mean detected depth vs mean claimed water depth**, by zip: +0.325 (weak positive)
- Mean detected depth vs *median* claimed depth, by zip: +0.149 (weak positive)
- % flagged flooded vs NFIP claim rate, by zip: not computable — field not available in this run
- % flagged flooded vs % claims reporting standing water, by zip: not computable — `altis_pct_flagged` is constant across all 11 zips (= 0.0) — the detector returned the same value everywhere, so there is nothing to correlate
- Mean paid building claim vs mean detected depth, by zip: +0.121 (weak positive)

## Data quality: the `waterDepth` unit ambiguity

FEMA documents `waterDepth` as inches while noting that some records were entered in feet. That note describes the dominant behaviour, not an edge case, so this validation applies an explicit rule (values <= 15 are read as feet, above that as inches) and reports the split rather than hiding it:

| Interpretation | Claims |
|---|---|
| feet | 554 |
| inches | 294 |
| invalid | 1 |
| **total** | **849** |

The rule is justified by the damage data: mean damage ratio rises monotonically with the raw value, reaching ~0.6 around raw value 6. A 60% loss at six inches is not credible; at six feet it sits on a standard one-story residential depth-damage curve.

## Zip-Level Detail

| Zip | NFIP Claims | Claim Rate % | NFIP Mean Depth (ft) | Altis Properties | Altis % Flagged | Altis Mean Depth (ft) |
|---|---|---|---|---|---|---|
| 33952 | 228 | - | 0.86 | 735 | 0.0 | 0.00 |
| 33950 | 168 | - | 0.66 | 78 | 0.0 | 0.00 |
| 33948 | 131 | - | 0.68 | 96 | 0.0 | 0.00 |
| 33955 | 81 | - | 0.21 | 2 | 0.0 | 0.00 |
| 33953 | 67 | - | 1.28 | 1 | 0.0 | 0.00 |
| 33980 | 58 | - | 1.10 | 36 | 0.0 | 0.00 |
| 34269 | 40 | - | 3.07 | 1 | 0.0 | 0.00 |
| 33982 | 31 | - | 2.38 | 10 | 0.0 | 0.13 |
| 33983 | 29 | - | 2.76 | 22 | 0.0 | 0.00 |
| 33954 | 10 | - | 0.17 | 16 | 0.0 | 0.00 |
| 34288 | 6 | - | 2.04 | 3 | 0.0 | 0.00 |

## Calibrated Flood Probability (held-out)

- Labelled properties: **1000** (11 flooded-truth), split **grouped_by_zip** -> train 858 / test 142
- Calibration method: **platt**
- **Brier score:** 0.0702 (lower is better; 0 is perfect, 0.25 is uninformative)
- **Expected calibration error:** 0.0685 (lower is better)

### Is this better than guessing?

- Label base rate: **0.011**
- A constant predictor (base rate for everyone) scores Brier **0.0109**
- Brier skill score: **-5.4528** — this model **does NOT beat** the constant predictor.
- Properties with any flood signal: **3** of 1000

> Only 3 of 1000 properties (0.3%) have any flood signal at all. Every metric here rests on those few properties and should not be treated as a validation of the detector.

### Precision / Recall by Triage Category (held-out positive = Dispatch + Remote-Approve)

- Precision: **None**, Recall: **0.0**, F1: **None** (n=142)

| Category | n | % truly flooded | % truly dry |
|---|---|---|---|
| Dispatch | 0 | None | None |
| Remote-Approve | 0 | None | None |
| Remote-Deny | 891 | 0.8 | 99.2 |
| Review | 109 | 3.7 | 96.3 |

_Label source: OpenFEMA NFIP Redacted Claims v3 — share of NFIP claims reporting standing water >= 50.0% (no policy denominator available - weaker label). Score: 0.5*coverage_fraction + 0.5*min(depth_ft/3, 1)._
> Labels are zip-resolution, so the hold-out is grouped by zip (train and test zips disjoint) to avoid leakage. Treat these as directional, claims-anchored accuracy — not per-house verified ground truth.

## Methodology & Limitations

- NFIP claims are released at zip-code resolution. `censusTract` is empty in the v3 dataset for these events and latitude/longitude are redacted to one decimal place (~11 km), so zip is the finest honest join key. This compares zip-level aggregates, not individual properties.
- The claim population is NFIP policyholders who filed. That is much closer to a carrier's insured book than the previous ground truth (self-selected federal aid applicants), but it still excludes uninsured structures and insured structures that chose not to file.
- Reported water depth is recorded during claim settlement. It is adjuster-informed rather than instrumented, and carries the unit ambiguity described above.
- Depth above GROUND is what the detector measures; NFIP depth is reported relative to the building. Phase 2 (first-floor height from the National Structure Inventory) is what closes that gap — until then a systematic offset of roughly the foundation height is expected, and it is larger for pier and crawlspace construction than for slab.
- Labels are zip-resolution, so the calibration hold-out is grouped by zip (train and test zips disjoint) to prevent leakage.

_Generated by validation/accuracy_check.py_