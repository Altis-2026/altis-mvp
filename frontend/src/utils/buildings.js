/* buildings.js — Geometry for the 3D property-inspect view.
 *
 * Turns a building footprint (a real OpenStreetMap ring from /api/buildings,
 * or a placeholder box) into the polygons Mapbox extrudes: walls, a stepped
 * gable roof, and a water plane at the modeled flood depth.
 *
 * The roof is built as a stack of progressively narrower slabs rather than
 * true sloped geometry. Each slab is an ordinary `fill-extrusion` polygon, so
 * the whole scene stays inside Mapbox's own renderer — no Three.js, no custom
 * WebGL layer, no new dependency on a 2.1 MB bundle — and every surface can
 * still be recolored per building by a data-driven expression, which is the
 * entire point of a triage map. Eight slabs read as a roofline at the pitched
 * camera angles this view uses.
 *
 * All heights are metres above local ground: Mapbox anchors `fill-extrusion`
 * features to the terrain surface under them, so a house on a sloped lot sits
 * on the slope and its water plane stays parallel to the ground it floods.
 *
 * Pure functions, no Mapbox imports — unit-tested by buildings.test.mjs under
 * `npm test` (node's built-in runner, no test framework dependency).
 */

const M_PER_DEG_LAT = 110540;
const M_PER_DEG_LON_EQ = 111320;

export const FT_TO_M = 0.3048;

/* Metres per degree of longitude/latitude at a given latitude. */
export function metersPerDegree(lat) {
  return {
    mx: M_PER_DEG_LON_EQ * Math.cos((lat * Math.PI) / 180),
    my: M_PER_DEG_LAT,
  };
}

/* Drop a ring's duplicated closing vertex, if it has one. */
export function openRing(ring) {
  const pts = (ring || []).map(p => [Number(p[0]), Number(p[1])]);
  while (pts.length > 1 &&
         pts[0][0] === pts[pts.length - 1][0] &&
         pts[0][1] === pts[pts.length - 1][1]) {
    pts.pop();
  }
  return pts;
}

/* Close a ring the way GeoJSON requires (last point repeats the first). */
export function closeRing(ring) {
  const pts = openRing(ring);
  if (pts.length === 0) return [];
  return [...pts, [pts[0][0], pts[0][1]]];
}

/* Area-weighted centroid of a [[lon, lat], …] ring.
 *
 * The shoelace cross-products are evaluated RELATIVE TO THE FIRST VERTEX. Run
 * on raw lon/lat the terms are ~95 × ~30 while the differences that matter are
 * ~1e-7 of that, and float64 cancellation puts the centroid of a 20 m house at
 * -95.47° roughly 14 m from where it belongs — enough to hang every roof off
 * the side of its walls. Shifting to a local origin first keeps full
 * precision. Same reason `areaM2` below does it. */
export function centroid(ring) {
  const pts = openRing(ring);
  if (pts.length === 0) return null;
  const mean = () => [
    pts.reduce((s, p) => s + p[0], 0) / pts.length,
    pts.reduce((s, p) => s + p[1], 0) / pts.length,
  ];
  if (pts.length < 3) return mean();

  const [ox, oy] = pts[0];
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < pts.length; i++) {
    const x0 = pts[i][0] - ox, y0 = pts[i][1] - oy;
    const j = (i + 1) % pts.length;
    const x1 = pts[j][0] - ox, y1 = pts[j][1] - oy;
    const cross = x0 * y1 - x1 * y0;
    a += cross;
    cx += (x0 + x1) * cross;
    cy += (y0 + y1) * cross;
  }
  if (Math.abs(a) < 1e-20) return mean();
  return [ox + cx / (3 * a), oy + cy / (3 * a)];
}

/* Ring area in square metres (shoelace, projected locally). */
export function areaM2(ring) {
  const pts = openRing(ring);
  if (pts.length < 3) return 0;
  const lat0 = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const { mx, my } = metersPerDegree(lat0);
  const [ox, oy] = pts[0];
  let a = 0;
  for (let i = 0; i < pts.length; i++) {
    const x0 = (pts[i][0] - ox) * mx, y0 = (pts[i][1] - oy) * my;
    const j = (i + 1) % pts.length;
    const x1 = (pts[j][0] - ox) * mx, y1 = (pts[j][1] - oy) * my;
    a += x0 * y1 - x1 * y0;
  }
  return Math.abs(a) / 2;
}

/* An axis-aligned rectangle centred on a point — the placeholder footprint,
 * mirroring pipeline.building_context.placeholder_footprint. */
export function placeholderRing(lon, lat, widthM = 11, depthM = 14) {
  const { mx, my } = metersPerDegree(lat);
  const dx = widthM / 2 / Math.max(mx, 1e-9);
  const dy = depthM / 2 / Math.max(my, 1e-9);
  return [
    [lon - dx, lat - dy], [lon + dx, lat - dy],
    [lon + dx, lat + dy], [lon - dx, lat + dy],
    [lon - dx, lat - dy],
  ];
}

/* Minimum-ish oriented bounding box of a footprint, found by PCA on the
 * vertices: the principal axis is the building's long axis, which is the ridge
 * line a gable roof runs along.
 *
 * Returns { center: [lon, lat], angle (radians, long axis, from east),
 *           length (m, along the long axis), width (m, across it) }. */
export function orientedBox(ring) {
  const pts = openRing(ring);
  if (pts.length < 3) return null;

  const c = centroid(ring);
  const { mx, my } = metersPerDegree(c[1]);
  const local = pts.map(([lon, lat]) => [(lon - c[0]) * mx, (lat - c[1]) * my]);

  let sxx = 0, syy = 0, sxy = 0;
  for (const [x, y] of local) { sxx += x * x; syy += y * y; sxy += x * y; }
  const n = local.length;
  sxx /= n; syy /= n; sxy /= n;

  // Principal axis of the vertex covariance.
  const angle = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  const cos = Math.cos(angle), sin = Math.sin(angle);

  let uMin = Infinity, uMax = -Infinity, vMin = Infinity, vMax = -Infinity;
  for (const [x, y] of local) {
    const u = x * cos + y * sin;
    const v = -x * sin + y * cos;
    if (u < uMin) uMin = u;
    if (u > uMax) uMax = u;
    if (v < vMin) vMin = v;
    if (v > vMax) vMax = v;
  }

  // Re-centre on the box itself rather than the vertex centroid.
  const uc = (uMin + uMax) / 2;
  const vc = (vMin + vMax) / 2;
  const cxLocal = uc * cos - vc * sin;
  const cyLocal = uc * sin + vc * cos;

  let length = uMax - uMin;
  let width = vMax - vMin;
  let boxAngle = angle;
  // Convention: `length` is always the longer side, so the ridge runs along it.
  if (width > length) {
    [length, width] = [width, length];
    boxAngle = angle + Math.PI / 2;
  }

  return {
    center: [c[0] + cxLocal / mx, c[1] + cyLocal / my],
    angle: boxAngle,
    length,
    width,
  };
}

/* Squash a ring toward the ridge line of its oriented box: every vertex keeps
 * its position ALONG the long axis and moves a fraction `shrink` of the way
 * across it, toward the ridge. At shrink = 1 the ring is the footprint itself;
 * at shrink → 0 it collapses onto the ridge line.
 *
 * Tapering the real footprint (rather than the bounding box) is what keeps the
 * roof sitting exactly on the walls. Roofing an L-shaped house with its
 * bounding box instead puts ~70% more roof area than there is house, and it
 * reads as a mushroom cap floating over the walls. */
function squashToRidge(ring, box, shrink) {
  const { mx, my } = metersPerDegree(box.center[1]);
  const cos = Math.cos(box.angle), sin = Math.sin(box.angle);
  const pts = openRing(ring);

  const moved = pts.map(([lon, lat]) => {
    const dx = (lon - box.center[0]) * mx;
    const dy = (lat - box.center[1]) * my;
    const u = dx * cos + dy * sin;          // along the ridge — preserved
    const v = (-dx * sin + dy * cos) * shrink;  // across it — pulled in
    const x = u * cos - v * sin;
    const y = u * sin + v * cos;
    return [box.center[0] + x / mx, box.center[1] + y / my];
  });
  return closeRing(moved);
}

/* Roof rise for a footprint: a fixed pitch over the building's half-width,
 * clamped so a wide warehouse doesn't grow a cathedral roof and a narrow
 * cottage still gets a visible one. Also held below `maxFrac` of the wall
 * height — a roof taller than the house it sits on looks top-heavy, and wide
 * footprints hit that limit constantly. */
export function roofRiseM(widthM, { pitch = 0.34, min = 1.0, max = 3.0,
                                    eaveM = null, maxFrac = 0.85 } = {}) {
  let rise = (Math.max(widthM, 0) / 2) * pitch;
  let ceiling = max;
  if (eaveM != null && eaveM > 0) ceiling = Math.min(ceiling, eaveM * maxFrac);
  return Math.min(Math.max(ceiling, min), Math.max(rise, min));
}

/* Stepped gable roof over a footprint.
 *
 * Slabs stack from the eaves to the ridge, each narrower and higher than the
 * one below, so the silhouette reads as two sloped planes meeting at a ridge
 * line down the building's long axis. The bottom slab matches the footprint
 * (plus a small eave overhang, as a real roof has), and each one above tapers
 * toward the ridge. Returns [{ ring, base, height }, …] in metres above local
 * ground. */
export function gableSlabs(ring, eaveM, { slabs = 8, rise = null, pitch,
                                          overhangM = 0.35 } = {}) {
  const box = orientedBox(ring);
  if (!box || box.width <= 0 || box.length <= 0) return [];

  const n = Math.max(1, Math.floor(slabs));
  const totalRise = rise == null
    ? roofRiseM(box.width, { eaveM, ...(pitch ? { pitch } : {}) })
    : rise;

  // The eaves overhang the walls slightly; the box is re-derived from the
  // overhung ring so the taper stays centred on it.
  const eaveRing = overhangM > 0 ? expandRing(ring, overhangM) : closeRing(ring);
  const eaveBox = orientedBox(eaveRing) || box;

  const out = [];
  for (let k = 0; k < n; k++) {
    out.push({
      ring: squashToRidge(eaveRing, eaveBox, 1 - k / n),
      base: eaveM + (k / n) * totalRise,
      height: eaveM + ((k + 1) / n) * totalRise,
    });
  }
  return out;
}

/* Push a ring outward from its centroid by `meters` — the little apron of
 * water around a flooded house, so the water plane reads as standing water on
 * the lot rather than a coloured band painted on the walls. */
export function expandRing(ring, meters) {
  const pts = openRing(ring);
  if (pts.length < 3 || !(meters > 0)) return closeRing(ring);
  const c = centroid(ring);
  const { mx, my } = metersPerDegree(c[1]);

  const grown = pts.map(([lon, lat]) => {
    const dx = (lon - c[0]) * mx;
    const dy = (lat - c[1]) * my;
    const d = Math.hypot(dx, dy);
    if (d < 1e-6) return [lon, lat];
    const s = (d + meters) / d;
    return [c[0] + (dx * s) / mx, c[1] + (dy * s) / my];
  });
  return closeRing(grown);
}

/* One building record (from /api/buildings, or a locally generated
 * placeholder) + its property → the GeoJSON features the globe renders.
 *
 * Emits three kinds of feature, all carrying the property_id so a click can
 * open the right drawer:
 *   kind='wall'  — the footprint extruded to eave height, coloured by triage
 *   kind='roof'  — the stepped gable slabs
 *   kind='water' — the flood plane, ground → depth, only when depth > 0
 */
export function buildingFeatures(building, property, options = {}) {
  const {
    color = '#6B8FA3',
    slabs = 8,
    apronM = 1.6,
    minDepthFt = 0.1,
  } = options;

  const ring = closeRing(building?.ring || []);
  if (ring.length < 4) return [];

  const eave = Math.max(2.2, Number(building?.height_m) || 3.2);
  const depthFt = Number(property?.max_depth_ft) || 0;
  const propertyId = String(property?.property_id ?? building?.property_id ?? '');

  const shared = {
    property_id: propertyId,
    address: property?.address ?? null,
    impact_class: property?.impact_class ?? null,
    max_depth_ft: depthFt,
    height_source: building?.height_source ?? 'default',
    footprint_source: building?.footprint_source ?? 'placeholder',
  };

  const features = [{
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [ring] },
    properties: { ...shared, kind: 'wall', base: 0, height: eave, color },
  }];

  for (const slab of gableSlabs(ring, eave, { slabs })) {
    features.push({
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [slab.ring] },
      properties: {
        ...shared, kind: 'roof',
        base: slab.base, height: slab.height,
        color: shadeColor(color, -0.22),
      },
    });
  }

  if (depthFt >= minDepthFt) {
    features.push({
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [expandRing(ring, apronM)] },
      properties: {
        ...shared, kind: 'water',
        base: 0, height: Math.max(0.05, depthFt * FT_TO_M),
        color: '#2E86C1',
      },
    });
  }

  return features;
}

/* Darken (amount < 0) or lighten (amount > 0) a #rrggbb colour. Used to set
 * the roof a shade off the walls so the two planes read as separate surfaces
 * under flat lighting. */
export function shadeColor(hex, amount) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ''));
  if (!m) return hex;
  const num = parseInt(m[1], 16);
  const clamp = v => Math.max(0, Math.min(255, Math.round(v)));
  const r = clamp(((num >> 16) & 255) * (1 + amount));
  const g = clamp(((num >> 8) & 255) * (1 + amount));
  const b = clamp((num & 255) * (1 + amount));
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, '0')}`;
}
