import { useState, useEffect } from 'react';
import SarPair from './SarPair.jsx';
import { api } from '../services/api.js';
import { useIsMobile } from '../hooks/useIsMobile.js';

const TRIAGE_OPTIONS = ['Dispatch', 'Remote-Approve', 'Remote-Deny', 'Review'];
const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;

const COLORS = {
  'Dispatch':       '#FF4444',
  'Remote-Approve': '#4CAF82',
  'Remote-Deny':    '#6B8FA3',
  'Review':         '#FFB347',
};

/* Confidence arc gauge */
function ConfidenceGauge({ value, color }) {
  const r   = 30;
  const circ = 2 * Math.PI * r;
  const pct  = Math.max(0, Math.min(100, +value || 0));
  const dash = (pct / 100) * circ;

  return (
    <svg width="72" height="72" viewBox="0 0 72 72" style={{ flexShrink: 0 }}>
      <circle cx="36" cy="36" r={r} fill="none" strokeWidth="5" style={{ stroke: "var(--wa-06)" }}/>
      <circle
        cx="36" cy="36" r={r}
        fill="none"
        strokeWidth="5"
        strokeDasharray={`${dash} ${circ - dash}`}
        strokeDashoffset={circ * 0.25}
        strokeLinecap="round"
        style={{ stroke: color || 'var(--teal)', transition: 'stroke-dasharray 0.6s ease' }}
      />
      <text x="36" y="40" textAnchor="middle" fontSize="14" fontWeight="800" style={{ fill: "var(--text-primary)" }}
            fontFamily="Plus Jakarta Sans, sans-serif">
        {pct}%
      </text>
    </svg>
  );
}

/* Measurement row — plain-English label first, optional technical term as a
   small sublabel so the science stays visible without leading with jargon. */
function Mrow({ label, tech, value }) {
  return (
    <tr>
      <td style={{ fontSize: '0.76rem', color: 'var(--text-muted)', padding: '5px 0', borderTop: '1px solid var(--wa-04)' }}>
        {label}
        {tech && (
          <div style={{ fontSize: '0.58rem', color: 'var(--text-disabled)', marginTop: 1, letterSpacing: '0.03em' }}>
            {tech}
          </div>
        )}
      </td>
      <td style={{ fontSize: '0.76rem', color: 'var(--text-body)', textAlign: 'right', borderTop: '1px solid var(--wa-04)', fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </td>
    </tr>
  );
}

export default function PropertyDrawer({ property, eventId, liveEventDate, onClose, onAddToCompare, isInCompare, compareFull, onFeedbackSaved, eventLabel, durationSlices }) {
  const isMobile = useIsMobile();
  const [sarView, setSarView] = useState('sar'); // 'sar' | 'optical'

  /* One-click drafted claim-file note */
  const [draft, setDraft]           = useState(null);   // { note, source }
  const [drafting, setDrafting]     = useState(false);
  const [draftErr, setDraftErr]     = useState('');
  const [copied, setCopied]         = useState(false);

  /* Adjuster feedback (human-in-the-loop ground truth) */
  const [verdict, setVerdict]       = useState(null);   // 'up' | 'down' | null
  const [corrected, setCorrected]   = useState('');
  const [note, setNote]             = useState('');
  const [fbStatus, setFbStatus]     = useState('idle'); // idle | saving | saved | error
  const [fbError, setFbError]       = useState('');

  // Reset feedback widget whenever a different property is opened.
  useEffect(() => {
    setVerdict(null); setCorrected(''); setNote('');
    setFbStatus('idle'); setFbError('');
    setDraft(null); setDrafting(false); setDraftErr(''); setCopied(false);
  }, [property?.property_id]);

  const runDraftNote = async () => {
    if (drafting) return;
    setDrafting(true);
    setDraftErr('');
    try {
      const res = await api.draftNote(property, eventLabel);
      setDraft(res);
    } catch (e) {
      setDraftErr(e?.detail || 'Could not draft the note.');
    } finally {
      setDrafting(false);
    }
  };

  const copyDraft = async () => {
    try {
      await navigator.clipboard.writeText(draft.note);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch { /* clipboard unavailable — text stays selectable */ }
  };

  if (!property) return null;

  // Portfolio property that hasn't been analyzed yet: show an honest "no
  // analysis yet" state instead of misleading 0%-confidence measurements.
  const notAnalyzed = !property.impact_class;

  const ic     = property.impact_class;
  const color  = COLORS[ic] || '#6B8FA3';
  const depth  = parseFloat(property.max_depth_ft || 0).toFixed(2);
  const pct    = parseFloat(property.pct_flooded || 0).toFixed(1);
  const conf   = parseInt(property.confidence_score || 0);
  const urban  = property.urban_flag == 1;
  const hasDepthCI = property.depth_ci_ft != null && property.depth_ci_ft !== '';
  const depthLabel = hasDepthCI
    ? `${depth} ft ± ${parseFloat(property.depth_ci_ft).toFixed(1)} ft`
    : `${depth} ft`;

  // Round-7 enrichment fields. `isLiveResult` distinguishes a live-analysis
  // row (which computes all of these) from a pre-baked event row (which never
  // did) — so "unavailable" states only render where the pipeline actually
  // attempted the measurement, and every attempted metric shows either a
  // value or a reason. Never a silent blank, never a placeholder zero.
  const num = (v) => (v == null || v === '' || Number.isNaN(+v) ? null : +v);
  const isLiveResult = property.rain_mm !== undefined;
  // Calibrated P(flooded), present only once a calibration has been fitted.
  const floodProb = num(property.flood_probability);
  const rainMm    = num(property.rain_mm);
  const durDays   = num(property.duration_days);
  const sevLow    = num(property.severity_low_usd);
  const sevMid    = num(property.severity_mid_usd);
  const sevHigh   = num(property.severity_high_usd);
  const floodZone = property.flood_zone && property.flood_zone !== 'null' ? property.flood_zone : null;

  // Phase 2 structure attributes. Each stays null when unknown so the drawer
  // renders an explicit "unavailable" reason rather than a misleading 0.
  const numOrNull = (v) =>
    v == null || v === '' || Number.isNaN(parseFloat(v)) ? null : parseFloat(v);
  const firstFloorHeight = numOrNull(property.first_floor_height_ft);
  const depthAboveFfe    = numOrNull(property.depth_above_ffe_ft);
  const handFt           = numOrNull(property.hand_ft);
  const foundationType   = property.foundation_type || null;
  const firstFloorSource = property.first_floor_source &&
    property.first_floor_source !== 'unavailable'
      ? property.first_floor_source : null;
  const sfha      = property.sfha_flag === true || property.sfha_flag === 1 || property.sfha_flag === '1';
  const vhAvail   = property.vh_available == 1;
  const vhAgrees  = vhAvail && (
    (parseFloat(property.pct_flooded || 0) >= 10) === (parseFloat(property.vh_water_pct || 0) >= 0.10));
  const vegLoss   = property.vegetation_loss == 1;
  const ndviDelta = num(property.ndvi_delta);
  const subro     = property.subrogation_flag == 1;
  const surgeCheck = property.surge_check_flag == 1;
  const opticalOk = property.optical_available == 1;
  const opticalPct = num(property.optical_water_pct);
  const isUS      = property.latitude >= 17 && property.latitude <= 72 &&
                    property.longitude >= -170 && property.longitude <= -65;

  const unavailable = (reason) => (
    <span style={{ color: 'var(--text-disabled)', fontStyle: 'italic' }}>{`Unavailable: ${reason}`}</span>
  );

  const breakdown = parseJsonField(property.confidence_factors);
  const ensembleVotes = parseJsonField(property.ensemble_votes);
  const disagrees = property.ensemble_disagreement === true
    || property.ensemble_disagreement === 1 || property.ensemble_disagreement === '1'
    || property.ensemble_disagreement === 'True';

  const submitFeedback = async (agree) => {
    setVerdict(agree ? 'up' : 'down');
    if (!eventId && !property.isPortfolio) { /* still allow, event_id may be '' */ }
    setFbStatus('saving');
    setFbError('');
    try {
      const res = await api.submitFeedback(property.property_id, {
        event_id:        eventId || '',
        agree,
        original_class:  ic || '',
        corrected_class: agree ? '' : corrected,
        note,
        address:         property.address || '',
        portfolio_id:    property.isPortfolio ? (property.portfolio_id || '') : '',
      });
      setFbStatus('saved');
      onFeedbackSaved?.(res.summary);
    } catch (err) {
      setFbStatus('error');
      setFbError(err?.detail || 'Could not save feedback.');
    }
  };

  return (
    <>
      {/* Backdrop (closes drawer on click, doesn't block globe) */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, zIndex: 'var(--z-drawer)',
          pointerEvents: 'all',
        }}
      />

      {/* Drawer panel */}
      <div
        className="anim-slide-in-right"
        onClick={e => e.stopPropagation()}
        style={{
          position:   'fixed',
          top:        0,
          right:      0,
          bottom:     0,
          width:      isMobile ? '100%' : '36%',
          minWidth:   isMobile ? 0 : 380,
          maxWidth:   isMobile ? 'none' : 520,
          zIndex:     'var(--z-drawer)',
          background: 'var(--panel-strong)',
          borderLeft: '1px solid var(--wa-06)',
          backdropFilter: 'blur(20px)',
          overflowY:  'auto',
          display:    'flex',
          flexDirection: 'column',
          pointerEvents: 'all',
        }}
      >
        {/* ── Header ─────────────────────────────────────── */}
        <div style={{
          padding:       '24px 24px 20px',
          borderBottom:  '1px solid var(--wa-05)',
          position:      'sticky',
          top:           0,
          background:    'var(--panel-strong)',
          zIndex:        1,
        }}>
          {/* Close + property ID */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
            <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', letterSpacing: '0.08em', fontWeight: 600 }}>
              {property.property_id}
            </span>
            <button onClick={onClose} style={{
              background: 'none', border: 'none', color: 'var(--text-muted)',
              cursor: 'pointer', fontSize: '1.1rem', lineHeight: 1, padding: '0 0 0 12px',
              transition: 'color 0.15s',
            }}
              onMouseEnter={e => e.currentTarget.style.color = '#fff'}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
            >
              ✕
            </button>
          </div>

          {/* Address */}
          <h2 style={{
            fontSize: '1.15rem', fontWeight: 700, color: 'var(--text-primary)',
            letterSpacing: '-0.01em', lineHeight: 1.3, marginBottom: 14,
          }}>
            {property.address}
          </h2>

          {/* Badge + gauge row */}
          {notAnalyzed ? (
            <span style={{
              display: 'inline-block', padding: '4px 12px', borderRadius: 999,
              background: 'var(--wa-06)', border: '1px solid var(--wa-12)',
              color: 'var(--text-muted)', fontSize: '0.66rem', fontWeight: 700,
              letterSpacing: '0.08em', textTransform: 'uppercase',
            }}>
              Not analyzed yet
            </span>
          ) : (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <span className={`badge badge-${ic}`}>{ic}</span>
              {urban && (
                <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 5 }}>
                  <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#FFB347', display: 'inline-block' }} />
                  <span style={{ fontSize: '0.62rem', color: '#FFB347', letterSpacing: '0.05em', fontWeight: 600 }}>
                    URBAN SAR ZONE: ELEVATED UNCERTAINTY
                  </span>
                </div>
              )}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
              <ConfidenceGauge value={conf} color={color} />
              <span
                title="How sure the model is about this triage decision. A model-internal score, not a validated probability."
                style={{ fontSize: '0.6rem', color: 'var(--text-muted)', letterSpacing: '0.08em', cursor: 'help' }}>
                DECISION CONFIDENCE
              </span>
            </div>
          </div>
          )}

          {/* Calibrated probability that this property actually flooded.
              Distinct from decision confidence above: this one is fitted
              against real FEMA ground truth and is only shown once that
              calibration exists. */}
          {!notAnalyzed && floodProb != null && (
            <div style={{
              marginTop: 14, padding: '10px 12px', borderRadius: 'var(--r-md)',
              background: 'rgba(76,175,130,0.06)', border: '1px solid rgba(76,175,130,0.22)',
            }}>
              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10 }}>
                <span style={{ fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--approve)', textTransform: 'uppercase' }}>
                  Validated flood probability
                </span>
                <strong style={{ fontSize: '1rem', color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>
                  {floodProb.toFixed(0)}%
                </strong>
              </div>
              <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: 4, lineHeight: 1.45 }}>
                Calibrated against FEMA Individual Assistance ground truth
                {property.flood_probability_source ? ` (${property.flood_probability_source})` : ''}.
                Labels are zip-resolution, so treat this as FEMA-anchored, not per-house verified.
              </div>
            </div>
          )}
        </div>

        {/* ── Not-analyzed body: honest empty state, no fake zeros ── */}
        {notAnalyzed ? (
          <div style={{ padding: '20px 24px', flex: 1 }}>
            <div style={{
              background: 'rgba(168,212,230,0.05)', border: '1px solid rgba(168,212,230,0.18)',
              borderRadius: 'var(--r-md)', padding: '16px 18px', lineHeight: 1.6,
            }}>
              <div style={{ fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 8 }}>
                No flood analysis yet
              </div>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-body)', margin: 0 }}>
                This property is in your portfolio but hasn't been checked against
                satellite data. Open the <b>Analysis</b> panel in the sidebar, enter
                the flood/landfall date, and click <b>Run</b>. Altis will pull real
                Sentinel-1 imagery and score every property, usually in 30–90 seconds.
              </p>
            </div>
            {property.coverage_amount > 0 && (
              <div style={{ marginTop: 16, background: 'var(--wa-02)', border: '1px solid var(--wa-05)', borderRadius: 'var(--r-md)', padding: '4px 14px' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <tbody>
                    <Mrow label="Policy Number" value={property.policy_number || '—'} />
                    <Mrow label="Coverage" value={`$${(+property.coverage_amount).toLocaleString()}`} />
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
        <div style={{ padding: '20px 24px', flex: 1 }}>

          {/* Property aerial — high-res basemap satellite of the parcel so an
              ops user can see the actual structure alongside the flood data.
              Labeled honestly: current basemap imagery, not post-event. */}
          {property.latitude && property.longitude && MAPBOX_TOKEN && (
            <div style={{ marginBottom: 24 }}>
              <div style={{
                fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
                color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 12,
              }}>
                Property Aerial View
              </div>
              <img
                src={`https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/`
                  + `${(+property.longitude).toFixed(6)},${(+property.latitude).toFixed(6)},17.5`
                  + `/460x220@2x?access_token=${MAPBOX_TOKEN}`}
                alt="Aerial view of property"
                style={{
                  width: '100%', borderRadius: 'var(--r-md)', display: 'block',
                  border: '1px solid var(--wa-07)',
                }}
                onError={e => { e.currentTarget.parentElement.style.display = 'none'; }}
              />
              <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: 5 }}>
                Current basemap imagery (pre-dates the event), for structure/parcel
                context, not damage assessment.
              </div>
            </div>
          )}

          {/* SAR imagery section */}
          <div style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <span style={{
                fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
                color: 'var(--teal)', textTransform: 'uppercase',
              }}>
                {sarView === 'optical' ? 'Sentinel-2 Optical (MNDWI)' : 'Sentinel-1 SAR Imagery'}
              </span>
              <div style={{ display: 'flex', gap: 4 }}>
                {['sar', 'optical'].map(v => (
                  <button key={v} onClick={() => setSarView(v)} style={{
                    padding: '4px 10px',
                    background: sarView === v ? 'rgba(168,212,230,0.1)' : 'transparent',
                    border: `1px solid ${sarView === v ? 'rgba(168,212,230,0.25)' : 'var(--wa-07)'}`,
                    borderRadius: 'var(--r-sm)',
                    color: sarView === v ? 'var(--teal)' : 'var(--text-muted)',
                    fontSize: '0.66rem', fontWeight: 600, cursor: 'pointer', fontFamily: 'var(--font)',
                    textTransform: 'uppercase', letterSpacing: '0.04em',
                  }}>
                    {v}
                  </button>
                ))}
              </div>
            </div>

            <SarPair
              propertyId={property.property_id}
              view={sarView}
              lat={property.latitude}
              lon={property.longitude}
              eventDate={liveEventDate}
            />
          </div>

          {/* Measurements */}
          <div style={{ marginBottom: 24 }}>
            <div style={{
              fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
              color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 12,
            }}>
              Raw Measurements
            </div>
            <div style={{
              background: 'var(--wa-02)',
              border:     '1px solid var(--wa-05)',
              borderRadius: 'var(--r-md)',
              padding:    '4px 14px',
            }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <tbody>
                  <Mrow label="Max Flood Depth"   value={depthLabel} />
                  {/* Phase 2: depth-damage curves take depth above the FIRST
                      FLOOR, not above ground. The two differ by the foundation
                      height, so both are shown with the source of the
                      adjustment — an adjuster can see exactly which number
                      drove the call. Absent (not zero) when unknown. */}
                  <Mrow label="First-Floor Height"
                        tech={firstFloorSource || 'USACE National Structure Inventory'}
                        value={firstFloorHeight != null
                          ? `${firstFloorHeight.toFixed(2)} ft${foundationType ? ` (${foundationType})` : ''}`
                          : unavailable('no matched structure — NSI is US-only')} />
                  <Mrow label="Depth Above First Floor"
                        tech="water depth minus foundation height"
                        value={depthAboveFfe != null
                          ? `${depthAboveFfe.toFixed(2)} ft`
                          : unavailable('first-floor height unknown')} />
                  <Mrow label="Height Above Nearest Drainage" tech="HAND, MERIT Hydro"
                        value={handFt != null
                          ? `${handFt.toFixed(1)} ft`
                          : unavailable('no HAND coverage here')} />
                  <Mrow label="Flooded Area" tech="SAR amplitude change" value={`${pct}%`} />
                  <Mrow label="Confidence Score"   value={`${conf}%`} />
                  <Mrow label="Water Visible from Optical" tech="Sentinel-2 MNDWI"
                        value={opticalOk
                          ? `${((opticalPct || 0) * 100).toFixed(0)}% of parcel`
                          : unavailable('no cloud-free Sentinel-2 scene')} />
                  {isLiveResult && (
                    <>
                      <Mrow label="Event Rainfall" tech="CHIRPS precipitation total"
                            value={rainMm != null
                              ? `${(rainMm / 25.4).toFixed(1)} in (${rainMm.toFixed(0)} mm)`
                              : unavailable('rainfall source failed, see backend log')} />
                      <Mrow label="Time Underwater" tech="multi-pass inundation duration"
                            value={durDays == null
                              ? unavailable('fewer than 2 satellite passes in window')
                              : durDays > 0 ? `~${durDays} days` : 'Drained before next pass'} />
                      <Mrow label="Second Radar Channel" tech="VH dual-polarization cross-check"
                            value={vhAvail
                              ? (vhAgrees ? 'Confirms ✓' : 'Disagrees ⚠ → Review')
                              : unavailable('no VH-polarized scene in window')} />
                      <Mrow label="Vegetation Damage" tech="NDVI pre/post delta"
                            value={ndviDelta != null
                              ? (vegLoss ? `Detected (Δ ${ndviDelta.toFixed(2)})` : 'None detected')
                              : unavailable('no cloud-free pre/post optical pair')} />
                      <Mrow label="FEMA Flood Zone" tech="NFHL regulatory zone"
                            value={floodZone === 'unavailable'
                              ? unavailable('FEMA NFHL service unreachable')
                              : floodZone
                              ? (sfha ? `${floodZone} (SFHA, NFIP likely)` : floodZone)
                              : isUS ? unavailable('no NFHL polygon at this point')
                                     : 'N/A, outside US NFHL coverage'} />
                    </>
                  )}
                  <Mrow label="Data Source"        value="Sentinel-1 radar" />
                  <Mrow label="Dense Urban Area" tech="radar shadow risk"
                        value={urban ? 'Yes, confidence reduced' : 'No'} />
                  <Mrow label="Resolution"         value="10m native, 30m sampled" />
                </tbody>
              </table>
            </div>
          </div>

          {/* Inundation timeline — per-slice flooded fraction across the post
              window (live analyses persist flood_slices; slice date ranges
              come from analysis meta). Shows the water arriving and receding
              pass by pass — data competitors' static "after photo" can't show. */}
          {(() => {
            const slices = parseJsonField(property.flood_slices);
            if (!Array.isArray(slices) || !slices.some(v => v != null)) return null;
            const maxV = Math.max(0.05, ...slices.filter(v => v != null));
            return (
              <div style={{ marginBottom: 24 }}>
                <div style={{
                  fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
                  color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 10,
                }}>
                  Inundation Timeline
                </div>
                <div style={{
                  background: 'var(--wa-02)', border: '1px solid var(--wa-05)',
                  borderRadius: 'var(--r-md)', padding: '14px 14px 10px',
                }}>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'flex-end', height: 64 }}>
                    {slices.map((v, i) => {
                      const meta = durationSlices?.[i];
                      const noPass = v == null;
                      const h = noPass ? 4 : Math.max(4, Math.round((v / maxV) * 56));
                      return (
                        <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column',
                                              alignItems: 'center', gap: 4 }}
                             title={noPass
                               ? `No satellite pass${meta ? ` (${meta.start} – ${meta.end})` : ''}`
                               : `${(v * 100).toFixed(0)}% of parcel flooded${meta ? ` (${meta.start} – ${meta.end})` : ''}`}>
                          <div style={{
                            width: '100%', height: h, borderRadius: 3,
                            background: noPass
                              ? 'repeating-linear-gradient(45deg, var(--wa-06), var(--wa-06) 3px, transparent 3px, transparent 6px)'
                              : 'linear-gradient(180deg, #A8D4E6, #4A7FA8)',
                            opacity: noPass ? 1 : 0.55 + 0.45 * (v / maxV),
                            transition: 'height 0.5s ease',
                          }} />
                          <span style={{ fontSize: '0.54rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                            {meta ? meta.start.slice(5) : `P${i + 1}`}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                  <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: 8, lineHeight: 1.45 }}>
                    Flooded fraction of this parcel per satellite pass window.
                    {durDays != null && durDays > 0 && ` Water persisted ~${durDays} days.`}
                    {durDays === 0 && ' Drained before the next pass.'}
                    {' '}Hatched = no usable pass in that window.
                  </div>
                </div>
              </div>
            );
          })()}

          {/* Estimated claim severity (reserving aid) */}
          {sevLow != null && sevHigh != null && (
            <div style={{
              marginBottom: 24, background: 'rgba(212,176,104,0.05)',
              border: '1px solid rgba(212,176,104,0.2)', borderRadius: 'var(--r-md)',
              padding: '13px 15px',
            }}>
              <div style={{ fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.1em', color: '#D4B068', textTransform: 'uppercase', marginBottom: 5 }}>
                Estimated Claim Severity
              </div>
              <div style={{ fontSize: '1.05rem', fontWeight: 800, color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>
                {sevMid != null ? `$${sevMid.toLocaleString()}` : `$${sevLow.toLocaleString()} – $${sevHigh.toLocaleString()}`}
              </div>
              {sevMid != null && (
                <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginTop: 2, fontVariantNumeric: 'tabular-nums' }}>
                  range ${sevLow.toLocaleString()} – ${sevHigh.toLocaleString()}
                </div>
              )}
              <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)', marginTop: 4, lineHeight: 1.45 }}>
                Depth-damage curve × dwelling coverage; range reflects depth uncertainty.
                Reserving aid, not an adjuster estimate.
              </div>
            </div>
          )}

          {/* No severity estimate — say why instead of showing nothing */}
          {isLiveResult && sevLow == null && (
            <div style={{
              marginBottom: 24, padding: '10px 14px', borderRadius: 'var(--r-md)',
              background: 'var(--wa-02)', border: '1px solid var(--wa-06)',
              fontSize: '0.7rem', color: 'var(--text-muted)', lineHeight: 1.5,
            }}>
              No claim severity estimate: {parseFloat(property.max_depth_ft || 0) < 0.1
                ? 'no significant flood depth detected at this property.'
                : 'no dwelling coverage amount was present in the uploaded file.'}
            </div>
          )}

          {/* Surge-miss safeguard: low-lying waterfront read dry */}
          {surgeCheck && (
            <div style={{
              marginBottom: 24, background: 'rgba(168,212,230,0.05)',
              border: '1px solid rgba(168,212,230,0.22)', borderRadius: 'var(--r-md)',
              padding: '12px 14px',
            }}>
              <div style={{ fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 5 }}>
                Surge Verification Suggested
              </div>
              <p style={{ fontSize: '0.74rem', color: 'var(--text-body)', lineHeight: 1.5, margin: 0 }}>
                This low-lying waterfront parcel reads dry, but storm surge can
                recede between satellite passes. If this event involved coastal
                surge, a quick spot-check (photos, call-out) is recommended before
                closing remotely.
              </p>
            </div>
          )}

          {/* Subrogation candidate */}
          {subro && (
            <div style={{
              marginBottom: 24, background: 'rgba(76,175,130,0.05)',
              border: '1px solid rgba(76,175,130,0.22)', borderRadius: 'var(--r-md)',
              padding: '12px 14px',
            }}>
              <div style={{ fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.1em', color: '#4CAF82', textTransform: 'uppercase', marginBottom: 5 }}>
                Subrogation Candidate
              </div>
              <p style={{ fontSize: '0.74rem', color: 'var(--text-body)', lineHeight: 1.5, margin: 0 }}>
                Flooding detected adjacent to a permanent water body / drainage channel.
                Worth reviewing whether third-party infrastructure (levee, drainage,
                road channeling) contributed, with potential cost recovery. Screening flag,
                not a legal determination.
              </p>
            </div>
          )}

          {/* Ensemble disagreement warning */}
          {disagrees && (
            <div style={{
              marginBottom: 24, background: 'rgba(255,179,71,0.06)',
              border: '1px solid rgba(255,179,71,0.25)', borderRadius: 'var(--r-md)',
              padding: '12px 14px',
            }}>
              <div style={{ fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.1em', color: '#FFB347', textTransform: 'uppercase', marginBottom: 6 }}>
                Sensor disagreement, flagged for Review
              </div>
              <p style={{ fontSize: '0.74rem', color: 'var(--text-body)', lineHeight: 1.5, margin: 0 }}>
                {property.ensemble_note || 'Independent flood signals (SAR / optical / DEM-hydrology) disagree on this property.'}
              </p>
              {ensembleVotes && (
                <div style={{ display: 'flex', gap: 14, marginTop: 8 }}>
                  {Object.entries(ensembleVotes).map(([k, v]) => (
                    <div key={k} style={{ fontSize: '0.66rem', color: 'var(--text-muted)' }}>
                      {k.replace('_', ' ')}: <span style={{ color: 'var(--text-bright)', fontWeight: 600 }}>{v}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Why this decision — confidence factor breakdown */}
          {breakdown && Array.isArray(breakdown.factors) && breakdown.factors.length > 0 && (
            <div style={{ marginBottom: 24 }}>
              <div style={{
                fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
                color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 12,
              }}>
                Why This Decision
              </div>
              <div style={{
                background: 'var(--wa-02)', border: '1px solid var(--wa-05)',
                borderRadius: 'var(--r-md)', padding: '4px 14px',
              }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <tbody>
                    <Mrow label="Base score" value={breakdown.base} />
                    {breakdown.factors.map((f, i) => (
                      <tr key={i}>
                        <td style={{ fontSize: '0.74rem', color: 'var(--text-secondary)', padding: '5px 0', borderTop: '1px solid var(--wa-04)' }}>
                          {f.factor}
                          <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)', marginTop: 2 }}>{f.reason}</div>
                        </td>
                        <td style={{
                          fontSize: '0.76rem', textAlign: 'right', verticalAlign: 'top', paddingTop: 5,
                          borderTop: '1px solid var(--wa-04)', fontVariantNumeric: 'tabular-nums',
                          color: f.delta > 0 ? '#4CAF82' : '#FF6B6B', fontWeight: 700,
                        }}>
                          {f.delta > 0 ? '+' : ''}{f.delta}
                        </td>
                      </tr>
                    ))}
                    <Mrow label="Final score (clamped 30–97)" value={`${breakdown.final_score}%`} />
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Adjuster note */}
          {property.adjuster_note && (
            <div style={{
              marginBottom: 24,
              background:   `linear-gradient(135deg, rgba(6,8,14,0) 0%, rgba(${colorToRgb(color)},0.04) 100%)`,
              border:       `1px solid rgba(${colorToRgb(color)},0.15)`,
              borderLeft:   `3px solid ${color}`,
              borderRadius: '0 var(--r-md) var(--r-md) 0',
              padding:      '14px 16px',
            }}>
              <div style={{
                fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.12em',
                color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 8,
              }}>
                Adjuster Note
              </div>
              <p style={{ fontSize: '0.88rem', color: 'var(--text-body)', lineHeight: 1.65, fontStyle: 'italic' }}>
                "{property.adjuster_note}"
              </p>
            </div>
          )}

          {/* One-click drafted claim-file note — turns the analysis into text
              an adjuster can paste straight into their claims system. */}
          <div style={{ marginBottom: 24 }}>
            <div style={{
              fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
              color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 10,
            }}>
              Claim-File Note
            </div>
            {draft ? (
              <div style={{
                background: 'var(--wa-02)', border: '1px solid rgba(168,212,230,0.18)',
                borderRadius: 'var(--r-md)', padding: '14px',
              }}>
                <p style={{ fontSize: '0.78rem', color: 'var(--text-body)', lineHeight: 1.65, margin: '0 0 12px', userSelect: 'text' }}>
                  {draft.note}
                </p>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <button onClick={copyDraft} style={{
                    padding: '8px 16px', borderRadius: 'var(--r-md)', border: 'none',
                    background: copied ? '#4CAF82' : 'linear-gradient(135deg, #DDF1FB, #8FC4E8)',
                    color: '#000', fontSize: '0.74rem', fontWeight: 800,
                    cursor: 'pointer', fontFamily: 'var(--font)', transition: 'background 0.2s',
                  }}>
                    {copied ? '✓ Copied' : 'Copy to clipboard'}
                  </button>
                  <button onClick={runDraftNote} disabled={drafting} style={{
                    padding: '8px 14px', borderRadius: 'var(--r-md)',
                    background: 'transparent', border: '1px solid var(--wa-12)',
                    color: 'var(--text-secondary)', fontSize: '0.72rem', fontWeight: 600,
                    cursor: drafting ? 'wait' : 'pointer', fontFamily: 'var(--font)',
                  }}>
                    {drafting ? 'Redrafting…' : 'Redraft'}
                  </button>
                  <span style={{ fontSize: '0.6rem', color: 'var(--text-disabled)', marginLeft: 'auto' }}>
                    {draft.source === 'llm' ? 'AI-drafted, review before use' : 'Template draft'}
                  </span>
                </div>
              </div>
            ) : (
              <div>
                <button onClick={runDraftNote} disabled={drafting} style={{
                  width: '100%', padding: '11px 0', borderRadius: 'var(--r-md)',
                  background: 'rgba(168,212,230,0.08)', border: '1px solid rgba(168,212,230,0.25)',
                  color: 'var(--teal)', fontSize: '0.78rem', fontWeight: 700,
                  cursor: drafting ? 'wait' : 'pointer', fontFamily: 'var(--font)',
                  letterSpacing: '0.02em', transition: 'background 0.15s',
                }}>
                  {drafting ? 'Drafting from satellite analysis…' : '✎ Draft claim-file note'}
                </button>
                {draftErr && (
                  <div style={{ fontSize: '0.7rem', color: '#FF6B6B', marginTop: 8 }}>{draftErr}</div>
                )}
              </div>
            )}
          </div>

          {/* Adjuster feedback — human-in-the-loop ground truth */}
          <div style={{ marginBottom: 24 }}>
            <div style={{
              fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
              color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 10,
            }}>
              Adjuster Verdict
            </div>

            {fbStatus === 'saved' ? (
              <div style={{
                background: 'rgba(76,175,130,0.08)', border: '1px solid rgba(76,175,130,0.25)',
                borderRadius: 'var(--r-md)', padding: '12px 14px', fontSize: '0.76rem', color: '#9FE3C0',
              }}>
                ✓ Verdict recorded{verdict === 'down' && corrected ? `, corrected to ${corrected}` : ''}.
                This feeds Altis's calibration as property-level ground truth.
              </div>
            ) : (
              <div style={{
                background: 'var(--wa-02)', border: '1px solid var(--wa-05)',
                borderRadius: 'var(--r-md)', padding: '14px',
              }}>
                <div style={{ fontSize: '0.74rem', color: 'var(--text-secondary)', marginBottom: 10, lineHeight: 1.5 }}>
                  Was this triage decision correct?
                </div>
                <div style={{ display: 'flex', gap: 10, marginBottom: verdict === 'down' ? 12 : 0 }}>
                  <button
                    onClick={() => submitFeedback(true)}
                    disabled={fbStatus === 'saving'}
                    style={verdictBtn(verdict === 'up', '#4CAF82')}
                  >
                    👍 Agree
                  </button>
                  <button
                    onClick={() => setVerdict('down')}
                    disabled={fbStatus === 'saving'}
                    style={verdictBtn(verdict === 'down', '#FF6B6B')}
                  >
                    👎 Disagree
                  </button>
                </div>

                {verdict === 'down' && (
                  <div className="anim-fade-in">
                    <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)', margin: '4px 0 6px' }}>
                      Correct classification (optional)
                    </div>
                    <select value={corrected} onChange={e => setCorrected(e.target.value)} style={{
                      width: '100%', padding: '7px 9px', fontSize: '0.74rem', marginBottom: 8,
                      background: 'var(--input-bg)', color: 'var(--text-primary)', border: '1px solid var(--wa-10)',
                      borderRadius: 'var(--r-sm)', fontFamily: 'var(--font)', cursor: 'pointer',
                    }}>
                      <option value="">— Select correct triage —</option>
                      {TRIAGE_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                    <textarea
                      value={note} onChange={e => setNote(e.target.value)}
                      placeholder="What did the satellite miss? (optional note for the model)"
                      rows={2}
                      style={{
                        width: '100%', padding: '8px 10px', fontSize: '0.74rem', resize: 'vertical',
                        background: 'var(--input-bg)', color: 'var(--text-primary)', border: '1px solid var(--wa-10)',
                        borderRadius: 'var(--r-sm)', fontFamily: 'var(--font)', marginBottom: 10,
                      }}
                    />
                    <button
                      onClick={() => submitFeedback(false)}
                      disabled={fbStatus === 'saving'}
                      style={{
                        width: '100%', padding: '10px 0', borderRadius: 'var(--r-md)', border: 'none',
                        background: '#FF6B6B', color: '#000', fontSize: '0.78rem', fontWeight: 800,
                        cursor: fbStatus === 'saving' ? 'wait' : 'pointer', fontFamily: 'var(--font)',
                      }}
                    >
                      {fbStatus === 'saving' ? 'Saving…' : 'Submit correction'}
                    </button>
                  </div>
                )}

                {fbStatus === 'error' && (
                  <div style={{ fontSize: '0.7rem', color: '#FF6B6B', marginTop: 8 }}>{fbError}</div>
                )}
              </div>
            )}
          </div>

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 10, marginBottom: 8 }}>
            <ActionButton
              label="Approve Claim"
              color="#4CAF82"
              onClick={() => alert('Guidewire integration coming in v2')}
            />
            <ActionButton
              label="Dispatch Adjuster"
              color="#FF4444"
              outline
              onClick={() => alert('CAT team dispatch integration coming in v2')}
            />
          </div>
          <div style={{ fontSize: '0.6rem', color: 'var(--text-disabled)', textAlign: 'center', marginBottom: 16 }}>
            Guidewire ClaimCenter integration coming in v2
          </div>

          <button
            onClick={() => !isInCompare && !compareFull && onAddToCompare?.(property)}
            disabled={isInCompare || compareFull}
            style={{
              width: '100%', padding: '10px 0',
              background: isInCompare ? 'rgba(168,212,230,0.1)' : 'transparent',
              border: `1px solid ${isInCompare ? 'rgba(168,212,230,0.3)' : 'var(--wa-10)'}`,
              borderRadius: 'var(--r-md)',
              color: isInCompare ? 'var(--teal)' : compareFull ? 'var(--text-disabled)' : 'var(--text-secondary)',
              fontSize: '0.78rem', fontWeight: 600, fontFamily: 'var(--font)',
              cursor: (isInCompare || compareFull) ? 'default' : 'pointer',
              letterSpacing: '0.02em',
            }}
          >
            {isInCompare ? '✓ Added to SAR compare' : compareFull ? 'Compare tray full (4 max)' : '+ Add to SAR compare'}
          </button>

        </div>
        )}
      </div>
    </>
  );
}

function ActionButton({ label, color, outline, onClick }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        flex:          1,
        padding:       '11px 0',
        background:    outline
          ? hover ? `rgba(${colorToRgb(color)},0.1)` : 'transparent'
          : hover ? color : `rgba(${colorToRgb(color)},0.85)`,
        border:        outline ? `1px solid ${color}` : 'none',
        borderRadius:  'var(--r-md)',
        color:         outline ? color : '#000',
        fontSize:      '0.8rem',
        fontWeight:    700,
        cursor:        'pointer',
        fontFamily:    'var(--font)',
        letterSpacing: '0.03em',
        transition:    'all 0.15s ease',
      }}
    >
      {label}
    </button>
  );
}

function verdictBtn(active, color) {
  return {
    flex: 1, padding: '10px 0', borderRadius: 'var(--r-md)', cursor: 'pointer',
    fontFamily: 'var(--font)', fontSize: '0.78rem', fontWeight: 700,
    background: active ? `${color}22` : 'var(--wa-03)',
    border: `1px solid ${active ? color : 'var(--wa-10)'}`,
    color: active ? color : 'var(--text-secondary)',
    transition: 'all 0.15s ease',
  };
}

function parseJsonField(value) {
  if (value == null || value === '') return null;
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return null; }
}

function colorToRgb(hex) {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0,2), 16);
  const g = parseInt(h.slice(2,4), 16);
  const b = parseInt(h.slice(4,6), 16);
  return `${r},${g},${b}`;
}
