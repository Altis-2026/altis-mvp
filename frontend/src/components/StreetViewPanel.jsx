import { useEffect, useState } from 'react';
import { api } from '../services/api.js';

/* StreetViewPanel — street-level imagery for one property.
 *
 * Sits beside the SAR before/after pair in the drawer and answers a different
 * question. SAR shows whether water was there; this shows what the water was
 * standing against. The pipeline measures depth above GROUND, but the
 * depth-damage curve in severity.py is a function of depth above the FINISHED
 * FLOOR — so a slab-on-grade house and one raised on piers take very
 * different losses from identical water, and nothing upstream can tell them
 * apart. A human can, in about two seconds, from the street.
 *
 * The panorama is the Maps Embed API in an iframe (Google prices it at no
 * charge, unlimited). Availability and capture date come from the backend's
 * free metadata lookup. No billable Google SKU is touched.
 *
 * The imagery ALWAYS predates the event — Google does not re-drive after a
 * storm — so the capture date is shown prominently and the panel says plainly
 * that this is pre-event context, not damage evidence.
 */
export default function StreetViewPanel({ property, googleKey }) {
  const [meta, setMeta] = useState(null);
  const [state, setState] = useState('loading');

  const lat = Number(property?.latitude);
  const lon = Number(property?.longitude);
  const hasCoords = Number.isFinite(lat) && Number.isFinite(lon);

  useEffect(() => {
    if (!hasCoords) { setState('nocoords'); return; }
    let cancelled = false;
    setState('loading');
    api.getStreetView([{ property_id: property.property_id, latitude: lat, longitude: lon }])
      .then(res => {
        if (cancelled) return;
        if (!res?.available) { setMeta(null); setState('unconfigured'); return; }
        const rec = res.properties?.[String(property.property_id)];
        setMeta(rec || null);
        setState(rec?.available ? 'ready' : 'none');
      })
      .catch(() => { if (!cancelled) setState('error'); });
    return () => { cancelled = true; };
  }, [property?.property_id, lat, lon, hasCoords]);

  // Without a client-side key there is no panorama to embed; say so once
  // rather than rendering a broken frame.
  if (!googleKey || state === 'unconfigured') return null;
  if (state === 'nocoords') return null;

  const label = { fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
                  color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 12 };

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
                    marginBottom: 12, gap: 10, flexWrap: 'wrap' }}>
        <div style={{ ...label, marginBottom: 0 }}>Street-level context</div>
        {meta?.date && (
          <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>
            Captured {meta.date} · before the event
          </div>
        )}
      </div>

      {state === 'loading' && (
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', padding: '8px 0' }}>
          Checking street-level imagery…
        </div>
      )}

      {state === 'none' && (
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)',
                      background: 'var(--wa-02)', border: '1px solid var(--wa-05)',
                      borderRadius: 'var(--r-md)', padding: '10px 14px' }}>
          No street-level imagery within {60} m of this address — rural roads and
          gated communities often have none.
        </div>
      )}

      {state === 'error' && (
        <div style={{ fontSize: '0.7rem', color: '#FFB347', padding: '8px 0' }}>
          Street-level imagery lookup failed.
        </div>
      )}

      {state === 'ready' && (
        <>
          <div style={{ borderRadius: 'var(--r-md)', overflow: 'hidden',
                        border: '1px solid var(--wa-05)', background: 'var(--wa-02)' }}>
            <iframe
              title="Street-level view of the property"
              width="100%"
              height="240"
              style={{ border: 0, display: 'block' }}
              loading="lazy"
              referrerPolicy="no-referrer-when-downgrade"
              allowFullScreen
              src={`https://www.google.com/maps/embed/v1/streetview?key=${encodeURIComponent(googleKey)}`
                   + `&location=${lat},${lon}&heading=0&pitch=0&fov=90`}
            />
          </div>
          <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)',
                        lineHeight: 1.5, marginTop: 8 }}>
            Pre-event imagery — it cannot show this flood. Use it to read what
            the depth is measured against: foundation type (slab, crawlspace or
            raised), storey count, and whether utilities sit at grade. Depth is
            measured above <em>ground</em>; damage depends on depth above the{' '}
            <em>finished floor</em>. Record what you see under “Adjuster
            verdict” so it feeds the loss estimate.
          </div>
        </>
      )}
    </div>
  );
}
