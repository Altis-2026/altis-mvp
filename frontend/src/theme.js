/* theme.js — light/dark theme state. The default is dark (the brand look);
   light mode is for daylight conference rooms and iPads. Persisted per
   browser, applied via [data-theme] on <html> so CSS variables cascade
   everywhere including portals and the access gate. */
const KEY = 'altis_theme';

export function getTheme() {
  try {
    const t = localStorage.getItem(KEY);
    return t === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(KEY, theme); } catch { /* private mode */ }
}

export function initTheme() {
  applyTheme(getTheme());
}
