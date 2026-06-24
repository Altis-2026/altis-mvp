# 04_triage_notes.py — Triage classification + confidence scoring + adjuster notes
# Key addition: urban_flag column from Step 3 now applies -15pt confidence penalty
# in dense urban areas where SAR shadow artifacts are most likely.
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from openai import OpenAI
import json
import math
import time
from config import (OPENROUTER_API_KEY, HARVEY, IAN, TRIAGE, OUTPUT_DIR,
                    PIPELINE_VERSION, OPTICAL, ENSEMBLE)
from provenance import write_manifest
from uncertainty import depth_interval_ft

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


def calculate_confidence(row, event_config):
    """
    Calculate 0-100 confidence score for each triage decision.

    Factors:
    - Recency of SAR acquisition post-event
    - Depth certainty (very deep/very dry = high confidence; shallow = uncertain)
    - Coverage coherence (high or very low coverage = more certain signal)
    - Internal consistency (depth vs coverage contradiction = penalty)
    - Urban SAR shadow zone (-15pts for dense urban areas from Step 3)
    - Sentinel-2 optical cross-check (Round 2): confirms or contradicts the
      SAR call when a cloud-free observation exists for this property.

    The urban penalty is the key new factor. In dense building environments,
    SAR shadow artifacts create dark pixels that look like water. A property
    at 0.4ft depth in a dense urban core should be reviewed, not remotely denied.

    The optical cross-check is a second, independent sensor used to catch the
    same kind of SAR false positive: if Sentinel-2 shows clear, dry ground at
    a property SAR flagged as flooded, that is strong evidence the SAR signal
    is a radar artifact, not real water. Conversely, when both sensors agree,
    confidence goes up. Optical is usually unavailable right after a storm
    (clouds) — when so, this factor contributes nothing, never a penalty.
    """
    return confidence_breakdown(row, event_config)['final_score']


CONFIDENCE_BASE = 65


def confidence_breakdown(row, event_config):
    """
    The 'why this decision' explainability view: returns the confidence score
    together with the exact per-factor contributions that produced it.

    Returns a dict:
      {
        'base': 65,
        'factors': [{'factor': str, 'delta': int, 'reason': str}, ...],
        'raw_score': int,        # base + sum(deltas), before clamping
        'final_score': int,      # clamped to [30, 97]
      }

    calculate_confidence() is a thin wrapper over this, so the breakdown and the
    score can never drift apart.
    """
    factors = []

    def add(factor, delta, reason):
        if delta:
            factors.append({'factor': factor, 'delta': int(delta), 'reason': reason})

    depth = row['max_depth_ft']
    # pct_flooded is a 0-1 fraction at this stage of the pipeline. The
    # *100 conversion to a percentage happens later, only for the final CSV /
    # display. All coverage thresholds below are therefore on the 0-1 scale.
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
    # Building walls create radar shadows that look like water; push borderline
    # urban cases toward Review rather than a confident Remote-Deny.
    if int(row.get('urban_flag', 0)) == 1:
        add('Urban SAR shadow', -15, 'Dense urban area — radar shadow can mimic water')

    # ── Sentinel-2 optical cross-check (Round 2)
    # Only applies when a cloud-free observation actually exists at this
    # property; otherwise it's a no-op (clouds are the norm right after a storm).
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
    return {
        'base': CONFIDENCE_BASE,
        'factors': factors,
        'raw_score': int(raw),
        'final_score': final,
    }


def classify_triage(row, thresholds):
    """
    Assign property to one of four triage categories.
    Returns (impact_class, recommended_action).
    """
    depth = row['max_depth_ft']
    pct   = row['pct_flooded']
    conf  = row['confidence_score']
    t     = thresholds

    # DISPATCH: deep or extensive flooding
    if depth >= t['dispatch_depth_ft'] and conf >= 55:
        return 'Dispatch', 'Send adjuster — major flood damage likely'
    if depth >= t['dispatch_low_depth_ft'] and pct >= t['dispatch_pct'] and conf >= 55:
        return 'Dispatch', 'Send adjuster — extensive property flooding detected'
    if depth >= 5.0:
        return 'Dispatch', 'Send adjuster — extreme flood depth detected'

    # REMOTE DENY: no significant flooding
    if (depth <= t['remote_deny_depth_ft'] and
            pct   <= t['remote_deny_pct'] and
            conf  >= t['remote_deny_conf']):
        return 'Remote-Deny', 'Deny remotely — no significant flooding detected by satellite'

    # REMOTE APPROVE: moderate confirmed flooding
    if (t['remote_approve_min_depth'] <= depth <= t['remote_approve_max_depth'] and
            pct  >= t['remote_approve_min_pct'] and
            conf >= t['remote_approve_conf']):
        return 'Remote-Approve', 'Approve remotely — flooding confirmed, documentation required'

    # REVIEW: everything else
    return 'Review', 'Flag for manual review — borderline measurements or low confidence'


def ensemble_votes(row, cfg=ENSEMBLE):
    """
    Independent flood votes from each available sensor/model.
    Each member returns 'flood', 'dry', or 'abstain' (no usable data).
    """
    pct = float(row.get('pct_flooded', 0.0) or 0.0)

    # SAR member — always votes.
    sar = 'flood' if pct >= cfg['sar_flood_pct'] else 'dry'

    # Optical member — only when a cloud-free observation exists.
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

    # DEM-hydrology member — height above local drainage. Abstains if unknown.
    rel = row.get('rel_elev_ft', None)
    try:
        rel = float(rel)
        rel_known = not (isinstance(rel, float) and math.isnan(rel))
    except (TypeError, ValueError):
        rel_known = False
    if rel_known:
        if rel <= cfg['dem_plausible_rel_ft']:
            dem = 'flood'          # low-lying: flooding plausible
        elif rel >= cfg['dem_implausible_rel_ft']:
            dem = 'dry'            # perched high above drainage: flood implausible
        else:
            dem = 'abstain'
    else:
        dem = 'abstain'

    return {'sar': sar, 'optical': optical, 'dem_hydrology': dem}


def ensemble_disagreement(row, cfg=ENSEMBLE):
    """
    Returns (disagree: bool, reason: str, votes: dict).
    Disagreement = at least one member votes 'flood' and at least one votes
    'dry' among the members that did not abstain. Such a property should go to
    manual Review rather than receive a confident automated remote decision.
    """
    votes = ensemble_votes(row, cfg)
    floods = [m for m, v in votes.items() if v == 'flood']
    drys   = [m for m, v in votes.items() if v == 'dry']

    if floods and drys:
        label = {'sar': 'SAR', 'optical': 'optical', 'dem_hydrology': 'DEM-hydrology'}
        reason = (f"Sensors disagree: {', '.join(label[m] for m in floods)} indicate "
                  f"flooding while {', '.join(label[m] for m in drys)} indicate dry/"
                  f"implausible — routed to manual review.")
        return True, reason, votes
    return False, '', votes


def generate_notes_batch(batch_rows):
    """
    Generate adjuster notes for up to 20 properties via Claude API.
    Returns list of note strings.
    """
    if not batch_rows:
        return []

    property_block = ""
    for i, row in enumerate(batch_rows, start=1):
        street = row['address'].split(',')[0]
        property_block += (
            f"Property {i}: {street} | "
            f"Depth: {row['max_depth_ft']:.1f}ft | "
            f"Coverage: {int(round(row['pct_flooded'] * 100))}% | "
            f"Class: {row['impact_class']} | "
            f"Confidence: {row['confidence_score']}%"
            + (" | Urban SAR zone" if int(row.get('urban_flag', 0)) == 1 else "")
            + "\n"
        )

    prompt = (
        "You are a licensed property insurance adjuster writing post-flood field notes.\n"
        "For each property write exactly ONE professional sentence under 25 words.\n"
        "Reference the specific depth and explain the triage decision.\n"
        "For Urban SAR zone properties, note that dense buildings create measurement uncertainty.\n"
        "Plain English — no jargon, no bullets.\n\n"
        f"{property_block}\n"
        f"Return ONLY a valid JSON array of exactly {len(batch_rows)} strings. "
        "No preamble, no markdown. Just the JSON array.\n"
        'Example: ["Note one.", "Note two."]'
    )

    response = client.chat.completions.create(
        model="anthropic/claude-3-5-haiku",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.choices[0].message.content.strip()

    try:
        notes = json.loads(raw)
        if isinstance(notes, list) and len(notes) == len(batch_rows):
            return [str(n).strip() for n in notes]
        raise ValueError(f"Expected {len(batch_rows)} notes, got {len(notes)}")
    except (json.JSONDecodeError, ValueError):
        # Deterministic fallback
        fallback = []
        for row in batch_rows:
            street = row['address'].split(',')[0]
            action = row['recommended_action'].split('—')[0].strip()
            urban  = " (urban SAR zone, elevated uncertainty)" if int(row.get('urban_flag', 0)) == 1 else ""
            fallback.append(
                f"Satellite analysis at {street} shows {row['max_depth_ft']:.1f}ft max flood depth "
                f"with {int(round(row['pct_flooded'] * 100))}% coverage at {row['confidence_score']}% confidence{urban}; "
                f"{action.lower()}."[:200]
            )
        return fallback


def add_adjuster_notes(df):
    BATCH_SIZE = 20
    all_notes  = []
    rows       = df.to_dict('records')
    print(f"  Generating notes for {len(rows)} properties...")

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        try:
            notes = generate_notes_batch(batch)
            all_notes.extend(notes)
        except Exception as e:
            print(f"  Claude API batch error: {e}. Using fallback.")
            for row in batch:
                street = row['address'].split(',')[0]
                all_notes.append(
                    f"Satellite data at {street} indicates {row['max_depth_ft']:.1f}ft depth; "
                    f"{row['impact_class'].lower()} per {row['confidence_score']}% confidence."
                )

        processed = min(i + BATCH_SIZE, len(rows))
        if processed % 100 == 0 or processed == len(rows):
            print(f"  Progress: {processed}/{len(rows)}")
        time.sleep(0.6)

    df = df.copy()
    df['adjuster_note'] = all_notes
    return df


def run_triage_pipeline(event_config):
    event_id, event_name = event_config['event_id'], event_config['event_name']

    print(f"\n{'=' * 60}")
    print(f"  Triage: {event_name}")
    print(f"{'=' * 60}")

    raw_path = os.path.join(OUTPUT_DIR, f"{event_id}_raw.csv")
    df = pd.read_csv(raw_path)

    # Backward compat: if urban_flag not in raw CSV (v1 pipeline), default to 0
    if 'urban_flag' not in df.columns:
        df['urban_flag'] = 0
        print("  Note: urban_flag not found — run 03_flood_pipeline.py to add it")

    # Backward compat: if optical columns not in raw CSV (pre-Round-2 pipeline),
    # default to "unavailable" — contributes nothing to confidence, same as
    # any property where Sentinel-2 was cloud-blocked.
    if 'optical_available' not in df.columns:
        df['optical_available'] = 0
        df['optical_water_pct'] = 0.0
        print("  Note: optical cross-check columns not found — run 03_flood_pipeline.py to add them")

    # Backward compat (Round 3): depth uncertainty interval. If 03 already wrote
    # it, keep it; otherwise derive it here from depth + DEM resolution (+ the
    # measured water-surface spread when present, else a depth-proportional
    # fallback inside depth_interval_ft).
    if 'depth_ci_ft' not in df.columns:
        dem_res = df['dem_resolution_m'] if 'dem_resolution_m' in df.columns else None
        spread_col = df['wse_spread_ft'] if 'wse_spread_ft' in df.columns else None
        lowers, uppers, cis = [], [], []
        for i, row in df.iterrows():
            res = row['dem_resolution_m'] if dem_res is not None else None
            spread = row['wse_spread_ft'] if spread_col is not None else None
            lo, up, ci = depth_interval_ft(row['max_depth_ft'], res, spread)
            lowers.append(lo); uppers.append(up); cis.append(ci)
        df['depth_lower_ft'] = lowers
        df['depth_upper_ft'] = uppers
        df['depth_ci_ft'] = cis
        print("  Note: depth interval derived in triage (run 03_flood_pipeline.py for measured WSE spread)")

    print("\nStep 1: Confidence scores (with urban SAR penalty)...")
    breakdowns = df.apply(lambda r: confidence_breakdown(r, event_config), axis=1)
    df['confidence_score'] = [b['final_score'] for b in breakdowns]
    # 'Why this decision': persist the per-factor breakdown as JSON so the API
    # and Reports panel can show exactly what drove each property's confidence.
    df['confidence_factors'] = [json.dumps(b['factors']) for b in breakdowns]
    urban_penalized = (df['urban_flag'] == 1).sum()
    print(f"  Mean: {df['confidence_score'].mean():.0f}%  "
          f"Urban-penalized: {urban_penalized}")

    print("\nStep 2: Triage classification...")
    results = df.apply(lambda r: classify_triage(r, TRIAGE), axis=1)
    df['impact_class']       = results.apply(lambda x: x[0])
    df['recommended_action'] = results.apply(lambda x: x[1])

    # Step 2b: ensemble disagreement override. When independent members
    # (SAR / optical / DEM-hydrology) genuinely conflict, downgrade a confident
    # automated decision to manual Review — we never auto-resolve a contested
    # signal. Recorded in dedicated columns for the Reports panel.
    ens = df.apply(lambda r: ensemble_disagreement(r, ENSEMBLE), axis=1)
    df['ensemble_disagreement'] = [int(e[0]) for e in ens]
    df['ensemble_note']         = [e[1] for e in ens]
    df['ensemble_votes']        = [json.dumps(e[2]) for e in ens]

    if ENSEMBLE['downgrade_to_review']:
        mask = (df['ensemble_disagreement'] == 1) & (df['impact_class'] != 'Review')
        downgraded = int(mask.sum())
        df.loc[mask, 'recommended_action'] = (
            'Flag for manual review — independent sensors (SAR/optical/DEM) disagree')
        df.loc[mask, 'impact_class'] = 'Review'
        if downgraded:
            print(f"  Ensemble disagreement downgraded {downgraded} decision(s) to Review")

    counts = df['impact_class'].value_counts()
    for cat in ['Dispatch', 'Remote-Approve', 'Remote-Deny', 'Review']:
        n = counts.get(cat, 0)
        print(f"    {cat:<20} {n:>5} ({n/len(df)*100:4.1f}%)")

    print("\nStep 3: Adjuster notes (Claude API)...")
    df = add_adjuster_notes(df)

    remote_count = len(df[df['impact_class'].isin(['Remote-Approve', 'Remote-Deny'])])
    savings      = remote_count * event_config['cost_per_inspection']
    print(f"\nEstimated savings: ${savings:,.0f} ({remote_count:,} remote resolutions)")

    # Format pct_flooded as percentage for final CSV
    df['pct_flooded']  = (df['pct_flooded'] * 100).round(1)
    df['max_depth_ft'] = df['max_depth_ft'].round(2)

    final_cols = [
        'property_id', 'address', 'pct_flooded', 'max_depth_ft',
        'depth_lower_ft', 'depth_upper_ft', 'depth_ci_ft',
        'impact_class', 'confidence_score', 'recommended_action',
        'adjuster_note', 'urban_flag', 'optical_available', 'optical_water_pct',
        'confidence_factors', 'ensemble_disagreement', 'ensemble_note',
        'ensemble_votes'
    ]
    final_df = df[[c for c in final_cols if c in df.columns]].copy()

    out = os.path.join(OUTPUT_DIR, f"{event_id}_final.csv")
    final_df.to_csv(out, index=False)
    print(f"\n✓ Saved → {out}")

    write_manifest(event_id, 'triage', {
        'pipeline_version':  PIPELINE_VERSION,
        'triage_thresholds': TRIAGE,
        'category_counts':   {cat: int(counts.get(cat, 0))
                               for cat in ['Dispatch', 'Remote-Approve', 'Remote-Deny', 'Review']},
        'estimated_savings_usd': savings,
        'remote_resolution_count': remote_count,
        'cost_per_inspection_usd': event_config['cost_per_inspection'],
        'optical_cross_check':    OPTICAL,
        'optical_available_count': int((df['optical_available'] == 1).sum()),
    })

    return final_df


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    run_triage_pipeline(HARVEY)
    run_triage_pipeline(IAN)
