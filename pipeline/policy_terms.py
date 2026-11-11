"""
policy_terms.py — turn a physical damage amount into what the carrier would
actually pay, by applying the policy's own terms.

WHY THIS EXISTS
  A depth-damage curve says how much of a building was physically damaged. A
  claims reserve is that damage after valuation basis, deductible and limit.
  Reporting the first as if it were the second overstates every reserve by the
  deductible and ignores the coverage cap, and it can be wrong by far more on
  replacement-cost policies.

WHERE EACH RULE COMES FROM (nothing here is a guess)
  Deductible codes   FEMA's published NFIP data dictionary (OpenFEMA
                     NfipClaims, field buildingDeductibleCode / contents...).
  Actual cash value  On an ACV-basis claim, payment = ACV damage - deductible,
                     exactly, for 75% of below-limit claims in the training
                     storms (Matthew, Louisiana 2016, Harvey, Irma).
  Replacement cost   On an RC-basis claim the payment is larger than the ACV
                     damage. Fitted on the same training storms:
                         paid = ACV_damage * (1 + theta * (RC/ACV - 1)) - deductible
                     with theta = REPLACEMENT_UPLIFT_THETA below (median over
                     claims). theta < 1 because not every dollar of a loss is
                     depreciated.
  Limit              Payment is capped at the coverage limit (87% of claims
                     whose loss exceeds limit + deductible paid <= the limit;
                     the rest are the payment components below).
  Coinsurance        NOT applied. The NFIP's 80%-to-replacement-cost rule
                     should shrink RC-basis payments when coverage < 80% of RC;
                     in the training claims theta is 0.331 where coverage >=
                     80% of RC and 0.356 where it is below, i.e. no visible
                     penalty. A rule the data does not show is not hard-coded.
                     Commercial/other carriers with real coinsurance clauses
                     can pass `coinsurance_ratio`.

Every result records which inputs were PROVIDED and which were ASSUMED, so a
reserve built on a guessed deductible says so.

Pure functions; no I/O. Fitted constants live at the top of the file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# FEMA OpenFEMA NfipClaims data dictionary, buildingDeductibleCode and
# contentsDeductibleCode legal values.
DEDUCTIBLE_CODES = {
    "0": 500, "1": 1000, "2": 2000, "3": 3000, "4": 4000, "5": 5000, "9": 750,
    "A": 10000, "B": 15000, "C": 20000, "D": 25000, "E": 50000,
    "F": 1250, "G": 1500, "H": 200,
}

# Fitted on the training storms only (validation/fit_loss_model.py rewrites
# this value into outputs/loss_model.json; the constant is the documented
# default so the module works without the file).
REPLACEMENT_UPLIFT_THETA = 0.33

# Used ONLY when the upload has no deductible. NFIP's own most common choices
# in the training storms are $1,250 (code F) and $2,000. Flagged as assumed.
ASSUMED_DEDUCTIBLE_BUILDING = 1250
ASSUMED_DEDUCTIBLE_CONTENTS = 1250


def deductible_from_code(code) -> Optional[float]:
    """NFIP deductible code -> dollars, or None if the code is unknown/blank."""
    if code is None:
        return None
    c = str(code).strip().upper()
    if c.endswith(".0"):            # CSV round-trips turn '2' into '2.0'
        c = c[:-2]
    return DEDUCTIBLE_CODES.get(c)


@dataclass
class PolicyTerms:
    """Terms for ONE coverage (building or contents) of one policy."""
    limit: float                                   # coverage limit, dollars
    deductible: Optional[float] = None
    valuation: Optional[str] = None                # 'RCV' | 'ACV' | None (unknown)
    acv_value: Optional[float] = None              # pre-loss actual cash value
    replacement_cost: Optional[float] = None
    coinsurance_ratio: Optional[float] = None      # e.g. 0.8; None = none applied
    provenance: dict = field(default_factory=dict)  # field -> 'provided' | 'assumed:<why>'

    def resolved(self, assumed_deductible=ASSUMED_DEDUCTIBLE_BUILDING) -> "PolicyTerms":
        """Fill unknowns with explicit, labelled assumptions; never silently."""
        p = dict(self.provenance)
        ded, val, acv, rc = self.deductible, self.valuation, self.acv_value, self.replacement_cost
        for name, v in (("deductible", ded), ("valuation", val), ("acv_value", acv), ("replacement_cost", rc)):
            p.setdefault(name, "provided" if v is not None else None)
        if ded is None:
            ded = assumed_deductible
            p["deductible"] = f"assumed: ${assumed_deductible:,.0f} (common NFIP choice)"
        if val is None:
            val = "ACV"
            p["valuation"] = "assumed: ACV (the lower, more conservative payment)"
        if acv is None and rc is not None:
            acv = rc / 1.25
            p["acv_value"] = "assumed: replacement cost / 1.25 (training-storm median RC/ACV)"
        if rc is None and acv is not None:
            rc = acv * 1.25
            p["replacement_cost"] = "assumed: ACV x 1.25 (training-storm median RC/ACV)"
        if acv is None and rc is None:
            acv = self.limit * 0.88 if self.limit else None     # training-storm median value/coverage = 0.88
            rc = acv * 1.25 if acv else None
            p["acv_value"] = "assumed: 0.88 x coverage (training-storm median)"
            p["replacement_cost"] = "assumed: 1.25 x ACV"
        return PolicyTerms(self.limit, ded, val, acv, rc, self.coinsurance_ratio, p)


def net_payment(acv_damage: float, terms: PolicyTerms, theta: float = REPLACEMENT_UPLIFT_THETA) -> dict:
    """
    Net payment for ONE coverage given the ACV physical damage in dollars.

    Steps: (1) valuation: ACV-basis pays the ACV damage; RC-basis pays it
    scaled up by the replacement-cost uplift; (2) optional coinsurance;
    (3) less the deductible; (4) capped at the limit; never negative.
    """
    t = terms.resolved()
    d = max(0.0, float(acv_damage or 0.0))
    uplift = 1.0
    if t.valuation == "RCV" and t.acv_value and t.replacement_cost and t.acv_value > 0:
        uplift = 1.0 + theta * max(0.0, t.replacement_cost / t.acv_value - 1.0)
    gross = d * uplift
    if t.coinsurance_ratio and t.replacement_cost:
        required = t.coinsurance_ratio * t.replacement_cost
        if t.limit < required:
            gross *= t.limit / required
    after_ded = max(0.0, gross - (t.deductible or 0.0))
    net = min(after_ded, t.limit or 0.0)
    return {
        "net": round(net, 2), "gross_after_valuation": round(gross, 2),
        "deductible_applied": round(min(gross, t.deductible or 0.0), 2),
        "limit_binding": bool(after_ded > (t.limit or 0.0)),
        "uplift": round(uplift, 4), "provenance": t.provenance,
    }


def net_payment_array(acv_damage, terms: PolicyTerms, theta: float = REPLACEMENT_UPLIFT_THETA):
    """Vectorised net_payment over an array of ACV damages (same rules)."""
    import numpy as np
    t = terms.resolved()
    d = np.maximum(0.0, np.asarray(acv_damage, float))
    uplift = 1.0
    if t.valuation == "RCV" and t.acv_value and t.replacement_cost and t.acv_value > 0:
        uplift = 1.0 + theta * max(0.0, t.replacement_cost / t.acv_value - 1.0)
    gross = d * uplift
    if t.coinsurance_ratio and t.replacement_cost:
        required = t.coinsurance_ratio * t.replacement_cost
        if t.limit < required:
            gross = gross * (t.limit / required)
    return np.minimum(np.maximum(0.0, gross - (t.deductible or 0.0)), t.limit or 0.0)
