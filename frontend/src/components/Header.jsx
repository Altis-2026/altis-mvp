/* Header.jsx — Fixed top bar with logo, event label, upload button */
import { useState } from 'react';

const AltisLogo = () => (
  <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
    <rect x="7" y="6" width="8" height="10" rx="1.5" fill="#A8D4E6"/>
    <rect x="0" y="8" width="6" height="6" rx="0.5" fill="#A8D4E6" opacity="0.65"/>
    <rect x="16" y="8" width="6" height="6" rx="0.5" fill="#A8D4E6" opacity="0.65"/>
    <rect x="1.5" y="9.5" width="1.5" height="1.5" fill="#000010" opacity="0.7"/>
    <rect x="3.5" y="9.5" width="1.5" height="1.5" fill="#000010" opacity="0.7"/>
    <rect x="1.5" y="11.5" width="1.5" height="1.5" fill="#000010" opacity="0.7"/>
    <rect x="3.5" y="11.5" width="1.5" height="1.5" fill="#000010" opacity="0.7"/>
    <rect x="16.5" y="9.5" width="1.5" height="1.5" fill="#000010" opacity="0.7"/>
    <rect x="18.5" y="9.5" width="1.5" height="1.5" fill="#000010" opacity="0.7"/>
    <rect x="16.5" y="11.5" width="1.5" height="1.5" fill="#000010" opacity="0.7"/>
    <rect x="18.5" y="11.5" width="1.5" height="1.5" fill="#000010" opacity="0.7"/>
    <circle cx="5.5" cy="18.5" r="2.5" fill="#A8D4E6" opacity="0.7"/>
    <line x1="7.8" y1="16.2" x2="9" y2="16" stroke="#A8D4E6" strokeWidth="1"/>
  </svg>
);

export default function Header({ selectedEvent, onUploadClick, onGridClick, loading }) {
  return (
    <header style={{
      position:   'fixed',
      top:        0,
      left:       0,
      right:      0,
      zIndex:     'var(--z-header)',
      padding:    '16px 24px 16px 72px',
      display:    'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      background: 'linear-gradient(to bottom, rgba(0,0,6,0.7) 0%, rgba(0,0,6,0) 100%)',
      pointerEvents: 'none',
    }}>

      {/* Left: Logo + event label */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, pointerEvents: 'all' }}>
        <AltisLogo />
        <span style={{ fontSize: '1.05rem', fontWeight: 800, letterSpacing: '0.12em', color: '#fff' }}>
          ALTIS
        </span>
        {selectedEvent && (
          <>
            <span style={{ color: 'rgba(255,255,255,0.15)', fontSize: '0.9rem' }}>|</span>
            <span style={{ fontSize: '0.82rem', fontWeight: 500, color: 'var(--text-secondary)', letterSpacing: '0.01em' }}>
              {selectedEvent.label}
            </span>
            {loading && (
              <span style={{
                width: 12, height: 12, borderRadius: '50%',
                border: '2px solid rgba(168,212,230,0.2)',
                borderTopColor: '#A8D4E6',
                display: 'inline-block',
                animation: 'spin 0.8s linear infinite',
              }} />
            )}
          </>
        )}
      </div>

      {/* Right: actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, pointerEvents: 'all' }}>
        {onGridClick && (
          <button onClick={onGridClick} style={ghostBtn}
            onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.08)'; e.currentTarget.style.color = '#fff'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.04)'; e.currentTarget.style.color = 'var(--text-secondary)'; }}>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M1 1h12v12H1zM1 5h12M1 9h12M5 1v12" stroke="currentColor" strokeWidth="1.2"/>
            </svg>
            Claims Grid
          </button>
        )}
        <button
          onClick={onUploadClick}
          style={tealBtn}
          onMouseEnter={e => {
            e.currentTarget.style.background = 'rgba(168,212,230,0.14)';
            e.currentTarget.style.borderColor = 'rgba(168,212,230,0.4)';
          }}
          onMouseLeave={e => {
            e.currentTarget.style.background = 'rgba(168,212,230,0.08)';
            e.currentTarget.style.borderColor = 'rgba(168,212,230,0.22)';
          }}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M7 1v8M4 4l3-3 3 3M2 11h10" stroke="#A8D4E6" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          Upload Portfolio
        </button>
      </div>

    </header>
  );
}

const tealBtn = {
  pointerEvents: 'all', display: 'flex', alignItems: 'center', gap: 7,
  padding: '8px 18px', background: 'rgba(168,212,230,0.08)',
  border: '1px solid rgba(168,212,230,0.22)', borderRadius: 'var(--r-md)',
  color: '#A8D4E6', fontSize: '0.8rem', fontWeight: 600, letterSpacing: '0.03em',
  cursor: 'pointer', fontFamily: 'var(--font)',
  transition: 'background 0.15s, border-color 0.15s', backdropFilter: 'blur(8px)',
};
const ghostBtn = {
  pointerEvents: 'all', display: 'flex', alignItems: 'center', gap: 7,
  padding: '8px 14px', background: 'rgba(255,255,255,0.04)',
  border: '1px solid rgba(255,255,255,0.1)', borderRadius: 'var(--r-md)',
  color: 'var(--text-secondary)', fontSize: '0.8rem', fontWeight: 600,
  letterSpacing: '0.03em', cursor: 'pointer', fontFamily: 'var(--font)',
  transition: 'background 0.15s, color 0.15s', backdropFilter: 'blur(8px)',
};
