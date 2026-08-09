#!/usr/bin/env python3
"""
accuracy_check.py — Validate Altis triage output against NFIP insurance claims.

WHAT CHANGED (Phase 0) AND WHY
------------------------------
This script previously validated against FEMA Individual Assistance housing
registrants. That ground truth had three defects, and the third one was fatal:

  1. Self-selected population. IA registrants are people who applied for
     federal aid. A carrier's insured book is close to the opposite
     population, so the comparison never really answered the carrier's
     question.
  2. Binary labels only. No depth, so the strongest available claim was
     "our spatial flood pattern is directionally consistent with where people
     applied for aid."
  3. Hurricane Ian is not in the dataset. The IA Housing Registrants endpoint
     returns count: 0 for DR-4673. Not a timeout and not rate limiting — the
     disaster simply is not in that table, so Ian could not be validated at
     all. Earlier fixes (retries, smaller pages) were chasing a symptom that
     was never the cause.

The replacement is the OpenFEMA NFIP Redacted Claims dataset: real insurance
claims, with a reported water depth and the dollar amount actually paid on
building and contents. See validation/nfip_claims.py for the API details and
for the `waterDepth` unit problem, which is real and is reported rather than
smoothed over.

TWO NUMBERS THAT ARE NOT THE SAME THING
---------------------------------------
  - `confidence_score` is DECISION confidence: how sure we are that this
    triage call is right, given sensor agreement, depth, recency.
  - `flood_probability` is a CALIBRATED PROBABILITY anchored to this ground
    truth: of properties scoring like this one, what fraction actually
    flooded.
They are deliberately kept separate throughout the codebase and must never be
conflated. This script fits the second one.

ZIP RESOLUTION, AND WHY THE HOLD-OUT IS GROUPED
------------------------------------------------
NFIP claims carry `reportedZipCode`. The `censusTract` field is empty in v3
for these events and lat/long is redacted to one decimal place (~11 km), so
ZIP is the finest honest join key. Labels are therefore zip-resolution, and
the calibration hold-out is grouped by zip (train and test zips disjoint) so
no label leaks across the split.

Usage:
    python validation/accuracy_check.py --event harvey
    python validation/accuracy_check.py --event ian
    python validation/accuracy_check.py               (both)
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

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / 'outputs'

# Make the pipeline's calibration core importable from this validation script.
sys.path.insert(0, str(BASE_DIR / 'pipeline'))
sys.path.insert(0, str(Path(__file__).parent))
import calibration as calib  # noqa: E402
import nfip_claims as nfip  # noqa: E402

# A zip is labelled "flooded" ground truth when at least this percentage of the
# NFIP policies in force there filed a claim for this event.
#
# This is a RATE, not a count, which is the whole point of paying for the
# policy-in-force denominator: it is a population statistic about insured
# structures, immune to the "bigger zip files more claims" artifact that a raw
# count would have. The threshold sits well above background — ordinary years
# produce claim rates far below 1% — while staying below the rates seen in
# genuinely inundated zips.
ZIP_CLAIM_RATE_THRESHOLD_PCT = 5.0

# Fallback when the policy denominator can't be retrieved: label on the share
# of claims reporting standing water above the reference level.
ZIP_DEPTH_LABEL_THRESHOLD_PCT = 50.0

# Legacy IA-based threshold, retained because derive_property_labels() still
# supports the old column for backward compatibility.
ZIP_FLOOD_LABEL_THRESHOLD = 0.5

# Triage classes treated as a positive ("predict flooded") decision.
POSITIVE_TRIAGE_CLASSES = ('Dispatch', 'Remote-Approve')

EVENT_META = {
    'harvey': {
        'label':        'Hurricane Harvey',
        'county':       'Harris County, TX',
        # Claims are selected on date of loss, which isolates the event
        # precisely. This is what makes the method work for Ian, which has no
        # usable disaster-number join in the claims table.
        'claims_start': '2017-08-25',
        'claims_end':   '2017-09-15',
        'as_of':        '2017-08-25',
    },
    'ian': {
        'label':        'Hurricane Ian',
        'county':       'Charlotte County, FL',
        'claims_start': '2022-09-28',
        'claims_end':   '2022-10-15',
        'as_of':        '2022-09-28',
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# ALTIS DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

ZIP_REGEX = re.compile(r'\b(\d{5})\b')


def extract_zip(address: str) -> Optional[str]:
    """
    Last-resort zip parse from an address string.

    RETAINED ONLY AS A FALLBACK. On the Harvey portfolio this approach found no
    zip at all for 300 of 1000 addresses and mistook street numbers for zips on
    others ("10005 Main Street, TX" -> 10005, lower Manhattan). Coordinate-based
    assignment via validation/zip_assign.py is the real path; see that module.
    """
    if not isinstance(address, str):
        return None
    matches = ZIP_REGEX.findall(address)
    return matches[-1] if matches else None


def load_altis_data(event_id: str, use_coordinates: bool = True) -> pd.DataFrame:
    """
    Load Altis triage output, joined to property coordinates, with a zip
    assigned to every property.

    Prefers coordinate-based ZIP assignment (point-in-polygon against Census
    ZCTA boundaries in Earth Engine) and falls back to address parsing only if
    Earth Engine is unavailable — reporting which path was taken, because the
    two produce measurably different validation sets.
    """
    final_path = OUTPUT_DIR / f"{event_id}_final.csv"
    if not final_path.exists():
        raise FileNotFoundError(
            f"{final_path} not found. Run pipeline/04_triage_notes.py for "
            f"'{event_id}' first.")

    df = pd.read_csv(final_path)
    df['property_id'] = df['property_id'].astype(str)

    props_path = OUTPUT_DIR / f"{event_id}_properties.csv"
    if props_path.exists():
        coords = pd.read_csv(props_path)[['property_id', 'latitude', 'longitude']]
        coords['property_id'] = coords['property_id'].astype(str)
        df = df.merge(coords, on='property_id', how='left')

    assigned = False
    if use_coordinates and {'latitude', 'longitude'} <= set(df.columns):
        try:
            from zip_assign import assign_zips
            zips = assign_zips(df[['property_id', 'latitude', 'longitude']],
                               cache_path=str(OUTPUT_DIR / f"{event_id}_zips.csv"))
            if not zips.empty:
                zips['property_id'] = zips['property_id'].astype(str)
                zips['zip'] = zips['zip'].astype(str).str.extract(r'^(\d{5})',
                                                                 expand=False)
                df = df.merge(zips, on='property_id', how='left')
                assigned = True
                print("  ZIP source: coordinates (Census ZCTA point-in-polygon)")
        except Exception as e:  # noqa: BLE001 - fall back, but say so
            print(f"  Coordinate ZIP assignment unavailable ({e}); "
                  f"falling back to address parsing.")

    if not assigned:
        df['zip'] = df['address'].apply(extract_zip)
        print("  ZIP source: address string parsing (fallback - less reliable)")

    missing = int(df['zip'].isna().sum())
    if missing:
        print(f"  Note: {missing}/{len(df)} properties have no ZIP and are "
              f"excluded from the zip-level comparison.")

    df = df.dropna(subset=['zip']).copy()
    df['zip'] = df['zip'].astype(str).str.zfill(5)
    df['flagged_flooded'] = df['impact_class'].isin(POSITIVE_TRIAGE_CLASSES)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# GROUND TRUTH ASSEMBLY
# ─────────────────────────────────────────────────────────────────────────────

def build_ground_truth(event_id: str, zips) -> tuple:
    """
    Assemble the NFIP ground truth for one event's zips.

    Returns (zip_agg, claims, diagnostics). `zip_agg` carries one row per zip
    with claim counts, depth statistics, paid amounts, and — when the policy
    denominator is retrievable — `nfip_claim_rate_pct`.
    """
    meta = EVENT_META[event_id]
    print(f"\n  Fetching NFIP claims ({meta['claims_start']} -> {meta['claims_end']}) "
          f"for {len(zips)} zips...")
    claims = nfip.fetch_event_claims(zips, meta['claims_start'], meta['claims_end'])

    diagnostics = {'unit_split': nfip.unit_split(claims), 'n_claims': len(claims)}
    if claims.empty:
        print("  No NFIP claims retrieved for this event/zip set.")
        return pd.DataFrame(), claims, diagnostics

    print(f"  Retrieved {len(claims):,} claims. "
          f"waterDepth interpretation: {diagnostics['unit_split'].get('counts')}")

    zip_agg = nfip.aggregate_by_zip(claims)

    print(f"  Fetching policy-in-force counts as of {meta['as_of']} "
          f"(the claim-rate denominator)...")
    policies = nfip.fetch_policies_in_force(zips, meta['as_of'])
    if not policies.empty:
        zip_agg = zip_agg.merge(policies, on='zip', how='left')
        with np.errstate(divide='ignore', invalid='ignore'):
            zip_agg['nfip_claim_rate_pct'] = (
                100.0 * zip_agg['nfip_claims'] / zip_agg['policies_in_force'])
        bad = ~np.isfinite(zip_agg['nfip_claim_rate_pct'].to_numpy(dtype=float))
        zip_agg.loc[bad, 'nfip_claim_rate_pct'] = np.nan
        diagnostics['policy_zips'] = int(policies['zip'].nunique())
    else:
        diagnostics['policy_zips'] = 0
        print("  Policy counts unavailable - falling back to depth-share labels.")

    return zip_agg, claims, diagnostics


def label_column_for(zip_agg: pd.DataFrame) -> tuple:
    """
    Choose the ground-truth column and threshold, preferring the claim RATE.

    Returns (column, threshold_pct, description). Falls back to the share of
    claims reporting standing water when the policy denominator is missing —
    a weaker label, and the report says so.
    """
    if ('nfip_claim_rate_pct' in zip_agg.columns and
            zip_agg['nfip_claim_rate_pct'].notna().sum() >= 3):
        return ('nfip_claim_rate_pct', ZIP_CLAIM_RATE_THRESHOLD_PCT,
                f"NFIP claim rate (claims per policy in force) "
                f">= {ZIP_CLAIM_RATE_THRESHOLD_PCT}%")
    return ('nfip_pct_depth_gt0', ZIP_DEPTH_LABEL_THRESHOLD_PCT,
            f"share of NFIP claims reporting standing water "
            f">= {ZIP_DEPTH_LABEL_THRESHOLD_PCT}% (no policy denominator "
            f"available - weaker label)")


# ─────────────────────────────────────────────────────────────────────────────
# COMPARISON
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_by_zip(zip_agg: pd.DataFrame, altis_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate Altis to zip level and merge against the NFIP ground truth."""
    if zip_agg.empty:
        return pd.DataFrame()

    altis_agg = altis_df.groupby('zip').agg(
        altis_properties      = ('property_id', 'count'),
        altis_pct_flagged     = ('flagged_flooded', lambda s: s.mean() * 100),
        altis_mean_depth_ft   = ('max_depth_ft', 'mean'),
        altis_max_depth_ft    = ('max_depth_ft', 'max'),
        altis_mean_confidence = ('confidence_score', 'mean'),
    ).reset_index()

    return zip_agg.merge(altis_agg, on='zip', how='inner')


def compute_metrics(merged: pd.DataFrame) -> dict:
    """
    Correlation metrics against the NFIP ground truth.

    The headline is `depth_corr`: Altis's mean detected depth against the mean
    depth adjusters actually recorded on settled claims, by zip. That is a
    continuous-to-continuous comparison, which is a materially stronger claim
    than the binary agreement the IA data supported.
    """
    metrics = {'zip_overlap_count': len(merged)}

    if len(merged) < 3:
        metrics['warning'] = (
            f"Only {len(merged)} overlapping zip codes — too few for a reliable "
            "correlation. Treat the numbers below as indicative only.")

    def corr(a, b):
        if a not in merged.columns or b not in merged.columns:
            return None
        valid = merged.dropna(subset=[a, b])
        if len(valid) < 3 or valid[a].nunique() < 2 or valid[b].nunique() < 2:
            return None
        return round(float(valid[a].corr(valid[b])), 3)

    metrics['depth_corr'] = corr('nfip_mean_depth_ft', 'altis_mean_depth_ft')
    metrics['median_depth_corr'] = corr('nfip_median_depth_ft', 'altis_mean_depth_ft')
    metrics['flagged_vs_claim_rate_corr'] = corr('nfip_claim_rate_pct',
                                                 'altis_pct_flagged')
    metrics['flagged_vs_depth_share_corr'] = corr('nfip_pct_depth_gt0',
                                                  'altis_pct_flagged')
    metrics['paid_vs_depth_corr'] = corr('nfip_paid_building', 'altis_mean_depth_ft')

    metrics['nfip_total_claims'] = int(merged['nfip_claims'].sum())
    metrics['altis_total_properties'] = int(merged['altis_properties'].sum())
    if 'policies_in_force' in merged.columns:
        total = merged['policies_in_force'].sum()
        metrics['policies_in_force'] = int(total) if pd.notna(total) else None
    return metrics


# ─────────────────────────────────────────────────────────────────────────────
# CALIBRATION & PER-PROPERTY LABELS
# ─────────────────────────────────────────────────────────────────────────────

def derive_property_labels(altis_df: pd.DataFrame, zip_agg: pd.DataFrame,
                           threshold: float = ZIP_FLOOD_LABEL_THRESHOLD,
                           rate_column: str = 'fema_pct_flood',
                           rate_is_pct: bool = True) -> pd.DataFrame:
    """
    Attach a binary ground-truth flood label to each Altis property from its
    zip's ground-truth rate.

    LABEL RESOLUTION IS ZIP-LEVEL: every property in a zip shares that zip's
    label. Honest but coarse, and exactly why the calibration split below must
    be grouped by zip.

    `rate_column` selects which ground-truth measure to threshold on. The
    default is the legacy FEMA IA column so existing callers and tests are
    unaffected; the NFIP path passes `nfip_claim_rate_pct` with its own
    threshold.

    Returns the subset of altis_df whose zip is present in the ground truth,
    with added columns: zip_truth_rate, flooded_truth, raw_flood_score.
    """
    if zip_agg.empty or rate_column not in zip_agg.columns:
        return altis_df.iloc[0:0].copy()

    rates = zip_agg.set_index('zip')[rate_column]
    if rate_is_pct:
        rates = rates / 100.0
        cutoff = threshold / 100.0 if threshold > 1 else threshold
    else:
        cutoff = threshold

    df = altis_df[altis_df['zip'].isin(rates.index)].copy()
    if df.empty:
        return df

    df['zip_truth_rate'] = df['zip'].map(rates)
    # Preserved for backward compatibility with existing consumers/tests.
    df['fema_zip_flood_rate'] = df['zip_truth_rate']
    df = df.dropna(subset=['zip_truth_rate'])
    if df.empty:
        return df

    df['flooded_truth'] = (df['zip_truth_rate'] >= cutoff).astype(int)

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
    human-verified, so they are strictly better truth than the zip-level label
    and take precedence. The most recent verdict per property wins.

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

    truth = labeled_df['flooded_truth'].astype(int)
    predicted_flood = labeled_df['impact_class'].isin(POSITIVE_TRIAGE_CLASSES).astype(int)
    out['overall'] = calib.classification_metrics(predicted_flood.values, truth.values)
    return out


def run_calibration(event_id: str, labeled_df: pd.DataFrame,
                    label_source: Optional[str] = None) -> Optional[dict]:
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
    result['label_source'] = label_source or (
        'FEMA Individual Assistance (zip-level flood-damage rate, '
        f'threshold={ZIP_FLOOD_LABEL_THRESHOLD})')
    result['label_resolution'] = 'zip_code'
    result['score_definition'] = '0.5*coverage_fraction + 0.5*min(depth_ft/3, 1)'
    result['triage_precision_recall'] = precision_recall_by_category(labeled_df)

    out_path = OUTPUT_DIR / f"calibration_{event_id}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f"  Calibration written -> {out_path}")

    labels_path = OUTPUT_DIR / f"{event_id}_labels.csv"
    keep = [c for c in ['property_id', 'zip', 'zip_truth_rate', 'fema_zip_flood_rate',
                        'flooded_truth', 'raw_flood_score', 'impact_class']
            if c in labeled_df.columns]
    labeled_df[keep].to_csv(labels_path, index=False)
    print(f"  Per-property labels written -> {labels_path}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def write_report(event_id: str, merged: pd.DataFrame, metrics: dict,
                 diagnostics: dict, label_desc: str,
                 calibration: Optional[dict] = None):
    meta = EVENT_META[event_id]
    out_path = OUTPUT_DIR / f"validation_{event_id}.md"

    def fmt_corr(c):
        if c is None:
            return "N/A (insufficient overlapping zip codes)"
        strength = ("strong" if abs(c) >= 0.6 else
                    "moderate" if abs(c) >= 0.35 else
                    "weak")
        return f"{c:+.3f} ({strength} {'positive' if c >= 0 else 'negative'})"

    units = (diagnostics.get('unit_split') or {}).get('counts', {})
    unit_total = (diagnostics.get('unit_split') or {}).get('total', 0)

    lines = [
        f"# Altis Accuracy Validation — {meta['label']}",
        "",
        "**Ground truth source:** OpenFEMA NFIP Redacted Claims v3 "
        f"(date of loss {meta['claims_start']} to {meta['claims_end']})",
        "",
        f"**Study area:** {meta['county']}",
        "",
        f"**Ground-truth label:** {label_desc}",
        "",
        "## Summary",
        "",
        f"- Zip codes compared: **{metrics['zip_overlap_count']}**",
        f"- NFIP claims in comparison: **{metrics['nfip_total_claims']:,}**",
        f"- Altis properties in comparison: **{metrics['altis_total_properties']:,}**",
    ]
    if metrics.get('policies_in_force'):
        lines.append(f"- NFIP policies in force (denominator): "
                     f"**{metrics['policies_in_force']:,}**")
    lines += [
        "",
        "## Correlation Metrics",
        "",
        "The headline number is the first one: Altis's satellite-derived mean "
        "depth against the mean water depth adjusters recorded on settled "
        "insurance claims, by zip. Both sides are continuous, which is a "
        "materially stronger test than the binary agreement the previous "
        "Individual Assistance ground truth could support.",
        "",
        f"- **Mean detected depth vs mean claimed water depth**, by zip: "
        f"{fmt_corr(metrics.get('depth_corr'))}",
        f"- Mean detected depth vs *median* claimed depth, by zip: "
        f"{fmt_corr(metrics.get('median_depth_corr'))}",
        f"- % flagged flooded vs NFIP claim rate, by zip: "
        f"{fmt_corr(metrics.get('flagged_vs_claim_rate_corr'))}",
        f"- % flagged flooded vs % claims reporting standing water, by zip: "
        f"{fmt_corr(metrics.get('flagged_vs_depth_share_corr'))}",
        f"- Mean paid building claim vs mean detected depth, by zip: "
        f"{fmt_corr(metrics.get('paid_vs_depth_corr'))}",
        "",
    ]

    if metrics.get('warning'):
        lines += [f"> {metrics['warning']}", ""]

    lines += [
        "## Data quality: the `waterDepth` unit ambiguity",
        "",
        "FEMA documents `waterDepth` as inches while noting that some records "
        "were entered in feet. That note describes the dominant behaviour, not "
        "an edge case, so this validation applies an explicit rule (values "
        f"<= {nfip.FEET_MAX} are read as feet, above that as inches) and "
        "reports the split rather than hiding it:",
        "",
        "| Interpretation | Claims |",
        "|---|---|",
    ]
    for k, v in sorted(units.items()):
        lines.append(f"| {k} | {v:,} |")
    lines += [
        f"| **total** | **{unit_total:,}** |",
        "",
        "The rule is justified by the damage data: mean damage ratio rises "
        "monotonically with the raw value, reaching ~0.6 around raw value 6. "
        "A 60% loss at six inches is not credible; at six feet it sits on a "
        "standard one-story residential depth-damage curve.",
        "",
        "## Zip-Level Detail",
        "",
        "| Zip | NFIP Claims | Claim Rate % | NFIP Mean Depth (ft) | "
        "Altis Properties | Altis % Flagged | Altis Mean Depth (ft) |",
        "|---|---|---|---|---|---|---|",
    ]

    def num(row, col, fmt="{:.2f}", dash="-"):
        if col not in row:
            return dash
        v = row.get(col)
        return dash if v is None or pd.isna(v) else fmt.format(v)

    for _, row in merged.sort_values('nfip_claims', ascending=False).iterrows():
        lines.append(
            f"| {row['zip']} | {int(row['nfip_claims'])} | "
            f"{num(row, 'nfip_claim_rate_pct', '{:.1f}')} | "
            f"{num(row, 'nfip_mean_depth_ft')} | "
            f"{int(row['altis_properties'])} | "
            f"{num(row, 'altis_pct_flagged', '{:.1f}')} | "
            f"{num(row, 'altis_mean_depth_ft')} |"
        )

    if calibration is not None:
        lines += _calibration_report_lines(calibration)

    lines += [
        "",
        "## Methodology & Limitations",
        "",
        "- NFIP claims are released at zip-code resolution. `censusTract` is "
        "empty in the v3 dataset for these events and latitude/longitude are "
        "redacted to one decimal place (~11 km), so zip is the finest honest "
        "join key. This compares zip-level aggregates, not individual "
        "properties.",
        "- The claim population is NFIP policyholders who filed. That is much "
        "closer to a carrier's insured book than the previous ground truth "
        "(self-selected federal aid applicants), but it still excludes "
        "uninsured structures and insured structures that chose not to file.",
        "- Reported water depth is recorded during claim settlement. It is "
        "adjuster-informed rather than instrumented, and carries the unit "
        "ambiguity described above.",
        "- Depth above GROUND is what the detector measures; NFIP depth is "
        "reported relative to the building. Phase 2 (first-floor height from "
        "the National Structure Inventory) is what closes that gap — until "
        "then a systematic offset of roughly the foundation height is "
        "expected, and it is larger for pier and crawlspace construction than "
        "for slab.",
        "- Labels are zip-resolution, so the calibration hold-out is grouped "
        "by zip (train and test zips disjoint) to prevent leakage.",
        "",
        "_Generated by validation/accuracy_check.py_",
    ]

    out_path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"\n  Report saved -> {out_path}")


def _calibration_report_lines(cal: dict) -> list:
    lines = ["", "## Calibrated Flood Probability (held-out)", ""]
    lines.append(f"- Labelled properties: **{cal['n_total']}** "
                 f"({cal['n_positive']} flooded-truth), split **{cal['split_kind']}** "
                 f"-> train {cal['n_train']} / test {cal['n_test']}")
    hm = cal.get('holdout_metrics')
    if not hm:
        lines += ["", f"> {cal.get('warning', 'Held-out metrics unavailable.')}", ""]
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
        "claims-anchored accuracy — not per-house verified ground truth.",
    ]
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_validation(event_id: str):
    print(f"\n{'=' * 60}")
    print(f"  Validating: {EVENT_META[event_id]['label']}")
    print(f"{'=' * 60}")

    altis_df = load_altis_data(event_id)
    print(f"  Loaded {len(altis_df)} Altis properties across "
          f"{altis_df['zip'].nunique()} zips")

    zips = sorted(altis_df['zip'].unique())
    zip_agg, claims, diagnostics = build_ground_truth(event_id, zips)
    if zip_agg.empty:
        print(f"  Skipping {event_id} — no NFIP ground truth retrieved.")
        return

    merged = aggregate_by_zip(zip_agg, altis_df)
    if merged.empty:
        print("  No overlapping zip codes between NFIP claims and Altis output.")
        return

    metrics = compute_metrics(merged)
    print(f"\n  Zip overlap:                  {metrics['zip_overlap_count']}")
    print(f"  Depth correlation (headline): {metrics['depth_corr']}")
    print(f"  Flagged vs claim-rate corr:   {metrics['flagged_vs_claim_rate_corr']}")

    label_col, label_thresh, label_desc = label_column_for(zip_agg)
    print(f"  Ground-truth label: {label_desc}")

    labeled = derive_property_labels(altis_df, zip_agg, threshold=label_thresh,
                                     rate_column=label_col)
    feedback = load_adjuster_feedback(event_id)
    labeled, n_human = merge_adjuster_labels(labeled, feedback)
    if n_human:
        print(f"  Merged {n_human} property-resolution adjuster labels.")
    print(f"  Labelled properties: {len(labeled)} "
          f"({int(labeled['flooded_truth'].sum()) if not labeled.empty else 0} "
          f"flooded-truth)")

    calibration = run_calibration(
        event_id, labeled,
        label_source=f"OpenFEMA NFIP Redacted Claims v3 — {label_desc}")
    if calibration and calibration.get('holdout_metrics'):
        hm = calibration['holdout_metrics']
        print(f"  Held-out Brier: {hm['brier_score']}  ECE: "
              f"{hm['expected_calibration_error']}  "
              f"(method={hm['method']}, n_test={calibration['n_test']})")

    write_report(event_id, merged, metrics, diagnostics, label_desc, calibration)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Validate Altis output against NFIP claims ground truth')
    parser.add_argument('--event', action='append', choices=['harvey', 'ian'],
                        help='Event to validate (repeatable). Default: both.')
    args = parser.parse_args()

    events = args.event or ['harvey', 'ian']
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for i, evt in enumerate(events):
        if i > 0:
            time.sleep(5)
        try:
            run_validation(evt)
        except FileNotFoundError as e:
            print(f"\n  {e}")
        except Exception as e:
            print(f"\n  Validation failed for {evt}: {e}")
