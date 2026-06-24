# Archived Streamlit App (backup)

This is the **original Streamlit dashboard** that predates the current
React + FastAPI architecture. It is kept here purely as a **backup / reference**
and is **not** part of the live product.

- `app.py` — original single-file Streamlit dashboard
- `generate_data.py` — synthetic data generator used by the old dashboard

The live application is:

- **Backend:** `backend/` (FastAPI) — `uvicorn backend.main:app --reload --port 8000`
- **Frontend:** `frontend/` (React + Vite + Mapbox GL) — `npm run dev`
- **Pipeline:** `pipeline/` (GEE SAR flood detection + triage)

If you ever need the old dashboard, it can be run with:

```bash
pip install streamlit
streamlit run archive/streamlit_app/app.py
```

Nothing in the active codebase imports from this folder.
