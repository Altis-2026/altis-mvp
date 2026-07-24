import SarPair from './SarPair.jsx';

const TRIAGE_COLORS = {
  'Dispatch': '#FF4444', 'Remote-Approve': '#4CAF82',
  'Remote-Deny': '#6B8FA3', 'Review': '#FFB347',
};

export default function SarComparePanel({ compareList = [], onRemove, onClear }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '0 16px 14px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: '0.92rem', fontWeight: 700, color: 'var(--text-primary)' }}>SAR compare</div>
          <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', marginTop: 4 }}>
            {compareList.length} of 4 properties
          </div>
        </div>
        {compareList.length > 0 && (
          <button onClick={onClear} style={{
            background: 'none', border: 'none', color: 'var(--text-muted)',
            fontSize: '0.66rem', cursor: 'pointer', fontFamily: 'var(--font)', textDecoration: 'underline',
          }}>
            Clear all
          </button>
        )}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 16px 16px' }}>
        {compareList.length === 0 && (
          <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.6 }}>
            Click <strong style={{ color: 'var(--text-secondary)', fontWeight: 700 }}>+ Add to SAR compare</strong> on
            any property, in the drawer or the Analysis table, to line up to 4
            before/after pairs side by side here.
          </div>
        )}

        {compareList.map(p => (
          <div key={p.property_id} style={{
            border: '1px solid var(--wa-07)', borderRadius: 'var(--r-md)',
            padding: 12, marginBottom: 12, background: 'var(--wa-02)',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: '0.76rem', fontWeight: 600, color: 'var(--text-bright)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {p.address?.split(',')[0] || p.property_id}
                </div>
                {p.impact_class && (
                  <span className={`badge badge-${p.impact_class}`} style={{ fontSize: '0.58rem', padding: '1px 6px', marginTop: 4, display: 'inline-block' }}>
                    {p.impact_class}
                  </span>
                )}
              </div>
              <button onClick={() => onRemove(p.property_id)} style={{
                background: 'none', border: 'none', color: 'var(--text-muted)',
                cursor: 'pointer', fontSize: '0.9rem', flexShrink: 0, padding: '0 0 0 8px',
              }}>
                ✕
              </button>
            </div>
            <SarPair propertyId={p.property_id} compact />
            <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: 6 }}>
              {parseFloat(p.max_depth_ft || 0).toFixed(1)}ft max depth · {p.confidence_score || 0}% confidence
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
