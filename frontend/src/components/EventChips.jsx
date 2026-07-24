/* EventChips.jsx — Bottom center event selector chips */

const ICONS = {
  harvey: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M8 2v2M8 12v2M2 8h2M12 8h2M3.7 3.7l1.4 1.4M10.9 10.9l1.4 1.4M3.7 12.3l1.4-1.4M10.9 5.1l1.4-1.4"
            stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  ian: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M8 2C5 2 2 4.5 2 8s3 6 6 6 6-2.5 6-6M8 2c1.5 1 2.5 3 2 5s-2 4-2 6"
            stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none"/>
    </svg>
  ),
};

export default function EventChips({ events, selectedEvent, onSelect, loading }) {
  return (
    <div style={{
      position:   'fixed',
      bottom:     32,
      left:       '50%',
      transform:  'translateX(-50%)',
      zIndex:     'var(--z-ui)',
      display:    'flex',
      gap:        12,
      pointerEvents: 'all',
    }}>
      {events.map(evt => {
        const active = selectedEvent === evt.id;
        return (
          <button
            key={evt.id}
            onClick={() => !loading && onSelect(evt.id)}
            style={{
              display:       'flex',
              alignItems:    'center',
              gap:           10,
              padding:       '12px 20px',
              background:    active
                ? 'rgba(168,212,230,0.12)'
                : 'var(--panel)',
              border:        `1px solid ${active
                ? 'rgba(168,212,230,0.35)'
                : 'var(--wa-07)'}`,
              borderRadius:  'var(--r-lg)',
              color:         active ? 'var(--teal)' : 'var(--text-secondary)',
              cursor:        loading ? 'not-allowed' : 'pointer',
              fontFamily:    'var(--font)',
              backdropFilter: 'blur(16px)',
              transition:    'all 0.2s ease',
              textAlign:     'left',
              opacity:       loading && !active ? 0.6 : 1,
            }}
            onMouseEnter={e => {
              if (!active && !loading) {
                e.currentTarget.style.background = 'rgba(168,212,230,0.07)';
                e.currentTarget.style.borderColor = 'rgba(168,212,230,0.2)';
                e.currentTarget.style.color = 'var(--teal)';
              }
            }}
            onMouseLeave={e => {
              if (!active) {
                e.currentTarget.style.background = 'var(--panel)';
                e.currentTarget.style.borderColor = 'var(--wa-07)';
                e.currentTarget.style.color = 'var(--text-secondary)';
              }
            }}
          >
            {/* Icon */}
            <span style={{ color: active ? 'var(--teal)' : '#3A5060' }}>
              {ICONS[evt.id]}
            </span>

            {/* Text */}
            <span>
              <div style={{ fontSize: '0.84rem', fontWeight: 700, letterSpacing: '-0.01em' }}>
                {evt.label}
              </div>
              <div style={{ fontSize: '0.68rem', opacity: 0.65, marginTop: 1 }}>
                {evt.sub}
              </div>
            </span>

            {/* Active indicator */}
            {active && (
              <span style={{
                width:        6,
                height:       6,
                borderRadius: '50%',
                background:   'var(--teal)',
                marginLeft:   4,
                flexShrink:   0,
              }} />
            )}
          </button>
        );
      })}
    </div>
  );
}
