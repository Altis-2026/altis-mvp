/* Sidebar.jsx — Icon rail (always visible) + expandable panel (340px) */

const RAIL_WIDTH  = 56;
export const PANEL_WIDTH = 340;

const NAV_ITEMS = [
  { id: 'events',     label: 'Events',       icon: 'M8 2v2M16 2v2M3 9h18M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z' },
  { id: 'portfolios', label: 'Portfolios',   icon: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z' },
  { id: 'dispatch',   label: 'Dispatch Queue', icon: 'M3 12h4l2-7 4 14 2-7h6' },
  { id: 'analysis',   label: 'Analysis',     icon: 'M4 19V10M10 19V5M16 19v-7M22 19H2' },
  { id: 'compare',    label: 'SAR Compare',  icon: 'M3 4h8v16H3zM13 4h8v16h-8zM3 12h8M13 12h8' },
  { id: 'reports',    label: 'Reports',      icon: 'M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM13 3v6h6M9 13h6M9 17h6' },
  { id: 'operations', label: 'Operations',   icon: 'M12 2a3 3 0 0 1 3 3 7 7 0 0 1 4 6v3l2 3H3l2-3v-3a7 7 0 0 1 4-6 3 3 0 0 1 3-3zM9 19a3 3 0 0 0 6 0' },
];

function NavIcon({ item, active, onClick, badge }) {
  return (
    <button
      onClick={onClick}
      title={item.label}
      style={{
        width: 40, height: 40, borderRadius: 'var(--r-md)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: active ? 'rgba(168,212,230,0.12)' : 'transparent',
        border: 'none', cursor: 'pointer', position: 'relative',
        transition: 'background 0.15s ease',
      }}
      onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'rgba(255,255,255,0.05)'; }}
      onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent'; }}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
           stroke={active ? '#A8D4E6' : '#6B7B85'} strokeWidth="1.8"
           strokeLinecap="round" strokeLinejoin="round">
        <path d={item.icon} />
      </svg>
      {badge > 0 && (
        <span style={{
          position: 'absolute', top: 2, right: 2,
          width: 15, height: 15, borderRadius: '50%',
          background: '#A8D4E6', color: '#000',
          fontSize: '0.58rem', fontWeight: 800,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          {badge}
        </span>
      )}
      {active && (
        <span style={{
          position: 'absolute', left: -8, top: '50%', transform: 'translateY(-50%)',
          width: 3, height: 18, borderRadius: 2, background: '#A8D4E6',
        }} />
      )}
    </button>
  );
}

export default function Sidebar({ activePanel, onSetPanel, compareCount, children }) {
  return (
    <>
      {/* Icon rail */}
      <div style={{
        position: 'fixed', top: 0, left: 0, bottom: 0, width: RAIL_WIDTH,
        zIndex: 'var(--z-ui)', background: 'rgba(4,6,12,0.92)',
        borderRight: '1px solid rgba(255,255,255,0.06)', backdropFilter: 'blur(16px)',
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        paddingTop: 64, gap: 6, pointerEvents: 'all',
      }}>
        {NAV_ITEMS.map(item => (
          <NavIcon
            key={item.id}
            item={item}
            active={activePanel === item.id}
            badge={item.id === 'compare' ? compareCount : 0}
            onClick={() => onSetPanel(activePanel === item.id ? null : item.id)}
          />
        ))}
      </div>

      {/* Expandable panel */}
      {activePanel && (
        <div
          className="anim-fade-in"
          style={{
            position: 'fixed', top: 0, left: RAIL_WIDTH, bottom: 0, width: PANEL_WIDTH,
            zIndex: 15, background: 'rgba(4,6,12,0.95)',
            borderRight: '1px solid rgba(255,255,255,0.06)', backdropFilter: 'blur(20px)',
            display: 'flex', flexDirection: 'column', pointerEvents: 'all',
            paddingTop: 64, overflow: 'hidden',
          }}
        >
          {children}
        </div>
      )}
    </>
  );
}

export { RAIL_WIDTH };
