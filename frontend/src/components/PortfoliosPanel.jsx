/* PortfoliosPanel.jsx — Saved carrier portfolios, reload without re-uploading */

function formatDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso.replace(' ', 'T') + 'Z');
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return iso;
  }
}

export default function PortfoliosPanel({ portfolios, activePortfolioId, onSelect, onUploadClick, loading }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '0 20px 14px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: '0.92rem', fontWeight: 700, color: 'var(--text-primary)' }}>Portfolios</div>
          <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', marginTop: 4 }}>
            {portfolios.length} saved
          </div>
        </div>
        <button onClick={onUploadClick} style={{
          background: 'rgba(168,212,230,0.1)', border: '1px solid rgba(168,212,230,0.25)',
          borderRadius: 'var(--r-sm)', color: 'var(--teal)', fontSize: '0.68rem', fontWeight: 700,
          padding: '6px 10px', cursor: 'pointer', fontFamily: 'var(--font)',
        }}>
          + New
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px' }}>
        {portfolios.length === 0 && (
          <div style={{ padding: '20px 8px', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
            No portfolios uploaded yet. Click "+ New" to geocode a carrier's policy CSV.
          </div>
        )}

        {portfolios.map(p => {
          const active = activePortfolioId === p.id;
          return (
            <button
              key={p.id}
              onClick={() => !loading && onSelect(p.id)}
              style={{
                width: '100%', textAlign: 'left', padding: '12px 10px', marginBottom: 4,
                borderRadius: 'var(--r-md)',
                background: active ? 'rgba(168,212,230,0.1)' : 'transparent',
                border: `1px solid ${active ? 'rgba(168,212,230,0.25)' : 'transparent'}`,
                cursor: loading ? 'not-allowed' : 'pointer', fontFamily: 'var(--font)',
                transition: 'background 0.15s ease',
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'var(--wa-04)'; }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent'; }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontSize: '0.8rem', fontWeight: 700, color: active ? 'var(--teal)' : '#fff' }}>
                  {p.id}
                </span>
                <span style={{ fontSize: '0.64rem', color: 'var(--text-muted)' }}>
                  {formatDate(p.created_at)}
                </span>
              </div>
              <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                {p.geocoded_count}/{p.total_count} geocoded
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
