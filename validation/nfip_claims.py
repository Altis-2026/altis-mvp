#!/usr/bin/env python3
"""
nfip_claims.py — OpenFEMA NFIP Redacted Claims as validation ground truth.

WHY THIS REPLACES INDIVIDUAL ASSISTANCE (IA) REGISTRANTS
--------------------------------------------------------
The previous ground truth was FEMA Individual Assistance housing registrants:
self-selected applicants for federal aid, aggregated to zip, carrying only a
binary "flood damage" flag. Three problems with it, in increasing severity:

  1. Self-selection. Registrants are people who applied for aid. Well-insured
     households often don't, so the sample systematically under-represents
     exactly the properties a carrier cares about.
  2. Binary labels only. There is no depth to correlate against, so the
     strongest claim available was "our flood pattern is directionally
     consistent with aid applications."
  3. Coverage gaps. Hurricane Ian (DR-4673) simply is not present in the IA
     Housing Registrants table — the endpoint returns count: 0. Validation for
     Ian was impossible, not merely noisy.

NFIP Redacted Claims fixes all three. These are actual insurance claims:
adjuster-settled, with a reported water depth and the dollar amount paid on
building and contents separately. The population is "NFIP policyholders who
filed", which is far closer to a carrier's book than "people who applied for
federal aid".

ENDPOINT VERSION
----------------
v3 (`NfipClaims`). The v2 endpoint (`FimaNfipClaims`) is DEPRECATED: it is
frozen as of 2026-06-01 and is scheduled for removal on 2026-10-15. v3 also
drops the `FIMA` prefix from the entity name, so the JSON response key differs.

THE waterDepth UNIT PROBLEM — READ THIS BEFORE TRUSTING ANY DEPTH NUMBER
------------------------------------------------------------------------
FEMA's own data dictionary defines `waterDepth` as:

    "Depth of flood water in inches. Note: there are instances where
     measurements were provided in feet."

That note is not a footnote, it is the dominant behaviour in modern events.
Empirically, on Harris County 2017 (Harvey) and Lee/Charlotte County 2022
(Ian), ~91-99% of non-null values fall in [-30, 15], with a median of 0-1.
Read as inches, that would mean the median NFIP claim in Harris County during
Harvey involved one inch of water — while paying out a mean of ~33% of the
building's value.

The damage data disambiguates it. Mean damage ratio (buildingDamageAmount /
buildingPropertyValue) rises monotonically with the raw value:

    raw=0 -> 0.26    raw=3 -> 0.50    raw=6 -> 0.61
    raw=1 -> 0.33    raw=4 -> 0.51    raw=7 -> 0.58
    raw=2 -> 0.45    raw=5 -> 0.50    raw=10 -> 0.56

A 61% loss ratio at six *inches* is not physically credible; at six *feet* it
sits right on a standard one-story residential depth-damage curve. The small
tail above 15 behaves like genuine inches entries (Charlotte County has a
distinct spike at 120 = 10 ft, consistent with Ian's surge there).

So the rule below treats <= FEET_MAX as feet and > FEET_MAX as inches, and
records which branch every claim took so the report can state the split rather
than hide it. Values outside [MIN_VALID_FT_EQUIV, MAX_VALID_IN] are dropped as
uninterpretable rather than clamped into looking reasonable.

This is a real ambiguity in the source data. We do not resolve it perfectly and
we do not pretend to: `depth_unit_assumed` travels with every record, and the
aggregate report prints the share of each branch.

GRANULARITY
-----------
Claims carry `reportedZipCode` (100% populated). `censusTract` is present in
the schema but empty in v3 for these events, and `latitude`/`longitude` are
redacted to one decimal place (~11 km), so ZIP is the finest honest join key.
"""
import time
from typing import Iterable, Optional

import pandas as pd
import requests

CLAIMS_API   = "https://www.fema.gov/api/open/v3/NfipClaims"
POLICIES_API = "https://www.fema.gov/api/open/v3/NfipPolicies"

# ── waterDepth interpretation bounds (see module docstring) ──────────────────
FEET_MAX          = 15     # raw value <= this is read as FEET
MAX_VALID_IN      = 200    # raw value above this is uninterpretable -> dropped
MIN_VALID_FT      = -30    # below this is uninterpretable -> dropped

# Fields we actually use. Requesting a subset keeps payloads small; OpenFEMA
# rejects nothing if a field is absent, so this stays safe across versions.
CLAIM_FIELDS = [
    'reportedZipCode', 'dateOfLoss', 'yearOfLoss', 'countyCode', 'state',
    'waterDepth', 'floodWaterDuration',
    'amountPaidOnBuildingClaim', 'amountPaidOnContentsClaim',
    'buildingDamageAmount', 'contentsDamageAmount', 'buildingPropertyValue',
    'occupancyType', 'numberOfFloorsInTheInsuredBuilding',
    'elevatedBuildingIndicator', 'basementEnclosureCrawlspaceType',
    'lowestFloorElevation', 'lowestAdjacentGrade', 'elevationDifference',
    'ratedFloodZone', 'originalConstructionDate', 'causeOfDamage',
]


def normalize_water_depth(raw) -> tuple:
    """
    Map a raw NFIP `waterDepth` to (depth_ft, unit_assumed).

    Returns (None, 'invalid') for nulls and out-of-range values, so callers
    drop them explicitly instead of silently coercing garbage to 0.0.
    """
    if raw is None:
        return None, 'null'
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None, 'invalid'

    if v < MIN_VALID_FT or v > MAX_VALID_IN:
        return None, 'invalid'
    if v <= FEET_MAX:
        return v, 'feet'
    return v / 12.0, 'inches'


def _zip_filter(zips: Iterable[str]) -> str:
    """
    OR-chain of zip equality tests.

    OpenFEMA's `in (...)` operator returns HTTP 503 on lists of this size, so
    an explicit OR chain is the working form. Chunking is the caller's job.
    """
    return " or ".join(f"reportedZipCode eq '{z}'" for z in zips)


def _get(url: str, params: dict, timeout: int = 120, retries: int = 5) -> Optional[dict]:
    """
    GET with retry and exponential backoff.

    OpenFEMA answers with an HTML error page (HTTP 503) rather than JSON under
    load, frequently enough that this is the normal case, not an edge case —
    observed failing and then succeeding on the identical query seconds apart.
    So we verify the CONTENT TYPE, not just the status code. Trusting the
    status code alone is the exact mistake that made the Hurricane Ian
    validation failure look like a timeout when the real answer was an empty
    result set.
    """
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 200 and \
                    resp.headers.get('content-type', '').startswith('application/json'):
                return resp.json()
            reason = f"http {resp.status_code}, content-type " \
                     f"{resp.headers.get('content-type', '?')}"
        except Exception as e:  # noqa: BLE001 - surfaced to caller below
            reason = f"{type(e).__name__}: {e}"
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    print(f"    OpenFEMA request failed after {retries} attempts ({reason})")
    return None


def fetch_event_claims(zips: Iterable[str], start_date: str, end_date: str,
                       zip_chunk: int = 40, page_size: int = 10000,
                       verbose: bool = True) -> pd.DataFrame:
    """
    Fetch NFIP claims whose date of loss falls inside [start_date, end_date]
    for the given zip codes.

    Filtering on dateOfLoss (rather than yearOfLoss or a disaster number)
    isolates the event precisely and is what makes this work for Ian, which has
    no usable disaster-number join in the claims table.

    PAGE SIZE AND CHUNKING ARE PERFORMANCE-CRITICAL, measured not guessed:
      - `$top=10000` is OpenFEMA's maximum and returns in ~13s. At the default
        1000 the same fetch needs 10x the requests, and deep `$skip` offsets
        get progressively slower — Harvey's 16,578 claims took over 20 minutes
        that way versus well under a minute at 10000.
      - A wide OR chain is CHEAPER than narrow ones, not more expensive: all 39
        Harvey zips in one filter answered in 0.6s, while a single-zip query
        for the same window took 19s. So chunk wide, not narrow.

    Returns a DataFrame with normalized columns:
        zip, depth_ft, depth_unit_assumed, paid_building, paid_contents,
        damage_building, property_value, occupancy_type, n_floors,
        elevated, basement_type, lowest_floor_elev, lowest_adjacent_grade,
        duration_raw, flood_zone, cause_of_damage
    """
    zips = sorted({str(z).strip() for z in zips if str(z).strip()})
    if not zips:
        return pd.DataFrame()

    date_clause = f"dateOfLoss ge '{start_date}' and dateOfLoss le '{end_date}'"
    records = []

    for i in range(0, len(zips), zip_chunk):
        chunk = zips[i:i + zip_chunk]
        flt = f"({_zip_filter(chunk)}) and {date_clause}"
        skip = 0
        while True:
            data = _get(CLAIMS_API, {
                '$filter': flt,
                '$select': ",".join(CLAIM_FIELDS),
                '$top': page_size,
                '$skip': skip,
                '$format': 'json',
            })
            if data is None:
                break
            page = data.get('NfipClaims', [])
            if not page:
                break
            records.extend(page)
            skip += page_size
            if len(page) < page_size:
                break
            time.sleep(0.2)
        if verbose:
            print(f"    zips {i + 1}-{i + len(chunk)} of {len(zips)}: "
                  f"{len(records):,} claims so far")

    if not records:
        return pd.DataFrame()

    raw = pd.DataFrame(records)
    depths = raw['waterDepth'].apply(normalize_water_depth) \
        if 'waterDepth' in raw.columns else pd.Series([(None, 'null')] * len(raw))

    out = pd.DataFrame({
        'zip':                 raw.get('reportedZipCode', pd.Series(dtype=str)).astype(str).str[:5],
        'depth_ft':            [d for d, _ in depths],
        'depth_unit_assumed':  [u for _, u in depths],
        'paid_building':       pd.to_numeric(raw.get('amountPaidOnBuildingClaim'), errors='coerce'),
        'paid_contents':       pd.to_numeric(raw.get('amountPaidOnContentsClaim'), errors='coerce'),
        'damage_building':     pd.to_numeric(raw.get('buildingDamageAmount'), errors='coerce'),
        'property_value':      pd.to_numeric(raw.get('buildingPropertyValue'), errors='coerce'),
        'occupancy_type':      raw.get('occupancyType'),
        'n_floors':            pd.to_numeric(raw.get('numberOfFloorsInTheInsuredBuilding'), errors='coerce'),
        'elevated':            raw.get('elevatedBuildingIndicator'),
        'basement_type':       raw.get('basementEnclosureCrawlspaceType'),
        'lowest_floor_elev':   pd.to_numeric(raw.get('lowestFloorElevation'), errors='coerce'),
        'lowest_adjacent_grade': pd.to_numeric(raw.get('lowestAdjacentGrade'), errors='coerce'),
        'duration_raw':        pd.to_numeric(raw.get('floodWaterDuration'), errors='coerce'),
        'flood_zone':          raw.get('ratedFloodZone'),
        'cause_of_damage':     raw.get('causeOfDamage'),
    })
    return out[out['zip'].str.match(r'^\d{5}$', na=False)].reset_index(drop=True)


def fetch_policies_in_force(zips: Iterable[str], as_of: str,
                            verbose: bool = True,
                            give_up_after: int = 5) -> pd.DataFrame:
    """
    Count NFIP policies in force per zip as of a date — the DENOMINATOR that
    turns a raw claim count into a claim RATE.

    This is what makes the ground truth non-self-selected: "12% of insured
    structures in this zip filed a flood claim for this event" is a population
    statistic, whereas "N people applied for aid" is not.

    Uses $inlinecount so we pay for a count, not for the records. Returns a
    DataFrame [zip, policies_in_force]; zips whose count could not be retrieved
    are omitted rather than defaulted to zero.

    FAILS FAST BY DESIGN. The policies dataset is very large and this
    date-in-force filter is expensive, so OpenFEMA frequently answers it with a
    503 no matter how patiently we retry — observed failing on essentially
    every zip for 40 minutes straight. After `give_up_after` consecutive
    failures we abandon the denominator entirely and return what we have; the
    caller then falls back to a weaker label and says so in the report. Grinding
    through every zip to collect the same failure is not worth the wall clock.
    """
    rows = []
    zips = sorted({str(z).strip() for z in zips if str(z).strip()})
    consecutive_failures = 0

    for n, z in enumerate(zips, 1):
        flt = (f"reportedZipCode eq '{z}' and policyEffectiveDate le '{as_of}' "
               f"and policyTerminationDate ge '{as_of}'")
        data = _get(POLICIES_API, {
            '$filter': flt, '$top': 1, '$inlinecount': 'allpages', '$format': 'json',
        }, retries=2)

        count = None if data is None else data.get('metadata', {}).get('count')
        if count is None:
            consecutive_failures += 1
            if consecutive_failures >= give_up_after:
                if verbose:
                    print(f"    Policy denominator abandoned after "
                          f"{consecutive_failures} consecutive failures "
                          f"({len(rows)}/{len(zips)} zips retrieved).")
                break
            continue

        consecutive_failures = 0
        rows.append({'zip': z, 'policies_in_force': int(count)})
        if verbose and n % 10 == 0:
            print(f"    policy counts: {n}/{len(zips)} zips")
        time.sleep(0.15)

    return pd.DataFrame(rows)


def aggregate_by_zip(claims: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse claims to one row per zip.

    `nfip_mean_depth_ft` / `nfip_median_depth_ft` are computed over claims with
    an interpretable depth only. `nfip_pct_depth_gt0` is the share of those
    claims reporting standing water above the reference level — the closest
    claims-side analogue to "this property flooded".
    """
    if claims.empty:
        return pd.DataFrame()

    valid = claims[claims['depth_ft'].notna()]

    grouped = claims.groupby('zip')
    agg = pd.DataFrame({
        'nfip_claims':          grouped.size(),
        'nfip_paid_building':   grouped['paid_building'].mean(),
        'nfip_paid_contents':   grouped['paid_contents'].mean(),
        'nfip_total_paid':      grouped['paid_building'].sum(),
    })

    if not valid.empty:
        vg = valid.groupby('zip')
        agg['nfip_depth_claims']     = vg.size()
        agg['nfip_mean_depth_ft']    = vg['depth_ft'].mean()
        agg['nfip_median_depth_ft']  = vg['depth_ft'].median()
        agg['nfip_p90_depth_ft']     = vg['depth_ft'].quantile(0.9)
        agg['nfip_pct_depth_gt0']    = vg['depth_ft'].apply(lambda s: (s > 0).mean() * 100)

    # Damage ratio, over claims that carry both a damage amount and a value.
    ratio_src = claims[(claims['damage_building'] > 0) & (claims['property_value'] > 1000)]
    if not ratio_src.empty:
        agg['nfip_mean_damage_ratio'] = (
            ratio_src.assign(r=(ratio_src['damage_building'] /
                                ratio_src['property_value']).clip(upper=1.5))
            .groupby('zip')['r'].mean()
        )

    return agg.reset_index()


def unit_split(claims: pd.DataFrame) -> dict:
    """Share of each waterDepth unit branch — reported, never hidden."""
    if claims.empty or 'depth_unit_assumed' not in claims.columns:
        return {}
    counts = claims['depth_unit_assumed'].value_counts().to_dict()
    total = int(sum(counts.values()))
    return {'counts': {k: int(v) for k, v in counts.items()}, 'total': total}
