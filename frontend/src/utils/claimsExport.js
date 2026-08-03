/* claimsExport.js — claims-system-ready export layouts.
   Turns Altis triage rows into files shaped for direct import into the two
   systems this market actually runs on, so the buyer's workflow is "download,
   import, done" with zero geospatial handling in between. These are
   import-file layouts (flat fields, system-native naming and codes), not
   certified API integrations; that distinction is stated in the UI copy. */
import { downloadCSV } from './csv.js';

const num = v => {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : null;
};

/* Altis triage class → dispatch decision codes claims systems key off. */
const DECISION = {
  'Dispatch':       { code: 'FIELD_DISPATCH', assignment: 'Field',  severity: 4 },
  'Review':         { code: 'MANUAL_REVIEW',  assignment: 'Review', severity: 3 },
  'Remote-Approve': { code: 'DESK_APPROVE',   assignment: 'Desk',   severity: 2 },
  'Remote-Deny':    { code: 'DESK_DENY',      assignment: 'Desk',   severity: 1 },
};

function decision(row) {
  return DECISION[row.impact_class] || { code: 'MANUAL_REVIEW', assignment: 'Review', severity: 3 };
}

/* Severity 1-5 refined by observed depth where available. */
function severityCode(row) {
  const d = num(row.max_depth_ft);
  if (d == null) return decision(row).severity;
  if (d >= 6) return 5;
  if (d >= 3) return 4;
  if (d >= 1) return 3;
  if (d >= 0.1) return 2;
  return 1;
}

/* Guidewire ClaimCenter loss-intake layout. */
export function exportGuidewire(rows, { eventLabel, eventDate } = {}) {
  const data = rows.map(r => {
    const d = decision(r);
    return {
      'PolicyNumber':            r.policy_number || '',
      'LossCause':               'flood',
      'LossDate':                eventDate || '',
      'CatastropheDescription':  eventLabel || '',
      'LossLocation_AddressLine1': r.address || '',
      'LossLocation_City':       r.city || '',
      'LossLocation_State':      r.state || '',
      'LossLocation_PostalCode': r.zip || '',
      'Segment':                 d.code,
      'AssignmentGroup':         d.assignment,
      'InitialReserve_Amount':   num(r.severity_mid_usd) ?? '',
      'Description':             r.adjuster_note || '',
      'Altis_FloodDepthFt':      num(r.max_depth_ft) ?? '',
      'Altis_ParcelFloodedPct':  num(r.pct_flooded) ?? '',
      'Altis_Confidence':        num(r.confidence_score) ?? '',
      'Altis_FloodProbabilityPct': num(r.flood_probability) ?? '',
      'Altis_FloodZone':         r.flood_zone || '',
      'Altis_SubrogationFlag':   r.subrogation_flag ? 'Y' : 'N',
      'Altis_PropertyId':        r.property_id || '',
    };
  });
  downloadCSV(`altis_guidewire_claimcenter_${data.length}`, data);
}

/* Duck Creek Claims intake layout. */
export function exportDuckCreek(rows, { eventLabel, eventDate } = {}) {
  const data = rows.map(r => {
    const d = decision(r);
    return {
      'PolicyNumber':     r.policy_number || '',
      'LossType':         'Flood',
      'LossDate':         eventDate || '',
      'CatastropheName':  eventLabel || '',
      'RiskAddress':      r.address || '',
      'City':             r.city || '',
      'StateCode':        r.state || '',
      'ZipCode':          r.zip || '',
      'SeverityCode':     severityCode(r),
      'AssignmentType':   d.assignment,
      'TriageDecision':   d.code,
      'ReserveIndemnity': num(r.severity_mid_usd) ?? '',
      'AdjusterNotes':    r.adjuster_note || '',
      'FloodDepthFeet':   num(r.max_depth_ft) ?? '',
      'ConfidencePct':    num(r.confidence_score) ?? '',
      'FloodProbabilityPct': num(r.flood_probability) ?? '',
      'FloodZone':        r.flood_zone || '',
      'SourceSystem':     'Altis',
      'SourceRecordId':   r.property_id || '',
    };
  });
  downloadCSV(`altis_duckcreek_claims_${data.length}`, data);
}
