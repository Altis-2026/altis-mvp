"""
Download NFIP claim records (OpenFEMA v3, free) for the loss-calculation
validation, cached as gzipped CSV under outputs/nfip_loss_cache/.

Every record is a REAL settled claim with the adjuster-recorded water depth,
the policy's coverage, deductible and valuation basis, the building's value
and replacement cost, and the amount actually paid. That makes it possible to
test the dollar calculation separately from flood detection: feed it the
recorded depth and see whether it reproduces the recorded payment.

Usage: python validation/nfip_loss_data.py --events train|test|all
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "outputs" / "nfip_loss_cache"
API = "https://www.fema.gov/api/open/v3/NfipClaims"
MAX_ROWS = 30000   # per event, in API order (not random; recorded in the report)

# name -> (OpenFEMA filter, split). Split is by DATE OF LOSS: events starting
# before 2018-09-01 are for fitting/exploring, later ones are held out.
EVENTS = {
    "matthew":  ("floodEvent eq 'Hurricane Matthew'", "train"),
    "louisiana2016": ("dateOfLoss ge '2016-08-11' and dateOfLoss le '2016-08-31' and state eq 'LA'", "train"),
    "harvey":   ("floodEvent eq 'Hurricane Harvey'", "train"),
    "irma":     ("floodEvent eq 'Hurricane Irma'", "train"),
    "florence": ("floodEvent eq 'Hurricane Florence'", "test"),
    "imelda":   ("floodEvent eq 'Tropical Storm Imelda'", "test"),
    "ida":      ("floodEvent eq 'Hurricane Ida'", "test"),
    "helene":   ("floodEvent eq 'Hurricane Helene'", "test"),
}
FIELDS = ("dateOfLoss,state,countyCode,reportedZipCode,latitude,longitude,floodEvent,causeOfDamage,"
          "waterDepth,exteriorWaterDepth,interiorWaterDepth,floodWaterDuration,"
          "buildingDamageAmount,buildingPropertyValue,buildingReplacementCost,totalBuildingInsuranceCoverage,"
          "buildingDeductibleCode,amountPaidOnBuildingClaim,netBuildingPaymentAmount,replacementCostBasis,"
          "nonPaymentReasonBuilding,primaryResidenceIndicator,occupancyType,numberOfFloorsInTheInsuredBuilding,"
          "basementEnclosureCrawlspaceType,foundationType,elevatedBuildingIndicator,originalConstructionDate,"
          "contentsDamageAmount,contentsPropertyValue,contentsReplacementCost,totalContentsInsuranceCoverage,"
          "contentsDeductibleCode,amountPaidOnContentsClaim,netContentsPaymentAmount,"
          "iccCoverage,amountPaidOnIncreasedCostOfComplianceClaim")


def get(params, tries=6):
    for k in range(tries):
        try:
            r = requests.get(API, params=params, timeout=180)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(5 * (k + 1))
    raise RuntimeError(f"OpenFEMA failed: {params.get('$filter')}")


def fetch(name):
    path = CACHE / f"{name}.csv.gz"
    if path.exists():
        return pd.read_csv(path, low_memory=False)
    flt = EVENTS[name][0]
    total = get({"$filter": flt, "$top": 1, "$count": "true", "$select": "id"})["metadata"]["count"]
    rows, skip = [], 0
    while skip < min(total, MAX_ROWS):
        d = get({"$filter": flt, "$top": 10000, "$skip": skip, "$select": FIELDS, "$orderby": "id"})
        rows += d["NfipClaims"]
        skip += 10000
        print(f"  {name}: {len(rows)}/{min(total, MAX_ROWS)}", flush=True)
    df = pd.DataFrame(rows)
    df.attrs["total_in_api"] = total
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    (CACHE / f"{name}.total").write_text(str(total))
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="train", choices=["train", "test", "all"])
    a = ap.parse_args()
    for n, (_, split) in EVENTS.items():
        if a.events in ("all", split):
            df = fetch(n)
            print(f"{n} ({split}): {len(df)} claims cached", flush=True)


if __name__ == "__main__":
    main()
