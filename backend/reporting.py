"""
reporting.py — Audit-ready PDF generation for a flood event.

Produces the single artifact a carrier's claims manager or an investor's
diligence team actually wants to hold: what Altis decided, how it decided it,
which satellite scenes it looked at, and how those decisions checked out against
independent FEMA ground truth. Everything here is derived from committed outputs
(the event CSV, the run config, the calibration JSON) — no live network call —
so the report is reproducible and matches exactly what the demo shows on screen.

reportlab is a hard dependency of this module; callers should surface a clear
error if it's missing rather than letting the ImportError escape raw.
"""
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / 'outputs'

# Colors mirror the frontend triage palette so the PDF reads as the same product.
TRIAGE_HEX = {
    'Dispatch': '#FF4444',
    'Remote-Approve': '#4CAF82',
    'Remote-Deny': '#6B8FA3',
    'Review': '#FFB347',
}


class ReportError(Exception):
    """Raised when a report can't be built (missing data or reportlab)."""


def _event_config(event_id: str) -> dict:
    from pipeline.config import HARVEY, IAN
    cfg = {'harvey': HARVEY, 'ian': IAN}.get(event_id)
    if cfg is None:
        raise ReportError(f"Unknown event '{event_id}'.")
    return cfg


def _load_calibration(event_id: str) -> Optional[dict]:
    path = OUTPUT_DIR / f"calibration_{event_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def build_event_report(event_id: str, df, stats: dict) -> bytes:
    """
    Render the audit PDF for an event and return it as bytes.

    `df` is the loaded event DataFrame (impact_class, max_depth_ft, pct_flooded,
    confidence_score, …); `stats` is the get_event_stats() summary. Both are
    passed in so this module stays free of the in-memory event cache and is
    trivially unit-testable with a synthetic frame.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
        )
        from reportlab.lib.enums import TA_LEFT
    except ImportError as e:  # pragma: no cover
        raise ReportError(f"PDF support not installed (reportlab missing): {e}")

    cfg = _event_config(event_id)
    cal = _load_calibration(event_id)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        title=f"Altis Flood Intelligence — {cfg['label']}",
        author="Altis",
    )

    styles = getSampleStyleSheet()
    ink = colors.HexColor('#0B1622')
    teal = colors.HexColor('#1C6E8C')
    muted = colors.HexColor('#5A6B78')

    h1 = ParagraphStyle('h1', parent=styles['Title'], textColor=ink,
                        fontSize=22, spaceAfter=2, alignment=TA_LEFT)
    h2 = ParagraphStyle('h2', parent=styles['Heading2'], textColor=teal,
                        fontSize=13, spaceBefore=16, spaceAfter=6)
    body = ParagraphStyle('body', parent=styles['BodyText'], textColor=ink,
                          fontSize=9.5, leading=14, spaceAfter=4)
    small = ParagraphStyle('small', parent=styles['BodyText'], textColor=muted,
                           fontSize=8, leading=11)

    elems = []

    # ── Title block ──────────────────────────────────────────────────────────
    elems.append(Paragraph("Altis Flood Intelligence Report", h1))
    elems.append(Paragraph(
        f"{cfg['label']} &nbsp;·&nbsp; {cfg['sub']}", body))
    generated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    elems.append(Paragraph(
        f"Audit report generated {generated} &nbsp;·&nbsp; "
        f"Study area: {cfg['study_name']}", small))
    elems.append(Spacer(1, 6))
    elems.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#D6DEE5')))

    # ── Triage summary ───────────────────────────────────────────────────────
    elems.append(Paragraph("Triage Summary", h2))
    savings = stats.get('estimated_savings', 0)
    elems.append(Paragraph(
        f"Altis classified <b>{stats.get('total', 0):,}</b> properties in the "
        f"affected area from Sentinel-1 SAR. <b>{stats.get('remote_total', 0):,}</b> "
        f"were resolved remotely (no truck roll), avoiding an estimated "
        f"<b>${savings:,}</b> in inspection cost at "
        f"${cfg.get('cost_per_inspection', 750)}/inspection. "
        f"<b>{stats.get('dispatch', 0):,}</b> were flagged for field dispatch and "
        f"<b>{stats.get('review', 0):,}</b> routed to manual review.", body))

    triage_rows = [['Triage decision', 'Properties', 'Share']]
    total = max(stats.get('total', 0), 1)
    for label, key in [('Dispatch (send adjuster)', 'dispatch'),
                       ('Remote-Approve', 'remote_approve'),
                       ('Remote-Deny', 'remote_deny'),
                       ('Review (manual)', 'review')]:
        n = stats.get(key, 0)
        triage_rows.append([label, f"{n:,}", f"{100 * n / total:.1f}%"])
    t = Table(triage_rows, colWidths=[3.2 * inch, 1.5 * inch, 1.5 * inch])
    t.setStyle(_table_style(colors))
    elems.append(t)

    # ── Top dispatch priorities (severity × coverage) ────────────────────────
    from backend.priority import rank_dispatch
    records = df.to_dict('records')
    queue = rank_dispatch(records, classes=('Dispatch',))[:10]
    if queue:
        elems.append(Paragraph("Highest-Priority Dispatches", h2))
        elems.append(Paragraph(
            "Ranked by severity (flood depth and extent) weighted by financial "
            "exposure where policy coverage is known.", small))
        elems.append(Spacer(1, 4))
        rows = [['#', 'Property', 'Depth (ft)', 'Area', 'Conf.', 'Priority']]
        for p in queue:
            full_addr = str(p.get('address', p.get('property_id', '')))
            addr = full_addr if len(full_addr) <= 40 else full_addr[:39].rstrip(', ') + '…'
            rows.append([
                str(p['priority_rank']), addr,
                f"{_num(p.get('max_depth_ft')):.1f}",
                f"{_num(p.get('pct_flooded')):.0f}%",
                f"{int(_num(p.get('confidence_score')))}%",
                f"{p['priority_score']:.0f}",
            ])
        t = Table(rows, colWidths=[0.35 * inch, 2.95 * inch, 0.8 * inch,
                                   0.6 * inch, 0.6 * inch, 0.8 * inch])
        t.setStyle(_table_style(colors, numeric_from=2))
        elems.append(t)

    # ── Scene sources + dates ────────────────────────────────────────────────
    elems.append(Paragraph("Satellite Scene Sources", h2))
    bbox = cfg['bbox']
    scene_rows = [
        ['Sensor / product', 'Window', 'Purpose'],
        ['Sentinel-1 GRD (C-band SAR, VV)',
         f"{cfg['pre_start']} → {cfg['pre_end']}", 'Pre-event baseline'],
        ['Sentinel-1 GRD (C-band SAR, VV)',
         f"{cfg['post_start']} → {cfg['post_end']}", 'Post-event flood extent'],
        ['Sentinel-2 MSI (MNDWI)', 'Post-event, cloud-permitting',
         'Independent optical cross-check'],
        ['JRC Global Surface Water', 'Climatology', 'Permanent-water masking'],
        ['USGS 3DEP / SRTM DEM', 'Static', 'Ground elevation for depth'],
    ]
    t = Table(scene_rows, colWidths=[2.7 * inch, 2.0 * inch, 1.5 * inch])
    t.setStyle(_table_style(colors))
    elems.append(t)
    elems.append(Spacer(1, 4))
    elems.append(Paragraph(
        f"Analysis bounding box [W,S,E,N]: [{bbox[0]}, {bbox[1]}, {bbox[2]}, "
        f"{bbox[3]}]. Days from event to post-scene: "
        f"{cfg.get('days_since_event', 'n/a')}.", small))

    # ── Validation: precision / recall vs FEMA ───────────────────────────────
    elems.append(Paragraph("Independent Validation (FEMA ground truth)", h2))
    if cal and cal.get('holdout_metrics'):
        hm = cal['holdout_metrics']
        cls = hm.get('classification', {})
        elems.append(Paragraph(
            f"Altis decisions were checked against FEMA Individual Assistance "
            f"flood-damage records at zip-code resolution, using a "
            f"zip-grouped hold-out (train and test zips disjoint) so the numbers "
            f"below carry no leakage.", body))
        val_rows = [
            ['Metric', 'Value', 'Reading'],
            ['Precision', _fmt(cls.get('precision')),
             'Of properties Altis flagged flooded, share truly flooded'],
            ['Recall', _fmt(cls.get('recall')),
             'Of truly-flooded properties, share Altis caught'],
            ['F1', _fmt(cls.get('f1')), 'Harmonic mean of precision & recall'],
            ['Brier score', _fmt(hm.get('brier_score')),
             'Probability calibration (0 = perfect, 0.25 = uninformative)'],
            ['Calibration error', _fmt(hm.get('expected_calibration_error')),
             'Mean gap between predicted and observed flood rate'],
        ]
        t = Table(val_rows, colWidths=[1.4 * inch, 0.9 * inch, 3.9 * inch])
        t.setStyle(_table_style(colors, numeric_cols=()))
        elems.append(t)
        elems.append(Spacer(1, 4))
        elems.append(Paragraph(
            f"Calibration method: {hm.get('method', 'n/a')} · "
            f"labelled properties: {cal.get('n_total', 'n/a')} "
            f"({cal.get('n_positive', 'n/a')} flooded-truth) · "
            f"test set: {cal.get('n_test', 'n/a')}.", small))
    else:
        elems.append(Paragraph(
            "No FEMA validation artifact is present for this event yet. Run "
            "<font face='Courier'>validation/accuracy_check.py --event "
            f"{event_id}</font> to generate calibrated precision/recall against "
            "FEMA Individual Assistance data; this section will then populate "
            "automatically.", body))

    # ── Methodology + limitations ────────────────────────────────────────────
    elems.append(Paragraph("Methodology &amp; Limitations", h2))
    for line in [
        "Flood extent is detected from the change in Sentinel-1 SAR backscatter "
        "between pre- and post-event composites, speckle-filtered and thresholded "
        "with a range-guarded Otsu cut, with permanent water masked out.",
        "Depth is water-surface elevation (a robust high percentile of flooded-pixel "
        "elevation) minus ground elevation from the DEM, reported with a ±1σ "
        "interval that reflects the DEM's own vertical accuracy.",
        "Three independent members (SAR, optical MNDWI, DEM-hydrology) vote per "
        "property; genuine disagreement is routed to manual Review rather than "
        "auto-resolved.",
        "FEMA Individual Assistance ground truth is zip-resolution and self-selected "
        "(aid applicants), so validation is directional zip-level agreement, not "
        "per-house verification. This is stated plainly because carriers' actuarial "
        "teams will ask.",
    ]:
        elems.append(Paragraph(f"• {line}", body))

    elems.append(Spacer(1, 10))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#D6DEE5')))
    elems.append(Paragraph(
        "Generated by Altis · Pre-decisional flood intelligence for claims triage. "
        "Decisions support, not replace, licensed adjuster judgment.", small))

    doc.build(elems)
    return buf.getvalue()


def build_cat_report(portfolio_id: str, results: list, meta: dict,
                     label: str = 'Live satellite analysis') -> bytes:
    """
    Reinsurance-format catastrophe report for an analyzed portfolio (Round 7):
    event, peril, affected zone, total insured exposure, estimated loss range,
    breakdown by triage class, methodology and data sources — the structured
    document a cat-modeling / reinsurance ops team compiles manually today.
    Built entirely from stored analysis results, so it's reproducible.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, HRFlowable,
        )
        from reportlab.lib.enums import TA_LEFT
    except ImportError as e:  # pragma: no cover
        raise ReportError(f"PDF support not installed (reportlab missing): {e}")

    if not results:
        raise ReportError("No analysis results to report. Run an analysis first.")

    def num(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    in_zone = [r for r in results
               if num(r.get('pct_flooded')) >= 10.0 or num(r.get('max_depth_ft')) > 0.1]
    tiv_total = int(sum(num(r.get('coverage_amount')) for r in results))
    tiv_zone = int(sum(num(r.get('coverage_amount')) for r in in_zone))
    sev_rows = [r for r in results if r.get('severity_low_usd') is not None]
    loss_low = int(sum(num(r.get('severity_low_usd')) for r in sev_rows))
    loss_mid = int(sum(num(r.get('severity_mid_usd')) for r in sev_rows))
    loss_high = int(sum(num(r.get('severity_high_usd')) for r in sev_rows))
    by_class = {c: sum(1 for r in results if r.get('impact_class') == c)
                for c in ('Dispatch', 'Review', 'Remote-Approve', 'Remote-Deny')}
    windows = (meta or {}).get('windows', {})
    bbox = (meta or {}).get('bbox')

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        title=f"Altis Catastrophe Report — {label}", author="Altis")

    styles = getSampleStyleSheet()
    ink = colors.HexColor('#0B1622')
    teal = colors.HexColor('#1C6E8C')
    muted = colors.HexColor('#5A6B78')
    h1 = ParagraphStyle('h1', parent=styles['Title'], textColor=ink,
                        fontSize=22, spaceAfter=2, alignment=TA_LEFT)
    h2 = ParagraphStyle('h2', parent=styles['Heading2'], textColor=teal,
                        fontSize=13, spaceBefore=16, spaceAfter=6)
    body = ParagraphStyle('body', parent=styles['BodyText'], textColor=ink,
                          fontSize=9.5, leading=14, spaceAfter=4)
    small = ParagraphStyle('small', parent=styles['BodyText'], textColor=muted,
                           fontSize=8, leading=11)

    elems = []
    elems.append(Paragraph("Altis Catastrophe Report", h1))
    elems.append(Paragraph(f"{label} &nbsp;·&nbsp; Peril: Flood (riverine/surface water)", body))
    generated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    elems.append(Paragraph(
        f"Generated {generated} &nbsp;·&nbsp; Portfolio {portfolio_id} &nbsp;·&nbsp; "
        f"Ground-truth source: Sentinel-1 SAR change detection", small))
    elems.append(Spacer(1, 6))
    elems.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#D6DEE5')))

    # ── Event & exposure summary ─────────────────────────────────────────────
    elems.append(Paragraph("Event &amp; Exposure Summary", h2))
    rows = [
        ['Item', 'Value'],
        ['Event window (post)', f"{windows.get('post_start', '—')} → {windows.get('post_end', '—')}"],
        ['Baseline window (pre)', f"{windows.get('pre_start', '—')} → {windows.get('pre_end', '—')}"],
        ['Policies analyzed', f"{len(results):,}"],
        ['Policies in detected flood zone', f"{len(in_zone):,}"],
        ['Total insured value (TIV)', f"${tiv_total:,}"],
        ['TIV in detected flood zone', f"${tiv_zone:,}"],
        ['Estimated gross loss (depth-damage)',
         (f"${loss_mid:,} (range ${loss_low:,} – ${loss_high:,})" if loss_mid
          else f"${loss_low:,} – ${loss_high:,}") if sev_rows
         else 'n/a (no coverage data)'],
    ]
    if bbox:
        rows.append(['Affected zone bbox [W,S,E,N]',
                     f"[{bbox[0]:.3f}, {bbox[1]:.3f}, {bbox[2]:.3f}, {bbox[3]:.3f}]"])
    t = Table(rows, colWidths=[2.7 * inch, 3.5 * inch])
    t.setStyle(_table_style(colors))
    elems.append(t)

    # ── Triage distribution ──────────────────────────────────────────────────
    elems.append(Paragraph("Claims Triage Distribution", h2))
    total = max(len(results), 1)
    rows = [['Triage decision', 'Policies', 'Share']]
    for cls in ('Dispatch', 'Review', 'Remote-Approve', 'Remote-Deny'):
        rows.append([cls, f"{by_class[cls]:,}", f"{100 * by_class[cls] / total:.1f}%"])
    t = Table(rows, colWidths=[3.2 * inch, 1.5 * inch, 1.5 * inch])
    t.setStyle(_table_style(colors, numeric_from=1))
    elems.append(t)

    # ── Largest estimated losses ─────────────────────────────────────────────
    top = sorted(sev_rows, key=lambda r: num(r.get('severity_high_usd')), reverse=True)[:10]
    if top:
        elems.append(Paragraph("Largest Estimated Losses", h2))
        rows = [['Property', 'Depth (ft)', 'Est. loss range', 'FEMA zone']]
        for r in top:
            addr = str(r.get('address', r.get('property_id', '')))
            addr = addr if len(addr) <= 38 else addr[:37].rstrip(', ') + '…'
            zone = r.get('flood_zone') or '—'
            rows.append([
                addr, f"{num(r.get('max_depth_ft')):.1f}",
                f"${int(num(r.get('severity_low_usd'))):,} – ${int(num(r.get('severity_high_usd'))):,}",
                str(zone)])
        t = Table(rows, colWidths=[2.6 * inch, 0.8 * inch, 1.9 * inch, 0.9 * inch])
        t.setStyle(_table_style(colors, numeric_from=1))
        elems.append(t)

    # ── Methodology & data sources ───────────────────────────────────────────
    elems.append(Paragraph("Ground-Truth Methodology &amp; Data Sources", h2))
    for line in [
        "Flood extent: Sentinel-1 C-band SAR change detection (pre/post median "
        "composites, speckle-filtered, range-guarded Otsu threshold, permanent "
        "water masked via JRC Global Surface Water).",
        "Depth: water-surface elevation (robust high percentile of flooded-pixel "
        "elevation) minus DEM ground elevation, reported with a ±1σ interval.",
        "Cross-checks: Sentinel-2 optical MNDWI, dual-polarization (VH) SAR, and "
        "DEM-hydrology plausibility — disagreements route to manual review, never "
        "auto-resolved.",
        "Loss estimates: USACE/FEMA-style generic residential depth-damage curve "
        "applied to reported dwelling coverage; the range reflects the depth "
        "uncertainty interval. Reserving aid, not an adjuster estimate.",
        f"Scene counts this run: {meta.get('pre_scene_count', '—')} pre / "
        f"{meta.get('post_scene_count', '—')} post Sentinel-1, "
        f"{meta.get('optical_scene_count', '—')} Sentinel-2, "
        f"{meta.get('vh_scene_count', '—')} VH-capable.",
    ]:
        elems.append(Paragraph(f"• {line}", body))

    elems.append(Spacer(1, 10))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#D6DEE5')))
    elems.append(Paragraph(
        "Generated by Altis · Pre-decisional catastrophe intelligence. Loss figures "
        "are satellite-derived estimates for reserving support, not adjusted claims.",
        small))

    doc.build(elems)
    return buf.getvalue()


def _table_style(colors, numeric_from: Optional[int] = None, numeric_cols=None):
    """Shared header/zebra table style. If numeric_from is set, columns at that
    index and beyond are right-aligned (for depth/score columns)."""
    from reportlab.lib import colors as c
    from reportlab.platypus import TableStyle
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), c.HexColor('#0B1622')),
        ('TEXTCOLOR', (0, 0), (-1, 0), c.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('TEXTCOLOR', (0, 1), (-1, -1), c.HexColor('#0B1622')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [c.white, c.HexColor('#F2F6F9')]),
        ('GRID', (0, 0), (-1, -1), 0.5, c.HexColor('#D6DEE5')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]
    if numeric_from is not None:
        style.append(('ALIGN', (numeric_from, 1), (-1, -1), 'RIGHT'))
    return TableStyle(style)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fmt(v) -> str:
    if v is None:
        return '—'
    try:
        return f"{float(v):.3f}".rstrip('0').rstrip('.') if float(v) < 1 else f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)
