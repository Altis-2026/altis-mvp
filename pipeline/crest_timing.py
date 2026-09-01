"""
crest_timing.py — Did the satellite actually see the flood peak?

THE FAILURE THIS EXISTS TO STOP
-------------------------------
Sentinel-1 samples a place every 6-12 days. A flood crest lasts hours to days.
When the two do not line up, the detector correctly reports "no water" for a
property that was under three feet of it, and the pipeline currently presents
that with the same confidence as a genuine dry reading.

That is the most dangerous output in the product. A false "no flood detected"
becomes a Remote-Deny, and a Remote-Deny on a house that flooded is a wrongly
denied claim. Hurricane Ian was dropped as a demo event for exactly this reason
(docs/DETECTION_LIMITS.md section 4): the first usable pass was four days after
landfall, by which time the surge had receded. Nothing in the output said so.

WHAT THIS MEASURES
------------------
USGS operates stream gauges that record stage every 15 minutes, free, with no
agreement needed. For any bounding box and date window this module finds the
gauges inside it, reads when each one crested, and compares those times against
the Sentinel-1 acquisition times the detector actually used.

The output is a per-event verdict:

    observed   — a pass fell within CREST['tolerance_hours'] of the crest
    partial    — some gauges were caught, others missed
    missed     — every gauge crested more than the tolerance from any pass
    unknown    — no gauge data, so no claim either way

`unknown` is a real answer and must never be collapsed into `observed`. Most of
the world has no gauge coverage, and silently treating "we could not check" as
"we checked and it was fine" would reintroduce the exact failure this module
exists to expose.

WHY STAGE AND NOT RAINFALL
--------------------------
CHIRPS rainfall is already loaded by the pipeline and was the obvious cheap
proxy. It is the wrong quantity: rain falling is not water standing, and the lag
between them is the whole problem. The Brazos crested at Richmond on 1
September, days after Harvey's rain had stopped, which is precisely why the
event's post window is shifted later than the HARVEY box's. Stage is the
quantity the detector is trying to see.

LIMITS, STATED PLAINLY
----------------------
- Gauges measure a CHANNEL. A property flooded by ponding, sheet flow or a
  reservoir release may crest at a different time from the nearest river gauge.
  A verdict of `observed` means the river crest was observed, which is
  evidence about timing, not proof every property was seen at its own peak.
- Gauge density is uneven and non-US coverage is largely absent.
- A pass that catches the crest still cannot see through canopy or resolve
  urban double-bounce. Timing is necessary, not sufficient.
"""
import datetime as dt
import json
import urllib.parse
import urllib.request

try:
    from config import CREST
except ImportError:  # pragma: no cover - import path guard
    from pipeline.config import CREST

NWIS_IV = "https://waterservices.usgs.gov/nwis/iv/"

# 00065 = gage height (feet). 00060 = discharge (cfs), used only as a fallback:
# peak discharge and peak stage occur at effectively the same time for this
# purpose, and some sites report one but not the other.
PARAM_STAGE = '00065'
PARAM_DISCHARGE = '00060'

# NWIS uses this for "no reading".
NWIS_NODATA = {'-999999', '', None}


def _parse_iso(s):
    """NWIS timestamps carry an offset; normalise everything to naive UTC."""
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:  # pragma: no cover - defensive
        return None
    if t.tzinfo is not None:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t


def fetch_gauge_peaks(bbox, start_date, end_date, timeout=120):
    """
    Peak stage time for every USGS gauge in `bbox` over the window.

    Returns a list of dicts, one per gauge, each with site id, name, peak
    value, peak time (naive UTC) and which parameter it came from. An empty
    list means no gauge coverage — the caller must report `unknown`, not
    assume the crest was seen.
    """
    peaks = []
    for param in (PARAM_STAGE, PARAM_DISCHARGE):
        params = {
            'format': 'json',
            'bBox': ','.join(f'{v:.6f}' for v in bbox),
            'parameterCd': param,
            'startDT': f'{start_date}T00:00Z',
            'endDT': f'{end_date}T23:59Z',
            'siteStatus': 'all',
        }
        url = NWIS_IV + '?' + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.loads(r.read().decode())
        except Exception:
            continue
        for series in data.get('value', {}).get('timeSeries', []):
            info = series.get('sourceInfo', {})
            site = (info.get('siteCode') or [{}])[0].get('value')
            if any(p['site'] == site for p in peaks):
                continue  # stage already found for this site
            readings = []
            for block in series.get('values', []):
                for v in block.get('value', []):
                    if v.get('value') in NWIS_NODATA:
                        continue
                    t = _parse_iso(v.get('dateTime', ''))
                    if t is None:
                        continue
                    try:
                        readings.append((t, float(v['value'])))
                    except (TypeError, ValueError):
                        continue
            if not readings:
                continue
            t_peak, v_peak = max(readings, key=lambda x: x[1])
            peaks.append({
                'site': site,
                'name': info.get('siteName', ''),
                'parameter': param,
                'peak_value': round(v_peak, 2),
                'peak_time_utc': t_peak.isoformat(),
                'n_readings': len(readings),
            })
    return peaks


def assess(scene_times_utc, peaks, cfg=CREST):
    """
    Did the acquisitions catch the crest?

    `scene_times_utc` are the Sentinel-1 acquisition datetimes the detector
    actually used (naive UTC). `peaks` comes from `fetch_gauge_peaks`.

    Returns a dict carrying the verdict, the signed gap to the nearest pass for
    each gauge (negative = the pass flew BEFORE the crest), and the worst case,
    which is the one that should drive a disclosure.
    """
    tol = float(cfg.get('tolerance_hours', 24))
    if not peaks:
        return {'crest_observed': 'unknown',
                'reason': 'no USGS gauge readings in this area and window',
                'tolerance_hours': tol, 'gauges': []}
    if not scene_times_utc:
        return {'crest_observed': 'unknown',
                'reason': 'no acquisition times supplied',
                'tolerance_hours': tol, 'gauges': []}

    scenes = sorted(scene_times_utc)
    detail = []
    for p in peaks:
        t_peak = _parse_iso(p['peak_time_utc'])
        if t_peak is None:  # pragma: no cover - defensive
            continue
        # Signed hours from crest to the nearest pass. Negative means the
        # nearest pass was before the crest, i.e. we photographed the rising
        # limb and the water kept climbing after we looked away.
        gaps = [(s - t_peak).total_seconds() / 3600.0 for s in scenes]
        nearest = min(gaps, key=abs)
        detail.append({**p,
                       'nearest_pass_gap_hours': round(nearest, 1),
                       'observed': abs(nearest) <= tol})

    if not detail:
        return {'crest_observed': 'unknown', 'reason': 'no usable peak times',
                'tolerance_hours': tol, 'gauges': []}

    n_obs = sum(1 for d in detail if d['observed'])
    if n_obs == len(detail):
        verdict = 'observed'
    elif n_obs == 0:
        verdict = 'missed'
    else:
        verdict = 'partial'

    worst = max(detail, key=lambda d: abs(d['nearest_pass_gap_hours']))
    return {
        'crest_observed': verdict,
        'tolerance_hours': tol,
        'gauges_total': len(detail),
        'gauges_observed': n_obs,
        'worst_gauge': worst['name'],
        'worst_gap_hours': worst['nearest_pass_gap_hours'],
        'reason': (
            f"{n_obs}/{len(detail)} gauges crested within {tol:.0f} h of a "
            f"pass; worst is {worst['name']} at "
            f"{worst['nearest_pass_gap_hours']:+.1f} h"),
        'gauges': detail,
    }


def safe_to_deny(assessment, cfg=CREST) -> bool:
    """
    May a 'no flood detected' be turned into a Remote-Deny?

    Only when the crest was actually observed. A missed or partial crest means
    an absence of signal is uninformative, and `unknown` means we could not
    check — which is not permission to assume the best.

    This is deliberately the strictest reading: the asymmetry is that a wrongly
    denied claim is far more costly, to the policyholder and to the carrier,
    than an unnecessary inspection.
    """
    if not cfg.get('gate_remote_deny', True):
        return True
    return assessment.get('crest_observed') == 'observed'
