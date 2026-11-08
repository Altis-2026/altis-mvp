import { useState, useEffect, useLayoutEffect, useRef, useCallback } from 'react';
import { AltisLogo } from './Brand.jsx';

/* Tutorial — an interactive, spotlight guided tour.

   Each step drives the real app to the right state (opens a sidebar panel,
   loads a demo event, opens a property) through the `ctl` object App passes
   in, then spotlights the element being explained (found by its
   `data-tour="…"` attribute) with a card placed beside it. Targets that
   appear asynchronously (the KPI bar after an event loads, the property
   drawer) are polled for; if one never appears the card simply centres.

   Keyboard: → / Enter next, ← back, Esc closes. */

const STEPS = [
  {
    id: 'welcome',
    title: 'Welcome to Altis',
    body: 'Altis turns satellite radar into a per-property flood decision within hours of an event, before an adjuster drives out. Radar sees through cloud and at night. This tour clicks through the real app for you, about a minute.',
    enter: (c) => { c.closeProperty(); c.setPanel(null); },
  },
  {
    id: 'rail',
    target: 'rail',
    placement: 'right',
    title: 'Your workspace',
    body: 'Every tool lives on this rail: Events, Portfolios, Dispatch Queue, Analysis, SAR Compare, Reports and Operations. Click an icon to open its panel, click it again to close.',
    enter: (c) => { c.closeProperty(); c.setPanel(null); },
  },
  {
    id: 'events',
    target: 'panel',
    placement: 'right',
    title: '1 · Pick an event',
    body: (c) => `Real catastrophes are ready with no setup. Click one and the globe flies in and loads every insured property. ${c.eventLabel ? `${c.eventLabel} is open now.` : 'We are opening Hurricane Harvey for you.'}`,
    enter: (c) => { c.closeProperty(); c.setPanel('events'); c.ensureEvent(); },
  },
  {
    id: 'kpis',
    target: 'kpis',
    placement: 'bottom',
    wait: true,
    title: '2 · Read the scoreboard',
    body: 'The headline numbers for the event: properties analysed, how many need a field visit, how many can be resolved remotely, and the field cost that avoids.',
    enter: (c) => { c.closeProperty(); c.setPanel(null); c.ensureEvent(); },
  },
  {
    id: 'map',
    target: 'map-controls',
    placement: 'left',
    title: '3 · Explore the map',
    body: 'Pins are coloured by decision: red dispatch, amber review, green resolve remotely. Clusters show counts; zoom in to split them. These toggles add the storm track, FEMA flood zones, a 3D building view, and a dollar-weighted exposure heatmap.',
    enter: (c) => { c.closeProperty(); c.setPanel(null); c.ensureEvent(); },
  },
  {
    id: 'timeslider',
    target: 'timeslider',
    placement: 'top',
    wait: true,
    title: '4 · Before and after',
    body: 'Flip the satellite layer between pre-event and post-event imagery to see where the water actually was.',
    enter: (c) => { c.closeProperty(); c.setPanel(null); c.ensureEvent(); },
  },
  {
    id: 'dispatch',
    target: 'panel',
    placement: 'right',
    title: '5 · Work the dispatch queue',
    body: 'Properties ranked by severity × coverage, so the top row is the first field visit worth making. A separate lane lists possible missed claims, where river gauges show water the radar did not. Click any row to open that property.',
    enter: (c) => { c.closeProperty(); c.setPanel('dispatch'); c.ensureEvent(); },
  },
  {
    id: 'drawer',
    target: 'drawer',
    placement: 'left',
    wait: true,
    title: '6 · Inspect a property',
    body: 'Everything about one property: the triage decision and its confidence, aerial view, before/after radar you can drag to compare, flood depth with an uncertainty range, a river-gauge estimate that can catch water the radar missed, an estimated reserve, and a one-click claim-file note. Scroll the drawer to see it all; Esc closes it.',
    enter: (c) => { c.setPanel(null); c.ensureEvent(); c.openTopProperty(); },
  },
  {
    id: 'analysis',
    target: 'panel',
    placement: 'right',
    title: '7 · Run live satellite analysis',
    body: 'For your own book: set the flood or landfall date and run. Altis pulls Sentinel-1 radar and Sentinel-2 optical imagery for that place and window, anywhere on Earth, and scores every property in minutes.',
    enter: (c) => { c.closeProperty(); c.setPanel('analysis'); },
  },
  {
    id: 'upload',
    target: 'upload-btn',
    placement: 'bottom',
    title: '8 · Upload your portfolio',
    body: 'Drop in a CSV, Excel or PDF policy file with whatever column names you already use. Altis maps the columns, lets you confirm them, and geocodes every property onto the map.',
    enter: (c) => { c.closeProperty(); c.setPanel(null); },
  },
  {
    id: 'grid',
    target: 'grid-btn',
    placement: 'bottom',
    wait: true,
    title: '9 · Claims grid and export',
    body: 'The full spreadsheet view: sort, filter, select, and export as an Altis CSV or in Guidewire ClaimCenter or Duck Creek layouts that drop straight into claims intake.',
    enter: (c) => { c.closeProperty(); c.setPanel(null); c.ensureEvent(); },
  },
  {
    id: 'reports',
    target: 'panel',
    placement: 'right',
    title: '10 · Reports and ROI',
    body: 'Download an audit-ready PDF for the event or a catastrophe report for your portfolio, check the ROI calculator against your own cost per field trip, see similar past floods and what their insured claims paid, and review validation results.',
    enter: (c) => { c.closeProperty(); c.setPanel('reports'); },
  },
  {
    id: 'chat',
    target: 'chat',
    placement: 'top',
    wait: true,
    title: '11 · Ask anything',
    body: 'The assistant answers questions about the event, your book, or a selected property, and how to use Altis. Type, or tap the microphone and talk.',
    enter: (c) => { c.closeProperty(); c.setPanel(null); },
  },
  {
    id: 'done',
    target: 'tutorial-btn',
    placement: 'top',
    title: "You're all set",
    body: (c) => `${c.eventLabel || 'A demo event'} stays loaded so you can keep exploring. Replay this tour any time from the Tutorial button.`,
    enter: (c) => { c.closeProperty(); c.setPanel(null); },
  },
];

const PAD = 8;          // spotlight padding around the target
const GAP = 14;         // gap between spotlight and card
const MARGIN = 12;      // keep the card this far from the viewport edge
const WAIT_MS = 8000;   // how long to wait for an async target

function findTarget(name) {
  if (!name) return null;
  const els = document.querySelectorAll(`[data-tour="${name}"]`);
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) return el;
  }
  return null;
}

function sameRect(a, b) {
  if (!a || !b) return a === b;
  return Math.abs(a.top - b.top) < 0.5 && Math.abs(a.left - b.left) < 0.5
    && Math.abs(a.width - b.width) < 0.5 && Math.abs(a.height - b.height) < 0.5;
}

/* Position the card next to the spotlight, trying the preferred side first,
   then the opposite, then the remaining sides; clamp into the viewport. */
function placeCard(rect, placement, cw, ch, vw, vh) {
  if (!rect) return { left: (vw - cw) / 2, top: (vh - ch) / 2 };
  const s = { top: rect.top - PAD, left: rect.left - PAD,
              right: rect.left + rect.width + PAD, bottom: rect.top + rect.height + PAD };
  const fits = {
    right:  s.right + GAP + cw <= vw - MARGIN,
    left:   s.left - GAP - cw >= MARGIN,
    bottom: s.bottom + GAP + ch <= vh - MARGIN,
    top:    s.top - GAP - ch >= MARGIN,
  };
  const opposite = { right: 'left', left: 'right', top: 'bottom', bottom: 'top' };
  const order = [placement, opposite[placement], 'bottom', 'top', 'right', 'left'];
  const side = order.find(p => fits[p]);
  const cx = rect.left + rect.width / 2 - cw / 2;
  const cy = rect.top + rect.height / 2 - ch / 2;
  let left, top;
  if (side === 'right')       { left = s.right + GAP;     top = cy; }
  else if (side === 'left')   { left = s.left - GAP - cw; top = cy; }
  else if (side === 'bottom') { left = cx; top = s.bottom + GAP; }
  else if (side === 'top')    { left = cx; top = s.top - GAP - ch; }
  else {
    // Target fills the screen (e.g. a full-width drawer on a phone): overlay
    // the card at whichever end of the viewport the target leaves more room.
    left = (vw - cw) / 2;
    top = (rect.top + rect.height / 2) > vh / 2 ? MARGIN + 56 : vh - ch - MARGIN;
  }
  left = Math.max(MARGIN, Math.min(left, vw - cw - MARGIN));
  top  = Math.max(MARGIN, Math.min(top,  vh - ch - MARGIN));
  return { left, top };
}

export default function Tutorial({ open, onClose, ctl }) {
  const [i, setI] = useState(0);
  const [rect, setRect] = useState(null);
  const [waiting, setWaiting] = useState(false);
  const [vp, setVp] = useState({ w: window.innerWidth, h: window.innerHeight });
  const [cardSize, setCardSize] = useState({ w: 380, h: 240 });
  const cardRef = useRef(null);
  const ctlRef = useRef(ctl);
  ctlRef.current = ctl;

  const step = STEPS[i];
  const last = i === STEPS.length - 1;

  useEffect(() => { if (open) setI(0); }, [open]);

  // Drive the app into this step's state.
  useEffect(() => {
    if (!open) return;
    try { step.enter?.(ctlRef.current); } catch (e) { console.warn('tour step', e); }
  }, [open, i]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Track the target's rectangle (it may slide in, load late, or move).
  useEffect(() => {
    if (!open) return undefined;
    setRect(null);
    if (!step.target) { setWaiting(false); return undefined; }
    setWaiting(true);
    const started = Date.now();
    let alive = true;
    const tick = () => {
      if (!alive) return;
      const el = findTarget(step.target);
      if (el) {
        const r = el.getBoundingClientRect();
        const next = { top: r.top, left: r.left, width: r.width, height: r.height };
        setRect(prev => (sameRect(prev, next) ? prev : next));
        setWaiting(false);
      } else if (Date.now() - started > WAIT_MS) {
        setRect(null);
        setWaiting(false);
      }
    };
    // Give the step's state change a moment to render before the first look.
    const first = setTimeout(tick, 60);
    const id = setInterval(tick, 150);
    return () => { alive = false; clearTimeout(first); clearInterval(id); };
  }, [open, i, step.target]);

  useEffect(() => {
    if (!open) return undefined;
    const onResize = () => setVp({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !cardRef.current) return;
    const r = cardRef.current.getBoundingClientRect();
    if (Math.abs(r.width - cardSize.w) > 1 || Math.abs(r.height - cardSize.h) > 1) {
      setCardSize({ w: r.width, h: r.height });
    }
  });

  const next = useCallback(() => setI(v => Math.min(v + 1, STEPS.length - 1)), []);
  const prev = useCallback(() => setI(v => Math.max(v - 1, 0)), []);

  // Capture phase so Esc/arrows drive the tour and don't also close the
  // property drawer or a modal underneath.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      const k = e.key;
      if (!['Escape', 'ArrowRight', 'ArrowLeft', 'Enter'].includes(k)) return;
      e.preventDefault();
      e.stopPropagation();
      if (k === 'Escape') onClose();
      else if (k === 'ArrowRight' || k === 'Enter') { if (last) onClose(); else next(); }
      else if (k === 'ArrowLeft') prev();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [open, last, next, prev, onClose]);

  if (!open) return null;

  const mobile = vp.w < 640;
  const cardW = Math.min(mobile ? vp.w - 2 * MARGIN : 380, vp.w - 2 * MARGIN);
  const pos = mobile
    ? (() => {
        // Phone: dock to the bottom, or the top if the target lives down there.
        const lowTarget = rect && rect.top + rect.height / 2 > vp.h * 0.55;
        return lowTarget ? { left: MARGIN, top: MARGIN + 52 }
                         : { left: MARGIN, top: vp.h - cardSize.h - MARGIN };
      })()
    : placeCard(rect, step.placement || 'bottom', cardW, cardSize.h, vp.w, vp.h);

  const spot = rect && {
    top: Math.max(rect.top - PAD, 2),
    left: Math.max(rect.left - PAD, 2),
    width: Math.min(rect.width + 2 * PAD, vp.w - Math.max(rect.left - PAD, 2) - 2),
    height: Math.min(rect.height + 2 * PAD, vp.h - Math.max(rect.top - PAD, 2) - 2),
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Altis guided tour, step ${i + 1} of ${STEPS.length}: ${step.title}`}
      style={{ position: 'fixed', inset: 0, zIndex: 1000, fontFamily: 'var(--font)' }}
    >
      <style>{`
        @keyframes altisTourPulse {
          0%   { box-shadow: 0 0 0 0 rgba(143,196,232,0.55); }
          70%  { box-shadow: 0 0 0 12px rgba(143,196,232,0); }
          100% { box-shadow: 0 0 0 0 rgba(143,196,232,0); }
        }
      `}</style>

      {/* Dim layer. With a target, the spotlight's giant box-shadow does the
          dimming and leaves a hole; without one, a plain scrim. Clicks on the
          dimmed area are swallowed so the tour can't be broken mid-step. */}
      {spot ? (
        <>
          <div style={{ position: 'fixed', inset: 0 }} />
          <div style={{
            position: 'fixed', ...spot, borderRadius: 12, pointerEvents: 'none',
            boxShadow: '0 0 0 9999px rgba(0,2,10,0.62)',
            transition: 'top 0.35s ease, left 0.35s ease, width 0.35s ease, height 0.35s ease',
          }} />
          <div style={{
            position: 'fixed', ...spot, borderRadius: 12, pointerEvents: 'none',
            border: '2px solid var(--teal)', animation: 'altisTourPulse 1.8s ease-out infinite',
            transition: 'top 0.35s ease, left 0.35s ease, width 0.35s ease, height 0.35s ease',
          }} />
        </>
      ) : (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,2,10,0.62)',
                      backdropFilter: step.target ? 'none' : 'blur(6px)' }} />
      )}

      {/* Card */}
      <div
        ref={cardRef}
        key={step.id}
        className="anim-fade-in"
        style={{
          position: 'fixed', left: pos.left, top: pos.top, width: cardW,
          background: 'var(--panel-solid)', border: '1px solid var(--wa-10)',
          borderRadius: 'var(--r-xl)', boxShadow: '0 24px 70px rgba(0,0,0,0.5)',
          overflow: 'hidden', transition: 'left 0.3s ease, top 0.3s ease',
        }}
      >
        {/* Progress bar */}
        <div style={{ height: 3, background: 'var(--wa-06)' }}>
          <div style={{ height: '100%', width: `${((i + 1) / STEPS.length) * 100}%`,
                        background: 'var(--teal)', transition: 'width 0.3s ease' }} />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      padding: '12px 16px 0' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <AltisLogo size={18} idSuffix="Tut" />
            <span style={{ fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.12em',
                           color: 'var(--teal)', textTransform: 'uppercase' }}>
              Guided tour · {i + 1} / {STEPS.length}
            </span>
          </div>
          <button onClick={onClose} aria-label="Close tour" title="Close (Esc)" style={{
            background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer',
            fontSize: '1rem', lineHeight: 1, padding: 4,
          }}>✕</button>
        </div>

        <div style={{ padding: '10px 16px 4px' }}>
          <h2 style={{ fontSize: '1.08rem', fontWeight: 800, color: 'var(--text-primary)',
                       letterSpacing: '-0.01em', margin: '0 0 8px', lineHeight: 1.25 }}>
            {step.title}
          </h2>
          <p style={{ fontSize: '0.82rem', lineHeight: 1.6, color: 'var(--text-body)', margin: 0 }}>
            {typeof step.body === 'function' ? step.body(ctl || {}) : step.body}
          </p>
          {waiting && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginTop: 10,
                          fontSize: '0.7rem', color: 'var(--text-muted)' }}>
              <span style={{
                width: 10, height: 10, borderRadius: '50%', display: 'inline-block',
                border: '2px solid var(--wa-15)', borderTopColor: 'var(--teal)',
                animation: 'spin 0.8s linear infinite',
              }} />
              Loading…
            </div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      padding: '12px 16px 14px', gap: 10 }}>
          {!last ? (
            <button onClick={onClose} style={linkBtn}>Skip tour</button>
          ) : <span />}
          <div style={{ display: 'flex', gap: 8 }}>
            {i > 0 && <button onClick={prev} style={ghost}>Back</button>}
            <button onClick={last ? onClose : next} style={primary} autoFocus>
              {last ? 'Finish' : 'Next'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const linkBtn = {
  background: 'none', border: 'none', padding: '6px 2px', cursor: 'pointer',
  color: 'var(--text-muted)', fontSize: '0.74rem', fontWeight: 600, fontFamily: 'var(--font)',
};
const ghost = {
  padding: '7px 14px', borderRadius: 'var(--r-md)', cursor: 'pointer',
  background: 'transparent', border: '1px solid var(--wa-12)',
  color: 'var(--text-secondary)', fontSize: '0.78rem', fontWeight: 600, fontFamily: 'var(--font)',
};
const primary = {
  padding: '7px 18px', borderRadius: 'var(--r-md)', cursor: 'pointer', border: 'none',
  background: 'linear-gradient(135deg, #DDF1FB, #8FC4E8)', color: '#001018',
  fontSize: '0.8rem', fontWeight: 800, fontFamily: 'var(--font)',
};
