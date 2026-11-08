import { useState, useEffect } from 'react';
import { api } from '../services/api.js';

/* AnalogsCard — "events like this one", from the free flood library
   (Global Flood Database + FEMA declarations + NFIP claim outcomes + every
   Altis run). Shows why each matched and, where real claims exist, what
   they paid. History as evidence, explicitly not a forecast. */

const money = (v) => (v == null ? '—' : `$${Math.round(v).toLocaleString()}`);

export default function AnalogsCard({ eventId, lat, lon, date, cause }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  const [open, setOpen] = useState(false);
  const key = eventId || (lat != null && lon != null ? `${lat.toFixed(2)},${lon.toFixed(2)},${date || ''}` : '');

  useEffect(() => {
    if (!key) return undefined;
    let alive = true;
    setData(null); setErr('');
    const p = eventId ? api.getEventAnalogs(eventId) : api.getAnalogs({ lat, lon, date, cause });
    p.then(d => alive && setData(d)).catch(e => alive && setErr(e?.detail || 'Event library unavailable.'));
    return () => { alive = false; };
  }, [key]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!key) return null;
  const pr = data?.priors;
  const all = data?.analogs || [];
  const shown = open ? all : all.slice(0, 4);

  return (
    <div style={{ marginTop: 22 }}>
      <div style={sec}>Similar past floods</div>
      {err && <div style={{ fontSize: '0.7rem', color: '#FFB347' }}>{err}</div>}
      {!data && !err && <div style={muted}>Searching the flood library…</div>}
      {data && (
        <>
          {pr && (
            <div style={{ padding: '10px 12px', borderRadius: 'var(--r-md)', background: 'rgba(168,212,230,0.05)',
                          border: '1px solid rgba(168,212,230,0.18)', marginBottom: 10 }}>
              <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', marginBottom: 6 }}>
                What insured claims looked like in the {pr.based_on} closest analogs with NFIP data
                {pr.dollars?.startsWith('constant') ? ' (today’s dollars)' : ''}
              </div>
              <Stat label="Median payout per claim" value={money(pr.median_paid_per_claim_usd)} />
              {pr.payout_range_80pct_usd && (
                <Stat label="Past estimates like this landed within"
                      value={`${money(pr.payout_range_80pct_usd[0])} – ${money(pr.payout_range_80pct_usd[1])}`} />
              )}
              <Stat label="Share of claims paid"
                    value={pr.share_of_claims_paid != null ? `${Math.round(pr.share_of_claims_paid * 100)}%` : '—'} />
              {pr.share_paid_range_80pct && (
                <Stat label="Past estimates landed within"
                      value={`${Math.round(pr.share_paid_range_80pct[0] * 100)}% – ${Math.round(pr.share_paid_range_80pct[1] * 100)}%`} />
              )}
              <Stat label="Median water in building"
                    value={pr.median_water_depth_in != null ? `${pr.median_water_depth_in} in` : '—'} />
              <Stat label="Median flood duration"
                    value={pr.median_flood_duration_h != null ? `${pr.median_flood_duration_h} h` : '—'} />
              {pr.backtest && (
                <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: 8, lineHeight: 1.45 }}>
                  Backtested on {pr.backtest.n_events} past US floods: the payout estimate was typically off by
                  about {pr.backtest.payout_typical_factor}× (a national-average guess: {pr.backtest.baseline_typical_factor}×),
                  and 80% of the time within {pr.backtest.payout_80pct_factor}×.{' '}
                  {pr.backtest.forecast_grade
                    ? 'This beat the national-average guess with statistical confidence.'
                    : 'The improvement over the national average is not statistically established yet, so treat this as history, not a forecast.'}
                </div>
              )}
            </div>
          )}
          {shown.map(a => (
            <div key={a.id} style={{ padding: '8px 0', borderTop: '1px solid var(--wa-05)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ fontSize: '0.74rem', color: 'var(--text-primary)', fontWeight: 600 }}>{a.name}</span>
                <span style={{ fontSize: '0.66rem', color: 'var(--teal)', fontWeight: 700, flexShrink: 0 }}>
                  {Math.round(a.similarity * 100)}% match
                </span>
              </div>
              <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', marginTop: 2 }}>
                {a.start?.slice(0, 7)} · {a.why.join(' · ') || 'partial match'}
                {a.nfip?.n_claims ? ` · ${a.nfip.n_claims.toLocaleString()} NFIP claims` : ''}
                {a.dead ? ` · ${a.dead} deaths` : ''}
              </div>
            </div>
          ))}
          {all.length > 4 && (
            <button onClick={() => setOpen(v => !v)} style={link}>
              {open ? 'Show fewer' : `Show all ${all.length}`}
            </button>
          )}
          <div style={{ ...muted, marginTop: 8 }}>
            {data.library?.n_events?.toLocaleString()} events in the library (satellite-mapped floods worldwide,
            US disaster declarations, and every Altis run). Analogs are evidence about events like this one,
            not a forecast.
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', padding: '2px 0' }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{value}</span>
    </div>
  );
}

const sec = { fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.08em', color: 'var(--teal)',
              textTransform: 'uppercase', marginBottom: 10 };
const muted = { fontSize: '0.64rem', color: 'var(--text-muted)', lineHeight: 1.45 };
const link = { background: 'none', border: 'none', color: 'var(--teal)', fontSize: '0.68rem', fontWeight: 700,
               cursor: 'pointer', padding: '6px 0', fontFamily: 'var(--font)' };
