"""
access.py — Can a field adjuster actually drive to this property?

Graph-cut of the OpenStreetMap road network against a water-depth surface.
Road segments carrying ≥ threshold water are removed; a property is
reachable when its nearest road node still connects, over dry segments, to an
arterial that leaves the study area. The threshold defaults to 0.30 m — the
depth at which the US NWS/FEMA "Turn Around, Don't Drown" guidance says most
cars can be swept away (15 cm stalls many).

Pure: the caller supplies ways and a depth_at(lons, lats) → metres function
(from the router's peak depth, or the observed water surface).
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

ARTERIAL = {'motorway', 'trunk', 'primary', 'secondary', 'motorway_link', 'trunk_link',
            'primary_link', 'secondary_link'}


def _m_per_deg(lat):
    return 111320.0 * math.cos(math.radians(lat)), 110540.0


def build_graph(ways, depth_at, threshold_m=0.30, sample_m=15.0):
    """
    Returns (nodes[lon,lat] array, edges list[(i, j, wet, len_m)], node_is_arterial).
    Segments are sampled every `sample_m` metres; a segment is wet if any
    sample reaches the threshold.
    """
    index = {}
    coords = []
    arterial = []

    def nid(lon, lat, art):
        key = (round(lon, 7), round(lat, 7))
        i = index.get(key)
        if i is None:
            i = index[key] = len(coords)
            coords.append(key)
            arterial.append(art)
        elif art:
            arterial[i] = True
        return i

    seg_pts, seg_ids, seg_len = [], [], []
    edges = []
    for w in ways:
        art = w.get('highway') in ARTERIAL
        pts = w['coords']
        for (lo0, la0), (lo1, la1) in zip(pts, pts[1:]):
            a, b = nid(lo0, la0, art), nid(lo1, la1, art)
            if a == b:
                continue
            mx, my = _m_per_deg((la0 + la1) / 2)
            L = math.hypot((lo1 - lo0) * mx, (la1 - la0) * my)
            k = max(2, int(L // sample_m) + 1)
            t = np.linspace(0, 1, k)
            seg_pts.append(np.column_stack([lo0 + (lo1 - lo0) * t, la0 + (la1 - la0) * t]))
            seg_ids.append(np.full(k, len(edges)))
            seg_len.append(L)
            edges.append([a, b, False, L])
    if not edges:
        return np.zeros((0, 2)), [], []
    P = np.vstack(seg_pts)
    ids = np.concatenate(seg_ids)
    d = np.asarray(depth_at(P[:, 0], P[:, 1]), float)
    wet_samples = np.nan_to_num(d, nan=0.0) >= threshold_m
    wet_edge = np.zeros(len(edges), bool)
    np.logical_or.at(wet_edge, ids, wet_samples)
    for e, wet in zip(edges, wet_edge):
        e[2] = bool(wet)
    return np.array(coords, float), [tuple(e) for e in edges], arterial


def _components(n, edges, ignore_water=False):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b, wet, _ in edges:
        if ignore_water or not wet:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    return [find(i) for i in range(n)]


def assess(ways, depth_at, props, bbox, threshold_m=0.30, snap_m=200.0, edge_frac=0.04):
    """
    props: [{'property_id', 'lon', 'lat'}]. Returns {property_id: result} with
    result['status'] ∈ accessible | street_flooded | isolated | unknown.
    """
    nodes, edges, arterial = build_graph(ways, depth_at, threshold_m)
    out = {}
    if len(nodes) == 0:
        return {p['property_id']: {'status': 'unknown', 'reason': 'no road data',
                                   'threshold_m': threshold_m} for p in props}
    comp = _components(len(nodes), edges)
    comp_all = _components(len(nodes), edges, ignore_water=True)
    w, s, e, n = bbox
    mx_e, my_e = (e - w) * edge_frac, (n - s) * edge_frac
    on_edge = ((nodes[:, 0] <= w + mx_e) | (nodes[:, 0] >= e - mx_e) |
               (nodes[:, 1] <= s + my_e) | (nodes[:, 1] >= n - my_e))
    # A node touches a dry segment?
    dry_deg = defaultdict(int)
    for a, b, wet, _ in edges:
        if not wet:
            dry_deg[a] += 1
            dry_deg[b] += 1
    safe = {comp[i] for i in range(len(nodes))
            if arterial[i] and on_edge[i] and dry_deg[i] > 0}
    if not safe:
        # Small study areas may not reach an arterial exit; fall back to the
        # largest dry component containing any arterial.
        sizes = defaultdict(int)
        for i in range(len(nodes)):
            if arterial[i] and dry_deg[i] > 0:
                sizes[comp[i]] += 1
        if sizes:
            safe = {max(sizes, key=sizes.get)}
    # Components that would reach safety if there were no water at all — a
    # property outside these is on a disconnected fragment of the OSM graph
    # (private road, mapping gap), which is not a flood finding.
    safe_all = {comp_all[i] for i in range(len(nodes)) if comp[i] in safe}
    from scipy.spatial import cKDTree
    lat0 = (s + n) / 2
    mx, my = _m_per_deg(lat0)
    tree = cKDTree(np.column_stack([nodes[:, 0] * mx, nodes[:, 1] * my]))
    dry_nodes = np.array([dry_deg[i] > 0 for i in range(len(nodes))])
    dry_tree = cKDTree(np.column_stack([nodes[dry_nodes, 0] * mx, nodes[dry_nodes, 1] * my])) \
        if dry_nodes.any() else None
    for p in props:
        q = (p['lon'] * mx, p['lat'] * my)
        dist, i = tree.query(q)
        base = {'threshold_m': threshold_m, 'nearest_road_m': round(float(dist), 1)}
        if dist > snap_m:
            out[p['property_id']] = {**base, 'status': 'unknown', 'reason': 'no road within snap radius'}
            continue
        if dry_deg[i] == 0:
            dd = None
            if dry_tree is not None:
                dd, _ = dry_tree.query(q)
            out[p['property_id']] = {**base, 'status': 'street_flooded',
                                     'dry_point_m': None if dd is None else int(round(dd))}
            continue
        if comp[i] in safe:
            status = 'accessible'
        elif comp_all[i] in safe_all:
            status = 'isolated'
        else:
            out[p['property_id']] = {**base, 'status': 'unknown',
                                     'reason': 'road network not connected in OSM'}
            continue
        out[p['property_id']] = {**base, 'status': status}
    return out


def summary(results: dict) -> dict:
    c = defaultdict(int)
    for r in results.values():
        c[r['status']] += 1
    return dict(c)
