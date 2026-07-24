import { useState, useEffect, useCallback } from 'react';
import { api } from '../services/api.js';

/* OperationsPanel — closes the monitor → pipeline loop. The monitor service
   watches NHC/USGS feeds and, on a new flood event, enqueues a pipeline run
   here. Operators see the queue, can promote a run's status, and can manually
   queue a run for the active event. This is the "always-on" story: detection
   flows straight into analysis. */

const STATUS_STYLE = {
  queued:   { color: '#FFB347', bg: 'rgba(255,179,71,0.12)', border: 'rgba(255,179,71,0.3)' },
  running:  { color: 'var(--teal)', bg: 'rgba(168,212,230,0.12)', border: 'rgba(168,212,230,0.3)' },
  complete: { color: '#4CAF82', bg: 'rgba(76,175,130,0.12)', border: 'rgba(76,175,130,0.3)' },
  failed:   { color: '#FF4444', bg: 'rgba(255,68,68,0.12)', border: 'rgba(255,68,68,0.3)' },
};
const NEXT_STATUS = { queued: 'running', running: 'complete', complete: 'queued', failed: 'queued' };

function ago(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso.includes('T') ? iso + (iso.endsWith('Z') ? '' : 'Z') : iso.replace(' ', 'T') + 'Z');
    const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  } catch { return ''; }
}

export default function OperationsPanel({ selectedEventMeta }) {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    setLoading(true);
    api.getRuns().then(d => setRuns(d.runs || [])).catch(() => setRuns([])).finally(() => setLoading(false));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const queueForEvent = async () => {
    if (!selectedEventMeta) return;
    setBusy(true);
    try {
      await api.createRun({
        title: `Re-run ${selectedEventMeta.label}`,
        source: 'manual', event_id: selectedEventMeta.id,
        note: 'Queued from Operations panel',
      });
      refresh();
    } finally { setBusy(false); }
  };

  const advance = async (run) => {
    const next = NEXT_STATUS[run.status] || 'queued';
    await api.setRunStatus(run.id, next);
    refresh();
  };

  const queued = runs.filter(r => r.status === 'queued').length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '0 18px 12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <div style={{ fontSize: '0.92rem', fontWeight: 700, color: 'var(--text-primary)' }}>Operations</div>
          <button onClick={refresh} style={linkBtn}>↻ Refresh</button>
        </div>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 3 }}>
          Monitor → pipeline run queue
        </div>

        <div style={{
          marginTop: 12, padding: '10px 12px', borderRadius: 'var(--r-md)',
          background: 'rgba(168,212,230,0.04)', border: '1px solid rgba(168,212,230,0.12)',
          fontSize: '0.7rem', color: 'var(--text-secondary)', lineHeight: 1.5,
        }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#4CAF82', display: 'inline-block', animation: 'pulse 2s infinite' }} />
            <strong style={{ color: 'var(--text-primary)' }}>Monitor active</strong>
          </span>
          {' '}· watching NHC cyclones and USGS flood gauges. New events auto-queue a run.
          <div style={{ marginTop: 4, color: 'var(--text-muted)' }}>{queued} run{queued === 1 ? '' : 's'} queued</div>
        </div>

        <button onClick={queueForEvent} disabled={!selectedEventMeta || busy} style={{
          width: '100%', marginTop: 10, padding: '9px 0', borderRadius: 'var(--r-sm)',
          background: selectedEventMeta ? 'rgba(168,212,230,0.12)' : 'var(--wa-03)',
          border: `1px solid ${selectedEventMeta ? 'rgba(168,212,230,0.3)' : 'var(--wa-06)'}`,
          color: selectedEventMeta ? 'var(--teal)' : 'var(--text-disabled)',
          fontSize: '0.72rem', fontWeight: 700, fontFamily: 'var(--font)',
          cursor: selectedEventMeta && !busy ? 'pointer' : 'not-allowed',
        }}>
          {busy ? 'Queuing…' : selectedEventMeta ? `+ Queue run for ${selectedEventMeta.label}` : '+ Queue run (select an event)'}
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 16px' }}>
        {loading && runs.length === 0 && <div style={empty}>Loading runs…</div>}
        {!loading && runs.length === 0 && (
          <div style={empty}>
            No runs yet. Run <code style={{ color: 'var(--text-secondary)' }}>python monitor/monitor.py</code> to
            detect live events, or queue one above.
          </div>
        )}

        {runs.map(run => {
          const st = STATUS_STYLE[run.status] || STATUS_STYLE.queued;
          return (
            <div key={run.id} style={{
              marginBottom: 6, padding: '11px 12px', borderRadius: 'var(--r-md)',
              background: 'var(--wa-02)', border: '1px solid var(--wa-06)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                <div style={{ fontSize: '0.76rem', color: 'var(--text-bright)', fontWeight: 600, lineHeight: 1.35, flex: 1 }}>
                  {run.title}
                </div>
                <button onClick={() => advance(run)} title="Advance status" style={{
                  flexShrink: 0, padding: '2px 8px', borderRadius: 'var(--r-sm)',
                  fontSize: '0.6rem', fontWeight: 800, letterSpacing: '0.04em', textTransform: 'uppercase',
                  color: st.color, background: st.bg, border: `1px solid ${st.border}`,
                  cursor: 'pointer', fontFamily: 'var(--font)',
                }}>{run.status}</button>
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 6, fontSize: '0.62rem', color: 'var(--text-muted)', flexWrap: 'wrap' }}>
                <span>{run.source}</span>
                <span>·</span>
                <span>{ago(run.created_at)}</span>
                {run.bbox && <><span>·</span><span>bbox set</span></>}
              </div>
              {run.note && (
                <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', marginTop: 5, lineHeight: 1.45 }}>
                  {run.note}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const empty = { padding: '20px 10px', fontSize: '0.76rem', color: 'var(--text-muted)', lineHeight: 1.5 };
const linkBtn = {
  background: 'none', border: 'none', color: 'var(--teal)', fontSize: '0.7rem',
  fontWeight: 700, cursor: 'pointer', fontFamily: 'var(--font)', padding: 0,
};
