import { useState, useRef, useCallback } from 'react';
import { api } from '../services/api.js';

export default function UploadModal({ events, onClose, onSuccess }) {
  const [dragging,   setDragging]   = useState(false);
  const [file,       setFile]       = useState(null);
  const [status,     setStatus]     = useState('idle'); // idle | uploading | success | error
  const [result,     setResult]     = useState(null);
  const [errorMsg,   setErrorMsg]   = useState('');
  const inputRef = useRef(null);

  const handleFile = useCallback((f) => {
    if (!f || !f.name.endsWith('.csv')) {
      setErrorMsg('Please upload a CSV file.');
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
      setResult(data);
      setStatus('success');
    } catch (err) {
      setErrorMsg(err.detail || 'Upload failed. Check file format and try again.');
      setStatus('error');
    }
  };

  const handleSuccess = () => {
    onSuccess?.(result);
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
          width:     500,
          background: 'rgba(6,8,16,0.97)',
          border:    '1px solid rgba(255,255,255,0.08)',
          borderRadius: 'var(--r-xl)',
          backdropFilter: 'blur(24px)',
          padding:   32,
          pointerEvents: 'all',
        }}
      >

        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
          <div>
            <div style={{ fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.14em', color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 6 }}>
              Portfolio Analysis
            </div>
            <h2 style={{ fontSize: '1.4rem', fontWeight: 800, color: '#fff', letterSpacing: '-0.02em' }}>
              Upload Carrier Portfolio
            </h2>
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>
              Upload your policy CSV and we'll geocode every property, fly the globe to your coverage area, and analyze against our satellite flood data.
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
                accept=".csv"
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
                    Drop your CSV here or click to browse
                  </div>
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                    Columns: policy_number, address, coverage_amount
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
              Geocode + Analyze Portfolio
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
              Geocoding addresses…
            </div>
            <div style={{ fontSize: '0.76rem', color: 'var(--text-secondary)' }}>
              Using US Census TIGER geocoder
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
