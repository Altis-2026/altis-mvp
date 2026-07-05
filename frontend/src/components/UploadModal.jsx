import { useState, useRef, useCallback } from 'react';
import { api } from '../services/api.js';
import { validateEventDate, clampDays } from '../lib/validation.js';

const ACCEPTED_EXT = ['.csv', '.xlsx', '.xls', '.pdf'];

const FIELDS = [
  { key: 'address',         label: 'Address',         required: true },
  { key: 'policy_number',   label: 'Policy Number',   required: false },
  { key: 'coverage_amount', label: 'Coverage Amount', required: false },
  { key: 'city',            label: 'City',            required: false },
  { key: 'state',           label: 'State',           required: false },
  { key: 'zip',             label: 'ZIP',              required: false },
];

function confidenceColor(confidence) {
  if (confidence >= 0.8) return { color: '#4CAF82', bg: 'rgba(76,175,130,0.1)',  border: 'rgba(76,175,130,0.25)' };
  if (confidence >= 0.5) return { color: '#FFB347', bg: 'rgba(255,179,71,0.1)',  border: 'rgba(255,179,71,0.25)' };
  return                          { color: '#3A5060', bg: 'rgba(255,255,255,0.03)', border: 'rgba(255,255,255,0.08)' };
}

export default function UploadModal({ events, onClose, onSuccess }) {
  const [dragging,   setDragging]   = useState(false);
  const [file,       setFile]       = useState(null);
  // idle | uploading | preview | settings | confirming | success | error
  const [status,     setStatus]     = useState('idle');
  const [uploadData, setUploadData] = useState(null);
  const [mapping,    setMapping]    = useState({});
  const [result,     setResult]     = useState(null);
  const [errorMsg,   setErrorMsg]   = useState('');
  /* Analysis settings collected in the upload flow itself */
  const [eventDate,  setEventDate]  = useState('');
  const [preDays,    setPreDays]    = useState(7);
  const [postDays,   setPostDays]   = useState(14);
  const inputRef = useRef(null);

  const handleFile = useCallback((f) => {
    if (!f) return;
    const ok = ACCEPTED_EXT.some(ext => f.name.toLowerCase().endsWith(ext));
    if (!ok) {
      setErrorMsg('Please upload a .csv, .xlsx, .xls, or .pdf file.');
      return;
    }
    setFile(f);
    setErrorMsg('');
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    handleFile(f);
  }, [handleFile]);

  const handleUpload = async () => {
    if (!file) return;
    setStatus('uploading');
    setErrorMsg('');

    try {
      const data = await api.uploadPortfolio(file);
      const initialMapping = {};
      for (const field of Object.keys(data.suggested_mapping || {})) {
        initialMapping[field] = data.suggested_mapping[field].matched_column;
      }
      setUploadData(data);
      setMapping(initialMapping);
      setStatus('preview');
    } catch (err) {
      setErrorMsg(err.detail || 'Upload failed. Check file format and try again.');
      setStatus('error');
    }
  };

  const dateError = eventDate ? validateEventDate(eventDate) : '';

  const settingsPayload = (runNow) => (eventDate && !dateError ? {
    eventDate,
    preDays:  clampDays(preDays, 3, 60, 7),
    postDays: clampDays(postDays, 3, 45, 14),
    runNow,
  } : null);

  const handleConfirm = async (runNow = false) => {
    if (!uploadData) return;
    setStatus('confirming');
    setErrorMsg('');

    try {
      const data = await api.confirmPortfolioUpload(uploadData.upload_id, mapping);
      if (runNow && eventDate) {
        // Straight into analysis — the modal closes and the run starts.
        onSuccess?.(data, settingsPayload(true));
        return;
      }
      setResult(data);
      setStatus('success');
    } catch (err) {
      setErrorMsg(err.detail || 'Confirm failed. Check your column mapping and try again.');
      setStatus('settings');
    }
  };

  const handleSuccess = () => {
    onSuccess?.(result, settingsPayload(false));
  };

  /* Sentinel-1 revisit is ~6–12 days depending on latitude/orbit overlap. */
  const passEstimate = (days) => {
    const lo = Math.max(1, Math.floor(days / 12));
    const hi = Math.max(1, Math.round(days / 6));
    return lo === hi ? `${lo}` : `${lo}–${hi}`;
  };

  const downloadTemplate = async () => {
    const r = await api.downloadTemplate();
    const blob = await r.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = 'altis_portfolio_template.csv';
    a.click();
  };

  const usedColumns = new Set(Object.values(mapping).filter(Boolean));
  const canConfirm = !!mapping.address;
  const modalWidth = status === 'preview' ? 640 : 500;

  return (
    /* Backdrop */
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 'var(--z-modal)',
        background: 'rgba(0,0,4,0.75)',
        backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        pointerEvents: 'all',
      }}
    >
      {/* Panel */}
      <div
        className="anim-slide-in-up"
        onClick={e => e.stopPropagation()}
        style={{
          width:     modalWidth,
          maxHeight: '85vh',
          overflowY: 'auto',
          background: 'rgba(6,8,16,0.97)',
          border:    '1px solid rgba(255,255,255,0.08)',
          borderRadius: 'var(--r-xl)',
          backdropFilter: 'blur(24px)',
          padding:   32,
          pointerEvents: 'all',
          transition: 'width 0.2s ease',
        }}
      >

        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
          <div>
            <div style={{ fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.14em', color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 6 }}>
              Portfolio Analysis
            </div>
            <h2 style={{ fontSize: '1.4rem', fontWeight: 800, color: '#fff', letterSpacing: '-0.02em' }}>
              {status === 'preview' ? 'Review Column Mapping'
                : status === 'settings' || status === 'confirming' ? 'Analysis Settings'
                : 'Upload Policy Portfolio'}
            </h2>
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>
              {status === 'preview'
                ? "Confirm how Altis mapped your columns before we geocode. Override any field that looks wrong."
                : status === 'settings' || status === 'confirming'
                ? "Set the flood event date and satellite window, then run the analysis directly — no extra navigation."
                : "Upload a CSV, Excel, or PDF policy file — we geocode every property and deliver real-time satellite ground truth across your book of business."}
            </p>
          </div>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', color: 'var(--text-muted)',
            cursor: 'pointer', fontSize: '1.2rem', padding: '0 0 0 16px', lineHeight: 1,
            transition: 'color 0.15s', flexShrink: 0,
          }}
            onMouseEnter={e => e.currentTarget.style.color = '#fff'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
          >✕</button>
        </div>

        {status === 'idle' || status === 'error' ? (
          <>
            {/* Drop zone */}
            <div
              onDragOver={e => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={handleDrop}
              onClick={() => inputRef.current?.click()}
              style={{
                border:       `2px dashed ${dragging ? '#A8D4E6' : file ? 'rgba(76,175,130,0.4)' : 'rgba(255,255,255,0.1)'}`,
                borderRadius: 'var(--r-lg)',
                padding:      '36px 24px',
                textAlign:    'center',
                cursor:       'pointer',
                background:   dragging ? 'rgba(168,212,230,0.04)' : file ? 'rgba(76,175,130,0.03)' : 'rgba(255,255,255,0.01)',
                transition:   'all 0.2s ease',
                marginBottom: 16,
              }}
            >
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED_EXT.join(',')}
                style={{ display: 'none' }}
                onChange={e => handleFile(e.target.files?.[0])}
              />

              {file ? (
                <>
                  <div style={{ fontSize: '1.8rem', marginBottom: 8 }}>📄</div>
                  <div style={{ fontSize: '0.9rem', fontWeight: 700, color: '#4CAF82', marginBottom: 4 }}>
                    {file.name}
                  </div>
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                    {(file.size / 1024).toFixed(1)} KB — Click to change
                  </div>
                </>
              ) : (
                <>
                  <div style={{ fontSize: '2rem', marginBottom: 10 }}>⬆</div>
                  <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>
                    Drop your file here or click to browse
                  </div>
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                    .csv, .xlsx, .xls, or .pdf — any column layout
                  </div>
                </>
              )}
            </div>

            {errorMsg && (
              <div style={{ padding: '10px 14px', background: 'rgba(255,68,68,0.08)', border: '1px solid rgba(255,68,68,0.2)', borderRadius: 'var(--r-md)', fontSize: '0.78rem', color: '#FF4444', marginBottom: 16 }}>
                {errorMsg}
              </div>
            )}

            {/* Template link */}
            <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 20 }}>
              <button onClick={downloadTemplate} style={{
                background: 'none', border: 'none', color: 'var(--teal)',
                fontSize: '0.76rem', cursor: 'pointer', fontFamily: 'var(--font)',
                textDecoration: 'underline', opacity: 0.75,
                transition: 'opacity 0.15s',
              }}
                onMouseEnter={e => e.currentTarget.style.opacity = 1}
                onMouseLeave={e => e.currentTarget.style.opacity = 0.75}
              >
                ↓ Download CSV template
              </button>
            </div>

            {/* Upload button */}
            <button
              onClick={handleUpload}
              disabled={!file}
              style={{
                width:      '100%',
                padding:    '14px',
                background: file ? '#A8D4E6' : 'rgba(255,255,255,0.05)',
                border:     'none',
                borderRadius: 'var(--r-md)',
                color:      file ? '#000' : 'var(--text-disabled)',
                fontSize:   '0.9rem',
                fontWeight: 800,
                cursor:     file ? 'pointer' : 'not-allowed',
                fontFamily: 'var(--font)',
                letterSpacing: '0.03em',
                transition: 'background 0.2s',
              }}
              onMouseEnter={e => { if (file) e.currentTarget.style.background = '#BEE0EF'; }}
              onMouseLeave={e => { if (file) e.currentTarget.style.background = '#A8D4E6'; }}
            >
              Parse File
            </button>
          </>
        ) : status === 'uploading' ? (
          /* Uploading state */
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <div style={{
              width: 40, height: 40, border: '3px solid rgba(168,212,230,0.15)',
              borderTopColor: '#A8D4E6', borderRadius: '50%',
              animation: 'spin 0.8s linear infinite',
              margin: '0 auto 16px',
            }} />
            <div style={{ fontSize: '0.9rem', fontWeight: 600, color: '#fff', marginBottom: 6 }}>
              Parsing file…
            </div>
            <div style={{ fontSize: '0.76rem', color: 'var(--text-secondary)' }}>
              Detecting columns and standardizing addresses
            </div>
          </div>
        ) : status === 'preview' && uploadData ? (
          /* Mapping review state */
          <div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 14 }}>
              {uploadData.row_count} row{uploadData.row_count === 1 ? '' : 's'} parsed from <strong style={{ color: '#fff' }}>{uploadData.filename}</strong>
            </div>

            {/* Mapping table */}
            <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--r-md)', overflow: 'hidden', marginBottom: 16 }}>
              {FIELDS.map((f, i) => {
                const suggestion = uploadData.suggested_mapping?.[f.key];
                const confidence = suggestion?.confidence ?? 0;
                const colors = confidenceColor(mapping[f.key] ? confidence : 0);
                return (
                  <div key={f.key} style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '10px 14px',
                    borderTop: i === 0 ? 'none' : '1px solid var(--border)',
                    background: 'rgba(255,255,255,0.015)',
                  }}>
                    <div style={{ fontSize: '0.78rem', color: '#fff', fontWeight: 600 }}>
                      {f.label}{f.required && <span style={{ color: '#FF4444' }}> *</span>}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <select
                        value={mapping[f.key] || ''}
                        onChange={e => setMapping(m => ({ ...m, [f.key]: e.target.value || null }))}
                        style={{
                          background: 'rgba(0,0,0,0.3)', color: '#fff',
                          border: `1px solid ${colors.border}`, borderRadius: 'var(--r-sm)',
                          padding: '5px 8px', fontSize: '0.74rem', fontFamily: 'var(--font)',
                          minWidth: 160, cursor: 'pointer',
                        }}
                      >
                        <option value="">— Not mapped —</option>
                        {uploadData.columns.map(col => (
                          <option key={col} value={col}>{col}</option>
                        ))}
                      </select>
                      {mapping[f.key] && (
                        <span style={{
                          fontSize: '0.64rem', fontWeight: 700, padding: '3px 7px',
                          borderRadius: 'var(--r-sm)', color: colors.color,
                          background: colors.bg, border: `1px solid ${colors.border}`,
                        }}>
                          {Math.round(confidence * 100)}%
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Flagged addresses callout */}
            {uploadData.flagged_count > 0 && (
              <div style={{
                padding: '10px 14px', marginBottom: 16,
                background: 'rgba(255,179,71,0.08)', border: '1px solid rgba(255,179,71,0.2)',
                borderRadius: 'var(--r-md)', fontSize: '0.76rem', color: '#FFB347',
              }}>
                {uploadData.flagged_count} address{uploadData.flagged_count === 1 ? '' : 'es'} didn't standardize cleanly — they'll still be geocoded, but double-check them after confirming.
              </div>
            )}

            {/* Preview table */}
            <div style={{ fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.06em', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 8 }}>
              Preview
            </div>
            <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--r-md)', overflowX: 'auto', marginBottom: 20, maxHeight: 220, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.72rem' }}>
                <thead>
                  <tr style={{ background: 'rgba(255,255,255,0.03)' }}>
                    <th style={previewTh}>Policy #</th>
                    <th style={previewTh}>Address</th>
                    <th style={previewTh}>Standardized</th>
                    <th style={previewTh}>Coverage</th>
                  </tr>
                </thead>
                <tbody>
                  {uploadData.preview_rows.slice(0, 8).map((row, i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={previewTd}>{row.policy_number || '—'}</td>
                      <td style={previewTd}>{row.address || '—'}</td>
                      <td style={{ ...previewTd, color: row.address_confidence < 0.5 ? '#FFB347' : 'var(--text-secondary)' }}>
                        {row.standardized_address || '—'}
                      </td>
                      <td style={previewTd}>{row.coverage_amount || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {errorMsg && (
              <div style={{ padding: '10px 14px', background: 'rgba(255,68,68,0.08)', border: '1px solid rgba(255,68,68,0.2)', borderRadius: 'var(--r-md)', fontSize: '0.78rem', color: '#FF4444', marginBottom: 16 }}>
                {errorMsg}
              </div>
            )}

            <div style={{ display: 'flex', gap: 10 }}>
              <button
                onClick={() => { setStatus('idle'); setUploadData(null); setFile(null); }}
                disabled={status === 'confirming'}
                style={{
                  flex: '0 0 auto', padding: '14px 18px',
                  background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)',
                  borderRadius: 'var(--r-md)', color: 'var(--text-secondary)',
                  fontSize: '0.84rem', fontWeight: 700, cursor: 'pointer', fontFamily: 'var(--font)',
                }}
              >
                ← Back
              </button>
              <button
                onClick={() => canConfirm && setStatus('settings')}
                disabled={!canConfirm}
                style={{
                  flex: 1, padding: '14px',
                  background: canConfirm ? '#A8D4E6' : 'rgba(255,255,255,0.05)',
                  border: 'none', borderRadius: 'var(--r-md)',
                  color: canConfirm ? '#000' : 'var(--text-disabled)',
                  fontSize: '0.9rem', fontWeight: 800,
                  cursor: canConfirm ? 'pointer' : 'not-allowed',
                  fontFamily: 'var(--font)', letterSpacing: '0.03em',
                }}
              >
                Continue → Analysis Settings
              </button>
            </div>
          </div>
        ) : (status === 'settings' || status === 'confirming') && uploadData ? (
          /* ── Analysis Settings step (event date + satellite window) ── */
          <div>
            <div style={{ marginBottom: 18 }}>
              <div style={{ fontSize: '0.68rem', fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 6 }}>
                Flood event date
              </div>
              <input
                type="date" value={eventDate}
                onChange={e => setEventDate(e.target.value)}
                disabled={status === 'confirming'}
                style={{
                  width: '100%', padding: '10px 12px', fontSize: '0.86rem', colorScheme: 'dark',
                  background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.12)',
                  borderRadius: 'var(--r-md)', color: '#fff', fontFamily: 'var(--font)',
                }}
              />
              <div style={{ fontSize: '0.66rem', color: dateError ? '#FFB347' : 'var(--text-muted)', marginTop: 5, lineHeight: 1.45 }}>
                {dateError || 'The landfall / peak-flood date. Satellite imagery is composited around it.'}
              </div>
            </div>

            <div style={{ display: 'flex', gap: 12, marginBottom: 14 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: '0.68rem', fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 6 }}>
                  Days before (baseline)
                </div>
                <input
                  type="number" min="3" max="60" value={preDays}
                  onChange={e => setPreDays(e.target.value)}
                  disabled={status === 'confirming'}
                  style={{
                    width: '100%', padding: '10px 12px', fontSize: '0.86rem',
                    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.12)',
                    borderRadius: 'var(--r-md)', color: '#fff', fontFamily: 'var(--font)',
                  }}
                />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: '0.68rem', fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 6 }}>
                  Days after (flood window)
                </div>
                <input
                  type="number" min="3" max="45" value={postDays}
                  onChange={e => setPostDays(e.target.value)}
                  disabled={status === 'confirming'}
                  style={{
                    width: '100%', padding: '10px 12px', fontSize: '0.86rem',
                    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.12)',
                    borderRadius: 'var(--r-md)', color: '#fff', fontFamily: 'var(--font)',
                  }}
                />
              </div>
            </div>

            <div style={{
              padding: '10px 13px', marginBottom: 18, borderRadius: 'var(--r-md)',
              background: 'rgba(168,212,230,0.05)', border: '1px solid rgba(168,212,230,0.15)',
              fontSize: '0.7rem', color: 'var(--text-secondary)', lineHeight: 1.55,
            }}>
              Sentinel-1 revisits every ~6–12 days: expect <b style={{ color: '#A8D4E6' }}>
              ~{passEstimate(+preDays || 7)} baseline</b> and <b style={{ color: '#A8D4E6' }}>
              ~{passEstimate(+postDays || 14)} post-event</b> satellite passes in this window.
              A very narrow baseline can leave no pre-event scene — widen it if analysis
              reports no imagery. Settings are saved with the portfolio for re-runs.
            </div>

            {errorMsg && (
              <div style={{ padding: '10px 14px', background: 'rgba(255,68,68,0.08)', border: '1px solid rgba(255,68,68,0.2)', borderRadius: 'var(--r-md)', fontSize: '0.78rem', color: '#FF4444', marginBottom: 16 }}>
                {errorMsg}
              </div>
            )}

            <div style={{ display: 'flex', gap: 10 }}>
              <button
                onClick={() => setStatus('preview')}
                disabled={status === 'confirming'}
                style={{
                  flex: '0 0 auto', padding: '14px 18px',
                  background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)',
                  borderRadius: 'var(--r-md)', color: 'var(--text-secondary)',
                  fontSize: '0.84rem', fontWeight: 700, cursor: 'pointer', fontFamily: 'var(--font)',
                }}
              >
                ← Back
              </button>
              <button
                onClick={() => handleConfirm(false)}
                disabled={status === 'confirming'}
                style={{
                  flex: '0 0 auto', padding: '14px 16px',
                  background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)',
                  borderRadius: 'var(--r-md)', color: 'var(--text-secondary)',
                  fontSize: '0.8rem', fontWeight: 700, cursor: 'pointer', fontFamily: 'var(--font)',
                }}
              >
                Geocode only
              </button>
              <button
                onClick={() => handleConfirm(true)}
                disabled={!eventDate || !!dateError || status === 'confirming'}
                style={{
                  flex: 1, padding: '14px',
                  background: eventDate && !dateError ? 'linear-gradient(135deg, #DDF1FB, #8FC4E8)' : 'rgba(255,255,255,0.05)',
                  border: 'none', borderRadius: 'var(--r-md)',
                  color: eventDate && !dateError ? '#000' : 'var(--text-disabled)',
                  fontSize: '0.9rem', fontWeight: 800,
                  cursor: eventDate && !dateError ? 'pointer' : 'not-allowed',
                  fontFamily: 'var(--font)', letterSpacing: '0.03em',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
                }}
              >
                {status === 'confirming' && (
                  <span style={{
                    width: 14, height: 14, border: '2px solid rgba(0,0,0,0.2)',
                    borderTopColor: '#000', borderRadius: '50%',
                    animation: 'spin 0.8s linear infinite',
                  }} />
                )}
                {status === 'confirming' ? 'Geocoding…' : 'Confirm & Run Analysis'}
              </button>
            </div>
          </div>
        ) : status === 'success' && result ? (
          /* Success state */
          <div>
            <div style={{ textAlign: 'center', marginBottom: 24 }}>
              <div style={{ fontSize: '2.5rem', marginBottom: 12 }}>🛰</div>
              <div style={{ fontSize: '1rem', fontWeight: 700, color: '#4CAF82', marginBottom: 6 }}>
                Portfolio Ready
              </div>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                <span style={{ color: '#fff', fontWeight: 700 }}>{result.geocoded_count}</span> of{' '}
                <span style={{ color: '#fff', fontWeight: 700 }}>{result.total_count}</span> addresses geocoded
              </div>
            </div>

            <div style={{
              background:   'rgba(76,175,130,0.06)',
              border:       '1px solid rgba(76,175,130,0.15)',
              borderRadius: 'var(--r-md)',
              padding:      '14px 16px',
              marginBottom: 20,
              fontSize:     '0.78rem',
              color:        'var(--text-secondary)',
              lineHeight:   1.6,
            }}>
              <strong style={{ color: '#fff' }}>Portfolio ID:</strong> {result.portfolio_id}<br />
              <strong style={{ color: '#fff' }}>Center:</strong>{' '}
              {result.center?.lat?.toFixed(4)}, {result.center?.lon?.toFixed(4)}
            </div>

            <button
              onClick={handleSuccess}
              style={{
                width: '100%', padding: '14px',
                background: '#A8D4E6', border: 'none',
                borderRadius: 'var(--r-md)',
                color: '#000', fontSize: '0.9rem', fontWeight: 800,
                cursor: 'pointer', fontFamily: 'var(--font)',
                letterSpacing: '0.03em',
              }}
            >
              View on Globe →
            </button>
          </div>
        ) : null}

      </div>
    </div>
  );
}

const previewTh = {
  textAlign: 'left', padding: '8px 10px', fontSize: '0.64rem',
  fontWeight: 700, letterSpacing: '0.04em', color: 'var(--text-muted)',
  textTransform: 'uppercase', whiteSpace: 'nowrap',
};

const previewTd = {
  padding: '7px 10px', color: 'var(--text-secondary)',
  whiteSpace: 'nowrap', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis',
};
