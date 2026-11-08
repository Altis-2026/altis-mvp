# Pre-registration: analogs backtest v2 (inflation + storm rainfall)

Written and committed 2026-11-08 BEFORE the v2 run. v1 result (nominal dollars,
no rainfall) is preserved in `outputs/analog_backtest_v1.json`: analogs did not
beat the baseline (payout error difference CI -0.037..+0.031 log10).

## Question
Do analog-based priors predict an event's insured outcomes better than a
naive guess once (a) payouts are in constant dollars and (b) the storm's
rainfall total is part of the similarity?

## Data and protocol (identical to v1 except the two changes below)
- Events: FEMA-library events with >= 100 NFIP claims, a median paid amount
  and a share paid (231 at time of writing). Leave-one-out; the queried event
  excluded from its own analogs; US analogs only; k = 8.
- Query inputs: where the event's claims were (centroid), start date, cause,
  and now its rainfall. These are all known at the time of a live event.
- Predicted: similarity-weighted median payout per paid claim; share of claims
  paid. Baseline: median over all other events (same definition as v1).

## The only two changes (fixed now; no tuning afterwards)
1. **Constant dollars.** Every payout (analog, baseline, actual) converted to
   September 2026 dollars with CPI-U all items (FRED CPIAUCSL), using the
   month of the event start.
2. **Rainfall term.** IMERG V07 precipitation (NASA GPM) summed over the 7
   days from event start, area-mean over a 50 km radius around the claims
   centroid, in mm. Similarity = exp(-|ln(a/b)|), weight 1.0 (same as the size
   and duration terms). Applied only when both events have a value.

## Metrics
- **Primary:** median absolute log10 error of predicted vs actual payout (real
  dollars). Difference analogs-minus-baseline, 95% bootstrap CI over events
  (2000 resamples, seed 0).
- Secondary: share-paid absolute error difference; Spearman rank correlations.
- Ablations reported: location only, cause only, and "full without rainfall"
  (inflation only) to show what each change contributes.

## Decision rule
- **Analogs are presented as a forecast ONLY IF the primary CI lies entirely
  below 0.** Otherwise the UI keeps "history, not a forecast".
- Run exactly once. No change to weights, k, windows or filters after seeing
  results; any further idea is a new pre-registration.

---

## Result (single run, 2026-11-08, after this file and the code were committed)

Output: `outputs/analog_backtest.json` (headline numbers verified identical when
descriptive error quantiles were added afterwards). 231 events, leave-one-out.

| | Analogs | Baseline | Difference, 95% CI |
|---|---|---|---|
| **Primary: payout error (median abs log10, real dollars)** | 0.201 | 0.229 | **-0.061 to +0.025** |
| Share-paid error (abs) | 0.057 | 0.063 | -0.017 to +0.003 |
| Rank correlation, payout / share paid | 0.34 / 0.39 | n/a | n/a |

Ablations (payout error diff CI): full without rainfall -0.059..+0.020;
location only -0.060..+0.020; cause only +0.020..+0.107 (worse than baseline).

**Decision rule: NOT MET.** The primary CI includes 0, so analogs are **not**
presented as a forecast. They remain labelled history.

What changed versus v1 (nominal, no rain; error 0.257 vs baseline 0.259):
analog error fell to 0.201 and baseline error to 0.229. Most of the gain is
from putting payouts in constant dollars (it helps both sides); rainfall adds a
small further gain on share paid (0.065 -> 0.057) and payout (0.205 -> 0.201).
The point estimate now favours analogs by about 12%, but the sample cannot
distinguish that from chance.

Product consequence (descriptive, not part of the decision): every analog
estimate is now shown in today's dollars with its measured error band
(typically off by about 1.6x; within 2.6x in 80% of past events), plus the
baseline's typical error (1.7x), and the "history, not a forecast" label
stays. The flag flips automatically if a future pre-registered run passes.

Not allowed by this protocol, and so not done: tuning weights, k or windows.
Next candidates, each a NEW pre-registration: storm wind/surge for coastal
events (Katrina and Sandy have little rain), exposure at risk instead of claim
counts, and more events (NFIP back to 1978 for events before 2000).
