import { useState } from 'react';
import SarPair from './SarPair.jsx';

const COLORS = {
  'Dispatch':       '#FF4444',
  'Remote-Approve': '#4CAF82',
  'Remote-Deny':    '#6B8FA3',
  'Review':         '#FFB347',
};

/* Confidence arc gauge */
function ConfidenceGauge({ value, color }) {
  const r   = 30;
  const circ = 2 * Math.PI * r;
  const pct  = Math.max(0, Math.min(100, +value || 0));
  const dash = (pct / 100) * circ;

  return (
    <svg width="72" height="72" viewBox="0 0 72 72" style={{ flexShrink: 0 }}>
      <circle cx="36" cy="36" r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="5"/>
      <circle
        cx="36" cy="36" r={r}
        fill="none"
        stroke={color || '#A8D4E6'}
        strokeWidth="5"
        strokeDasharray={`${dash} ${circ - dash}`}
        strokeDashoffset={circ * 0.25}
        strokeLinecap="round"
        style={{ transition: 'stroke-dasharray 0.6s ease' }}
      />
      <text x="36" y="40" textAnchor="middle" fill="#fff" fontSize="14" fontWeight="800"
            fontFamily="Plus Jakarta Sans, sans-serif">
        {pct}%
      </text>
    </svg>
  );
}

/* Measurement row */
function Mrow({ label, value }) {
  return (
    <tr>
      <td style={{ fontSize: '0.76rem', color: 'var(--text-muted)', padding: '5px 0', borderTop: '1px solid rgba(255,255,255,0.04)' }}>
        {label}
      </td>
      <td style={{ fontSize: '0.76rem', color: '#ccc', textAlign: 'right', borderTop: '1px solid rgba(255,255,255,0.04)', fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </td>
    </tr>
  );
}

export default function PropertyDrawer({ property, eventId, onClose, onAddToCompare, isInCompare, compareFull }) {
  const [sarView, setSarView] = useState('sar'); // 'sar' | 'optical'

  if (!property) return null;

  const ic     = property.impact_class;
  const color  = COLORS[ic] || '#6B8FA3';
  const depth  = parseFloat(property.max_depth_ft || 0).toFixed(2);
  const pct    = parseFloat(property.pct_flooded || 0).toFixed(1);
  const conf   = parseInt(property.confidence_score || 0);
  const urban  = property.urban_flag == 1;
  const hasDepthCI = property.depth_ci_ft != null && property.depth_ci_ft !== '';
  const depthLabel = hasDepthCI
    ? `${depth} ft ± ${parseFloat(property.depth_ci_ft).toFixed(1)} ft`
    : `${depth} ft`;

  const breakdown = parseJsonField(property.confidence_factors);
  const ensembleVotes = parseJsonField(property.ensemble_votes);
  const disagrees = property.ensemble_disagreement === true
    || property.ensemble_disagreement === 1 || property.ensemble_disagreement === '1'
    || property.ensemble_disagreement === 'True';

  return (
    <>
      {/* Backdrop (closes drawer on click, doesn't block globe) */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, zIndex: 'var(--z-drawer)',
          pointerEvents: 'all',
        }}
      />

      {/* Drawer panel */}
      <div
        className="anim-slide-in-right"
        onClick={e => e.stopPropagation()}
        style={{
          position:   'fixed',
          top:        0,
          right:      0,
          bottom:     0,
          width:      '36%',
          minWidth:   380,
          maxWidth:   520,
          zIndex:     'var(--z-drawer)',
          background: 'rgba(4,6,14,0.95)',
          borderLeft: '1px solid rgba(255,255,255,0.06)',
          backdropFilter: 'blur(20px)',
          overflowY:  'auto',
          display:    'flex',
          flexDirection: 'column',
          pointerEvents: 'all',
        }}
      >
        {/* ── Header ─────────────────────────────────────── */}
        <div style={{
          padding:       '24px 24px 20px',
          borderBottom:  '1px solid rgba(255,255,255,0.05)',
          position:      'sticky',
          top:           0,
          background:    'rgba(4,6,14,0.98)',
          zIndex:        1,
        }}>
          {/* Close + property ID */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
            <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', letterSpacing: '0.08em', fontWeight: 600 }}>
              {property.property_id}
            </span>
            <button onClick={onClose} style={{
              background: 'none', border: 'none', color: 'var(--text-muted)',
              cursor: 'pointer', fontSize: '1.1rem', lineHeight: 1, padding: '0 0 0 12px',
              transition: 'color 0.15s',
            }}
              onMouseEnter={e => e.currentTarget.style.color = '#fff'}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
            >
              ✕
            </button>
          </div>

          {/* Address */}
          <h2 style={{
            fontSize: '1.15rem', fontWeight: 700, color: '#fff',
            letterSpacing: '-0.01em', lineHeight: 1.3, marginBottom: 14,
          }}>
            {property.address}
          </h2>

          {/* Badge + gauge row */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <span className={`badge badge-${ic}`}>{ic}</span>
              {urban && (
                <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 5 }}>
                  <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#FFB347', display: 'inline-block' }} />
                  <span style={{ fontSize: '0.62rem', color: '#FFB347', letterSpacing: '0.05em', fontWeight: 600 }}>
                    URBAN SAR ZONE — ELEVATED UNCERTAINTY
                  </span>
                </div>
              )}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
              <ConfidenceGauge value={conf} color={color} />
              <span style={{ fontSize: '0.6rem', color: 'var(--text-muted)', letterSpacing: '0.08em' }}>
                CONFIDENCE
              </span>
            </div>
          </div>
        </div>

        {/* ── Body ───────────────────────────────────────── */}
        <div style={{ padding: '20px 24px', flex: 1 }}>

          {/* SAR imagery section */}
          <div style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <span style={{
                fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
                color: 'var(--teal)', textTransform: 'uppercase',
              }}>
                Sentinel-1 SAR Imagery
              </span>
              <div style={{ display: 'flex', gap: 4 }}>
                {['sar', 'optical'].map(v => (
                  <button key={v} onClick={() => setSarView(v)} style={{
                    padding: '4px 10px',
                    background: sarView === v ? 'rgba(168,212,230,0.1)' : 'transparent',
                    border: `1px solid ${sarView === v ? 'rgba(168,212,230,0.25)' : 'rgba(255,255,255,0.07)'}`,
                    borderRadius: 'var(--r-sm)',
                    color: sarView === v ? '#A8D4E6' : 'var(--text-muted)',
                    fontSize: '0.66rem', fontWeight: 600, cursor: 'pointer', fontFamily: 'var(--font)',
                    textTransform: 'uppercase', letterSpacing: '0.04em',
                  }}>
                    {v}
                  </button>
                ))}
              </div>
            </div>

            <SarPair propertyId={property.property_id} />

            <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: 6, textAlign: 'center' }}>
              Synthetic preview shown when GEE imagery isn't cached — see README
            </div>
          </div>

          {/* Measurements */}
          <div style={{ marginBottom: 24 }}>
            <div style={{
              fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
              color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 12,
            }}>
              Raw Measurements
            </div>
            <div style={{
              background: 'rgba(255,255,255,0.02)',
              border:     '1px solid rgba(255,255,255,0.05)',
              borderRadius: 'var(--r-md)',
              padding:    '4px 14px',
            }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <tbody>
                  <Mrow label="Max Flood Depth"   value={depthLabel} />
                  <Mrow label="Area Flooded"       value={`${pct}%`} />
                  <Mrow label="Confidence Score"   value={`${conf}%`} />
                  <Mrow label="Data Source"        value="Sentinel-1 SAR" />
                  <Mrow label="Urban Shadow Zone"  value={urban ? 'Yes (-15pt penalty)' : 'No'} />
                  <Mrow label="Resolution"         value="10m native, 30m sampled" />
                </tbody>
              </table>
            </div>
          </div>

          {/* Ensemble disagreement warning */}
          {disagrees && (
            <div style={{
              marginBottom: 24, background: 'rgba(255,179,71,0.06)',
              border: '1px solid rgba(255,179,71,0.25)', borderRadius: 'var(--r-md)',
              padding: '12px 14px',
            }}>
              <div style={{ fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.1em', color: '#FFB347', textTransform: 'uppercase', marginBottom: 6 }}>
                Sensor disagreement — flagged for Review
              </div>
              <p style={{ fontSize: '0.74rem', color: '#ccc', lineHeight: 1.5, margin: 0 }}>
                {property.ensemble_note || 'Independent flood signals (SAR / optical / DEM-hydrology) disagree on this property.'}
              </p>
              {ensembleVotes && (
                <div style={{ display: 'flex', gap: 14, marginTop: 8 }}>
                  {Object.entries(ensembleVotes).map(([k, v]) => (
                    <div key={k} style={{ fontSize: '0.66rem', color: 'var(--text-muted)' }}>
                      {k.replace('_', ' ')}: <span style={{ color: '#ddd', fontWeight: 600 }}>{v}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Why this decision — confidence factor breakdown */}
          {breakdown && Array.isArray(breakdown.factors) && breakdown.factors.length > 0 && (
            <div style={{ marginBottom: 24 }}>
              <div style={{
                fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.12em',
                color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 12,
              }}>
                Why This Decision
              </div>
              <div style={{
                background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)',
                borderRadius: 'var(--r-md)', padding: '4px 14px',
              }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <tbody>
                    <Mrow label="Base score" value={breakdown.base} />
                    {breakdown.factors.map((f, i) => (
                      <tr key={i}>
                        <td style={{ fontSize: '0.74rem', color: 'var(--text-secondary)', padding: '5px 0', borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                          {f.factor}
                          <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)', marginTop: 2 }}>{f.reason}</div>
                        </td>
                        <td style={{
                          fontSize: '0.76rem', textAlign: 'right', verticalAlign: 'top', paddingTop: 5,
                          borderTop: '1px solid rgba(255,255,255,0.04)', fontVariantNumeric: 'tabular-nums',
                          color: f.delta > 0 ? '#4CAF82' : '#FF6B6B', fontWeight: 700,
                        }}>
                          {f.delta > 0 ? '+' : ''}{f.delta}
                        </td>
                      </tr>
                    ))}
                    <Mrow label="Final score (clamped 30–97)" value={`${breakdown.final_score}%`} />
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Adjuster note */}
          {property.adjuster_note && (
            <div style={{
              marginBottom: 24,
              background:   `linear-gradient(135deg, rgba(6,8,14,0) 0%, rgba(${colorToRgb(color)},0.04) 100%)`,
              border:       `1px solid rgba(${colorToRgb(color)},0.15)`,
              borderLeft:   `3px solid ${color}`,
              borderRadius: '0 var(--r-md) var(--r-md) 0',
              padding:      '14px 16px',
            }}>
              <div style={{
                fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.12em',
                color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 8,
              }}>
                Adjuster Note
              </div>
              <p style={{ fontSize: '0.88rem', color: '#bbb', lineHeight: 1.65, fontStyle: 'italic' }}>
                "{property.adjuster_note}"
              </p>
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 10, marginBottom: 8 }}>
            <ActionButton
              label="Approve Claim"
              color="#4CAF82"
              onClick={() => alert('Guidewire integration coming in v2')}
            />
            <ActionButton
              label="Dispatch Adjuster"
              color="#FF4444"
              outline
              onClick={() => alert('CAT team dispatch integration coming in v2')}
            />
          </div>
          <div style={{ fontSize: '0.6rem', color: 'var(--text-disabled)', textAlign: 'center', marginBottom: 16 }}>
            Guidewire ClaimCenter integration — v2
          </div>

          <button
            onClick={() => !isInCompare && !compareFull && onAddToCompare?.(property)}
            disabled={isInCompare || compareFull}
            style={{
              width: '100%', padding: '10px 0',
              background: isInCompare ? 'rgba(168,212,230,0.1)' : 'transparent',
              border: `1px solid ${isInCompare ? 'rgba(168,212,230,0.3)' : 'rgba(255,255,255,0.1)'}`,
              borderRadius: 'var(--r-md)',
              color: isInCompare ? '#A8D4E6' : compareFull ? 'var(--text-disabled)' : 'var(--text-secondary)',
              fontSize: '0.78rem', fontWeight: 600, fontFamily: 'var(--font)',
              cursor: (isInCompare || compareFull) ? 'default' : 'pointer',
              letterSpacing: '0.02em',
            }}
          >
            {isInCompare ? '✓ Added to SAR compare' : compareFull ? 'Compare tray full (4 max)' : '+ Add to SAR compare'}
          </button>

        </div>
      </div>
    </>
  );
}

function ActionButton({ label, color, outline, onClick }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        flex:          1,
        padding:       '11px 0',
        background:    outline
          ? hover ? `rgba(${colorToRgb(color)},0.1)` : 'transparent'
          : hover ? color : `rgba(${colorToRgb(color)},0.85)`,
        border:        outline ? `1px solid ${color}` : 'none',
        borderRadius:  'var(--r-md)',
        color:         outline ? color : '#000',
        fontSize:      '0.8rem',
        fontWeight:    700,
        cursor:        'pointer',
        fontFamily:    'var(--font)',
        letterSpacing: '0.03em',
        transition:    'all 0.15s ease',
      }}
    >
      {label}
    </button>
  );
}

function parseJsonField(value) {
  if (value == null || value === '') return null;
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return null; }
}

function colorToRgb(hex) {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0,2), 16);
  const g = parseInt(h.slice(2,4), 16);
  const b = parseInt(h.slice(4,6), 16);
  return `${r},${g},${b}`;
}
