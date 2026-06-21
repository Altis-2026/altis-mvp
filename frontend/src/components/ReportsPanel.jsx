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
                    ? <th key={j} style={{ textAlign: 'left', padding: '4px 8px', borderBottom: '1px solid rgba(255,255,255,0.15)', color: '#A8D4E6' }}>{c}</th>
                    : <td key={j} style={{ padding: '4px 8px', borderBottom: '1px solid rgba(255,255,255,0.05)', color: '#ccc' }}>{c}</td>
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
          <li key={i} style={{ fontSize: '0.74rem', color: '#bbb', marginBottom: 3, lineHeight: 1.5 }}>
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
      if (p.startsWith('**') && p.endsWith('**')) return <strong key={i} style={{ color: '#fff' }}>{p.slice(2, -2)}</strong>;
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
      out.push(<div key={idx} style={{ fontSize: '0.9rem', fontWeight: 800, color: '#fff', margin: '4px 0 8px' }}>{line.slice(2)}</div>);
    } else if (line.startsWith('## ')) {
      out.push(<div key={idx} style={{ fontSize: '0.78rem', fontWeight: 700, color: '#A8D4E6', margin: '14px 0 6px' }}>{line.slice(3)}</div>);
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
      out.push(<p key={idx} style={{ fontSize: '0.76rem', color: '#bbb', lineHeight: 1.6, margin: '4px 0' }}>{inline(line)}</p>);
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

  const loadReport = async () => {
    if (!reportEventId) return;
    setLoadingReport(true);
    setReport(null);
    setReportError('');
    try {
      const data = await api.getValidationReport(reportEventId);
      setReport(data.content);
    } catch (err) {
      setReportError(err?.detail || 'Could not load report.');
    } finally {
      setLoadingReport(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '0 20px 14px' }}>
        <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#fff' }}>Reports</div>
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

        {/* Validation section */}
        <div style={{ fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.08em', color: 'var(--teal)', textTransform: 'uppercase', margin: '22px 0 10px' }}>
          FEMA validation
        </div>

        <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
          <select value={reportEventId} onChange={e => setReportEventId(e.target.value)} style={{
            flex: 1, padding: '6px 8px', fontSize: '0.74rem', background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(255,255,255,0.08)', borderRadius: 'var(--r-sm)', color: '#fff',
          }}>
            {events.map(e => <option key={e.id} value={e.id}>{e.label}</option>)}
          </select>
          <button onClick={loadReport} disabled={loadingReport} style={{
            padding: '6px 12px', fontSize: '0.72rem', fontWeight: 700,
            background: 'rgba(168,212,230,0.12)', border: '1px solid rgba(168,212,230,0.3)',
            borderRadius: 'var(--r-sm)', color: '#A8D4E6', cursor: 'pointer', fontFamily: 'var(--font)',
          }}>
            {loadingReport ? '…' : 'Load'}
          </button>
        </div>

        {reportError && (
          <div style={{ fontSize: '0.72rem', color: '#FFB347', lineHeight: 1.5, marginBottom: 10 }}>
            {reportError}
          </div>
        )}

        {report && (
          <div style={{
            border: '1px solid rgba(255,255,255,0.06)', borderRadius: 'var(--r-md)',
            padding: '12px 14px', background: 'rgba(255,255,255,0.015)',
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

function exportBtnStyle(disabled) {
  return {
    display: 'block', width: '100%', textAlign: 'left', padding: '10px 12px', marginBottom: 8,
    background: disabled ? 'rgba(255,255,255,0.02)' : 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.07)', borderRadius: 'var(--r-md)',
    color: disabled ? 'var(--text-disabled)' : '#ccc', fontSize: '0.76rem', fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer', fontFamily: 'var(--font)',
  };
}
