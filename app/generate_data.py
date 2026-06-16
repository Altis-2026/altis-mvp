"""
generate_data.py
Generates realistic synthetic flood event data for Harvey and Ian.
Runs automatically if the real GEE pipeline CSVs are not yet in outputs/.
"""

import pandas as pd
import numpy as np
import os

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
OUTPUTS_DIR = os.path.join(os.path.dirname(BASE_DIR), "outputs")

# Real streets in documented flood-affected neighborhoods
HARVEY_STREETS = [
    "S Braeswood Blvd", "Braesridge Dr", "Glenmont Dr", "Brays Oaks Dr",
    "Fondren Rd", "Bellaire Blvd", "Beechnut St", "S Gessner Rd",
    "N Braeswood Blvd", "Chimney Rock Rd", "S Rice Ave", "Newcastle Dr",
    "Dumfries Dr", "Jackwood St", "Sanford Dr", "Braesvalley Dr",
    "Willowbend Blvd", "Westpark Dr", "Bissonnet St", "Linkwood Dr",
    "Carlow Dr", "Celia Dr", "Bayou Glen Rd", "Rutherglenn Dr",
    "Wigton Dr", "Carew St", "Roseneath Dr", "Braeburn Valley Dr",
    "Ludington Dr", "Crestdale Dr",
]

IAN_STREETS = [
    "Tamiami Trail", "Harbor Blvd", "Kings Hwy", "Midway Blvd",
    "El Jobean Rd", "Cochran Blvd", "Duncan Rd", "Peachland Blvd",
    "Edgewater Dr", "Murdock Ave", "Olean Blvd", "Veterans Blvd",
    "Coudriet Dr", "Rampart Blvd", "Palomino Ave", "Loveland Blvd",
    "Conway Blvd", "Tropicana Ave", "Paulson Dr", "Atwater St",
    "Quesada Ave", "Collingswood Blvd", "Ohara Dr", "Flamingo Blvd",
    "Olean Terrace", "Bayshore Rd", "Elkcam Blvd", "Forrest Nelson Blvd",
]

DISPATCH_NOTES = [
    "Satellite radar detects {d}ft of standing water across {p}% of the property; physical inspection required before adjudication.",
    "Post-event SAR confirms major inundation at {d}ft peak depth; adjuster dispatch recommended for full structural damage assessment.",
    "Flood depth of {d}ft recorded at {c}% satellite confidence; in-person inspection necessary to document interior losses.",
    "SAR imagery shows {p}% of parcel submerged to {d}ft maximum depth; immediate adjuster dispatch recommended.",
    "High-confidence flood signal at {d}ft depth detected across {p}% of property footprint; on-site evaluation required before processing.",
    "Sentinel-1 backscatter confirms extensive inundation to {d}ft across {p}% of lot; dispatch adjuster for damage documentation.",
]

APPROVE_NOTES = [
    "Satellite confirms {d}ft flooding across {p}% of property at {c}% confidence; evidence sufficient for remote claim approval.",
    "SAR data shows clear inundation of {d}ft with {p}% lot coverage; remote approval recommended pending policyholder documentation.",
    "Post-event imagery confirms {d}ft flood depth at {c}% confidence; no structural inspection required, approve with standard documentation.",
    "Flood signal of {d}ft detected across {p}% of footprint at {c}% confidence; remote approval is supported by satellite record.",
    "Satellite analysis confirms moderate flooding at {d}ft across {p}% of the parcel; remote claim approval appropriate.",
]

DENY_NOTES = [
    "Satellite imagery shows no significant flooding at this location; max detected depth is {d}ft at {c}% confidence; remote denial supported.",
    "Pre and post-event SAR show no material change in backscatter at property location; flood depth under 0.3ft; deny remotely.",
    "No inundation detected at {c}% confidence; property located outside confirmed flood extent per Sentinel-1 analysis.",
    "SAR analysis confirms property was not reached by floodwaters; {d}ft depth reading supports remote denial at {c}% confidence.",
    "Post-event imagery is consistent with pre-event baseline at this parcel; no flooding detected; remote denial supported.",
]

REVIEW_NOTES = [
    "Borderline flood signal of {d}ft at {c}% confidence; recommend manual review before any dispatch or denial decision.",
    "Ambiguous SAR return at property location; {d}ft estimated depth but confidence is {c}%; flag for human adjuster review.",
    "Low-confidence flood detection at this parcel; {d}ft depth estimated but signal quality warrants human verification.",
    "Satellite data shows {p}% coverage at {d}ft estimated depth but confidence is below threshold; manual review required.",
    "Mixed signals in SAR imagery; {d}ft estimated depth with {c}% confidence; do not adjudicate without human review.",
]


def _make_addresses(streets, city_state, zipcodes, n, rng):
    addrs = []
    for _ in range(n):
        s   = rng.choice(streets)
        num = int(rng.randint(100, 9900))
        if num % 2 == 0:
            num += 1
        z = rng.choice(zipcodes)
        addrs.append(f"{num} {s}, {city_state} {z}")
    return addrs


def _make_note(cat, d, p, c, rng):
    d_r = round(float(d), 1)
    p_r = int(round(float(p)))
    if   cat == "Dispatch":       pool = DISPATCH_NOTES
    elif cat == "Remote-Approve": pool = APPROVE_NOTES
    elif cat == "Remote-Deny":    pool = DENY_NOTES
    else:                         pool = REVIEW_NOTES
    return rng.choice(pool).format(d=d_r, p=p_r, c=c)


def generate_event_data(event_id: str, n: int = 1000, save: bool = True) -> pd.DataFrame:
    """
    Generate realistic synthetic flood event data.
    event_id : 'harvey' or 'ian'
    """
    rng = np.random.RandomState(0 if event_id == "harvey" else 1)

    if event_id == "harvey":
        streets    = HARVEY_STREETS
        city_state = "Houston, TX"
        zipcodes   = ["77035", "77096", "77025", "77401", "77005", "77030"]
        # Spatial centers per triage class (flood in SW quadrant for Harvey)
        centers = {
            "Dispatch":       (29.665, -95.535),
            "Remote-Approve": (29.695, -95.495),
            "Remote-Deny":    (29.740, -95.455),
            "Review":         (29.710, -95.510),
        }
        spread   = (0.025, 0.030)
        fracs    = dict(Dispatch=0.28, RA=0.35, RD=0.25, RV=0.12)
        prefix   = "HARV"
    else:
        streets    = IAN_STREETS
        city_state = "Port Charlotte, FL"
        zipcodes   = ["33948", "33952", "33981", "33950"]
        # Storm surge hits coast (south), lighter inland
        centers = {
            "Dispatch":       (26.900, -82.060),
            "Remote-Approve": (26.955, -82.040),
            "Remote-Deny":    (27.025, -82.020),
            "Review":         (26.975, -82.055),
        }
        spread   = (0.030, 0.035)
        fracs    = dict(Dispatch=0.35, RA=0.30, RD=0.22, RV=0.13)
        prefix   = "IAN"

    n_d  = int(n * fracs["Dispatch"])
    n_ra = int(n * fracs["RA"])
    n_rd = int(n * fracs["RD"])
    n_rv = n - n_d - n_ra - n_rd

    categories = (
        ["Dispatch"]       * n_d  +
        ["Remote-Approve"] * n_ra +
        ["Remote-Deny"]    * n_rd +
        ["Review"]         * n_rv
    )
    rng.shuffle(categories)

    depths, pcts, confs, lats, lons = [], [], [], [], []

    for cat in categories:
        clat, clon = centers[cat]
        lats.append(round(float(rng.normal(clat, spread[0])), 6))
        lons.append(round(float(rng.normal(clon, spread[1])), 6))

        if cat == "Dispatch":
            d = float(np.clip(rng.lognormal(np.log(4.2), 0.38), 2.5, 12.0))
            p = float(rng.uniform(45, 90))
            c = int(rng.randint(65, 93))
        elif cat == "Remote-Approve":
            d = float(np.clip(rng.lognormal(np.log(1.2), 0.30), 0.5, 2.9))
            p = float(rng.uniform(20, 68))
            c = int(rng.randint(78, 96))
        elif cat == "Remote-Deny":
            d = float(rng.uniform(0.0, 0.28))
            p = float(rng.uniform(0.0, 4.8))
            c = int(rng.randint(80, 97))
        else:
            d = float(rng.uniform(0.3, 1.9))
            p = float(rng.uniform(5, 42))
            c = int(rng.randint(36, 77))

        depths.append(round(d, 2))
        pcts.append(round(p, 1))
        confs.append(c)

    addresses = _make_addresses(streets, city_state, zipcodes, n, rng)

    rec_map = {
        "Dispatch":       "Send adjuster — major flood damage likely",
        "Remote-Approve": "Approve remotely — flooding confirmed, documentation required",
        "Remote-Deny":    "Deny remotely — no significant flooding detected",
        "Review":         "Flag for manual review — borderline measurements",
    }

    notes = [_make_note(cat, d, p, c, rng)
             for cat, d, p, c in zip(categories, depths, pcts, confs)]

    property_ids = [f"{prefix}-{str(i+1).zfill(5)}" for i in range(n)]

    df = pd.DataFrame({
        "property_id":        property_ids,
        "address":            addresses,
        "latitude":           lats,
        "longitude":          lons,
        "pct_flooded":        pcts,
        "max_depth_ft":       depths,
        "impact_class":       categories,
        "confidence_score":   confs,
        "recommended_action": [rec_map[c] for c in categories],
        "adjuster_note":      notes,
    })

    if save:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        out = os.path.join(OUTPUTS_DIR, f"{event_id}_final.csv")
        if not os.path.exists(out):
            df.to_csv(out, index=False)
            print(f"Synthetic data saved: {out}")

    return df


if __name__ == "__main__":
    for eid in ("harvey", "ian"):
        df = generate_event_data(eid, n=1000, save=True)
        print(f"\n{eid.upper()} — {len(df)} properties")
        print(df["impact_class"].value_counts().to_string())
