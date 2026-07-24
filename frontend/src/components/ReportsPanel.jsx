import { useState } from 'react';
import { api } from '../services/api.js';
import { downloadCSV } from '../utils/csv.js';

/* Minimal markdown renderer — just enough for accuracy_check.py's report format:
   headers (#, ##), bullets (-), blockquotes (>), bold (**), italic (_), tables (|) */
function renderMiniMarkdown(md) {
  const lines = md.split('\n');
  const out = [];
  let tableBuf = [];
  let listBuf  = [];

  const flushTable = (key) => {
    if (tableBuf.length === 0) return;
    const rows = tableBuf.filter(l => !/^\|[\s-:|]+\|$/.test(l));
    out.push(
      <div key={`tbl-${key}`} style={{ overflowX: 'auto', margin: '8px 0' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: '0.66rem', whiteSpace: 'nowrap' }}>
          {rows.map((row, i) => {
            const cells = row.split('|').slice(1, -1).map(c => c.trim());
            return (
              <tr key={i}>
                {cells.map((c, j) => (
                  i === 0
                    ? <th key={j} style={{ textAlign: 'left', padding: '4px 8px', borderBottom: '1px solid var(--wa-15)', color: 'var(--teal)' }}>{c}</th>
                    : <td key={j} style={{ padding: '4px 8px', borderBottom: '1px solid var(--wa-05)', color: 'var(--text-body)' }}>{c}</td>
                ))}
              </tr>
            );
          })}
        </table>
      </div>
    );
    tableBuf = [];
  };

  const flushList = (key) => {
    if (listBuf.length === 0) return;
    out.push(
      <ul key={`ul-${key}`} style={{ margin: '4px 0 8px', paddingLeft: 18 }}>
        {listBuf.map((item, i) => (
          <li key={i} style={{ fontSize: '0.74rem', color: 'var(--text-body)', marginBottom: 3, lineHeight: 1.5 }}>
            {inline(item)}
          </li>
        ))}
      </ul>
    );
    listBuf = [];
  };

  const inline = (text) => {
    const parts = text.split(/(\*\*[^*]+\*\*|_[^_]+_)/g);
    return parts.map((p, i) => {
      if (p.startsWith('**') && p.endsWith('**')) return <strong key={i} style={{ color: 'var(--text-primary)' }}>{p.slice(2, -2)}</strong>;
      if (p.startsWith('_') && p.endsWith('_'))   return <em key={i} style={{ color: 'var(--text-muted)' }}>{p.slice(1, -1)}</em>;
      return p;
    });
  };

  lines.forEach((line, idx) => {
    if (line.startsWith('|')) { tableBuf.push(line); return; }
    flushTable(idx);

    if (line.startsWith('- ')) { listBuf.push(line.slice(2)); return; }
    flushList(idx);

    if (!line.trim()) return;

    if (line.startsWith('# ')) {
      out.push(<div key={idx} style={{ fontSize: '0.9rem', fontWeight: 800, color: 'var(--text-primary)', margin: '4px 0 8px' }}>{line.slice(2)}</div>);
    } else if (line.startsWith('## ')) {
      out.push(<div key={idx} style={{ fontSize: '0.78rem', fontWeight: 700, color: 'var(--teal)', margin: '14px 0 6px' }}>{line.slice(3)}</div>);
    } else if (line.startsWith('> ')) {
      out.push(
        <div key={idx} style={{
          borderLeft: '2px solid #FFB347', padding: '4px 10px', margin: '6px 0',
          fontSize: '0.72rem', color: 'var(--text-secondary)', background: 'rgba(255,179,71,0.05)',
        }}>
          {inline(line.slice(2))}
        </div>
      );
    } else if (line.startsWith('_') && line.endsWith('_')) {
      out.push(<div key={idx} style={{ fontSize: '0.66rem', color: 'var(--text-muted)', marginTop: 10 }}>{line.slice(1, -1)}</div>);
    } else {
      out.push(<p key={idx} style={{ fontSize: '0.76rem', color: 'var(--text-body)', lineHeight: 1.6, margin: '4px 0' }}>{inline(line)}</p>);
    }
  });

  flushTable('end');
  flushList('end');
  return out;
}

const EVENT_EXPORT_COLS = ['property_id', 'address', 'impact_class', 'max_depth_ft',
  'pct_flooded', 'confidence_score', 'urban_flag', 'adjuster_note'];
const PORTFOLIO_EXPORT_COLS = ['property_id', 'policy_number', 'address', 'coverage_amount',
  'impact_class', 'max_depth_ft', 'confidence_score', 'adjuster_note'];

export default function ReportsPanel({
  events = [], eventLabel, eventProperties = [],
  portfolioId, portfolioLabel, portfolioProperties = [],
}) {
  const [reportEventId, setReportEventId] = useState(events[0]?.id || '');
  const [report,         setReport]        = useState(null);
  const [reportError,    setReportError]   = useState('');
  const [loadingReport,  setLoadingReport] = useState(false);
  const [calibration,    setCalibration]   = useState(null);
  const [calibError,     setCalibError]    = useState('');
  const [downloading,    setDownloading]   = useState(false);
  const [catDownloading, setCatDownloading] = useState(false);
  const [catError,       setCatError]      = useState('');

  const _saveBlob = (blob, filename) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const downloadReport = async () => {
    if (!reportEventId || downloading) return;
    setDownloading(true);
    try {
      _saveBlob(await api.downloadEventReport(reportEventId), `altis_audit_${reportEventId}.pdf`);
    } catch (err) {
      setReportError(err?.detail || 'Could not download the report.');
    } finally {
      setDownloading(false);
    }
  };

  const hasAnalyzedPortfolio = portfolioId && portfolioProperties.length > 0
    && portfolioProperties[0]?.impact_class;

  const downloadCatReport = async () => {
    if (!portfolioId || catDownloading) return;
    setCatDownloading(true);
    setCatError('');
    try {
      _saveBlob(await api.downloadCatReport(portfolioId), `altis_cat_report_${portfolioId}.pdf`);
    } catch (err) {
      setCatError(err?.detail || 'Could not download the catastrophe report.');
    } finally {
      setCatDownloading(false);
    }
  };

  const loadReport = async () => {
    if (!reportEventId) return;
    setLoadingReport(true);
    setReport(null);
    setReportError('');
    setCalibration(null);
    setCalibError('');
    try {
      const data = await api.getValidationReport(reportEventId);
      setReport(data.content);
    } catch (err) {
      setReportError(err?.detail || 'Could not load report.');
    }
    try {
      const calib = await api.getAccuracyCalibration(reportEventId);
      setCalibration(calib);
    } catch (err) {
      setCalibError(err?.detail || 'No calibration data for this event.');
    } finally {
      setLoadingReport(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '0 20px 14px' }}>
        <div style={{ fontSize: '0.92rem', fontWeight: 700, color: 'var(--text-primary)' }}>Reports</div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 20px 20px' }}>

        {/* Export section */}
        <div style={{ fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.08em', color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 10 }}>
          Export
        </div>

        <button
          disabled={eventProperties.length === 0}
          onClick={() => downloadCSV(`altis_${eventLabel || 'event'}_results`, eventProperties, EVENT_EXPORT_COLS)}
          style={exportBtnStyle(eventProperties.length === 0)}
        >
          ↓ Export event results {eventLabel ? `(${eventLabel})` : ''}
        </button>

        <button
          disabled={portfolioProperties.length === 0}
          onClick={() => downloadCSV(`altis_portfolio_${portfolioId || 'results'}`, portfolioProperties, PORTFOLIO_EXPORT_COLS)}
          style={exportBtnStyle(portfolioProperties.length === 0)}
        >
          ↓ Export portfolio results {portfolioLabel ? `(${portfolioLabel})` : ''}
        </button>

        <button
          onClick={downloadReport}
          disabled={!reportEventId || downloading}
          style={{
            ...exportBtnStyle(!reportEventId || downloading),
            display: 'flex', alignItems: 'center', gap: 8,
            color: (!reportEventId || downloading) ? 'var(--text-disabled)' : 'var(--teal)',
            background: (!reportEventId || downloading) ? 'var(--wa-02)' : 'rgba(168,212,230,0.08)',
            border: '1px solid rgba(168,212,230,0.2)',
          }}
        >
          <span style={{ fontSize: '0.9rem' }}>📄</span>
          {downloading ? 'Downloading…' : 'Download audit PDF'} {events.find(e => e.id === reportEventId)?.label ? `(${events.find(e => e.id === reportEventId).label})` : ''}
        </button>
        <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', margin: '-2px 2px 0 2px', lineHeight: 1.4 }}>
          Methodology, scene sources + dates, triage table, top dispatch priorities,
          and FEMA-validated precision/recall in one carrier-ready document.
        </div>

        {hasAnalyzedPortfolio && (
          <>
            <button
              onClick={downloadCatReport}
              disabled={catDownloading}
              style={{
                ...exportBtnStyle(catDownloading),
                display: 'flex', alignItems: 'center', gap: 8, marginTop: 10,
                color: catDownloading ? 'var(--text-disabled)' : '#D4B068',
                background: catDownloading ? 'var(--wa-02)' : 'rgba(212,176,104,0.08)',
                border: '1px solid rgba(212,176,104,0.2)',
              }}
            >
              <span style={{ fontSize: '0.9rem' }}>🗂</span>
              {catDownloading ? 'Downloading…' : 'Download catastrophe report'} ({portfolioLabel || portfolioId})
            </button>
            <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', margin: '4px 2px 0', lineHeight: 1.4 }}>
              Reinsurance-format: exposure, estimated loss range, triage distribution,
              methodology, for the analyzed portfolio's most recent live run.
            </div>
            {catError && (
              <div style={{ fontSize: '0.68rem', color: '#FF6B6B', margin: '4px 2px 0' }}>{catError}</div>
            )}
          </>
        )}

        {/* ROI section — the per-event business case, computed from the live
            triage numbers on screen. This is the pricing conversation. */}
        <RoiWidget rows={hasAnalyzedPortfolio ? portfolioProperties : eventProperties} />

        {/* Validation section */}
        <div style={{ fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.08em', color: 'var(--teal)', textTransform: 'uppercase', margin: '22px 0 10px' }}>
          FEMA validation
        </div>

        <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
          <select value={reportEventId} onChange={e => setReportEventId(e.target.value)} style={{
            flex: 1, padding: '6px 8px', fontSize: '0.74rem', background: 'var(--wa-03)',
            border: '1px solid var(--wa-08)', borderRadius: 'var(--r-sm)', color: 'var(--text-primary)',
          }}>
            {events.map(e => <option key={e.id} value={e.id}>{e.label}</option>)}
          </select>
          <button onClick={loadReport} disabled={loadingReport} style={{
            padding: '6px 12px', fontSize: '0.72rem', fontWeight: 700,
            background: 'rgba(168,212,230,0.12)', border: '1px solid rgba(168,212,230,0.3)',
            borderRadius: 'var(--r-sm)', color: 'var(--teal)', cursor: 'pointer', fontFamily: 'var(--font)',
          }}>
            {loadingReport ? '…' : 'Load'}
          </button>
        </div>

        {reportError && (
          <div style={{ fontSize: '0.72rem', color: '#FFB347', lineHeight: 1.5, marginBottom: 10 }}>
            {reportError}
          </div>
        )}

        {calibration && calibration.holdout_metrics && (
          <ReliabilityWidget calibration={calibration} />
        )}

        {report && (
          <div style={{
            border: '1px solid var(--wa-06)', borderRadius: 'var(--r-md)',
            padding: '12px 14px', background: 'var(--wa-02)',
          }}>
            {renderMiniMarkdown(report)}
          </div>
        )}

        {!report && !reportError && !loadingReport && (
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
            Run <code style={{ color: 'var(--text-secondary)' }}>validation/accuracy_check.py</code> first
            to generate a report, then load it here.
          </div>
        )}

      </div>
    </div>
  );
}

/* ROI on this event: field visits avoided times trip cost, against Altis
   priced per property. Assumptions are editable on the spot so a skeptical
   claims VP can plug in their own numbers live and watch it recompute. */
function RoiWidget({ rows = [] }) {
  const [tripCost, setTripCost] = useState(450);
  const [perProp,  setPerProp]  = useState(5);

  const analyzed = rows.filter(r => r.impact_class);
  if (analyzed.length === 0) return null;

  const desk = analyzed.filter(r =>
    r.impact_class === 'Remote-Approve' || r.impact_class === 'Remote-Deny').length;
  const gross = desk * (+tripCost || 0);
  const cost  = analyzed.length * (+perProp || 0);
  const net   = gross - cost;
  const mult  = cost > 0 ? gross / cost : null;

  const inputStyle = {
    width: 74, padding: '5px 8px', fontSize: '0.74rem', textAlign: 'right',
    background: 'var(--input-bg)', color: 'var(--text-primary)',
    border: '1px solid var(--wa-10)', borderRadius: 'var(--r-sm)',
    fontFamily: 'var(--font)', fontVariantNumeric: 'tabular-nums',
  };
  const row = { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0' };

  return (
    <>
      <div style={{ fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.08em', color: 'var(--teal)', textTransform: 'uppercase', margin: '22px 0 10px' }}>
        Return on this event
      </div>
      <div style={{
        border: '1px solid rgba(76,175,130,0.22)', borderRadius: 'var(--r-md)',
        padding: '12px 14px', background: 'rgba(76,175,130,0.05)', marginBottom: 4,
      }}>
        <div style={row}>
          <span style={{ fontSize: '0.74rem', color: 'var(--text-secondary)' }}>
            Field visits avoided (desk-resolved)
          </span>
          <strong style={{ fontSize: '0.82rem', color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>
            {desk.toLocaleString()} of {analyzed.length.toLocaleString()}
          </strong>
        </div>
        <div style={row}>
          <span style={{ fontSize: '0.74rem', color: 'var(--text-secondary)' }}>Cost per field visit</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>$</span>
            <input type="number" min="0" value={tripCost}
                   onChange={e => setTripCost(e.target.value)} style={inputStyle} />
          </span>
        </div>
        <div style={row}>
          <span style={{ fontSize: '0.74rem', color: 'var(--text-secondary)' }}>Altis price per property</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>$</span>
            <input type="number" min="0" step="0.5" value={perProp}
                   onChange={e => setPerProp(e.target.value)} style={inputStyle} />
          </span>
        </div>
        <div style={{ borderTop: '1px solid rgba(76,175,130,0.2)', marginTop: 6, paddingTop: 8 }}>
          <div style={row}>
            <span style={{ fontSize: '0.74rem', color: 'var(--text-secondary)' }}>Trip spend avoided</span>
            <strong style={{ fontSize: '0.82rem', color: 'var(--approve)', fontVariantNumeric: 'tabular-nums' }}>
              ${gross.toLocaleString()}
            </strong>
          </div>
          <div style={row}>
            <span style={{ fontSize: '0.74rem', color: 'var(--text-secondary)' }}>
              Altis cost ({analyzed.length.toLocaleString()} properties)
            </span>
            <span style={{ fontSize: '0.78rem', color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>
              ${cost.toLocaleString()}
            </span>
          </div>
          <div style={row}>
            <span style={{ fontSize: '0.78rem', fontWeight: 700, color: 'var(--text-primary)' }}>Net savings</span>
            <strong style={{ fontSize: '0.95rem', color: net >= 0 ? 'var(--approve)' : 'var(--dispatch)', fontVariantNumeric: 'tabular-nums' }}>
              ${net.toLocaleString()}{mult != null && mult >= 1 ? ` (${mult.toFixed(0)}x)` : ''}
            </strong>
          </div>
        </div>
        <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.45 }}>
          Adjust the assumptions to your own numbers. Counts come from the
          triage currently loaded, not projections.
        </div>
      </div>
    </>
  );
}

function ReliabilityWidget({ calibration }) {
  const m = calibration.holdout_metrics;
  const curve = (m.reliability_curve || []).filter(b => b.count > 0);
  const cls = m.classification || {};
  const byCat = calibration.triage_precision_recall?.by_category || {};

  const W = 220, H = 130, pad = 18;
  const toX = v => pad + v * (W - 2 * pad);
  const toY = v => H - pad - v * (H - 2 * pad);

  return (
    <div style={{
      border: '1px solid rgba(168,212,230,0.15)', borderRadius: 'var(--r-md)',
      padding: '14px 16px', background: 'rgba(168,212,230,0.03)', marginBottom: 14,
    }}>
      <div style={{ fontSize: '0.72rem', fontWeight: 700, color: 'var(--teal)', marginBottom: 10 }}>
        Calibrated probability, held-out reliability
      </div>

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <svg width={W} height={H} style={{ flexShrink: 0 }}>
          {/* perfect-calibration diagonal */}
          <line x1={toX(0)} y1={toY(0)} x2={toX(1)} y2={toY(1)}
                style={{ stroke: "var(--wa-15)" }} strokeDasharray="3,3" />
          {/* axes */}
          <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} style={{ stroke: "var(--wa-20)" }} />
          <line x1={pad} y1={pad} x2={pad} y2={H - pad} style={{ stroke: "var(--wa-20)" }} />
          {curve.map((b, i) => (
            <circle key={i}
              cx={toX(b.mean_predicted)} cy={toY(b.observed_frequency)}
              r={Math.max(2, Math.min(6, Math.sqrt(b.count)))}
              fill="#A8D4E6" opacity={0.85}
            />
          ))}
        </svg>

        <div style={{ flex: 1, minWidth: 140 }}>
          <Stat label="Brier score" value={m.brier_score} />
          <Stat label="Calibration error (ECE)" value={m.expected_calibration_error} />
          <Stat label="Held-out precision" value={cls.precision} />
          <Stat label="Held-out recall" value={cls.recall} />
          <Stat label="Held-out F1" value={cls.f1} />
          <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: 4 }}>
            n={m.classification?.support ?? '—'} · method: {m.method} · zip-grouped split
          </div>
        </div>
      </div>

      {Object.keys(byCat).length > 0 && (
        <table style={{ width: '100%', marginTop: 12, fontSize: '0.68rem', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={thStyle}>Category</th>
              <th style={thStyle}>n</th>
              <th style={thStyle}>% truly flooded</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(byCat).map(([cat, v]) => (
              <tr key={cat}>
                <td style={tdStyle}>{cat}</td>
                <td style={tdStyle}>{v.n}</td>
                <td style={tdStyle}>{v.pct_truly_flooded != null ? `${v.pct_truly_flooded}%` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const thStyle = { textAlign: 'left', padding: '3px 6px', color: 'var(--teal)', borderBottom: '1px solid var(--wa-10)' };
const tdStyle = { padding: '3px 6px', color: 'var(--text-body)', borderBottom: '1px solid var(--wa-04)' };

function Stat({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', padding: '2px 0' }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: 'var(--text-bright)', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
        {typeof value === 'number' ? value.toFixed(value < 1 ? 3 : 2) : (value ?? '—')}
      </span>
    </div>
  );
}

function exportBtnStyle(disabled) {
  return {
    display: 'block', width: '100%', textAlign: 'left', padding: '10px 12px', marginBottom: 8,
    background: disabled ? 'var(--wa-02)' : 'var(--wa-04)',
    border: '1px solid var(--wa-07)', borderRadius: 'var(--r-md)',
    color: disabled ? 'var(--text-disabled)' : 'var(--text-body)', fontSize: '0.76rem', fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer', fontFamily: 'var(--font)',
  };
}
