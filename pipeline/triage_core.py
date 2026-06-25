"""
triage_core.py — Pure triage scoring logic, importable from anywhere.

This is the single source of truth for confidence scoring, triage
classification, and ensemble-disagreement detection. Both the batch script
(04_triage_notes.py) and the backend's live, on-demand analysis
(backend/live_pipeline.py) import from here, so a property analyzed live
"anywhere in the world" is scored by exactly the same calibrated rules as a
pre-computed demo event — no second implementation to drift out of sync.

No network, no Earth Engine, no LLM — just deterministic functions over a row
of measurements, so it stays trivially unit-testable.
"""
import math

# Dual import: works whether the pipeline dir is on sys.path (batch script /
# tests) or imported as a package (backend → `pipeline.triage_core`).
try:
    from config import TRIAGE, OPTICAL, ENSEMBLE
except ImportError:  # pragma: no cover - import path guard
    from pipeline.config import TRIAGE, OPTICAL, ENSEMBLE

CONFIDENCE_BASE = 65


def confidence_breakdown(row, event_config):
    """
    The 'why this decision' explainability view: returns the confidence score
    together with the exact per-factor contributions that produced it.

    Returns a dict:
      {'base': 65, 'factors': [{'factor', 'delta', 'reason'}...],
       'raw_score': int, 'final_score': int (clamped to [30, 97])}

    calculate_confidence() is a thin wrapper over this, so the breakdown and the
    score can never drift apart.
    """
    factors = []

    def add(factor, delta, reason):
        if delta:
            factors.append({'factor': factor, 'delta': int(delta), 'reason': reason})

    depth = row['max_depth_ft']
    # pct_flooded is a 0-1 fraction at this stage of the pipeline.
    pct  = row['pct_flooded']
    days = event_config['days_since_event']

    # Recency factor
    if days <= 2:   add('SAR recency', +15, f'Imagery {days}d post-event — very fresh')
    elif days <= 4: add('SAR recency', +8,  f'Imagery {days}d post-event — fresh')
    elif days <= 7: add('SAR recency', +2,  f'Imagery {days}d post-event — acceptable')
    else:           add('SAR recency', -8,  f'Imagery {days}d post-event — stale')

    # Depth certainty
    if depth >= 4.0:    add('Depth certainty', +12, f'Deep water ({depth:.1f}ft) — unambiguous')
    elif depth >= 2.0:  add('Depth certainty', +7,  f'Moderate depth ({depth:.1f}ft) — clear signal')
    elif depth >= 1.0:  add('Depth certainty', +3,  f'Shallow-moderate depth ({depth:.1f}ft)')
    elif depth >= 0.5:  add('Depth certainty', -4,  f'Shallow ({depth:.1f}ft) — hardest to measure')
    elif depth < 0.1:   add('Depth certainty', +10, 'Near-zero depth — confidently not flooded')
    else:               add('Depth certainty', -7,  f'Very shallow ({depth:.1f}ft) — most uncertain')

    # Coverage coherence (pct is a 0-1 fraction)
    if pct >= 0.60:    add('Coverage coherence', +8, f'{pct*100:.0f}% coverage — extensive, coherent')
    elif pct >= 0.35:  add('Coverage coherence', +4, f'{pct*100:.0f}% coverage — substantial')
    elif pct < 0.05:   add('Coverage coherence', +7, f'{pct*100:.0f}% coverage — confidently dry')
    else:              add('Coverage coherence', -3, f'{pct*100:.0f}% coverage — ambiguous partial')

    # Internal consistency (pct is a 0-1 fraction)
    if depth > 1.5 and pct < 0.08:
        add('Internal consistency', -10, 'Deep water over a tiny footprint — physically suspicious')
    if depth < 0.3 and pct > 0.45:
        add('Internal consistency', -8, 'Near-zero depth but half the area flagged — suspicious')

    # ── Urban SAR shadow penalty
    if int(row.get('urban_flag', 0)) == 1:
        add('Urban SAR shadow', -15, 'Dense urban area — radar shadow can mimic water')

    # ── Sentinel-2 optical cross-check
    if int(row.get('optical_available', 0)) == 1:
        optical_water_pct = float(row.get('optical_water_pct', 0.0))
        sar_says_flooded   = pct >= OPTICAL['sar_flood_pct']
        optical_says_water = optical_water_pct >= OPTICAL['water_confirm_pct']
        optical_says_dry   = optical_water_pct < OPTICAL['water_contradict_pct']

        if sar_says_flooded and optical_says_water:
            add('Optical cross-check', OPTICAL['confirm_bonus'],
                'Sentinel-2 confirms standing water — sensors agree')
        elif sar_says_flooded and optical_says_dry:
            add('Optical cross-check', OPTICAL['contradict_penalty'],
                'Sentinel-2 shows dry ground — likely SAR false positive')
        elif not sar_says_flooded and optical_says_dry:
            add('Optical cross-check', OPTICAL['confirm_dry_bonus'],
                'Sentinel-2 confirms dry ground — sensors agree')

    raw = CONFIDENCE_BASE + sum(f['delta'] for f in factors)
    final = max(30, min(97, int(raw)))
    return {'base': CONFIDENCE_BASE, 'factors': factors,
            'raw_score': int(raw), 'final_score': final}


def calculate_confidence(row, event_config):
    """0-100 confidence score for a triage decision (thin wrapper)."""
    return confidence_breakdown(row, event_config)['final_score']


def classify_triage(row, thresholds=TRIAGE):
    """
    Assign property to one of four triage categories.
    Returns (impact_class, recommended_action).
    """
    depth = row['max_depth_ft']
    pct   = row['pct_flooded']
    conf  = row['confidence_score']
    t     = thresholds

    if depth >= t['dispatch_depth_ft'] and conf >= 55:
        return 'Dispatch', 'Send adjuster — major flood damage likely'
    if depth >= t['dispatch_low_depth_ft'] and pct >= t['dispatch_pct'] and conf >= 55:
        return 'Dispatch', 'Send adjuster — extensive property flooding detected'
    if depth >= 5.0:
        return 'Dispatch', 'Send adjuster — extreme flood depth detected'

    if (depth <= t['remote_deny_depth_ft'] and
            pct   <= t['remote_deny_pct'] and
            conf  >= t['remote_deny_conf']):
        return 'Remote-Deny', 'Deny remotely — no significant flooding detected by satellite'

    if (t['remote_approve_min_depth'] <= depth <= t['remote_approve_max_depth'] and
            pct  >= t['remote_approve_min_pct'] and
            conf >= t['remote_approve_conf']):
        return 'Remote-Approve', 'Approve remotely — flooding confirmed, documentation required'

    return 'Review', 'Flag for manual review — borderline measurements or low confidence'


def ensemble_votes(row, cfg=ENSEMBLE):
    """
    Independent flood votes from each available sensor/model.
    Each member returns 'flood', 'dry', or 'abstain' (no usable data).
    """
    pct = float(row.get('pct_flooded', 0.0) or 0.0)

    sar = 'flood' if pct >= cfg['sar_flood_pct'] else 'dry'

    if int(row.get('optical_available', 0)) == 1:
        ow = float(row.get('optical_water_pct', 0.0) or 0.0)
        if ow >= cfg['optical_water_pct']:
            optical = 'flood'
        elif ow < cfg['optical_dry_pct']:
            optical = 'dry'
        else:
            optical = 'abstain'
    else:
        optical = 'abstain'

    rel = row.get('rel_elev_ft', None)
    try:
        rel = float(rel)
        rel_known = not (isinstance(rel, float) and math.isnan(rel))
    except (TypeError, ValueError):
        rel_known = False
    if rel_known:
        if rel <= cfg['dem_plausible_rel_ft']:
            dem = 'flood'
        elif rel >= cfg['dem_implausible_rel_ft']:
            dem = 'dry'
        else:
            dem = 'abstain'
    else:
        dem = 'abstain'

    return {'sar': sar, 'optical': optical, 'dem_hydrology': dem}


def ensemble_disagreement(row, cfg=ENSEMBLE):
    """
    Returns (disagree: bool, reason: str, votes: dict).
    Disagreement = at least one member votes 'flood' and at least one votes
    'dry' among non-abstaining members → route to manual Review.
    """
    votes = ensemble_votes(row, cfg)
    floods = [m for m, v in votes.items() if v == 'flood']
    drys   = [m for m, v in votes.items() if v == 'dry']

    # DEM-hydrology contributes only a *plausibility* prior: "this ground is
    # low-lying, so flooding is physically possible here." When neither actual
    # detector (SAR nor optical) sees water, that lone prior must NOT manufacture
    # a manual review against an agreeing dry consensus — we trust the sensors,
    # and the property is confidently dry. (Its dissenting "perched too high to
    # flood" vote, which catches SAR false positives, is fully preserved below.)
    # This matters most on flat coastal terrain where almost every parcel is
    # low-lying: without it, every confidently-dry coastal property is needlessly
    # downgraded to Review.
    detector_flood = votes['sar'] == 'flood' or votes['optical'] == 'flood'
    if floods == ['dem_hydrology'] and not detector_flood:
        return False, '', votes

    if floods and drys:
        label = {'sar': 'SAR', 'optical': 'optical', 'dem_hydrology': 'DEM-hydrology'}
        reason = (f"Sensors disagree: {', '.join(label[m] for m in floods)} indicate "
                  f"flooding while {', '.join(label[m] for m in drys)} indicate dry/"
                  f"implausible — routed to manual review.")
        return True, reason, votes
    return False, '', votes
