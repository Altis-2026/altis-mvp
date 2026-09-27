/* floodReplay.js — paint the routed flood (uint8 depth grids) onto a canvas.
 *
 * The bake (pipeline/08_bake_flood_animation.py) ships frames as raw uint8
 * where each value is depth in 10 cm steps and 0 means dry, so a frame is just
 * a slice of one ArrayBuffer — no per-frame decode, no image requests.
 *
 * Painting goes through a cell-sized ImageData first, then scales up with
 * smoothing on, so the water edge reads as a surface rather than as pixels.
 * Mapbox drapes the result over the terrain via a canvas source.
 */

export const DRY = 0;

/* Depth ramp: shallow sheet water is pale and translucent, deep water is dark
 * and solid, so depth reads without a legend. Alpha rises with depth too —
 * shallow water should not hide the terrain under it. */
const STOPS = [
  { d: 0.10, c: [150, 224, 250], a: 110 },
  { d: 0.30, c: [ 96, 196, 240], a: 150 },
  { d: 0.80, c: [ 46, 150, 214], a: 185 },
  { d: 1.60, c: [ 28, 104, 184], a: 210 },
  { d: 3.00, c: [ 26,  68, 150], a: 228 },
  { d: 6.00, c: [ 32,  40, 122], a: 240 },
  { d: 12.0, c: [ 40,  22,  96], a: 248 },
];

/* 256-entry lookup keyed by the raw uint8 value, built once. */
export function buildRamp(depthStepM = 0.1) {
  const lut = new Uint8ClampedArray(256 * 4);
  for (let v = 1; v < 256; v++) {
    const depth = v * depthStepM;
    let lo = STOPS[0], hi = STOPS[STOPS.length - 1];
    for (let i = 0; i < STOPS.length - 1; i++) {
      if (depth >= STOPS[i].d && depth <= STOPS[i + 1].d) { lo = STOPS[i]; hi = STOPS[i + 1]; break; }
      if (depth < STOPS[0].d) { lo = hi = STOPS[0]; break; }
    }
    const span = hi.d - lo.d;
    const f = span > 0 ? Math.min(1, Math.max(0, (depth - lo.d) / span)) : 0;
    const o = v * 4;
    lut[o]     = lo.c[0] + (hi.c[0] - lo.c[0]) * f;
    lut[o + 1] = lo.c[1] + (hi.c[1] - lo.c[1]) * f;
    lut[o + 2] = lo.c[2] + (hi.c[2] - lo.c[2]) * f;
    lut[o + 3] = lo.a + (hi.a - lo.a) * f;
  }
  return lut;   // index 0 stays fully transparent = dry
}

/* One frame of the flood, ready to hand to a canvas source. */
export class FloodPainter {
  /* `minDepthM` hides the hairline of water that sits in every creek after
   * rain — true, but it is not the flood, and at 500 m it reads as noise
   * competing with the floodplain. `softenPx` blurs the upscaled edge so the
   * surface reads as water rather than as grid cells. */
  constructor(meta, buffer, { scale = 6, minDepthM = 0.25, softenPx = 2.5 } = {}) {
    const [frames, rows, cols] = meta.shape;
    this.frames = frames;
    this.rows = rows;
    this.cols = cols;
    this.cells = rows * cols;
    this.data = new Uint8Array(buffer);
    this.step = meta.depth_step_m || 0.1;
    this.lut = buildRamp(this.step);
    this.minValue = Math.max(1, Math.round(minDepthM / this.step));
    this.softenPx = softenPx;
    this.cell = document.createElement('canvas');
    this.cell.width = cols;
    this.cell.height = rows;
    this.cellCtx = this.cell.getContext('2d');
    this.image = this.cellCtx.createImageData(cols, rows);
    this.canvas = document.createElement('canvas');
    this.canvas.width = cols * scale;
    this.canvas.height = rows * scale;
    this.ctx = this.canvas.getContext('2d');
    this.ctx.imageSmoothingEnabled = true;
    this.ctx.imageSmoothingQuality = 'high';
    this.lastFrame = -1;
    this._wetCache = new Map();
  }

  /** Paint frame `i` (clamped). `fade` 0–1 scales alpha for entry/exit. */
  paint(i, fade = 1) {
    const f = Math.max(0, Math.min(this.frames - 1, Math.round(i)));
    const px = this.image.data;
    const src = this.data;
    const lut = this.lut;
    const base = f * this.cells;
    const a = Math.max(0, Math.min(1, fade));
    for (let k = 0; k < this.cells; k++) {
      const v = src[base + k];
      const o = k * 4;
      if (v < this.minValue) { px[o + 3] = 0; continue; }
      const l = v * 4;
      px[o]     = lut[l];
      px[o + 1] = lut[l + 1];
      px[o + 2] = lut[l + 2];
      px[o + 3] = lut[l + 3] * a;
    }
    this.cellCtx.putImageData(this.image, 0, 0);
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.ctx.filter = this.softenPx ? `blur(${this.softenPx}px)` : 'none';
    this.ctx.drawImage(this.cell, 0, 0, this.canvas.width, this.canvas.height);
    this.ctx.filter = 'none';
    this.lastFrame = f;
    return f;
  }

  /** Share of the grid under water in frame `i` — drives the HUD readout. */
  wetFraction(i) {
    const f = Math.max(0, Math.min(this.frames - 1, Math.round(i)));
    const hit = this._wetCache.get(f);
    if (hit !== undefined) return hit;
    const base = f * this.cells;
    let n = 0;
    for (let k = 0; k < this.cells; k++) if (this.data[base + k] >= this.minValue) n++;
    const frac = n / this.cells;
    this._wetCache.set(f, frac);
    return frac;
  }
}

/* Mapbox canvas sources take corners clockwise from the top-left. */
export function cornersFromBounds([w, s, e, n]) {
  return [[w, n], [e, n], [e, s], [w, s]];
}
