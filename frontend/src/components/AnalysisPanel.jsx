import { useState, useMemo } from 'react';

const TRIAGE_CLASSES = ['Dispatch', 'Remote-Approve', 'Remote-Deny', 'Review'];
const MAX_ROWS = 150;

function Chip({ label, active, color, onClick }) {
  return (
    <button onClick={onClick} style={{
      padding: '4px 9px', borderRadius: 'var(--r-sm)', fontSize: '0.66rem', fontWeight: 700,
      background: active ? `${color}22` : 'transparent',
      border: `1px solid ${active ? color : 'rgba(255,255,255,0.1)'}`,
      color: active ? color : 'var(--text-muted)', cursor: 'pointer', fontFamily: 'var(--font)',
      whiteSpace: 'nowrap',
    }}>
      {label}
    </button>
  );
}

const TRIAGE_COLORS = {
  'Dispatch': '#FF4444', 'Remote-Approve': '#4CAF82',
  'Remote-Deny': '#6B8FA3', 'Review': '#FFB347',
};

export default function AnalysisPanel({
  eventProperties = [], eventLabel,
  portfolioProperties = [], portfolioLabel, portfolioAnalyzed,
  onSelectProperty, onAddToCompare, compareIds, compareFull,
  onAnalyzePortfolio, analyzing,
}) {
  const hasEvent     = eventProperties.length > 0;
  const hasPortfolio = portfolioProperties.length > 0;

  const [source, setSource] = useState(hasEvent ? 'event' : 'portfolio');
  const [search, setSearch] = useState('');
  const [triageFilter, setTriageFilter] = useState(new Set());
  const [minConfidence, setMinConfidence] = useState(0);
  const [minDepth, setMinDepth] = useState('');
  const [maxDepth, setMaxDepth] = useState('');
  const [urbanOnly, setUrbanOnly] = useState(false);
  const [sortKey, setSortKey] = useState('confidence_score');
  const [sortDir, setSortDir] = useState('desc');

  const dataset    = source === 'event' ? eventProperties : portfolioProperties;
  const datasetName = source === 'event' ? eventLabel : portfolioLabel;
  const hasTriage   = dataset.length > 0 && dataset[0].impact_class !== undefined;

  const toggleTriage = (cls) => {
    const next = new Set(triageFilter);
    next.has(cls) ? next.delete(cls) : next.add(cls);
    setTriageFilter(next);
  };

  const filtered = useMemo(() => {
    let rows = dataset.filter(p => {
      if (search) {
        const q = search.toLowerCase();
        const hit = `${p.address || ''}`.toLowerCase().includes(q) ||
                    `${p.property_id || ''}`.toLowerCase().includes(q);
        if (!hit) return false;
      }
      if (hasTriage) {
        if (triageFilter.size > 0 && !triageFilter.has(p.impact_class)) return false;
        if ((p.confidence_score || 0) < minConfidence) return false;
        if (minDepth !== '' && (p.max_depth_ft || 0) < parseFloat(minDepth)) return false;
        if (maxDepth !== '' && (p.max_depth_ft || 0) > parseFloat(maxDepth)) return false;
        if (urbanOnly && !(p.urban_flag == 1 || p.urban_flag === true)) return false;
      }
      return true;
    });

    if (hasTriage) {
      rows = [...rows].sort((a, b) => {
        const av = a[sortKey] ?? 0, bv = b[sortKey] ?? 0;
        return sortDir === 'asc' ? av - bv : bv - av;
      });
    }
    return rows;
  }, [dataset, search, triageFilter, minConfidence, minDepth, maxDepth, urbanOnly, hasTriage, sortKey, sortDir]);

  const shown = filtered.slice(0, MAX_ROWS);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '0 16px 12px' }}>
        <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#fff', marginBottom: 10 }}>Analysis</div>

        {/* Source toggle */}
        {hasEvent && hasPortfolio && (
          <div style={{ display: 'flex', gap: 4, marginBottom: 10, background: 'rgba(255,255,255,0.03)', borderRadius: 'var(--r-sm)', padding: 3 }}>
            {['event', 'portfolio'].map(s => (
              <button key={s} onClick={() => setSource(s)} style={{
                flex: 1, padding: '6px 0', borderRadius: 4, fontSize: '0.68rem', fontWeight: 700,
                background: source === s ? 'rgba(168,212,230,0.12)' : 'transparent',
                border: 'none', color: source === s ? '#A8D4E6' : 'var(--text-muted)',
                cursor: 'pointer', fontFamily: 'var(--font)', textTransform: 'capitalize',
              }}>
                {s}
              </button>
            ))}
          </div>
        )}

        {!hasEvent && !hasPortfolio && (
          <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)' }}>
            Select an event or upload a portfolio to see properties here.
          </div>
        )}

        {dataset.length > 0 && (
          <>
            <input
              type="text" placeholder="Search address or ID…" value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                width: '100%', padding: '7px 10px', fontSize: '0.76rem', marginBottom: 10,
                background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: 'var(--r-sm)', color: '#fff', fontFamily: 'var(--font)',
              }}
            />

            {hasTriage ? (
              <>
                <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 10 }}>
                  {TRIAGE_CLASSES.map(cls => (
                    <Chip key={cls} label={cls} active={triageFilter.has(cls)}
                          color={TRIAGE_COLORS[cls]} onClick={() => toggleTriage(cls)} />
                  ))}
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 8 }}>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.66rem', color: 'var(--text-muted)', marginBottom: 3 }}>
                      <span>Min confidence</span><span>{minConfidence}%</span>
                    </div>
                    <input type="range" min="0" max="100" value={minConfidence}
                           onChange={e => setMinConfidence(+e.target.value)}
                           style={{ width: '100%' }} />
                  </div>

                  <div style={{ display: 'flex', gap: 8 }}>
                    <input type="number" placeholder="Min depth ft" value={minDepth}
                           onChange={e => setMinDepth(e.target.value)}
                           style={{ flex: 1, padding: '5px 8px', fontSize: '0.7rem', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 'var(--r-sm)', color: '#fff' }} />
                    <input type="number" placeholder="Max depth ft" value={maxDepth}
                           onChange={e => setMaxDepth(e.target.value)}
                           style={{ flex: 1, padding: '5px 8px', fontSize: '0.7rem', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 'var(--r-sm)', color: '#fff' }} />
                  </div>

                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.72rem', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                    <input type="checkbox" checked={urbanOnly} onChange={e => setUrbanOnly(e.target.checked)} />
                    Urban SAR zone only
                  </label>
                </div>
              </>
            ) : source === 'portfolio' && !portfolioAnalyzed && (
              <div style={{
                padding: '10px 12px', background: 'rgba(255,179,71,0.06)', border: '1px solid rgba(255,179,71,0.15)',
                borderRadius: 'var(--r-md)', fontSize: '0.72rem', color: 'var(--text-secondary)', marginBottom: 10, lineHeight: 1.5,
              }}>
                Not yet analyzed against flood data.
                {eventLabel && (
                  <button onClick={onAnalyzePortfolio} disabled={analyzing} style={{
                    display: 'block', width: '100%', marginTop: 8, padding: '8px 0',
                    background: 'rgba(168,212,230,0.12)', border: '1px solid rgba(168,212,230,0.3)',
                    borderRadius: 'var(--r-sm)', color: '#A8D4E6', fontWeight: 700, fontSize: '0.72rem',
                    cursor: analyzing ? 'wait' : 'pointer', fontFamily: 'var(--font)',
                  }}>
                    {analyzing ? 'Analyzing…' : `Analyze against ${eventLabel}`}
                  </button>
                )}
              </div>
            )}

            <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)' }}>
              {filtered.length} of {dataset.length} {datasetName ? `— ${datasetName}` : ''}
              {filtered.length > MAX_ROWS && ` (showing first ${MAX_ROWS})`}
            </div>
          </>
        )}
      </div>

      {/* Table */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 16px' }}>
        {shown.map(p => {
          const inCompare = compareIds?.has(p.property_id);
          return (
            <div
              key={p.property_id}
              onClick={() => onSelectProperty(p)}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '9px 8px',
                borderRadius: 'var(--r-sm)', cursor: 'pointer', marginBottom: 2,
                transition: 'background 0.12s ease',
              }}
              onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '0.74rem', color: '#ddd', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {p.address?.split(',')[0] || p.property_id}
                </div>
                {hasTriage ? (
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 3 }}>
                    <span className={`badge badge-${p.impact_class}`} style={{ fontSize: '0.6rem', padding: '1px 6px' }}>
                      {p.impact_class}
                    </span>
                    <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>
                      {parseFloat(p.max_depth_ft || 0).toFixed(1)}ft · {p.confidence_score || 0}%
                    </span>
                  </div>
                ) : (
                  <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: 2 }}>
                    {p.policy_number || ''}
                  </div>
                )}
              </div>
              {hasTriage && (
                <button
                  onClick={e => { e.stopPropagation(); !inCompare && !compareFull && onAddToCompare(p); }}
                  disabled={inCompare || compareFull}
                  title={inCompare ? 'In compare tray' : 'Add to SAR compare'}
                  style={{
                    flexShrink: 0, width: 22, height: 22, borderRadius: 5,
                    background: inCompare ? 'rgba(168,212,230,0.15)' : 'rgba(255,255,255,0.04)',
                    border: 'none', color: inCompare ? '#A8D4E6' : 'var(--text-muted)',
                    cursor: (inCompare || compareFull) ? 'default' : 'pointer', fontSize: '0.8rem',
                  }}
                >
                  {inCompare ? '✓' : '+'}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
