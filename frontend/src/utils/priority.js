/* priority.js — Dispatch-queue severity × coverage ranking (client mirror of
   backend/priority.py). Used for portfolio properties, which are ranked in the
   browser; event properties come pre-ranked from /events/{id}/dispatch-queue.
   Keep this formula in lockstep with priority.py. */

const SEVERITY_DEPTH_FT_CAP = 6.0;
const DEPTH_WEIGHT = 0.6;
const AREA_WEIGHT = 0.4;

function num(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : 0;
}

export function severity(depthFt, pctFlooded) {
  const depthNorm = Math.min(Math.max(num(depthFt), 0) / SEVERITY_DEPTH_FT_CAP, 1);
  const areaNorm = Math.min(Math.max(num(pctFlooded), 0) / 100, 1);
  return DEPTH_WEIGHT * depthNorm + AREA_WEIGHT * areaNorm;
}

export function exposureMultiplier(coverageAmount) {
  const c = num(coverageAmount);
  if (c <= 0) return 1.0;
  return 1.0 + Math.log10(1 + c) / 6.0;
}

export function priorityScore(depthFt, pctFlooded, coverageAmount) {
  return Math.round(severity(depthFt, pctFlooded) * exposureMultiplier(coverageAmount) * 1000) / 10;
}

/* Rank property objects in `classes` by descending priority, annotated with
   priority_score + priority_rank. Does not mutate the input array. */
export function rankDispatch(properties, classes = ['Dispatch', 'Review']) {
  const wanted = new Set(classes);
  const queue = properties
    .filter(p => wanted.has(p.impact_class))
    .map(p => ({
      ...p,
      priority_score: priorityScore(p.max_depth_ft, p.pct_flooded, p.coverage_amount),
    }))
    .sort((a, b) => b.priority_score - a.priority_score);
  queue.forEach((p, i) => { p.priority_rank = i + 1; });
  return queue;
}
