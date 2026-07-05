/* api.js — All Altis backend API calls */

const BASE = '/api';

async function get(path) {
  const r = await fetch(`${BASE}${path}`);
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
    fetch(`${BASE}/portfolio/${id}/zone-summary`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bbox }),
    }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),

  /* Per-portfolio analysis settings (event date + window) */
  getPortfolioSettings: (id) => get(`/portfolio/${id}/settings`),
  savePortfolioSettings: (id, settings) =>
    fetch(`${BASE}/portfolio/${id}/settings`, {
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
  downloadTemplate: ()     => fetch(`${BASE}/portfolio/template`),

  uploadPortfolio: (file) => {
    const form = new FormData();
    form.append('file', file);
    return fetch(`${BASE}/portfolio/upload`, { method: 'POST', body: form })
      .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); });
  },

  confirmPortfolioUpload: (uploadId, mapping) =>
    fetch(`${BASE}/portfolio/${uploadId}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mapping }),
    }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),

  getPortfolio:     (id)         => get(`/portfolio/${id}`),
  listPortfolios:   ()           => get('/portfolios'),
  analyzePortfolio: (id, evtId)  => fetch(`${BASE}/portfolio/${id}/analyze/${evtId}`,
                                          { method: 'POST' }).then(r => r.json()),
  getResults:       (id, evtId)  => get(`/portfolio/${id}/results/${evtId}`),

  /* Live, global satellite analysis (real Sentinel-1, any location + date) */
  geeStatus:    ()                 => get('/gee-status'),
  analyzeLive:  (id, payload)      => fetch(`${BASE}/portfolio/${id}/analyze-live`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),

  /* Dispatch queue (severity × coverage) */
  getDispatchQueue: (evtId, classes) =>
    get(`/events/${evtId}/dispatch-queue${classes ? `?classes=${encodeURIComponent(classes)}` : ''}`),

  /* Adjuster feedback loop */
  submitFeedback: (propertyId, payload) =>
    fetch(`${BASE}/property/${propertyId}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),
  getFeedback: (evtId) => get(`/events/${evtId}/feedback`),

  /* Pipeline runs (monitor → pipeline loop) */
  getRuns:   ()        => get('/runs'),
  createRun: (payload) => fetch(`${BASE}/runs`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),
  setRunStatus: (runId, status) => fetch(`${BASE}/runs/${runId}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  }).then(r => r.json()),

  /* Reports */
  getValidationReport: (evtId) => fetch(`${BASE}/validation/${evtId}`)
    .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),
  getAccuracyCalibration: (evtId) => fetch(`${BASE}/accuracy/${evtId}`)
    .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),
  eventReportUrl: (evtId) => `${BASE}/events/${evtId}/report`,

  /* Chat ("Ask about this area") */
  sendChatMessage: (payload) => fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),

  /* Health */
  health: () => get('/health'),
};
