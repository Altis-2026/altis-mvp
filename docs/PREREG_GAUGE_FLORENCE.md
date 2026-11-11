# Pre-registration: gauge-depth test on Hurricane Florence (2018)

Written and committed 2026-11-08 BEFORE running the test, per roadmap B1.

- **Question:** does river-gauge physics depth (backend/gauges.py, unchanged
  logic) find water at surveyed flood sites on an event never used to build it?
- **Ground truth:** every USGS STN high-water mark for event 283 ("2018
  Florence") with environment = Riverine, a surveyed height above ground > 0
  and an elevation. 503 marks at the time of writing. No region is chosen:
  all of them are used.
- **Method:** `gauge_depth_for_points` exactly as in the product: NWS crest in
  the window 2018-09-13 .. 2018-10-05, NAVD88 datums, reservoir/lake pool
  gauges excluded, along-channel interpolation, product rule = within 3 km of
  a gauged channel. Ground = USGS 3DEP 1 m lidar, 10 m where 1 m is missing
  (also applied to the product in the same commit).
- **Unit:** distinct survey sites; Wilson 95% CIs.
- **Pass criteria (both):**
  1. site-recall 95% CI lower bound above 18.5% (the upper bound of radar's
     Brazos site recall), within the 3 km product rule;
  2. mean depth bias within +/- 1.5 ft within the 3 km product rule.
- **Will be reported regardless of outcome**, including marks beyond 3 km.
- **Not tested here:** radar at the same marks (needs a pipeline run over
  the whole region); precision (marks have no dry class).

---

## Result (run 2026-11-08, after this file was committed in 30ba7d36)

Output: `outputs/gauge_depth_check_florence.json`. 496 riverine marks at 470
sites had a usable estimate input; ground from 3DEP (1 m, 10 m fallback).

| Subset | Sites | Site recall (95% CI) | Bias | MAE | r |
|---|---|---|---|---|---|
| Within 1 km of a gauged channel | 133 | 62% (53-69%) | +0.31 ft | 2.76 ft | 0.32 |
| **Product rule, within 3 km** | **221** | **59% (52-65%)** | **+1.01 ft** | 3.65 ft | 0.04 |
| Within 5 km | 257 | 60% (54-66%) | +2.13 ft | 4.77 ft | -0.03 |
| All marks (no distance cap) | 470 | 57% (53-62%) | +5.26 ft | 7.91 ft | 0.03 |

**Pre-registered criteria: both PASS** (recall CI lower bound 52% > 18.5%;
bias +1.01 ft within +/-1.5 ft).

**What it does not show:** depth *magnitude* skill is weak at the 3 km rule
(r = 0.04); it is moderate only within 1 km (r = 0.32). Overestimation grows
quickly with distance from the gauged channel, which confirms the cap is
necessary. Radar was not run on these marks, so no head-to-head here.
Plain reading: on an independent event, gauge physics reliably says *water
reached this spot* near gauged rivers; it should not be sold as a precise
depth beyond about 1 km from the channel.
