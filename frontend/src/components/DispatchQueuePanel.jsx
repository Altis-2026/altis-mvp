import { useState, useEffect, useMemo } from 'react';
import { api } from '../services/api.js';
import { rankDispatch } from '../utils/priority.js';

/* DispatchQueuePanel — the work queue an adjuster actually clears, ordered by
   severity × coverage rather than a flat list. Event queues come pre-ranked
   from the backend; portfolio queues are ranked client-side from analysis
   results. The #1 item is what the CAT team should hit first. */

const TRIAGE_COLORS = {
  'Dispatch': '#FF4444', 'Review': '#FFB347',
  'Remote-Approve': '#4CAF82', 'Remote-Deny': '#6B8FA3',
};

export default function DispatchQueuePanel({
  eventId, eventLabel, eventProperties = [],
  portfolioProps = [], portfolioAnalyzed, portfolioLabel,
  onSelectProperty, onOpenGrid,
}) {
  const hasEvent = eventProperties.length > 0;
  const hasPortfolio = portfolioAnalyzed && portfolioProps.length > 0;

  const [source, setSource] = useState(hasEvent ? 'event' : 'portfolio');
  const [eventQueue, setEventQueue] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => { if (!hasEvent && hasPortfolio) setSource('portfolio'); }, [hasEvent, hasPortfolio]);

  useEffect(() => {
    if (source !== 'event' || !eventId) { setEventQueue([]); return; }
    setLoading(true);
    api.getDispatchQueue(eventId)
      .then(d => setEventQueue(d.queue || []))
      .catch(() => setEventQueue([]))
      .finally(() => setLoading(false));
  }, [eventId, source]);

  const portfolioQueue = useMemo(
    () => (hasPortfolio ? rankDispatch(portfolioProps) : []),
    [portfolioProps, hasPortfolio]
  );

  const queue = source === 'event' ? eventQueue : portfolioQueue;
  const maxScore = queue.length ? queue[0].priority_score || 1 : 1;
  const label = source === 'event' ? eventLabel : portfolioLabel;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '0 18px 12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <div style={{ fontSize: '0.92rem', fontWeight: 700, color: 'var(--text-primary)' }}>Dispatch Queue</div>
          {queue.length > 0 && (
            <button onClick={() => onOpenGrid?.(source)} style={linkBtn}>Open grid →</button>
          )}
        </div>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 3 }}>
          Ranked by severity × coverage
        </div>

        {hasEvent && hasPortfolio && (
          <div style={{ display: 'flex', gap: 4, marginTop: 10, background: 'var(--wa-03)', borderRadius: 'var(--r-sm)', padding: 3 }}>
            {['event', 'portfolio'].map(s => (
              <button key={s} onClick={() => setSource(s)} style={{
                flex: 1, padding: '6px 0', borderRadius: 4, fontSize: '0.68rem', fontWeight: 700,
                background: source === s ? 'rgba(168,212,230,0.12)' : 'transparent',
                border: 'none', color: source === s ? 'var(--teal)' : 'var(--text-muted)',
                cursor: 'pointer', fontFamily: 'var(--font)', textTransform: 'capitalize',
              }}>{s}</button>
            ))}
          </div>
        )}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 16px' }}>
        {loading && <div style={empty}>Loading queue…</div>}

        {!loading && queue.length === 0 && (
          <div style={empty}>
            {source === 'portfolio' && !portfolioAnalyzed
              ? 'Analyze a portfolio against an event to build its dispatch queue.'
              : 'Select an event to see its severity-ranked dispatch queue.'}
          </div>
        )}

        {!loading && queue.length > 0 && (
          <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)', margin: '0 6px 8px' }}>
            {queue.length} properties prioritized {label ? `for ${label}` : ''}
          </div>
        )}

        {!loading && queue.map((p, i) => {
          const color = TRIAGE_COLORS[p.impact_class] || '#6B8FA3';
          const top = i === 0;
          const pct = Math.max(6, Math.round((p.priority_score / maxScore) * 100));
          return (
            <button key={p.property_id} onClick={() => onSelectProperty?.(p)} style={{
              display: 'block', width: '100%', textAlign: 'left', marginBottom: 6,
              padding: '11px 12px', borderRadius: 'var(--r-md)', cursor: 'pointer',
              fontFamily: 'var(--font)',
              background: top ? 'rgba(255,68,68,0.06)' : 'var(--wa-02)',
              border: `1px solid ${top ? 'rgba(255,68,68,0.3)' : 'var(--wa-06)'}`,
            }}
              onMouseEnter={e => e.currentTarget.style.background = 'var(--wa-05)'}
              onMouseLeave={e => e.currentTarget.style.background = top ? 'rgba(255,68,68,0.06)' : 'var(--wa-02)'}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                <span style={{
                  flexShrink: 0, width: 22, height: 22, borderRadius: 6,
                  background: top ? '#FF4444' : 'var(--wa-06)',
                  color: top ? '#000' : 'var(--text-secondary)',
                  fontSize: '0.7rem', fontWeight: 800,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>{p.priority_rank}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '0.76rem', color: 'var(--text-bright)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {p.address?.split(',')[0] || p.property_id}
                  </div>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 3 }}>
                    <span className={`badge badge-${p.impact_class}`} style={{ fontSize: '0.58rem', padding: '1px 5px' }}>{p.impact_class}</span>
                    <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>
                      {parseFloat(p.max_depth_ft || 0).toFixed(1)}ft
                      {p.coverage_amount > 0 && ` · $${(p.coverage_amount / 1000).toFixed(0)}k`}
                      {` · ${p.confidence_score || 0}%`}
                    </span>
                  </div>
                </div>
                <span style={{ flexShrink: 0, fontSize: '0.74rem', fontWeight: 800, color, fontVariantNumeric: 'tabular-nums' }}>
                  {Math.round(p.priority_score)}
                </span>
              </div>
              <div style={{ height: 3, borderRadius: 2, marginTop: 8, background: 'var(--wa-05)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${pct}%`, background: color, opacity: 0.85 }} />
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

const empty = { padding: '20px 10px', fontSize: '0.76rem', color: 'var(--text-muted)', lineHeight: 1.5 };
const linkBtn = {
  background: 'none', border: 'none', color: 'var(--teal)', fontSize: '0.7rem',
  fontWeight: 700, cursor: 'pointer', fontFamily: 'var(--font)', padding: 0,
};
