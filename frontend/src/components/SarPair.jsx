import { useState, useEffect } from 'react';
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

/* Pre/post pair — refetches per property_id and sensor view (sar | optical).
   When lat/lon + eventDate are supplied and Earth Engine is live, the backend
   returns REAL Sentinel-1/Sentinel-2 imagery for that exact spot + date. */
export default function SarPair({ propertyId, view = 'sar', lat, lon, eventDate, compact = false }) {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(false);

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

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: compact ? 6 : 10 }}>
        <ImgBox label="Pre"  src={data?.pre_url}  loading={loading} compact={compact} />
        <ImgBox label="Post" src={data?.post_url} loading={loading} compact={compact} />
      </div>
      {!compact && (
        <div style={{ fontSize: '0.62rem', marginTop: 6, textAlign: 'center',
                      color: real ? 'var(--approve)' : 'var(--text-muted)' }}>
          {loading ? 'Loading imagery…'
            : real ? '● Live Sentinel imagery — this exact location & date'
            : 'Synthetic preview — run live analysis (with GEE) for real imagery'}
        </div>
      )}
    </div>
  );
}
