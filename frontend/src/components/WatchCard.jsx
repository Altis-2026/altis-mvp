import { useState, useEffect } from 'react';
import { api } from '../services/api.js';

/* WatchCard — what is happening right now: active tropical cyclones (NHC)
   and recently declared flood-type disasters (FEMA), plus how much of the
   loaded portfolio sits inside each storm's 5-day forecast cone. This is the
   pre-event view: who to pre-position adjusters for before landfall. */

const money = (v) => {
  if (!v) return '$0';
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}k`;
  return `$${Math.round(v)}`;
};

export default function WatchCard({ portfolioId }) {
  const [watch, setWatch] = useState(null);
  const [exposure, setExposure] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    api.getWatch().then(d => alive && setWatch(d)).catch(e => alive && setErr(e?.detail || 'Watch feeds unavailable.'));
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!portfolioId) { setExposure(null); return undefined; }
    let alive = true;
    api.getStormExposure(portfolioId).then(d => alive && setExposure(d)).catch(() => alive && setExposure(null));
    return () => { alive = false; };
  }, [portfolioId]);

  const expFor = (id) => exposure?.storms?.find(s => s.id === id)?.exposure;

  return (
    <div style={card}>
      <div style={hdr}>Active now</div>
      {err && <div style={{ fontSize: '0.68rem', color: '#FFB347' }}>{err}</div>}
      {!watch && !err && <div style={muted}>Checking NHC and FEMA…</div>}
      {watch && (
        <>
          {watch.active_storms.length === 0 && <div style={muted}>No active tropical cyclones (NHC).</div>}
          {watch.active_storms.map(s => {
            const e = expFor(s.id);
            return (
              <div key={s.id} style={{ padding: '5px 0' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: '0.72rem' }}>
                  <span style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{s.classification} {s.name}</span>
                  <span style={{ color: 'var(--text-muted)' }}>{s.intensity_kt ? `${s.intensity_kt} kt` : ''}</span>
                </div>
                {portfolioId && e && (
                  <div style={{ fontSize: '0.66rem', marginTop: 2,
                                color: e.n_in_cone ? '#FFB347' : 'var(--text-muted)' }}>
                    {e.n_in_cone
                      ? `${e.n_in_cone.toLocaleString()} of your properties in the 5-day cone · ${money(e.tiv_in_cone)} insured`
                      : 'None of your properties are in its 5-day cone.'}
                  </div>
                )}
              </div>
            );
          })}
          {watch.recent_disasters.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ ...muted, marginBottom: 4 }}>FEMA flood-type disasters declared in the last 60 days</div>
              {watch.recent_disasters.slice(0, 5).map(d => (
                <div key={d.disaster_number} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: '0.68rem', padding: '2px 0' }}>
                  <span style={{ color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {d.title}
                  </span>
                  <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{d.states.join(', ')} · {d.declared.slice(5)}</span>
                </div>
              ))}
            </div>
          )}
          {portfolioId && exposure && (
            <div style={{ ...muted, marginTop: 6 }}>{exposure.note}</div>
          )}
        </>
      )}
    </div>
  );
}

const card = {
  marginTop: 12, padding: '10px 12px', borderRadius: 'var(--r-md)',
  background: 'var(--wa-02)', border: '1px solid var(--wa-06)',
};
const hdr = { fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 6 };
const muted = { fontSize: '0.64rem', color: 'var(--text-muted)', lineHeight: 1.45 };
