/* Shared input validation for analysis settings. */

/* Sentinel-1 has data from October 2014; future dates have no imagery. */
export function validateEventDate(dateStr) {
  if (!dateStr) return 'Pick the flood/landfall date.';
  const d = new Date(dateStr + 'T00:00:00Z');
  if (Number.isNaN(d.getTime())) return 'Invalid date.';
  if (d < new Date('2014-10-01')) return 'Satellite archive starts October 2014 (Sentinel-1 launch).';
  if (d > new Date()) return 'That date is in the future — no imagery exists yet.';
  return '';
}

export const clampDays = (v, lo, hi, dflt) => {
  const n = parseInt(v, 10);
  return Number.isNaN(n) ? dflt : Math.max(lo, Math.min(hi, n));
};
