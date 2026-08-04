#!/usr/bin/env python3
"""
accuracy_check.py — Validate Altis triage output against FEMA ground truth.

WHY ZIP-CODE LEVEL:
FEMA's public Individual Assistance (IA) data is released at the zip-code
level, not per-address, to protect survivor privacy. This means we can't
validate "this exact house was flooded" — but we CAN validate "this zip
code had elevated flood damage" and check that against Altis's own
zip-level aggregation. This is the standard approach used in academic
remote-sensing flood validation studies, and it's defensible to a
carrier's actuarial team because it's the same ground truth FEMA itself
uses to release individual assistance funding.

DATA SOURCE:
OpenFEMA API — IndividualAssistanceHousingRegistrantsLargeDisasters
  https://www.fema.gov/api/open/v2/IndividualAssistanceHousingRegistrantsLargeDisasters
  Free, no API key required.

  Harvey -> disasterNumber 4332 (TX, declared Aug 25 2017)
  Ian    -> disasterNumber 4673 (FL, declared Sep 29 2022)

METRICS PRODUCED:
  1. Zip-level correlation: Altis mean depth vs FEMA mean self-reported water level
  2. Zip-level correlation: Altis % flagged-flooded vs FEMA % flood-damage registrants
  3. Confusion-style table: zips Altis calls "high impact" vs FEMA registration volume
  4. A markdown report saved to outputs/validation_{event_id}.md

LIMITATIONS (stated explicitly in the report, not hidden):
  - FEMA IA registrants are self-selected (renters/owners who applied for aid),
    not a random sample of all properties — likely undercounts well-insured
    or unaffected high-value properties relative to true flood extent.
  - Zip-code aggregation hides intra-zip variation that Altis resolves at
    the property level — this is comparing Altis's coarse aggregate to FEMA's
    coarse aggregate, not validating Altis's actual unit of analysis.
  - Self-reported water level in FEMA data is unverified at time of registration.

Usage:
    python validation/accuracy_check.py --event harvey
    python validation/accuracy_check.py --event ian
    python validation/accuracy_check.py --event harvey --event ian   (both)
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
import requests

BASE_DIR   = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / 'outputs'
# This specific dataset lives under v1, not v2 like most current OpenFEMA
# datasets — confirmed live (200) against v1, while v2 404s. Verified directly
# against fema.gov rather than assumed; do not "fix" this back to v2.
FEMA_API   = "https://www.fema.gov/api/open/v1/IndividualAssistanceHousingRegistrantsLargeDisasters"

# Make the pipeline's calibration core importable from this validation script.
sys.path.insert(0, str(BASE_DIR / 'pipeline'))
import calibration as calib  # noqa: E402

# A zip is labelled "flooded" ground-truth when at least this fraction of its
# FEMA IA registrants reported flood damage (vs wind-only). Surge/flood zones
# clear this; wind-damage-only zones do not. Documented and configurable.
ZIP_FLOOD_LABEL_THRESHOLD = 0.5
# Triage classes treated as a positive ("predict flooded") decision.
POSITIVE_TRIAGE_CLASSES = ('Dispatch', 'Remote-Approve')

EVENT_META = {
    'harvey': {
        'disaster_number': 4332,
        'county_filter':   'Harris (County)',
        'state':           'TX',
        'label':           'Hurricane Harvey',
    },
    'ian': {
        'disaster_number': 4673,
        'county_filter':   'Charlotte (County)',
        'state':           'FL',
        'label':           'Hurricane Ian',
    },
}

# Candidate field names — OpenFEMA has renamed fields across dataset versions.
# We probe the live schema first and fall back to this priority list.
ZIP_FIELD_CANDIDATES   = ['damagedZipCode', 'zipCode', 'damagedZip']
FLOOD_FIELD_CANDIDATES = ['floodDamage', 'floodDamageIndicator']
WATER_FIELD_CANDIDATES = ['waterLevel', 'floodWaterLevel', 'highWaterLevel']
COUNTY_FIELD_CANDIDATES = ['county', 'damagedCounty']


# ─────────────────────────────────────────────────────────────────────────────
# FEMA DATA FETCH
# ─────────────────────────────────────────────────────────────────────────────

def _fema_get(params: dict, timeout: int = 60, retries: int = 3, backoff: float = 5.0):
    """
    GET against FEMA_API with retries. The public OpenFEMA API is prone to
    slow responses under sustained request volume — observed in practice: a
    large paginated fetch for one disaster immediately followed by the very
    first request for the next one can trip a read timeout, even though the
    endpoint itself is healthy (retrying shortly after succeeds). Raises the
    last error if every attempt fails, so callers keep their existing
    failure handling unchanged.
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return requests.get(FEMA_API, params=params, timeout=timeout)
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                print(f"  FEMA request timed out/failed (attempt {attempt}/{retries}), "
                      f"retrying in {backoff:.0f}s...")
                time.sleep(backoff)
    raise last_err


def discover_fields() -> list[str]:
    """Probe the live OpenFEMA endpoint for one record to learn actual field names."""
    try:
        resp = _fema_get({'$top': 1})
        resp.raise_for_status()
        records = resp.json().get('IndividualAssistanceHousingRegistrantsLargeDisasters', [])
        if records:
            return list(records[0].keys())
    except Exception as e:
        print(f"  Warning: could not probe FEMA schema ({e}). Using known field names.")
    return []


def pick_field(available: list[str], candidates: list[str]) -> Optional[str]:
    if not available:
        return candidates[0]  # best guess if probe failed
    for c in candidates:
        if c in available:
            return c
    # Case-insensitive partial match fallback
    for c in candidates:
        for a in available:
            if c.lower() in a.lower():
                return a
    return None


def fetch_fema_data(event_id: str) -> pd.DataFrame:
    """
    Fetch all FEMA IA registrant records for an event's county, paginated.
    Returns a DataFrame with normalized columns: zip, flood_flag, water_level.
    """
    meta = EVENT_META[event_id]
    print(f"\nFetching FEMA IA data for {meta['label']} (DR-{meta['disaster_number']})...")

    available_fields = discover_fields()
    zip_field   = pick_field(available_fields, ZIP_FIELD_CANDIDATES)
    flood_field = pick_field(available_fields, FLOOD_FIELD_CANDIDATES)
    water_field = pick_field(available_fields, WATER_FIELD_CANDIDATES)
    county_field = pick_field(available_fields, COUNTY_FIELD_CANDIDATES)

    print(f"  Using fields: zip={zip_field}, flood={flood_field}, "
          f"water={water_field}, county={county_field}")

    all_records = []
    skip = 0
    # Smaller than Harvey's working 1000-row page size on purpose: Ian (DR-4673)
    # has a much larger underlying registrant volume (Hurricane Ian was one of
    # the costliest storms on record), and a full 1000-row page computation for
    # it was consistently exceeding FEMA's server-side response time even
    # though trivial small-$top requests for the same filter answered
    # instantly. Smaller pages trade more round trips for each one finishing
    # well inside the timeout.
    page_size = 200

    while True:
        filter_str = f"disasterNumber eq {meta['disaster_number']}"
        try:
            resp = _fema_get({
                '$filter': filter_str,
                '$top':    page_size,
                '$skip':   skip,
            })
            resp.raise_for_status()
            data    = resp.json()
            records = data.get('IndividualAssistanceHousingRegistrantsLargeDisasters', [])
        except Exception as e:
            print(f"  FEMA API request failed at skip={skip}: {e}")
            break

        if not records:
            break

        all_records.extend(records)
        print(f"  Fetched {len(all_records):,} records so far...")
        skip += page_size

        if len(records) < page_size:
            break
        if skip > 200_000:  # safety cap
            print("  Reached safety cap of 200k records, stopping pagination.")
            break
        time.sleep(0.2)

    if not all_records:
        print("  No FEMA records retrieved. Check network access or disaster number.")
        return pd.DataFrame()

    df = pd.DataFrame(all_records)

    # Filter to target county if county field exists (some events span multiple counties)
    if county_field and county_field in df.columns:
        before = len(df)
        df = df[df[county_field].astype(str).str.contains(
            meta['county_filter'].split(' (')[0], case=False, na=False)]
        print(f"  Filtered to {meta['county_filter']}: {before:,} -> {len(df):,} records")

    # Normalize output columns
    out = pd.DataFrame()
    out['zip'] = df[zip_field].astype(str).str[:5] if zip_field in df.columns else None

    if flood_field and flood_field in df.columns:
        out['flood_flag'] = df[flood_field].apply(_to_bool)
    else:
        out['flood_flag'] = None

    if water_field and water_field in df.columns:
        out['water_level'] = pd.to_numeric(df[water_field], errors='coerce')
    else:
        out['water_level'] = None

    out = out.dropna(subset=['zip'])
    out = out[out['zip'].str.match(r'^\d{5}$', na=False)]

    print(f"  Final FEMA dataset: {len(out):,} registrant records across "
          f"{out['zip'].nunique()} zip codes")
    return out


def _to_bool(val) -> Optional[bool]:
    if pd.isna(val):
        return None
    s = str(val).strip().lower()
    if s in ('y', 'yes', 'true', '1'):
        return True
    if s in ('n', 'no', 'false', '0'):
        return False
    return None


# ─────────────────────────────────────────────────────────────────────────────
# ALTIS DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

ZIP_REGEX = re.compile(r'\b(\d{5})\b')

def extract_zip(address: str) -> Optional[str]:
    if not isinstance(address, str):
        return None
    matches = ZIP_REGEX.findall(address)
    return matches[-1] if matches else None  # zip is usually the last 5-digit group


def load_altis_data(event_id: str) -> pd.DataFrame:
    """Load Altis final triage CSV and extract zip codes from addresses."""
    path = OUTPUT_DIR / f"{event_id}_final.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run pipeline/04_triage_notes.py for '{event_id}' first."
        )

    df = pd.read_csv(path)

    if 'zip' not in df.columns:
        df['zip'] = df['address'].apply(extract_zip)

    missing = df['zip'].isna().sum()
    if missing:
        print(f"  Note: {missing}/{len(df)} properties had no extractable zip code "
              f"and will be excluded from zip-level comparison.")

    df = df.dropna(subset=['zip'])
    df['flagged_flooded'] = df['impact_class'].isin(['Dispatch', 'Remote-Approve'])
    return df


# ─────────────────────────────────────────────────────────────────────────────
# COMPARISON
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_by_zip(fema_df: pd.DataFrame, altis_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate both datasets to zip level and merge for comparison."""
    fema_agg = fema_df.groupby('zip').agg(
        fema_registrants = ('zip', 'count'),
        fema_pct_flood    = ('flood_flag', lambda s: s.mean() * 100 if s.notna().any() else np.nan),
        fema_mean_water   = ('water_level', 'mean'),
    ).reset_index()

    altis_agg = altis_df.groupby('zip').agg(
        altis_properties     = ('property_id', 'count'),
        altis_pct_flagged     = ('flagged_flooded', lambda s: s.mean() * 100),
        altis_mean_depth_ft   = ('max_depth_ft', 'mean'),
        altis_mean_confidence = ('confidence_score', 'mean'),
    ).reset_index()

    merged = fema_agg.merge(altis_agg, on='zip', how='inner')
    return merged


def compute_metrics(merged: pd.DataFrame) -> dict:
    """Compute correlation and agreement metrics."""
    metrics = {}

    if len(merged) < 3:
        metrics['warning'] = (
            f"Only {len(merged)} overlapping zip codes — too few for reliable "
            "correlation. Results below are indicative only."
        )

    # % flagged correlation
    valid = merged.dropna(subset=['fema_pct_flood', 'altis_pct_flagged'])
    if len(valid) >= 3:
        metrics['pct_flood_corr'] = round(
            valid['fema_pct_flood'].corr(valid['altis_pct_flagged']), 3)
    else:
        metrics['pct_flood_corr'] = None

    # Depth / water level correlation
    valid_w = merged.dropna(subset=['fema_mean_water', 'altis_mean_depth_ft'])
    if len(valid_w) >= 3:
        metrics['depth_water_corr'] = round(
            valid_w['fema_mean_water'].corr(valid_w['altis_mean_depth_ft']), 3)
    else:
        metrics['depth_water_corr'] = None

    metrics['zip_overlap_count']    = len(merged)
    metrics['fema_total_zips']      = merged['zip'].nunique()
    metrics['fema_total_registrants'] = int(merged['fema_registrants'].sum())
    metrics['altis_total_properties'] = int(merged['altis_properties'].sum())

    return metrics


# ─────────────────────────────────────────────────────────────────────────────
# CALIBRATION & PER-PROPERTY LABELS (Round 3)
# ─────────────────────────────────────────────────────────────────────────────

def derive_property_labels(altis_df: pd.DataFrame, fema_agg: pd.DataFrame,
                           threshold: float = ZIP_FLOOD_LABEL_THRESHOLD) -> pd.DataFrame:
    """
    Attach a binary ground-truth flood label to each Altis property from the
    FEMA flood-damage rate of its zip code.

    LABEL RESOLUTION IS ZIP-LEVEL (FEMA's release granularity): every property
    in a given zip shares that zip's label. This is honest but coarse, and it is
    exactly why the calibration split below must be grouped by zip.

    Returns the subset of altis_df (properties whose zip is present in the FEMA
    data) with added columns: fema_zip_flood_rate, flooded_truth, raw_flood_score.
    """
    fema_rate = fema_agg.set_index('zip')['fema_pct_flood'] / 100.0  # back to 0-1
    df = altis_df.copy()
    df = df[df['zip'].isin(fema_rate.index)].copy()
    if df.empty:
        return df
    df['fema_zip_flood_rate'] = df['zip'].map(fema_rate)
    df = df.dropna(subset=['fema_zip_flood_rate'])
    df['flooded_truth'] = (df['fema_zip_flood_rate'] >= threshold).astype(int)

    # pct_flooded in the final CSV is a 0-100 percentage; convert back to 0-1.
    pct_frac = pd.to_numeric(df['pct_flooded'], errors='coerce').fillna(0.0) / 100.0
    depth = pd.to_numeric(df['max_depth_ft'], errors='coerce').fillna(0.0)
    df['raw_flood_score'] = [
        calib.raw_flood_score(p, d) for p, d in zip(pct_frac, depth)]
    return df


def adjuster_label(agree, corrected_class: str, impact_class: str) -> Optional[int]:
    """
    Convert one adjuster verdict into a property-resolution flood label (1/0/None).

    Priority of signal:
      1. An explicit corrected class is the strongest signal — the adjuster is
         telling us what the property actually is.
      2. Otherwise, agreement means "the original call was right" → that call's
         positivity is the truth.
      3. Disagreement without a correction flips the original call's positivity
         (they're saying it's wrong, just not what to instead).
    Returns None when the verdict carries no usable signal.
    """
    positive = set(POSITIVE_TRIAGE_CLASSES)
    cc = (corrected_class or '').strip()
    if cc:
        return 1 if cc in positive else 0

    own_positive = 1 if impact_class in positive else 0
    if agree in (1, True, '1', 'true', 'True'):
        return own_positive
    if agree in (0, False, '0', 'false', 'False'):
        return 1 - own_positive
    return None


def merge_adjuster_labels(labeled_df: pd.DataFrame,
                          feedback_df: pd.DataFrame) -> tuple:
    """
    Override zip-derived ground truth with property-resolution human labels
    wherever adjusters have weighed in. Adjuster verdicts are per-house and
    human-verified, so they are strictly better truth than the zip-level FEMA
    label and take precedence. The most recent verdict per property wins.

    Returns (merged_df, n_human_labeled). Pure: inputs are not mutated, no DB
    or network access — the caller supplies the feedback frame.
    """
    df = labeled_df.copy()
    df['human_labeled'] = 0
    if feedback_df is None or len(feedback_df) == 0 or df.empty:
        return df, 0

    fb = feedback_df.copy()
    if 'created_at' in fb.columns:
        fb = fb.sort_values('created_at')
    latest = fb.groupby('property_id').tail(1)

    impact_by_id = df.set_index('property_id')['impact_class'].to_dict()
    label_map = {}
    for _, r in latest.iterrows():
        pid = r['property_id']
        impact = impact_by_id.get(pid, r.get('original_class', ''))
        lab = adjuster_label(r.get('agree'), r.get('corrected_class', ''), impact)
        if lab is not None and pid in impact_by_id:
            label_map[pid] = lab

    if not label_map:
        return df, 0

    mask = df['property_id'].isin(label_map)
    df.loc[mask, 'flooded_truth'] = df.loc[mask, 'property_id'].map(label_map).astype(int)
    df.loc[mask, 'human_labeled'] = 1
    return df, int(mask.sum())


def load_adjuster_feedback(event_id: str) -> pd.DataFrame:
    """
    Best-effort load of stored adjuster feedback for an event from the backend
    DB. Returns an empty frame if the backend isn't importable or there's no
    feedback yet — validation must never hard-depend on the API being present.
    """
    try:
        sys.path.insert(0, str(BASE_DIR))
        from backend import database as db
        rows = db.get_feedback_for_event(event_id)
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  (No adjuster feedback merged: {e})")
        return pd.DataFrame()


def precision_recall_by_category(labeled_df: pd.DataFrame) -> dict:
    """
    For each triage category, report how its members line up with ground truth:
    n, the share truly flooded ("flood precision" of that category), and the
    share truly dry. Plus an overall precision/recall treating Dispatch+
    Remote-Approve as the positive (predicted-flooded) decision.
    """
    out = {'by_category': {}, 'overall': None}
    if labeled_df.empty:
        return out

    truth = labeled_df['flooded_truth'].astype(int)
    for cat in ['Dispatch', 'Remote-Approve', 'Remote-Deny', 'Review']:
        members = labeled_df[labeled_df['impact_class'] == cat]
        n = len(members)
        if n == 0:
            out['by_category'][cat] = {'n': 0, 'pct_truly_flooded': None,
                                       'pct_truly_dry': None}
            continue
        t = members['flooded_truth'].astype(int)
        out['by_category'][cat] = {
            'n': int(n),
            'pct_truly_flooded': round(float(t.mean()) * 100, 1),
            'pct_truly_dry': round(float((1 - t).mean()) * 100, 1),
        }

    predicted_flood = labeled_df['impact_class'].isin(POSITIVE_TRIAGE_CLASSES).astype(int)
    out['overall'] = calib.classification_metrics(predicted_flood.values, truth.values)
    return out


def run_calibration(event_id: str, labeled_df: pd.DataFrame) -> Optional[dict]:
    """
    Fit a calibrated flood-probability map (raw_flood_score -> P(flooded)) with
    a zip-grouped hold-out so the reported numbers are honest, attach
    precision/recall by triage category, and persist to calibration_{event}.json.
    """
    if labeled_df.empty or labeled_df['flooded_truth'].nunique() < 2:
        print("  Calibration skipped — need both flooded and dry labelled zips.")
        return None

    result = calib.fit_and_evaluate(
        scores=labeled_df['raw_flood_score'].values,
        labels=labeled_df['flooded_truth'].values,
        groups=labeled_df['zip'].values,
        method='auto',
    )
    result['event_id'] = event_id
    result['label_source'] = (
        'FEMA Individual Assistance (zip-level flood-damage rate, '
        f'threshold={ZIP_FLOOD_LABEL_THRESHOLD})')
    result['label_resolution'] = 'zip_code'
    result['score_definition'] = '0.5*coverage_fraction + 0.5*min(depth_ft/3, 1)'
    result['triage_precision_recall'] = precision_recall_by_category(labeled_df)

    out_path = OUTPUT_DIR / f"calibration_{event_id}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f"  ✓ Calibration written -> {out_path}")

    # Also persist the per-property labels for auditing / re-use.
    labels_path = OUTPUT_DIR / f"{event_id}_labels.csv"
    labeled_df[['property_id', 'zip', 'fema_zip_flood_rate', 'flooded_truth',
                'raw_flood_score', 'impact_class']].to_csv(labels_path, index=False)
    print(f"  ✓ Per-property labels written → {labels_path}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def write_report(event_id: str, merged: pd.DataFrame, metrics: dict,
                 calibration: Optional[dict] = None):
    meta = EVENT_META[event_id]
    out_path = OUTPUT_DIR / f"validation_{event_id}.md"

    corr_pct   = metrics.get('pct_flood_corr')
    corr_depth = metrics.get('depth_water_corr')

    def fmt_corr(c):
        if c is None:
            return "N/A (insufficient overlapping zip codes)"
        strength = ("strong" if abs(c) >= 0.6 else
                    "moderate" if abs(c) >= 0.35 else
                    "weak")
        return f"{c:+.3f} ({strength} {'positive' if c >= 0 else 'negative'} correlation)"

    lines = [
        f"# Altis Accuracy Validation — {meta['label']}",
        "",
        f"**Ground truth source:** FEMA OpenFEMA Individual Assistance "
        f"(DR-{meta['disaster_number']}, {meta['county_filter']})",
        "",
        "## Summary",
        "",
        f"- Zip codes compared: **{metrics['zip_overlap_count']}**",
        f"- FEMA IA registrants in comparison: **{metrics['fema_total_registrants']:,}**",
        f"- Altis properties in comparison: **{metrics['altis_total_properties']:,}**",
        "",
        "## Correlation Metrics",
        "",
        f"- **% flagged-flooded** (Altis Dispatch+Remote-Approve) vs "
        f"**% FEMA flood-damage registrants**, by zip: {fmt_corr(corr_pct)}",
        f"- **Mean depth (ft)** (Altis) vs **mean self-reported water level** "
        f"(FEMA), by zip: {fmt_corr(corr_depth)}",
        "",
    ]

    if metrics.get('warning'):
        lines += [f"> ⚠ {metrics['warning']}", ""]

    lines += [
        "## Zip-Level Detail",
        "",
        "| Zip | FEMA Registrants | FEMA % Flood | Altis Properties | "
        "Altis % Flagged | Altis Mean Depth (ft) |",
        "|---|---|---|---|---|---|",
    ]

    for _, row in merged.sort_values('fema_registrants', ascending=False).iterrows():
        lines.append(
            f"| {row['zip']} | {int(row['fema_registrants'])} | "
            f"{row['fema_pct_flood']:.1f}% | {int(row['altis_properties'])} | "
            f"{row['altis_pct_flagged']:.1f}% | {row['altis_mean_depth_ft']:.2f} |"
        )

    if calibration is not None:
        lines += _calibration_report_lines(calibration)

    lines += [
        "",
        "## Methodology & Limitations",
        "",
        "- FEMA IA data is released at zip-code level only — this validation "
        "compares zip-level aggregates, not individual properties.",
        "- FEMA registrants are self-selected applicants for federal aid, not "
        "a random or complete sample of affected properties.",
        "- Self-reported water level in FEMA data is unverified at time of "
        "registration and may not reflect peak depth.",
        "- A positive, moderate-to-strong correlation supports that Altis's "
        "spatial flood pattern is directionally consistent with independently "
        "reported ground damage. It does not constitute property-level validation.",
        "",
        f"_Generated by validation/accuracy_check.py_",
    ]

    out_path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"\n✓ Report saved -> {out_path}")


def _calibration_report_lines(cal: dict) -> list:
    lines = ["", "## Calibrated Flood Probability (held-out)", ""]
    lines.append(f"- Labelled properties: **{cal['n_total']}** "
                 f"({cal['n_positive']} flooded-truth), split **{cal['split_kind']}** "
                 f"→ train {cal['n_train']} / test {cal['n_test']}")
    hm = cal.get('holdout_metrics')
    if not hm:
        lines += ["", f"> ⚠ {cal.get('warning', 'Held-out metrics unavailable.')}", ""]
        return lines
    lines += [
        f"- Calibration method: **{hm['method']}**",
        f"- **Brier score:** {hm['brier_score']} (lower is better; "
        "0 is perfect, 0.25 is uninformative)",
        f"- **Expected calibration error:** {hm['expected_calibration_error']} "
        "(lower is better)",
        "",
        "### Precision / Recall by Triage Category (held-out positive = "
        "Dispatch + Remote-Approve)",
        "",
    ]
    cls = hm.get('classification', {})
    lines += [
        f"- Precision: **{cls.get('precision')}**, Recall: **{cls.get('recall')}**, "
        f"F1: **{cls.get('f1')}** (n={cls.get('support')})",
        "",
        "| Category | n | % truly flooded | % truly dry |",
        "|---|---|---|---|",
    ]
    pr = cal.get('triage_precision_recall', {}).get('by_category', {})
    for cat in ['Dispatch', 'Remote-Approve', 'Remote-Deny', 'Review']:
        c = pr.get(cat, {})
        lines.append(f"| {cat} | {c.get('n', 0)} | "
                     f"{c.get('pct_truly_flooded')} | {c.get('pct_truly_dry')} |")
    lines += [
        "",
        f"_Label source: {cal.get('label_source')}. "
        f"Score: {cal.get('score_definition')}._",
        "> Labels are zip-resolution, so the hold-out is grouped by zip (train "
        "and test zips disjoint) to avoid leakage. Treat these as directional, "
        "FEMA-anchored accuracy — not per-house verified ground truth.",
    ]
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_validation(event_id: str):
    print(f"\n{'=' * 60}")
    print(f"  Validating: {EVENT_META[event_id]['label']}")
    print(f"{'=' * 60}")

    fema_df = fetch_fema_data(event_id)
    if fema_df.empty:
        print(f"  Skipping {event_id} — no FEMA data retrieved.")
        return

    altis_df = load_altis_data(event_id)
    print(f"  Loaded {len(altis_df)} Altis properties with zip codes")

    merged = aggregate_by_zip(fema_df, altis_df)
    if merged.empty:
        print("  No overlapping zip codes between FEMA data and Altis output. "
              "Check that county/disaster filters match your event's bounding box.")
        return

    metrics = compute_metrics(merged)

    print(f"\n  Zip overlap:        {metrics['zip_overlap_count']}")
    print(f"  % flood correlation: {metrics['pct_flood_corr']}")
    print(f"  Depth/water correlation: {metrics['depth_water_corr']}")

    # Round 3: per-property labels + calibrated flood probability + precision/
    # recall by triage category, on a zip-grouped hold-out (the honest number).
    labeled = derive_property_labels(altis_df, merged)
    # Human-in-the-loop: where adjusters have given verdicts, their per-house
    # labels override the coarse zip-level FEMA truth.
    feedback = load_adjuster_feedback(event_id)
    labeled, n_human = merge_adjuster_labels(labeled, feedback)
    if n_human:
        print(f"  Merged {n_human} property-resolution adjuster labels "
              f"(override zip-level truth).")
    print(f"  Labelled properties: {len(labeled)} "
          f"({int(labeled['flooded_truth'].sum()) if not labeled.empty else 0} flooded-truth)")
    calibration = run_calibration(event_id, labeled)
    if calibration and calibration.get('holdout_metrics'):
        hm = calibration['holdout_metrics']
        print(f"  Held-out Brier: {hm['brier_score']}  ECE: "
              f"{hm['expected_calibration_error']}  "
              f"(method={hm['method']}, n_test={calibration['n_test']})")

    write_report(event_id, merged, metrics, calibration)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Validate Altis output against FEMA IA data')
    parser.add_argument('--event', action='append', choices=['harvey', 'ian'],
                        help='Event to validate (repeatable). Default: both.')
    args = parser.parse_args()

    events = args.event or ['harvey', 'ian']
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for i, evt in enumerate(events):
        if i > 0:
            # A brief cooldown between events. A large paginated fetch for one
            # disaster (Harvey alone can be 200+ sequential requests) followed
            # immediately by the first request for the next one has been
            # observed to trip a read timeout on FEMA's side even though nothing
            # is actually wrong — this gives their API a moment to recover.
            time.sleep(5)
        try:
            run_validation(evt)
        except FileNotFoundError as e:
            print(f"\n  {e}")
        except Exception as e:
            print(f"\n  Validation failed for {evt}: {e}")
