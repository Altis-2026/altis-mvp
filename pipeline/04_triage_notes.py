# 04_triage_notes.py — Triage classification, confidence scoring, adjuster note generation
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from openai import OpenAI
import json
import time
from config import OPENROUTER_API_KEY, HARVEY, IAN, TRIAGE, OUTPUT_DIR

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIDENCE SCORING
# ─────────────────────────────────────────────────────────────────────────────

def calculate_confidence(row, event_config):
    """
    Calculate a 0–100 confidence score for each property's triage decision.

    Factors:
    - Recency: how many days after the event was the best SAR pass
    - Depth certainty: very deep and near-zero are both high confidence,
      shallow (0.3–0.8ft) is the hardest range to measure accurately
    - Coverage coherence: high or very low coverage are more certain signals
    - Internal consistency: penalise if depth and coverage contradict each other
    """
    score = 65  # Base score
    depth = row['max_depth_ft']
    pct   = row['pct_flooded']       # 0–1
    days  = event_config['days_since_event']

    # Recency factor
    if days <= 2:
        score += 15
    elif days <= 4:
        score += 8
    elif days <= 7:
        score += 2
    else:
        score -= 8

    # Depth certainty factor
    if depth >= 4.0:
        score += 12
    elif depth >= 2.0:
        score += 7
    elif depth >= 1.0:
        score += 3
    elif depth >= 0.5:
        score -= 4   # Shallow range — harder to measure
    elif depth < 0.1:
        score += 10  # Near zero — confidently not flooded
    else:
        score -= 7   # Very shallow — most uncertain

    # Coverage coherence factor
    if pct >= 0.60:
        score += 8
    elif pct >= 0.35:
        score += 4
    elif pct >= 0.15:
        score += 0
    elif pct < 0.05:
        score += 7   # Confidently not flooded
    else:
        score -= 3

    # Internal consistency check
    if depth > 1.5 and pct < 0.08:
        score -= 10  # High depth but barely any area flooded — suspicious
    if depth < 0.3 and pct > 0.45:
        score -= 8   # Near-zero depth but nearly half the area flagged — suspicious

    return max(30, min(97, int(score)))


# ─────────────────────────────────────────────────────────────────────────────
# TRIAGE CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

def classify_triage(row, thresholds):
    """
    Assign each property to one of four triage categories.
    Returns (impact_class, recommended_action).

    Categories:
    - Dispatch:       Needs a physical adjuster visit
    - Remote-Approve: Flooding confirmed; approve claim without visit
    - Remote-Deny:    No flooding detected; deny claim without visit
    - Review:         Ambiguous — send to human review queue
    """
    depth = row['max_depth_ft']
    pct   = row['pct_flooded']
    conf  = row['confidence_score']
    t     = thresholds

    # ── DISPATCH ──────────────────────────────────────────────────────────────
    # Deep flooding always requires inspection regardless of confidence
    if depth >= t['dispatch_depth_ft'] and conf >= 55:
        return 'Dispatch', 'Send adjuster — major flood damage likely'

    # Significant depth combined with high coverage
    if (depth >= t['dispatch_low_depth_ft'] and
            pct >= t['dispatch_pct'] and conf >= 55):
        return 'Dispatch', 'Send adjuster — extensive property flooding detected'

    # Extreme depth regardless of confidence (never remotely deny extreme cases)
    if depth >= 5.0:
        return 'Dispatch', 'Send adjuster — extreme flood depth detected'

    # ── REMOTE DENY ───────────────────────────────────────────────────────────
    if (depth <= t['remote_deny_depth_ft'] and
            pct  <= t['remote_deny_pct'] and
            conf >= t['remote_deny_conf']):
        return 'Remote-Deny', 'Deny remotely — no significant flooding detected by satellite'

    # ── REMOTE APPROVE ────────────────────────────────────────────────────────
    if (t['remote_approve_min_depth'] <= depth <= t['remote_approve_max_depth'] and
            pct  >= t['remote_approve_min_pct'] and
            conf >= t['remote_approve_conf']):
        return 'Remote-Approve', 'Approve remotely — flooding confirmed, standard documentation required'

    # ── REVIEW (catch-all) ────────────────────────────────────────────────────
    return 'Review', 'Flag for manual review — borderline measurements or low confidence'


# ─────────────────────────────────────────────────────────────────────────────
# ADJUSTER NOTE GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def generate_notes_batch(batch_rows):
    """
    Generate adjuster notes for up to 20 properties in a single Claude API call.
    Returns a list of note strings in the same order as batch_rows.
    """
    if not batch_rows:
        return []

    property_block = ""
    for i, row in enumerate(batch_rows, start=1):
        street = row['address'].split(',')[0]  # Just the street address for brevity
        property_block += (
            f"Property {i}: {street} | "
            f"Depth: {row['max_depth_ft']:.1f}ft | "
            f"Coverage: {int(row['pct_flooded'] * 100)}% | "
            f"Class: {row['impact_class']} | "
            f"Confidence: {row['confidence_score']}%\n"
        )

    prompt = (
        "You are a licensed property insurance adjuster writing post-flood field notes.\n"
        "For each property below write exactly ONE professional sentence under 25 words.\n"
        "Each note must reference the specific depth and explain the triage decision.\n"
        "Use plain English — no jargon, no bullet points.\n\n"
        f"{property_block}\n"
        f"Return ONLY a valid JSON array of exactly {len(batch_rows)} strings. "
        "No preamble, no markdown, no explanation. Just the JSON array.\n"
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
        # Fallback: build deterministic notes from the data
        fallback = []
        for row in batch_rows:
            street = row['address'].split(',')[0]
            action = row['recommended_action'].split('—')[0].strip()
            note = (
                f"Satellite analysis at {street} shows {row['max_depth_ft']:.1f}ft max flood depth "
                f"with {int(row['pct_flooded']*100)}% coverage at {row['confidence_score']}% confidence; "
                f"{action.lower()}."
            )
            fallback.append(note[:200])  # Hard cap at 200 chars
        return fallback


def add_adjuster_notes(df):
    """
    Add adjuster_note column to the DataFrame.
    Processes in batches of 20 with rate-limit protection.
    """
    BATCH_SIZE = 20
    all_notes  = []
    rows       = df.to_dict('records')

    print(f"  Generating adjuster notes for {len(rows)} properties in batches of {BATCH_SIZE}...")

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]

        try:
            notes = generate_notes_batch(batch)
            all_notes.extend(notes)
        except Exception as e:
            print(f"  Warning: Claude API batch {i}–{i+BATCH_SIZE} error: {e}. Using fallback.")
            for row in batch:
                street = row['address'].split(',')[0]
                all_notes.append(
                    f"Satellite data at {street} indicates {row['max_depth_ft']:.1f}ft depth; "
                    f"{row['impact_class'].lower()} per confidence {row['confidence_score']}%."
                )

        processed = min(i + BATCH_SIZE, len(rows))
        if processed % 100 == 0 or processed == len(rows):
            print(f"  Progress: {processed}/{len(rows)} notes generated")

        time.sleep(0.6)  # Respect Claude API rate limits

    df = df.copy()
    df['adjuster_note'] = all_notes
    return df


# ─────────────────────────────────────────────────────────────────────────────
# FULL TRIAGE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def run_triage_pipeline(event_config):
    """
    Load raw flood CSV → add confidence, triage, adjuster notes → save final CSV.
    """
    event_id   = event_config['event_id']
    event_name = event_config['event_name']

    print(f"\n{'=' * 60}")
    print(f"  Triage pipeline: {event_name}")
    print(f"{'=' * 60}")

    # Load raw flood data
    raw_path = os.path.join(OUTPUT_DIR, f"{event_id}_raw.csv")
    df = pd.read_csv(raw_path)
    print(f"\nLoaded {len(df)} properties from {raw_path}")

    # 1. Confidence scores
    print("\nStep 1: Calculating confidence scores...")
    df['confidence_score'] = df.apply(
        lambda row: calculate_confidence(row, event_config), axis=1
    )
    print(f"  Mean confidence: {df['confidence_score'].mean():.0f}%  "
          f"Min: {df['confidence_score'].min()}%  Max: {df['confidence_score'].max()}%")

    # 2. Triage classification
    print("\nStep 2: Classifying properties...")
    results = df.apply(lambda r: classify_triage(r, TRIAGE), axis=1)
    df['impact_class']      = results.apply(lambda x: x[0])
    df['recommended_action'] = results.apply(lambda x: x[1])

    counts = df['impact_class'].value_counts()
    print("  Triage breakdown:")
    for cat in ['Dispatch', 'Remote-Approve', 'Remote-Deny', 'Review']:
        n   = counts.get(cat, 0)
        pct = n / len(df) * 100
        print(f"    {cat:<20} {n:>5} ({pct:4.1f}%)")

    # 3. Adjuster notes
    print("\nStep 3: Generating adjuster notes via Claude API...")
    df = add_adjuster_notes(df)

    # 4. Savings calculation
    remote_count  = len(df[df['impact_class'].isin(['Remote-Approve', 'Remote-Deny'])])
    savings       = remote_count * event_config['cost_per_inspection']
    total         = len(df)

    print(f"\n{'─' * 60}")
    print(f"  SAVINGS SUMMARY — {event_name}")
    print(f"{'─' * 60}")
    print(f"  Total properties analyzed: {total:,}")
    print(f"  Remote resolved:           {remote_count:,} "
          f"({remote_count/total*100:.1f}%)")
    print(f"  Estimated savings:         ${savings:,.0f}")
    print(f"{'─' * 60}")

    # 5. Final column selection and formatting
    df['pct_flooded']  = (df['pct_flooded'] * 100).round(1)   # Convert 0-1 to 0-100
    df['max_depth_ft'] = df['max_depth_ft'].round(2)

    final_cols = [
        'property_id', 'address', 'pct_flooded', 'max_depth_ft',
        'impact_class', 'confidence_score', 'recommended_action', 'adjuster_note'
    ]
    final_df = df[final_cols].copy()

    # 6. Save
    out_path = os.path.join(OUTPUT_DIR, f"{event_id}_final.csv")
    final_df.to_csv(out_path, index=False)
    print(f"\n✓ Final CSV saved → {out_path}")
    print(final_df.head(3).to_string(index=False))

    return final_df


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    harvey_final = run_triage_pipeline(HARVEY)
    ian_final    = run_triage_pipeline(IAN)

    print("\n" + "=" * 60)
    print("✓ Day 2 complete. Both final CSVs are ready for the Streamlit app.")
    print(f"  harvey_final.csv: {len(harvey_final)} properties")
    print(f"  ian_final.csv:    {len(ian_final)} properties")
    print("=" * 60)