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
    from config import TRIAGE, OPTICAL, ENSEMBLE, SAR_VH
except ImportError:  # pragma: no cover - import path guard
    from pipeline.config import TRIAGE, OPTICAL, ENSEMBLE, SAR_VH

CONFIDENCE_BASE = 65


def _as_float(value):
    """Parse a measurement to float, treating None/NaN/garbage as 'unknown'."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _hand_cfg(key):
    """
    Read a HAND threshold. Imported lazily so this module keeps working against
    an older config.py that predates the HAND block.
    """
    try:
        from config import HAND
    except ImportError:  # pragma: no cover - import path guard
        from pipeline.config import HAND
    return HAND[key]


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

    # ── Dual-polarization (VH) cross-check — coherence-proxy from the same
    #    Sentinel-1 pass. Abstains (no factor) when no VH scene was available,
    #    so rows from older pipelines score identically.
    if int(row.get('vh_available', 0)) == 1:
        vh_pct = float(row.get('vh_water_pct', 0.0) or 0.0)
        sar_says_flooded = pct >= OPTICAL['sar_flood_pct']
        vh_says_flooded  = vh_pct >= SAR_VH['flood_pct']
        vh_says_dry      = vh_pct < SAR_VH['dry_pct']

        if sar_says_flooded and vh_says_flooded:
            add('Dual-pol cross-check', SAR_VH['agree_bonus'],
                'VH channel independently confirms the flood signal')
        elif sar_says_flooded and vh_says_dry:
            add('Dual-pol cross-check', SAR_VH['disagree_penalty'],
                'VH channel does not corroborate — possible VV artifact')
        elif not sar_says_flooded and vh_says_dry:
            add('Dual-pol cross-check', SAR_VH['agree_dry_bonus'],
                'VH channel confirms dry ground')

    raw = CONFIDENCE_BASE + sum(f['delta'] for f in factors)
    final = max(30, min(97, int(raw)))
    return {'base': CONFIDENCE_BASE, 'factors': factors,
            'raw_score': int(raw), 'final_score': final}


def dualpol_review_override(row, cfg=SAR_VH):
    """
    Hard cross-check: when the VV channel calls flood but the (available) VH
    channel reads dry, the safe action is manual Review — mirrors the ensemble
    downgrade. Returns (override: bool, reason: str).
    """
    if not cfg.get('downgrade_to_review'):
        return False, ''
    if int(row.get('vh_available', 0)) != 1:
        return False, ''
    pct = float(row.get('pct_flooded', 0.0) or 0.0)
    vh_pct = float(row.get('vh_water_pct', 0.0) or 0.0)
    if pct >= OPTICAL['sar_flood_pct'] and vh_pct < cfg['dry_pct']:
        return True, ('VV amplitude change indicates flooding but the VH channel '
                      'does not corroborate — routed to manual review (dual-pol '
                      'cross-check).')
    return False, ''


def calculate_confidence(row, event_config):
    """0-100 confidence score for a triage decision (thin wrapper)."""
    return confidence_breakdown(row, event_config)['final_score']


def classify_triage(row, thresholds=TRIAGE, crest_observed=None):
    """
    Assign property to one of four triage categories.
    Returns (impact_class, recommended_action).

    `crest_observed` is the event-level verdict from `crest_timing.assess`
    ('observed' / 'partial' / 'missed' / 'unknown'). Anything other than
    'observed' BLOCKS Remote-Deny and downgrades it to Review.

    WHY THIS GATE EXISTS. Remote-Deny is the only class that acts on the
    ABSENCE of a signal, so it is the only one whose correctness depends on the
    satellite having actually looked at the right moment. Sentinel-1 revisits
    every 6-12 days and a crest lasts hours; measured on our own events, the
    Brazos crested 40.9 hours after the nearest pass. A "no flood detected"
    from that pass is not evidence the property stayed dry, and turning it into
    a denial is how a genuinely flooded house gets refused.

    Passing None keeps the historical behaviour, so callers that have not been
    updated do not silently change — but the batch pipeline supplies it.
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
        if crest_observed is not None and crest_observed != 'observed':
            return ('Review',
                    'Flag for manual review — no flooding detected, but the '
                    'satellite did not observe the flood crest '
                    f'({crest_observed}), so the absence of a signal is not '
                    'evidence this property stayed dry')
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

    # DEM-hydrology vote. HAND (height above the nearest drainage channel) is
    # the hydrologically correct terrain descriptor and takes precedence when
    # present. The old relative-elevation heuristic — height above the minimum
    # within a fixed circle — remains the fallback for rows produced before
    # HAND existed and for locations MERIT Hydro doesn't cover.
    #
    # The distinction matters most on flat coastal ground, where nearly every
    # parcel sits within a few feet of its neighbourhood minimum, so the old
    # measure abstained almost everywhere it was needed most.
    dem = 'abstain'
    hand = _as_float(row.get('hand_ft'))
    if hand is not None:
        if hand <= _hand_cfg('plausible_ft'):
            dem = 'flood'
        elif hand >= _hand_cfg('implausible_ft'):
            dem = 'dry'
    else:
        rel = _as_float(row.get('rel_elev_ft'))
        if rel is not None:
            if rel <= cfg['dem_plausible_rel_ft']:
                dem = 'flood'
            elif rel >= cfg['dem_implausible_rel_ft']:
                dem = 'dry'

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
