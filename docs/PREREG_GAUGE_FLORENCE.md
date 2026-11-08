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
