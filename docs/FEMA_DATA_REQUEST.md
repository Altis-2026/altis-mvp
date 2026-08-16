# Getting address-level flood claims: what actually works

**Status:** researched 2026-08-16, not yet sent. This corrects the plan recorded
in `PROJECT_STATE.md` §7 item 0(d), which was written from search snippets and
is materially over-optimistic.

---

## The correction

PROJECT_STATE said:

> The public OpenFEMA claims are zip-redacted by the Privacy Act; the full
> address-level version exists and FEMA does grant access through an
> Information Sharing Access Agreement (ISAA). Contact OpenFEMA@fema.dhs.gov.
> [...] the highest ceiling long-term, since it's literally the same dataset
> already in use, just at full precision.

Three parts of that are wrong or misleading, and one is right.

**Wrong — the contact.** OpenFEMA is the *public* data programme. ISAAs are not
processed there. The route runs through the **Regional Flood Insurance Liaison
(RFIL)** for the FEMA region concerned (Texas is Region 6), who receives the
data-request letter and shepherds the ISAA through the Region or FEMA HQ.

**Wrong — the eligibility.** An ISAA is not a general research licence. FEMA's
PIVOT system data is available to **NFIP-participating communities, local /
regional / state entities, and their third-party contractors**, for purposes
tied to the programme: floodplain management, Community Rating System
compliance, hazard mitigation planning, grant applications. "Validating a
commercial flood-detection model" is not one of those purposes. Altis cannot
apply on its own behalf as a vendor to carriers.

**Misleading — the ceiling.** FEMA is explicit that a policyholder's address is
PII under the Privacy Act and cannot be released publicly. The realistic
outcome of an ISAA is not "the same dataset at full precision, for us"; it is
data held under an agreement, for a sponsoring public entity's stated purpose,
with handling obligations attached.

**Right — it is genuinely the highest-quality flood-claims truth that exists,**
and the agreement mechanism is real, runs on a stated ~10-working-day review,
and lasts three years once executed.

---

## The three routes, in the order worth pursuing them

### 1. A design-partner carrier's own claims file — fastest, and it needs no FEMA

This is the route PROJECT_STATE never considered, and it dominates the other
two on every axis that matters.

A mid-market carrier or MGA already holds, at full street address: which
policies were in force, which filed, what was paid, what the adjuster recorded,
and — critically — **which insured properties did NOT file.** That last set is
the negative class this project has been missing, and no public dataset
provides it. It arrives without a Privacy Act problem because the carrier is
the data owner and can share its own book under an NDA or a pilot agreement.

It also validates the thing we actually sell. FEMA's claims tell us about NFIP
policies; a carrier's book tells us about *their* book, which is the population
a triage recommendation will be scored against in production.

**Do this first.** It is a commercial conversation, not a federal one, and the
first design partner unblocks it.

### 2. A community sponsor, with Altis as third-party contractor

The legitimate ISAA path. It requires an NFIP-participating community (a
county, a flood control district, a council of governments) that wants
floodplain-management or mitigation-planning work done, and engages Altis to do
it. The community requests the data; Altis handles it as their contractor,
under the executed ISAA and its handling terms.

Natural candidates in areas already studied here: Fort Bend County, Harris
County Flood Control District, and the Houston-Galveston Area Council. Note
that the Sugar Land structural-flooding survey used in `validation/` came from
exactly this class of organisation, which is evidence they hold and share such
data.

Be honest internally about what this is: it is a genuine public-interest
engagement that also yields validation data. If there is no real
floodplain-management deliverable, it is not an appropriate use of the
mechanism and should not be pursued as a backdoor.

### 3. Academic collaboration

FEMA's own FAQ notes that some datasets have been available for academic use
under approved information-sharing agreements. A university partner with a
flood-hazard group can hold the agreement and publish; Altis contributes method
and compute. Slowest, weakest control over timing, but real precedent exists.

---

## Draft letter — route 2, for a community sponsor to send

Not to be sent by Altis directly. This is the text a sponsoring community's
floodplain administrator would adapt onto their letterhead and send to their
FEMA Regional Flood Insurance Liaison.

> **Subject:** NFIP data request and Information Sharing Access Agreement —
> [COMMUNITY NAME], CID [NUMBER]
>
> Dear [Regional Flood Insurance Liaison, FEMA Region 6],
>
> [COMMUNITY NAME] requests access to NFIP policy and claims data for our
> jurisdiction under an Information Sharing Access Agreement, in support of
> [floodplain management / CRS Activity 5xx / hazard mitigation plan update].
>
> **Data requested:** NFIP claims and policies-in-force for [COUNTY/COMMUNITY],
> for the period [DATES], at the finest spatial resolution available under the
> agreement, including claim date, paid amounts, and reported water depth.
>
> **Purpose:** to characterise observed flood exposure across the jurisdiction
> and to evaluate the accuracy of remotely sensed flood extent and depth
> products used in our [mitigation planning / post-event response] workflow.
>
> **Third-party contractor:** [ALTIS LEGAL ENTITY] will process the data on our
> behalf. They will be bound by the handling, storage, and destruction terms of
> the executed ISAA, and will not retain or redistribute record-level data.
>
> **Point of contact:** [NAME, TITLE, EMAIL, PHONE]
>
> We understand the agreement runs three years and covers repeat requests for
> the same data. Please advise on any additional documentation required.

---

## What to do before any of this lands

Nothing here blocks the current work, and none of it should be waited on.

The measurements this project needed most were sitting in public archives the
whole time and are already in the repo:

- **USGS high water marks** (`outputs/usgs_hwm_event180.json`) — 2,364 surveyed
  points, measured depths. Gives recall and depth error.
- **USGS SIR 2018-5070 mapped flood extent AND mapped-area boundary**
  (`outputs/usgs_brazos_extent.geojson`) — 194 km² flooded against 195 km² of
  mapped-dry ground inside the Brazos study area, which labels **68,624 real
  structures (25,062 flooded, 43,562 dry)**. This is the negative class the
  FEMA route was wanted for, and it is already here.

The honest framing: FEMA data would tell us about *insured loss*, which is a
different and commercially valuable quantity. It is no longer the blocker for
measuring whether the detector works.
