/* DemoMode.jsx — a scripted ~25-second run through Altis, for screen recording.
 *
 * It drives the REAL map, the REAL baked analysis and the REAL routed flood —
 * there is no mock layer here. A recording is therefore a recording of the
 * product, and anything it claims on screen can be clicked through afterwards.
 *
 * The arc is a zoom through scales: orbit → radar → properties → the flood in
 * motion (the part the satellite never saw) → one house → the forecast.
 *
 *   Shift+D   start / stop
 *   Space     pause / resume
 *   R         restart
 *   Esc       exit
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../services/api.js';
import { FloodPainter } from '../utils/floodReplay.js';

/* The scripted run is written against this event (its copy quotes real
   figures from it), and it is the one with a calibrated flood replay. */
export const DEMO_EVENT = 'lismore';

const LISMORE = { center: [153.285, -28.86], zoom: 9.6 };
const HOUSE = { center: [153.2786, -28.8105], zoom: 17.4 };

/* Beats are absolute ms offsets; `t` inside a beat is its own 0→1 progress. */
const BEATS = [
  { id: 'open',    at: 0,     dur: 2600 },
  { id: 'radar',   at: 2600,  dur: 2800 },
  { id: 'pins',    at: 5400,  dur: 2600 },
  { id: 'flood',   at: 8000,  dur: 8600 },   // the hero
  { id: 'house',   at: 16600, dur: 3800 },
  { id: 'predict', at: 20400, dur: 2600 },
  { id: 'card',    at: 23000, dur: 2000 },
];
const TOTAL = BEATS[BEATS.length - 1].at + BEATS[BEATS.length - 1].dur;

const beatAt = (ms) => {
  for (let i = BEATS.length - 1; i >= 0; i--) if (ms >= BEATS[i].at) return BEATS[i];
  return BEATS[0];
};
const clamp01 = (v) => Math.max(0, Math.min(1, v));
const easeInOut = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);

export default function DemoMode({ open, onClose, globeRef, eventId, properties,
                                   onSetPinColors, onSetInspect3D, onFloodState }) {
  const [painter, setPainter] = useState(null);
  const [meta, setMeta] = useState(null);
  const [loadErr, setLoadErr] = useState('');
  const [ms, setMs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [chromeVisible, setChromeVisible] = useState(true);
  const raf = useRef(null);
  const started = useRef(0);
  const offset = useRef(0);
  const doneBeats = useRef(new Set());
  const hideTimer = useRef(null);

  /* ── Load the routed flood once ─────────────────────────────── */
  useEffect(() => {
    if (!open || painter || !eventId) return;
    let alive = true;
    (async () => {
      try {
        const m = await api.getFloodAnimation(eventId);
        const buf = await api.getFloodAnimationFrames(eventId);
        if (!alive) return;
        setMeta(m);
        setPainter(new FloodPainter(m, buf));
      } catch (e) {
        if (alive) setLoadErr(e?.detail || e?.message || 'No flood replay for this event.');
      }
    })();
    return () => { alive = false; };
  }, [open, eventId, painter]);

  /* First frame with water worth showing — so the hero beat opens on a flood
     starting, not on an empty map. Measured with the painter's own display
     threshold, because that is what the viewer will actually see. */
  const floodStart = useMemo(() => {
    if (!painter || !meta) return 0;
    const peak = painter.wetFraction(meta.peak_index ?? 0) || 1;
    const target = Math.max(0.01, peak * 0.10);
    for (let i = 0; i < painter.frames; i++) {
      if (painter.wetFraction(i) >= target) return Math.max(0, i - 4);
    }
    return 0;
  }, [painter, meta]);

  const frames = meta?.shape?.[0] ?? 1;
  const passIdx = meta?.pass_index ?? 0;
  const peakIdx = meta?.peak_index ?? 0;

  /* Frame for the current hero progress.
   *
   * Not linear on purpose: the flood builds, the crest HOLDS for a beat so the
   * peak registers, then the recession runs on to the satellite pass — which
   * is the punchline and needs room to land, not the last half-second. */
  const heroFrame = useCallback((t) => {
    const last = frames - 1;
    const x = clamp01(t);
    const BUILD = 0.42, HOLD = 0.54;          // t breakpoints
    if (x < BUILD) return floodStart + easeInOut(x / BUILD) * (peakIdx - floodStart);
    if (x < HOLD) return peakIdx;
    return peakIdx + easeInOut((x - HOLD) / (1 - HOLD)) * (last - peakIdx);
  }, [frames, floodStart, peakIdx]);

  const frame = useMemo(() => {
    const b = beatAt(ms);
    const t = clamp01((ms - b.at) / b.dur);
    if (b.id === 'flood') return heroFrame(t);
    if (b.id === 'open') return passIdx;
    return b.id === 'radar' || b.id === 'pins' ? passIdx : (frames - 1);
  }, [ms, heroFrame, passIdx, frames]);

  const floodOpacity = useMemo(() => {
    const b = beatAt(ms);
    const t = clamp01((ms - b.at) / b.dur);
    switch (b.id) {
      case 'open':    return 0;
      case 'radar':   return 0.9 * clamp01(t * 2.2);
      case 'pins':    return 0.72;
      case 'flood':   return 0.92;
      case 'house':   return 0.55 * (1 - clamp01(t * 1.6));
      default:        return 0;
    }
  }, [ms]);

  /* ── Beat entry actions (camera, layers) — fire once each ────── */
  const enterBeat = useCallback((id) => {
    const g = globeRef.current;
    if (!g) return;
    switch (id) {
      case 'open':
        onSetInspect3D?.(false);
        onSetPinColors?.(null);
        g.jumpTo({ center: [153.9, -26.2], zoom: 2.6, pitch: 0, bearing: 0 });
        g.flyTo({ ...LISMORE, pitch: 30, bearing: -12, duration: 2600, curve: 1.5 });
        break;
      case 'radar':
        g.easeTo({ ...LISMORE, zoom: 10.1, pitch: 38, bearing: -6, duration: 2800 });
        break;
      case 'pins':
        g.easeTo({ center: [153.29, -28.88], zoom: 10.4, pitch: 42, bearing: 2, duration: 2600 });
        break;
      case 'flood':
        g.easeTo({ center: [153.30, -28.90], zoom: 10.2, pitch: 46, bearing: 14, duration: 8600 });
        break;
      case 'house':
        onSetInspect3D?.(true);
        g.flyTo({ ...HOUSE, pitch: 66, bearing: 28, duration: 3400, curve: 1.6 });
        break;
      case 'predict':
        onSetInspect3D?.(false);
        onSetPinColors?.('susceptibility');
        g.flyTo({ center: [153.29, -28.89], zoom: 10.3, pitch: 34, bearing: -8, duration: 2600 });
        break;
      case 'card':
        g.easeTo({ zoom: 10.0, pitch: 20, bearing: -16, duration: 2600 });
        break;
      default: break;
    }
  }, [globeRef, onSetInspect3D, onSetPinColors]);

  /* ── Transport ───────────────────────────────────────────────── */
  const tick = useCallback(() => {
    const now = performance.now();
    const t = Math.min(TOTAL, offset.current + (now - started.current));
    setMs(t);
    const b = beatAt(t);
    if (!doneBeats.current.has(b.id)) {
      doneBeats.current.add(b.id);
      enterBeat(b.id);
    }
    if (t >= TOTAL) { setPlaying(false); return; }
    raf.current = requestAnimationFrame(tick);
  }, [enterBeat]);

  const play = useCallback(() => {
    started.current = performance.now();
    setPlaying(true);
    raf.current = requestAnimationFrame(tick);
  }, [tick]);

  const pause = useCallback(() => {
    cancelAnimationFrame(raf.current);
    offset.current = ms;
    setPlaying(false);
  }, [ms]);

  const restart = useCallback(() => {
    cancelAnimationFrame(raf.current);
    offset.current = 0;
    doneBeats.current = new Set();
    setMs(0);
    started.current = performance.now();
    setPlaying(true);
    raf.current = requestAnimationFrame(tick);
  }, [tick]);

  /* Autostart once the flood is loaded. */
  useEffect(() => {
    if (open && painter && !playing && ms === 0 && offset.current === 0) {
      const id = setTimeout(restart, 350);
      return () => clearTimeout(id);
    }
  }, [open, painter, playing, ms, restart]);

  useEffect(() => () => cancelAnimationFrame(raf.current), []);

  /* Hand the flood layer's state to the Globe (via App). */
  useEffect(() => {
    onFloodState?.(open && painter && meta
      ? { painter, bounds: meta.bounds, frame, opacity: floodOpacity }
      : null);
  }, [open, painter, meta, frame, floodOpacity, onFloodState]);
  useEffect(() => () => onFloodState?.(null), [onFloodState]);

  /* Leaving demo mode restores the app to a normal state. */
  useEffect(() => {
    if (open) return;
    cancelAnimationFrame(raf.current);
    offset.current = 0;
    doneBeats.current = new Set();
    setMs(0);
    setPlaying(false);
    onSetPinColors?.(null);
    onSetInspect3D?.(null);
  }, [open, onSetPinColors, onSetInspect3D]);

  /* Keys + auto-hiding controls, so a recording stays clean. */
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === 'Escape') { onClose?.(); }
      else if (e.code === 'Space') { e.preventDefault(); playing ? pause() : play(); }
      else if (e.key === 'r' || e.key === 'R') { restart(); }
    };
    const wake = () => {
      setChromeVisible(true);
      clearTimeout(hideTimer.current);
      hideTimer.current = setTimeout(() => setChromeVisible(false), 1800);
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('mousemove', wake);
    wake();
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousemove', wake);
      clearTimeout(hideTimer.current);
    };
  }, [open, playing, pause, play, restart, onClose]);


  if (!open) return null;

  const b = beatAt(ms);
  const bt = clamp01((ms - b.at) / b.dur);
  const simTime = meta?.times?.[Math.round(frame)] || null;
  // Only meaningful once the replay is loaded; without it every comparison
  // against frame 0 would be trivially true.
  const ready = !!(painter && meta);
  const crossedPeak = ready && b.id === 'flood' && frame >= peakIdx;
  const crossedPass = ready && b.id === 'flood' && frame >= passIdx;
  // Measured off the painter, not the bake, so the readout matches exactly
  // what is drawn (the painter hides water below its display threshold).
  const wetNow = painter ? painter.wetFraction(frame) : 0;
  const wetPeak = painter ? painter.wetFraction(peakIdx) : 1;

  return (
    <>
      <DemoStage
        beat={b.id} t={bt} meta={meta} simTime={simTime}
        crossedPeak={crossedPeak} crossedPass={crossedPass} ready={ready}
        wetNow={wetNow} wetPeak={wetPeak}
        propertyCount={properties?.length || 0}
        loading={!painter && !loadErr} loadErr={loadErr}
      />
      <DemoControls
        visible={chromeVisible} playing={playing} ms={ms} total={TOTAL}
        onPlay={play} onPause={pause} onRestart={restart} onClose={onClose}
        onScrub={(v) => {
          cancelAnimationFrame(raf.current);
          const t = v * TOTAL;
          offset.current = t;
          started.current = performance.now();
          setMs(t);
          doneBeats.current = new Set([beatAt(t).id]);
          enterBeat(beatAt(t).id);
          if (playing) raf.current = requestAnimationFrame(tick);
        }}
      />
    </>
  );
}

/* The visible film: title cards, the simulated clock, the satellite marker. */
export function DemoStage({ beat, t, meta, simTime, crossedPeak, crossedPass, ready,
                     wetNow, wetPeak, propertyCount, loading, loadErr }) {
  const fade = (inAt = 0.08, outAt = 0.88) =>
    clamp01(t / inAt) * (1 - clamp01((t - outAt) / (1 - outAt)));

  const wrap = {
    position: 'fixed', inset: 0, zIndex: 40, pointerEvents: 'none',
    fontFamily: 'var(--font)', color: '#fff',
  };
  const lower = {
    position: 'absolute', left: 56, bottom: 92, maxWidth: '60vw',
  };
  const kicker = {
    fontSize: '0.66rem', letterSpacing: '0.34em', textTransform: 'uppercase',
    color: 'rgba(168,212,230,0.92)', fontWeight: 700, marginBottom: 10,
  };
  const head = {
    fontSize: 'clamp(1.7rem, 3.5vw, 3rem)', fontWeight: 800, letterSpacing: '-0.02em',
    lineHeight: 1.06, textShadow: '0 4px 28px rgba(0,0,0,0.75)',
  };
  const sub = {
    marginTop: 12, fontSize: '0.92rem', color: 'rgba(255,255,255,0.82)', lineHeight: 1.5,
    textShadow: '0 2px 14px rgba(0,0,0,0.8)', maxWidth: 560,
  };

  return (
    <div style={wrap}>
      {/* Cinematic letterboxing */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 62,
                    background: 'linear-gradient(180deg, rgba(0,0,0,0.72), transparent)' }} />
      <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 170,
                    background: 'linear-gradient(0deg, rgba(0,0,0,0.78), transparent)' }} />

      {(loading || loadErr) && (
        <div style={{ position: 'absolute', left: 56, top: 78, maxWidth: 420 }}>
          <div style={{ ...kicker, color: loadErr ? '#FFB347' : undefined, marginBottom: 4 }}>
            {loadErr ? 'Flood replay unavailable' : 'Loading the routed flood…'}
          </div>
          {loadErr && <div style={{ ...sub, marginTop: 4, fontSize: '0.8rem' }}>{loadErr}</div>}
        </div>
      )}

      {beat === 'open' && (
        <div style={{ ...lower, opacity: fade(0.14, 0.72) }}>
          <div style={{ ...head, fontSize: 'clamp(2.4rem, 6vw, 5rem)', letterSpacing: '0.16em' }}>ALTIS</div>
          <div style={sub}>Flood damage, resolved from orbit.</div>
        </div>
      )}

      {beat === 'radar' && (
        <div style={{ ...lower, opacity: fade() }}>
          <div style={kicker}>Sentinel-1 · C-band radar · 10 m</div>
          <div style={head}>The satellite looked<br />once.</div>
          <div style={sub}>2 March 2022, 19:06 UTC. Radar sees through cloud and at night —
            but only when it passes overhead.</div>
        </div>
      )}

      {beat === 'pins' && (
        <div style={{ ...lower, opacity: fade() }}>
          <div style={kicker}>Automated triage</div>
          <div style={head}>{propertyCount ? propertyCount.toLocaleString() : '800'} properties,<br />classified in one pass.</div>
          <div style={sub}>Dispatch an adjuster, review, or settle remotely — each with the
            evidence attached.</div>
        </div>
      )}

      {beat === 'flood' && (
        <>
          <div style={{ ...lower, opacity: clamp01(t * 6) * (1 - clamp01((t - 0.82) / 0.18)) }}>
            <div style={kicker}>Synthetic revisit · physics reconstruction</div>
            <div style={head}>
              {crossedPass ? 'Two and a half days late.'
                : crossedPeak ? 'This is the peak.'
                : 'So we rebuilt the rest.'}
            </div>
            <div style={sub}>
              {crossedPass
                ? 'By the time the satellite looked, the water had already dropped. Everything before this moment is reconstructed, not observed.'
                : crossedPeak
                ? 'The flood crested on 28 February — and no satellite was overhead to see it.'
                : 'A shallow-water solver, forced by observed rainfall and calibrated to the one radar pass we do have.'}
            </div>
          </div>

          {/* Simulated clock + how much of the flood is visible right now */}
          <div style={{ display: ready ? 'block' : 'none',
            position: 'absolute', right: 56, top: 92, textAlign: 'right',
            opacity: clamp01(t * 6) * (1 - clamp01((t - 0.9) / 0.1)),
          }}>
            <div style={{ ...kicker, marginBottom: 6 }}>Simulated</div>
            <div style={{ fontSize: '2.1rem', fontWeight: 800, letterSpacing: '-0.01em',
                          fontVariantNumeric: 'tabular-nums', textShadow: '0 3px 20px rgba(0,0,0,0.8)' }}>
              {simTime ? new Date(simTime).toLocaleString('en-GB',
                { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) : '—'}
            </div>
            <div style={{ marginTop: 14, fontSize: '0.72rem', color: 'rgba(255,255,255,0.7)',
                          letterSpacing: '0.06em' }}>
              AREA UNDER WATER
            </div>
            <div style={{ fontSize: '1.5rem', fontWeight: 800, fontVariantNumeric: 'tabular-nums',
                          color: wetNow >= wetPeak * 0.98 ? '#FF6B6B' : '#7FD4F5' }}>
              {(wetNow * 100).toFixed(1)}%
            </div>
            <div style={{ marginTop: 16, display: 'flex', gap: 22, justifyContent: 'flex-end' }}>
              <Stat label="SATELLITE PASSES" value="1" />
              <Stat label="SIMULATED HOURS" value={meta ? String(meta.shape[0]) : '—'} />
            </div>
          </div>

          {crossedPass && (
            <div className="anim-fade-in" style={{
              position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%,-50%)',
              padding: '10px 20px', border: '1px solid rgba(255,179,71,0.7)',
              background: 'rgba(20,12,0,0.55)', borderRadius: 999, backdropFilter: 'blur(6px)',
              fontSize: '0.72rem', letterSpacing: '0.2em', fontWeight: 700, color: '#FFB347',
            }}>
              ◀ SATELLITE PASS
            </div>
          )}
        </>
      )}

      {beat === 'house' && (
        <div style={{ ...lower, opacity: fade() }}>
          <div style={kicker}>Structure-aware depth</div>
          <div style={head}>3.4 ft above<br />the finished floor.</div>
          <div style={sub}>Not depth above the ground — depth above the floor the carpet is on.
            That is what decides the claim.</div>
        </div>
      )}

      {beat === 'predict' && (
        <div style={{ ...lower, opacity: fade() }}>
          <div style={kicker}>Pre-landfall model · 0.83 AUC held out</div>
          <div style={head}>And we know<br />before the storm.</div>
          <div style={sub}>Trained on 11 real floods across five continents, then tested on
            floods it had never seen.</div>
        </div>
      )}

      {beat === 'card' && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center', gap: 26,
          background: `rgba(4,7,14,${(0.9 * clamp01(t * 2.4)).toFixed(3)})`,
          opacity: 1 - clamp01((t - 0.88) / 0.12),
        }}>
          <div style={{ fontSize: 'clamp(2.6rem, 7vw, 5.4rem)', fontWeight: 800,
                        letterSpacing: '0.18em', opacity: clamp01((t - 0.06) * 4) }}>ALTIS</div>
          <div style={{ display: 'flex', gap: 'clamp(24px, 5vw, 64px)', flexWrap: 'wrap',
                        justifyContent: 'center', opacity: clamp01((t - 0.24) * 3) }}>
            <Stat big label="HELD-OUT AUC" value="0.83" />
            <Stat big label="FLOOD EVENTS TRAINED" value="11" />
            <Stat big label="PUBLIC DATASETS" value="8" />
            <Stat big label="PAID APIs" value="0" />
          </div>
          <div style={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.55)', letterSpacing: '0.12em',
                        opacity: clamp01((t - 0.44) * 3) }}>
            SENTINEL-1 · NOAA · FEMA · USGS · JRC · CHIRPS · OSM · NHC
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, big }) {
  return (
    <div style={{ textAlign: big ? 'center' : 'right' }}>
      <div style={{ fontSize: big ? 'clamp(1.8rem, 4vw, 3rem)' : '1.15rem', fontWeight: 800,
                    fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>{value}</div>
      <div style={{ marginTop: 6, fontSize: big ? '0.62rem' : '0.54rem', letterSpacing: '0.16em',
                    color: 'rgba(255,255,255,0.6)', fontWeight: 700 }}>{label}</div>
    </div>
  );
}

function DemoControls({ visible, playing, ms, total, onPlay, onPause, onRestart, onClose, onScrub }) {
  const btn = {
    padding: '7px 14px', borderRadius: 999, cursor: 'pointer', fontFamily: 'var(--font)',
    fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase',
    background: 'rgba(255,255,255,0.09)', border: '1px solid rgba(255,255,255,0.22)',
    color: '#fff', backdropFilter: 'blur(10px)',
  };
  return (
    <div style={{
      position: 'fixed', bottom: 18, left: '50%', transform: 'translateX(-50%)',
      zIndex: 60, display: 'flex', alignItems: 'center', gap: 10,
      padding: '10px 14px', borderRadius: 14, background: 'rgba(8,12,20,0.72)',
      border: '1px solid rgba(255,255,255,0.14)', backdropFilter: 'blur(14px)',
      opacity: visible ? 1 : 0, transition: 'opacity 0.5s ease',
      pointerEvents: visible ? 'all' : 'none', minWidth: 520,
    }}>
      <button style={btn} onClick={playing ? onPause : onPlay}>{playing ? '❚❚' : '▶'}</button>
      <button style={btn} onClick={onRestart}>↻</button>
      <input type="range" min="0" max="1" step="0.001" value={ms / total}
             onChange={(e) => onScrub(+e.target.value)}
             style={{ flex: 1, accentColor: '#7FD4F5' }} aria-label="Scrub demo" />
      <span style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.75)', fontFamily: 'var(--font)',
                     fontVariantNumeric: 'tabular-nums', minWidth: 74, textAlign: 'right' }}>
        {(ms / 1000).toFixed(1)}s / {(total / 1000).toFixed(0)}s
      </span>
      <button style={btn} onClick={onClose}>Exit</button>
    </div>
  );
}
