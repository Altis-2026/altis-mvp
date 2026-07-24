import { useState, useEffect, useCallback } from 'react';
import { AltisLogo } from './Brand.jsx';

/* Tutorial — a guided walkthrough of the whole product, opened from the
   "Tutorial" button on the globe. Self-contained, theme-aware, keyboard
   navigable (arrows, Enter, Esc). Content is written for a carrier / MGA /
   CAT-adjusting audience: what each part is and why it matters to them. */

const Icon = ({ d, extra }) => (
  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
    {d.map((path, i) => <path key={i} d={path} />)}
    {extra}
  </svg>
);

const STEPS = [
  {
    title: 'Welcome to Altis',
    icon: <Icon d={['M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z', 'M2 12h20', 'M12 2a15 15 0 0 1 0 20', 'M12 2a15 15 0 0 0 0 20']} />,
    body: 'Altis turns satellite radar into a per-property flood decision within hours of an event, before an adjuster ever drives out. Radar sees through cloud and at night, so you are not waiting days for a clear aerial flyover. This tour takes about a minute.',
  },
  {
    title: 'Start with an event',
    icon: <Icon d={['M8 2v3M16 2v3M3 9h18', 'M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z']} />,
    body: 'Open the Events panel on the left. Three real catastrophes are ready to explore with no setup: the Northern Rivers floods in Australia, Hurricane Harvey, and Hurricane Ian. Pick one and the globe flies in, loading every property colored by its triage decision: red to dispatch, amber to review, green to resolve remotely.',
  },
  {
    title: 'Upload your own book',
    icon: <Icon d={['M7 10l5-5 5 5', 'M12 5v12', 'M4 21h16']} />,
    body: 'Use Upload Portfolio, top right. Drop in a CSV, Excel, or PDF policy file with whatever column names you already use. Altis maps the columns for you, shows a review screen to confirm, then geocodes every property onto the map. Addresses resolve worldwide, not just the United States.',
  },
  {
    title: 'Run live satellite analysis',
    icon: <Icon d={['M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z', 'M12 8v4l3 2']} />,
    body: 'In the Analysis panel, set the flood or landfall date and run. Altis pulls real Sentinel-1 radar and Sentinel-2 optical imagery for that exact location and window, anywhere on Earth, and scores every property in a couple of minutes. No GIS staff required.',
  },
  {
    title: 'Inspect any property',
    icon: <Icon d={['M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z', 'M9 22V12h6v10']} />,
    body: 'Click any pin to open the property drawer: an aerial view of the parcel, before-and-after satellite imagery you can drag to compare, flood depth with an uncertainty range, a confidence score with a full factor-by-factor explanation, an estimated claim reserve in dollars, and a one-click claim-file note you can paste straight into your system.',
  },
  {
    title: 'Work the queue, export to your system',
    icon: <Icon d={['M3 6h18M3 12h18M3 18h12']} />,
    body: 'The Dispatch Queue ranks properties by severity and coverage, so the top row is the first field visit worth making. The Claims Grid is the full spreadsheet view: sort, filter, select, and export. Choose an Altis CSV, or a Guidewire ClaimCenter or Duck Creek layout that drops straight into your claims intake.',
  },
  {
    title: 'Prove the value, ask anything',
    icon: <Icon d={['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z']} />,
    body: 'Reports gives you an audit-ready PDF, a catastrophe report, and a live ROI calculator that shows field visits avoided against your own cost per trip. And the assistant bar at the bottom answers anything about your book, an event, or how to use Altis. You can type or tap the microphone and just talk to it.',
  },
];

export default function Tutorial({ open, onClose }) {
  const [i, setI] = useState(0);

  useEffect(() => { if (open) setI(0); }, [open]);

  const next = useCallback(() => setI(v => Math.min(v + 1, STEPS.length - 1)), []);
  const prev = useCallback(() => setI(v => Math.max(v - 1, 0)), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowRight' || e.key === 'Enter') {
        if (i === STEPS.length - 1) onClose(); else next();
      } else if (e.key === 'ArrowLeft') prev();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, i, next, prev, onClose]);

  if (!open) return null;

  const step = STEPS[i];
  const last = i === STEPS.length - 1;

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'rgba(0,0,6,0.62)', backdropFilter: 'blur(8px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
        fontFamily: 'var(--font)',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        className="anim-slide-in-up"
        style={{
          width: 'min(460px, 94vw)', background: 'var(--panel-solid)',
          border: '1px solid var(--wa-10)', borderRadius: 'var(--r-xl)',
          boxShadow: '0 24px 70px rgba(0,0,0,0.5)', overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '16px 20px', borderBottom: '1px solid var(--wa-06)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <AltisLogo size={22} idSuffix="Tut" />
            <span className="wordmark" style={{ fontSize: '0.9rem', fontWeight: 800, letterSpacing: '0.12em' }}>
              ALTIS
            </span>
            <span style={{ fontSize: '0.62rem', fontWeight: 600, letterSpacing: '0.1em', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Guided tour
            </span>
          </div>
          <button onClick={onClose} aria-label="Close tutorial" style={{
            background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer',
            fontSize: '1.1rem', lineHeight: 1, padding: 4,
          }}>✕</button>
        </div>

        {/* Body */}
        <div style={{ padding: '26px 24px 20px' }}>
          <div style={{
            width: 54, height: 54, borderRadius: 14, marginBottom: 18,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'var(--teal-dim)', border: '1px solid var(--teal-border)', color: 'var(--teal)',
          }}>
            {step.icon}
          </div>
          <div style={{ fontSize: '0.66rem', fontWeight: 700, letterSpacing: '0.12em', color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 8 }}>
            Step {i + 1} of {STEPS.length}
          </div>
          <h2 style={{ fontSize: '1.32rem', fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.01em', marginBottom: 12, lineHeight: 1.2 }}>
            {step.title}
          </h2>
          <p style={{ fontSize: '0.9rem', lineHeight: 1.65, color: 'var(--text-body)', margin: 0 }}>
            {step.body}
          </p>
        </div>

        {/* Footer: progress dots + nav */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 20px 18px', gap: 12,
        }}>
          <div style={{ display: 'flex', gap: 6 }}>
            {STEPS.map((_, idx) => (
              <button
                key={idx}
                onClick={() => setI(idx)}
                aria-label={`Go to step ${idx + 1}`}
                style={{
                  width: idx === i ? 20 : 7, height: 7, borderRadius: 999, border: 'none', padding: 0,
                  cursor: 'pointer', transition: 'all 0.25s ease',
                  background: idx === i ? 'var(--teal)' : 'var(--wa-15)',
                }}
              />
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {i > 0 && (
              <button onClick={prev} style={ghost}>Back</button>
            )}
            {!last && (
              <button onClick={onClose} style={ghost}>Skip</button>
            )}
            <button onClick={last ? onClose : next} style={primary}>
              {last ? 'Get started' : 'Next'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const ghost = {
  padding: '8px 14px', borderRadius: 'var(--r-md)', cursor: 'pointer',
  background: 'transparent', border: '1px solid var(--wa-12)',
  color: 'var(--text-secondary)', fontSize: '0.78rem', fontWeight: 600, fontFamily: 'var(--font)',
};
const primary = {
  padding: '8px 20px', borderRadius: 'var(--r-md)', cursor: 'pointer', border: 'none',
  background: 'linear-gradient(135deg, #DDF1FB, #8FC4E8)', color: '#001018',
  fontSize: '0.8rem', fontWeight: 800, fontFamily: 'var(--font)',
};
