"""
08_bake_flood_animation.py — The flood in motion, for the demo/presentation mode.

    python pipeline/08_bake_flood_animation.py [event_id ...]

The router already computes the whole water surface at every timestep; the
intel bake keeps only the per-property numbers. This re-runs it with the SAME
calibrated forcing multiplier recorded in outputs/{event}_intel.json and keeps
the depth field every hour, so the app can play the flood forward and back in
time — including the days the satellite never saw.

Output (per event):
  outputs/{event}_flood.bin   frames × rows × cols, uint8, depth in 10 cm steps
  outputs/{event}_flood.json  metadata: bounds, shape, times, the satellite
                              pass index, and the calibration that produced it

uint8 at 10 cm is deliberate: it is the honest precision of a 500 m routed
depth, it keeps the payload small (the file gzips to a few hundred kB because
most of the grid is dry), and 0 means dry so the frontend can skip it.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from backend import routing as R  # noqa: E402
from pipeline.config import HARVEY, IAN, LISMORE, ROUTER  # noqa: E402

CONFIGS = {c['event_id']: c for c in (HARVEY, IAN, LISMORE)}
FRAME_HOURS = 1.0
DEPTH_STEP_M = 0.1
MAX_DEPTH_M = 25.4           # 254 × 10 cm; 255 is reserved for "deeper than this"

# How much of the routed catchment to keep, as a margin in degrees around the
# study box — wide enough to watch the flood wave arrive from upstream.
VIEW_PAD_DEG = 0.14


def bake(event_id: str):
    cfg = CONFIGS[event_id]
    intel_path = ROOT / 'outputs' / f'{event_id}_intel.json'
    intel = json.loads(intel_path.read_text()) if intel_path.exists() else {}
    router = (intel.get('event') or {}).get('router')
    if not router:
        print(f'  {event_id}: no calibrated router in the intel bake — skipping '
              f'(SAR saw too little flooding to calibrate against).')
        return
    k = float(router['calibrated_k'])
    pass_time = datetime.fromisoformat(router['pass_time'])
    t0 = datetime.fromisoformat(router['sim_start'])
    t_end = datetime.fromisoformat(router['sim_end'])
    res_m = float(router['res_final_m'])
    domain = router['domain_bbox']

    print(f'▶ {event_id}: replaying the calibrated run (k={k}, {res_m:.0f} m grid, '
          f'{t0:%d %b} → {t_end:%d %b})')
    zg, channel = R._router_dem(domain, res_m)
    day0 = t0.date()
    ndays = (t_end.date() - day0).days + 2
    us = event_id in ('harvey', 'ian')
    rain, t0_rain = R._rain_stack(domain, zg, day0, ndays, us)
    t_end_s = (t_end - t0_rain).total_seconds()
    t_pass_s = (pass_time - t0_rain).total_seconds()

    res = R._run(zg, channel, rain, t0_rain, t_end_s, k, t_pass_s=t_pass_s,
                 grid_every_s=FRAME_HOURS * 3600)
    grids = res['grids']
    times = res['grid_times']
    print(f'  {len(grids)} frames, {res["steps"]} solver steps')

    # Crop to the viewing window.
    w, s, e, n = cfg['bbox']
    win = [w - VIEW_PAD_DEG, s - VIEW_PAD_DEG, e + VIEW_PAD_DEG, n + VIEW_PAD_DEG]
    c0 = max(0, int((win[0] - zg.west) / zg.dx))
    c1 = min(zg.shape[1], int(np.ceil((win[2] - zg.west) / zg.dx)))
    r0 = max(0, int((zg.north - win[3]) / zg.dy))
    r1 = min(zg.shape[0], int(np.ceil((zg.north - win[1]) / zg.dy)))
    sub = grids[:, r0:r1, c0:c1]
    bounds = [zg.west + c0 * zg.dx, zg.north - r1 * zg.dy,
              zg.west + c1 * zg.dx, zg.north - r0 * zg.dy]

    # Open sea is held at its level by the solver; it is not flooding, so it
    # must not paint the ocean blue for the whole animation.
    sea = R.sea_mask(zg.data)[r0:r1, c0:c1]
    sub = np.where(sea[None, :, :], 0.0, sub)

    q = np.clip(np.rint(sub / DEPTH_STEP_M), 0, 255).astype(np.uint8)
    q[(sub > MAX_DEPTH_M)] = 255
    (ROOT / 'outputs' / f'{event_id}_flood.bin').write_bytes(q.tobytes())

    stamps = [(t0_rain + timedelta(seconds=float(t))).replace(microsecond=0).isoformat()
              for t in times]
    pass_idx = int(np.argmin(np.abs(times - t_pass_s)))
    wet_frac = [(float((f >= 1).mean())) for f in q]
    peak_idx = int(np.argmax(wet_frac))
    meta = {
        'event_id': event_id,
        'bounds': [round(v, 6) for v in bounds],          # [w, s, e, n]
        'shape': [int(q.shape[0]), int(q.shape[1]), int(q.shape[2])],
        'depth_step_m': DEPTH_STEP_M,
        'frame_hours': FRAME_HOURS,
        'times': stamps,
        'pass_index': pass_idx,
        'pass_time': pass_time.isoformat(),
        'peak_index': peak_idx,
        'peak_time': stamps[peak_idx],
        'wet_fraction': [round(v, 4) for v in wet_frac],
        'max_depth_ft': round(float(sub.max()) * 3.28084, 1),
        'calibration': {'k': k, 'skill_at_pass': router.get('skill_at_pass'),
                        'res_m': res_m, 'method': router.get('method'),
                        'rain_product': router.get('rain_product')},
        'note': ('Depth every hour from the local-inertial router, calibrated to the '
                 'Sentinel-1 pass. Hydraulically consistent with that observation; '
                 'between passes it is a model, not a measurement.'),
    }
    (ROOT / 'outputs' / f'{event_id}_flood.json').write_text(json.dumps(meta, separators=(',', ':')))
    size = (ROOT / 'outputs' / f'{event_id}_flood.bin').stat().st_size
    print(f'  ✓ {event_id}_flood.bin {size / 1e6:.2f} MB ({q.shape[1]}×{q.shape[2]} cells)  '
          f'pass frame {pass_idx} ({stamps[pass_idx][:13]}), peak frame {peak_idx} '
          f'({stamps[peak_idx][:13]}), max {meta["max_depth_ft"]} ft')


if __name__ == '__main__':
    for ev in (sys.argv[1:] or list(CONFIGS)):
        bake(ev)
