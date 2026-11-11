import { useState, useEffect, useMemo } from 'react';
import { api } from '../services/api.js';

/* PostStormAerial — what the property looked like from the air AFTER the storm.

   NOAA's National Geodetic Survey flies the worst-hit areas and publishes the
   photos free (about 30 cm: individual roofs, driveways, standing water and
   debris are visible). This is direct visual evidence at this house, the thing
   radar cannot give. It is NOT damage assessment: water may have receded before
   the flight, and no photo here does not mean dry. Shown only where a flight
   actually covered the property. */

const SIZE = 3;                       // 3 x 3 tiles, centred on the property
const US = (lat, lon) => lat >= 17 && lat <= 72 && lon >= -170 && lon <= -65;

function tileMath(lat, lon, z) {
  const n = 2 ** z;
  const fx = ((lon + 180) / 360) * n;
  const fy = ((1 - Math.asinh(Math.tan((lat * Math.PI) / 180)) / Math.PI) / 2) * n;
  return { x: Math.floor(fx), y: Math.floor(fy), fx: fx - Math.floor(fx), fy: fy - Math.floor(fy) };
}

export default function PostStormAerial({ property, eventDate }) {
  const lat = Number(property?.latitude);
  const lon = Number(property?.longitude);
  const ok = Number.isFinite(lat) && Number.isFinite(lon) && !!eventDate;
  const [data, setData] = useState(null);
  const [state, setState] = useState('loading');
  const [pick, setPick] = useState(0);
  const [wide, setWide] = useState(false);
  const [urls, setUrls] = useState({});

  useEffect(() => {
    if (!ok) { setState('none'); return undefined; }
    let alive = true;
    setState('loading'); setData(null); setPick(0); setWide(false);
    api.getPostStormFlights(lat, lon, eventDate)
      .then(d => { if (!alive) return; setData(d); setState(d.flights?.length ? 'ready' : (d.service_error ? 'down' : 'none')); })
      .catch(() => alive && setState('error'));
    return () => { alive = false; };
  }, [lat, lon, eventDate, ok]);

  const flight = data?.flights?.[pick];
  const z = flight ? Math.max(15, flight.zoom - (wide ? 1 : 0)) : null;
  const grid = useMemo(() => {
    if (!flight) return null;
    const t = tileMath(lat, lon, z);
    const cells = [];
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) cells.push({ dx, dy, x: t.x + dx, y: t.y + dy });
    return { ...t, cells };
  }, [flight, z, lat, lon]);

  useEffect(() => {
    if (!grid || !flight) return undefined;
    let alive = true;
    const made = [];
    setUrls({});
    grid.cells.forEach(c => {
      api.getImageryTileUrl(flight.code, z, c.x, c.y).then(u => {
        if (u) made.push(u);
        if (alive && u) setUrls(prev => ({ ...prev, [`${c.x}/${c.y}`]: u }));
      });
    });
    return () => { alive = false; made.forEach(u => URL.revokeObjectURL(u)); };
  }, [grid, flight, z]);

  if (state === 'loading') {
    return <div style={{ ...muted, marginBottom: 24 }}>Checking for NOAA post-storm aerial photos…</div>;
  }
  if (state === 'down' || state === 'error') {
    return (
      <div style={{ ...muted, marginBottom: 24 }}>
        NOAA's imagery service didn't respond, so post-storm aerial photos couldn't be checked. This does not mean none exist; reopen this property to retry.
      </div>
    );
  }
  if (state === 'none') {
    if (ok && US(lat, lon)) {
      return (
        <div style={{ ...muted, marginBottom: 24 }}>
          No NOAA post-storm aerial photos cover this spot. That means no flight reached it, not that it stayed dry.
        </div>
      );
    }
    return null;
  }

  const loaded = Object.keys(urls).length;
  return (
    <div style={{ marginBottom: 24 }}>
      <div style={sec}>Post-storm aerial photo</div>
      <div style={{ position: 'relative', width: '100%', aspectRatio: '1 / 1', overflow: 'hidden',
                    borderRadius: 'var(--r-md)', border: '1px solid var(--wa-08)', background: '#0b1118' }}>
        <div style={{
          position: 'absolute', width: `${SIZE * 100}%`, height: `${SIZE * 100}%`,
          left: `${(-0.5 - grid.fx) * 100}%`, top: `${(-0.5 - grid.fy) * 100}%`,
          display: 'grid', gridTemplateColumns: `repeat(${SIZE}, 1fr)`,
        }}>
          {grid.cells.map(c => {
            const u = urls[`${c.x}/${c.y}`];
            return u ? <img key={`${c.x}/${c.y}`} src={u} alt="" draggable={false}
                            style={{ width: '100%', height: '100%', display: 'block' }} />
                     : <div key={`${c.x}/${c.y}`} style={{ background: '#0b1118' }} />;
          })}
        </div>
        {/* property marker */}
        <div style={{ position: 'absolute', left: '50%', top: '50%', width: 34, height: 34, marginLeft: -17, marginTop: -17,
                      borderRadius: '50%', border: '2px solid var(--teal)', boxShadow: '0 0 0 1px rgba(0,0,0,0.6)',
                      pointerEvents: 'none' }} />
        {loaded < grid.cells.length && (
          <div style={{ position: 'absolute', left: 8, bottom: 8, fontSize: '0.6rem', color: '#fff',
                        background: 'rgba(0,0,0,0.55)', padding: '2px 6px', borderRadius: 4 }}>Loading imagery…</div>
        )}
        <button onClick={() => setWide(w => !w)} style={{
          position: 'absolute', right: 8, top: 8, padding: '4px 9px', fontSize: '0.64rem', fontWeight: 700,
          borderRadius: 6, border: '1px solid rgba(255,255,255,0.3)', background: 'rgba(0,0,0,0.55)', color: '#fff',
          cursor: 'pointer', fontFamily: 'var(--font)',
        }}>{wide ? 'Closer' : 'Wider'}</button>
      </div>

      {data.flights.length > 1 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
          {data.flights.map((f, i) => (
            <button key={f.code} onClick={() => setPick(i)} style={{
              padding: '4px 10px', fontSize: '0.66rem', fontWeight: 700, borderRadius: 6, cursor: 'pointer',
              fontFamily: 'var(--font)',
              background: i === pick ? 'rgba(168,212,230,0.14)' : 'transparent',
              border: `1px solid ${i === pick ? 'rgba(168,212,230,0.4)' : 'var(--wa-10)'}`,
              color: i === pick ? 'var(--teal)' : 'var(--text-secondary)',
            }}>{f.date.slice(5)}</button>
          ))}
        </div>
      )}
      <div style={{ ...muted, marginTop: 8 }}>
        Flown {flight.date} ({flight.days_after_event >= 0 ? `${flight.days_after_event} days after` : `${-flight.days_after_event} days before`} the
        event start, after {flight.storm.replace(/_/g, ' ')}. A direct look at this location from the air:
        roofs, yards, standing water and debris. Water may already have receded, so a dry-looking photo does not
        prove it never flooded.
      </div>
      <div style={{ ...muted, marginTop: 4 }}>{data.attribution}</div>
    </div>
  );
}

const sec = { fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em', color: 'var(--teal)',
              textTransform: 'uppercase', marginBottom: 10 };
const muted = { fontSize: '0.64rem', color: 'var(--text-muted)', lineHeight: 1.45 };
