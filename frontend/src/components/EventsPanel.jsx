/* EventsPanel.jsx — Full event list (replaces the old fixed bottom chips) */

export default function EventsPanel({ events, selectedEvent, onSelect, loading }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '0 20px 14px' }}>
        <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#fff' }}>Events</div>
        <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', marginTop: 4 }}>
          {events.length} processed disaster{events.length !== 1 ? 's' : ''}
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px' }}>
        {events.length === 0 && (
          <div style={{ padding: '20px 8px', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
            No events found. Run the pipeline against an event in pipeline/config.py
            to populate this list.
          </div>
        )}

        {events.map(evt => {
          const active = selectedEvent === evt.id;
          return (
            <button
              key={evt.id}
              onClick={() => !loading && onSelect(evt.id)}
              style={{
                width: '100%', textAlign: 'left', display: 'flex', alignItems: 'center', gap: 10,
                padding: '12px 10px', marginBottom: 4, borderRadius: 'var(--r-md)',
                background: active ? 'rgba(168,212,230,0.1)' : 'transparent',
                border: `1px solid ${active ? 'rgba(168,212,230,0.25)' : 'transparent'}`,
                cursor: loading ? 'not-allowed' : 'pointer', fontFamily: 'var(--font)',
                opacity: loading && !active ? 0.55 : 1, transition: 'background 0.15s ease',
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'rgba(255,255,255,0.04)'; }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent'; }}
            >
              <span style={{
                width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                background: active ? '#A8D4E6' : 'rgba(255,255,255,0.15)',
              }} />
              <span>
                <div style={{ fontSize: '0.82rem', fontWeight: 700, color: active ? '#A8D4E6' : '#fff' }}>
                  {evt.label}
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 1 }}>
                  {evt.sub}
                </div>
              </span>
            </button>
          );
        })}
      </div>

      <div style={{
        padding: '14px 20px', borderTop: '1px solid rgba(255,255,255,0.05)',
        fontSize: '0.68rem', color: 'var(--text-muted)', lineHeight: 1.5,
      }}>
        To add a new disaster: add its bounding box to <code style={{ color: 'var(--text-secondary)' }}>pipeline/config.py</code>,
        run steps 02–04, and it appears here automatically.
      </div>
    </div>
  );
}
