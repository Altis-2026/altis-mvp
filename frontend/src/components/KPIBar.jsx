/* KPIBar.jsx — Property stats bar above event chips */

function KPI({ label, value, color = '#fff', sub }) {
  return (
    <div style={{ textAlign: 'center', padding: '10px 20px', borderRight: '1px solid var(--wa-05)' }}>
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

function money(v) {
  const n = +v || 0;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toLocaleString()}`;
}

import { useIsNarrow } from '../hooks/useIsMobile.js';

export default function KPIBar({ stats, exposure, leftInset = 72 }) {
  const narrow = useIsNarrow();
  if (!stats && !exposure) return null;

  const savings = stats?.estimated_savings
    ? `$${(stats.estimated_savings / 1000).toFixed(0)}k`
    : '$0';

  return (
    <div
      className="anim-slide-in-up"
      style={{
        position:   'fixed',
        top:        narrow ? 64 : 72,
        left:       narrow ? 64 : leftInset + 16,
        right:      narrow ? 8 : 'auto',
        maxWidth:   narrow ? 'none' : `calc(100vw - ${leftInset + 32}px)`,
        overflowX:  'auto',
        transition: 'left 0.25s ease',
        zIndex:     'var(--z-ui)',
        display:    'flex',
        background: 'var(--panel-strong)',
        border:     '1px solid var(--wa-06)',
        borderRadius: 'var(--r-lg)',
        backdropFilter: 'blur(20px)',
        overflow:   'hidden',
        whiteSpace: 'nowrap',
      }}
    >
      {exposure ? (
        /* Portfolio-level view: what a CAT ops manager reads first */
        (() => {
          const remote = (exposure.by_class?.['Remote-Approve'] ?? 0) +
                         (exposure.by_class?.['Remote-Deny'] ?? 0);
          return (
        <>
          <KPI
            label="TIV Exposed"
            value={money(exposure.tiv_in_zone)}
            color="var(--text-primary)"
            sub={`${exposure.policies_in_zone} of ${exposure.policies_total} policies in zone`}
          />
          <KPI
            label="Est. Loss Range"
            value={exposure.est_loss_high_usd > 0
              ? `${money(exposure.est_loss_low_usd)}–${money(exposure.est_loss_high_usd)}`
              : '—'}
            color="var(--review)"
            sub={exposure.est_loss_mid_usd ? `central ${money(exposure.est_loss_mid_usd)}` : undefined}
          />
          <KPI
            label="Field Dispatch"
            value={(exposure.by_class?.Dispatch ?? 0).toLocaleString()}
            color="var(--dispatch)"
          />
          <KPI
            label="Savings vs Full Deployment"
            value={money(remote * 750)}
            color="var(--teal)"
            sub={`${remote.toLocaleString()} truck rolls avoided`}
          />
        </>
          );
        })()
      ) : (
        <>
          <KPI
            label="Policies Analyzed"
            value={stats.total?.toLocaleString()}
            color="var(--text-primary)"
          />
          <KPI
            label="Field Dispatch"
            value={stats.dispatch?.toLocaleString()}
            color="var(--dispatch)"
          />
          <KPI
            label="Resolved Remotely"
            value={stats.remote_total?.toLocaleString()}
            color="var(--approve)"
          />
          <KPI
            label="Savings vs Full Deployment"
            value={savings}
            color="var(--teal)"
            sub={`${stats.remote_total?.toLocaleString()} truck rolls avoided`}
          />
        </>
      )}
    </div>
  );
}
