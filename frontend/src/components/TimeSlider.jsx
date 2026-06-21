/* TimeSlider.jsx — Pre/post satellite imagery toggle (bottom left) */

export default function TimeSlider({ timeMode, onTimeChange, hasTile }) {
  return (
    <div
      className="anim-fade-in"
      style={{
        position:   'fixed',
        bottom:     32,
        left:       72,
        zIndex:     'var(--z-ui)',
        display:    'flex',
        flexDirection: 'column',
        gap:        8,
      }}
    >
      {/* Label */}
      <div style={{
        fontSize:      '0.6rem',
        fontWeight:    700,
        letterSpacing: '0.12em',
        color:         'var(--text-muted)',
        textTransform: 'uppercase',
        paddingLeft:   2,
      }}>
        Satellite View
      </div>

      {/* Toggle */}
      <div style={{
        display:       'flex',
        background:    'rgba(4,6,12,0.88)',
        border:        '1px solid rgba(255,255,255,0.07)',
        borderRadius:  'var(--r-md)',
        backdropFilter: 'blur(16px)',
        padding:       3,
        gap:           3,
      }}>
        {[
          { value: 'pre',  label: 'Pre-Event' },
          { value: 'post', label: 'Post-Event' },
        ].map(({ value, label }) => {
          const active = timeMode === value;
          return (
            <button
              key={value}
              onClick={() => onTimeChange(value)}
              style={{
                padding:       '8px 16px',
                background:    active ? 'rgba(168,212,230,0.12)' : 'transparent',
                border:        `1px solid ${active ? 'rgba(168,212,230,0.3)' : 'transparent'}`,
                borderRadius:  'var(--r-sm)',
                color:         active ? '#A8D4E6' : 'var(--text-muted)',
                fontSize:      '0.76rem',
                fontWeight:    active ? 700 : 500,
                letterSpacing: '0.04em',
                cursor:        'pointer',
                fontFamily:    'var(--font)',
                transition:    'all 0.18s ease',
                display:       'flex',
                alignItems:    'center',
                gap:           6,
              }}
            >
              {/* Status dot */}
              <span style={{
                width:        5,
                height:       5,
                borderRadius: '50%',
                background:   active
                  ? (value === 'post' ? '#4CAF82' : '#6B8FA3')
                  : 'rgba(255,255,255,0.15)',
                transition:   'background 0.18s ease',
              }} />
              {label}
            </button>
          );
        })}
      </div>

      {/* Overlay status */}
      <div style={{
        fontSize:   '0.62rem',
        color:      hasTile ? 'rgba(76,175,130,0.7)' : 'rgba(255,255,255,0.18)',
        paddingLeft: 2,
      }}>
        {hasTile
          ? '● Satellite overlay active'
          : '○ No overlay (GEE auth required)'}
      </div>
    </div>
  );
}
