import { Component } from 'react';

/* ErrorBoundary — a rendering bug in any panel must never white-screen a
   live demo. Shows a friendly recovery card and logs the real error to the
   console for the engineer. */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('Altis UI error:', error, info?.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{
        position: 'fixed', inset: 0, zIndex: 100, display: 'flex',
        alignItems: 'center', justifyContent: 'center',
        background: 'rgba(0,0,4,0.92)',
      }}>
        <div style={{
          maxWidth: 420, padding: '28px 32px', textAlign: 'center',
          background: 'rgba(6,8,16,0.97)', border: '1px solid rgba(168,212,230,0.2)',
          borderRadius: 16,
        }}>
          <div style={{ fontSize: '2rem', marginBottom: 12 }}>🛰</div>
          <div style={{ fontSize: '1rem', fontWeight: 800, color: '#fff', marginBottom: 8 }}>
            Something hiccuped in the display
          </div>
          <p style={{ fontSize: '0.8rem', color: '#8B9AA3', lineHeight: 1.6, marginBottom: 18 }}>
            Your data and analysis results are safe on the server — this was a
            display issue only. Reload to pick up right where you left off.
          </p>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '11px 28px', border: 'none', borderRadius: 8,
              background: 'linear-gradient(135deg, #DDF1FB, #8FC4E8)', color: '#000',
              fontSize: '0.84rem', fontWeight: 800, cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            Reload Altis
          </button>
        </div>
      </div>
    );
  }
}
