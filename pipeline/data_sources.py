"""
Registry of free data sources Altis uses or can use. Single source of truth
for docs/DATA_SOURCES.md and validation/source_probe.py (which checks each
one is actually reachable from a deployment).

status:   "in_use"   wired into the product or a validation script
          "ready"    verified reachable, not yet wired in
          "blocked"  needs an account, credential or outreach first
licence:  short form; always confirm the provider's current terms before
          commercial use.
"""

SOURCES = [
    # ── Satellite imagery ────────────────────────────────────────────────
    {"name": "Sentinel-1 GRD (radar)", "provider": "ESA Copernicus via Google Earth Engine",
     "use": "Flood extent through cloud, day and night", "access": "ee:COPERNICUS/S1_GRD",
     "licence": "Copernicus open (commercial use allowed)", "status": "in_use"},
    {"name": "Sentinel-2 L2A (optical)", "provider": "ESA Copernicus via Earth Engine",
     "use": "Optical water check, vegetation loss", "access": "ee:COPERNICUS/S2_SR_HARMONIZED",
     "licence": "Copernicus open", "status": "in_use"},
    {"name": "Copernicus Data Space STAC catalogue", "provider": "ESA",
     "use": "Last and next satellite pass for any area (pass tracker)",
     "access": "https://catalogue.dataspace.copernicus.eu/stac/collections",
     "licence": "Copernicus open; search needs no account", "status": "in_use"},
    {"name": "NASA OPERA DSWx-S1 (water extent product)", "provider": "NASA JPL / ASF",
     "use": "Independent second opinion on flood extent",
     "access": "https://cmr.earthdata.nasa.gov/search/collections.json?short_name=OPERA_L3_DSWX-S1_V1",
     "licence": "NASA open data", "status": "blocked",
     "note": "Search is open; downloads need a free NASA Earthdata login"},
    {"name": "ASF Sentinel-1 archive + HyP3 on-demand processing", "provider": "Alaska Satellite Facility",
     "use": "Open-source path off Earth Engine (terrain-corrected radar)",
     "access": "https://api.daac.asf.alaska.edu/services/search/param?platform=S1&maxResults=1&output=json",
     "licence": "Open", "status": "ready", "note": "HyP3 jobs need a free Earthdata login"},
    {"name": "Microsoft Planetary Computer STAC", "provider": "Microsoft",
     "use": "Sentinel-1 RTC, Sentinel-2, DEMs without Earth Engine",
     "access": "https://planetarycomputer.microsoft.com/api/stac/v1/collections",
     "licence": "Per-dataset (mostly open)", "status": "ready"},
    {"name": "Element 84 Earth Search (AWS open data)", "provider": "Element 84 / AWS",
     "use": "Sentinel-2 COGs without Earth Engine", "access": "https://earth-search.aws.element84.com/v1/collections",
     "licence": "Copernicus open", "status": "ready"},

    # ── Terrain, hydrology, surface water ────────────────────────────────
    {"name": "USGS 3DEP 1 m lidar DEM", "provider": "USGS via Earth Engine",
     "use": "Ground elevation for depth (US)", "access": "ee:USGS/3DEP/1m",
     "licence": "US public domain", "status": "in_use"},
    {"name": "USGS 3DEP 10 m DEM", "provider": "USGS via Earth Engine", "use": "US fallback DEM",
     "access": "ee:USGS/3DEP/10m", "licence": "US public domain", "status": "ready"},
    {"name": "Copernicus GLO-30 DEM", "provider": "ESA via Earth Engine", "use": "Global DEM",
     "access": "ee:COPERNICUS/DEM/GLO30", "licence": "Copernicus DEM licence (commercial allowed)", "status": "in_use"},
    {"name": "FABDEM (forest and buildings removed)", "provider": "Univ. of Bristol via awesome-gee-community",
     "use": "Better bare-earth DEM outside the US", "access": "ee:projects/sat-io/open-datasets/FABDEM",
     "licence": "CC BY-NC-SA 4.0 (NON-commercial)", "status": "ready",
     "note": "Research/validation only unless a commercial licence is bought"},
    {"name": "MERIT Hydro (HAND, flow direction)", "provider": "Univ. of Tokyo via Earth Engine",
     "use": "Height above nearest drainage", "access": "ee:MERIT/Hydro/v1_0_1",
     "licence": "CC BY-NC 4.0 / ODbL dual (check)", "status": "in_use"},
    {"name": "JRC Global Surface Water", "provider": "EC JRC via Earth Engine",
     "use": "Permanent water mask", "access": "ee:JRC/GSW1_4/GlobalSurfaceWater",
     "licence": "Open (Copernicus)", "status": "in_use"},
    {"name": "USGS NLDI river network", "provider": "USGS",
     "use": "Real main-stem channel geometry for gauge interpolation",
     "access": "https://api.water.usgs.gov/nldi/linked-data/nwissite/USGS-08114000",
     "licence": "US public domain", "status": "in_use"},
    {"name": "HydroSHEDS free-flowing rivers", "provider": "WWF via Earth Engine",
     "use": "Global river lines (non-US gauge interpolation)",
     "access": "ee:WWF/HydroSHEDS/v1/FreeFlowingRivers", "licence": "Open with attribution", "status": "ready"},

    # ── Gauges, rainfall, forecasts ──────────────────────────────────────
    {"name": "NOAA National Water Prediction Service (NWPS)", "provider": "NOAA NWS",
     "use": "Gauge stage, flood categories, historic crests, datums, forecasts",
     "access": "https://api.water.noaa.gov/nwps/v1/gauges/RMOT2", "licence": "US public domain", "status": "in_use"},
    {"name": "USGS NWIS water data", "provider": "USGS",
     "use": "Instantaneous stage, gauge datums", "access": "https://waterservices.usgs.gov/nwis/site/?format=rdb&sites=08114000",
     "licence": "US public domain", "status": "in_use"},
    {"name": "NOAA National Water Model (AWS)", "provider": "NOAA OWP",
     "use": "Streamflow on every US river reach, forecasts and retrospective",
     "access": "https://noaa-nwm-pds.s3.amazonaws.com/", "licence": "US public domain", "status": "ready"},
    {"name": "NOAA MRMS radar rainfall", "provider": "NOAA NSSL",
     "use": "Hourly 1 km rainfall (US) for nowcasts", "access": "https://mrms.ncep.noaa.gov/data/2D/",
     "licence": "US public domain", "status": "ready"},
    {"name": "GPM IMERG rainfall", "provider": "NASA via Earth Engine", "use": "Global rainfall",
     "access": "ee:NASA/GPM_L3/IMERG_V07", "licence": "NASA open", "status": "ready"},
    {"name": "CHIRPS daily rainfall", "provider": "UCSB CHG via Earth Engine", "use": "Rainfall (in pipeline)",
     "access": "ee:UCSB-CHG/CHIRPS/DAILY", "licence": "CC BY 4.0", "status": "in_use"},
    {"name": "ERA5-Land daily", "provider": "ECMWF via Earth Engine", "use": "Soil moisture, antecedent wetness",
     "access": "ee:ECMWF/ERA5_LAND/DAILY_AGGR", "licence": "Copernicus open", "status": "ready"},
    {"name": "NOAA National Hurricane Center", "provider": "NOAA NHC",
     "use": "Active storms, forecast cones, surge-flooding GIS, archive cones",
     "access": "https://www.nhc.noaa.gov/CurrentStorms.json", "licence": "US public domain", "status": "in_use"},
    {"name": "Australian Bureau of Meteorology Water Data (KiWIS)", "provider": "BoM",
     "use": "River gauges for Australian events (gauge depth outside the US)",
     "access": "http://www.bom.gov.au/waterdata/services?service=kisters&type=queryServices&request=getrequestinfo&datasource=0&format=json",
     "licence": "CC BY 4.0", "status": "ready"},

    # ── Buildings and exposure ───────────────────────────────────────────
    {"name": "USACE National Structure Inventory", "provider": "USACE", "use": "Structures, first-floor height",
     "access": "https://nsi.sec.usace.army.mil/nsiapi/structures?bbox=-95.8,29.6,-95.79,29.61",
     "licence": "Public (registration for bulk)", "status": "in_use"},
    {"name": "Microsoft US building footprints", "provider": "Microsoft via awesome-gee-community",
     "use": "Building outlines for address matching", "access": "ee:projects/sat-io/open-datasets/MSBuildings/US",
     "licence": "ODbL", "status": "ready"},
    {"name": "Google Open Buildings v3", "provider": "Google via Earth Engine",
     "use": "Building outlines (Africa, Asia, Latin America)", "access": "ee:GOOGLE/Research/open-buildings/v3/polygons",
     "licence": "CC BY 4.0 / ODbL", "status": "ready"},
    {"name": "Overture Maps (buildings, places)", "provider": "Overture Maps Foundation",
     "use": "Global buildings with heights", "access": "https://overturemaps-us-west-2.s3.amazonaws.com/",
     "licence": "ODbL / CDLA", "status": "ready"},
    {"name": "ESA WorldCover 10 m", "provider": "ESA via Earth Engine", "use": "Land cover (urban masking)",
     "access": "ee:ESA/WorldCover/v200", "licence": "CC BY 4.0", "status": "ready"},
    {"name": "GHSL built-up surface", "provider": "EC JRC via Earth Engine", "use": "Urban density",
     "access": "ee:JRC/GHSL/P2023A/GHS_BUILT_S", "licence": "CC BY 4.0", "status": "ready"},
    {"name": "FEMA National Flood Hazard Layer", "provider": "FEMA", "use": "Flood zones on the map",
     "access": "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer?f=json",
     "licence": "US public domain", "status": "in_use"},

    # ── Ground truth and history ─────────────────────────────────────────
    {"name": "OpenFEMA NFIP claims v3", "provider": "FEMA",
     "use": "Insured outcomes per event (calibration, analogs)",
     "access": "https://www.fema.gov/api/open/v3/NfipClaims?$top=1", "licence": "US public domain", "status": "in_use"},
    {"name": "OpenFEMA disaster declarations", "provider": "FEMA", "use": "Event list, counties, live watch",
     "access": "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries?$top=1",
     "licence": "US public domain", "status": "in_use"},
    {"name": "USGS STN high-water marks", "provider": "USGS",
     "use": "Surveyed flood depth (point ground truth)",
     "access": "https://stn.wim.usgs.gov/STNServices/Events.json", "licence": "US public domain", "status": "in_use"},
    {"name": "Global Flood Database", "provider": "Cloud to Street / Tellman et al. via Earth Engine",
     "use": "913 mapped historical floods (event library)", "access": "ee:GLOBAL_FLOOD_DB/MODIS_EVENTS/V1",
     "licence": "CC BY-NC 4.0 (NON-commercial)", "status": "in_use",
     "note": "Fine for research and analog evidence; check terms before selling outputs derived from it"},
    {"name": "Sen1Floods11 hand labels", "provider": "Cloud to Street (Bonafilia et al. 2020)",
     "use": "Extent benchmark and training labels", "access": "https://storage.googleapis.com/sen1floods11/v1.1/Sen1Floods11_Metadata.geojson",
     "licence": "CC BY 4.0", "status": "in_use"},
    {"name": "UrbanSARFloods benchmark", "provider": "Zenodo", "use": "Urban flood labels",
     "access": "https://zenodo.org/api/records?q=UrbanSARFloods", "licence": "Check record", "status": "ready"},
    {"name": "Copernicus EMS rapid mapping", "provider": "EC Copernicus EMS",
     "use": "Analyst-mapped flood extents for activations worldwide",
     "access": "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/?limit=1",
     "licence": "Copernicus open", "status": "ready"},
    {"name": "NOAA Emergency Response Imagery", "provider": "NOAA NGS",
     "use": "Post-hurricane aerial photos (truth for coastal US storms)",
     "access": "https://storms.ngs.noaa.gov/", "licence": "US public domain", "status": "ready"},
    {"name": "Dartmouth Flood Observatory archive", "provider": "Univ. of Colorado",
     "use": "Global flood event list 1985-present", "access": "https://floodobservatory.colorado.edu/",
     "licence": "Check terms", "status": "ready"},
]


def to_markdown(probe: dict | None = None) -> str:
    reach = {r["name"]: r for r in (probe or {}).get("results", [])}
    lines = [
        "# Free data sources",
        "",
        "Generated from `pipeline/data_sources.py` (the single source of truth) by",
        "`python pipeline/data_sources.py --md`. Reachability is from",
        "`validation/source_probe.py`" + (f", last run {probe['checked']}" if probe else "") + ".",
        "",
        "**Status:** `in_use` = wired into the product or a validation script; `ready` = verified",
        "reachable, not wired in yet; `blocked` = needs a free account or outreach first.",
        "",
        "**Licences are summaries.** Confirm each provider's current terms before commercial use.",
        "Sources marked NON-commercial (FABDEM, Global Flood Database, possibly MERIT Hydro) are fine",
        "for research and validation but need a licence check before outputs derived from them are sold.",
        "",
        "| Source | Used for | Status | Licence | Reachable | Access |",
        "|---|---|---|---|---|---|",
    ]
    for s in SOURCES:
        r = reach.get(s["name"])
        ok = "" if r is None else ("yes" if r["reachable"] else ("not checked" if r["reachable"] is None else "NO"))
        note = f" ({s['note']})" if s.get("note") else ""
        acc = s["access"]
        acc = f"`{acc[3:]}` (Earth Engine)" if acc.startswith("ee:") else acc
        lines.append(f"| **{s['name']}** ({s['provider']}) | {s['use']}{note} | {s['status']} | "
                     f"{s['licence']} | {ok} | {acc} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    if "--md" in sys.argv:
        pp = root / "outputs" / "source_probe.json"
        probe = json.loads(pp.read_text()) if pp.exists() else None
        (root / "docs" / "DATA_SOURCES.md").write_text(to_markdown(probe))
        print("wrote docs/DATA_SOURCES.md")
