/* api.js — All Altis backend API calls */

const BASE = '/api';

async function get(path) {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`API ${path}: ${r.status}`);
  return r.json();
}

export const api = {
  /* Events */
  getEvents:      ()       => get('/events'),
  getProperties:  (id)     => get(`/events/${id}/properties`),
  getTiles:       (id)     => get(`/events/${id}/tiles`),

  /* SAR thumbnails */
  getSarThumbnails: (pid, view = 'sar') => get(`/sar-thumbnails/${pid}?view=${view}`),

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

  /* Health */
  health: () => get('/health'),
};
