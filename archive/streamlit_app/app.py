"""
app.py  —  Altis Flood Intelligence Dashboard
Run:  streamlit run app/app.py  (from the altis-mvp/ root)
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import os, sys, hashlib
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_data import generate_event_data

# ── PAGE CONFIG ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Altis",
    page_icon="🛰",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CONSTANTS ──────────────────────────────────────────────────────────────────
C = {
    "Dispatch":       "#FF4444",
    "Remote-Approve": "#4CAF82",
    "Remote-Deny":    "#6B8FA3",
    "Review":         "#FFB347",
}
C_BG = {
    "Dispatch":       "rgba(255,68,68,0.10)",
    "Remote-Approve": "rgba(76,175,130,0.10)",
    "Remote-Deny":    "rgba(107,143,163,0.10)",
    "Review":         "rgba(255,179,71,0.10)",
}

EVENTS = {
    "harvey": {"label": "Hurricane Harvey", "sub": "Harris County, TX  •  August 2017",  "lat": 29.700, "lon": -95.500, "zoom": 10},
    "ian":    {"label": "Hurricane Ian",    "sub": "Charlotte County, FL  •  Sept 2022", "lat": 26.970, "lon": -82.050, "zoom": 10},
}

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
OUTPUTS_DIR = os.path.join(os.path.dirname(BASE_DIR), "outputs")
ROWS_PER_PAGE = 20

# ── CSS ────────────────────────────────────────────────────────────────────────
def inject_css():
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');

*, *::before, *::after {
    font-family: 'Plus Jakarta Sans', -apple-system, sans-serif !important;
    box-sizing: border-box;
}

/* hide streamlit chrome */
#MainMenu, footer, header,
[data-testid="stDecoration"],
[data-testid="stHeader"],
[data-testid="collapsedControl"],
section[data-testid="stSidebar"] { display: none !important; }

/* background */
.stApp, .stApp > div { background-color: #000 !important; }
.main .block-container {
    padding: 0 !important;
    max-width: 100% !important;
}

/* all text */
p, span, div, label, td, th { color: inherit; }

/* buttons — base */
.stButton > button {
    font-weight: 600 !important;
    letter-spacing: 0.025em !important;
    border-radius: 4px !important;
    transition: all 0.15s ease !important;
    border: 1px solid #222 !important;
    background: transparent !important;
    color: #8B9AA3 !important;
    padding: 0.35rem 0.9rem !important;
    font-size: 0.8rem !important;
    line-height: 1.4 !important;
}
.stButton > button:hover {
    border-color: #A8D4E6 !important;
    color: #A8D4E6 !important;
    background: rgba(168,212,230,0.06) !important;
}
.stButton > button:focus { box-shadow: none !important; outline: none !important; }

/* CTA */
div[data-testid="stButton"].cta-btn > button,
.cta-wrap .stButton > button {
    background: #A8D4E6 !important;
    color: #000 !important;
    border: none !important;
    padding: 0.65rem 2.4rem !important;
    font-size: 0.92rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.04em !important;
}
.cta-wrap .stButton > button:hover { background: #BEE0EF !important; color: #000 !important; }

/* row arrow button */
.row-btn .stButton > button {
    border: none !important;
    color: #3A5060 !important;
    font-size: 1rem !important;
    padding: 0 0.3rem !important;
    min-height: 0 !important;
    height: 28px !important;
}
.row-btn .stButton > button:hover {
    color: #A8D4E6 !important;
    background: transparent !important;
    border: none !important;
}

/* back button */
.back-btn .stButton > button {
    color: #8B9AA3 !important;
    border: none !important;
    padding: 0.25rem 0.5rem !important;
    font-size: 0.82rem !important;
}
.back-btn .stButton > button:hover { color: #A8D4E6 !important; }

/* close detail button */
.close-btn .stButton > button {
    color: #555 !important;
    border: none !important;
    padding: 0 0.4rem !important;
    font-size: 1.1rem !important;
    line-height: 1 !important;
    min-height: 0 !important;
    height: 28px !important;
}
.close-btn .stButton > button:hover { color: #FFF !important; }

/* text input */
.stTextInput > div > div > input {
    background: #080808 !important;
    border: 1px solid #1A1A1A !important;
    color: #FFF !important;
    border-radius: 4px !important;
    font-size: 0.84rem !important;
    padding: 0.45rem 0.75rem !important;
}
.stTextInput > div > div > input:focus {
    border-color: #A8D4E6 !important;
    box-shadow: 0 0 0 1px rgba(168,212,230,0.15) !important;
    outline: none !important;
}
.stTextInput > div > div > input::placeholder { color: #333 !important; }
.stTextInput > label, .stTextInput label {
    color: #444 !important;
    font-size: 0.7rem !important;
    letter-spacing: 0.08em !important;
}

/* segmented control / pills */
.stSegmentedControl > div,
[data-testid="stSegmentedControl"] > div {
    background: #080808 !important;
    border: 1px solid #1A1A1A !important;
    border-radius: 5px !important;
    padding: 2px !important;
    gap: 2px !important;
}
[data-testid="stSegmentedControl"] label {
    color: #555 !important;
    font-size: 0.8rem !important;
    font-weight: 500 !important;
    border-radius: 3px !important;
    padding: 0.3rem 0.9rem !important;
}
[data-testid="stSegmentedControl"] label[data-selected="true"] {
    background: rgba(168,212,230,0.12) !important;
    color: #A8D4E6 !important;
}

/* plotly chart */
[data-testid="stPlotlyChart"] { border-radius: 6px; overflow: hidden; }

/* image */
[data-testid="stImage"] img { border-radius: 3px !important; }

/* column gap fix */
[data-testid="stHorizontalBlock"] { gap: 0 !important; }

/* markdown reset */
.stMarkdown p { margin: 0 !important; }

/* scrollbar */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: #000; }
::-webkit-scrollbar-thumb { background: #1A1A1A; border-radius: 2px; }
::-webkit-scrollbar-thumb:hover { background: #282828; }
</style>
""", unsafe_allow_html=True)


# ── DATA LOADING ───────────────────────────────────────────────────────────────
@st.cache_data
def load_data(event_id: str) -> pd.DataFrame:
    path = os.path.join(OUTPUTS_DIR, f"{event_id}_final.csv")
    if os.path.exists(path):
        df = pd.read_csv(path)
    else:
        df = generate_event_data(event_id, n=1000, save=True)
    if "latitude" not in df.columns:
        rng = np.random.RandomState(42)
        cfg = EVENTS[event_id]
        df["latitude"]  = rng.normal(cfg["lat"], 0.045, len(df))
        df["longitude"] = rng.normal(cfg["lon"], 0.055, len(df))
    return df


# ── SAR IMAGE GENERATION ───────────────────────────────────────────────────────
def _sar_images(property_id: str, depth_ft: float):
    """Synthetic SAR-like images (blue-gray false color, realistic speckle)."""
    seed = int(hashlib.md5(property_id.encode()).hexdigest()[:8], 16) % 100_000
    rng  = np.random.RandomState(seed)
    H, W = 180, 260

    base = rng.exponential(scale=0.32, size=(H, W)).astype(np.float32)

    for _ in range(rng.randint(5, 12)):
        bx = rng.randint(5, W - 20)
        by = rng.randint(5, H - 18)
        bw = rng.randint(8, 22)
        bh = rng.randint(6, 14)
        base[by:by+bh, bx:bx+bw] += rng.uniform(0.35, 1.0)

    ry = rng.randint(H // 3, 2 * H // 3)
    base[ry:ry+2, :] *= rng.uniform(0.25, 0.40)

    p97   = np.percentile(base, 97) + 1e-8
    pre   = np.clip(base / p97, 0, 1)
    post_b = base.copy()

    if depth_ft > 0.2:
        inten = min(depth_ft / 7.0, 1.0)
        cx = rng.randint(W // 4, 3 * W // 4)
        cy = rng.randint(H // 2, H - 15)
        rx = int(38 + W * 0.17 * inten)
        ry2 = int(28 + H * 0.17 * inten)
        Y, X = np.ogrid[:H, :W]
        m1   = ((X - cx)**2 / rx**2 + (Y - cy)**2 / ry2**2) <= 1.0
        post_b[m1] = rng.uniform(0.01, 0.06, m1.sum())
        if depth_ft > 1.5:
            cx2, cy2 = rng.randint(15, W - 25), rng.randint(H // 3, H - 15)
            r2 = int(18 + 22 * inten)
            m2 = (X - cx2)**2 + (Y - cy2)**2 <= r2**2
            post_b[m2] = rng.uniform(0.01, 0.05, m2.sum())

    p97b = np.percentile(post_b, 97) + 1e-8
    post = np.clip(post_b / p97b, 0, 1)

    def to_rgb(arr):
        rgb = np.zeros((H, W, 3), dtype=np.uint8)
        rgb[:,:,0] = (arr * 155).astype(np.uint8)
        rgb[:,:,1] = (arr * 178).astype(np.uint8)
        rgb[:,:,2] = (arr * 205).astype(np.uint8)
        return rgb

    return Image.fromarray(to_rgb(pre)), Image.fromarray(to_rgb(post))


# ── MAP ────────────────────────────────────────────────────────────────────────
def _build_map(df: pd.DataFrame, event_id: str) -> go.Figure:
    cfg  = EVENTS[event_id]
    fig  = go.Figure()
    order = ["Dispatch", "Remote-Approve", "Remote-Deny", "Review"]

    for cat in order:
        sub = df[df["impact_class"] == cat]
        if sub.empty:
            continue
        fig.add_trace(go.Scattermapbox(
            lat=sub["latitude"],
            lon=sub["longitude"],
            mode="markers",
            marker=dict(size=6, color=C[cat], opacity=0.72),
            name=cat,
            text=sub["address"],
            customdata=np.column_stack([sub["max_depth_ft"].values, sub["confidence_score"].values]),
            hovertemplate=(
                "<b>%{text}</b><br>"
                f"<span style='color:{C[cat]}'>{cat}</span><br>"
                "Depth: %{customdata[0]:.1f} ft &nbsp; Confidence: %{customdata[1]}%"
                "<extra></extra>"
            ),
        ))

    fig.update_layout(
        mapbox_style="carto-darkmatter",
        mapbox_zoom=cfg["zoom"],
        mapbox_center={"lat": cfg["lat"], "lon": cfg["lon"]},
        margin=dict(r=0, t=0, l=0, b=0),
        paper_bgcolor="#000",
        plot_bgcolor="#000",
        legend=dict(
            bgcolor="rgba(8,8,8,0.92)",
            bordercolor="#1A1A1A",
            borderwidth=1,
            font=dict(color="#CCC", size=11, family="Plus Jakarta Sans"),
            x=0.01, y=0.99,
        ),
        height=310,
    )
    return fig


# ── SCREEN 1: EVENT SUMMARY ────────────────────────────────────────────────────
def screen_summary():
    event_id = st.session_state.event
    df       = load_data(event_id)
    cfg      = EVENTS[event_id]

    total    = len(df)
    dispatch = (df["impact_class"] == "Dispatch").sum()
    remote   = df["impact_class"].isin(["Remote-Approve", "Remote-Deny"]).sum()
    savings  = remote * 750

    # ── HEADER
    h_left, h_right = st.columns([1, 1])
    with h_left:
        st.markdown("""
<div style="padding:1.6rem 2rem 1.2rem 2rem;display:flex;align-items:center;gap:0.65rem">
  <svg width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="7" y="6" width="8" height="10" rx="1.5" fill="#A8D4E6"/>
    <rect x="0" y="8" width="6" height="6" rx="0.5" fill="#A8D4E6" opacity="0.65"/>
    <rect x="16" y="8" width="6" height="6" rx="0.5" fill="#A8D4E6" opacity="0.65"/>
    <rect x="1.5" y="9.5" width="1.5" height="1.5" fill="#000" opacity="0.6"/>
    <rect x="3.5" y="9.5" width="1.5" height="1.5" fill="#000" opacity="0.6"/>
    <rect x="1.5" y="11.5" width="1.5" height="1.5" fill="#000" opacity="0.6"/>
    <rect x="3.5" y="11.5" width="1.5" height="1.5" fill="#000" opacity="0.6"/>
    <rect x="16.5" y="9.5" width="1.5" height="1.5" fill="#000" opacity="0.6"/>
    <rect x="18.5" y="9.5" width="1.5" height="1.5" fill="#000" opacity="0.6"/>
    <rect x="16.5" y="11.5" width="1.5" height="1.5" fill="#000" opacity="0.6"/>
    <rect x="18.5" y="11.5" width="1.5" height="1.5" fill="#000" opacity="0.6"/>
    <circle cx="5.5" cy="18.5" r="2.5" fill="#A8D4E6" opacity="0.7"/>
    <line x1="7.8" y1="16.2" x2="9" y2="16" stroke="#A8D4E6" stroke-width="1"/>
  </svg>
  <span style="font-size:1.1rem;font-weight:800;color:#FFF;letter-spacing:0.12em">ALTIS</span>
</div>
""", unsafe_allow_html=True)

    with h_right:
        st.markdown('<div style="padding:1.3rem 2rem 0 0;display:flex;justify-content:flex-end">', unsafe_allow_html=True)
        ev = st.segmented_control(
            "event_toggle", ["harvey", "ian"],
            format_func=lambda x: "Harvey 2017" if x == "harvey" else "Ian 2022",
            default=event_id, label_visibility="collapsed",
            key="sum_event_toggle",
        )
        if ev and ev != st.session_state.event:
            st.session_state.event = ev
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ── DIVIDER
    st.markdown('<div style="border-top:1px solid #111;margin:0 2rem"></div>', unsafe_allow_html=True)

    # ── EVENT NAME
    st.markdown(f"""
<div style="padding:1.8rem 2rem 0.6rem 2rem">
  <p style="font-size:0.7rem;font-weight:600;letter-spacing:0.14em;color:#A8D4E6;text-transform:uppercase;margin-bottom:0.25rem">
    LIVE TRIAGE REPORT
  </p>
  <h1 style="font-size:2rem;font-weight:800;color:#FFF;margin:0;letter-spacing:-0.02em">{cfg['label']}</h1>
  <p style="font-size:0.82rem;color:#4A5568;margin-top:0.3rem">{cfg['sub']}</p>
</div>
""", unsafe_allow_html=True)

    # ── KPI CARDS
    st.markdown('<div style="padding:0.8rem 2rem 0 2rem">', unsafe_allow_html=True)

    kpi_html = f"""
<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1.4fr;gap:1rem">

  <div style="background:#080808;border:1px solid #141414;border-radius:6px;padding:1.4rem 1.6rem">
    <p style="font-size:0.68rem;font-weight:600;letter-spacing:0.12em;color:#3A5060;text-transform:uppercase;margin-bottom:0.6rem">
      PROPERTIES ANALYZED
    </p>
    <p style="font-size:2.6rem;font-weight:800;color:#FFF;margin:0;letter-spacing:-0.03em;line-height:1">
      {total:,}
    </p>
  </div>

  <div style="background:#080808;border:1px solid #141414;border-radius:6px;padding:1.4rem 1.6rem">
    <p style="font-size:0.68rem;font-weight:600;letter-spacing:0.12em;color:#3A5060;text-transform:uppercase;margin-bottom:0.6rem">
      FLAGGED FOR DISPATCH
    </p>
    <p style="font-size:2.6rem;font-weight:800;color:#FF4444;margin:0;letter-spacing:-0.03em;line-height:1">
      {dispatch:,}
    </p>
  </div>

  <div style="background:#080808;border:1px solid #141414;border-radius:6px;padding:1.4rem 1.6rem">
    <p style="font-size:0.68rem;font-weight:600;letter-spacing:0.12em;color:#3A5060;text-transform:uppercase;margin-bottom:0.6rem">
      RESOLVED REMOTELY
    </p>
    <p style="font-size:2.6rem;font-weight:800;color:#4CAF82;margin:0;letter-spacing:-0.03em;line-height:1">
      {remote:,}
    </p>
  </div>

  <div style="background:#080808;border:1px solid #1A2A1A;border-radius:6px;padding:1.4rem 1.6rem;
              box-shadow:0 0 40px rgba(76,175,130,0.06)">
    <p style="font-size:0.68rem;font-weight:600;letter-spacing:0.12em;color:#2A4A3A;text-transform:uppercase;margin-bottom:0.6rem">
      ESTIMATED SAVINGS
    </p>
    <p style="font-size:2.9rem;font-weight:800;color:#A8D4E6;margin:0;letter-spacing:-0.03em;line-height:1">
      ${savings:,.0f}
    </p>
    <p style="font-size:0.72rem;color:#2A4A3A;margin-top:0.5rem">
      {remote:,} inspections avoided @ $750 each
    </p>
  </div>

</div>
"""
    st.markdown(kpi_html, unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── HEADLINE
    pct_remote = remote / total * 100
    headline = (
        f"After {cfg['label']}, Altis analyzed <b>{total:,}</b> properties and resolved "
        f"<b>{remote:,}</b> ({pct_remote:.0f}%) remotely — no adjuster required. "
        f"Estimated savings: <b>${savings:,.0f}</b>."
    )
    st.markdown(f"""
<div style="padding:1.6rem 2rem 0 2rem">
  <p style="font-size:1.05rem;font-weight:500;color:#8B9AA3;line-height:1.65;max-width:820px">
    {headline}
  </p>
</div>
""", unsafe_allow_html=True)

    # ── MAP
    st.markdown('<div style="padding:1.4rem 2rem 0 2rem">', unsafe_allow_html=True)
    st.markdown("""
<p style="font-size:0.68rem;font-weight:600;letter-spacing:0.12em;color:#3A5060;
          text-transform:uppercase;margin-bottom:0.75rem">
  PROPERTY TRIAGE MAP — SENTINEL-1 SAR DERIVED
</p>
""", unsafe_allow_html=True)
    fig = _build_map(df, event_id)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown("</div>", unsafe_allow_html=True)

    # ── CTA
    st.markdown('<div style="padding:1.6rem 2rem 2.5rem 2rem;display:flex;justify-content:flex-start">', unsafe_allow_html=True)
    st.markdown('<div class="cta-wrap">', unsafe_allow_html=True)
    if st.button("View Property Triage  →", key="cta_triage"):
        st.session_state.screen = "triage"
        st.session_state.page   = 0
        st.session_state.filter = "All"
        st.session_state.selected_id = None
        st.rerun()
    st.markdown("</div></div>", unsafe_allow_html=True)


# ── SCREEN 2: PROPERTY TRIAGE TABLE ───────────────────────────────────────────
def screen_triage():
    event_id = st.session_state.event
    df       = load_data(event_id)
    cfg      = EVENTS[event_id]

    active_filter = st.session_state.get("filter", "All")
    search        = st.session_state.get("search_query", "")
    page          = st.session_state.get("page", 0)

    # apply filter
    fdf = df.copy()
    if active_filter != "All":
        fdf = fdf[fdf["impact_class"] == active_filter]
    if search:
        fdf = fdf[fdf["address"].str.contains(search, case=False, na=False)]
    fdf = fdf.reset_index(drop=True)

    total_pages = max(1, (len(fdf) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    page        = min(page, total_pages - 1)

    # ── HEADER
    h1, h2, h3 = st.columns([0.5, 3, 2])
    with h1:
        st.markdown('<div style="padding:1.2rem 0 0 2rem" class="back-btn">', unsafe_allow_html=True)
        if st.button("← Back", key="back_btn"):
            st.session_state.screen = "summary"
            st.session_state.selected_id = None
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with h2:
        st.markdown(f"""
<div style="padding:1.1rem 0 0 0.5rem;display:flex;align-items:center;gap:0.6rem">
  <svg width="18" height="18" viewBox="0 0 22 22" fill="none">
    <rect x="7" y="6" width="8" height="10" rx="1.5" fill="#A8D4E6"/>
    <rect x="0" y="8" width="6" height="6" rx="0.5" fill="#A8D4E6" opacity="0.6"/>
    <rect x="16" y="8" width="6" height="6" rx="0.5" fill="#A8D4E6" opacity="0.6"/>
  </svg>
  <span style="font-size:0.95rem;font-weight:800;color:#FFF;letter-spacing:0.1em">ALTIS</span>
  <span style="color:#222;margin:0 0.3rem">|</span>
  <span style="font-size:0.9rem;font-weight:500;color:#4A5568">{cfg['label']}</span>
</div>
""", unsafe_allow_html=True)

    with h3:
        st.markdown('<div style="padding:1rem 2rem 0 0;display:flex;justify-content:flex-end">', unsafe_allow_html=True)
        ev = st.segmented_control(
            "triage_event", ["harvey", "ian"],
            format_func=lambda x: "Harvey 2017" if x == "harvey" else "Ian 2022",
            default=event_id, label_visibility="collapsed",
            key="triage_event_toggle",
        )
        if ev and ev != st.session_state.event:
            st.session_state.event       = ev
            st.session_state.page        = 0
            st.session_state.selected_id = None
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div style="border-top:1px solid #0E0E0E;margin:0 2rem 0 2rem"></div>', unsafe_allow_html=True)

    # ── FILTER + SEARCH + EXPORT
    st.markdown('<div style="padding:1rem 2rem 0 2rem">', unsafe_allow_html=True)
    fc1, fc2, fc3 = st.columns([3, 2.5, 0.8])

    with fc1:
        new_filter = st.segmented_control(
            "Category Filter",
            options=["All", "Dispatch", "Remote-Approve", "Remote-Deny", "Review"],
            default=active_filter,
            label_visibility="collapsed",
            key="cat_filter",
        )
        if new_filter and new_filter != active_filter:
            st.session_state.filter = new_filter
            st.session_state.page   = 0
            st.session_state.selected_id = None
            st.rerun()

    with fc2:
        new_search = st.text_input(
            "search", value=search,
            placeholder="Search address...",
            label_visibility="collapsed",
            key="search_box",
        )
        if new_search != search:
            st.session_state.search_query = new_search
            st.session_state.page = 0
            st.rerun()

    with fc3:
        csv_bytes = fdf[[
            "property_id", "address", "pct_flooded", "max_depth_ft",
            "impact_class", "confidence_score", "recommended_action", "adjuster_note"
        ]].to_csv(index=False).encode()
        st.download_button(
            label="Export CSV",
            data=csv_bytes,
            file_name=f"altis_{event_id}_triage.csv",
            mime="text/csv",
            key="export_csv",
        )

    st.markdown("</div>", unsafe_allow_html=True)

    # ── RESULTS COUNT
    st.markdown(f"""
<div style="padding:0.7rem 2rem 0 2rem">
  <p style="font-size:0.72rem;color:#333;letter-spacing:0.06em">
    {len(fdf):,} PROPERTIES
    {'— filtered to ' + active_filter if active_filter != 'All' else ''}
    {' matching "' + search + '"' if search else ''}
  </p>
</div>
""", unsafe_allow_html=True)

    # ── TABLE HEADER
    st.markdown("""
<div style="
    padding:0.55rem 2rem;
    display:grid;
    grid-template-columns:3fr 1.4fr 0.9fr 1.6fr 2fr 0.5fr;
    gap:0;
    border-top:1px solid #0E0E0E;
    border-bottom:1px solid #0E0E0E;
    margin-top:0.6rem;
">
  <p style="font-size:0.66rem;font-weight:700;letter-spacing:0.1em;color:#2A3A42;text-transform:uppercase;margin:0">ADDRESS</p>
  <p style="font-size:0.66rem;font-weight:700;letter-spacing:0.1em;color:#2A3A42;text-transform:uppercase;margin:0">TIER</p>
  <p style="font-size:0.66rem;font-weight:700;letter-spacing:0.1em;color:#2A3A42;text-transform:uppercase;margin:0">DEPTH</p>
  <p style="font-size:0.66rem;font-weight:700;letter-spacing:0.1em;color:#2A3A42;text-transform:uppercase;margin:0">CONFIDENCE</p>
  <p style="font-size:0.66rem;font-weight:700;letter-spacing:0.1em;color:#2A3A42;text-transform:uppercase;margin:0">NOTE</p>
  <p style="margin:0"></p>
</div>
""", unsafe_allow_html=True)

    # ── TABLE ROWS
    if fdf.empty:
        st.markdown("""
<div style="padding:3rem 2rem;text-align:center">
  <p style="color:#333;font-size:0.9rem">No properties match the current filter.</p>
</div>
""", unsafe_allow_html=True)
    else:
        page_df = fdf.iloc[page * ROWS_PER_PAGE : (page + 1) * ROWS_PER_PAGE]

        for _, row in page_df.iterrows():
            color   = C[row["impact_class"]]
            bg      = C_BG[row["impact_class"]]
            note_preview = (row["adjuster_note"][:72] + "...") if len(row["adjuster_note"]) > 72 else row["adjuster_note"]
            pct     = int(row["confidence_score"])

            rc_main, rc_btn = st.columns([20, 1], gap="small")

            with rc_main:
                st.markdown(f"""
<div style="
    padding:0.7rem 2rem;
    display:grid;
    grid-template-columns:3fr 1.4fr 0.9fr 1.6fr 2fr;
    gap:0;
    border-bottom:1px solid #0A0A0A;
    align-items:center;
    transition:background 0.1s;
">
  <p style="font-size:0.82rem;color:#CCC;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:1rem">
    {row["address"]}
  </p>

  <div>
    <span style="
      display:inline-block;
      background:{bg};
      color:{color};
      border:1px solid {color}38;
      border-radius:3px;
      padding:0.18rem 0.55rem;
      font-size:0.7rem;
      font-weight:700;
      letter-spacing:0.04em;
      white-space:nowrap;
    ">{row["impact_class"]}</span>
  </div>

  <p style="font-size:0.84rem;color:#8B9AA3;margin:0;font-variant-numeric:tabular-nums">
    {row["max_depth_ft"]:.1f} ft
  </p>

  <div style="display:flex;align-items:center;gap:0.5rem;padding-right:0.5rem">
    <div style="flex:1;height:3px;background:#111;border-radius:2px;overflow:hidden">
      <div style="width:{pct}%;height:100%;background:{color};border-radius:2px"></div>
    </div>
    <span style="font-size:0.76rem;color:#444;min-width:28px;font-variant-numeric:tabular-nums">{pct}%</span>
  </div>

  <p style="font-size:0.76rem;color:#3A4A52;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:0.5rem">
    {note_preview}
  </p>
</div>
""", unsafe_allow_html=True)

            with rc_btn:
                st.markdown('<div class="row-btn" style="padding-top:0.55rem">', unsafe_allow_html=True)
                if st.button("→", key=f"v_{row['property_id']}"):
                    if st.session_state.get("selected_id") == row["property_id"]:
                        st.session_state.selected_id = None
                    else:
                        st.session_state.selected_id = row["property_id"]
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

    # ── PAGINATION
    if total_pages > 1:
        st.markdown('<div style="padding:1rem 2rem;display:flex;align-items:center;gap:1rem">', unsafe_allow_html=True)
        pg1, pg2, pg3 = st.columns([1, 3, 1])
        with pg1:
            if page > 0:
                if st.button("← Prev", key="prev_page"):
                    st.session_state.page = page - 1
                    st.rerun()
        with pg2:
            start = page * ROWS_PER_PAGE + 1
            end   = min((page + 1) * ROWS_PER_PAGE, len(fdf))
            st.markdown(f"""
<p style="text-align:center;font-size:0.75rem;color:#333;letter-spacing:0.06em;margin:0;padding-top:0.4rem">
  {start}–{end} of {len(fdf):,} &nbsp;|&nbsp; Page {page+1} of {total_pages}
</p>
""", unsafe_allow_html=True)
        with pg3:
            if page < total_pages - 1:
                if st.button("Next →", key="next_page"):
                    st.session_state.page = page + 1
                    st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ── DETAIL PANEL
    sel_id = st.session_state.get("selected_id")
    if sel_id:
        sel_rows = df[df["property_id"] == sel_id]
        if not sel_rows.empty:
            render_detail(sel_rows.iloc[0])


# ── DETAIL PANEL ───────────────────────────────────────────────────────────────
def render_detail(row):
    color  = C[row["impact_class"]]
    pct    = int(row["confidence_score"])

    st.markdown("""
<div style="border-top:1px solid #111;margin:1rem 2rem 0 2rem"></div>
<div style="padding:0 2rem">
""", unsafe_allow_html=True)

    top_l, top_r = st.columns([8, 1])
    with top_l:
        st.markdown(f"""
<div style="padding-top:1.4rem;padding-bottom:0.5rem">
  <p style="font-size:0.66rem;font-weight:700;letter-spacing:0.12em;color:#A8D4E6;
            text-transform:uppercase;margin-bottom:0.5rem">PROPERTY DETAIL</p>
  <h2 style="font-size:1.3rem;font-weight:700;color:#FFF;margin:0;letter-spacing:-0.01em">
    {row["address"]}
  </h2>
  <p style="font-size:0.72rem;color:#333;margin-top:0.25rem;font-variant-numeric:tabular-nums">
    {row["property_id"]}
  </p>
</div>
""", unsafe_allow_html=True)
    with top_r:
        st.markdown('<div class="close-btn" style="padding-top:1.4rem;display:flex;justify-content:flex-end">', unsafe_allow_html=True)
        if st.button("✕", key="close_detail"):
            st.session_state.selected_id = None
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    d1, d2, d3, d4 = st.columns(4)
    def stat_card(label, val, col_color="#FFF"):
        return f"""
<div style="background:#060606;border:1px solid #111;border-radius:5px;padding:1rem 1.2rem">
  <p style="font-size:0.64rem;letter-spacing:0.1em;color:#2A3A42;text-transform:uppercase;margin-bottom:0.4rem">{label}</p>
  <p style="font-size:1.5rem;font-weight:700;color:{col_color};margin:0;letter-spacing:-0.02em;line-height:1">{val}</p>
</div>"""

    with d1:
        st.markdown(stat_card("IMPACT CLASS", row["impact_class"], color), unsafe_allow_html=True)
    with d2:
        st.markdown(stat_card("MAX DEPTH", f"{row['max_depth_ft']:.1f} ft"), unsafe_allow_html=True)
    with d3:
        st.markdown(stat_card("FLOOD COVERAGE", f"{row['pct_flooded']:.0f}%"), unsafe_allow_html=True)
    with d4:
        st.markdown(stat_card("CONFIDENCE", f"{pct}%", color), unsafe_allow_html=True)

    # adjuster note
    st.markdown(f"""
<div style="margin-top:1rem;background:#060606;border:1px solid #111;border-left:3px solid {color};
            border-radius:0 5px 5px 0;padding:1rem 1.4rem">
  <p style="font-size:0.64rem;letter-spacing:0.1em;color:#2A3A42;text-transform:uppercase;margin-bottom:0.5rem">
    ADJUSTER NOTE
  </p>
  <p style="font-size:0.9rem;color:#AAA;line-height:1.6;margin:0;font-style:italic">
    "{row['adjuster_note']}"
  </p>
</div>
""", unsafe_allow_html=True)

    # SAR images
    st.markdown("""
<p style="font-size:0.64rem;letter-spacing:0.1em;color:#2A3A42;text-transform:uppercase;
          margin-top:1.4rem;margin-bottom:0.6rem">
  SENTINEL-1 SAR IMAGERY
</p>
""", unsafe_allow_html=True)

    img_pre, img_post = _sar_images(row["property_id"], float(row["max_depth_ft"]))
    ic1, ic2, ic3 = st.columns([1, 1, 1])

    with ic1:
        st.markdown('<p style="font-size:0.7rem;color:#2A3A42;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:0.3rem">PRE-EVENT</p>', unsafe_allow_html=True)
        st.image(img_pre, use_container_width=True)

    with ic2:
        st.markdown('<p style="font-size:0.7rem;color:#2A3A42;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:0.3rem">POST-EVENT</p>', unsafe_allow_html=True)
        st.image(img_post, use_container_width=True)

    with ic3:
        st.markdown(f"""
<div style="background:#060606;border:1px solid #111;border-radius:5px;padding:1.2rem;height:100%">
  <p style="font-size:0.64rem;letter-spacing:0.1em;color:#2A3A42;text-transform:uppercase;margin-bottom:0.8rem">
    RAW MEASUREMENTS
  </p>
  <table style="width:100%;border-collapse:collapse">
    <tr>
      <td style="font-size:0.76rem;color:#3A5060;padding:0.3rem 0">Max Depth</td>
      <td style="font-size:0.76rem;color:#CCC;text-align:right;font-variant-numeric:tabular-nums">{row["max_depth_ft"]:.2f} ft</td>
    </tr>
    <tr>
      <td style="font-size:0.76rem;color:#3A5060;padding:0.3rem 0;border-top:1px solid #0E0E0E">Coverage</td>
      <td style="font-size:0.76rem;color:#CCC;text-align:right;font-variant-numeric:tabular-nums;border-top:1px solid #0E0E0E">{row["pct_flooded"]:.1f}%</td>
    </tr>
    <tr>
      <td style="font-size:0.76rem;color:#3A5060;padding:0.3rem 0;border-top:1px solid #0E0E0E">Confidence</td>
      <td style="font-size:0.76rem;color:#CCC;text-align:right;font-variant-numeric:tabular-nums;border-top:1px solid #0E0E0E">{pct}%</td>
    </tr>
    <tr>
      <td style="font-size:0.76rem;color:#3A5060;padding:0.3rem 0;border-top:1px solid #0E0E0E">Data Source</td>
      <td style="font-size:0.76rem;color:#CCC;text-align:right;border-top:1px solid #0E0E0E">Sentinel-1</td>
    </tr>
    <tr>
      <td style="font-size:0.76rem;color:#3A5060;padding:0.3rem 0;border-top:1px solid #0E0E0E">Resolution</td>
      <td style="font-size:0.76rem;color:#CCC;text-align:right;border-top:1px solid #0E0E0E">10 m</td>
    </tr>
  </table>
</div>
""", unsafe_allow_html=True)

    st.markdown('<div style="padding-bottom:2.5rem"></div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    inject_css()

    defaults = {
        "screen":      "summary",
        "event":       "harvey",
        "page":        0,
        "filter":      "All",
        "search_query": "",
        "selected_id": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    if st.session_state.screen == "summary":
        screen_summary()
    else:
        screen_triage()


if __name__ == "__main__":
    main()
