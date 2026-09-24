/* PrelandfallPanel — "which properties flood if this storm delivers X mm?"
 *
 * Pre-landfall susceptibility from a model trained on 11 real flood events
 * (Sen1Floods11) and validated leave-one-event-out. For a pre-baked event it
 * shows the hindcast (observed rainfall standing in for the forecast) with its
 * back-test; for a portfolio it scores the book against the live 7-day
 * forecast or any rainfall scenario. The rainfall slider reads each
 * property's own probability curve, so moving it is instant.
 */
import { useEffect, useMemo, useState } from 'react';
import { api } from '../services/api';

const BAND_COLOR = { 'very high': '#E5484D', high: '#F28C38', elevated: '#E0C35A', low: '#4F8A9C' };
const band = (p) => (p >= 0.6 ? 'very high' : p >= 0.35 ? 'high' : p >= 0.15 ? 'elevated' : 'low');

function interp(curve, xs, x) {
  if (!Array.isArray(curve) || !curve.length) return 0;
  if (x <= xs[0]) return curve[0];
  for (let i = 1; i < xs.length; i++) {
    if (x <= xs[i]) {
      const f = (x - xs[i - 1]) / (xs[i] - xs[i - 1]);
      return curve[i - 1] + f * (curve[i] - curve[i - 1]);
    }
  }
  return curve[curve.length - 1];
}

// Slider runs on a log scale so 25 mm and 600 mm are both reachable.
const toSlider = (mm, lo, hi) => (Math.log(mm) - Math.log(lo)) / (Math.log(hi) - Math.log(lo));
const fromSlider = (t, lo, hi) => Math.exp(Math.log(lo) + t * (Math.log(hi) - Math.log(lo)));

const title = { fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em', color: 'var(--teal)',
                textTransform: 'uppercase', marginBottom: 8 };
const card = { background: 'var(--wa-02)', border: '1px solid var(--wa-05)', borderRadius: 'var(--r-md)', padding: '12px 14px' };
const btn = (primary) => ({
  padding: '7px 12px', borderRadius: 'var(--r-md)', cursor: 'pointer', fontFamily: 'var(--font)',
  fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.03em',
  background: primary ? 'rgba(168,212,230,0.12)' : 'transparent',
  border: `1px solid ${primary ? 'rgba(168,212,230,0.35)' : 'var(--wa-12)'}`,
  color: primary ? 'var(--teal)' : 'var(--text-body)',
});

export default function PrelandfallPanel({ eventId, eventLabel, eventProperties, portfolioId, portfolioProperties,
                                           onSelectProperty, onColorOverride }) {
  const [data, setData] = useState(null);         // { scenarios_mm, properties, hindcast?, backtest?, model?, forecast? }
  const [source, setSource] = useState(null);     // 'event' | 'portfolio'
  const [status, setStatus] = useState('idle');
  const [err, setErr] = useState('');
  const [rain, setRain] = useState(150);
  const [colorMap, setColorMap] = useState(false);
  const [showCard, setShowCard] = useState(false);
  const [card0, setCard0] = useState(null);

  useEffect(() => {
    api.getSusceptibilityModel?.().then(setCard0).catch(() => setCard0(null));
  }, []);

  useEffect(() => {
    setData(null); setErr(''); setSource(null);
    if (!eventId) return;
    let alive = true;
    setStatus('loading');
    api.getSusceptibility(eventId)
      .then(d => {
        if (!alive) return;
        setData(d); setSource('event'); setStatus('idle');
        const m = d?.hindcast?.rain_3day_mm_median;
        if (m) setRain(Math.max(25, Math.min(600, m)));
      })
      .catch(() => { if (alive) setStatus('idle'); });
    return () => { alive = false; };
  }, [eventId]);

  const scorePortfolio = async (useForecast) => {
    if (!portfolioId) return;
    setStatus('running'); setErr('');
    try {
      const d = await api.runSusceptibility(useForecast
        ? { portfolio_id: portfolioId, use_forecast: true }
        : { portfolio_id: portfolioId, rain_3day_mm: rain });
      setData(d); setSource('portfolio'); setStatus('idle');
      if (d?.rain_3day_mm) setRain(Math.max(25, Math.min(600, d.rain_3day_mm)));
    } catch (e) {
      setErr(e?.detail || e?.message || 'Scoring failed.');
      setStatus('idle');
    }
  };

  const xs = data?.scenarios_mm || [25, 50, 100, 150, 200, 300, 400, 600];
  const lo = xs[0], hi = xs[xs.length - 1];
  const rows = useMemo(() => {
    const props = data?.properties || {};
    const pool = source === 'portfolio' ? portfolioProperties : eventProperties;
    const byId = new Map((pool || []).map(p => [String(p.property_id), p]));
    return Object.entries(props).map(([id, v]) => {
      const p = interp(v.curve, xs, rain);
      return { id, p, band: band(p), row: byId.get(id), v };
    }).sort((a, b) => b.p - a.p);
  }, [data, rain, source, eventProperties, portfolioProperties, xs]);

  const counts = useMemo(() => {
    const c = { 'very high': 0, high: 0, elevated: 0, low: 0 };
    rows.forEach(r => { c[r.band] += 1; });
    return c;
  }, [rows]);
  const tivAtRisk = useMemo(() => rows.reduce((s, r) => s + (r.p >= 0.35 ? (+r.row?.coverage_amount || 0) : 0), 0), [rows]);

  useEffect(() => {
    if (!onColorOverride) return;
    if (!colorMap || !rows.length) { onColorOverride(null); return; }
    const m = {};
    rows.forEach(r => { m[r.id] = BAND_COLOR[r.band]; });
    onColorOverride(m);
  }, [colorMap, rows, onColorOverride]);
  useEffect(() => () => onColorOverride?.(null), [onColorOverride]);

  const model = data?.model || card0;
  const val = model?.validation;

  return (
    <div style={{ padding: '18px 18px 24px' }}>
      <div style={{ fontSize: '0.95rem', fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>Pre-landfall risk</div>
      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.5, marginBottom: 16 }}>
        Which properties flood if the storm delivers this much rain — before any satellite has looked.
      </div>

      {portfolioId && (
        <div style={{ ...card, marginBottom: 14 }}>
          <div style={title}>Your portfolio</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button style={btn(true)} disabled={status === 'running'} onClick={() => scorePortfolio(true)}>
              {status === 'running' ? 'Scoring…' : 'Score vs 7-day forecast'}
            </button>
            <button style={btn(false)} disabled={status === 'running'} onClick={() => scorePortfolio(false)}>
              Score at {Math.round(rain)} mm
            </button>
          </div>
          {data?.forecast && source === 'portfolio' && (
            <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)', marginTop: 8, lineHeight: 1.5 }}>
              Forecast heaviest 3 days: <b style={{ color: 'var(--text-primary)' }}>{Math.round(data.forecast.max_3day_mm)} mm</b>
              {' '}({data.forecast.source}).
            </div>
          )}
          {err && <div style={{ fontSize: '0.68rem', color: '#FF6B6B', marginTop: 8 }}>{err}</div>}
        </div>
      )}

      {!data ? (
        <div style={{ ...card, fontSize: '0.74rem', color: 'var(--text-body)', lineHeight: 1.55 }}>
          {status === 'loading' ? 'Loading…'
            : eventId ? 'No pre-landfall hindcast for this event yet.'
            : 'Select an event to see its hindcast, or load a portfolio to score it against the forecast.'}
        </div>
      ) : (
        <>
          <div style={{ ...card, marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <div style={title}>Rainfall scenario · heaviest 3 days</div>
              <div style={{ fontSize: '0.9rem', fontWeight: 800, color: 'var(--text-primary)' }}>
                {Math.round(rain)} mm <span style={{ fontSize: '0.66rem', color: 'var(--text-muted)', fontWeight: 600 }}>({(rain / 25.4).toFixed(1)} in)</span>
              </div>
            </div>
            <input type="range" min="0" max="1" step="0.001" value={toSlider(rain, lo, hi)}
                   onChange={e => setRain(fromSlider(+e.target.value, lo, hi))}
                   style={{ width: '100%', accentColor: '#F28C38' }} aria-label="Rainfall scenario" />
            {source === 'event' && data.hindcast && (
              <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                What actually fell at {eventLabel || 'this event'}: median {Math.round(data.hindcast.rain_3day_mm_median)} mm
                {' '}(max {Math.round(data.hindcast.rain_3day_mm_max)} mm, {data.hindcast.rain_product}).
                <button style={{ ...btn(false), padding: '2px 8px', marginLeft: 6, fontSize: '0.6rem' }}
                        onClick={() => setRain(Math.max(lo, Math.min(hi, data.hindcast.rain_3day_mm_median)))}>use</button>
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6, marginTop: 12 }}>
              {['very high', 'high', 'elevated', 'low'].map(b => (
                <div key={b} style={{ textAlign: 'center', padding: '6px 4px', borderRadius: 'var(--r-sm)',
                                      background: 'var(--wa-03)', border: `1px solid ${BAND_COLOR[b]}55` }}>
                  <div style={{ fontSize: '1rem', fontWeight: 800, color: BAND_COLOR[b] }}>{counts[b]}</div>
                  <div style={{ fontSize: '0.56rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{b}</div>
                </div>
              ))}
            </div>
            {tivAtRisk > 0 && (
              <div style={{ fontSize: '0.7rem', color: 'var(--text-body)', marginTop: 10 }}>
                Insured value at high/very-high risk: <b>${Math.round(tivAtRisk).toLocaleString()}</b>
              </div>
            )}
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, fontSize: '0.7rem', color: 'var(--text-body)', cursor: 'pointer' }}>
              <input type="checkbox" checked={colorMap} onChange={e => setColorMap(e.target.checked)} />
              Colour map pins by flood probability
            </label>
          </div>

          <div style={title}>Highest-risk properties</div>
          <div style={{ ...card, padding: '4px 0', marginBottom: 14 }}>
            {rows.slice(0, 25).map(r => (
              <button key={r.id} onClick={() => r.row && onSelectProperty?.(source === 'portfolio' ? { ...r.row, isPortfolio: true } : r.row)}
                      style={{ display: 'flex', alignItems: 'center', gap: 10, width: '100%', padding: '7px 14px',
                               background: 'transparent', border: 'none', borderBottom: '1px solid var(--wa-04)',
                               cursor: r.row ? 'pointer' : 'default', textAlign: 'left', fontFamily: 'var(--font)' }}>
                <span style={{ width: 38, fontSize: '0.74rem', fontWeight: 800, color: BAND_COLOR[r.band] }}>{Math.round(r.p * 100)}%</span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', fontSize: '0.72rem', color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {r.row?.address || r.id}
                  </span>
                  <span style={{ display: 'block', height: 3, marginTop: 4, borderRadius: 2, background: 'var(--wa-05)' }}>
                    <span style={{ display: 'block', height: 3, width: `${Math.round(r.p * 100)}%`, borderRadius: 2, background: BAND_COLOR[r.band] }} />
                  </span>
                </span>
                {r.v?.features?.hand_m != null && (
                  <span style={{ fontSize: '0.6rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>HAND {r.v.features.hand_m} m</span>
                )}
              </button>
            ))}
          </div>

          {source === 'event' && data.backtest && (
            <>
              <div style={title}>Back-test against what happened</div>
              <div style={{ ...card, marginBottom: 14, fontSize: '0.72rem', color: 'var(--text-body)', lineHeight: 1.55 }}>
                {data.backtest.vs_nfip_claims && (
                  <div style={{ marginBottom: 6 }}>
                    vs FEMA flood-insurance claims: Spearman <b>{data.backtest.vs_nfip_claims.spearman}</b> across {data.backtest.vs_nfip_claims.zips} zip codes.
                  </div>
                )}
                {data.backtest.vs_sar?.auc != null && (
                  <div>vs radar-observed flooding: AUC <b>{data.backtest.vs_sar.auc}</b> ({data.backtest.vs_sar.sar_wet} wet / {data.backtest.vs_sar.sar_dry} dry). <span style={{ color: 'var(--text-muted)' }}>{data.backtest.vs_sar.note}</span></div>
                )}
              </div>
            </>
          )}
        </>
      )}

      {model && (
        <div style={card}>
          <button onClick={() => setShowCard(s => !s)} style={{ ...btn(false), width: '100%' }}>
            {showCard ? 'Hide model card' : 'Model card · how this was trained and tested'}
          </button>
          {showCard && (
            <div style={{ fontSize: '0.7rem', color: 'var(--text-body)', lineHeight: 1.6, marginTop: 10 }}>
              <div>Gradient-boosted trees on terrain (height above drainage, slope, upstream area, floodplain position, historic water) and rainfall.</div>
              <div style={{ marginTop: 6 }}>Trained on <b>{model.training?.dataset}</b>: {model.training?.chips} chips, {model.training?.samples?.toLocaleString?.()} labelled pixels, events {(model.training?.events || []).join(', ')}.</div>
              {val && (
                <div style={{ marginTop: 6 }}>
                  Validation ({val.scheme}): pooled AUC <b>{val.pooled_oof_auc}</b> on floods the model never saw.
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                    {Object.entries(val.per_event || {}).map(([k, v]) => (
                      <span key={k} style={{ fontSize: '0.6rem', padding: '2px 6px', borderRadius: 999, border: '1px solid var(--wa-12)' }}>{k} {v.auc}</span>
                    ))}
                  </div>
                </div>
              )}
              <div style={{ marginTop: 8, color: 'var(--text-muted)' }}>
                Probability that a location floods given the scenario — a ranking and planning signal, not a depth or a claim
                probability. Training chips come from places that flooded, so absolute values are conditional on an event of that size.
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
