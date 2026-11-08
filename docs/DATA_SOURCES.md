# Free data sources

Generated from `pipeline/data_sources.py` (the single source of truth) by
`python pipeline/data_sources.py --md`. Reachability is from
`validation/source_probe.py`, last run 2026-11-08T10:36:38Z.

**Status:** `in_use` = wired into the product or a validation script; `ready` = verified
reachable, not wired in yet; `blocked` = needs a free account or outreach first.

**Licences are summaries.** Confirm each provider's current terms before commercial use.
Sources marked NON-commercial (FABDEM, Global Flood Database, possibly MERIT Hydro) are fine
for research and validation but need a licence check before outputs derived from them are sold.

| Source | Used for | Status | Licence | Reachable | Access |
|---|---|---|---|---|---|
| **Sentinel-1 GRD (radar)** (ESA Copernicus via Google Earth Engine) | Flood extent through cloud, day and night | in_use | Copernicus open (commercial use allowed) | yes | `COPERNICUS/S1_GRD` (Earth Engine) |
| **Sentinel-2 L2A (optical)** (ESA Copernicus via Earth Engine) | Optical water check, vegetation loss | in_use | Copernicus open | yes | `COPERNICUS/S2_SR_HARMONIZED` (Earth Engine) |
| **Copernicus Data Space STAC catalogue** (ESA) | Last and next satellite pass for any area (pass tracker) | in_use | Copernicus open; search needs no account | yes | https://catalogue.dataspace.copernicus.eu/stac/collections |
| **NASA OPERA DSWx-S1 (water extent product)** (NASA JPL / ASF) | Independent second opinion on flood extent (Search is open; downloads need a free NASA Earthdata login) | blocked | NASA open data | yes | https://cmr.earthdata.nasa.gov/search/collections.json?short_name=OPERA_L3_DSWX-S1_V1 |
| **ASF Sentinel-1 archive + HyP3 on-demand processing** (Alaska Satellite Facility) | Open-source path off Earth Engine (terrain-corrected radar) (HyP3 jobs need a free Earthdata login) | ready | Open | yes | https://api.daac.asf.alaska.edu/services/search/param?platform=S1&maxResults=1&output=json |
| **Microsoft Planetary Computer STAC** (Microsoft) | Sentinel-1 RTC, Sentinel-2, DEMs without Earth Engine | ready | Per-dataset (mostly open) | yes | https://planetarycomputer.microsoft.com/api/stac/v1/collections |
| **Element 84 Earth Search (AWS open data)** (Element 84 / AWS) | Sentinel-2 COGs without Earth Engine | ready | Copernicus open | yes | https://earth-search.aws.element84.com/v1/collections |
| **USGS 3DEP 1 m lidar DEM** (USGS via Earth Engine) | Ground elevation for depth (US) | in_use | US public domain | yes | `USGS/3DEP/1m` (Earth Engine) |
| **USGS 3DEP 10 m DEM** (USGS via Earth Engine) | US fallback DEM | ready | US public domain | yes | `USGS/3DEP/10m` (Earth Engine) |
| **Copernicus GLO-30 DEM** (ESA via Earth Engine) | Global DEM | in_use | Copernicus DEM licence (commercial allowed) | yes | `COPERNICUS/DEM/GLO30` (Earth Engine) |
| **FABDEM (forest and buildings removed)** (Univ. of Bristol via awesome-gee-community) | Better bare-earth DEM outside the US (Research/validation only unless a commercial licence is bought) | ready | CC BY-NC-SA 4.0 (NON-commercial) | yes | `projects/sat-io/open-datasets/FABDEM` (Earth Engine) |
| **MERIT Hydro (HAND, flow direction)** (Univ. of Tokyo via Earth Engine) | Height above nearest drainage | in_use | CC BY-NC 4.0 / ODbL dual (check) | yes | `MERIT/Hydro/v1_0_1` (Earth Engine) |
| **JRC Global Surface Water** (EC JRC via Earth Engine) | Permanent water mask | in_use | Open (Copernicus) | yes | `JRC/GSW1_4/GlobalSurfaceWater` (Earth Engine) |
| **USGS NLDI river network** (USGS) | Real main-stem channel geometry for gauge interpolation | in_use | US public domain | yes | https://api.water.usgs.gov/nldi/linked-data/nwissite/USGS-08114000 |
| **HydroSHEDS free-flowing rivers** (WWF via Earth Engine) | Global river lines (non-US gauge interpolation) | ready | Open with attribution | yes | `WWF/HydroSHEDS/v1/FreeFlowingRivers` (Earth Engine) |
| **NOAA National Water Prediction Service (NWPS)** (NOAA NWS) | Gauge stage, flood categories, historic crests, datums, forecasts | in_use | US public domain | yes | https://api.water.noaa.gov/nwps/v1/gauges/RMOT2 |
| **USGS NWIS water data** (USGS) | Instantaneous stage, gauge datums | in_use | US public domain | yes | https://waterservices.usgs.gov/nwis/site/?format=rdb&sites=08114000 |
| **NOAA National Water Model (AWS)** (NOAA OWP) | Streamflow on every US river reach, forecasts and retrospective | ready | US public domain | yes | https://noaa-nwm-pds.s3.amazonaws.com/ |
| **NOAA MRMS radar rainfall** (NOAA NSSL) | Hourly 1 km rainfall (US) for nowcasts | ready | US public domain | yes | https://mrms.ncep.noaa.gov/data/2D/ |
| **GPM IMERG rainfall** (NASA via Earth Engine) | Global rainfall | ready | NASA open | yes | `NASA/GPM_L3/IMERG_V07` (Earth Engine) |
| **CHIRPS daily rainfall** (UCSB CHG via Earth Engine) | Rainfall (in pipeline) | in_use | CC BY 4.0 | yes | `UCSB-CHG/CHIRPS/DAILY` (Earth Engine) |
| **ERA5-Land daily** (ECMWF via Earth Engine) | Soil moisture, antecedent wetness | ready | Copernicus open | yes | `ECMWF/ERA5_LAND/DAILY_AGGR` (Earth Engine) |
| **NOAA National Hurricane Center** (NOAA NHC) | Active storms, forecast cones, surge-flooding GIS, archive cones | in_use | US public domain | yes | https://www.nhc.noaa.gov/CurrentStorms.json |
| **Australian Bureau of Meteorology Water Data (KiWIS)** (BoM) | River gauges for Australian events (gauge depth outside the US) | ready | CC BY 4.0 | yes | http://www.bom.gov.au/waterdata/services?service=kisters&type=queryServices&request=getrequestinfo&datasource=0&format=json |
| **USACE National Structure Inventory** (USACE) | Structures, first-floor height | in_use | Public (registration for bulk) | yes | https://nsi.sec.usace.army.mil/nsiapi/structures?bbox=-95.8,29.6,-95.79,29.61 |
| **Microsoft US building footprints** (Microsoft via awesome-gee-community) | Building outlines for address matching | ready | ODbL | yes | `projects/sat-io/open-datasets/MSBuildings/US` (Earth Engine) |
| **Google Open Buildings v3** (Google via Earth Engine) | Building outlines (Africa, Asia, Latin America) | ready | CC BY 4.0 / ODbL | yes | `GOOGLE/Research/open-buildings/v3/polygons` (Earth Engine) |
| **Overture Maps (buildings, places)** (Overture Maps Foundation) | Global buildings with heights | ready | ODbL / CDLA | yes | https://overturemaps-us-west-2.s3.amazonaws.com/ |
| **ESA WorldCover 10 m** (ESA via Earth Engine) | Land cover (urban masking) | ready | CC BY 4.0 | yes | `ESA/WorldCover/v200` (Earth Engine) |
| **GHSL built-up surface** (EC JRC via Earth Engine) | Urban density | ready | CC BY 4.0 | yes | `JRC/GHSL/P2023A/GHS_BUILT_S` (Earth Engine) |
| **FEMA National Flood Hazard Layer** (FEMA) | Flood zones on the map | in_use | US public domain | yes | https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer?f=json |
| **OpenFEMA NFIP claims v3** (FEMA) | Insured outcomes per event (calibration, analogs) | in_use | US public domain | yes | https://www.fema.gov/api/open/v3/NfipClaims?$top=1 |
| **OpenFEMA disaster declarations** (FEMA) | Event list, counties, live watch | in_use | US public domain | yes | https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries?$top=1 |
| **USGS STN high-water marks** (USGS) | Surveyed flood depth (point ground truth) | in_use | US public domain | yes | https://stn.wim.usgs.gov/STNServices/Events.json |
| **Global Flood Database** (Cloud to Street / Tellman et al. via Earth Engine) | 913 mapped historical floods (event library) (Fine for research and analog evidence; check terms before selling outputs derived from it) | in_use | CC BY-NC 4.0 (NON-commercial) | yes | `GLOBAL_FLOOD_DB/MODIS_EVENTS/V1` (Earth Engine) |
| **Sen1Floods11 hand labels** (Cloud to Street (Bonafilia et al. 2020)) | Extent benchmark and training labels | in_use | CC BY 4.0 | yes | https://storage.googleapis.com/sen1floods11/v1.1/Sen1Floods11_Metadata.geojson |
| **UrbanSARFloods benchmark** (Zenodo) | Urban flood labels | ready | Check record | yes | https://zenodo.org/api/records?q=UrbanSARFloods |
| **Copernicus EMS rapid mapping** (EC Copernicus EMS) | Analyst-mapped flood extents for activations worldwide | ready | Copernicus open | yes | https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/?limit=1 |
| **NOAA Emergency Response Imagery** (NOAA NGS) | Post-hurricane aerial photos (truth for coastal US storms) | ready | US public domain | yes | https://storms.ngs.noaa.gov/ |
| **Dartmouth Flood Observatory archive** (Univ. of Colorado) | Global flood event list 1985-present | ready | Check terms | yes | https://floodobservatory.colorado.edu/ |
