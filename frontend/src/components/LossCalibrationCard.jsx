import { useState } from 'react';
import { api } from '../services/api.js';

/* LossCalibrationCard — paste the first settled claims (policy number and
   amount paid) and Altis learns this storm's dollar scale and rescales the
   whole book. On four held-out storms the uncalibrated total was off by up to
   about 30%; after 200 settled claims it was typically within 6%, after 1,000
   within 2.5% (docs/PREREG_LOSS_MODEL.md). */

const money = (v) => (v == null ? '—' : `$${Math.round(v).toLocaleString()}`);

/* Accepts "policy,paid" lines separated by comma, tab or semicolon; a header
   row and blank lines are ignored. */
export function parseSettled(text) {
  const out = [];
  for (const line of String(text || '').split(/\r?\n/)) {
    const parts = line.split(/[,\t;]/).map(s => s.trim()).filter(Boolean);
    if (parts.length < 2) continue;
    const amount = Number(parts[parts.length - 1].replace(/[$\s]/g, '').replace(/,/g, ''));
    if (!Number.isFinite(amount) || amount < 0) continue;
    out.push({ policy_number: parts[0], paid_amount: amount });
  }
  return out;
}

export default function LossCalibrationCard({ portfolioId, exposure, onDone }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState('');
  const settled = parseSettled(text);
  const applied = exposure?.storm_calibration;

  const apply = async () => {
    setBusy(true); setErr(''); setResult(null);
    try {
      const r = await api.calibrateLosses(portfolioId, settled);
      setResult(r);
      onDone?.();
    } catch (e) {
      setErr(typeof e?.detail === 'string' ? e.detail : 'Could not calibrate. Check the policy numbers match the analysed portfolio.');
    } finally { setBusy(false); }
  };

  return (
    <div style={{ marginTop: 22 }}>
      <div style={sec}>Calibrate to settled claims</div>
      <div style={{ ...muted, marginBottom: 8 }}>
        Paste building claims your team has already settled (policy number, building amount paid; contents are not calibrated). Altis learns this storm's
        dollar scale and rescales the rest of the book. Typical accuracy of the storm total: about 9% after 50
        settled claims, 6% after 200, 2.5% after 1,000 (tested on four held-out storms).
      </div>
      {applied && !result && (
        <div style={{ ...muted, color: 'var(--teal)', marginBottom: 8 }}>
          Calibrated: ×{applied.factor} from {applied.n_settled.toLocaleString()} settled claims.
        </div>
      )}
      <textarea
        value={text} onChange={e => setText(e.target.value)} rows={4}
        placeholder={'policy_number,building_paid\nPOL-1042,18250\nPOL-1077,4120'}
        style={{
          width: '100%', boxSizing: 'border-box', padding: '8px 10px', fontSize: '0.72rem',
          fontFamily: 'var(--font-mono, monospace)', background: 'var(--input-bg)', color: 'var(--text-primary)',
          border: '1px solid var(--wa-10)', borderRadius: 'var(--r-sm)', resize: 'vertical',
        }}
      />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 6 }}>
        <span style={muted}>{settled.length.toLocaleString()} claims read</span>
        <button onClick={apply} disabled={busy || settled.length === 0} style={{
          padding: '6px 14px', fontSize: '0.72rem', fontWeight: 700, borderRadius: 'var(--r-sm)', fontFamily: 'var(--font)',
          cursor: busy || settled.length === 0 ? 'not-allowed' : 'pointer',
          background: 'rgba(168,212,230,0.12)', border: '1px solid rgba(168,212,230,0.3)', color: 'var(--teal)',
          opacity: settled.length === 0 ? 0.5 : 1,
        }}>{busy ? 'Calibrating…' : 'Calibrate'}</button>
      </div>
      {err && <div style={{ fontSize: '0.7rem', color: '#FFB347', marginTop: 8, lineHeight: 1.45 }}>{err}</div>}
      {result && (
        <div style={{ marginTop: 10, padding: '10px 12px', borderRadius: 'var(--r-md)',
                      background: 'rgba(76,175,130,0.06)', border: '1px solid rgba(76,175,130,0.22)' }}>
          <Row label="Storm scale factor" value={`×${result.factor} (95%: ${result.ci95[0]}–${result.ci95[1]})`} />
          <Row label="Settled claims matched" value={`${result.matched.toLocaleString()} of ${result.submitted.toLocaleString()}`} />
          <Row label="Book loss estimate now" value={money(result.new_totals.est_loss_mid_usd)} />
          {result.low_n && <div style={{ ...muted, color: '#FFB347', marginTop: 4 }}>Fewer than 100 claims: treat the factor as provisional.</div>}
          <div style={{ ...muted, marginTop: 6 }}>{result.note}</div>
        </div>
      )}
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: '0.7rem', padding: '2px 0' }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: 'var(--text-primary)', fontWeight: 700, textAlign: 'right' }}>{value}</span>
    </div>
  );
}

const sec = { fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.08em', color: 'var(--teal)', textTransform: 'uppercase', marginBottom: 8 };
const muted = { fontSize: '0.64rem', color: 'var(--text-muted)', lineHeight: 1.45 };
