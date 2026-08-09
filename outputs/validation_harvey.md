# Altis Accuracy Validation — Hurricane Harvey

**Ground truth source:** OpenFEMA NFIP Redacted Claims v3 (date of loss 2017-08-25 to 2017-09-15)

**Study area:** Harris County, TX

**Ground-truth label:** share of NFIP claims reporting standing water >= 50.0% (no policy denominator available - weaker label)

## Summary

- Zip codes compared: **39**
- NFIP claims in comparison: **16,578**
- Altis properties in comparison: **1,000**

## Correlation Metrics

The headline number is the first one: Altis's satellite-derived mean depth against the mean water depth adjusters recorded on settled insurance claims, by zip. Both sides are continuous, which is a materially stronger test than the binary agreement the previous Individual Assistance ground truth could support.

- **Mean detected depth vs mean claimed water depth**, by zip: not computable — `altis_mean_depth_ft` is constant across all 39 zips (= 0.0) — the detector returned the same value everywhere, so there is nothing to correlate
- Mean detected depth vs *median* claimed depth, by zip: not computable — `altis_mean_depth_ft` is constant across all 39 zips (= 0.0) — the detector returned the same value everywhere, so there is nothing to correlate
- % flagged flooded vs NFIP claim rate, by zip: not computable — field not available in this run
- % flagged flooded vs % claims reporting standing water, by zip: not computable — `altis_pct_flagged` is constant across all 39 zips (= 0.0) — the detector returned the same value everywhere, so there is nothing to correlate
- Mean paid building claim vs mean detected depth, by zip: not computable — `altis_mean_depth_ft` is constant across all 39 zips (= 0.0) — the detector returned the same value everywhere, so there is nothing to correlate

## Data quality: the `waterDepth` unit ambiguity

FEMA documents `waterDepth` as inches while noting that some records were entered in feet. That note describes the dominant behaviour, not an edge case, so this validation applies an explicit rule (values <= 15 are read as feet, above that as inches) and reports the split rather than hiding it:

| Interpretation | Claims |
|---|---|
| feet | 15,089 |
| inches | 1,478 |
| invalid | 11 |
| **total** | **16,578** |

The rule is justified by the damage data: mean damage ratio rises monotonically with the raw value, reaching ~0.6 around raw value 6. A 60% loss at six inches is not credible; at six feet it sits on a standard one-story residential depth-damage curve.

## Zip-Level Detail

| Zip | NFIP Claims | Claim Rate % | NFIP Mean Depth (ft) | Altis Properties | Altis % Flagged | Altis Mean Depth (ft) |
|---|---|---|---|---|---|---|
| 77096 | 2511 | - | 1.80 | 120 | 0.0 | 0.00 |
| 77401 | 2088 | - | 1.38 | 94 | 0.0 | 0.00 |
| 77079 | 1723 | - | 2.79 | 20 | 0.0 | 0.00 |
| 77025 | 1351 | - | 1.69 | 30 | 0.0 | 0.00 |
| 77074 | 1146 | - | 1.70 | 15 | 0.0 | 0.00 |
| 77024 | 876 | - | 2.45 | 38 | 0.0 | 0.00 |
| 77035 | 856 | - | 1.43 | 5 | 0.0 | 0.00 |
| 77077 | 693 | - | 1.65 | 3 | 0.0 | 0.00 |
| 77008 | 691 | - | 1.44 | 11 | 0.0 | 0.00 |
| 77042 | 507 | - | 1.94 | 9 | 0.0 | 0.00 |
| 77063 | 369 | - | 1.53 | 26 | 0.0 | 0.00 |
| 77072 | 353 | - | 0.54 | 8 | 0.0 | 0.00 |
| 77099 | 275 | - | 0.72 | 7 | 0.0 | 0.00 |
| 77071 | 259 | - | 1.23 | 7 | 0.0 | 0.00 |
| 77007 | 247 | - | 1.55 | 96 | 0.0 | 0.00 |
| 77056 | 247 | - | 2.78 | 88 | 0.0 | 0.00 |
| 77009 | 237 | - | 1.88 | 1 | 0.0 | 0.00 |
| 77004 | 234 | - | 0.95 | 5 | 0.0 | 0.00 |
| 77005 | 213 | - | 0.83 | 32 | 0.0 | 0.00 |
| 77047 | 197 | - | 0.82 | 41 | 0.0 | 0.00 |
| 77030 | 171 | - | 0.84 | 27 | 0.0 | 0.00 |
| 77043 | 121 | - | 0.77 | 10 | 0.0 | 0.00 |
| 77057 | 120 | - | 2.25 | 70 | 0.0 | 0.00 |
| 77045 | 116 | - | 0.58 | 2 | 0.0 | 0.00 |
| 77031 | 105 | - | 1.03 | 4 | 0.0 | 0.00 |
| 77081 | 104 | - | 0.62 | 11 | 0.0 | 0.00 |
| 77019 | 101 | - | 2.47 | 32 | 0.0 | 0.00 |
| 77489 | 100 | - | 0.74 | 2 | 0.0 | 0.00 |
| 77055 | 88 | - | 0.91 | 25 | 0.0 | 0.00 |
| 77478 | 74 | - | 0.35 | 6 | 0.0 | 0.00 |
| 77036 | 67 | - | 0.56 | 20 | 0.0 | 0.00 |
| 77477 | 65 | - | 0.66 | 4 | 0.0 | 0.00 |
| 77027 | 63 | - | 2.70 | 35 | 0.0 | 0.00 |
| 77006 | 58 | - | 0.65 | 22 | 0.0 | 0.00 |
| 77098 | 42 | - | 0.74 | 43 | 0.0 | 0.00 |
| 77054 | 37 | - | 0.58 | 21 | 0.0 | 0.00 |
| 77051 | 36 | - | 0.60 | 1 | 0.0 | 0.00 |
| 77085 | 28 | - | 0.50 | 6 | 0.0 | 0.00 |
| 77046 | 9 | - | -2.67 | 3 | 0.0 | 0.00 |

## Calibrated Flood Probability (held-out)

**No calibration was fitted, and no calibrator file was written.**

- Labelled properties: **1000** (942 flooded-truth)
- Distinct `raw_flood_score` values: **1** (constant at 0.0)
- Label base rate: **0.942**

With a constant score, any fitted calibrator emits a single number and its Brier score is `p(1-p)` = **0.0546** by construction — a restatement of the base rate, not a measure of detection accuracy.

> Calibration not fitted: the detector produced an identical score for every property, so no probability model is identifiable. Any Brier score here would measure label prevalence, not accuracy. See docs/DETECTION_LIMITS.md.


## Methodology & Limitations

- NFIP claims are released at zip-code resolution. `censusTract` is empty in the v3 dataset for these events and latitude/longitude are redacted to one decimal place (~11 km), so zip is the finest honest join key. This compares zip-level aggregates, not individual properties.
- The claim population is NFIP policyholders who filed. That is much closer to a carrier's insured book than the previous ground truth (self-selected federal aid applicants), but it still excludes uninsured structures and insured structures that chose not to file.
- Reported water depth is recorded during claim settlement. It is adjuster-informed rather than instrumented, and carries the unit ambiguity described above.
- Depth above GROUND is what the detector measures; NFIP depth is reported relative to the building. Phase 2 (first-floor height from the National Structure Inventory) is what closes that gap — until then a systematic offset of roughly the foundation height is expected, and it is larger for pier and crawlspace construction than for slab.
- Labels are zip-resolution, so the calibration hold-out is grouped by zip (train and test zips disjoint) to prevent leakage.

_Generated by validation/accuracy_check.py_