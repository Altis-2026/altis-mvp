"""
event_image.py — Build the combined flood-depth image for a configured event.

WHY THIS MODULE EXISTS
----------------------
Assembling the detector for one event is not a one-liner: it is a DEM, a
pre/post SAR pair on a chosen orbit, a 12-month multi-temporal baseline, the VH
channel with its OWN baseline, every other orbit with coverage stacked in, HAND,
and an optical cross-check — each with its own abstention rule. That assembly
used to live inline in 03_flood_pipeline.py, which meant any other caller
wanting "the real detector, exactly as the pipeline runs it" had to copy it.

This repo has already paid for that class of mistake once: Phase 4b was
computed correctly and then silently dropped because one output list did not
mention it, costing a full 4,000-property run. A copied 130-line assembly is
the same failure mode with more surface area — a validation script that drifts
from the pipeline measures a detector nobody ships.

So the assembly lives here, once, and both callers import it:
  - pipeline/03_flood_pipeline.py  — the batch run that writes the CSVs
  - validation/hwm_check.py        — point-level validation against surveyed
                                     USGS high water marks

The caller is still responsible for ee.Initialize() (see flood_detect.py).
"""
try:
    from config import BASELINE, CROSS_ORBIT, DUALPOL
except ImportError:  # pragma: no cover - import path guard
    from pipeline.config import BASELINE, CROSS_ORBIT, DUALPOL

try:
    from flood_detect import (
        load_dem, load_sar_composite, load_optical_water_mask,
        build_flood_depth_image, load_sar_baseline, load_hand,
        load_sar_orbits, baseline_window, load_sar_vh_composite,
    )
except ImportError:  # pragma: no cover - import path guard
    from pipeline.flood_detect import (
        load_dem, load_sar_composite, load_optical_water_mask,
        build_flood_depth_image, load_sar_baseline, load_hand,
        load_sar_orbits, baseline_window, load_sar_vh_composite,
    )


def acquisition_times_utc(bbox, start_date, end_date, orbit_passes=None):
    """
    Every Sentinel-1 acquisition time in the window, as naive-UTC ISO strings.

    Needed because "did we see the crest?" cannot be answered from a date
    range — only from the instants the sensor actually looked. Restricted to
    the orbit passes the detector used, so the answer describes the imagery
    the result was built from rather than everything in the archive.
    """
    import datetime as dt
    import ee

    coll = (ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(ee.Geometry.Rectangle(bbox))
            .filterDate(start_date, end_date)
            .filter(ee.Filter.eq('instrumentMode', 'IW'))
            .filter(ee.Filter.listContains(
                'transmitterReceiverPolarisation', 'VV')))
    if orbit_passes:
        coll = coll.filter(ee.Filter.inList(
            'orbitProperties_pass', list(orbit_passes)))
    stamps = coll.aggregate_array('system:time_start').getInfo() or []
    seen = []
    for t in sorted(set(stamps)):
        iso = dt.datetime.utcfromtimestamp(t / 1000).replace(
            microsecond=0).isoformat()
        if iso not in seen:
            seen.append(iso)
    return seen


def build_event_image(event_config, verbose=True):
    """
    Assemble the combined flood-depth image for one event configuration.

    Returns (combined_image, meta) where `meta` carries every provenance value
    the caller needs for a run manifest — scene counts, the chosen orbit, the
    baseline window and whether it was usable, which orbits stacked in, the
    HAND source, and whether dual-pol was active or abstained.

    `meta` deliberately distinguishes "measured, nothing there" from "not
    measured": `dualpol_active` False means the VH baseline was too thin to
    trust, which is not the same claim as a dual-pol score of zero.
    """
    def say(msg):
        if verbose:
            print(msg, flush=True)

    bbox = event_config['bbox']

    say("\nLoading DEM (3DEP with building mask)...")
    dem, dem_res = load_dem(bbox)

    say("Loading pre-event Sentinel-1...")
    pre_image, pre_count, orbit = load_sar_composite(
        bbox, event_config['pre_start'], event_config['pre_end'])
    say(f"  {pre_count} scenes, orbit: {orbit}")

    say("Loading post-event Sentinel-1...")
    post_image, post_count, _ = load_sar_composite(
        bbox, event_config['post_start'], event_config['post_end'],
        orbit_pass=orbit)
    say(f"  {post_count} scenes")

    say("Loading Sentinel-2 optical cross-check (post-event window)...")
    optical_water, optical_valid, optical_count = load_optical_water_mask(
        bbox, event_config['post_start'], event_config['post_end'])
    if optical_count > 0:
        say(f"  {optical_count} cloud-filtered S2 scenes available for cross-check")
    else:
        say("  No cloud-free S2 scenes in window — optical cross-check unavailable "
            "(expected immediately after a storm; SAR-only result is unaffected)")

    # ── Phase 1a: multi-temporal baseline. A year of same-orbit pre-event
    #    scenes gives a per-pixel mean and variance, so "is this anomalous?"
    #    replaces "is this darker than one earlier picture?".
    base_start, base_end = baseline_window(event_config['post_start'])
    say(f"\nBuilding multi-temporal baseline ({base_start} → {base_end}, "
        f"orbit {orbit})...")
    baseline_mean, baseline_std, baseline_n = load_sar_baseline(
        bbox, base_start, base_end, orbit)
    if baseline_n >= BASELINE['min_scenes']:
        say(f"  {baseline_n} baseline scenes — z-score change detection active "
            f"(threshold {BASELINE['z_threshold']}σ)")
    else:
        say(f"  Only {baseline_n} baseline scenes (need "
            f"{BASELINE['min_scenes']}) — falling back to single pre-event "
            f"composite")
        baseline_mean = baseline_std = None

    # ── Phase 4b: the VH channel, with its OWN baseline. VH's normal level and
    #    normal variability are nothing like VV's on the same pixel, so a
    #    shared baseline would be meaningless; each channel is measured against
    #    its own history and only the normalised evidence is compared.
    post_vh = vh_base_mean = vh_base_std = None
    vh_base_n = vh_post_n = 0
    if DUALPOL.get('enabled', True):
        say(f"\nLoading VH channel for dual-pol discrimination (orbit {orbit})...")
        post_vh, vh_post_n = load_sar_vh_composite(
            bbox, event_config['post_start'], event_config['post_end'], orbit)
        if post_vh is None:
            say("  No VH-capable post-event scene — dual-pol abstains")
        else:
            vh_base_mean, vh_base_std, vh_base_n = load_sar_baseline(
                bbox, base_start, base_end, orbit, band='VH')
            if vh_base_n < DUALPOL['min_vh_baseline_scenes']:
                say(f"  Only {vh_base_n} VH baseline scenes (need "
                    f"{DUALPOL['min_vh_baseline_scenes']}) — dual-pol abstains "
                    f"rather than trusting a noisy σ")
                vh_base_mean = vh_base_std = None
            else:
                say(f"  {vh_post_n} VH post scenes, {vh_base_n} VH baseline "
                    f"scenes — dual-pol water score active")

    # ── Phase 1c: cross-orbit stacking. Every other orbit with post-event
    #    coverage contributes its own independently-thresholded mask, which is
    #    what shrinks the revisit gap.
    orbit_stack = {}
    if CROSS_ORBIT['enabled']:
        all_orbits = load_sar_orbits(
            bbox, event_config['post_start'], event_config['post_end'])
        for other, (composite, n_scenes) in all_orbits.items():
            if other == orbit:
                continue
            try:
                o_pre, _, _ = load_sar_composite(
                    bbox, event_config['pre_start'], event_config['pre_end'],
                    orbit_pass=other)
            except ValueError:
                # No pre-event scene on this orbit. Harmless: the baseline
                # below is the primary reference, and orbit_flood_mask falls
                # back to the absolute threshold if neither exists.
                o_pre = None
            o_base_mean, o_base_std, o_base_n = load_sar_baseline(
                bbox, base_start, base_end, other)
            if o_base_n < BASELINE['min_scenes']:
                o_base_mean = o_base_std = None
            spec = {
                'post': composite, 'pre': o_pre,
                'baseline_mean': o_base_mean, 'baseline_std': o_base_std,
            }
            # This orbit's VH channel, again against its own VH baseline. Only
            # a complete set enables dual-pol for the orbit; a partial one is
            # left out entirely so it cannot contribute a spurious zero.
            if DUALPOL.get('enabled', True):
                o_post_vh, _ = load_sar_vh_composite(
                    bbox, event_config['post_start'],
                    event_config['post_end'], other)
                if o_post_vh is not None:
                    o_vh_mean, o_vh_std, o_vh_n = load_sar_baseline(
                        bbox, base_start, base_end, other, band='VH')
                    if o_vh_n >= DUALPOL['min_vh_baseline_scenes']:
                        spec.update({'post_vh': o_post_vh,
                                     'vh_baseline_mean': o_vh_mean,
                                     'vh_baseline_std': o_vh_std})
            orbit_stack[other] = spec
            say(f"  Cross-orbit: {other} contributes {n_scenes} post scenes "
                f"({o_base_n} baseline scenes"
                f"{', dual-pol' if 'post_vh' in spec else ''})")
        if not orbit_stack:
            say("  Cross-orbit: no second orbit with coverage in this window")

    # ── Phase 1b: HAND replaces the neighbourhood-minimum elevation heuristic
    #    as the DEM-hydrology plausibility vote.
    hand_img, hand_source = load_hand(bbox)
    say(f"  HAND source: {hand_source}")

    say("\nBuilding flood map (Otsu threshold + slope mask)...")
    combined = build_flood_depth_image(
        bbox, pre_image, post_image, dem, event_config['wse_radius_m'],
        optical_water=optical_water, optical_valid=optical_valid,
        hand=hand_img, baseline_mean=baseline_mean, baseline_std=baseline_std,
        orbit_stack=orbit_stack,
        post_vh=post_vh,
        vh_baseline_mean=vh_base_mean, vh_baseline_std=vh_base_std,
        # Resolve each orbit's Otsu threshold once instead of recomputing that
        # whole-bbox histogram on every sampling batch (see guarded_otsu).
        precompute_thresholds=True)

    # Acquisition instants, for the crest-timing disclosure. Restricted to the
    # orbits actually used so the answer describes this result's imagery.
    used_orbits = sorted({orbit} | set(orbit_stack.keys()))
    try:
        acq_times = acquisition_times_utc(
            bbox, event_config['post_start'], event_config['post_end'],
            orbit_passes=used_orbits)
    except Exception as exc:  # pragma: no cover - provenance must not fail a run
        say(f"  Could not read acquisition times ({exc}); crest timing will "
            f"report 'unknown' rather than assume the crest was seen.")
        acq_times = []

    meta = {
        'dem_resolution_m':        dem_res,
        'acquisition_times_utc':   acq_times,
        'sar_orbit_pass':          orbit,
        'pre_event_scene_count':   pre_count,
        'post_event_scene_count':  post_count,
        'optical_scene_count':     optical_count,
        'baseline_window':         [base_start, base_end],
        'baseline_scene_count':    baseline_n,
        # True only when the multi-temporal baseline was thick enough to use.
        # Several downstream provenance strings key off this rather than off a
        # scene count, so the distinction is recorded once, here.
        'baseline_active':         baseline_mean is not None,
        'cross_orbit_passes':      sorted([orbit] + list(orbit_stack.keys())),
        'cross_orbit_combine':     CROSS_ORBIT['combine'] if orbit_stack else 'n/a',
        'hand_source':             hand_source,
        'dualpol_enabled':         bool(DUALPOL.get('enabled', True)),
        'dualpol_active':          vh_base_mean is not None,
        'vh_post_scene_count':     vh_post_n,
        'vh_baseline_scene_count': vh_base_n,
    }
    return combined, meta
