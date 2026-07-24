import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../services/api.js';

/* Single pre/post image box */
function ImgBox({ label, src, loading, compact }) {
  return (
    <div>
      <div style={{
        fontSize: compact ? '0.56rem' : '0.62rem', fontWeight: 700, letterSpacing: '0.1em',
        color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: compact ? 3 : 6,
      }}>
        {label}
      </div>
      <div style={{
        width: '100%', aspectRatio: '3/2', background: 'rgba(255,255,255,0.03)',
        borderRadius: 'var(--r-sm)', overflow: 'hidden', border: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        {loading ? (
          <div style={{ color: 'var(--text-muted)', fontSize: compact ? '0.62rem' : '0.72rem' }}>…</div>
        ) : src ? (
          <img src={src} alt={label} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        ) : (
          <div style={{ color: 'var(--text-muted)', fontSize: compact ? '0.6rem' : '0.72rem', textAlign: 'center', padding: 8 }}>
            No imagery
          </div>
        )}
      </div>
    </div>
  );
}

/* Draggable before/after swipe — post beneath, pre clipped on top. The
   strongest "look what the satellite saw" moment in a live demo. */
function SwipeCompare({ preUrl, postUrl }) {
  const [pos, setPos] = useState(50);         // divider position, %
  const boxRef  = useRef(null);
  const dragRef = useRef(false);

  const moveTo = useCallback((clientX) => {
    const box = boxRef.current;
    if (!box) return;
    const r = box.getBoundingClientRect();
    setPos(Math.max(2, Math.min(98, ((clientX - r.left) / r.width) * 100)));
  }, []);

  useEffect(() => {
    const move = e => { if (dragRef.current) moveTo(e.touches ? e.touches[0].clientX : e.clientX); };
    const up   = () => { dragRef.current = false; };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    window.addEventListener('touchmove', move, { passive: true });
    window.addEventListener('touchend', up);
    return () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      window.removeEventListener('touchmove', move);
      window.removeEventListener('touchend', up);
    };
  }, [moveTo]);

  const tag = (side, text) => (
    <span style={{
      position: 'absolute', top: 8, [side]: 8, zIndex: 2, pointerEvents: 'none',
      padding: '3px 9px', borderRadius: 999, fontSize: '0.56rem', fontWeight: 800,
      letterSpacing: '0.12em', textTransform: 'uppercase',
      background: 'rgba(2,4,10,0.78)', color: '#CFE8F2',
      border: '1px solid rgba(168,212,230,0.3)', backdropFilter: 'blur(6px)',
    }}>{text}</span>
  );

  return (
    <div
      ref={boxRef}
      onMouseDown={e => { dragRef.current = true; moveTo(e.clientX); }}
      onTouchStart={e => { dragRef.current = true; moveTo(e.touches[0].clientX); }}
      style={{
        position: 'relative', width: '100%', aspectRatio: '3/2', overflow: 'hidden',
        borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
        cursor: 'ew-resize', userSelect: 'none', touchAction: 'pan-y',
      }}
    >
      <img src={postUrl} alt="Post-event" draggable={false}
           style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }} />
      <img src={preUrl} alt="Pre-event" draggable={false}
           style={{ position: 'absolute', inset: 0, width: '100%', height: '100%',
                    objectFit: 'cover', clipPath: `inset(0 ${100 - pos}% 0 0)` }} />

      {tag('left', 'Pre')}
      {tag('right', 'Post')}
      {/* Divider + grip */}
      <div style={{
        position: 'absolute', top: 0, bottom: 0, left: `${pos}%`, width: 2,
        transform: 'translateX(-1px)', zIndex: 2,
        background: 'linear-gradient(180deg, #DDF1FB, #8FC4E8)',
        boxShadow: '0 0 8px rgba(168,212,230,0.8)',
      }} />
      <div style={{
        position: 'absolute', top: '50%', left: `${pos}%`, zIndex: 3,
        transform: 'translate(-50%,-50%)', width: 26, height: 26, borderRadius: '50%',
        background: 'linear-gradient(135deg, #DDF1FB, #8FC4E8)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        boxShadow: '0 2px 10px rgba(0,0,0,0.55)', pointerEvents: 'none',
      }}>
        <svg width="12" height="10" viewBox="0 0 12 10" fill="none">
          <path d="M4 1L1 5l3 4M8 1l3 4-3 4" stroke="#000010" strokeWidth="1.6"
                strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </div>
    </div>
  );
}

/* Pre/post pair — refetches per property_id and sensor view (sar | optical).
   When lat/lon + eventDate are supplied and Earth Engine is live, the backend
   returns REAL Sentinel-1/Sentinel-2 imagery for that exact spot + date.
   Full-size mode offers Swipe (default when both images exist) and Split. */
export default function SarPair({ propertyId, view = 'sar', lat, lon, eventDate, compact = false }) {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(false);
  const [mode,    setMode]    = useState('swipe'); // 'swipe' | 'split'

  useEffect(() => {
    if (!propertyId) return;
    setData(null);
    setLoading(true);
    const opts = (lat != null && lon != null && eventDate) ? { lat, lon, eventDate } : {};
    api.getSarThumbnails(propertyId, view, opts)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [propertyId, view, lat, lon, eventDate]);

  const real = data?.is_real_sar;
  const canSwipe = !compact && data?.pre_url && data?.post_url;
  const swipe = canSwipe && mode === 'swipe';

  return (
    <div>
      {canSwipe && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 4, marginBottom: 6 }}>
          {[['swipe', 'Swipe'], ['split', 'Split']].map(([v, label]) => (
            <button key={v} onClick={() => setMode(v)} style={{
              padding: '3px 9px',
              background: mode === v ? 'rgba(168,212,230,0.1)' : 'transparent',
              border: `1px solid ${mode === v ? 'rgba(168,212,230,0.25)' : 'rgba(255,255,255,0.07)'}`,
              borderRadius: 'var(--r-sm)',
              color: mode === v ? '#A8D4E6' : 'var(--text-muted)',
              fontSize: '0.6rem', fontWeight: 700, cursor: 'pointer', fontFamily: 'var(--font)',
              textTransform: 'uppercase', letterSpacing: '0.06em',
            }}>
              {label}
            </button>
          ))}
        </div>
      )}

      {swipe ? (
        <SwipeCompare preUrl={data.pre_url} postUrl={data.post_url} />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: compact ? 6 : 10 }}>
          <ImgBox label="Pre"  src={data?.pre_url}  loading={loading} compact={compact} />
          <ImgBox label="Post" src={data?.post_url} loading={loading} compact={compact} />
        </div>
      )}

      {!compact && (
        <div style={{ fontSize: '0.62rem', marginTop: 6, textAlign: 'center',
                      color: real ? 'var(--approve)' : 'var(--text-muted)' }}>
          {loading ? 'Loading imagery…'
            : real ? '● Live Sentinel imagery — this exact location & date'
            : 'Synthetic preview — run live analysis (with GEE) for real imagery'}
        </div>
      )}
      {swipe && (
        <div style={{ fontSize: '0.6rem', marginTop: 2, textAlign: 'center', color: 'var(--text-muted)' }}>
          Drag to compare before / after
        </div>
      )}
    </div>
  );
}
