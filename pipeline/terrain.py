"""
terrain.py — DEM grids and terrain hydrology, pure numpy.

The physics phases all stand on the same ground model:

  * Grid          a north-up lon/lat raster with bilinear sampling and
                  metric cell sizes.
  * terrarium     decode + mosaic for the free, key-less AWS Terrain Tiles
                  (USGS 3DEP ~10 m in the US, SRTM/other national DEMs
                  elsewhere). No GEE needed, so the same code runs on Railway,
                  in tests, and in offline baking.
  * drainage()    priority-flood depression filling (Barnes et al. 2014) that
                  also records, for every cell, the neighbour it drains to —
                  a flow tree consistent with the filled surface, with flats
                  resolved by flood order. From that one pass we get flow
                  accumulation and HAND.
  * HAND          Height Above Nearest Drainage (Rennó et al. 2008; Nobre et
                  al. 2011): a cell's elevation minus the elevation of the
                  stream cell its water reaches. The single most predictive
                  terrain feature for fluvial/pluvial flood exposure, used by
                  NOAA's National Water Model inundation mapping.

No network here; backend/geodata.py fetches the tiles.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import numpy as np

EARTH_R = 6371008.8
_NEIGH = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_NEIGH4 = [(-1, 0), (0, -1), (0, 1), (1, 0)]


@dataclass
class Grid:
    """North-up raster: data[row, col], row 0 at `north`, col 0 at `west`."""
    data: np.ndarray
    west: float
    north: float
    dx: float          # degrees longitude per cell
    dy: float          # degrees latitude per cell (positive)

    @property
    def shape(self):
        return self.data.shape

    @property
    def south(self):
        return self.north - self.dy * self.data.shape[0]

    @property
    def east(self):
        return self.west + self.dx * self.data.shape[1]

    @property
    def cell_m(self) -> tuple[float, float]:
        """(dx_m, dy_m) at the grid's central latitude."""
        lat = math.radians((self.north + self.south) / 2)
        return (math.radians(self.dx) * EARTH_R * math.cos(lat),
                math.radians(self.dy) * EARTH_R)

    def rc(self, lon, lat):
        """Fractional (row, col) of cell centres."""
        col = (np.asarray(lon, float) - self.west) / self.dx - 0.5
        row = (self.north - np.asarray(lat, float)) / self.dy - 0.5
        return row, col

    def lonlat(self, row, col):
        return (self.west + (np.asarray(col) + 0.5) * self.dx,
                self.north - (np.asarray(row) + 0.5) * self.dy)

    def sample(self, lon, lat, method: str = 'bilinear'):
        """Values at points; NaN outside the grid."""
        row, col = self.rc(lon, lat)
        row, col = np.atleast_1d(row), np.atleast_1d(col)
        h, w = self.data.shape
        out = np.full(row.shape, np.nan)
        if method == 'nearest':
            r = np.rint(row).astype(int)
            c = np.rint(col).astype(int)
            ok = (r >= 0) & (r < h) & (c >= 0) & (c < w)
            out[ok] = self.data[r[ok], c[ok]]
            return out
        r0 = np.floor(row).astype(int)
        c0 = np.floor(col).astype(int)
        fr, fc = row - r0, col - c0
        ok = (r0 >= -1) & (r0 < h) & (c0 >= -1) & (c0 < w)
        r0c, r1c = np.clip(r0, 0, h - 1), np.clip(r0 + 1, 0, h - 1)
        c0c, c1c = np.clip(c0, 0, w - 1), np.clip(c0 + 1, 0, w - 1)
        d = self.data
        v = (d[r0c, c0c] * (1 - fr) * (1 - fc) + d[r0c, c1c] * (1 - fr) * fc +
             d[r1c, c0c] * fr * (1 - fc) + d[r1c, c1c] * fr * fc)
        out[ok] = v[ok]
        return out

    def resample(self, west, south, east, north, res_deg_x, res_deg_y=None):
        """Bilinear resample onto a new regular grid covering the box."""
        res_deg_y = res_deg_y or res_deg_x
        w = max(1, int(round((east - west) / res_deg_x)))
        h = max(1, int(round((north - south) / res_deg_y)))
        cols = west + (np.arange(w) + 0.5) * res_deg_x
        rows = north - (np.arange(h) + 0.5) * res_deg_y
        lon, lat = np.meshgrid(cols, rows)
        vals = self.sample(lon.ravel(), lat.ravel()).reshape(h, w)
        return Grid(vals, west, north, res_deg_x, res_deg_y)


# ── Web-mercator tiles ───────────────────────────────────────────────────────

def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(-85.0511, min(85.0511, lat)))
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def tile_to_lonlat(x, y, z):
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def decode_terrarium(rgb: np.ndarray) -> np.ndarray:
    """Terrarium PNG → metres: (R*256 + G + B/256) − 32768."""
    rgb = rgb.astype(np.float64)
    return rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0


def zoom_for_resolution(res_m: float, lat: float) -> int:
    """Smallest tile zoom whose pixels are at least as fine as res_m."""
    for z in range(8, 16):
        px = 2 * math.pi * EARTH_R * math.cos(math.radians(lat)) / (256 * 2 ** z)
        if px <= res_m:
            return z
    return 15


def mosaic_tiles(bbox, z, get_tile, tile_px: int = 256) -> Grid:
    """
    Mosaic mercator tiles covering bbox=[w,s,e,n] and resample to a regular
    lon/lat grid at (roughly) the tiles' native resolution. `get_tile(z,x,y)`
    returns a float array (tile_px × tile_px) or None for a missing tile.
    """
    w, s, e, n = bbox
    x0, y0 = lonlat_to_tile(w, n, z)
    x1, y1 = lonlat_to_tile(e, s, z)
    tx0, ty0, tx1, ty1 = int(x0), int(y0), int(x1), int(y1)
    mh = (ty1 - ty0 + 1) * tile_px
    mw = (tx1 - tx0 + 1) * tile_px
    merc = np.full((mh, mw), np.nan)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            t = get_tile(z, tx, ty)
            if t is not None:
                merc[(ty - ty0) * tile_px:(ty - ty0 + 1) * tile_px,
                     (tx - tx0) * tile_px:(tx - tx0 + 1) * tile_px] = t
    # Target lon/lat grid at the native pixel pitch.
    lat_c = (s + n) / 2
    px_m = 2 * math.pi * EARTH_R * math.cos(math.radians(lat_c)) / (tile_px * 2 ** z)
    res_x = 360.0 / (tile_px * 2 ** z)
    res_y = math.degrees(px_m / EARTH_R)
    gw = max(1, int(math.ceil((e - w) / res_x)))
    gh = max(1, int(math.ceil((n - s) / res_y)))
    lons = w + (np.arange(gw) + 0.5) * res_x
    lats = n - (np.arange(gh) + 0.5) * res_y
    # Continuous mercator pixel coords of each target cell centre.
    nz = 2 ** z
    px = ((lons + 180.0) / 360.0 * nz - tx0) * tile_px - 0.5
    lat_r = np.radians(np.clip(lats, -85.0511, 85.0511))
    py = ((1.0 - np.arcsinh(np.tan(lat_r)) / math.pi) / 2.0 * nz - ty0) * tile_px - 0.5
    PX, PY = np.meshgrid(px, py)
    merc_grid = Grid(merc, 0.0, 0.0, 1.0, 1.0)
    # Reuse Grid.sample in pixel space: west=−0.5 offset handled by rc().
    vals = merc_grid.sample(PX.ravel() + 0.5, -(PY.ravel() + 0.5)).reshape(gh, gw)
    return Grid(vals, w, n, res_x, res_y)


# ── Hydrology ────────────────────────────────────────────────────────────────

def fill_nan(dem: np.ndarray) -> np.ndarray:
    """Replace NaN holes with the nearest-row/col mean so routing can proceed."""
    d = dem.copy()
    if not np.isnan(d).any():
        return d
    fill = np.nanmedian(d) if np.isfinite(d).any() else 0.0
    d[np.isnan(d)] = fill
    return d


def drainage(dem: np.ndarray, cell_m: tuple[float, float] | None = None, outlets=None,
             connectivity: int = 8):
    """
    Priority-flood from the grid edge (and any `outlets` mask, e.g. the open
    sea) inward. Returns
      filled : depression-filled elevation (m)
      parent : flat index each cell drains to (−1 for edge outlets)
      order  : flat indices in flood order (outlets first); every cell's parent
               appears before it, so iterating `order` is downstream→upstream.
    """
    d = fill_nan(np.asarray(dem, float))
    neigh = _NEIGH if connectivity == 8 else _NEIGH4
    h, w = d.shape
    n = h * w
    flat = d.ravel()
    filled = flat.copy()
    parent = np.full(n, -1, dtype=np.int64)
    closed = np.zeros(n, dtype=bool)
    heap = []
    counter = 0
    for r in range(h):
        for c in (0, w - 1) if w > 1 else (0,):
            i = r * w + c
            if not closed[i]:
                closed[i] = True
                heapq.heappush(heap, (filled[i], counter, i)); counter += 1
    for c in range(w):
        for r in (0, h - 1) if h > 1 else (0,):
            i = r * w + c
            if not closed[i]:
                closed[i] = True
                heapq.heappush(heap, (filled[i], counter, i)); counter += 1
    if outlets is not None:
        for i in np.flatnonzero(np.asarray(outlets, bool).ravel()):
            if not closed[i]:
                closed[i] = True
                heapq.heappush(heap, (filled[i], counter, int(i))); counter += 1
    order = np.empty(n, dtype=np.int64)
    k = 0
    while heap:
        z, _, i = heapq.heappop(heap)
        order[k] = i; k += 1
        r, c = divmod(i, w)
        for dr, dc in neigh:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w:
                j = rr * w + cc
                if not closed[j]:
                    closed[j] = True
                    if filled[j] < z:
                        filled[j] = z
                    parent[j] = i
                    heapq.heappush(heap, (filled[j], counter, j)); counter += 1
    return filled.reshape(h, w), parent, order


def d4_conditioned(dem: np.ndarray, outlets=None) -> np.ndarray:
    """
    Hydrologically condition a DEM for a 4-neighbour flow model (the Bates
    router moves water only across orthogonal faces). An 8-neighbour fill
    lets valleys connect diagonally, which a D4 model sees as a dam; so for
    every diagonal link of the D8 flow tree the lower orthogonal neighbour
    is breached down to the upstream cell's level, then any remaining D4 pit
    is filled. Returns the conditioned elevation.
    """
    filled, parent, order = drainage(dem, outlets=outlets, connectivity=8)
    z = filled.ravel().copy()
    h, w = filled.shape
    for i in order:
        p = parent[i]
        if p < 0:
            continue
        r, c = divmod(int(i), w)
        pr, pc = divmod(int(p), w)
        if r != pr and c != pc:                      # diagonal link
            k1, k2 = r * w + pc, pr * w + c          # the two orthogonal detours
            k = k1 if z[k1] <= z[k2] else k2
            if z[k] > z[i]:
                z[k] = z[i]
    out, _, _ = drainage(z.reshape(h, w), outlets=outlets, connectivity=4)
    return out


def flow_accumulation(parent: np.ndarray, order: np.ndarray, weights=None) -> np.ndarray:
    """Upstream cell count (or weighted sum) including the cell itself."""
    acc = np.ones(parent.shape[0]) if weights is None else np.asarray(weights, float).ravel().copy()
    for i in order[::-1]:
        p = parent[i]
        if p >= 0:
            acc[p] += acc[i]
    return acc


def hand(dem: np.ndarray, cell_m: tuple[float, float], stream_area_km2: float = 1.0):
    """
    Height Above Nearest Drainage. Stream cells are those with contributing
    area ≥ stream_area_km2. Returns (hand_m, acc_km2, stream_mask).
    """
    d = fill_nan(np.asarray(dem, float))
    filled, parent, order = drainage(d)
    acc = flow_accumulation(parent, order)
    cell_km2 = cell_m[0] * cell_m[1] / 1e6
    acc_km2 = acc * cell_km2
    stream = acc_km2 >= stream_area_km2
    ref = np.arange(parent.shape[0])
    for i in order:                     # downstream before upstream
        if not stream[i]:
            p = parent[i]
            ref[i] = ref[p] if p >= 0 else i
    flatd = d.ravel()
    hand_m = np.maximum(0.0, flatd - flatd[ref])
    shape = d.shape
    return hand_m.reshape(shape), acc_km2.reshape(shape), stream.reshape(shape)


def slope_deg(dem: np.ndarray, cell_m: tuple[float, float]) -> np.ndarray:
    d = fill_nan(np.asarray(dem, float))
    gy, gx = np.gradient(d, cell_m[1], cell_m[0])
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def relative_elevation(dem: np.ndarray, radius_cells: int) -> np.ndarray:
    """Elevation minus the local minimum within a square window (m)."""
    from scipy.ndimage import minimum_filter
    d = fill_nan(np.asarray(dem, float))
    return d - minimum_filter(d, size=2 * radius_cells + 1, mode='nearest')
