import { useState, useEffect } from 'react';
import { api } from '../services/api.js';

/* DataFreshness — when the satellites last saw this area, when the next radar
   pass is expected, and what the river gauges say right now. This is what
   makes a speed claim honest: results arrive hours after a PASS, and passes
   are days apart, so the user should see the schedule, not assume it. */

const CAT_COLOR = {
  major: '#FF4444', moderate: '#FF8A3D', minor: '#FFB347', action: '#E8D06A',
  no_flooding: '#4CAF82',
};
const CAT_LABEL = {
  major: 'Major flood', moderate: 'Moderate flood', minor: 'Minor flood',
  action: 'Near flood stage', no_flooding: 'Normal',
};

function fmtWhen(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function fmtHours(h) {
  if (h == null) return '';
  if (h < 48) return `${Math.round(h)} h`;
  return `${(h / 24).toFixed(1)} days`;
}

export default function DataFreshness({ eventId, bbox, title = 'Data freshness' }) {
  const [passes, setPasses] = useState(null);
  const [gauges, setGauges] = useState(null);
  const [err, setErr] = useState('');
  const key = eventId || (bbox ? bbox.map(v => v.toFixed(3)).join(',') : '');

  useEffect(() => {
    if (!key) return undefined;
    let alive = true;
    setPasses(null); setGauges(null); setErr('');
    const p = eventId ? api.getEventPasses(eventId) : api.getPasses(bbox);
    p.then(d => alive && setPasses(d))
     .catch(e => alive && setErr(e?.detail || 'Satellite catalogue unavailable.'));
    if (bbox) {
      api.getGauges(bbox).then(d => alive && setGauges(d)).catch(() => alive && setGauges({ gauges: [] }));
    }
    return () => { alive = false; };
  }, [key]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!key) return null;
  const s1 = passes?.sentinel1?.summary;
  const s2 = passes?.sentinel2;
  const flooding = (gauges?.gauges || []).filter(g => ['minor', 'moderate', 'major', 'action'].includes(g.observed_category));

  return (
    <div style={card}>
      <div style={hdr}>{title}</div>
      {err && <div style={{ fontSize: '0.68rem', color: '#FFB347' }}>{err}</div>}
      {!passes && !err && <div style={muted}>Checking satellite schedule…</div>}
      {s1 && (
        <>
          <Row label="Last radar pass (Sentinel-1)"
               value={s1.last_usable_pass ? `${fmtWhen(s1.last_usable_pass)} · ${fmtHours(s1.hours_since_last_pass)} ago` : 'none in 40 days'} />
          <Row label="Next expected radar pass"
               value={s1.next_expected_pass ? `${fmtWhen(s1.next_expected_pass)} · in ${fmtHours(s1.hours_until_next_pass)}` : 'unknown'}
               strong />
          {s1.median_revisit_days != null && (
            <Row label="Typical revisit here" value={`every ~${s1.median_revisit_days} days`} />
          )}
          {s2 && !s2.error && (
            <Row label="Last clear optical (Sentinel-2)"
                 value={s2.last_mostly_clear_pass ? fmtWhen(s2.last_mostly_clear_pass) : 'none mostly clear'} />
          )}
          <div style={{ ...muted, marginTop: 6 }}>
            Results arrive within hours of a pass. Next-pass times assume each satellite's 12-day
            repeat; ESA can change its plan. River gauges below fill the gap between passes.
          </div>
        </>
      )}
      {gauges && (
        <div style={{ marginTop: 10 }}>
          <div style={{ ...hdr, marginBottom: 6 }}>River gauges now (NOAA)</div>
          {gauges.gauges.length === 0 && <div style={muted}>No NOAA gauges in this area (US only).</div>}
          {gauges.gauges.length > 0 && flooding.length === 0 && (
            <div style={muted}>{gauges.gauges.length} gauges, none at or above flood stage.</div>
          )}
          {flooding.map(g => (
            <div key={g.lid} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: '0.68rem', padding: '3px 0' }}>
              <span style={{ color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{g.name}</span>
              <span style={{ color: CAT_COLOR[g.observed_category], fontWeight: 700, flexShrink: 0 }}>
                {CAT_LABEL[g.observed_category]}{g.observed_stage_ft != null ? ` · ${g.observed_stage_ft.toFixed(1)} ft` : ''}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Row({ label, value, strong }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: '0.68rem', padding: '3px 0' }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: strong ? 'var(--teal)' : 'var(--text-primary)', fontWeight: strong ? 700 : 600, textAlign: 'right' }}>{value}</span>
    </div>
  );
}

const card = {
  marginTop: 12, padding: '10px 12px', borderRadius: 'var(--r-md)',
  background: 'var(--wa-02)', border: '1px solid var(--wa-06)',
};
const hdr = { fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 6 };
const muted = { fontSize: '0.64rem', color: 'var(--text-muted)', lineHeight: 1.45 };
