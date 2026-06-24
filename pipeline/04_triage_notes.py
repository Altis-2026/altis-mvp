# 04_triage_notes.py — Triage classification + confidence scoring + adjuster notes
# Key addition: urban_flag column from Step 3 now applies -15pt confidence penalty
# in dense urban areas where SAR shadow artifacts are most likely.
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from openai import OpenAI
import json
import time
from config import OPENROUTER_API_KEY, HARVEY, IAN, TRIAGE, OUTPUT_DIR, PIPELINE_VERSION
from provenance import write_manifest

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

    The urban penalty is the key new factor. In dense building environments,
    SAR shadow artifacts create dark pixels that look like water. A property
    at 0.4ft depth in a dense urban core should be reviewed, not remotely denied.
    """
    score = 65  # Base score
    depth = row['max_depth_ft']
    pct   = row['pct_flooded']
    days  = event_config['days_since_event']

    # Recency factor
    if days <= 2:   score += 15
    elif days <= 4: score += 8
    elif days <= 7: score += 2
    else:           score -= 8

    # Depth certainty
    if depth >= 4.0:    score += 12
    elif depth >= 2.0:  score += 7
    elif depth >= 1.0:  score += 3
    elif depth >= 0.5:  score -= 4   # Shallow — hardest to measure accurately
    elif depth < 0.1:   score += 10  # Near-zero — confidently not flooded
    else:               score -= 7   # Very shallow — most uncertain

    # Coverage coherence
    if pct >= 60:    score += 8
    elif pct >= 35:  score += 4
    elif pct < 5:    score += 7   # Confidently dry
    else:            score -= 3

    # Internal consistency
    if depth > 1.5 and pct < 8:   score -= 10  # Deep but tiny area — suspicious
    if depth < 0.3 and pct > 45:  score -= 8   # Near-zero depth but half flooded — suspicious

    # ── Urban SAR shadow penalty (new in v2)
    # Properties in high-density urban areas get -15pts.
    # Building walls create radar shadows that look like water.
    # This pushes borderline urban cases toward Review rather than
    # a confident Remote-Deny. Legally and scientifically defensible.
    urban_flag = int(row.get('urban_flag', 0))
    if urban_flag == 1:
        score -= 15

    return max(30, min(97, int(score)))


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
            f"Coverage: {int(row['pct_flooded'])}% | "
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
                f"with {int(row['pct_flooded'])}% coverage at {row['confidence_score']}% confidence{urban}; "
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

    print("\nStep 1: Confidence scores (with urban SAR penalty)...")
    df['confidence_score'] = df.apply(
        lambda r: calculate_confidence(r, event_config), axis=1)
    urban_penalized = (df['urban_flag'] == 1).sum()
    print(f"  Mean: {df['confidence_score'].mean():.0f}%  "
          f"Urban-penalized: {urban_penalized}")

    print("\nStep 2: Triage classification...")
    results = df.apply(lambda r: classify_triage(r, TRIAGE), axis=1)
    df['impact_class']       = results.apply(lambda x: x[0])
    df['recommended_action'] = results.apply(lambda x: x[1])

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
        'impact_class', 'confidence_score', 'recommended_action',
        'adjuster_note', 'urban_flag'
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
    })

    return final_df


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    run_triage_pipeline(HARVEY)
    run_triage_pipeline(IAN)
