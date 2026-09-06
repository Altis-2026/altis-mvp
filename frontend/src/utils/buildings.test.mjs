/* Tests for utils/buildings.js — the 3D property-inspect geometry.
 *
 * Runs on node's built-in test runner (`npm test` in frontend/), so the
 * frontend gains real tests without adding a test framework to a bundle that
 * is already 2.1 MB.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  metersPerDegree, openRing, closeRing, centroid, areaM2, placeholderRing,
  orientedBox, roofRiseM, gableSlabs, expandRing, buildingFeatures, shadeColor,
  FT_TO_M,
} from './buildings.js';

/* A 20m x 10m house in Houston, long axis east-west. */
const HOUSE_LAT = 29.68;
const HOUSE_LON = -95.47;
function house(lengthM = 20, widthM = 10) {
  const { mx, my } = metersPerDegree(HOUSE_LAT);
  const dx = lengthM / 2 / mx;
  const dy = widthM / 2 / my;
  return closeRing([
    [HOUSE_LON - dx, HOUSE_LAT - dy], [HOUSE_LON + dx, HOUSE_LAT - dy],
    [HOUSE_LON + dx, HOUSE_LAT + dy], [HOUSE_LON - dx, HOUSE_LAT + dy],
  ]);
}

// ── Ring primitives ─────────────────────────────────────────────────────────

test('closeRing closes an open ring and is idempotent', () => {
  const open = [[0, 0], [1, 0], [1, 1]];
  const closed = closeRing(open);
  assert.deepEqual(closed[0], closed[closed.length - 1]);
  assert.deepEqual(closeRing(closed), closed);
});

test('openRing strips the closing vertex', () => {
  assert.equal(openRing([[0, 0], [1, 0], [1, 1], [0, 0]]).length, 3);
});

test('centroid of a rectangle is its middle', () => {
  const c = centroid([[-2, -1], [2, -1], [2, 1], [-2, 1], [-2, -1]]);
  assert.ok(Math.abs(c[0]) < 1e-9);
  assert.ok(Math.abs(c[1]) < 1e-9);
});

test('centroid of a degenerate ring falls back to the vertex mean', () => {
  const c = centroid([[0, 0], [1, 1], [2, 2]]);
  assert.ok(Math.abs(c[0] - 1) < 1e-9 && Math.abs(c[1] - 1) < 1e-9);
});

test('areaM2 matches the footprint it was built from', () => {
  const a = areaM2(house(20, 10));
  assert.ok(Math.abs(a - 200) < 2, `expected ~200 m², got ${a}`);
});

test('placeholderRing has the requested dimensions', () => {
  const a = areaM2(placeholderRing(HOUSE_LON, HOUSE_LAT, 11, 14));
  assert.ok(Math.abs(a - 154) < 3, `expected ~154 m², got ${a}`);
});

// ── Oriented bounding box ───────────────────────────────────────────────────

test('orientedBox recovers the dimensions of an axis-aligned house', () => {
  const box = orientedBox(house(20, 10));
  assert.ok(Math.abs(box.length - 20) < 0.5, `length ${box.length}`);
  assert.ok(Math.abs(box.width - 10) < 0.5, `width ${box.width}`);
});

test('orientedBox length is always the longer side', () => {
  for (const [l, w] of [[20, 10], [10, 20], [15, 15]]) {
    const box = orientedBox(house(l, w));
    assert.ok(box.length >= box.width - 1e-6);
  }
});

test('orientedBox follows a rotated footprint', () => {
  // Rotate a 24m x 8m house by 30° and check the box follows it.
  const angle = Math.PI / 6;
  const { mx, my } = metersPerDegree(HOUSE_LAT);
  const corners = [[-12, -4], [12, -4], [12, 4], [-12, 4]].map(([u, v]) => {
    const x = u * Math.cos(angle) - v * Math.sin(angle);
    const y = u * Math.sin(angle) + v * Math.cos(angle);
    return [HOUSE_LON + x / mx, HOUSE_LAT + y / my];
  });
  const box = orientedBox(closeRing(corners));
  assert.ok(Math.abs(box.length - 24) < 0.6, `length ${box.length}`);
  assert.ok(Math.abs(box.width - 8) < 0.6, `width ${box.width}`);
  const deg = ((box.angle * 180) / Math.PI % 180 + 180) % 180;
  assert.ok(Math.abs(deg - 30) < 3, `angle ${deg}°`);
});

test('orientedBox centre sits on the footprint centre', () => {
  const box = orientedBox(house());
  assert.ok(Math.abs(box.center[0] - HOUSE_LON) < 1e-6);
  assert.ok(Math.abs(box.center[1] - HOUSE_LAT) < 1e-6);
});

test('orientedBox refuses a degenerate ring', () => {
  assert.equal(orientedBox([[0, 0], [1, 1]]), null);
});

// ── Roof ────────────────────────────────────────────────────────────────────

test('roofRiseM scales with width but stays in its clamps', () => {
  assert.ok(roofRiseM(10) > roofRiseM(4));
  assert.ok(roofRiseM(2) >= 1.0);          // a narrow cottage still gets a roof
  assert.ok(roofRiseM(200) <= 3.0);        // a warehouse doesn't get a cathedral
});

test('roofRiseM never lets the roof out-tower the walls', () => {
  // A wide footprint on a single-storey house: the pitch alone would give a
  // roof taller than the house, which reads as top-heavy.
  const eave = 3.2;
  assert.ok(roofRiseM(40, { eaveM: eave }) <= eave * 0.85 + 1e-9);
  assert.ok(roofRiseM(40, { eaveM: eave }) < roofRiseM(40));
});

test('gable slabs stack contiguously from eave to ridge', () => {
  const eave = 3.2;
  const slabs = gableSlabs(house(), eave, { slabs: 8 });
  assert.equal(slabs.length, 8);
  assert.equal(slabs[0].base, eave);
  for (let i = 0; i < slabs.length; i++) {
    assert.ok(slabs[i].height > slabs[i].base, 'each slab has positive thickness');
    if (i > 0) {
      assert.ok(Math.abs(slabs[i].base - slabs[i - 1].height) < 1e-9,
                'no gap or overlap between slabs');
    }
  }
  const top = slabs[slabs.length - 1];
  const expected = eave + roofRiseM(orientedBox(house()).width, { eaveM: eave });
  assert.ok(Math.abs(top.height - expected) < 0.35, `top ${top.height} vs ${expected}`);
});

test('the roof sits on the walls, not on the bounding box', () => {
  /* Regression: roofing an L-shaped house with its oriented bounding box puts
     far more roof on the building than there is building, and it renders as a
     mushroom cap overhanging the walls. The eave slab must track the actual
     footprint area (plus a small overhang), not the box. */
  const { mx, my } = metersPerDegree(HOUSE_LAT);
  const m = ([x, y]) => [HOUSE_LON + x / mx, HOUSE_LAT + y / my];
  const L = closeRing([[-12, -8], [12, -8], [12, 0], [2, 0], [2, 8], [-12, 8]].map(m));

  const footprint = areaM2(L);
  const box = orientedBox(L);
  const boxArea = box.length * box.width;
  assert.ok(boxArea > footprint * 1.3, 'the L really is much smaller than its box');

  const eaveSlab = gableSlabs(L, 3.2, { slabs: 8 })[0];
  const roofArea = areaM2(eaveSlab.ring);
  assert.ok(roofArea < footprint * 1.35,
            `eave slab ${roofArea.toFixed(0)} m² should track the ${footprint.toFixed(0)} m² footprint, not the ${boxArea.toFixed(0)} m² box`);
  assert.ok(roofArea > footprint, 'but it should still overhang slightly');
});

test('gable slabs narrow monotonically toward the ridge', () => {
  const slabs = gableSlabs(house(), 3.2, { slabs: 6 });
  const areas = slabs.map(s => areaM2(s.ring));
  for (let i = 1; i < areas.length; i++) {
    assert.ok(areas[i] < areas[i - 1], `slab ${i} should be narrower`);
  }
});

test('gable slabs keep the ridge along the long axis', () => {
  // The topmost slab should still run the length of the house (plus the eave
  // overhang at each end), but be thin across it.
  const slabs = gableSlabs(house(20, 10), 3.2, { slabs: 8 });
  const top = orientedBox(slabs[slabs.length - 1].ring);
  assert.ok(top.length > 20 && top.length < 21.5, `ridge length ${top.length}`);
  assert.ok(top.width < 2.5, `ridge width ${top.width}`);
});

test('gableSlabs returns nothing for an unusable ring', () => {
  assert.deepEqual(gableSlabs([[0, 0], [1, 1]], 3), []);
});

// ── Water apron ─────────────────────────────────────────────────────────────

test('expandRing grows the footprint outward', () => {
  const base = house();
  const grown = expandRing(base, 1.6);
  assert.ok(areaM2(grown) > areaM2(base));
  assert.equal(grown.length, base.length);
  assert.deepEqual(grown[0], grown[grown.length - 1]);
});

test('expandRing with no distance returns the ring unchanged in shape', () => {
  const base = house();
  assert.ok(Math.abs(areaM2(expandRing(base, 0)) - areaM2(base)) < 1e-6);
});

// ── Feature assembly ────────────────────────────────────────────────────────

const BUILDING = {
  property_id: 'P1', ring: house(), height_m: 3.2,
  footprint_source: 'osm', height_source: 'typology',
};

test('a dry property gets walls and a roof but no water', () => {
  const feats = buildingFeatures(BUILDING,
    { property_id: 'P1', max_depth_ft: 0, impact_class: 'Remote-Deny' },
    { color: '#6B8FA3' });
  const kinds = feats.map(f => f.properties.kind);
  assert.equal(kinds.filter(k => k === 'wall').length, 1);
  assert.equal(kinds.filter(k => k === 'roof').length, 8);
  assert.equal(kinds.filter(k => k === 'water').length, 0);
});

test('a flooded property gets a water plane at the modeled depth', () => {
  const depthFt = 3.2;
  const feats = buildingFeatures(BUILDING,
    { property_id: 'P1', max_depth_ft: depthFt, impact_class: 'Dispatch' },
    { color: '#FF4444' });
  const water = feats.find(f => f.properties.kind === 'water');
  assert.ok(water, 'water feature exists');
  assert.equal(water.properties.base, 0);
  assert.ok(Math.abs(water.properties.height - depthFt * FT_TO_M) < 1e-9);
  // Water stands on the lot, so it is wider than the house itself.
  assert.ok(areaM2(water.geometry.coordinates[0]) > areaM2(BUILDING.ring));
});

test('a trace depth does not draw water', () => {
  const feats = buildingFeatures(BUILDING,
    { property_id: 'P1', max_depth_ft: 0.05 }, {});
  assert.equal(feats.filter(f => f.properties.kind === 'water').length, 0);
});

test('deep water rises above the eaves — the roof stays visible above it', () => {
  const feats = buildingFeatures(BUILDING, { property_id: 'P1', max_depth_ft: 12 }, {});
  const water = feats.find(f => f.properties.kind === 'water');
  const wall = feats.find(f => f.properties.kind === 'wall');
  const roofTop = Math.max(...feats.filter(f => f.properties.kind === 'roof')
                                   .map(f => f.properties.height));
  assert.ok(water.properties.height > wall.properties.height);
  assert.ok(roofTop > wall.properties.height);
});

test('every feature carries the property id and provenance for the UI', () => {
  const feats = buildingFeatures(BUILDING,
    { property_id: 'P1', address: '123 Elm St', max_depth_ft: 2, impact_class: 'Dispatch' },
    {});
  for (const f of feats) {
    assert.equal(f.properties.property_id, 'P1');
    assert.equal(f.properties.footprint_source, 'osm');
    assert.equal(f.properties.height_source, 'typology');
    assert.equal(f.geometry.type, 'Polygon');
    assert.ok(f.properties.height >= f.properties.base);
  }
});

test('a building with an unusable ring produces no features', () => {
  assert.deepEqual(buildingFeatures({ ring: [[0, 0], [1, 1]] }, { property_id: 'X' }), []);
  assert.deepEqual(buildingFeatures({}, { property_id: 'X' }), []);
});

test('a missing height falls back to a one-storey wall', () => {
  const feats = buildingFeatures({ ring: house() }, { property_id: 'P1' }, {});
  assert.equal(feats.find(f => f.properties.kind === 'wall').properties.height, 3.2);
});

// ── Colour ──────────────────────────────────────────────────────────────────

test('shadeColor darkens and lightens within range', () => {
  assert.equal(shadeColor('#808080', -0.5), '#404040');
  assert.equal(shadeColor('#ffffff', 0.5), '#ffffff');   // clamped, not overflowed
  assert.equal(shadeColor('#000000', -0.5), '#000000');
});

test('shadeColor passes through anything it cannot parse', () => {
  assert.equal(shadeColor('rebeccapurple', -0.2), 'rebeccapurple');
});
