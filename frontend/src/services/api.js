/* api.js — All Altis backend API calls */

// Local dev: relative '/api', proxied to localhost:8000 by vite.config.js.
// Production build: set VITE_API_BASE_URL to the deployed backend's origin
// (e.g. https://altis-api.up.railway.app/api) — frontend and backend are
// deployed as two separate services, so a relative path would otherwise
// resolve against the frontend's own domain and 404.
const BASE = import.meta.env.VITE_API_BASE_URL || '/api';

// ── Demo access code (see AccessGate.jsx) ──────────────────────────────────
// The backend's shared-secret gate (X-Demo-Password header) is optional —
// off entirely unless the host sets DEMO_PASSWORD. When it's on, AccessGate
// collects the code once and stores it here (+ sessionStorage, so a reload
// within the tab doesn't re-prompt) for every request below to attach.
const _STORAGE_KEY = 'altis_demo_code';
let _demoCode = (typeof sessionStorage !== 'undefined'
  && sessionStorage.getItem(_STORAGE_KEY)) || null;

export function setDemoCode(code) {
  _demoCode = code || null;
  if (typeof sessionStorage !== 'undefined') {
    if (_demoCode) sessionStorage.setItem(_STORAGE_KEY, _demoCode);
    else sessionStorage.removeItem(_STORAGE_KEY);
  }
}
export function getDemoCode() { return _demoCode; }

function authHeaders(extra = {}) {
  return _demoCode ? { ...extra, 'X-Demo-Password': _demoCode } : extra;
}

// Every network call in this file goes through this wrapper so the access
// code (when set) is attached uniformly — callers below are unaffected.
function authFetch(path, options = {}) {
  return fetch(`${BASE}${path}`, {
    ...options,
    headers: authHeaders(options.headers || {}),
  });
}

async function get(path) {
  const r = await authFetch(path);
  if (!r.ok) {
    // Surface the backend's human-readable reason, not just a status code.
    let detail = `Request failed (${r.status})`;
    try {
      const j = await r.json();
      if (j?.detail) detail = j.detail;
    } catch { /* non-JSON error body */ }
    const err = new Error(detail);
    err.detail = detail;
    err.status = r.status;
    throw err;
  }
  return r.json();
}

export const api = {
  /* Events */
  getEvents:      ()       => get('/events'),
  getProperties:  (id)     => get(`/events/${id}/properties`),
  getTiles:       (id)     => get(`/events/${id}/tiles`),
  getStormTrack:  (id)     => get(`/events/${id}/storm-track`),  // throws on 404 → caller treats as "no track"

  /* Pre-event flood risk score (1–5), no event date needed */
  getRiskScore:   (id)     => get(`/portfolio/${id}/risk-score`),

  /* Fast PIF zone summary — pure bbox math, no GEE, returns instantly */
  zoneSummary: (id, bbox) =>
    authFetch(`/portfolio/${id}/zone-summary`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bbox }),
    }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),

  /* Per-portfolio analysis settings (event date + window) */
  getPortfolioSettings: (id) => get(`/portfolio/${id}/settings`),
  savePortfolioSettings: (id, settings) =>
    authFetch(`/portfolio/${id}/settings`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }).then(r => r.json()),

  /* SAR thumbnails — pass coords + eventDate to get REAL imagery (live path) */
  getSarThumbnails: (pid, view = 'sar', opts = {}) => {
    const q = new URLSearchParams({ view });
    if (opts.lat != null && opts.lon != null && opts.eventDate) {
      q.set('lat', opts.lat); q.set('lon', opts.lon); q.set('event_date', opts.eventDate);
    }
    return get(`/sar-thumbnails/${pid}?${q.toString()}`);
  },

  /* Portfolio */
  downloadTemplate: ()     => authFetch('/portfolio/template'),

  uploadPortfolio: (file) => {
    const form = new FormData();
    form.append('file', file);
    return authFetch('/portfolio/upload', { method: 'POST', body: form })
      .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); });
  },

  confirmPortfolioUpload: (uploadId, mapping) =>
    authFetch(`/portfolio/${uploadId}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mapping }),
    }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),

  getPortfolio:     (id)         => get(`/portfolio/${id}`),
  listPortfolios:   ()           => get('/portfolios'),
  analyzePortfolio: (id, evtId)  => authFetch(`/portfolio/${id}/analyze/${evtId}`,
                                          { method: 'POST' }).then(r => r.json()),
  getResults:       (id, evtId)  => get(`/portfolio/${id}/results/${evtId}`),

  /* Live, global satellite analysis (real Sentinel-1, any location + date) */
  geeStatus:    ()                 => get('/gee-status'),
  analyzeLive:  (id, payload)      => authFetch(`/portfolio/${id}/analyze-live`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),

  /* Dispatch queue (severity × coverage) */
  getDispatchQueue: (evtId, classes) =>
    get(`/events/${evtId}/dispatch-queue${classes ? `?classes=${encodeURIComponent(classes)}` : ''}`),

  /* Adjuster feedback loop */
  submitFeedback: (propertyId, payload) =>
    authFetch(`/property/${propertyId}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),
  getFeedback: (evtId) => get(`/events/${evtId}/feedback`),

  /* Pipeline runs (monitor → pipeline loop) */
  getRuns:   ()        => get('/runs'),
  createRun: (payload) => authFetch('/runs', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),
  setRunStatus: (runId, status) => authFetch(`/runs/${runId}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  }).then(r => r.json()),

  /* Reports */
  getValidationReport: (evtId) => authFetch(`/validation/${evtId}`)
    .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),
  getAccuracyCalibration: (evtId) => authFetch(`/accuracy/${evtId}`)
    .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),
  // JS-driven download (fetch + blob), not a raw <a href>: a plain browser
  // navigation can't carry our X-Demo-Password header, so the link would
  // 401 the instant the access gate is turned on.
  downloadEventReport: (evtId) => authFetch(`/events/${evtId}/report`)
    .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.blob(); }),
  // Reinsurance-format catastrophe report for an analyzed portfolio. Only
  // ever reachable via direct URL before — that would 401 the moment the
  // demo access gate is on, since a bare navigation can't carry our header.
  downloadCatReport: (portfolioId, evtId = 'live') =>
    authFetch(`/portfolio/${portfolioId}/cat-report/${evtId}`)
      .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.blob(); }),

  /* Chat ("Ask about this area") */
  sendChatMessage: (payload) => authFetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),

  /* One-click adjuster note draft (LLM w/ deterministic fallback) */
  draftNote: (property, eventLabel) => authFetch('/property/draft-note', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ property, event_label: eventLabel || null }),
  }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),

  /* Health — open even when the demo gate is on (hosting healthchecks) */
  health: () => get('/health'),

  /* Gate probe — 401 when the demo gate is on and no valid code is stored.
     AccessGate must use this, NOT health(): health is deliberately
     unauthenticated so platform healthchecks pass, which makes it blind to
     the gate. */
  authCheck: () => get('/auth-check'),
};
