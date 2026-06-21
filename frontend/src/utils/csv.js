/* csv.js — minimal CSV writer + browser download. No dependency needed
   for the flat, simple objects Altis deals with (no nested commas-in-commas
   beyond what quoting handles). */

function escapeCell(val) {
  if (val === null || val === undefined) return '';
  const s = String(val);
  if (/[",\n]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

export function toCSV(rows, columns) {
  if (!rows || rows.length === 0) return '';
  const cols = columns || Object.keys(rows[0]);
  const header = cols.join(',');
  const lines = rows.map(row => cols.map(c => escapeCell(row[c])).join(','));
  return [header, ...lines].join('\n');
}

export function downloadCSV(filename, rows, columns) {
  const csv  = toCSV(rows, columns);
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = filename.endsWith('.csv') ? filename : `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
