import { useState, useEffect } from 'react';
import { api, setDemoCode } from '../services/api.js';
import { AltisLogo } from './Brand.jsx';

/*
 * AccessGate — lightweight shared-password screen for a public demo/investor
 * link. Wraps the whole app in main.jsx. Does nothing (renders children
 * immediately) unless the backend has DEMO_PASSWORD configured — checked by
 * calling /api/auth-check with whatever code (if any) is already stored; a
 * 200 means either there's no gate at all, or a previously-entered code is
 * still good. Only a 401 triggers the prompt screen. (/api/health can't be
 * the probe: it's deliberately unauthenticated so hosting healthchecks pass.)
 */
export default function AccessGate({ children }) {
  const [status, setStatus] = useState('checking'); // checking | locked | unlocked | offline
  const [code,   setCode]   = useState('');
  const [error,  setError]  = useState('');
  const [busy,   setBusy]   = useState(false);

  const probe = async () => {
    try {
      await api.authCheck();
      setStatus('unlocked');
    } catch (err) {
      if (err?.status === 401) {
        setDemoCode(null); // stored code (if any) is stale/wrong — drop it
        setStatus('locked');
      } else {
        setStatus('offline'); // backend unreachable — not a password problem
      }
    }
  };

  useEffect(() => { probe(); }, []);

  const submit = async (e) => {
    e.preventDefault();
    if (!code.trim() || busy) return;
    setBusy(true);
    setError('');
    setDemoCode(code.trim());
    try {
      await api.authCheck();
      setStatus('unlocked');
    } catch (err) {
      setDemoCode(null);
      setError(err?.status === 401
        ? 'Incorrect access code.'
        : "Couldn't reach the Altis server. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  if (status === 'unlocked') return children;

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 999,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: '#000004', fontFamily: "'Plus Jakarta Sans', sans-serif",
    }}>
      <div style={{ width: 'min(360px, 88vw)', textAlign: 'center' }}>
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 14 }}>
          <AltisLogo size={54} idSuffix="Gate" />
        </div>
        <div style={{
          fontSize: '1.3rem', fontWeight: 800, letterSpacing: '0.14em', marginBottom: 6,
          background: 'linear-gradient(135deg, #DDF1FB, #8FC4E8)',
          WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent',
        }}>
          ALTIS
        </div>
        <div style={{
          fontSize: '0.62rem', fontWeight: 600, letterSpacing: '0.14em',
          color: '#3A5060', textTransform: 'uppercase', marginBottom: 36,
        }}>
          Real-time satellite ground truth
        </div>

        {status === 'checking' && (
          <div style={{ color: '#8B9AA3', fontSize: '0.82rem' }}>Loading…</div>
        )}

        {status === 'offline' && (
          <div>
            <p style={{ color: '#8B9AA3', fontSize: '0.82rem', lineHeight: 1.6, marginBottom: 18 }}>
              Couldn't reach the Altis server. It may still be starting up.
            </p>
            <button onClick={probe} style={retryBtnStyle}>Retry</button>
          </div>
        )}

        {status === 'locked' && (
          <form onSubmit={submit}>
            <input
              type="password"
              autoFocus
              value={code}
              onChange={e => setCode(e.target.value)}
              placeholder="Access code"
              disabled={busy}
              style={{
                width: '100%', padding: '13px 16px', fontSize: '0.9rem',
                background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.12)',
                borderRadius: 10, color: '#fff', textAlign: 'center',
                fontFamily: 'inherit', marginBottom: 12, boxSizing: 'border-box',
              }}
            />
            {error && (
              <div style={{ color: '#FF6B6B', fontSize: '0.74rem', marginBottom: 12 }}>{error}</div>
            )}
            <button type="submit" disabled={busy || !code.trim()} style={{
              width: '100%', padding: '13px', border: 'none', borderRadius: 10,
              background: (busy || !code.trim())
                ? 'rgba(255,255,255,0.06)'
                : 'linear-gradient(135deg, #DDF1FB, #8FC4E8)',
              color: (busy || !code.trim()) ? '#3A5060' : '#000',
              fontSize: '0.86rem', fontWeight: 800, fontFamily: 'inherit',
              cursor: (busy || !code.trim()) ? 'default' : 'pointer',
            }}>
              {busy ? 'Checking…' : 'Enter'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

const retryBtnStyle = {
  padding: '10px 24px', border: '1px solid rgba(168,212,230,0.3)', borderRadius: 8,
  background: 'rgba(168,212,230,0.08)', color: '#A8D4E6', fontSize: '0.8rem',
  fontWeight: 700, fontFamily: 'inherit', cursor: 'pointer',
};
