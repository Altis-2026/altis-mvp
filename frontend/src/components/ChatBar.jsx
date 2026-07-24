import { useState, useRef, useEffect, useCallback } from 'react';
import { api } from '../services/api.js';
import { AltisLogo } from './Brand.jsx';
import { useIsMobile } from '../hooks/useIsMobile.js';

/* The Altis assistant — one place to ask anything: operate the product,
   query the book, read trends. Voice-enabled: hold a real conversation
   hands-free (Web Speech recognition in, synthesized speech out). Replies
   are plain professional prose (the backend enforces no-markdown; we also
   strip any stray formatting defensively before display/speech). */

/* Strip markdown artifacts so text reads/speaks cleanly even if the model slips. */
function toPlainText(s) {
  return String(s || '')
    .replace(/```[\s\S]*?```/g, m => m.replace(/```\w*\n?/g, ''))
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/\*([^*\n]+)\*/g, '$1')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/^\s*[-*•]\s+/gm, '')
    .trim();
}

const SpeechRecognitionImpl =
  typeof window !== 'undefined'
    ? (window.SpeechRecognition || window.webkitSpeechRecognition)
    : null;

export default function ChatBar({ eventMeta, eventStats, selectedProperty, portfolioId, panelOpen }) {
  const isMobile = useIsMobile();
  const [expanded, setExpanded] = useState(false); // transcript panel visible
  const [focused,  setFocused]  = useState(false); // input focused → show chips
  const [input,     setInput]     = useState('');
  const [messages,  setMessages]  = useState([]); // { role, content }
  const [sending,   setSending]   = useState(false);
  const [error,     setError]     = useState(null);
  const [listening, setListening] = useState(false);
  const [speakBack, setSpeakBack] = useState(false); // voice replies on/off
  const scrollRef  = useRef(null);
  const recogRef   = useRef(null);
  const voiceAsked = useRef(false); // last message came in by voice → speak the answer

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, sending]);

  // Never leave the tab talking after unmount.
  useEffect(() => () => {
    try { window.speechSynthesis?.cancel(); recogRef.current?.abort(); } catch { /* noop */ }
  }, []);

  const speak = useCallback((text) => {
    if (!window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.04;
    u.pitch = 1.0;
    // Prefer a natural en voice when the browser offers one.
    const voices = window.speechSynthesis.getVoices();
    const pick = voices.find(v => /en[-_]/i.test(v.lang) && /natural|neural|premium/i.test(v.name))
              || voices.find(v => /en[-_]US/i.test(v.lang));
    if (pick) u.voice = pick;
    window.speechSynthesis.speak(u);
  }, []);

  const send = useCallback(async (forcedText) => {
    const text = (forcedText ?? input).trim();
    if (!text || sending) return;
    setError(null);
    setExpanded(true);   // show the transcript once there's a conversation
    setFocused(false);   // hide the starter chips
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
      const clean = toPlainText(reply);
      setMessages(m => [...m, { role: 'assistant', content: clean }]);
      if (speakBack || voiceAsked.current) speak(clean);
    } catch (e) {
      setError(e?.detail || 'Could not reach the assistant.');
    } finally {
      setSending(false);
      voiceAsked.current = false;
    }
  }, [input, sending, messages, eventMeta, eventStats, selectedProperty,
      portfolioId, speakBack, speak]);

  /* ── Voice input ─────────────────────────────────────────────── */
  const stopListening = useCallback(() => {
    try { recogRef.current?.stop(); } catch { /* noop */ }
    setListening(false);
  }, []);

  const startListening = useCallback(() => {
    if (!SpeechRecognitionImpl) return;
    try { window.speechSynthesis?.cancel(); } catch { /* noop */ }
    const r = new SpeechRecognitionImpl();
    r.lang = 'en-US';
    r.interimResults = true;
    r.continuous = false;
    let finalText = '';
    r.onresult = (e) => {
      let interim = '';
      for (const res of e.results) {
        if (res.isFinal) finalText += res[0].transcript;
        else interim += res[0].transcript;
      }
      setInput(finalText + interim);
    };
    r.onend = () => {
      setListening(false);
      const said = finalText.trim();
      if (said) {
        voiceAsked.current = true; // spoken question → spoken answer
        send(said);
      }
    };
    r.onerror = () => setListening(false);
    recogRef.current = r;
    setListening(true);
    setInput('');
    r.start();
  }, [send]);

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };

  const suggestions = [
    selectedProperty ? 'Should we dispatch an adjuster to this property?' : null,
    portfolioId ? 'What does the flood-zone mix of this book look like?' : null,
    portfolioId ? 'Which cities are driving our estimated losses?' : null,
    eventMeta && !portfolioId ? `Summarize ${eventMeta.label} for leadership.` : null,
    'How do I upload a portfolio?',
    !eventMeta && !portfolioId ? 'What can Altis do?' : null,
  ].filter(Boolean).slice(0, 3);

  const hasConversation = messages.length > 0 || error;
  // On a phone the transcript, side panels, and the property drawer each want
  // the whole screen; keep the assistant out of the way (state preserved via
  // display:none) whenever one of those is open.
  const hidden = isMobile && (panelOpen || !!selectedProperty);

  return (
    <div style={{
      position: 'fixed', bottom: isMobile ? 12 : 22, zIndex: 25,
      ...(isMobile
        ? { left: RAIL_W + 4, right: 8 }
        : { left: '50%', transform: 'translateX(-50%)', width: 'min(600px, 92vw)' }),
      display: hidden ? 'none' : 'flex', flexDirection: 'column',
      alignItems: 'stretch', gap: 8,
    }}>
      {expanded && (hasConversation || sending) && (
        <div
          className="glass anim-slide-in-up"
          style={{
            width: '100%', display: 'flex', flexDirection: 'column',
            maxHeight: isMobile ? '52vh' : 360, borderRadius: 'var(--r-lg)', overflow: 'hidden',
          }}
        >
          {/* Transcript header — minimize / clear */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '8px 10px 8px 12px', borderBottom: '1px solid var(--border)', flexShrink: 0,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <AltisLogo size={16} idSuffix="Hdr" />
              <span style={{ fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.06em', color: 'var(--text-secondary)' }}>
                Altis assistant
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              {messages.length > 0 && (
                <button onClick={() => { setMessages([]); setError(null); setExpanded(false); }}
                  aria-label="Clear conversation" title="Clear conversation" style={iconBtn}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                  </svg>
                </button>
              )}
              <button onClick={() => setExpanded(false)}
                aria-label="Minimize assistant" title="Minimize" style={iconBtn}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M6 9l6 6 6-6"/>
                </svg>
              </button>
            </div>
          </div>

          {/* Scrolling messages */}
          <div ref={scrollRef} style={{
            flex: 1, minHeight: 0, overflowY: 'auto', padding: '12px 14px',
            display: 'flex', flexDirection: 'column', gap: 10,
          }}>
            {messages.map((m, i) => (
              <div key={i} style={{
                display: 'flex', gap: 8, alignItems: 'flex-start',
                justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
              }}>
                {m.role === 'assistant' && (
                  <div style={{ flexShrink: 0, marginTop: 2 }}>
                    <AltisLogo size={18} idSuffix={`Msg${i}`} />
                  </div>
                )}
                <div style={{
                  maxWidth: '85%', fontSize: '0.8rem', lineHeight: 1.55,
                  padding: '8px 12px', borderRadius: 'var(--r-md)',
                  whiteSpace: 'pre-wrap',
                  color: m.role === 'user' ? 'var(--bg)' : 'var(--text-primary)',
                  background: m.role === 'user' ? 'var(--teal)' : 'var(--wa-04)',
                  border: m.role === 'user' ? 'none' : '1px solid var(--border)',
                }}>
                  {m.content}
                </div>
              </div>
            ))}
            {sending && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <AltisLogo size={18} idSuffix="Think" />
                <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                  Altis is thinking…
                </span>
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
        </div>
      )}

      {/* Suggestion chips — context-aware starting points on focus */}
      {focused && !expanded && messages.length === 0 && !sending && (
        <div className="anim-fade-in" style={{
          display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: isMobile ? 'flex-start' : 'center',
        }}>
          {suggestions.map(s => (
            <button key={s} onMouseDown={e => e.preventDefault()} onClick={() => send(s)} style={{
              padding: '6px 12px', borderRadius: 999, cursor: 'pointer',
              background: 'var(--panel)', border: '1px solid rgba(168,212,230,0.2)',
              color: 'var(--teal)', fontSize: '0.68rem', fontWeight: 600,
              fontFamily: 'var(--font)', backdropFilter: 'blur(10px)',
            }}>
              {s}
            </button>
          ))}
        </div>
      )}

      <div
        className="glass"
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: 6,
          borderRadius: 999, padding: '6px 6px 6px 14px',
          boxShadow: '0 8px 30px rgba(0,0,0,0.45)',
          border: listening ? '1px solid rgba(168,212,230,0.55)' : undefined,
        }}
      >
        {/* Logo, or an expand toggle when a minimized conversation exists */}
        {hasConversation ? (
          <button
            onClick={() => setExpanded(v => !v)}
            aria-label={expanded ? 'Minimize conversation' : 'Show conversation'}
            title={expanded ? 'Minimize conversation' : 'Show conversation'}
            style={{ ...iconBtn, flexShrink: 0 }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--teal)"
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                 style={{ transform: expanded ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>
              <path d="M18 15l-6-6-6 6"/>
            </svg>
          </button>
        ) : (
          <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center' }}>
            <AltisLogo size={20} idSuffix="Bar" />
          </div>
        )}
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 150)}
          onKeyDown={onKeyDown}
          placeholder={
            listening ? 'Listening…'
              : selectedProperty ? `Ask about ${selectedProperty.address || selectedProperty.property_id}…`
              : portfolioId ? 'Ask about your book, this event, or how to use Altis…'
              : eventMeta ? `Ask about ${eventMeta.label}, or how to use Altis…`
              : 'Ask Altis anything: your book, an event, or how to use the product…'
          }
          style={{
            flex: 1, background: 'transparent', border: 'none', outline: 'none',
            color: 'var(--text-primary)', fontSize: '0.82rem', fontFamily: 'var(--font)',
          }}
        />

        {/* Voice replies toggle */}
        {typeof window !== 'undefined' && 'speechSynthesis' in window && (
          <button
            onClick={() => {
              const next = !speakBack;
              setSpeakBack(next);
              if (!next) window.speechSynthesis.cancel();
            }}
            aria-label={speakBack ? 'Turn spoken replies off' : 'Turn spoken replies on'}
            title={speakBack ? 'Spoken replies on' : 'Spoken replies off'}
            style={roundBtn(speakBack ? 'rgba(168,212,230,0.16)' : 'transparent')}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                 stroke={speakBack ? 'var(--teal)' : 'var(--wa-20)'} strokeWidth="1.8"
                 strokeLinecap="round" strokeLinejoin="round">
              <path d="M11 5L6 9H2v6h4l5 4V5z"/>
              {speakBack
                ? <path d="M15.5 8.5a5 5 0 0 1 0 7M18.4 5.6a9 9 0 0 1 0 12.8"/>
                : <line x1="16" y1="9" x2="22" y2="15"/>}
              {!speakBack && <line x1="22" y1="9" x2="16" y2="15"/>}
            </svg>
          </button>
        )}

        {/* Mic — voice input */}
        {SpeechRecognitionImpl && (
          <button
            onClick={listening ? stopListening : startListening}
            aria-label={listening ? 'Stop listening' : 'Ask by voice'}
            title={listening ? 'Stop listening' : 'Ask by voice'}
            style={{
              ...roundBtn(listening ? 'rgba(255,68,68,0.18)' : 'rgba(168,212,230,0.1)'),
              animation: listening ? 'pulse 1.2s ease-in-out infinite' : 'none',
            }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                 stroke={listening ? '#FF6B6B' : 'var(--teal)'} strokeWidth="1.8"
                 strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="2" width="6" height="12" rx="3"/>
              <path d="M5 10v1a7 7 0 0 0 14 0v-1M12 18v4"/>
            </svg>
          </button>
        )}

        <button
          onClick={() => send()}
          disabled={sending || !input.trim()}
          aria-label="Send"
          style={{
            width: 34, height: 34, borderRadius: '50%', border: 'none', flexShrink: 0,
            background: input.trim() ? 'linear-gradient(135deg, var(--teal), #D4B068)' : 'var(--wa-06)',
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

const RAIL_W = 56; // matches the sidebar rail so the bar clears it on phones

const roundBtn = (bg) => ({
  width: 30, height: 30, borderRadius: '50%', border: 'none', flexShrink: 0,
  background: bg, cursor: 'pointer', display: 'flex', alignItems: 'center',
  justifyContent: 'center', transition: 'background 0.15s',
});

const iconBtn = {
  width: 28, height: 28, borderRadius: 'var(--r-sm)', border: 'none',
  background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};
