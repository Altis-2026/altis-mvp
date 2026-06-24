/* Header.jsx — Fixed top bar with logo, event label, upload button */
import { useState } from 'react';

/* Altis brand mark — satellite with solar panels + dish, teal-on-black. */
const AltisLogo = () => (
  <svg width="24" height="24" viewBox="0 0 64 64">
    <g transform="translate(32,30) rotate(-35)">
      <rect x="-26" y="-7" width="14" height="14" rx="1.2" fill="#A8D4E6" opacity="0.55"/>
      <rect x="12"  y="-7" width="14" height="14" rx="1.2" fill="#A8D4E6" opacity="0.55"/>
      <line x1="-12" y1="0" x2="-7" y2="0" stroke="#A8D4E6" strokeWidth="2"/>
      <line x1="7"   y1="0" x2="12" y2="0" stroke="#A8D4E6" strokeWidth="2"/>
      <g opacity="0.85">
        <line x1="-22" y1="-4" x2="-18" y2="-4" stroke="#000004" strokeWidth="1.4"/>
        <line x1="-22" y1="0"  x2="-18" y2="0"  stroke="#000004" strokeWidth="1.4"/>
        <line x1="-22" y1="4"  x2="-18" y2="4"  stroke="#000004" strokeWidth="1.4"/>
        <line x1="18" y1="-4" x2="22" y2="-4" stroke="#000004" strokeWidth="1.4"/>
        <line x1="18" y1="0"  x2="22" y2="0"  stroke="#000004" strokeWidth="1.4"/>
        <line x1="18" y1="4"  x2="22" y2="4"  stroke="#000004" strokeWidth="1.4"/>
      </g>
      <rect x="-7" y="-8" width="14" height="16" rx="2" fill="#A8D4E6"/>
      <circle cx="0" cy="-15" r="6" fill="none" stroke="#A8D4E6" strokeWidth="2"/>
      <line x1="0" y1="-9" x2="0" y2="-15" stroke="#A8D4E6" strokeWidth="1.6"/>
    </g>
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
