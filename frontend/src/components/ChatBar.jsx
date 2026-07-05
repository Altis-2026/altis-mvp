import { useState, useRef, useEffect } from 'react';
import { api } from '../services/api.js';

/* "Ask about this area" — floating chat bar below the globe.
   Grounds questions in whatever event/property is currently on screen. */
export default function ChatBar({ eventMeta, eventStats, selectedProperty, portfolioId }) {
  const [open,     setOpen]     = useState(false);
  const [input,    setInput]    = useState('');
  const [messages, setMessages] = useState([]); // { role, content }
  const [sending,  setSending]  = useState(false);
  const [error,    setError]    = useState(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, sending]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setError(null);
    setOpen(true);
    const next = [...messages, { role: 'user', content: text }];
    setMessages(next);
    setInput('');
    setSending(true);
    try {
      const { reply } = await api.sendChatMessage({
        message: text,
        history: next,
        event_meta: eventMeta || null,
        event_stats: eventStats || null,
        property: selectedProperty || null,
        portfolio_id: portfolioId || null,
      });
      setMessages(m => [...m, { role: 'assistant', content: reply }]);
    } catch (e) {
      setError(e?.detail || 'Could not reach the assistant.');
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };

  return (
    <div style={{
      position: 'fixed', left: '50%', bottom: 22, transform: 'translateX(-50%)',
      zIndex: 25, width: 'min(560px, 92vw)', display: 'flex', flexDirection: 'column',
      alignItems: 'center', gap: 8,
    }}>
      {open && (messages.length > 0 || error) && (
        <div
          ref={scrollRef}
          className="glass anim-slide-in-up"
          style={{
            width: '100%', maxHeight: 280, overflowY: 'auto', borderRadius: 'var(--r-lg)',
            padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10,
          }}
        >
          {messages.map((m, i) => (
            <div key={i} style={{
              display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
            }}>
              <div style={{
                maxWidth: '85%', fontSize: '0.78rem', lineHeight: 1.5,
                padding: '8px 12px', borderRadius: 'var(--r-md)',
                color: m.role === 'user' ? 'var(--bg)' : 'var(--text-primary)',
                background: m.role === 'user' ? 'var(--teal)' : 'rgba(255,255,255,0.04)',
                border: m.role === 'user' ? 'none' : '1px solid var(--border)',
              }}>
                {m.content}
              </div>
            </div>
          ))}
          {sending && (
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
              Altis is thinking…
            </div>
          )}
          {error && (
            <div style={{
              fontSize: '0.72rem', color: 'var(--dispatch)', background: 'var(--dispatch-dim)',
              border: '1px solid rgba(255,68,68,0.25)', borderRadius: 'var(--r-sm)', padding: '6px 10px',
            }}>
              {error}
            </div>
          )}
        </div>
      )}

      <div
        className="glass"
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: 8,
          borderRadius: 999, padding: '6px 6px 6px 18px',
          boxShadow: '0 8px 30px rgba(0,0,0,0.45)',
        }}
      >
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder={
            selectedProperty ? `Ask about ${selectedProperty.address || selectedProperty.property_id}…`
              : eventMeta ? `Ask about ${eventMeta.label}…`
              : 'Ask about this area…'
          }
          style={{
            flex: 1, background: 'transparent', border: 'none', outline: 'none',
            color: 'var(--text-primary)', fontSize: '0.82rem', fontFamily: 'var(--font)',
          }}
        />
        <button
          onClick={send}
          disabled={sending || !input.trim()}
          aria-label="Send"
          style={{
            width: 34, height: 34, borderRadius: '50%', border: 'none', flexShrink: 0,
            background: input.trim() ? 'linear-gradient(135deg, var(--teal), #D4B068)' : 'rgba(255,255,255,0.06)',
            color: 'var(--bg)', cursor: input.trim() ? 'pointer' : 'default',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            opacity: sending ? 0.6 : 1, transition: 'background 0.15s, opacity 0.15s',
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
            <path d="M3 11L21 3L13 21L11 13L3 11Z" stroke="currentColor" strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round" fill="currentColor" />
          </svg>
        </button>
      </div>
    </div>
  );
}
