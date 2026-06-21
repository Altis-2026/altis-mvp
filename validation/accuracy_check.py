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
FEMA_API   = "https://www.fema.gov/api/open/v2/IndividualAssistanceHousingRegistrantsLargeDisasters"

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

def discover_fields() -> list[str]:
    """Probe the live OpenFEMA endpoint for one record to learn actual field names."""
    try:
        resp = requests.get(FEMA_API, params={'$top': 1}, timeout=20)
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
    page_size = 1000

    while True:
        filter_str = f"disasterNumber eq {meta['disaster_number']}"
        try:
            resp = requests.get(FEMA_API, params={
                '$filter': filter_str,
                '$top':    page_size,
                '$skip':   skip,
            }, timeout=30)
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
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def write_report(event_id: str, merged: pd.DataFrame, metrics: dict):
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

    out_path.write_text('\n'.join(lines))
    print(f"\n✓ Report saved → {out_path}")


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

    write_report(event_id, merged, metrics)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Validate Altis output against FEMA IA data')
    parser.add_argument('--event', action='append', choices=['harvey', 'ian'],
                        help='Event to validate (repeatable). Default: both.')
    args = parser.parse_args()

    events = args.event or ['harvey', 'ian']
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for evt in events:
        try:
            run_validation(evt)
        except FileNotFoundError as e:
            print(f"\n  {e}")
        except Exception as e:
            print(f"\n  Validation failed for {evt}: {e}")
