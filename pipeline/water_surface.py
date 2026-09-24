"""
water_surface.py — Rebuild a water-depth surface from point observations.

The SAR pipeline reports depth at each property: water-surface elevation
(WSE) minus ground. Water surfaces are smooth at neighbourhood scale (that is
how the pipeline estimates WSE in the first place — a p90 neighbourhood
percentile), so point observations can be turned back into a surface:

  1. WSE_i = ground_i + depth_i at every flooded observation.
  2. Each DEM cell takes the inverse-distance-weighted WSE of flooded
     observations within `radius_m`.
  3. A cell whose nearest observation is DRY, and closer than the nearest wet
     one, is dry — dry evidence is evidence too.
  4. depth = max(0, WSE − ground).

This is the "observed" state used to calibrate the hydraulic router and to cut
the road network when no routed peak is available. It never extrapolates past
the observations' reach.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from pipeline.terrain import Grid


def _xy(lon, lat, lat0):
    mx = 111320.0 * np.cos(np.radians(lat0))
    return np.column_stack([np.asarray(lon) * mx, np.asarray(lat) * 110540.0])


def observed_depth_grid(dem: Grid, obs_lon, obs_lat, obs_depth_m, wet_min_m: float = 0.03,
                        radius_m: float = 400.0, power: float = 2.0) -> Grid:
    lat0 = (dem.north + dem.south) / 2
    obs_lon, obs_lat = np.asarray(obs_lon, float), np.asarray(obs_lat, float)
    depth = np.nan_to_num(np.asarray(obs_depth_m, float), nan=0.0)
    ground = dem.sample(obs_lon, obs_lat)
    ok = np.isfinite(ground)
    obs_lon, obs_lat, depth, ground = obs_lon[ok], obs_lat[ok], depth[ok], ground[ok]
    wet = depth >= wet_min_m
    h, w = dem.shape
    out = np.zeros((h, w))
    if not wet.any():
        return Grid(out, dem.west, dem.north, dem.dx, dem.dy)
    rows, cols = np.mgrid[0:h, 0:w]
    clon, clat = dem.lonlat(rows.ravel(), cols.ravel())
    cxy = _xy(clon, clat, lat0)
    wxy = _xy(obs_lon[wet], obs_lat[wet], lat0)
    wse = ground[wet] + depth[wet]
    wtree = cKDTree(wxy)
    k = int(min(8, wet.sum()))
    dist, idx = wtree.query(cxy, k=k, distance_upper_bound=radius_m)
    if k == 1:
        dist, idx = dist[:, None], idx[:, None]
    valid = np.isfinite(dist)
    wts = np.where(valid, 1.0 / np.maximum(dist, 1.0) ** power, 0.0)
    safe_idx = np.where(valid, idx, 0)
    num = (wts * wse[safe_idx]).sum(axis=1)
    den = wts.sum(axis=1)
    cell_wse = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    nearest_wet = np.where(valid[:, 0], dist[:, 0], np.inf)
    if (~wet).any():
        dtree = cKDTree(_xy(obs_lon[~wet], obs_lat[~wet], lat0))
        nearest_dry, _ = dtree.query(cxy, distance_upper_bound=radius_m)
        cell_wse[nearest_dry < nearest_wet] = np.nan
    dep = np.nan_to_num(cell_wse - dem.data.ravel(), nan=0.0)
    out = np.maximum(0.0, dep).reshape(h, w)
    return Grid(out, dem.west, dem.north, dem.dx, dem.dy)
