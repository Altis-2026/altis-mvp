/* Header.jsx — Fixed top bar with logo, event label, theme toggle, upload */
import { useState } from 'react';
import { AltisLogo } from './Brand.jsx';
import { getTheme, applyTheme } from '../theme.js';
import { useIsNarrow } from '../hooks/useIsMobile.js';

export default function Header({ selectedEvent, onUploadClick, onGridClick, loading }) {
  const [theme, setTheme] = useState(getTheme());
  const compact = useIsNarrow();

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    setTheme(next);
  };

  return (
    <header style={{
      position:   'fixed',
      top:        0,
      left:       0,
      right:      0,
      zIndex:     'var(--z-header)',
      padding:    compact ? '10px 12px 10px 60px' : '16px 24px 16px 72px',
      display:    'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      background: 'linear-gradient(to bottom, var(--header-fade) 0%, transparent 100%)',
      pointerEvents: 'none',
    }}>

      {/* Left: Logo + event label */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, pointerEvents: 'all', minWidth: 0 }}>
        <AltisLogo />
        <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.15 }}>
          <span className="wordmark" style={{
            fontSize: compact ? '0.95rem' : '1.05rem', fontWeight: 800, letterSpacing: '0.12em',
          }}>
            ALTIS
          </span>
          {!compact && (
            <span style={{ fontSize: '0.56rem', fontWeight: 600, letterSpacing: '0.14em', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Real-time satellite ground truth
            </span>
          )}
        </div>
        {selectedEvent && !compact && (
          <>
            <span style={{ color: 'var(--wa-15)', fontSize: '0.9rem' }}>|</span>
            <span style={{
              fontSize: '0.82rem', fontWeight: 500, color: 'var(--text-secondary)',
              letterSpacing: '0.01em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {selectedEvent.label}
            </span>
            {loading && (
              <span style={{
                width: 12, height: 12, borderRadius: '50%',
                border: '2px solid rgba(168,212,230,0.2)',
                borderTopColor: 'var(--teal)',
                display: 'inline-block', flexShrink: 0,
                animation: 'spin 0.8s linear infinite',
              }} />
            )}
          </>
        )}
      </div>

      {/* Right: actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: compact ? 6 : 10, pointerEvents: 'all' }}>
        <button
          onClick={toggleTheme}
          style={{ ...ghostBtn, padding: compact ? '8px 10px' : '8px 12px' }}
          aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          title={theme === 'dark' ? 'Light mode, for bright rooms' : 'Dark mode'}
        >
          {theme === 'dark' ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <circle cx="12" cy="12" r="4"/>
              <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>
            </svg>
          )}
          {!compact && (theme === 'dark' ? 'Light' : 'Dark')}
        </button>
        {onGridClick && (
          <button onClick={onGridClick} style={compact ? { ...ghostBtn, padding: '8px 10px' } : ghostBtn}
            onMouseEnter={e => { e.currentTarget.style.background = 'var(--wa-08)'; e.currentTarget.style.color = 'var(--text-primary)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'var(--wa-04)'; e.currentTarget.style.color = 'var(--text-secondary)'; }}>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M1 1h12v12H1zM1 5h12M1 9h12M5 1v12" stroke="currentColor" strokeWidth="1.2"/>
            </svg>
            {!compact && 'Claims Grid'}
          </button>
        )}
        <button
          onClick={onUploadClick}
          style={compact ? { ...tealBtn, padding: '8px 12px' } : tealBtn}
          onMouseEnter={e => {
            e.currentTarget.style.background = 'rgba(168,212,230,0.14)';
            e.currentTarget.style.borderColor = 'rgba(168,212,230,0.4)';
          }}
          onMouseLeave={e => {
            e.currentTarget.style.background = 'var(--teal-dim)';
            e.currentTarget.style.borderColor = 'var(--teal-border)';
          }}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M7 1v8M4 4l3-3 3 3M2 11h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          {compact ? 'Upload' : 'Upload Portfolio'}
        </button>
      </div>

    </header>
  );
}

const tealBtn = {
  pointerEvents: 'all', display: 'flex', alignItems: 'center', gap: 7,
  padding: '8px 18px', background: 'var(--teal-dim)',
  border: '1px solid var(--teal-border)', borderRadius: 'var(--r-md)',
  color: 'var(--teal)', fontSize: '0.8rem', fontWeight: 600, letterSpacing: '0.03em',
  cursor: 'pointer', fontFamily: 'var(--font)',
  transition: 'background 0.15s, border-color 0.15s', backdropFilter: 'blur(8px)',
};
const ghostBtn = {
  pointerEvents: 'all', display: 'flex', alignItems: 'center', gap: 7,
  padding: '8px 14px', background: 'var(--wa-04)',
  border: '1px solid var(--wa-10)', borderRadius: 'var(--r-md)',
  color: 'var(--text-secondary)', fontSize: '0.8rem', fontWeight: 600,
  letterSpacing: '0.03em', cursor: 'pointer', fontFamily: 'var(--font)',
  transition: 'background 0.15s, color 0.15s', backdropFilter: 'blur(8px)',
};
