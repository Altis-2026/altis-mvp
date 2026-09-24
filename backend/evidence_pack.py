"""
evidence_pack.py — The per-property forensic evidence pack (PDF).

A claims file has to survive a coverage dispute, a regulator's market-conduct
exam, and occasionally a courtroom. The pack holds, for one property and one
event: what Altis decided and why, every measurement with its uncertainty,
the water line against the finished floor, the routed hydrograph with the
satellite pass marked, each flag with the evidence that raised it, a site map
from real terrain / water / road data, the full data lineage, and an explicit
limitations section.

Rules the pack keeps:
  * Nothing synthetic. Satellite chips are embedded only when real imagery
    was retrieved; otherwise the pack says so. The site map is drawn from the
    same DEM, water surface and road network the flags used.
  * Every number carries its basis ("observed", "assumed", "routed").
  * A SHA-256 digest of the exact inputs is printed on every page, so a copy
    of the pack can be checked against the record it came from.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FT_PER_M = 3.28084


class EvidenceError(Exception):
    pass


def code_version() -> str:
    for var in ('RAILWAY_GIT_COMMIT_SHA', 'VERCEL_GIT_COMMIT_SHA', 'GIT_COMMIT'):
        if os.getenv(var):
            return os.getenv(var)[:12]
    try:
        return subprocess.check_output(['git', 'rev-parse', '--short=12', 'HEAD'], cwd=BASE_DIR,
                                       stderr=subprocess.DEVNULL, timeout=3).decode().strip()
    except Exception:  # noqa: BLE001
        return 'unknown'


def digest(record: dict) -> str:
    blob = json.dumps(record, sort_keys=True, default=str, separators=(',', ':')).encode()
    return hashlib.sha256(blob).hexdigest()


# ── Site map (real terrain + water + roads) ──────────────────────────────────

def site_map_png(lat: float, lon: float, depth_ft: float, intel: dict | None,
                 half_m: float = 350.0, px: int = 520) -> bytes | None:
    """
    Hillshaded terrain around the property with the observed water surface in
    blue, OSM roads, and the property marker. Returns PNG bytes or None when
    terrain is unavailable.
    """
    import numpy as np
    from PIL import Image, ImageDraw
    from backend import geodata
    from pipeline.terrain import Grid
    dlat = half_m / 110540.0
    dlon = half_m / (111320.0 * math.cos(math.radians(lat)))
    bbox = [lon - dlon, lat - dlat, lon + dlon, lat + dlat]
    try:
        dem = geodata.dem_grid(bbox, res_m=5.0)
    except Exception:  # noqa: BLE001
        return None
    g = dem.resample(*bbox, (2 * dlon) / px, (2 * dlat) / px)
    z = np.nan_to_num(g.data, nan=float(np.nanmedian(g.data)))
    cell = g.cell_m
    gy, gx = np.gradient(z, cell[1], cell[0])
    az, alt = math.radians(315), math.radians(45)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    shade = np.clip(shade, 0, 1)
    zn = (z - z.min()) / max(1e-6, z.max() - z.min())
    base = np.stack([60 + 90 * zn, 70 + 80 * zn, 60 + 60 * zn], -1) * (0.55 + 0.45 * shade[..., None])
    rgb = np.clip(base, 0, 255)
    # Water: observed WSE at the property extended as a flat local surface —
    # the same neighbourhood-WSE assumption the SAR pipeline uses.
    ground_here = float(g.sample([lon], [lat])[0])
    peak_ft = ((intel or {}).get('hydrograph') or {}).get('peak_depth_ft')
    for d_ft, col, a in ((peak_ft, (100, 181, 246), 0.35), (depth_ft, (33, 150, 243), 0.6)):
        if d_ft and d_ft > 0.05 and math.isfinite(ground_here):
            wse = ground_here + d_ft / FT_PER_M
            wet = z < wse
            depth = np.clip((wse - z) / 2.0, 0, 1)
            alpha = (a * (0.5 + 0.5 * depth))[..., None] * wet[..., None]
            rgb = rgb * (1 - alpha) + np.array(col) * alpha
    img = Image.fromarray(rgb.astype('uint8'), 'RGB')
    draw = ImageDraw.Draw(img)

    def to_px(lo, la):
        return ((lo - bbox[0]) / (2 * dlon) * px, (bbox[3] - la) / (2 * dlat) * px)
    try:
        ways = geodata.osm_roads([bbox[0] - 0.002, bbox[1] - 0.002, bbox[2] + 0.002, bbox[3] + 0.002],
                                 timeout_s=12, attempts=1)
        for w in ways:
            pts = [to_px(lo, la) for lo, la in w['coords']]
            if len(pts) >= 2:
                major = w.get('highway') in ('motorway', 'trunk', 'primary', 'secondary')
                draw.line(pts, fill=(245, 245, 235) if major else (215, 215, 205), width=3 if major else 2)
    except Exception:  # noqa: BLE001
        pass
    cx, cy = to_px(lon, lat)
    draw.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], outline=(255, 70, 70), width=3)
    draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=(255, 70, 70))
    # Scale bar: 100 m
    bar = 100.0 / (2 * half_m) * px
    draw.rectangle([14, px - 22, 14 + bar, px - 17], fill=(255, 255, 255))
    draw.text((14, px - 36), '100 m', fill=(255, 255, 255))
    draw.text((px - 14, 12), 'N', fill=(255, 255, 255))
    out = io.BytesIO()
    img.save(out, 'PNG')
    return out.getvalue()


# ── PDF ──────────────────────────────────────────────────────────────────────

def build_evidence_pack(prop: dict, event: dict, intel_event: dict | None = None,
                        imagery: dict | None = None, include_site_map: bool = True) -> bytes:
    """
    prop:   the property row (triage fields + optional 'intel').
    event:  {'id', 'label', 'sub', 'windows'?}.
    intel_event: event-level intel (sources/products/router skill).
    imagery: {'pre_png', 'post_png', 'label'} real chips, or None.
    """
    try:
        from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String, Circle
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (HRFlowable, Image as RLImage, KeepTogether, PageBreak,
                                        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)
    except ImportError as e:  # pragma: no cover
        raise EvidenceError(f'PDF support not installed: {e}')
    from backend.reporting import _table_style

    intel = prop.get('intel') or {}
    record = {'property': {k: v for k, v in prop.items() if k != 'intel'}, 'intel': intel,
              'event': event.get('id')}
    dig = digest(record)
    version = code_version()
    generated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    styles = getSampleStyleSheet()
    ink, teal, muted = colors.HexColor('#0B1622'), colors.HexColor('#1C6E8C'), colors.HexColor('#5A6B78')
    red, amber = colors.HexColor('#C62828'), colors.HexColor('#B26A00')
    h1 = ParagraphStyle('h1', parent=styles['Title'], textColor=ink, fontSize=19, alignment=TA_LEFT, spaceAfter=2)
    h2 = ParagraphStyle('h2', parent=styles['Heading2'], textColor=teal, fontSize=12, spaceBefore=12, spaceAfter=5)
    body = ParagraphStyle('b', parent=styles['BodyText'], textColor=ink, fontSize=9, leading=13, spaceAfter=3)
    small = ParagraphStyle('s', parent=styles['BodyText'], textColor=muted, fontSize=7.6, leading=10.5)

    def esc(v):
        return (str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')) if v is not None else '—'

    def num(v, d=1, unit=''):
        try:
            return f'{float(v):.{d}f}{unit}'
        except (TypeError, ValueError):
            return '—'

    def kv_table(rows, widths=(2.3 * inch, 4.6 * inch)):
        t = Table([[Paragraph(f'<b>{esc(k)}</b>', body), Paragraph(esc(v), body)] for k, v in rows],
                  colWidths=widths)
        t.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#D6DEE5')),
            ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, colors.HexColor('#F4F7FA')]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
        return t

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.65 * inch, bottomMargin=0.7 * inch,
                            leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            title=f"Altis Evidence Pack — {prop.get('property_id')}", author='Altis')

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 6.8)
        canvas.setFillColor(muted)
        canvas.drawString(0.7 * inch, 0.42 * inch,
                          f"Altis evidence pack · {prop.get('property_id')} · {event.get('label')} · "
                          f"generated {generated} · code {version}")
        canvas.drawString(0.7 * inch, 0.3 * inch, f'Input digest SHA-256 {dig}')
        canvas.drawRightString(letter[0] - 0.7 * inch, 0.42 * inch, f'Page {_doc.page}')
        canvas.restoreState()

    E = []
    E.append(Paragraph('Flood Claim Evidence Pack', h1))
    E.append(Paragraph(f"<b>{esc(prop.get('address'))}</b>", body))
    E.append(Paragraph(f"{esc(event.get('label'))} · {esc(event.get('sub', ''))} · Property "
                       f"{esc(prop.get('property_id'))}"
                       + (f" · Policy {esc(prop.get('policy_number'))}" if prop.get('policy_number') else ''), small))
    E.append(Spacer(1, 4))
    E.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#D6DEE5')))

    # 1. Decision
    E.append(Paragraph('1. Triage decision', h2))
    ic = prop.get('impact_class') or '—'
    rows = [('Class', ic), ('Recommended action', prop.get('recommended_action')),
            ('Confidence', f"{prop.get('confidence_score', '—')}%")]
    if prop.get('original_class') and prop.get('original_class') != ic:
        rows.append(('Held back from', f"{prop['original_class']} — {(intel.get('class_override') or {}).get('reason', '')}"))
    if prop.get('adjuster_note'):
        rows.append(('Satellite note', prop.get('adjuster_note')))
    E.append(kv_table(rows))
    try:
        factors = json.loads(prop.get('confidence_factors') or '[]') if isinstance(prop.get('confidence_factors'), str) \
            else (prop.get('confidence_factors') or [])
    except ValueError:
        factors = []
    if factors:
        E.append(Spacer(1, 4))
        t = Table([['Confidence factor', 'Δ', 'Reason']] +
                  [[f.get('factor'), f"{f.get('delta', 0):+d}", Paragraph(esc(f.get('reason')), small)] for f in factors],
                  colWidths=[1.6 * inch, 0.45 * inch, 4.85 * inch])
        t.setStyle(_table_style(colors))
        E.append(t)

    # 2. Measurements
    E.append(Paragraph('2. Satellite measurements', h2))
    ci = prop.get('depth_ci_ft')
    E.append(kv_table([
        ('Flood depth at grade', f"{num(prop.get('max_depth_ft'), 2)} ft (±{num(ci, 1)} ft, ~68% interval)"),
        ('Flooded share of parcel', f"{num(prop.get('pct_flooded'), 1)}% (Sentinel-1 amplitude change)"),
        ('Optical cross-check', ('Sentinel-2 water ' + num(float(prop.get('optical_water_pct') or 0) * 100, 0) + '%')
         if str(prop.get('optical_available')) in ('1', 'True', 'true') else 'no cloud-free Sentinel-2 scene'),
        ('Dense urban area', 'yes — radar shadow can mimic water; confidence reduced'
         if str(prop.get('urban_flag')) in ('1', 'True') else 'no'),
        ('Radar passes used', ', '.join(((intel_event or {}).get('event') or {}).get('sar_passes', [])[:6]) or '—'),
    ]))

    # 3. Structure
    sd = intel.get('structure')
    if sd:
        E.append(Paragraph('3. Water against the finished floor', h2))
        peak_sd = intel.get('structure_peak')
        above = peak_sd if (peak_sd and peak_sd['depth_above_floor_ft'] > sd['depth_above_floor_ft']) else sd
        E.append(kv_table([
            ('Foundation', f"{sd['foundation']} ({'observed by adjuster' if sd['foundation_observed'] else 'assumed — not yet observed'})"),
            ('First-floor height above grade', f"{num(sd['floor_height_ft'])} ft (HAZUS default for type)"),
            ('Water above finished floor', (f"{num(above['depth_above_floor_ft'], 2)} ft ±{num(above['depth_above_floor_ci_ft'])}"
                                            if above['depth_above_floor_ft'] > 0 else 'below the floor')
             + (' (routed peak)' if above is peak_sd else ' (at satellite pass)')),
            ('Depth used for damage curve', f"{num(intel.get('damage_depth_ft'), 2)} ft"),
        ]))
        d = Drawing(6.9 * inch, 1.5 * inch)
        W, H = 6.9 * inch, 1.5 * inch
        fl = float(sd['floor_height_ft'])
        wat = max(0.0, float(sd['depth_at_structure_ft']))
        pk = max(0.0, float(peak_sd['depth_at_structure_ft'])) if peak_sd else None
        top = max(fl + 9, wat + 1, (pk or 0) + 1)
        base_y, sc = 14, (H - 24) / top
        d.add(Rect(0, 0, W, base_y, fillColor=colors.HexColor('#B8A58C'), strokeColor=None))
        d.add(Rect(2.4 * inch, base_y, 2.1 * inch, fl * sc, fillColor=colors.HexColor('#C9CDD3'), strokeColor=None))
        d.add(Rect(2.4 * inch, base_y + fl * sc, 2.1 * inch, 8 * sc, fillColor=colors.HexColor('#EEF1F4'),
                   strokeColor=colors.HexColor('#7D8A96')))
        if pk and pk > wat + 0.05:
            d.add(Rect(0, base_y, W, pk * sc, fillColor=colors.Color(0.39, 0.71, 0.96, 0.25), strokeColor=None))
            d.add(String(4, base_y + pk * sc + 2, f'routed peak {pk:.1f} ft', fontSize=7, fillColor=teal))
        if wat > 0:
            d.add(Rect(0, base_y, W, wat * sc, fillColor=colors.Color(0.13, 0.59, 0.95, 0.45), strokeColor=None))
            d.add(String(4, base_y + wat * sc + 2 if not pk else base_y + wat * sc - 9,
                         f'at satellite pass {wat:.1f} ft', fontSize=7, fillColor=ink))
        d.add(Line(2.3 * inch, base_y + fl * sc, 4.6 * inch, base_y + fl * sc, strokeColor=ink,
                   strokeDashArray=[3, 2]))
        d.add(String(4.65 * inch, base_y + fl * sc - 2, f'finished floor {fl:.1f} ft', fontSize=7, fillColor=ink))
        E.append(Spacer(1, 4))
        E.append(d)

    # 4. Hydrograph
    hy = intel.get('hydrograph')
    if hy and hy.get('depth_ft') and hy.get('t0') and hy.get('step_h'):
        E.append(Paragraph('4. Synthetic revisit — routed hydrograph', h2))
        vals = [float(v) for v in hy['depth_ft']]
        W, H = 6.9 * inch, 1.7 * inch
        d = Drawing(W, H)
        L, B = 28, 16
        vmax = max(1.0, max(vals), float(hy.get('obs_depth_ft') or 0)) * 1.1
        X = lambda i: L + i / max(1, len(vals) - 1) * (W - L - 6)
        Y = lambda v: B + v / vmax * (H - B - 8)
        d.add(Line(L, B, W - 6, B, strokeColor=muted))
        d.add(Line(L, B, L, H - 8, strokeColor=muted))
        for f in (0.5, 1.0):
            d.add(String(2, Y(vmax * f / 1.1) - 3, f'{vmax * f / 1.1:.0f} ft', fontSize=6.5, fillColor=muted))
        d.add(PolyLine([c for i, v in enumerate(vals) for c in (X(i), Y(v))], strokeColor=teal, strokeWidth=1.4))
        t0 = datetime.fromisoformat(hy['t0'])
        step = float(hy['step_h'])
        if sd and 0 < float(sd['floor_height_ft']) < vmax:
            d.add(Line(L, Y(sd['floor_height_ft']), W - 6, Y(sd['floor_height_ft']), strokeColor=ink,
                       strokeDashArray=[3, 2], strokeWidth=0.6))
        try:
            ip = (datetime.fromisoformat(hy['pass_time']) - t0).total_seconds() / 3600 / step
            if 0 <= ip <= len(vals) - 1:
                d.add(Line(X(ip), B, X(ip), H - 8, strokeColor=amber, strokeWidth=0.8))
                d.add(Circle(X(ip), Y(float(hy.get('obs_depth_ft') or 0)), 2.6, fillColor=amber, strokeColor=None))
                d.add(String(X(ip) + 3, H - 14, 'satellite pass', fontSize=6.5, fillColor=amber))
        except (TypeError, ValueError):
            pass
        for k in range(0, len(vals), max(1, int(24 / step))):
            d.add(String(X(k) - 8, 4, (t0 + timedelta(hours=k * step)).strftime('%b %d'),
                         fontSize=6, fillColor=muted))
        E.append(d)
        skill = ((intel_event or {}).get('event') or {}).get('router') or {}
        sk = skill.get('skill_at_pass') or {}
        E.append(kv_table([
            ('Peak depth at grade', f"{num(hy.get('peak_depth_ft'), 2)} ft at {esc(hy.get('peak_time'))}"),
            ('Observed at pass', f"{num(hy.get('obs_depth_ft'), 2)} ft at {esc(hy.get('pass_time'))}"),
            ('Hours wet / above floor', f"{num(hy.get('hours_wet'), 0)} h / {num(hy.get('hours_above_floor'), 0)} h"),
            ('Basis', hy.get('basis')),
            ('Router fit at the pass', f"CSI {num(sk.get('csi'), 2)} (hits {sk.get('hits', '—')}, misses "
                                       f"{sk.get('misses', '—')}, false alarms {sk.get('false_alarms', '—')}); "
                                       f"forcing multiplier k={num(skill.get('calibrated_k'), 2)}; "
                                       f"{num(skill.get('res_final_m'), 0)} m grid"),
        ]))

    # 5. Flags
    flags = intel.get('flags') or []
    E.append(Paragraph('5. Adjuster flags', h2))
    if not flags:
        E.append(Paragraph('No flags raised.', body))
    for f in flags:
        colr = {'alert': red, 'caution': amber}.get(f['level'], teal)
        ev = f.get('evidence') or {}
        block = [Paragraph(f"<font color='{colr.hexval()}'><b>{f['level'].upper()}</b></font> &nbsp;<b>{esc(f['title'])}</b>", body),
                 Paragraph(esc(f['detail']), body)]
        rows = [(k.replace('_', ' '), v) for k, v in ev.items() if v is not None and not isinstance(v, (dict, list))]
        if rows:
            block.append(kv_table(rows[:12], widths=(2.3 * inch, 4.6 * inch)))
        block.append(Spacer(1, 5))
        E.append(KeepTogether(block))

    # 6. Context
    w, rain, terr = intel.get('wind'), intel.get('rain'), intel.get('terrain') or {}
    hab, acc = intel.get('habitability'), intel.get('access')
    E.append(Paragraph('6. Event context', h2))
    E.append(kv_table([
        ('Peak sustained wind', (f"{num(w['peak_kt'], 0)} kt ({w['category']}) at {w['peak_time']}; "
                                 f"{w['hours_ge_34kt']} h ≥ 34 kt; closest approach {w['closest_nm']} nm")
         if w else 'no tropical cyclone'),
        ('Rainfall', (f"{num(rain['max_3day_mm'], 0)} mm heaviest 3 days (peak {rain['peak_day']}); "
                      f"{num(rain['total_mm'], 0)} mm event total") if rain else 'unavailable'),
        ('Radar pass after peak', f"{num((intel.get('sar_lag_hours') or 0) / 24, 1)} days" if intel.get('sar_lag_hours') is not None else 'unknown'),
        ('Terrain', f"ground {num(terr.get('ground_asl_m'), 1)} m above sea level; "
                    f"{num(terr.get('hand_m'), 1)} m above nearest drainage (HAND)"),
        ('Historic surface water', f"{num(intel.get('jrc_occurrence_pct'), 0)}% of observations 1984–2021 (JRC GSW)"),
        ('Road access', (acc or {}).get('status', 'unknown')),
        ('Uninhabitable (planning)', (f"{hab['days_low']}–{hab['days_high']} days · {hab.get('accommodation')} · {hab['scope']}"
                                      if hab and hab.get('displacement') else 'not displaced') if hab else '—'),
    ]))

    # 7. Imagery & site map
    E.append(Paragraph('7. Imagery and site map', h2))
    if imagery and imagery.get('pre_png') and imagery.get('post_png'):
        pre = RLImage(io.BytesIO(imagery['pre_png']), width=3.3 * inch, height=3.3 * inch)
        post = RLImage(io.BytesIO(imagery['post_png']), width=3.3 * inch, height=3.3 * inch)
        t = Table([[pre, post], [Paragraph('Pre-event', small), Paragraph('Post-event', small)]],
                  colWidths=[3.45 * inch, 3.45 * inch])
        E.append(t)
        E.append(Paragraph(esc(imagery.get('label') or 'Sentinel-1 SAR'), small))
    else:
        E.append(Paragraph('Satellite chips not embedded: real imagery requires the Earth Engine '
                           'connection on the processing server. Altis never embeds synthetic '
                           'imagery in an evidence document.', small))
    if include_site_map and prop.get('latitude') is not None:
        png = site_map_png(float(prop['latitude']), float(prop['longitude']),
                           float(prop.get('max_depth_ft') or 0), intel)
        if png:
            E.append(Spacer(1, 4))
            E.append(RLImage(io.BytesIO(png), width=3.6 * inch, height=3.6 * inch))
            E.append(Paragraph('Site map: hillshaded terrain (AWS Terrain Tiles), water surface at the satellite '
                               'pass (dark blue) and routed peak (light blue) extended at the observed level, '
                               'OpenStreetMap roads, property marked in red. 100 m scale bar.', small))

    # 8. Lineage
    E.append(Paragraph('8. Data lineage', h2))
    ie = intel_event or {}
    prod = ie.get('products') or {}
    lineage = [
        ('Flood detection', 'Sentinel-1 GRD (ESA Copernicus) change detection, Otsu threshold, JRC permanent-water '
                            'mask; depth = water-surface elevation − DEM'),
        ('Pass times', 'Copernicus Data Space STAC (sentinel-1-grd)'),
        ('Rainfall', prod.get('rain') or '—'),
        ('Wind', prod.get('wind') or 'n/a'),
        ('Terrain / HAND', 'AWS Terrain Tiles (USGS 3DEP in US, SRTM elsewhere); priority-flood drainage, HAND'),
        ('Historic water', 'JRC Global Surface Water v1.4 occurrence'),
        ('Roads', 'OpenStreetMap via Overpass (ODbL)'),
        ('Hydraulics', ((ie.get('event') or {}).get('router') or {}).get('method') or 'router not run for this event'),
        ('Intel layer version', f"{ie.get('version', '—')} generated {ie.get('generated_at', '—')}"),
        ('Code version', version),
        ('Input digest', dig),
    ]
    E.append(kv_table(lineage))

    # 9. Limitations
    E.append(Paragraph('9. Limitations', h2))
    for t in [
        'Depths are derived from satellite radar and a digital elevation model. They are consistent with the '
        'observation, not surveyed measurements; the stated interval is the method uncertainty.',
        'Sentinel-1 revisits every 6–12 days. Water that drained before a pass is invisible to it — the '
        'transient-flood flag exists for exactly this reason. A dry reading is not proof of no loss.',
        'Dense urban areas produce radar shadow and layover that can mimic or hide water.',
        'The first-floor height is a HAZUS default for the foundation type unless observed by an adjuster.',
        'The routed hydrograph is a physics model calibrated to one pass; between passes it is an estimate '
        'whose fit statistics are stated above. It has not been validated per property against ground truth.',
        'Wind is a parametric reconstruction from NHC best-track radii with an open-terrain surface factor; '
        'local gusts and sheltering are not resolved.',
        'The habitability figure is a planning band for accommodation decisions, not a contractor schedule.',
        'This pack supports, and does not replace, an adjuster’s determination of coverage and loss.',
    ]:
        E.append(Paragraph('• ' + t, body))

    doc.build(E, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
