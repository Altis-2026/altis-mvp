/* Header.jsx — Fixed top bar with logo, event label, upload button */
import { useState } from 'react';

/* Altis brand mark — gradient satellite (panels/body/dish), matching the
   official ice-blue-on-black logo. */
const AltisLogo = ({ size = 26 }) => (
  <svg width={size} height={size} viewBox="0 0 64 64">
    <defs>
      <linearGradient id="altisIce" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#DDF1FB"/>
        <stop offset="1" stopColor="#8FC4E8"/>
      </linearGradient>
    </defs>
    <g transform="translate(33,29) rotate(45)">
      <g stroke="#000004" strokeWidth="1.3">
        <rect x="-27" y="-7.5" width="15" height="15" rx="1.5" fill="url(#altisIce)"/>
        <line x1="-22" y1="-7.5" x2="-22" y2="7.5"/>
        <line x1="-17" y1="-7.5" x2="-17" y2="7.5"/>
        <line x1="-27" y1="-2.5" x2="-12" y2="-2.5"/>
        <line x1="-27" y1="2.5"  x2="-12" y2="2.5"/>
        <rect x="12" y="-7.5" width="15" height="15" rx="1.5" fill="url(#altisIce)"/>
        <line x1="17" y1="-7.5" x2="17" y2="7.5"/>
        <line x1="22" y1="-7.5" x2="22" y2="7.5"/>
        <line x1="12" y1="-2.5" x2="27" y2="-2.5"/>
        <line x1="12" y1="2.5"  x2="27" y2="2.5"/>
      </g>
      <line x1="-12" y1="0" x2="-7" y2="0" stroke="url(#altisIce)" strokeWidth="2.4"/>
      <line x1="7"   y1="0" x2="12" y2="0" stroke="url(#altisIce)" strokeWidth="2.4"/>
      <rect x="-7" y="-9" width="14" height="18" rx="3.5" fill="url(#altisIce)"/>
      <rect x="-3.5" y="-13" width="7" height="5" rx="2" fill="url(#altisIce)"/>
      <path d="M -12 12 A 9.5 9.5 0 0 1 7 12 L -12 12 Z"
            transform="rotate(180 -2.5 15)" fill="url(#altisIce)"/>
      <line x1="-2.5" y1="17" x2="-2.5" y2="23.5" stroke="url(#altisIce)" strokeWidth="1.8"/>
      <circle cx="-2.5" cy="24.5" r="2" fill="url(#altisIce)"/>
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
        <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.15 }}>
          <span style={{
            fontSize: '1.05rem', fontWeight: 800, letterSpacing: '0.12em',
            background: 'linear-gradient(135deg, #DDF1FB, #8FC4E8)',
            WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent',
          }}>
            ALTIS
          </span>
          <span style={{ fontSize: '0.56rem', fontWeight: 600, letterSpacing: '0.14em', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
            Real-time satellite ground truth
          </span>
        </div>
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
