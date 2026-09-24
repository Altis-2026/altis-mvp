/* IntelPanel — the "why look twice" layer on top of the SAR triage result.
 *
 * Adjuster flags (with confirm / dismiss that become training labels), depth
 * above the finished floor, the routed hydrograph with the satellite pass
 * marked on it, wind / rain / terrain / access context, a habitability
 * planning estimate, and the forensic evidence-pack download.
 *
 * Every field is optional: pre-baked events carry `property.intel`, live
 * portfolios fetch it on demand, and any missing piece renders an honest
 * "unavailable" line rather than a blank or a crash.
 */
import { useEffect, useMemo, useState } from 'react';
import { api } from '../services/api';

const LEVEL = {
  alert:   { color: '#FF6B6B', bg: 'rgba(255,107,107,0.08)', border: 'rgba(255,107,107,0.35)', label: 'ALERT' },
  caution: { color: '#FFB347', bg: 'rgba(255,179,71,0.07)',  border: 'rgba(255,179,71,0.32)',  label: 'CAUTION' },
  info:    { color: '#A8D4E6', bg: 'rgba(168,212,230,0.05)', border: 'rgba(168,212,230,0.22)', label: 'INFO' },
};

const EVIDENCE_LABELS = {
  peak_wind_kt: 'Peak sustained wind (kt)', wind_class: 'Wind class', peak_wind_utc: 'Peak wind (UTC)',
  hours_ge_34kt: 'Hours ≥ 34 kt', hours_ge_64kt: 'Hours ≥ 64 kt', closest_approach_nm: 'Closest approach (nm)',
  depth_ft: 'Depth at grade (ft)', depth_above_floor_ft: 'Above finished floor (ft)',
  rain_3day_max_mm: 'Max 3-day rain (mm)', rain_event_total_mm: 'Event rain total (mm)',
  rain_peak_day: 'Heaviest rain day', hand_m: 'Height above drainage (m)', sar_lag_hours: 'Radar pass lag (h)',
  ground_asl_m: 'Ground above sea level (m)', surge_exposed: 'Surge exposure',
  jrc_occurrence_at_point_pct: 'Historic water at point (%)', jrc_occurrence_nearby_pct: 'Historic water nearby (%)',
  dataset: 'Dataset', status: 'Status', threshold_m: 'Impassable depth (m)', nearest_road_m: 'Nearest road (m)',
  dry_point_m: 'Nearest dry road (m)', depth_basis: 'Depth basis', basis: 'Basis',
};

const sectionTitle = {
  fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
  color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 10,
};
const card = {
  background: 'var(--wa-02)', border: '1px solid var(--wa-05)',
  borderRadius: 'var(--r-md)', padding: '12px 14px',
};

const fmtNum = (v, d = 1) => (v == null || Number.isNaN(+v) ? null : (+v).toFixed(d));
const fmtUtc = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC';
};

function FlagCard({ flag, onVerdict, verdict }) {
  const [open, setOpen] = useState(false);
  const lv = LEVEL[flag.level] || LEVEL.info;
  const ev = flag.evidence && typeof flag.evidence === 'object' ? flag.evidence : {};
  return (
    <div style={{ background: lv.bg, border: `1px solid ${lv.border}`, borderRadius: 'var(--r-md)', padding: '11px 13px', marginBottom: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
        <span style={{ fontSize: '0.56rem', fontWeight: 800, letterSpacing: '0.1em', color: lv.color,
                       border: `1px solid ${lv.border}`, borderRadius: 999, padding: '1px 7px' }}>{lv.label}</span>
        <span style={{ fontSize: '0.8rem', fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.3 }}>{flag.title}</span>
      </div>
      <p style={{ fontSize: '0.74rem', color: 'var(--text-body)', lineHeight: 1.55, margin: '0 0 8px' }}>{flag.detail}</p>
      {flag.action === 'hold_remote_deny' && (
        <div style={{ fontSize: '0.66rem', color: lv.color, fontWeight: 600, marginBottom: 8 }}>
          ↳ Remote denial is blocked for this property until someone looks.
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <button onClick={() => setOpen(o => !o)} style={chip(false)}>{open ? 'Hide evidence' : 'Evidence'}</button>
        <span style={{ flex: 1 }} />
        {verdict ? (
          <span style={{ fontSize: '0.66rem', color: 'var(--text-muted)' }}>
            {verdict === 'saving' ? 'Saving…' : verdict === 'error' ? 'Could not save' : `Marked ${verdict} ✓`}
          </span>
        ) : (
          <>
            <button onClick={() => onVerdict('confirmed')} style={chip(false)}>Confirm</button>
            <button onClick={() => onVerdict('dismissed')} style={chip(false)}>Dismiss</button>
          </>
        )}
      </div>
      {open && (
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8 }}>
          <tbody>
            {Object.entries(ev).filter(([, v]) => v !== null && v !== undefined && typeof v !== 'object').map(([k, v]) => (
              <tr key={k}>
                <td style={{ fontSize: '0.66rem', color: 'var(--text-muted)', padding: '3px 0' }}>{EVIDENCE_LABELS[k] || k}</td>
                <td style={{ fontSize: '0.68rem', color: 'var(--text-primary)', textAlign: 'right', padding: '3px 0' }}>
                  {typeof v === 'boolean' ? (v ? 'Yes' : 'No') : (/_utc$|_time$/.test(k) ? fmtUtc(v) : String(v))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function chip(active) {
  return {
    padding: '3px 10px', borderRadius: 999, cursor: 'pointer', fontFamily: 'var(--font)',
    fontSize: '0.64rem', fontWeight: 600, letterSpacing: '0.03em',
    background: active ? 'rgba(168,212,230,0.12)' : 'transparent',
    border: '1px solid var(--wa-12)', color: 'var(--text-body)',
  };
}

/* Side-view schematic: ground, finished floor, water line (+ routed peak). */
function DepthStack({ structure, peak }) {
  const floorFt = +structure.floor_height_ft || 0;
  const waterFt = Math.max(0, +structure.depth_at_structure_ft || 0);
  const peakFt = peak ? Math.max(0, +peak.depth_at_structure_ft || 0) : null;
  const top = Math.max(floorFt + 8, waterFt + 1, (peakFt || 0) + 1, 10);
  const H = 120, W = 300, base = 104, scale = (base - 12) / top;
  const y = (ft) => base - ft * scale;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Water level against the finished floor">
      <rect x="0" y={base} width={W} height={H - base} fill="rgba(140,110,80,0.35)" />
      <line x1="0" x2={W} y1={base} y2={base} stroke="rgba(200,170,130,0.7)" strokeWidth="1" />
      {/* house: foundation + walls + roof */}
      <rect x="110" y={y(floorFt)} width="90" height={Math.max(0, base - y(floorFt))} fill="rgba(160,160,170,0.35)" />
      <rect x="110" y={y(floorFt + 8)} width="90" height={y(floorFt) - y(floorFt + 8)} fill="rgba(210,215,225,0.16)" stroke="rgba(210,215,225,0.45)" />
      <polygon points={`104,${y(floorFt + 8)} 155,${y(floorFt + 11.5)} 206,${y(floorFt + 8)}`} fill="rgba(210,215,225,0.22)" />
      <line x1="100" x2="210" y1={y(floorFt)} y2={y(floorFt)} stroke="#E8E8EE" strokeWidth="1.5" strokeDasharray="4 3" />
      <text x="214" y={y(floorFt) + 3} fontSize="8.5" fill="#E8E8EE">floor {floorFt.toFixed(1)} ft</text>
      {peakFt != null && peakFt > waterFt + 0.05 && (
        <>
          <rect x="0" y={y(peakFt)} width={W} height={Math.max(0, y(waterFt) - y(peakFt))} fill="rgba(66,165,245,0.13)" />
          <line x1="0" x2={W} y1={y(peakFt)} y2={y(peakFt)} stroke="#64B5F6" strokeWidth="1" strokeDasharray="2 3" />
          <text x="4" y={y(peakFt) - 3} fontSize="8.5" fill="#90CAF9">routed peak {peakFt.toFixed(1)} ft</text>
        </>
      )}
      {waterFt > 0 && (
        <>
          <rect x="0" y={y(waterFt)} width={W} height={base - y(waterFt)} fill="rgba(33,150,243,0.30)" />
          <line x1="0" x2={W} y1={y(waterFt)} y2={y(waterFt)} stroke="#42A5F5" strokeWidth="1.5" />
          <text x="4" y={Math.min(base - 3, y(waterFt) + 11)} fontSize="8.5" fill="#BBDEFB">at satellite pass {waterFt.toFixed(1)} ft</text>
        </>
      )}
    </svg>
  );
}

/* Depth vs time with the SAR pass, the peak and the floor line. */
function Hydrograph({ hydro, floorFt }) {
  const pts = Array.isArray(hydro?.depth_ft) ? hydro.depth_ft.map(Number) : [];
  const times = useMemo(() => {
    if (Array.isArray(hydro?.times) && hydro.times.length === pts.length) return hydro.times.map(t => new Date(t).getTime());
    if (hydro?.t0 && hydro?.step_h) {
      const t0 = new Date(hydro.t0).getTime();
      return pts.map((_, i) => t0 + i * hydro.step_h * 3600e3);
    }
    return null;
  }, [hydro, pts.length]);
  if (pts.length < 2 || !times) return null;
  const W = 320, H = 140, L = 30, R = 8, T = 10, B = 22;
  const tMin = times[0], tMax = times[times.length - 1];
  const vMax = Math.max(1, ...pts, (floorFt || 0) + 0.5, +hydro.obs_depth_ft || 0) * 1.1;
  const X = (t) => L + ((t - tMin) / Math.max(1, tMax - tMin)) * (W - L - R);
  const Y = (v) => T + (1 - v / vMax) * (H - T - B);
  const path = pts.map((v, i) => `${i ? 'L' : 'M'}${X(times[i]).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
  const passT = hydro.pass_time ? new Date(hydro.pass_time).getTime() : null;
  const peakT = hydro.peak_time ? new Date(hydro.peak_time).getTime() : null;
  const days = [];
  for (let d = Math.ceil(tMin / 864e5) * 864e5; d <= tMax; d += 864e5 * Math.max(1, Math.round((tMax - tMin) / 864e5 / 6))) days.push(d);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Routed depth over time">
      {[0, 0.5, 1].map(f => (
        <g key={f}>
          <line x1={L} x2={W - R} y1={Y(vMax * f / 1.1)} y2={Y(vMax * f / 1.1)} stroke="var(--wa-07)" strokeWidth="0.5" />
          <text x={L - 4} y={Y(vMax * f / 1.1) + 3} fontSize="8" fill="var(--text-muted)" textAnchor="end">{(vMax * f / 1.1).toFixed(0)}</text>
        </g>
      ))}
      {days.map(d => (
        <text key={d} x={X(d)} y={H - 8} fontSize="7.5" fill="var(--text-muted)" textAnchor="middle">
          {new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })}
        </text>
      ))}
      {floorFt > 0 && floorFt < vMax && (
        <>
          <line x1={L} x2={W - R} y1={Y(floorFt)} y2={Y(floorFt)} stroke="#E8E8EE" strokeDasharray="4 3" strokeWidth="1" />
          <text x={W - R} y={Y(floorFt) - 3} fontSize="7.5" fill="#E8E8EE" textAnchor="end">finished floor</text>
        </>
      )}
      <path d={`${path} L${X(tMax)},${Y(0)} L${X(tMin)},${Y(0)} Z`} fill="rgba(33,150,243,0.18)" />
      <path d={path} fill="none" stroke="#42A5F5" strokeWidth="1.6" />
      {passT && passT >= tMin && passT <= tMax && (
        <>
          <line x1={X(passT)} x2={X(passT)} y1={T} y2={H - B} stroke="#FFB347" strokeWidth="1" />
          <circle cx={X(passT)} cy={Y(+hydro.obs_depth_ft || 0)} r="3.2" fill="#FFB347" />
          <text x={X(passT) + 4} y={T + 8} fontSize="7.5" fill="#FFB347">satellite pass</text>
        </>
      )}
      {peakT && (
        <circle cx={X(peakT)} cy={Y(+hydro.peak_depth_ft || 0)} r="3" fill="#64B5F6" stroke="#fff" strokeWidth="0.8" />
      )}
      <text x={L} y={T - 2} fontSize="7.5" fill="var(--text-muted)">ft</text>
    </svg>
  );
}

function Row({ label, value, sub }) {
  return (
    <tr style={{ borderBottom: '1px solid var(--wa-04)' }}>
      <td style={{ fontSize: '0.7rem', color: 'var(--text-muted)', padding: '7px 0', verticalAlign: 'top' }}>
        {label}{sub && <div style={{ fontSize: '0.6rem', color: 'var(--text-disabled)' }}>{sub}</div>}
      </td>
      <td style={{ fontSize: '0.74rem', color: 'var(--text-primary)', padding: '7px 0', textAlign: 'right', fontWeight: 600 }}>{value}</td>
    </tr>
  );
}

const dim = (t) => <span style={{ color: 'var(--text-disabled)', fontStyle: 'italic', fontWeight: 400 }}>{t}</span>;

export default function IntelPanel({ property, eventId: selectedEventId }) {
  const isPortfolio = !!property.isPortfolio;
  const eventId = (isPortfolio && property.portfolio_event_id) || selectedEventId;
  const [fetched, setFetched] = useState(null);
  const [state, setState] = useState('idle');   // idle | loading | running | error
  const [err, setErr] = useState('');
  const [verdicts, setVerdicts] = useState({});
  const [packState, setPackState] = useState('idle');

  const pid = property.portfolio_id;
  const own = useMemo(() => {
    const v = property.intel;
    if (v && typeof v === 'string') { try { return JSON.parse(v); } catch { return null; } }
    return v && typeof v === 'object' ? v : null;
  }, [property.intel]);
  useEffect(() => {
    setFetched(null); setVerdicts({}); setErr(''); setPackState('idle');
    if (own || !isPortfolio || !pid || !eventId) return;
    let alive = true;
    setState('loading');
    api.getPortfolioIntel(pid, eventId)
      .then(res => { if (alive) { setFetched(res?.properties?.[String(property.property_id)] ? res : null); setState('idle'); } })
      .catch(() => { if (alive) setState('idle'); });
    return () => { alive = false; };
  }, [property.property_id, pid, eventId, isPortfolio, own]);

  const intel = own || fetched?.properties?.[String(property.property_id)] || null;

  const runDeep = async () => {
    setState('running'); setErr('');
    try {
      const res = await api.runPortfolioIntel(pid, eventId);
      setFetched(res);
      setState('idle');
    } catch (e) {
      setErr(e?.detail || e?.message || 'Deep analysis failed.');
      setState('error');
    }
  };

  const onVerdict = async (flag, verdict) => {
    setVerdicts(v => ({ ...v, [flag.code]: 'saving' }));
    try {
      await api.submitFlagFeedback(property.property_id, {
        event_id: eventId || '', portfolio_id: isPortfolio ? (pid || '') : '',
        flag_code: flag.code, flag_level: flag.level, verdict,
      });
      setVerdicts(v => ({ ...v, [flag.code]: verdict }));
    } catch {
      setVerdicts(v => ({ ...v, [flag.code]: 'error' }));
    }
  };

  const downloadPack = async () => {
    setPackState('loading');
    try {
      const q = { event_id: eventId || '' };
      if (isPortfolio && pid) q.portfolio_id = pid;
      const blob = await api.downloadEvidencePack(property.property_id, q);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `altis-evidence-${property.property_id}.pdf`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      setPackState('idle');
    } catch (e) {
      setPackState('error');
      setErr(e?.message || 'Could not build the evidence pack.');
    }
  };

  if (!intel) {
    if (!isPortfolio) return null;
    return (
      <div style={{ marginBottom: 24 }}>
        <div style={sectionTitle}>Deep analysis</div>
        <div style={{ ...card, lineHeight: 1.55 }}>
          <p style={{ fontSize: '0.76rem', color: 'var(--text-body)', margin: '0 0 10px' }}>
            Adjuster flags (wind vs water, missed transient flooding, prior water, road access),
            depth above the finished floor and — where the satellite saw enough flooding —
            a routed hydrograph for every property in this portfolio.
          </p>
          <button onClick={runDeep} disabled={state === 'running' || state === 'loading'} style={{
            ...chip(true), padding: '7px 14px', fontSize: '0.7rem',
            cursor: state === 'running' ? 'wait' : 'pointer',
          }}>
            {state === 'running' ? 'Running… (1–3 min)' : state === 'loading' ? 'Checking…' : 'Run deep analysis'}
          </button>
          {err && <div style={{ fontSize: '0.68rem', color: '#FF6B6B', marginTop: 8 }}>{err}</div>}
        </div>
      </div>
    );
  }

  const flags = Array.isArray(intel.flags) ? intel.flags : [];
  const sd = intel.structure || null;
  const sdPeak = intel.structure_peak || null;
  const hydro = intel.hydrograph || null;
  const wind = intel.wind || null;
  const rain = intel.rain || null;
  const terr = intel.terrain || {};
  const acc = intel.access || null;
  const hab = intel.habitability || null;
  const above = sdPeak && sdPeak.depth_above_floor_ft > (sd?.depth_above_floor_ft ?? -1e9) ? sdPeak : sd;

  return (
    <div style={{ marginBottom: 24 }}>
      {property.original_class && property.original_class !== property.impact_class && (
        <div style={{ ...card, borderColor: 'rgba(255,179,71,0.35)', background: 'rgba(255,179,71,0.06)', marginBottom: 14 }}>
          <div style={{ fontSize: '0.7rem', color: '#FFB347', fontWeight: 700, marginBottom: 3 }}>
            Held back: {property.original_class} → {property.impact_class}
          </div>
          <div style={{ fontSize: '0.72rem', color: 'var(--text-body)', lineHeight: 1.5 }}>
            The satellite reads this property dry, but the flags below say that reading can't be trusted
            on its own. Remote denial is withheld.
          </div>
        </div>
      )}

      <div style={sectionTitle}>Adjuster flags {flags.length > 0 && <span style={{ color: 'var(--text-muted)' }}>· {flags.length}</span>}</div>
      {flags.length === 0 ? (
        <div style={{ ...card, fontSize: '0.74rem', color: 'var(--text-body)', marginBottom: 18 }}>
          No flags: no wind/flood allocation issue, no sign of missed transient flooding, no history of
          surface water, and a dry road route is available.
        </div>
      ) : (
        <div style={{ marginBottom: 18 }}>
          {flags.map(f => (
            <FlagCard key={f.code} flag={f} verdict={verdicts[f.code]} onVerdict={(v) => onVerdict(f, v)} />
          ))}
        </div>
      )}

      {sd && (
        <>
          <div style={sectionTitle}>Water vs. finished floor</div>
          <div style={{ ...card, marginBottom: 18 }}>
            <DepthStack structure={sd} peak={sdPeak} />
            <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 6 }}>
              <tbody>
                <Row label="Above finished floor" sub={sdPeak ? 'at routed peak' : 'at satellite pass'}
                     value={above.depth_above_floor_ft > 0
                       ? `${(+above.depth_above_floor_ft).toFixed(1)} ft (±${(+above.depth_above_floor_ci_ft).toFixed(1)})`
                       : 'Below floor'} />
                <Row label="Foundation" sub={sd.foundation_observed ? 'observed by adjuster' : 'assumed until observed'}
                     value={`${sd.foundation} · floor ${(+sd.floor_height_ft).toFixed(1)} ft`} />
                {intel.damage_depth_ft != null && (
                  <Row label="Depth used for damage curve" value={`${(+intel.damage_depth_ft).toFixed(1)} ft`} />
                )}
              </tbody>
            </table>
            {!sd.foundation_observed && (
              <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.5 }}>
                Record the first-floor type below (Street View helps) to replace the assumption — it
                recalculates this and becomes a calibration label.
              </div>
            )}
          </div>
        </>
      )}

      {hydro && (
        <>
          <div style={sectionTitle}>Synthetic revisit · routed hydrograph</div>
          <div style={{ ...card, marginBottom: 18 }}>
            <Hydrograph hydro={hydro} floorFt={sd ? +sd.floor_height_ft : 0} />
            <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 4 }}>
              <tbody>
                <Row label="Peak depth at grade" value={`${fmtNum(hydro.peak_depth_ft)} ft · ${fmtUtc(hydro.peak_time)}`} />
                <Row label="Satellite saw" value={`${fmtNum(hydro.obs_depth_ft)} ft · ${fmtUtc(hydro.pass_time)}`} />
                <Row label="Time wet" value={`${Math.round(hydro.hours_wet || 0)} h`} />
                {hydro.hours_above_floor != null && <Row label="Time above floor" value={`${Math.round(hydro.hours_above_floor)} h`} />}
              </tbody>
            </table>
            <div style={{ fontSize: '0.63rem', color: 'var(--text-muted)', marginTop: 8, lineHeight: 1.5 }}>
              Local-inertial 2D router (Bates et al. 2010) forced by observed rainfall and calibrated to this
              event's radar pass{hydro.skill_csi != null ? ` (fit CSI ${(+hydro.skill_csi).toFixed(2)})` : ''}.
              Basis here: {hydro.basis}. Hydraulically consistent with the observation — not independently
              validated per property.
            </div>
          </div>
        </>
      )}

      <div style={sectionTitle}>Event context</div>
      <div style={{ ...card, padding: '4px 14px', marginBottom: 18 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            <Row label="Peak sustained wind" sub={wind ? 'NHC best track · open-terrain estimate' : null}
                 value={wind ? `${Math.round(wind.peak_kt)} kt · ${wind.category}` : dim('no tropical cyclone')} />
            {wind && <Row label="Wind timing" value={`${fmtUtc(wind.peak_time)} · ${wind.hours_ge_34kt ?? 0} h ≥ 34 kt`} />}
            <Row label="Heaviest 3-day rain" sub={rain?.peak_day ? `peak ${rain.peak_day}` : null}
                 value={rain ? `${Math.round(rain.max_3day_mm)} mm (${(rain.max_3day_mm / 25.4).toFixed(1)} in)` : dim('unavailable')} />
            <Row label="Radar pass after peak"
                 value={intel.sar_lag_hours != null ? `${(intel.sar_lag_hours / 24).toFixed(1)} days` : dim('unknown')} />
            <Row label="Height above drainage" sub="HAND"
                 value={terr.hand_m != null ? `${(+terr.hand_m).toFixed(1)} m` : dim('unavailable')} />
            <Row label="Ground elevation" value={terr.ground_asl_m != null ? `${(+terr.ground_asl_m).toFixed(1)} m ASL` : dim('unavailable')} />
            <Row label="Road access"
                 value={!acc ? dim('unavailable')
                   : acc.status === 'accessible' ? 'Dry route available'
                   : acc.status === 'street_flooded' ? 'Street flooded'
                   : acc.status === 'isolated' ? 'Cut off by water' : dim(acc.reason || 'unknown')} />
            <Row label="Uninhabitable (planning)"
                 value={!hab ? dim('no water above floor data')
                   : hab.displacement ? `${hab.days_low}–${hab.days_high} days · ${hab.accommodation}` : 'Not displaced'} />
          </tbody>
        </table>
      </div>

      <button onClick={downloadPack} disabled={packState === 'loading'} style={{
        width: '100%', padding: '10px 14px', borderRadius: 'var(--r-md)', cursor: packState === 'loading' ? 'wait' : 'pointer',
        background: 'rgba(168,212,230,0.08)', border: '1px solid rgba(168,212,230,0.3)', color: 'var(--teal)',
        fontFamily: 'var(--font)', fontSize: '0.74rem', fontWeight: 700, letterSpacing: '0.04em',
      }}>
        {packState === 'loading' ? 'Building evidence pack…' : '⬇ Forensic evidence pack (PDF)'}
      </button>
      {packState === 'error' && err && <div style={{ fontSize: '0.68rem', color: '#FF6B6B', marginTop: 6 }}>{err}</div>}
    </div>
  );
}
