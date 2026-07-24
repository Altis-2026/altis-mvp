import { useIsMobile } from '../hooks/useIsMobile.js';
import { useState, useMemo } from 'react';
import { downloadCSV } from '../utils/csv.js';
import { exportGuidewire, exportDuckCreek } from '../utils/claimsExport.js';

/* DataGrid — full-screen claims table where a claims manager lives as much as
   the globe. Sortable columns, text + triage filters, row selection with
   bulk CSV export, and click-through to the property drawer. Works for both
   event triage data and analyzed portfolio data (columns adapt). */

const TRIAGE_COLORS = {
  'Dispatch': '#FF4444', 'Remote-Approve': '#4CAF82',
  'Remote-Deny': '#6B8FA3', 'Review': '#FFB347', 'No Coverage': '#5A6B78',
};
const TRIAGE_CLASSES = ['Dispatch', 'Remote-Approve', 'Remote-Deny', 'Review'];

const EVENT_COLUMNS = [
  { key: 'property_id',      label: 'ID',          w: 110, type: 'text' },
  { key: 'address',          label: 'Address',     w: 240, type: 'text' },
  { key: 'impact_class',     label: 'Triage',      w: 130, type: 'badge' },
  { key: 'max_depth_ft',     label: 'Depth (ft)',  w: 95,  type: 'num' },
  { key: 'pct_flooded',      label: 'Area %',      w: 80,  type: 'num' },
  { key: 'confidence_score', label: 'Conf %',      w: 80,  type: 'num' },
  { key: 'adjuster_note',    label: 'Adjuster note', w: 320, type: 'text' },
];
const PORTFOLIO_COLUMNS = [
  { key: 'property_id',      label: 'ID',          w: 130, type: 'text' },
  { key: 'policy_number',    label: 'Policy',      w: 110, type: 'text' },
  { key: 'address',          label: 'Address',     w: 240, type: 'text' },
  { key: 'coverage_amount',  label: 'Coverage',    w: 110, type: 'money' },
  { key: 'impact_class',     label: 'Triage',      w: 130, type: 'badge' },
  { key: 'max_depth_ft',     label: 'Depth (ft)',  w: 95,  type: 'num' },
  { key: 'confidence_score', label: 'Conf %',      w: 80,  type: 'num' },
  { key: 'adjuster_note',    label: 'Adjuster note', w: 300, type: 'text' },
];

function fmt(value, type) {
  if (value === null || value === undefined || value === '') return '—';
  if (type === 'num') return Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
  if (type === 'money') return `$${Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  return String(value);
}

export default function DataGrid({ title, rows = [], kind = 'event', onClose, onRowClick, eventLabel, eventDate }) {
  const isMobile = useIsMobile();
  const columns = kind === 'portfolio' ? PORTFOLIO_COLUMNS : EVENT_COLUMNS;
  const hasTriage = rows.length > 0 && rows[0].impact_class !== undefined;

  const [search, setSearch] = useState('');
  const [triage, setTriage] = useState(new Set());
  const [sortKey, setSortKey] = useState(kind === 'portfolio' ? 'coverage_amount' : 'max_depth_ft');
  const [sortDir, setSortDir] = useState('desc');
  const [selected, setSelected] = useState(new Set());

  const toggleTriage = (c) => {
    const next = new Set(triage);
    next.has(c) ? next.delete(c) : next.add(c);
    setTriage(next);
  };

  const sortBy = (key) => {
    if (key === sortKey) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('desc'); }
  };

  const filtered = useMemo(() => {
    let r = rows.filter(p => {
      if (search) {
        const q = search.toLowerCase();
        const hit = `${p.address || ''} ${p.property_id || ''} ${p.policy_number || ''}`
          .toLowerCase().includes(q);
        if (!hit) return false;
      }
      if (triage.size > 0 && !triage.has(p.impact_class)) return false;
      return true;
    });
    const col = columns.find(c => c.key === sortKey);
    const numeric = col && (col.type === 'num' || col.type === 'money');
    r = [...r].sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey];
      if (numeric) { av = parseFloat(av) || 0; bv = parseFloat(bv) || 0; return sortDir === 'asc' ? av - bv : bv - av; }
      av = `${av ?? ''}`.toLowerCase(); bv = `${bv ?? ''}`.toLowerCase();
      return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    return r;
  }, [rows, search, triage, sortKey, sortDir, columns]);

  const allSelected = filtered.length > 0 && filtered.every(p => selected.has(p.property_id));
  const toggleAll = () => {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(filtered.map(p => p.property_id)));
  };
  const toggleRow = (id) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  };

  const [exportFormat, setExportFormat] = useState('altis');

  const exportRows = (which) => {
    const data = which === 'selected'
      ? filtered.filter(p => selected.has(p.property_id))
      : filtered;
    const opts = { eventLabel, eventDate };
    if (exportFormat === 'guidewire') return exportGuidewire(data, opts);
    if (exportFormat === 'duckcreek') return exportDuckCreek(data, opts);
    downloadCSV(`altis_${kind}_${which}_${data.length}`, data, columns.map(c => c.key));
  };

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 'var(--z-modal)',
        background: 'rgba(0,0,4,0.78)', backdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 28,
      }}
    >
      <div
        className="anim-slide-in-up"
        onClick={e => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: isMobile ? 'none' : 1180, height: '100%', maxHeight: isMobile ? '100dvh' : '90vh',
          background: 'var(--panel-solid)', border: '1px solid var(--wa-08)',
          borderRadius: 'var(--r-xl)', display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{ padding: '20px 24px 16px', borderBottom: '1px solid var(--wa-06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 }}>
            <div>
              <div style={{ fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.14em', color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 4 }}>
                Claims Data Grid
              </div>
              <h2 style={{ fontSize: '1.25rem', fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
                {title || 'Properties'}
              </h2>
            </div>
            <button onClick={onClose} style={closeBtn}
              onMouseEnter={e => e.currentTarget.style.color = '#fff'}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>✕</button>
          </div>

          {/* Toolbar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <input
              value={search} onChange={e => setSearch(e.target.value)}
              placeholder="Search address, ID, policy…"
              style={{
                flex: '1 1 240px', minWidth: 200, padding: '8px 12px', fontSize: '0.78rem',
                background: 'var(--wa-04)', border: '1px solid var(--wa-08)',
                borderRadius: 'var(--r-sm)', color: 'var(--text-primary)', fontFamily: 'var(--font)',
              }}
            />
            {hasTriage && TRIAGE_CLASSES.map(c => (
              <button key={c} onClick={() => toggleTriage(c)} style={{
                padding: '6px 10px', borderRadius: 'var(--r-sm)', fontSize: '0.66rem', fontWeight: 700,
                background: triage.has(c) ? `${TRIAGE_COLORS[c]}22` : 'transparent',
                border: `1px solid ${triage.has(c) ? TRIAGE_COLORS[c] : 'var(--wa-10)'}`,
                color: triage.has(c) ? TRIAGE_COLORS[c] : 'var(--text-muted)',
                cursor: 'pointer', fontFamily: 'var(--font)', whiteSpace: 'nowrap',
              }}>{c}</button>
            ))}
          </div>

          {/* Counts + bulk actions */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 12, gap: 12, flexWrap: 'wrap' }}>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>
              <strong style={{ color: 'var(--text-primary)' }}>{filtered.length.toLocaleString()}</strong> of {rows.length.toLocaleString()} shown
              {selected.size > 0 && <span style={{ color: 'var(--teal)' }}> · {selected.size} selected</span>}
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <select
                value={exportFormat}
                onChange={e => setExportFormat(e.target.value)}
                title="Export layout. Guidewire and Duck Creek files are shaped for direct claim-intake import."
                style={{
                  padding: '6px 8px', fontSize: '0.7rem', fontWeight: 600,
                  background: 'var(--wa-04)', color: 'var(--text-secondary)',
                  border: '1px solid var(--wa-10)', borderRadius: 'var(--r-sm)',
                  fontFamily: 'var(--font)', cursor: 'pointer',
                }}
              >
                <option value="altis">Altis CSV</option>
                <option value="guidewire">Guidewire ClaimCenter</option>
                <option value="duckcreek">Duck Creek Claims</option>
              </select>
              <button onClick={() => exportRows('selected')} disabled={selected.size === 0} style={gridBtn(selected.size === 0)}>
                ↓ Export selected
              </button>
              <button onClick={() => exportRows('filtered')} disabled={filtered.length === 0} style={gridBtn(filtered.length === 0)}>
                ↓ Export all shown
              </button>
              {selected.size > 0 && (
                <button onClick={() => setSelected(new Set())} style={gridBtn(false, true)}>Clear</button>
              )}
            </div>
          </div>
        </div>

        {/* Table */}
        <div style={{ flex: 1, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.74rem', whiteSpace: 'nowrap' }}>
            <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
              <tr style={{ background: 'rgba(12,16,24,0.98)' }}>
                <th style={{ ...thStyle, width: 36, textAlign: 'center' }}>
                  <input type="checkbox" checked={allSelected} onChange={toggleAll} style={{ cursor: 'pointer' }} />
                </th>
                {columns.map(c => (
                  <th key={c.key} onClick={() => sortBy(c.key)}
                      style={{ ...thStyle, minWidth: c.w, cursor: 'pointer', userSelect: 'none' }}>
                    {c.label}
                    <span style={{ color: sortKey === c.key ? 'var(--teal)' : 'transparent', marginLeft: 4 }}>
                      {sortKey === c.key ? (sortDir === 'asc' ? '▲' : '▼') : '▼'}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((p, i) => {
                const isSel = selected.has(p.property_id);
                return (
                  <tr key={p.property_id || i}
                      style={{ background: isSel ? 'rgba(168,212,230,0.07)' : 'transparent', cursor: 'pointer' }}
                      onMouseEnter={e => { if (!isSel) e.currentTarget.style.background = 'var(--wa-03)'; }}
                      onMouseLeave={e => { if (!isSel) e.currentTarget.style.background = 'transparent'; }}>
                    <td style={{ ...tdStyle, textAlign: 'center' }} onClick={e => { e.stopPropagation(); toggleRow(p.property_id); }}>
                      <input type="checkbox" checked={isSel} readOnly style={{ cursor: 'pointer' }} />
                    </td>
                    {columns.map(c => (
                      <td key={c.key} style={{
                        ...tdStyle,
                        textAlign: (c.type === 'num' || c.type === 'money') ? 'right' : 'left',
                        color: c.key === 'adjuster_note' ? 'var(--text-muted)' : 'var(--text-body)',
                        maxWidth: c.w, overflow: 'hidden', textOverflow: 'ellipsis',
                      }}
                        onClick={() => onRowClick?.(p)}>
                        {c.type === 'badge' && p[c.key]
                          ? <span className={`badge badge-${p[c.key]}`} style={{ fontSize: '0.6rem', padding: '1px 6px' }}>{p[c.key]}</span>
                          : fmt(p[c.key], c.type)}
                      </td>
                    ))}
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr><td colSpan={columns.length + 1} style={{ ...tdStyle, textAlign: 'center', color: 'var(--text-muted)', padding: 40 }}>
                  No properties match the current filters.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

const thStyle = {
  textAlign: 'left', padding: '10px 12px', fontSize: '0.64rem', fontWeight: 700,
  letterSpacing: '0.04em', color: 'var(--text-secondary)', textTransform: 'uppercase',
  borderBottom: '1px solid var(--wa-10)',
};
const tdStyle = { padding: '8px 12px', borderBottom: '1px solid var(--wa-04)', fontVariantNumeric: 'tabular-nums' };
const closeBtn = {
  background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer',
  fontSize: '1.2rem', lineHeight: 1, padding: '0 0 0 16px', transition: 'color 0.15s',
};
function gridBtn(disabled, ghost) {
  return {
    padding: '7px 12px', fontSize: '0.7rem', fontWeight: 700, fontFamily: 'var(--font)',
    borderRadius: 'var(--r-sm)', whiteSpace: 'nowrap',
    background: ghost ? 'transparent' : disabled ? 'var(--wa-03)' : 'rgba(168,212,230,0.12)',
    border: `1px solid ${ghost ? 'var(--wa-12)' : disabled ? 'var(--wa-06)' : 'rgba(168,212,230,0.3)'}`,
    color: ghost ? 'var(--text-secondary)' : disabled ? 'var(--text-disabled)' : 'var(--teal)',
    cursor: disabled ? 'not-allowed' : 'pointer',
  };
}
