"""
generate_manual.py — Produces ALTIS_OPERATOR_MANUAL.pdf, a complete
operator's guide to running and demoing the Altis MVP.

Run:  python docs/generate_manual.py
Output: docs/ALTIS_OPERATOR_MANUAL.pdf

Pure reportlab, no app imports — safe to run anywhere reportlab is installed.
"""
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, ListFlowable, ListItem, PageBreak, Paragraph,
    SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ── Brand palette (mirrors frontend/src/styles/globals.css) ──────────────────
TEAL = colors.HexColor("#1E7A99")      # darkened teal for print legibility
TEAL_LIGHT = colors.HexColor("#A8D4E6")
INK = colors.HexColor("#0C1419")
SLATE = colors.HexColor("#46606E")
DISPATCH = colors.HexColor("#D32F2F")
APPROVE = colors.HexColor("#2E7D52")
REVIEW = colors.HexColor("#C77B1E")
RULE = colors.HexColor("#D5DEE3")
ZEBRA = colors.HexColor("#F2F6F8")

styles = getSampleStyleSheet()


def S(name, **kw):
    base = kw.pop("parent", styles["Normal"])
    return ParagraphStyle(name, parent=base, **kw)


H1 = S("H1", fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=INK,
       spaceBefore=4, spaceAfter=6)
H2 = S("H2", fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=TEAL,
       spaceBefore=16, spaceAfter=4)
H3 = S("H3", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=INK,
       spaceBefore=10, spaceAfter=2)
BODY = S("Body", fontSize=10, leading=15, textColor=INK, spaceAfter=6,
         alignment=TA_LEFT)
SMALL = S("Small", fontSize=8.5, leading=12, textColor=SLATE)
LEAD = S("Lead", fontSize=11, leading=16, textColor=SLATE, spaceAfter=8)
CODE = S("Code", fontName="Courier", fontSize=9, leading=13, textColor=INK,
         backColor=ZEBRA, borderPadding=6, spaceBefore=2, spaceAfter=8)
STEP = S("Step", fontSize=10, leading=15, textColor=INK, leftIndent=2)


def bullets(items, style=BODY):
    return ListFlowable(
        [ListItem(Paragraph(t, style), leftIndent=12, value="•") for t in items],
        bulletType="bullet", start="•", leftIndent=14, spaceBefore=2, spaceAfter=8,
    )


def numbered(items, style=STEP):
    return ListFlowable(
        [ListItem(Paragraph(t, style), leftIndent=14) for t in items],
        bulletType="1", leftIndent=18, spaceBefore=2, spaceAfter=8,
    )


def rule(color=RULE, w=0.8, space=8):
    return HRFlowable(width="100%", thickness=w, color=color,
                      spaceBefore=space, spaceAfter=space)


def kv_table(rows, col0=1.7 * inch, col1=4.6 * inch):
    t = Table([[Paragraph(f"<b>{a}</b>", BODY), Paragraph(b, BODY)] for a, b in rows],
              colWidths=[col0, col1])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def grid_table(header, rows, widths):
    data = [[Paragraph(f"<b>{h}</b>", SMALL) for h in header]]
    data += [[Paragraph(str(c), SMALL) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    t.setStyle(TableStyle(style))
    return t


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(0.9 * inch, 0.7 * inch, 7.6 * inch, 0.7 * inch)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(SLATE)
    canvas.drawString(0.9 * inch, 0.55 * inch, "Altis — Operator's Manual")
    canvas.drawRightString(7.6 * inch, 0.55 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build(path):
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.85 * inch, bottomMargin=0.9 * inch,
        title="Altis Operator's Manual", author="Altis",
    )
    E = []  # story

    # ── Cover ────────────────────────────────────────────────────────────────
    E += [Spacer(1, 1.4 * inch)]
    E += [Paragraph("ALTIS", S("Brand", fontName="Helvetica-Bold", fontSize=42,
                               leading=46, textColor=TEAL))]
    E += [Spacer(1, 6)]
    E += [Paragraph("Operator's Manual", S("Sub", fontName="Helvetica", fontSize=18,
                                           leading=22, textColor=INK))]
    E += [Spacer(1, 10), rule(TEAL, 1.4, 4), Spacer(1, 10)]
    E += [Paragraph(
        "AI-powered flood damage triage for property &amp; casualty insurers. "
        "This guide covers everything from first launch to running a full "
        "investor demo, plus how the science works and what the system can and "
        "cannot do today.", LEAD)]
    E += [Spacer(1, 0.5 * inch)]
    E += [kv_table([
        ("Version", "MVP build — branch claude/wizardly-volta-bj8wdd"),
        ("Audience", "Operators, demo presenters, evaluators"),
        ("Two processes", "FastAPI backend (port 8000) + React frontend (port 5173)"),
        ("Keys needed", "One free Mapbox token (globe). No paid APIs for the demo."),
    ])]
    E += [PageBreak()]

    # ── 1. What Altis is ─────────────────────────────────────────────────────
    E += [Paragraph("1 — What Altis is", H1), rule()]
    E += [Paragraph(
        "After a major flood, a carrier faces thousands of claims and a fixed "
        "number of adjusters. Sending someone to every property wastes days on "
        "homes that were barely touched, while the worst-hit wait. Altis turns "
        "satellite radar into a property-level triage within hours of the event "
        "— before anyone drives anywhere — telling you who to dispatch today, "
        "who can be resolved remotely, and how confident it is in each call.", BODY)]
    E += [Paragraph("What it produces, per property", H3)]
    E += [bullets([
        "A <b>triage decision</b>: Dispatch, Remote-Approve, Remote-Deny, or Review.",
        "An <b>estimated flood depth</b> with an uncertainty interval (± feet).",
        "A <b>calibrated confidence</b> score, validated against FEMA declarations.",
        "A plain-English <b>“why this decision”</b> breakdown of every factor.",
    ])]
    E += [Paragraph("The five things a demo should show", H3)]
    E += [bullets([
        "The <b>3D globe</b> flying to an event with ~1,000 triaged properties.",
        "A <b>severity-ranked dispatch queue</b> — the worklist, not a flat list.",
        "<b>Universal portfolio upload</b> — any messy carrier spreadsheet, fuzzy-mapped.",
        "The <b>adjuster feedback loop</b> — a human correction that re-trains the model.",
        "A one-click <b>audit-ready PDF</b> a carrier could file.",
    ])]

    # ── 2. Architecture ──────────────────────────────────────────────────────
    E += [Paragraph("2 — How the system fits together", H1), rule()]
    E += [Paragraph(
        "Three independent units. For a local demo you only run the first two; "
        "the monitor is optional and needs internet.", BODY)]
    E += [grid_table(
        ["Unit", "What it is", "Where", "Needed for demo?"],
        [["Frontend", "React + Mapbox GL globe UI", "frontend/", "Yes (port 5173)"],
         ["Backend", "FastAPI + SQLite API", "backend/", "Yes (port 8000)"],
         ["Pipeline", "Sentinel-1 SAR flood detection", "pipeline/", "Pre-computed; no"],
         ["Validation", "FEMA calibration + precision/recall", "validation/", "Optional"],
         ["Monitor", "NHC/USGS event detection", "monitor/", "Optional"]],
        widths=[0.9 * inch, 2.5 * inch, 1.1 * inch, 1.8 * inch])]
    E += [Spacer(1, 4)]
    E += [Paragraph(
        "The frontend talks to the backend only through <font face='Courier'>/api/...</font>. "
        "The Vite dev server proxies that to port 8000 automatically, so you "
        "never configure URLs for local use.", SMALL)]

    # ── 3. First-time setup ──────────────────────────────────────────────────
    E += [PageBreak()]
    E += [Paragraph("3 — First-time setup", H1), rule()]
    E += [Paragraph("Prerequisites", H3)]
    E += [bullets([
        "<b>Python 3.10+</b> and <b>Node 18+</b> (check: <font face='Courier'>python --version</font>, <font face='Courier'>node --version</font>).",
        "A free <b>Mapbox token</b> from account.mapbox.com (free tier = 50,000 map loads/month).",
        "Git, and a clone of the repo on the <font face='Courier'>claude/wizardly-volta-bj8wdd</font> branch.",
    ])]
    E += [Paragraph("Step A — Backend (run from the repo root)", H3)]
    E += [Paragraph(
        "python -m venv .venv<br/>"
        ".venv\\Scripts\\Activate.ps1 &nbsp;&nbsp;# Windows PowerShell<br/>"
        "# (macOS/Linux: source .venv/bin/activate)<br/>"
        "pip install -r requirements.txt", CODE)]
    E += [Paragraph(
        "PowerShell note: if activation is blocked, run "
        "<font face='Courier'>Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass</font> "
        "once, then re-run the activate line. When active, your prompt starts with "
        "<font face='Courier'>(.venv)</font>.", SMALL)]
    E += [Paragraph("Step B — Start the backend", H3)]
    E += [Paragraph("uvicorn backend.main:app --reload --port 8000", CODE)]
    E += [Paragraph("You should see <font face='Courier'>Altis backend ready at "
                    "http://localhost:8000</font>. Leave this window running.", BODY)]
    E += [Paragraph("Step C — Frontend (new terminal, from frontend/)", H3)]
    E += [Paragraph(
        "cd frontend<br/>"
        "npm install<br/>"
        "copy .env.example .env &nbsp;&nbsp;# macOS/Linux: cp .env.example .env<br/>"
        "# open .env and paste your token:<br/>"
        "# VITE_MAPBOX_TOKEN=pk.your_real_token<br/>"
        "npm run dev", CODE)]
    E += [Paragraph(
        "Open <b>http://localhost:5173</b>. The globe should render and slowly "
        "rotate. If it's black, the token isn't loaded — see Troubleshooting.", BODY)]

    # ── 4. Interface tour ────────────────────────────────────────────────────
    E += [PageBreak()]
    E += [Paragraph("4 — The interface, panel by panel", H1), rule()]
    E += [Paragraph("Left sidebar", H3)]
    E += [kv_table([
        ("Events", "Pick the flood event. The globe flies there and loads properties."),
        ("Analysis", "Score an uploaded portfolio against the selected event."),
        ("Dispatch Queue", "Severity × coverage-ranked worklist — who to send first."),
        ("Operations", "Monitor → pipeline run queue. Queue and advance runs."),
        ("Reports", "Calibration reliability chart + the audit PDF download."),
    ])]
    E += [Paragraph("Top bar", H3)]
    E += [kv_table([
        ("Upload Portfolio", "Bring in a carrier spreadsheet (.csv/.xlsx/.xls/.pdf)."),
        ("Claims Grid", "Full-screen sortable/filterable table with bulk CSV export."),
        ("KPI strip", "Properties, dispatch count, remote-resolved, estimated savings."),
    ])]
    E += [Paragraph("The globe", H3)]
    E += [bullets([
        "Pins are color-coded by decision: <font color='#D32F2F'><b>red = Dispatch</b></font>, "
        "<font color='#2E7D52'><b>green = Remote-resolved</b></font>, "
        "<font color='#C77B1E'><b>amber = Review</b></font>.",
        "Red Dispatch pins <b>glow and grow</b> as you zoom in; they're emphasized on purpose.",
        "<b>Addresses label</b> only when you zoom in close, to keep the wide view clean.",
        "Click any pin to open the property drawer on the right.",
    ])]

    # ── 5. Core workflows ────────────────────────────────────────────────────
    E += [PageBreak()]
    E += [Paragraph("5 — Core workflows", H1), rule()]

    E += [Paragraph("5.1  Inspect a single property", H3)]
    E += [numbered([
        "Click any pin on the globe.",
        "The right drawer shows the SAR before/after, estimated depth (± uncertainty), "
        "the confidence gauge, and the “Why this decision” factor breakdown.",
        "Scroll to <b>Adjuster Verdict</b>: click 👍 Agree or 👎 Disagree, optionally pick a "
        "corrected class and add a note, then Submit. This writes to the ground-truth table.",
    ])]

    E += [Paragraph("5.2  Work the dispatch queue", H3)]
    E += [numbered([
        "Sidebar → <b>Dispatch Queue</b>. Rows are ranked by severity × coverage exposure.",
        "#1 is what the CAT team hits first. Click a row to inspect that property.",
        "Use <b>Open grid →</b> to jump into the full table view.",
    ])]

    E += [Paragraph("5.3  Claims data grid", H3)]
    E += [numbered([
        "Top bar → <b>Claims Grid</b> (or “Open grid” from the queue).",
        "Sort by any column, filter by triage decision, or search.",
        "Tick rows and <b>Export selected</b> (or Export filtered) to CSV — the manager's "
        "spreadsheet-native view.",
    ])]

    E += [Paragraph("5.4  Upload a portfolio (the headline feature)", H3)]
    E += [numbered([
        "Top bar → <b>Upload Portfolio</b>. Drag in a .csv/.xlsx/.xls/.pdf — any column names.",
        "Altis fuzzy-maps your columns to Policy / Address / Coverage / City / State / Zip and "
        "shows a <b>review-and-confirm</b> screen, color-coded by match confidence "
        "(teal = high, amber = medium, dim = unmatched).",
        "Override any wrong mapping from the dropdowns; check the preview rows and the "
        "flagged-address callout.",
        "Click <b>Confirm &amp; Geocode</b>. The globe flies to your geocoded book.",
        "Sidebar → <b>Analysis</b> → analyze against the matching event, then revisit "
        "Dispatch Queue in <i>portfolio</i> mode.",
    ])]
    E += [Paragraph(
        "Try the included samples in <font face='Courier'>samples/</font>: "
        "demo_portfolio_harvey.csv and demo_portfolio_charlotte.csv have deliberately "
        "messy, real-world carrier headers. <b>Match the sample to its event</b> — Harvey "
        "samples → Hurricane Harvey, Charlotte/Ian samples → Hurricane Ian.", SMALL)]

    E += [Paragraph("5.5  Audit PDF + validation", H3)]
    E += [numbered([
        "Sidebar → <b>Reports</b> → <b>Download audit PDF</b>: a carrier-ready document with "
        "methodology, satellite scene sources + dates, the triage table, top dispatch "
        "priorities, and FEMA precision/recall.",
        "The reliability chart appears once you've generated calibration (see §7).",
    ])]

    E += [Paragraph("5.6  Operations — the always-on loop", H3)]
    E += [numbered([
        "Sidebar → <b>Operations</b> shows the monitor → pipeline run queue.",
        "Click <b>+ Queue run</b> to enqueue one manually; click a status pill to advance it "
        "(queued → running → complete).",
    ])]

    # ── 6. How the triage works ──────────────────────────────────────────────
    E += [PageBreak()]
    E += [Paragraph("6 — How the triage actually works", H1), rule()]
    E += [Paragraph(
        "This is the section that wins technical due-diligence. Altis is not a black "
        "box — every decision is traceable to physical signals.", LEAD)]
    E += [Paragraph("Detection", H3)]
    E += [bullets([
        "<b>SAR change detection.</b> Sentinel-1 radar sees through clouds (which blanket "
        "every hurricane). Altis compares <i>before</i> vs <i>after</i> backscatter, with a "
        "speckle filter and an Otsu threshold guarded to a physical open-water range.",
        "<b>Optical cross-check.</b> When a cloud-free Sentinel-2 view exists, MNDWI confirms "
        "or contradicts the radar call — advisory only, never blocking.",
        "<b>Depth.</b> Water-surface elevation minus ground elevation from a DEM, clamped to "
        "physically plausible residential depths.",
    ])]
    E += [Paragraph("Trust &amp; safety", H3)]
    E += [bullets([
        "<b>Uncertainty interval.</b> Depth is reported ± a 1σ interval driven by the DEM's "
        "own vertical accuracy — never shown more precise than the elevation data supports.",
        "<b>Ensemble disagreement → Review.</b> Three members vote (SAR, optical, DEM-hydrology). "
        "When they genuinely conflict, the property is sent to manual Review rather than a "
        "confident automated call.",
        "<b>Calibration.</b> Raw scores are isotonic/Platt-calibrated so a “78% confidence” "
        "means what it says, validated against real FEMA disaster declarations.",
        "<b>Human-in-the-loop.</b> Adjuster verdicts (§5.1) become per-property ground truth "
        "that overrides coarse zip-level FEMA labels and feeds the next calibration run.",
    ])]

    # ── 7. Validation & monitor (optional) ───────────────────────────────────
    E += [Paragraph("7 — Optional: real validation &amp; live detection", H1), rule()]
    E += [Paragraph("Both need internet but no API key.", BODY)]
    E += [Paragraph("FEMA validation + calibration", H3)]
    E += [Paragraph("python validation/accuracy_check.py --event harvey", CODE)]
    E += [Paragraph(
        "Writes outputs/validation_harvey.md and outputs/calibration_harvey.json, which "
        "populate the Reports reliability chart and the PDF's precision/recall section. Any "
        "adjuster verdicts you submitted are merged in as property-level ground truth.", BODY)]
    E += [Paragraph("Live event detection", H3)]
    E += [Paragraph("python monitor/monitor.py &nbsp;&nbsp;# one pass; --loop to run continuously", CODE)]
    E += [Paragraph(
        "Detected NHC/USGS flood events are auto-queued as pipeline runs and appear in the "
        "Operations panel — the monitor → pipeline loop, closed.", BODY)]

    # ── 8. Coverage & limits ─────────────────────────────────────────────────
    E += [PageBreak()]
    E += [Paragraph("8 — Coverage, limits &amp; honest answers", H1), rule()]
    E += [Paragraph(
        "Read this before a demo. These are the questions a sharp evaluator asks, and the "
        "true answers.", LEAD)]
    E += [Paragraph("“Why only two events? Does it work anywhere?”", H3)]
    E += [bullets([
        "The <b>technology is global</b>: Sentinel-1 images the entire Earth every ~6–12 days. "
        "Nothing in the algorithm is specific to Texas or Florida — the pipeline takes a "
        "bounding box + dates as input.",
        "The demo ships with <b>Harvey and Ian pre-computed</b> so it runs instantly with no "
        "Earth Engine login. That's a packaging choice, not a tech limit.",
        "Adding a new event = add a config dict (bbox + before/after dates) and run the "
        "pipeline. See §9.",
    ])]
    E += [Paragraph("Real constraints today (don't oversell)", H3)]
    E += [bullets([
        "<b>You need an actual flood with known dates.</b> SAR compares before vs after, so you "
        "can't point at a dry location on a random day and get a result.",
        "<b>Geocoding is US-only</b> (free US Census geocoder). International addresses need a "
        "different geocoder swapped in — small change, not present today.",
        "<b>Live satellite tiles need Google Earth Engine credentials</b> (a free service "
        "account). Without them the app serves synthetic SAR thumbnails so the demo never "
        "hard-fails.",
        "<b>“Fully accurate” is the wrong claim.</b> Accuracy varies with terrain, urban radar "
        "artifacts, DEM quality, and cloud cover — which is exactly why Altis reports "
        "calibrated confidence and uncertainty instead of a single number.",
    ])]
    E += [Paragraph(
        "The defensible one-liner: <i>“Global satellite coverage, US go-to-market today, and "
        "adding a new flood event is a config + pipeline run, not a rebuild.”</i>", BODY)]

    # ── 9. Adding a new event ────────────────────────────────────────────────
    E += [Paragraph("9 — Adding a new flood event", H1), rule()]
    E += [numbered([
        "In <font face='Courier'>pipeline/config.py</font>, copy the HARVEY dict to a new one "
        "(e.g. MILTON) and set: event_id, label, the [west, south, east, north] bbox, and the "
        "pre_/post_ date windows around the flood.",
        "Register it in the <font face='Courier'>EVENTS</font> dict at the bottom of the file.",
        "Set up Google Earth Engine (service account) and run "
        "<font face='Courier'>python pipeline/03_flood_pipeline.py</font> for the new event "
        "to generate its outputs/ CSVs.",
        "Restart the backend. The new event appears in the sidebar automatically.",
    ])]
    E += [Paragraph(
        "For production hosting (Vercel + Render, Postgres migration, GEE service account, "
        "hardening checklist, ~$10–30/mo pilot cost), see DEPLOYMENT.md in the repo.", SMALL)]

    # ── 10. Troubleshooting ──────────────────────────────────────────────────
    E += [Paragraph("10 — Troubleshooting", H1), rule()]
    E += [grid_table(
        ["Symptom", "Fix"],
        [["Globe is black / “Set VITE_MAPBOX_TOKEN” in console",
          "Put a real token in frontend/.env, then restart npm run dev."],
         ["Frontend loads but no data appears",
          "Make sure the backend is running on port 8000 (the dev server proxies /api to it)."],
         ["Upload says “could not geocode”",
          "The Census geocoder needs internet and real US addresses."],
         ["PDF / report 404",
          "Generate calibration first (§7) for the validation section; the rest works regardless."],
         ["PowerShell blocks .venv activation",
          "Run Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass, then activate again."],
         ["Port already in use",
          "Stop the other process, or change --port (and the proxy target in vite.config.js)."]],
        widths=[2.9 * inch, 3.4 * inch])]
    E += [Spacer(1, 10), rule()]
    E += [Paragraph(
        "Quick reference also lives in the repo: RUN_LOCAL.md (setup + demo script), "
        "DEPLOYMENT.md (hosting), and README.md (overview + API surface).", SMALL)]

    doc.build(E, onFirstPage=_footer, onLaterPages=_footer)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "ALTIS_OPERATOR_MANUAL.pdf")
    build(out)
    print(f"Wrote {out}")
