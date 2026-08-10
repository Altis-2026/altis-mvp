# Altis Accuracy Validation — Hurricane Harvey — Brazos River

**Ground truth source:** OpenFEMA NFIP Redacted Claims v3 (date of loss 2017-08-27 to 2017-09-20)

**Study area:** Fort Bend County, TX

**Ground-truth label:** NFIP claims per residential structure >= 2.0% (policies-in-force denominator unavailable; structure counts substituted)

## Summary

- Zip codes compared: **15**
- NFIP claims in comparison: **3,135**
- Altis properties in comparison: **4,000**

## Correlation Metrics

The headline number is the first one: Altis's satellite-derived mean depth against the mean water depth adjusters recorded on settled insurance claims, by zip. Both sides are continuous, which is a materially stronger test than the binary agreement the previous Individual Assistance ground truth could support.

- **Mean detected depth vs mean claimed water depth**, by zip: +0.366 (moderate positive)
- Mean detected depth vs *median* claimed depth, by zip: +0.373 (moderate positive)
- % flagged flooded vs NFIP claim rate, by zip: not computable — field not available in this run
- % flagged flooded vs % claims reporting standing water, by zip: +0.105 (weak positive)
- Mean paid building claim vs mean detected depth, by zip: +0.537 (moderate positive)

## Data quality: the `waterDepth` unit ambiguity

FEMA documents `waterDepth` as inches while noting that some records were entered in feet. That note describes the dominant behaviour, not an edge case, so this validation applies an explicit rule (values <= 15 are read as feet, above that as inches) and reports the split rather than hiding it:

| Interpretation | Claims |
|---|---|
| feet | 2,823 |
| inches | 310 |
| invalid | 2 |
| **total** | **3,135** |

The rule is justified by the damage data: mean damage ratio rises monotonically with the raw value, reaching ~0.6 around raw value 6. A 60% loss at six inches is not credible; at six feet it sits on a standard one-story residential depth-damage curve.

## Zip-Level Detail

| Zip | NFIP Claims | Claim Rate % | NFIP Mean Depth (ft) | Altis Properties | Altis % Flagged | Altis Mean Depth (ft) |
|---|---|---|---|---|---|---|
| 77450 | 992 | - | 1.56 | 67 | 0.0 | 0.00 |
| 77406 | 553 | - | 1.13 | 524 | 0.2 | 0.03 |
| 77479 | 337 | - | 0.82 | 505 | 0.0 | 0.00 |
| 77072 | 202 | - | 0.44 | 119 | 0.0 | 0.00 |
| 77494 | 144 | - | 1.31 | 210 | 0.0 | 0.00 |
| 77099 | 139 | - | 0.68 | 52 | 0.0 | 0.00 |
| 77407 | 136 | - | 0.98 | 541 | 0.0 | 0.01 |
| 77471 | 122 | - | 1.79 | 311 | 0.0 | 0.00 |
| 77082 | 113 | - | 0.37 | 70 | 0.0 | 0.00 |
| 77469 | 96 | - | 0.60 | 543 | 0.0 | 0.00 |
| 77498 | 88 | - | 0.78 | 325 | 0.0 | 0.00 |
| 77083 | 71 | - | 0.52 | 484 | 0.0 | 0.00 |
| 77441 | 60 | - | 1.64 | 121 | 0.0 | 0.03 |
| 77478 | 50 | - | 0.38 | 108 | 0.0 | 0.00 |
| 77461 | 32 | - | 1.29 | 20 | 0.0 | 0.00 |

## Calibrated Flood Probability (held-out)

- Labelled properties: **3980** (832 flooded-truth), split **grouped_by_zip** -> train 3429 / test 551
- Calibration method: **isotonic**
- **Brier score:** 0.1719 (lower is better; 0 is perfect, 0.25 is uninformative)
- **Expected calibration error:** 0.0166 (lower is better)

### Is this better than guessing?

- Label base rate: **0.209**
- A constant predictor (base rate for everyone) scores Brier **0.1653**
- Brier skill score: **-0.0396** — this model **does NOT beat** the constant predictor.
- Properties with any flood signal: **116** of 3980

### Precision / Recall by Triage Category (held-out positive = Dispatch + Remote-Approve)

- Precision: **0.0**, Recall: **0.0**, F1: **None** (n=551)

| Category | n | % truly flooded | % truly dry |
|---|---|---|---|
| Dispatch | 1 | 100.0 | 0.0 |
| Remote-Approve | 0 | None | None |
| Remote-Deny | 3868 | 21.2 | 78.8 |
| Review | 111 | 9.9 | 90.1 |

_Label source: OpenFEMA NFIP Redacted Claims v3 — NFIP claims per residential structure >= 2.0% (policies-in-force denominator unavailable; structure counts substituted). Score: 0.5*coverage_fraction + 0.5*min(depth_ft/3, 1)._
> Labels are zip-resolution, so the hold-out is grouped by zip (train and test zips disjoint) to avoid leakage. Treat these as directional, claims-anchored accuracy — not per-house verified ground truth.

## Methodology & Limitations

- NFIP claims are released at zip-code resolution. `censusTract` is empty in the v3 dataset for these events and latitude/longitude are redacted to one decimal place (~11 km), so zip is the finest honest join key. This compares zip-level aggregates, not individual properties.
- The claim population is NFIP policyholders who filed. That is much closer to a carrier's insured book than the previous ground truth (self-selected federal aid applicants), but it still excludes uninsured structures and insured structures that chose not to file.
- Reported water depth is recorded during claim settlement. It is adjuster-informed rather than instrumented, and carries the unit ambiguity described above.
- Depth above GROUND is what the detector measures; NFIP depth is reported relative to the building. Phase 2 (first-floor height from the National Structure Inventory) is what closes that gap — until then a systematic offset of roughly the foundation height is expected, and it is larger for pier and crawlspace construction than for slab.
- Labels are zip-resolution, so the calibration hold-out is grouped by zip (train and test zips disjoint) to prevent leakage.

_Generated by validation/accuracy_check.py_