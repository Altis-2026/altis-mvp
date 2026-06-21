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
  getSarThumbnails: (pid)  => get(`/sar-thumbnails/${pid}`),

  /* Portfolio */
  downloadTemplate: ()     => fetch(`${BASE}/portfolio/template`),

  uploadPortfolio: (file) => {
    const form = new FormData();
    form.append('file', file);
    return fetch(`${BASE}/portfolio/upload`, { method: 'POST', body: form })
      .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); });
  },

  getPortfolio:     (id)         => get(`/portfolio/${id}`),
  listPortfolios:   ()           => get('/portfolios'),
  analyzePortfolio: (id, evtId)  => fetch(`${BASE}/portfolio/${id}/analyze/${evtId}`,
                                          { method: 'POST' }).then(r => r.json()),
  getResults:       (id, evtId)  => get(`/portfolio/${id}/results/${evtId}`),

  /* Reports */
  getValidationReport: (evtId) => fetch(`${BASE}/validation/${evtId}`)
    .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); }),

  /* Health */
  health: () => get('/health'),
};
