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

/* Pre/post SAR pair — fetches once per property_id, reused in drawer + compare tray */
export default function SarPair({ propertyId, compact = false }) {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!propertyId) return;
    setData(null);
    setLoading(true);
    api.getSarThumbnails(propertyId)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [propertyId]);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: compact ? 6 : 10 }}>
      <ImgBox label="Pre"  src={data?.pre_url}  loading={loading} compact={compact} />
      <ImgBox label="Post" src={data?.post_url} loading={loading} compact={compact} />
    </div>
  );
}
