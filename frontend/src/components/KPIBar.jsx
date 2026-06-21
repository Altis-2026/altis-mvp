/* KPIBar.jsx — Property stats bar above event chips */

function KPI({ label, value, color = '#fff', sub }) {
  return (
    <div style={{ textAlign: 'center', padding: '10px 20px', borderRight: '1px solid rgba(255,255,255,0.05)' }}>
      <div style={{
        fontSize:      '0.64rem',
        fontWeight:    700,
        letterSpacing: '0.1em',
        color:         'var(--text-muted)',
        textTransform: 'uppercase',
        marginBottom:  6,
      }}>
        {label}
      </div>
      <div style={{
        fontSize:      '1.8rem',
        fontWeight:    800,
        color,
        letterSpacing: '-0.03em',
        lineHeight:    1,
      }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginTop: 4 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

export default function KPIBar({ stats }) {
  if (!stats) return null;

  const savings = stats.estimated_savings
    ? `$${(stats.estimated_savings / 1000).toFixed(0)}k`
    : '$0';

  return (
    <div
      className="anim-slide-in-up"
      style={{
        position:   'fixed',
        top:        72,
        left:       72,
        zIndex:     'var(--z-ui)',
        display:    'flex',
        background: 'rgba(4,6,12,0.9)',
        border:     '1px solid rgba(255,255,255,0.06)',
        borderRadius: 'var(--r-lg)',
        backdropFilter: 'blur(20px)',
        overflow:   'hidden',
        whiteSpace: 'nowrap',
      }}
    >
      <KPI
        label="Properties"
        value={stats.total?.toLocaleString()}
        color="var(--text-primary)"
      />
      <KPI
        label="Dispatch"
        value={stats.dispatch?.toLocaleString()}
        color="var(--dispatch)"
      />
      <KPI
        label="Remote Resolved"
        value={stats.remote_total?.toLocaleString()}
        color="var(--approve)"
      />
      <KPI
        label="Estimated Savings"
        value={savings}
        color="var(--teal)"
        sub={`${stats.remote_total?.toLocaleString()} inspections avoided`}
        style={{ borderRight: 'none' }}
      />
    </div>
  );
}
