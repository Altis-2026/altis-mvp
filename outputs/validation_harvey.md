# Altis Accuracy Validation — Hurricane Harvey

**Ground truth source:** OpenFEMA NFIP Redacted Claims v3 (date of loss 2017-08-25 to 2017-09-15)

**Study area:** Harris County, TX

**Ground-truth label:** NFIP claims per residential structure >= 2.0% (policies-in-force denominator unavailable; structure counts substituted)

## Summary

- Zip codes compared: **55**
- NFIP claims in comparison: **24,219**
- Altis properties in comparison: **4,000**

## Correlation Metrics

The headline number is the first one: Altis's satellite-derived mean depth against the mean water depth adjusters recorded on settled insurance claims, by zip. Both sides are continuous, which is a materially stronger test than the binary agreement the previous Individual Assistance ground truth could support.

- **Mean detected depth vs mean claimed water depth**, by zip: -0.011 (weak negative)
- Mean detected depth vs *median* claimed depth, by zip: -0.017 (weak negative)
- % flagged flooded vs NFIP claim rate, by zip: not computable — field not available in this run
- % flagged flooded vs % claims reporting standing water, by zip: not computable — `altis_pct_flagged` is constant across all 55 zips (= 0.0) — the detector returned the same value everywhere, so there is nothing to correlate
- Mean paid building claim vs mean detected depth, by zip: +0.012 (weak positive)

## Data quality: the `waterDepth` unit ambiguity

FEMA documents `waterDepth` as inches while noting that some records were entered in feet. That note describes the dominant behaviour, not an edge case, so this validation applies an explicit rule (values <= 15 are read as feet, above that as inches) and reports the split rather than hiding it:

| Interpretation | Claims |
|---|---|
| feet | 21,958 |
| inches | 2,247 |
| invalid | 14 |
| **total** | **24,219** |

The rule is justified by the damage data: mean damage ratio rises monotonically with the raw value, reaching ~0.6 around raw value 6. A 60% loss at six inches is not credible; at six feet it sits on a standard one-story residential depth-damage curve.

## Zip-Level Detail

| Zip | NFIP Claims | Claim Rate % | NFIP Mean Depth (ft) | Altis Properties | Altis % Flagged | Altis Mean Depth (ft) |
|---|---|---|---|---|---|---|
| 77096 | 2511 | - | 1.80 | 64 | 0.0 | 0.00 |
| 77401 | 2088 | - | 1.38 | 27 | 0.0 | 0.00 |
| 77079 | 1723 | - | 2.79 | 64 | 0.0 | 0.00 |
| 77084 | 1577 | - | 2.15 | 214 | 0.0 | 0.00 |
| 77429 | 1205 | - | 1.54 | 142 | 0.0 | 0.00 |
| 77074 | 1146 | - | 1.70 | 46 | 0.0 | 0.00 |
| 77450 | 1113 | - | 1.53 | 162 | 0.0 | 0.00 |
| 77070 | 899 | - | 2.19 | 74 | 0.0 | 0.00 |
| 77024 | 876 | - | 2.45 | 67 | 0.0 | 0.00 |
| 77035 | 856 | - | 1.43 | 5 | 0.0 | 0.00 |
| 77077 | 693 | - | 1.65 | 94 | 0.0 | 0.00 |
| 77088 | 682 | - | 1.26 | 46 | 0.0 | 0.00 |
| 77406 | 656 | - | 1.12 | 61 | 0.0 | 0.08 |
| 77379 | 580 | - | 2.63 | 5 | 0.0 | 0.00 |
| 77041 | 508 | - | 1.12 | 77 | 0.0 | 0.00 |
| 77042 | 507 | - | 1.94 | 37 | 0.0 | 0.00 |
| 77493 | 460 | - | 1.34 | 206 | 0.0 | 0.15 |
| 77063 | 369 | - | 1.53 | 35 | 0.0 | 0.00 |
| 77072 | 353 | - | 0.54 | 95 | 0.0 | 0.00 |
| 77091 | 307 | - | 1.10 | 19 | 0.0 | 0.00 |
| 77433 | 292 | - | 1.53 | 239 | 0.0 | 0.01 |
| 77099 | 275 | - | 0.72 | 52 | 0.0 | 0.00 |
| 77040 | 265 | - | 0.85 | 88 | 0.0 | 0.00 |
| 77092 | 263 | - | 1.21 | 39 | 0.0 | 0.00 |
| 77071 | 259 | - | 1.23 | 10 | 0.0 | 0.00 |
| 77449 | 247 | - | 1.20 | 323 | 0.0 | 0.04 |
| 77056 | 247 | - | 2.78 | 27 | 0.0 | 0.00 |
| 77065 | 221 | - | 0.97 | 45 | 0.0 | 0.00 |
| 77038 | 219 | - | 1.30 | 13 | 0.0 | 0.00 |
| 77069 | 215 | - | 2.45 | 42 | 0.0 | 0.00 |
| 77068 | 210 | - | 3.43 | 7 | 0.0 | 0.00 |
| 77082 | 198 | - | 0.43 | 93 | 0.0 | 0.00 |
| 77494 | 188 | - | 1.25 | 291 | 0.0 | 0.01 |
| 77407 | 163 | - | 0.96 | 154 | 0.0 | 0.00 |
| 77094 | 143 | - | 1.18 | 23 | 0.0 | 0.00 |
| 77095 | 125 | - | 0.91 | 163 | 0.0 | 0.00 |
| 77043 | 121 | - | 0.77 | 54 | 0.0 | 0.00 |
| 77057 | 120 | - | 2.25 | 37 | 0.0 | 0.00 |
| 77066 | 120 | - | 0.81 | 76 | 0.0 | 0.00 |
| 77080 | 116 | - | 0.81 | 78 | 0.0 | 0.00 |
| 77083 | 112 | - | 0.55 | 140 | 0.0 | 0.00 |
| 77498 | 107 | - | 0.72 | 27 | 0.0 | 0.00 |
| 77031 | 105 | - | 1.03 | 15 | 0.0 | 0.00 |
| 77081 | 104 | - | 0.62 | 22 | 0.0 | 0.00 |
| 77055 | 88 | - | 0.91 | 66 | 0.0 | 0.00 |
| 77447 | 84 | - | 1.68 | 8 | 0.0 | 0.00 |
| 77478 | 74 | - | 0.35 | 2 | 0.0 | 0.00 |
| 77441 | 73 | - | 1.53 | 40 | 0.0 | 0.00 |
| 77377 | 67 | - | 0.89 | 1 | 0.0 | 0.00 |
| 77036 | 67 | - | 0.56 | 59 | 0.0 | 0.00 |
| 77477 | 65 | - | 0.66 | 5 | 0.0 | 0.00 |
| 77064 | 52 | - | 0.48 | 107 | 0.0 | 0.00 |
| 77067 | 47 | - | 0.39 | 18 | 0.0 | 0.00 |
| 77086 | 41 | - | 0.80 | 60 | 0.0 | 0.00 |
| 77014 | 17 | - | 0.40 | 36 | 0.0 | 0.00 |

## Calibrated Flood Probability (held-out)

- Labelled properties: **3766** (1581 flooded-truth), split **grouped_by_zip** -> train 2471 / test 1295
- Calibration method: **isotonic**
- **Brier score:** 0.2565 (lower is better; 0 is perfect, 0.25 is uninformative)
- **Expected calibration error:** 0.084 (lower is better)

### Is this better than guessing?

- Label base rate: **0.4198**
- A constant predictor (base rate for everyone) scores Brier **0.2436**
- Brier skill score: **-0.0531** — this model **does NOT beat** the constant predictor.
- Properties with any flood signal: **51** of 3766

> Only 51 of 3766 properties (1.4%) have any flood signal at all. Every metric here rests on those few properties and should not be treated as a validation of the detector.

### Precision / Recall by Triage Category (held-out positive = Dispatch + Remote-Approve)

- Precision: **None**, Recall: **0.0**, F1: **None** (n=1295)

| Category | n | % truly flooded | % truly dry |
|---|---|---|---|
| Dispatch | 0 | None | None |
| Remote-Approve | 0 | None | None |
| Remote-Deny | 3664 | 42.2 | 57.8 |
| Review | 102 | 32.4 | 67.6 |

_Label source: OpenFEMA NFIP Redacted Claims v3 — NFIP claims per residential structure >= 2.0% (policies-in-force denominator unavailable; structure counts substituted). Score: 0.5*coverage_fraction + 0.5*min(depth_ft/3, 1)._
> Labels are zip-resolution, so the hold-out is grouped by zip (train and test zips disjoint) to avoid leakage. Treat these as directional, claims-anchored accuracy — not per-house verified ground truth.

## Methodology & Limitations

- NFIP claims are released at zip-code resolution. `censusTract` is empty in the v3 dataset for these events and latitude/longitude are redacted to one decimal place (~11 km), so zip is the finest honest join key. This compares zip-level aggregates, not individual properties.
- The claim population is NFIP policyholders who filed. That is much closer to a carrier's insured book than the previous ground truth (self-selected federal aid applicants), but it still excludes uninsured structures and insured structures that chose not to file.
- Reported water depth is recorded during claim settlement. It is adjuster-informed rather than instrumented, and carries the unit ambiguity described above.
- Depth above GROUND is what the detector measures; NFIP depth is reported relative to the building. Phase 2 (first-floor height from the National Structure Inventory) is what closes that gap — until then a systematic offset of roughly the foundation height is expected, and it is larger for pier and crawlspace construction than for slab.
- Labels are zip-resolution, so the calibration hold-out is grouped by zip (train and test zips disjoint) to prevent leakage.

_Generated by validation/accuracy_check.py_