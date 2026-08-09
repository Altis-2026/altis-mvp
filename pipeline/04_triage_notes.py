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

# Triage scoring now lives in a shared, importable core module so the live
# on-demand backend pipeline scores properties with the identical calibrated
# logic. Re-exported here so this script (and its tests) keep their old surface.
from triage_core import (
    CONFIDENCE_BASE, confidence_breakdown, calculate_confidence,
    classify_triage, ensemble_votes, ensemble_disagreement,
)

# Lazy/conditional construction: newer openai-SDK versions raise immediately
# on OpenAI(api_key=None) rather than waiting for an actual API call, which
# would crash this module on import (and therefore crash tests / any caller
# that imports it) on a fresh checkout before .env is configured. The one
# call site below already catches broadly and falls back to a deterministic
# note, so a missing client degrades the same way a network failure would —
# never a hard crash.
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
) if OPENROUTER_API_KEY else None



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

    if client is None:
        raise RuntimeError("OPENROUTER_API_KEY not configured")

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
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', action='append', choices=['harvey', 'ian'],
                        help='Run only this event (repeatable). Default: both.')
    args = parser.parse_args()
    events = args.event or ['harvey', 'ian']
    if 'harvey' in events:
        run_triage_pipeline(HARVEY)
    if 'ian' in events:
        run_triage_pipeline(IAN)
