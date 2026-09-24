"""
hydraulic_route.py — Local-inertial 2D flood routing (Bates et al. 2010).

Sentinel-1 sees a flood once every 6–12 days, usually not at the peak. The
router fills the gaps with physics: shallow water moving over the terrain
under gravity and bed friction, forced by observed rainfall, then (in
backend/routing.py) calibrated so its state at the satellite's pass matches
what the satellite saw. Between and beyond the passes it gives depth over
time — peak depth, time wet, time above the floor.

Scheme: Bates, Horritt & Fewtrell (2010), "A simple inertial formulation of
the shallow water equations for efficient two-dimensional flood inundation
modelling", J. Hydrology 387 — as implemented in LISFLOOD-FP. On a regular
grid, depth h at cell centres and unit-width discharge q at cell faces:

    h_flow  = max(η_i, η_j) − max(z_i, z_j)            (η = z + h)
    q_new   = (q − g·h_flow·Δt·∂η/∂x) / (1 + g·Δt·n²·|q| / h_flow^(7/3))
    Δh      = Δt · (Σ q_in − Σ q_out) / Δx + Δt · rain

with three safeguards:
  * CFL:     Δt = C · min(Δx, Δy) / √(g · h_max),  C = 0.4
  * Froude:  |q| ≤ Fr_max · h_flow · √(g·h_flow),   Fr_max = 1
    (the standard LISFLOOD-FP limiter against supercritical instability on
    steep, shallow cells)
  * Mass:    a cell can never send out more water in a step than it holds;
    outgoing fluxes are scaled down to the available volume, so depth stays
    non-negative and volume is conserved to round-off (tested).

Boundaries: domain edges are free-outflow faces with Manning normal-depth
flux on the local bed slope — water leaves where the terrain says it would.

Pure numpy, no I/O. Units: metres, seconds.
"""
from __future__ import annotations

import numpy as np

G = 9.81
H_DRY = 1e-3          # below this a face carries no flow


class RouterResult(dict):
    pass


def _face_flux(q, eta_a, eta_b, zmax, inv_d, dt, gn2, fr_max):
    """Inertial update of one face array (see module docstring)."""
    h_flow = np.maximum(eta_a, eta_b)
    h_flow -= zmax
    wet = h_flow > H_DRY
    hf = np.where(wet, h_flow, 1.0)
    num = eta_b - eta_a
    num *= (-G * dt) * inv_d
    num *= hf
    num += q
    hf73 = hf * hf * np.cbrt(hf)
    den = np.abs(q)
    den *= gn2 * dt
    den /= hf73
    den += 1.0
    num /= den
    cap = np.sqrt(hf)
    cap *= hf * (fr_max * np.sqrt(G))
    np.clip(num, -cap, cap, out=num)
    num[~wet] = 0.0
    return num


def route(z: np.ndarray, dx: float, dy: float, t_end_s: float, rain_rate=None,
          manning=0.06, h0=None, record_every_s: float = 3600.0, record_cells=None,
          cfl: float = 0.4, fr_max: float = 1.0, dt_max: float = 60.0, boundary_slope_min=1e-4,
          snapshot_at_s: float | None = None, sea_mask=None, sea_level: float = 0.0):
    """
    Run the router.

    z          bed elevation (h, w), metres; NaN treated as a high wall.
    rain_rate  callable t_s → rate (m/s) scalar or (h, w) array, or None.
    manning    scalar or (h, w) Manning's n.
    record_cells  (rows, cols) index arrays to record depth at every
               `record_every_s`.
    snapshot_at_s  also return the full depth field at this time (h_snapshot).
    sea_mask   cells held at `sea_level` (open ocean): water arriving there
               leaves the domain, counted in volume_out.
    Returns RouterResult with h (final), h_max (peak depth), t_peak (s),
    wet_seconds (time with h > 0.03 m), times, series (T × N), volume_in,
    volume_out, volume_final, steps.
    """
    z = np.array(z, dtype=np.float64)
    wall = ~np.isfinite(z)
    z[wall] = np.nanmax(z[~wall]) + 1e3 if (~wall).any() else 0.0
    h_, w_ = z.shape
    n = np.broadcast_to(np.asarray(manning, float), z.shape).copy()
    h = np.zeros_like(z) if h0 is None else np.array(h0, float)
    h[wall] = 0.0
    qx = np.zeros((h_, w_ + 1))
    qy = np.zeros((h_ + 1, w_))
    cell_area = dx * dy
    h_max = np.zeros_like(z) if sea_mask is not None else h.copy()
    t_peak = np.zeros_like(z)
    wet_s = np.zeros_like(z)
    times, series = [], []
    rr, cc = (record_cells if record_cells is not None else (np.array([], int), np.array([], int)))
    vol_in = 0.0
    vol_out = 0.0
    t = 0.0
    next_rec = 0.0
    steps = 0
    nx_face = 0.5 * (n[:, :-1] + n[:, 1:])
    ny_face = 0.5 * (n[:-1, :] + n[1:, :])
    gn2x = G * nx_face ** 2
    gn2y = G * ny_face ** 2
    zmax_x = np.maximum(z[:, :-1], z[:, 1:])
    zmax_y = np.maximum(z[:-1, :], z[1:, :])
    inv_n = 1.0 / n
    # Outward bed slope at each boundary for the normal-depth outflow.
    s_w = np.maximum(boundary_slope_min, (z[:, 1] - z[:, 0]) / dx) if w_ > 1 else np.full(h_, boundary_slope_min)
    s_e = np.maximum(boundary_slope_min, (z[:, -2] - z[:, -1]) / dx) if w_ > 1 else np.full(h_, boundary_slope_min)
    s_n = np.maximum(boundary_slope_min, (z[1, :] - z[0, :]) / dy) if h_ > 1 else np.full(w_, boundary_slope_min)
    s_s = np.maximum(boundary_slope_min, (z[-2, :] - z[-1, :]) / dy) if h_ > 1 else np.full(w_, boundary_slope_min)

    sq_w, sq_e = np.sqrt(s_w) * inv_n[:, 0], np.sqrt(s_e) * inv_n[:, -1]
    sq_n, sq_s = np.sqrt(s_n) * inv_n[0, :], np.sqrt(s_s) * inv_n[-1, :]
    has_wall = bool(wall.any())
    if has_wall:
        wx = np.zeros((h_, w_ + 1), bool)
        wx[:, :-1] |= wall
        wx[:, 1:] |= wall
        wy = np.zeros((h_ + 1, w_), bool)
        wy[:-1, :] |= wall
        wy[1:, :] |= wall
    h_snap = None
    sea = None
    if sea_mask is not None and np.any(sea_mask):
        sea = np.asarray(sea_mask, bool) & ~wall
        sea_h = np.maximum(0.0, sea_level - z[sea])
        h[sea] = sea_h
    vol_sea0 = float(h[sea].sum() * cell_area) if sea is not None else 0.0
    land = ~sea if sea is not None else None
    if sea is not None:
        # Faces between two held sea cells carry nothing we need; zeroing
        # them keeps deep bathymetry out of the dynamics and the CFL limit.
        sea_x = np.zeros((h_, w_ + 1), bool)
        sea_x[:, 1:-1] = sea[:, :-1] & sea[:, 1:]
        sea_y = np.zeros((h_ + 1, w_), bool)
        sea_y[1:-1, :] = sea[:-1, :] & sea[1:, :]
    while t < t_end_s - 1e-9:
        hmax_now = float(h[land].max()) if land is not None and land.any() else float(h.max())
        dt = dt_max if hmax_now < H_DRY else min(dt_max, cfl * min(dx, dy) / np.sqrt(G * hmax_now))
        dt = min(dt, t_end_s - t)
        if snapshot_at_s is not None and h_snap is None:
            if t >= snapshot_at_s - 1e-9:
                h_snap = h.copy()
            else:
                dt = min(dt, snapshot_at_s - t)
        if record_cells is not None and t >= next_rec - 1e-9:
            times.append(t)
            series.append(h[rr, cc].copy())
            next_rec += record_every_s
        eta = z + h
        # Interior faces.
        if w_ > 1:
            qx[:, 1:-1] = _face_flux(qx[:, 1:-1], eta[:, :-1], eta[:, 1:], zmax_x,
                                     1.0 / dx, dt, gn2x, fr_max)
        if h_ > 1:
            qy[1:-1, :] = _face_flux(qy[1:-1, :], eta[:-1, :], eta[1:, :], zmax_y,
                                     1.0 / dy, dt, gn2y, fr_max)
        # Boundary outflow (outward positive by construction below).
        qx[:, 0] = -(h[:, 0] * np.cbrt(h[:, 0] ** 2)) * sq_w
        qx[:, -1] = (h[:, -1] * np.cbrt(h[:, -1] ** 2)) * sq_e
        qy[0, :] = -(h[0, :] * np.cbrt(h[0, :] ** 2)) * sq_n
        qy[-1, :] = (h[-1, :] * np.cbrt(h[-1, :] ** 2)) * sq_s
        # Walls block flow on any face touching them.
        if has_wall:
            qx[wx] = 0.0
            qy[wy] = 0.0
        if sea is not None:
            qx[sea_x] = 0.0
            qy[sea_y] = 0.0
            # The fixed-level sea absorbs, never supplies: no flux may leave
            # a sea cell toward land (tidal forcing is out of scope).
            qx[:, 1:-1] = np.where(sea[:, :-1] & (qx[:, 1:-1] > 0), 0.0, qx[:, 1:-1])
            qx[:, 1:-1] = np.where(sea[:, 1:] & (qx[:, 1:-1] < 0), 0.0, qx[:, 1:-1])
            qy[1:-1, :] = np.where(sea[:-1, :] & (qy[1:-1, :] > 0), 0.0, qy[1:-1, :])
            qy[1:-1, :] = np.where(sea[1:, :] & (qy[1:-1, :] < 0), 0.0, qy[1:-1, :])

        # Mass limiter: scale each cell's outgoing fluxes to what it holds.
        px = np.maximum(qx, 0.0)             # flow leaving the cell on the face's left
        nxq = np.maximum(-qx, 0.0)           # flow leaving the cell on the face's right
        py = np.maximum(qy, 0.0)
        nyq = np.maximum(-qy, 0.0)
        out_vol = ((px[:, 1:] + nxq[:, :-1]) * dy + (py[1:, :] + nyq[:-1, :]) * dx) * dt
        avail = h * cell_area
        over = out_vol > avail
        if over.any():
            scale = np.ones_like(h)
            scale[over] = avail[over] / out_vol[over]
            # A face's flux leaves exactly one cell (its upwind cell).
            qx[:, 1:] = np.where(qx[:, 1:] > 0, qx[:, 1:] * scale, qx[:, 1:])
            qx[:, :-1] = np.where(qx[:, :-1] < 0, qx[:, :-1] * scale, qx[:, :-1])
            qy[1:, :] = np.where(qy[1:, :] > 0, qy[1:, :] * scale, qy[1:, :])
            qy[:-1, :] = np.where(qy[:-1, :] < 0, qy[:-1, :] * scale, qy[:-1, :])

        dh = dt * ((qx[:, :-1] - qx[:, 1:]) / dx + (qy[:-1, :] - qy[1:, :]) / dy)
        vol_out += dt * (np.sum(np.maximum(-qx[:, 0], 0) + np.maximum(qx[:, -1], 0)) * dy +
                         np.sum(np.maximum(-qy[0, :], 0) + np.maximum(qy[-1, :], 0)) * dx)
        h += dh
        if rain_rate is not None:
            r = rain_rate(t)
            if np.ndim(r) == 0:
                if r:
                    add = np.full(z.shape, float(r) * dt)
                    if has_wall:
                        add[wall] = 0.0
                    h += add
                    vol_in += float(add.sum()) * cell_area
            else:
                add = np.asarray(r, float) * dt
                if has_wall:
                    add = np.where(wall, 0.0, add)
                h += add
                vol_in += float(add.sum()) * cell_area
        np.maximum(h, 0.0, out=h)        # round-off only; the limiter guarantees ≥ 0
        if sea is not None:
            vol_out += float((h[sea] - sea_h).sum()) * cell_area
            h[sea] = sea_h
        t += dt
        steps += 1
        deeper = h > h_max
        if sea is not None:
            deeper[sea] = False
        h_max[deeper] = h[deeper]
        t_peak[deeper] = t
        wet_s[h > 0.03] += dt

    if record_cells is not None and (not times or times[-1] < t - 1e-6):
        times.append(t)
        series.append(h[rr, cc].copy())
    if snapshot_at_s is not None and h_snap is None:
        h_snap = h.copy()
    return RouterResult(h=h, h_max=h_max, h_snapshot=h_snap, t_peak=t_peak, wet_seconds=wet_s,
                        times=np.array(times), series=np.array(series) if series else np.zeros((0, len(rr))),
                        volume_in=vol_in, volume_out=vol_out,
                        volume_final=float(h.sum() * cell_area) - vol_sea0, steps=steps)
