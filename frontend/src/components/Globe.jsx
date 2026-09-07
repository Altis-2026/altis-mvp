import { useEffect, useRef, useCallback, useState } from 'react';
import { useIsMobile } from '../hooks/useIsMobile.js';
import { useTheme } from '../hooks/useTheme.js';
import { getTheme } from '../theme.js';
import Tutorial from './Tutorial.jsx';
import { api } from '../services/api.js';
import { buildingFeatures, placeholderRing } from '../utils/buildings.js';

/* Globe atmosphere per theme. Dark keeps the deep-space starfield brand look;
   light turns the surrounding space into a soft daylight sky so the whole
   canvas reads bright, not a dark globe on a light chrome. */
const DARK_FOG = {
  'color':          'rgba(2, 4, 10, 0.9)',
  'high-color':     'rgba(2, 4, 10, 0.9)',
  'horizon-blend':  0.35,
  'space-color':    'rgba(0, 0, 4, 1)',
  'star-intensity': 0.88,
};
const LIGHT_FOG = {
  'color':          'rgba(214, 232, 246, 0.85)', // soft horizon haze
  'high-color':     'rgba(154, 197, 232, 1)',    // gentle daytime sky blue
  'horizon-blend':  0.15,
  'space-color':    'rgba(242, 246, 249, 1)',    // exactly the light --bg
  'star-intensity': 0,
};
const fogFor = (theme) => (theme === 'light' ? LIGHT_FOG : DARK_FOG);
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';

const TRIAGE_COLORS = {
  'Dispatch':       '#FF4444',
  'Remote-Approve': '#4CAF82',
  'Remote-Deny':    '#6B8FA3',
  'Review':         '#FFB347',
  'Portfolio':      '#E8D5A3',
};

/* FEMA National Flood Hazard Layer, served straight from FEMA's public ArcGIS
   endpoint as dynamic raster tiles (layer 28 = flood hazard zones). US-only by
   nature of the dataset. */
const FEMA_NFHL_TILES =
  'https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/export' +
  '?dpi=96&transparent=true&format=png32&layers=show:28' +
  '&bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=256,256&f=image';

const CAT_COLORS = {
  '5': '#FF2D2D', '4': '#FF5A36', '3': '#FF8C42',
  '2': '#FFB347', '1': '#FFD97A', 'TS': '#A8D4E6', 'TD': '#6B8FA3',
};

/* ── 3D property inspect ─────────────────────────────────────────────────────
   An additional mode, not a replacement: the flat pin/cluster view stays the
   default at portfolio scale, where thousands of pins render fine and extruded
   geometry would not. Buildings appear only once the camera is inside a
   neighbourhood, and only for the properties actually in the viewport, which
   is the same principle the existing clustering and the address-label reveal
   (minzoom 12.5) already follow. */
const INSPECT_MIN_ZOOM = 14.5;   // below this, buildings are not drawn at all
const INSPECT_FLY_ZOOM = 18.6;   // where the toggle takes you from altitude
const INSPECT_PITCH = 62;        // enough to see water up a wall
const MAX_BUILDINGS = 220;       // per viewport — the cap that keeps this fast
const TERRAIN_EXAGGERATION = 1.4;

export default function Globe({
  properties = [],
  portfolioProperties = [],
  selectedProperty,
  onPropertySelect,
  tileUrl,
  timeMode,
  flyTarget,
  dimmed,
  leftInset = 0,
  stormTrack = null,
  zoneBbox = null,
}) {
  const containerRef = useRef(null);
  const mapRef       = useRef(null);
  const rafRef       = useRef(null);
  const isRotating   = useRef(true);
  const pinsReady    = useRef(false);
  const [showFema,  setShowFema]  = useState(false);
  const [showTrack, setShowTrack] = useState(true);
  const [showHeat,  setShowHeat]  = useState(false);
  const [zoomLevel, setZoomLevel] = useState(1.5);
  const [inspect3D, setInspect3D] = useState(false);
  const [buildingStatus, setBuildingStatus] = useState(null); // {loading,count,…}
  /* Latest properties, readable from map event handlers without re-binding
     them on every data change. */
  const allPropsRef  = useRef([]);
  const inspectRef   = useRef(false);
  const buildingReq  = useRef(0);      // drops responses from stale viewports
  const buildingTimer = useRef(null);
  const [showTutorial, setShowTutorial] = useState(false);
  const [guideVisible, setGuideVisible] = useState(false); // faded-in yet?
  const [guideGone,    setGuideGone]    = useState(false); // fully dismissed
  const isMobile = useIsMobile();
  const theme = useTheme();

  /* Restyle the globe atmosphere the instant the theme toggles. */
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => map.setFog(fogFor(theme));
    if (map.isStyleLoaded()) apply();
    else map.once('style.load', apply);
  }, [theme]);

  /* First-run guide: fade in on arrival, linger, then fade itself out so it
     never lingers over the map. Only relevant before anything is loaded. */
  useEffect(() => {
    const anyPins = properties.length > 0 || portfolioProperties.length > 0;
    if (anyPins) return;
    const inT   = setTimeout(() => setGuideVisible(true), 60);
    const fadeT = setTimeout(() => setGuideVisible(false), 7000);
    const goneT = setTimeout(() => setGuideGone(true), 7800);
    return () => { clearTimeout(inT); clearTimeout(fadeT); clearTimeout(goneT); };
  }, [properties.length, portfolioProperties.length]);

  /* ── Initialize map ─────────────────────────────────────────── */
  useEffect(() => {
    if (mapRef.current || !containerRef.current) return;

    const token = import.meta.env.VITE_MAPBOX_TOKEN;
    if (!token || token.startsWith('pk.your')) {
      console.error('⚠ Set VITE_MAPBOX_TOKEN in frontend/.env');
      return;
    }

    mapboxgl.accessToken = token;

    const map = new mapboxgl.Map({
      container:   containerRef.current,
      style:       'mapbox://styles/mapbox/satellite-streets-v12',
      projection:  'globe',
      zoom:        1.5,
      center:      [0, 20],
      attributionControl: false,
      logoPosition:       'bottom-right',
    });

    mapRef.current = map;

    map.on('style.load', () => {
      /* Atmosphere matches the current theme (dark starfield / light sky). */
      map.setFog(fogFor(getTheme()));

      /* ── Event properties source (clustered) */
      map.addSource('properties', {
        type:          'geojson',
        data:          emptyFC(),
        cluster:       true,
        clusterMaxZoom: 11,
        clusterRadius:  48,
      });

      /* ── Exposure heat layer — dollar-weighted concentration view for the
         exec/underwriting audience. Unclustered twin source (heatmaps can't
         read a clustered one); added first so every pin layer stacks above
         it — pins always stay visible. Off until toggled. */
      map.addSource('exposure-heat', { type: 'geojson', data: emptyFC() });
      map.addLayer({
        id:     'exposure-heatmap',
        type:   'heatmap',
        source: 'exposure-heat',
        layout: { visibility: 'none' },
        maxzoom: 15,
        paint: {
          'heatmap-weight':    ['get', 'heat_w'],
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 4, 0.7, 10, 1.4],
          'heatmap-radius':    ['interpolate', ['linear'], ['zoom'], 4, 18, 9, 42, 13, 64],
          'heatmap-opacity':   ['interpolate', ['linear'], ['zoom'], 4, 0.62, 13, 0.45, 15, 0],
          'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
            0,    'rgba(0,0,0,0)',
            0.15, 'rgba(107,143,163,0.35)',
            0.4,  'rgba(168,212,230,0.55)',
            0.65, 'rgba(212,176,104,0.75)',
            0.85, 'rgba(255,120,60,0.85)',
            1,    'rgba(255,68,68,0.95)'],
        }
      });

      /* Cluster halos */
      map.addLayer({
        id:     'cluster-halo',
        type:   'circle',
        source: 'properties',
        filter: ['has', 'point_count'],
        paint: {
          'circle-color':        'rgba(168, 212, 230, 0)',
          'circle-radius':       ['step', ['get', 'point_count'], 28, 10, 36, 50, 44],
          'circle-blur':         0.4,
          'circle-stroke-width': 0,
        }
      });

      /* Cluster circles */
      map.addLayer({
        id:     'clusters',
        type:   'circle',
        source: 'properties',
        filter: ['has', 'point_count'],
        paint: {
          'circle-color':        '#A8D4E6',
          'circle-radius':       ['step', ['get', 'point_count'], 20, 10, 26, 50, 32],
          'circle-opacity':      0.88,
          'circle-stroke-width': 1.5,
          'circle-stroke-color': 'rgba(255,255,255,0.25)',
        }
      });

      /* Cluster count labels */
      map.addLayer({
        id:     'cluster-count',
        type:   'symbol',
        source: 'properties',
        filter: ['has', 'point_count'],
        layout: {
          'text-field': ['get', 'point_count_abbreviated'],
          'text-font':  ['DIN Offc Pro Medium', 'Arial Unicode MS Bold'],
          'text-size':  12,
        },
        paint: { 'text-color': '#000010' }
      });

      /* Dispatch emphasis glow — sits beneath the pins so high-severity
         dispatch properties read as urgent at a glance, scaling with zoom. */
      map.addLayer({
        id:     'dispatch-glow',
        type:   'circle',
        source: 'properties',
        filter: ['all', ['!', ['has', 'point_count']], ['==', ['get', 'impact_class'], 'Dispatch']],
        paint: {
          'circle-color':  '#FF4444',
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 8, 10, 15, 14, 24],
          'circle-blur':   1,
          'circle-opacity': ['interpolate', ['linear'], ['zoom'], 4, 0.35, 10, 0.5],
        }
      });

      /* Individual property pins — radius & stroke emphasise Dispatch and grow
         with zoom (zoom-dependent density/label behaviour). */
      map.addLayer({
        id:     'pins',
        type:   'circle',
        source: 'properties',
        filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-color':        ['get', 'color'],
          'circle-radius':       pinRadius('__none__'),
          'circle-opacity':      0.92,
          'circle-stroke-width': pinStroke('__none__'),
          'circle-stroke-color': ['case',
            ['==', ['get', 'impact_class'], 'Dispatch'], 'rgba(255,255,255,0.85)',
            'rgba(255,255,255,0.4)'],
        }
      });

      /* Address labels appear only when zoomed into a neighbourhood, so the
         globe stays clean at altitude but is legible up close. */
      map.addLayer({
        id:     'pin-labels',
        type:   'symbol',
        source: 'properties',
        filter: ['!', ['has', 'point_count']],
        minzoom: 12.5,
        layout: {
          'text-field':         ['coalesce', ['get', 'address'], ['get', 'property_id']],
          'text-size':          10,
          'text-offset':        [0, 1.2],
          'text-anchor':        'top',
          'text-font':          ['DIN Offc Pro Medium', 'Arial Unicode MS Regular'],
          'text-optional':      true,
          'text-allow-overlap': false,
        },
        paint: {
          'text-color':      '#CFE8F2',
          'text-halo-color': 'rgba(0,0,8,0.92)',
          'text-halo-width': 1.2,
        }
      });

      /* ── Portfolio source */
      map.addSource('portfolio', {
        type: 'geojson',
        data: emptyFC(),
      });

      map.addLayer({
        id:     'portfolio-pins',
        type:   'circle',
        source: 'portfolio',
        paint: {
          'circle-color':        ['coalesce', ['get', 'color'], '#E8D5A3'],
          'circle-radius':       5,
          'circle-opacity':      0.85,
          'circle-stroke-width': 1.5,
          'circle-stroke-color': 'rgba(232,213,163,0.5)',
        }
      });

      /* Address labels for portfolio pins — same zoomed-in-only behaviour
         as the event pin-labels layer. */
      map.addLayer({
        id:     'portfolio-pin-labels',
        type:   'symbol',
        source: 'portfolio',
        minzoom: 12.5,
        layout: {
          'text-field':         ['coalesce', ['get', 'address'], ['get', 'property_id']],
          'text-size':          10,
          'text-offset':        [0, 1.2],
          'text-anchor':        'top',
          'text-font':          ['DIN Offc Pro Medium', 'Arial Unicode MS Regular'],
          'text-optional':      true,
          'text-allow-overlap': false,
        },
        paint: {
          'text-color':      '#E8D5A3',
          'text-halo-color': 'rgba(0,0,8,0.92)',
          'text-halo-width': 1.2,
        }
      });

      /* ── Portfolio area outline (bounding box around the analyzed
         properties) + a label naming the area and property count. */
      map.addSource('portfolio-bounds', { type: 'geojson', data: emptyFC() });
      map.addSource('portfolio-bounds-label', { type: 'geojson', data: emptyFC() });

      map.addLayer({
        id:     'portfolio-bounds-fill',
        type:   'fill',
        source: 'portfolio-bounds',
        paint: {
          'fill-color':   '#E8D5A3',
          'fill-opacity': 0.06,
        }
      });

      map.addLayer({
        id:     'portfolio-bounds-line',
        type:   'line',
        source: 'portfolio-bounds',
        paint: {
          'line-color':     '#E8D5A3',
          'line-width':     2,
          'line-dasharray': [2, 1.5],
          'line-opacity':   0.85,
        }
      });

      map.addLayer({
        id:     'portfolio-bounds-label',
        type:   'symbol',
        source: 'portfolio-bounds-label',
        layout: {
          'text-field':  ['get', 'label'],
          'text-size':   13,
          'text-anchor': 'bottom',
          'text-offset': [0, -0.6],
          'text-font':   ['DIN Offc Pro Medium', 'Arial Unicode MS Bold'],
        },
        paint: {
          'text-color':      '#E8D5A3',
          'text-halo-color': 'rgba(0,0,8,0.92)',
          'text-halo-width': 1.4,
        }
      });

      /* ── Event zone box (zone-summary scope, distinct red styling) ── */
      map.addSource('event-zone', { type: 'geojson', data: emptyFC() });
      map.addLayer({
        id:     'event-zone-line',
        type:   'line',
        source: 'event-zone',
        paint: {
          'line-color':     '#FF6B6B',
          'line-width':     1.8,
          'line-dasharray': [4, 2],
          'line-opacity':   0.7,
        }
      });
      map.addLayer({
        id:     'event-zone-label',
        type:   'symbol',
        source: 'event-zone',
        layout: {
          'text-field':  'EVENT ZONE',
          'text-size':   11,
          'text-anchor': 'top-left',
          'text-offset': [0.5, 0.3],
          'text-font':   ['DIN Offc Pro Medium', 'Arial Unicode MS Bold'],
        },
        paint: {
          'text-color':      '#FF9B9B',
          'text-halo-color': 'rgba(0,0,8,0.92)',
          'text-halo-width': 1.3,
        }
      });

      /* ── Storm track (NHC best track, simplified) ── */
      map.addSource('storm-track', { type: 'geojson', data: emptyFC() });

      map.addLayer({
        id:     'storm-track-line',
        type:   'line',
        source: 'storm-track',
        filter: ['==', ['get', 'kind'], 'track'],
        paint: {
          'line-color':     '#FF8C42',
          'line-width':     3.5,
          'line-dasharray': [3, 2],
          'line-opacity':   1,
        }
      });

      map.addLayer({
        id:     'storm-track-fixes',
        type:   'circle',
        source: 'storm-track',
        filter: ['==', ['get', 'kind'], 'fix'],
        paint: {
          'circle-color': ['match', ['get', 'category'],
            '5', CAT_COLORS['5'], '4', CAT_COLORS['4'], '3', CAT_COLORS['3'],
            '2', CAT_COLORS['2'], '1', CAT_COLORS['1'], 'TS', CAT_COLORS['TS'],
            CAT_COLORS['TD']],
          'circle-radius': ['interpolate', ['linear'], ['get', 'wind_kt'],
                            25, 4, 90, 7, 140, 10],
          'circle-opacity': 0.9,
          'circle-stroke-width': 1.5,
          'circle-stroke-color': 'rgba(0,0,8,0.8)',
        }
      });

      map.addLayer({
        id:     'storm-track-labels',
        type:   'symbol',
        source: 'storm-track',
        filter: ['==', ['get', 'kind'], 'fix'],
        minzoom: 5,
        layout: {
          'text-field':  ['concat', 'Cat ', ['get', 'category'], ' · ', ['get', 'time']],
          'text-size':   10,
          'text-offset': [0, 1.3],
          'text-anchor': 'top',
          'text-font':   ['DIN Offc Pro Medium', 'Arial Unicode MS Regular'],
          'text-optional': true,
        },
        paint: {
          'text-color':      '#FFCFA3',
          'text-halo-color': 'rgba(0,0,8,0.92)',
          'text-halo-width': 1.2,
        }
      });

      /* ── 3D property inspect: one source, three extrusion layers.
         Walls and roof slabs are coloured by triage decision through a
         data-driven expression — the reason this is built from ordinary
         fill-extrusions rather than a mesh layer. Water is added last so it
         blends over the walls behind it. All three are empty and invisible
         until inspect mode is switched on. */
      map.addSource('buildings-3d', { type: 'geojson', data: emptyFC() });

      map.addLayer({
        id:     'building-walls',
        type:   'fill-extrusion',
        source: 'buildings-3d',
        filter: ['==', ['get', 'kind'], 'wall'],
        layout: { visibility: 'none' },
        paint: {
          'fill-extrusion-color':   ['get', 'color'],
          'fill-extrusion-base':    ['get', 'base'],
          'fill-extrusion-height':  ['get', 'height'],
          'fill-extrusion-opacity': 0.94,
          'fill-extrusion-vertical-gradient': true,
        }
      });

      map.addLayer({
        id:     'building-roofs',
        type:   'fill-extrusion',
        source: 'buildings-3d',
        filter: ['==', ['get', 'kind'], 'roof'],
        layout: { visibility: 'none' },
        paint: {
          'fill-extrusion-color':   ['get', 'color'],
          'fill-extrusion-base':    ['get', 'base'],
          'fill-extrusion-height':  ['get', 'height'],
          // Fully opaque: the roof slabs are nested solids (see gableSlabs),
          // so any translucency would composite them against each other and
          // band the roof with darker rings.
          'fill-extrusion-opacity': 1,
          'fill-extrusion-vertical-gradient': false,
        }
      });

      map.addLayer({
        id:     'building-water',
        type:   'fill-extrusion',
        source: 'buildings-3d',
        filter: ['==', ['get', 'kind'], 'water'],
        layout: { visibility: 'none' },
        paint: {
          'fill-extrusion-color':   '#2E86C1',
          'fill-extrusion-base':    ['get', 'base'],
          'fill-extrusion-height':  ['get', 'height'],
          'fill-extrusion-opacity': 0.55,
          'fill-extrusion-vertical-gradient': true,
        }
      });

      pinsReady.current = true;

      /* ── Flood overlay (raster, inserted below pins) */
      addFloodLayer(map, null);

      /* Start rotation */
      startRotation();
    });

    /* Cluster click → expand */
    map.on('click', 'clusters', (e) => {
      const [feat] = map.queryRenderedFeatures(e.point, { layers: ['clusters'] });
      map.getSource('properties').getClusterExpansionZoom(
        feat.properties.cluster_id,
        (err, zoom) => {
          if (err) return;
          map.flyTo({ center: feat.geometry.coordinates, zoom: zoom + 1, duration: 900 });
        }
      );
    });

    /* Pin click → open drawer. Mapbox GL serializes feature properties
       through JSON: null can arrive as the string "null" and booleans as
       "true"/"false", which would make the drawer render NaN/garbage —
       sanitize back to real types before handing to React. */
    map.on('click', 'pins', (e) => {
      onPropertySelect?.(cleanFeatureProps(e.features[0].properties));
    });

    map.on('click', 'portfolio-pins', (e) => {
      onPropertySelect?.({ ...cleanFeatureProps(e.features[0].properties), isPortfolio: true });
    });

    /* Clicking a house opens the same drawer its pin would — in inspect mode
       the building IS the click target, since it covers the pin. The building
       feature only carries an id, so the full property row is looked up. */
    ['building-walls', 'building-roofs'].forEach(layer => {
      map.on('click', layer, (e) => {
        const id = e.features?.[0]?.properties?.property_id;
        const prop = allPropsRef.current.find(p => String(p.property_id) === String(id));
        if (prop) onPropertySelect?.(prop);
      });
    });

    /* Cursors */
    ['clusters', 'pins', 'portfolio-pins', 'building-walls', 'building-roofs'].forEach(layer => {
      map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
    });

    /* Stop rotation on user interaction */
    map.on('mousedown',  stopRotation);
    map.on('touchstart', stopRotation);

    /* Track zoom so layer hints (FEMA renders only at neighborhood scale)
       can tell the user why nothing appeared yet. */
    map.on('zoomend', () => setZoomLevel(map.getZoom()));

    /* Reload buildings after any camera move that settles, so panning down a
       street brings the next block's houses in. Debounced inside. */
    map.on('moveend', () => { if (inspectRef.current) scheduleBuildingRefresh(); });

    return () => {
      stopRotation();
      map.remove();
      mapRef.current = null;
      pinsReady.current = false;
    };
  }, []);

  /* ── Auto-rotation ───────────────────────────────────────────── */
  const startRotation = useCallback(() => {
    isRotating.current = true;
    const rotate = () => {
      if (!isRotating.current || !mapRef.current) return;
      const c = mapRef.current.getCenter();
      mapRef.current.setCenter([c.lng + 0.012, c.lat]);
      rafRef.current = requestAnimationFrame(rotate);
    };
    rafRef.current = requestAnimationFrame(rotate);
  }, []);

  const stopRotation = useCallback(() => {
    isRotating.current = false;
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
  }, []);

  /* ── Update event properties ─────────────────────────────────── */
  useEffect(() => {
    if (!pinsReady.current || !mapRef.current) return;
    const src = mapRef.current.getSource('properties');
    if (!src) return;

    src.setData({
      type:     'FeatureCollection',
      features: properties.map(p => ({
        type:       'Feature',
        geometry:   { type: 'Point', coordinates: [+p.longitude, +p.latitude] },
        properties: { ...p, color: TRIAGE_COLORS[p.impact_class] || '#6B8FA3' },
      }))
    });
  }, [properties]);

  /* ── Update portfolio properties ─────────────────────────────── */
  useEffect(() => {
    if (!pinsReady.current || !mapRef.current) return;
    const map = mapRef.current;
    const src = map.getSource('portfolio');
    if (!src) return;

    const valid = portfolioProperties.filter(p => p.latitude && p.longitude);

    src.setData({
      type:     'FeatureCollection',
      features: valid.map(p => ({
        type:       'Feature',
        geometry:   { type: 'Point', coordinates: [+p.longitude, +p.latitude] },
        properties: { ...p, color: TRIAGE_COLORS[p.impact_class] || '#E8D5A3' },
      }))
    });

    const boundsSrc      = map.getSource('portfolio-bounds');
    const boundsLabelSrc = map.getSource('portfolio-bounds-label');
    if (!boundsSrc || !boundsLabelSrc) return;

    if (valid.length === 0) {
      boundsSrc.setData(emptyFC());
      boundsLabelSrc.setData(emptyFC());
      return;
    }

    const lats = valid.map(p => +p.latitude);
    const lons = valid.map(p => +p.longitude);
    let minLat = Math.min(...lats), maxLat = Math.max(...lats);
    let minLon = Math.min(...lons), maxLon = Math.max(...lons);

    // Pad so the box visibly frames the cluster instead of clipping the pins.
    const padLat = Math.max((maxLat - minLat) * 0.18, 0.004);
    const padLon = Math.max((maxLon - minLon) * 0.18, 0.004);
    minLat -= padLat; maxLat += padLat;
    minLon -= padLon; maxLon += padLon;

    boundsSrc.setData({
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [[
            [minLon, minLat], [maxLon, minLat],
            [maxLon, maxLat], [minLon, maxLat],
            [minLon, minLat],
          ]],
        },
      }],
    });

    boundsLabelSrc.setData({
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [(minLon + maxLon) / 2, maxLat] },
        properties: { label: `PORTFOLIO · ${valid.length} PROPERTIES` },
      }],
    });
  }, [portfolioProperties]);

  /* ── Event zone box (zone-summary scope) ─────────────────────── */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !pinsReady.current) return;
    const src = map.getSource('event-zone');
    if (!src) return;
    if (!zoneBbox || zoneBbox.length !== 4) {
      src.setData(emptyFC());
      return;
    }
    const [w, s, e, n] = zoneBbox;
    src.setData({
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
        },
      }],
    });
  }, [zoneBbox]);

  /* ── Exposure heat data + visibility ─────────────────────────── */
  useEffect(() => {
    if (!pinsReady.current || !mapRef.current) return;
    const map = mapRef.current;
    const src = map.getSource('exposure-heat');
    if (!src) return;

    // Whatever book is on screen, weighted by what a carrier cares about:
    // estimated loss where analyzed, coverage exposure otherwise, flood
    // depth as the last resort. Normalized 0.15–1 so a single giant policy
    // doesn't wash out the rest of the map.
    const all = [...properties, ...portfolioProperties]
      .filter(p => p.latitude && p.longitude);
    const val = p => +p.severity_mid_usd || +p.coverage_amount
                  || (+p.max_depth_ft || 0) * 50000 || 0;
    const max = Math.max(1, ...all.map(val));
    src.setData({
      type: 'FeatureCollection',
      features: all.map(p => ({
        type:       'Feature',
        geometry:   { type: 'Point', coordinates: [+p.longitude, +p.latitude] },
        properties: { heat_w: Math.max(0.15, val(p) / max) },
      }))
    });
  }, [properties, portfolioProperties]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer('exposure-heatmap')) return;
    map.setLayoutProperty('exposure-heatmap', 'visibility',
                          showHeat ? 'visible' : 'none');
  }, [showHeat]);

  /* ── Storm track overlay ─────────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !pinsReady.current) return;
    const src = map.getSource('storm-track');
    if (!src) return;
    src.setData(stormTrack && showTrack ? stormTrack : emptyFC());
  }, [stormTrack, showTrack]);

  /* Clicking the storm-track toggle ON flies to the track — the landfall
     segment is often hundreds of miles from the study area (Harvey came
     ashore 200mi southwest of Houston), so without this the toggle looks
     like it does nothing. */
  const flyToTrack = useCallback(() => {
    const map = mapRef.current;
    const line = stormTrack?.features?.find(f => f.geometry.type === 'LineString');
    if (!map || !line) return;
    const lons = line.geometry.coordinates.map(c => c[0]);
    const lats = line.geometry.coordinates.map(c => c[1]);
    stopRotation();
    map.fitBounds([[Math.min(...lons), Math.min(...lats)],
                   [Math.max(...lons), Math.max(...lats)]],
                  { padding: 90, duration: 1800, maxZoom: 8 });
  }, [stormTrack, stopRotation]);

  /* ── FEMA NFHL raster overlay (US flood zones) ───────────────── */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !pinsReady.current) return;
    if (showFema) {
      if (!map.getSource('fema-nfhl')) {
        map.addSource('fema-nfhl', {
          type: 'raster', tiles: [FEMA_NFHL_TILES], tileSize: 256,
        });
        map.addLayer({
          id: 'fema-nfhl-layer', type: 'raster', source: 'fema-nfhl',
          paint: { 'raster-opacity': 0.55 },
        }, 'clusters');
      }
    } else {
      if (map.getLayer('fema-nfhl-layer'))  map.removeLayer('fema-nfhl-layer');
      if (map.getSource('fema-nfhl'))       map.removeSource('fema-nfhl');
    }
  }, [showFema]);

  /* ── 3D property inspect ─────────────────────────────────────── */

  /* Every property currently on screen, event and portfolio alike, kept in a
     ref so the map's long-lived event handlers always see the latest book. */
  useEffect(() => {
    allPropsRef.current = [...properties, ...portfolioProperties]
      .filter(p => p.latitude && p.longitude);
    if (inspectRef.current) scheduleBuildingRefresh();
  }, [properties, portfolioProperties]);

  /* Pull footprints for the properties in view and rebuild the extrusions.
     Stable identity (no deps) because it is bound once to map events and
     reads everything it needs from refs. */
  const refreshBuildings = useCallback(async () => {
    const map = mapRef.current;
    if (!map || !inspectRef.current || !map.getSource('buildings-3d')) return;

    if (map.getZoom() < INSPECT_MIN_ZOOM) {
      map.getSource('buildings-3d').setData(emptyFC());
      setBuildingStatus({ tooFar: true });
      return;
    }

    const bounds = map.getBounds();
    const visible = allPropsRef.current
      .filter(p => bounds.contains([+p.longitude, +p.latitude]))
      .slice(0, MAX_BUILDINGS);

    if (visible.length === 0) {
      map.getSource('buildings-3d').setData(emptyFC());
      setBuildingStatus({ empty: true });
      return;
    }

    const seq = ++buildingReq.current;
    setBuildingStatus(s => ({ ...(s || {}), loading: true, tooFar: false, empty: false }));

    let payload;
    try {
      payload = await api.getBuildings(visible.map(p => ({
        property_id: p.property_id,
        latitude:  +p.latitude,
        longitude: +p.longitude,
      })));
    } catch {
      /* The backend is unreachable — draw illustrative boxes locally rather
         than dropping the user into an empty 3D scene. */
      payload = {
        available: false,
        reason: 'Footprint service unreachable — showing illustrative boxes.',
        buildings: visible.map(p => ({
          property_id: p.property_id,
          ring: placeholderRing(+p.longitude, +p.latitude),
          height_m: 3.2, footprint_source: 'placeholder', height_source: 'default',
        })),
        summary: { rendered: visible.length, footprints_matched: 0, match_rate: 0 },
      };
    }

    /* A newer viewport already asked; this answer is stale. */
    if (seq !== buildingReq.current || !inspectRef.current) return;

    const byId = new Map(visible.map(p => [String(p.property_id), p]));
    const features = [];
    let solarHeights = 0;
    for (const b of payload.buildings || []) {
      const prop = byId.get(String(b.property_id));
      if (!prop) continue;

      /* Solar API roof geometry, when the backend was allowed to fetch it,
         reports the highest roof plane in metres above SEA LEVEL. Terrain is
         already loaded in this mode, so the ground elevation under the
         building is free to sample here — no second (billable) elevation API.
         The result replaces the typology-guessed height only when it lands in
         a physically plausible range; otherwise the guess stands, because a
         datum mismatch should never produce a six-storey bungalow. */
      const roofElev = b.solar?.max_plane_elev_m;
      if (roofElev != null && b.solar?.quality_ok && b.centroid) {
        const ground = mapRef.current?.queryTerrainElevation(b.centroid);
        if (ground != null) {
          const measured = roofElev - ground;
          if (measured >= 2.2 && measured <= 70) {
            b.height_m = Math.round(measured * 100) / 100;
            b.height_source = 'solar';
            solarHeights += 1;
          }
        }
      }

      features.push(...buildingFeatures(b, prop, {
        color: TRIAGE_COLORS[prop.impact_class] || '#6B8FA3',
      }));
    }

    const src = mapRef.current?.getSource('buildings-3d');
    if (src) src.setData({ type: 'FeatureCollection', features });

    setBuildingStatus({
      loading: false,
      available: payload.available,
      reason: payload.reason,
      count: (payload.buildings || []).length,
      matched: payload.summary?.footprints_matched ?? 0,
      matchRate: payload.summary?.match_rate ?? 0,
      heightSources: payload.summary?.height_sources || {},
      flooded: visible.filter(p => (+p.max_depth_ft || 0) >= 0.1).length,
      solarHeights,
      solar: payload.summary?.solar || null,
    });
  }, []);

  /* Coalesce the burst of moveend events a single drag produces. */
  const scheduleBuildingRefresh = useCallback(() => {
    if (buildingTimer.current) clearTimeout(buildingTimer.current);
    buildingTimer.current = setTimeout(() => { refreshBuildings(); }, 260);
  }, [refreshBuildings]);

  /* Entering inspect mode drapes real terrain, tilts the camera, and reveals
     the extrusion layers; leaving it puts every one of those back. Terrain is
     what makes a sloped lot read correctly — without it the houses and their
     water planes sit on a flat sheet. */
  useEffect(() => {
    const map = mapRef.current;
    inspectRef.current = inspect3D;
    if (!map || !pinsReady.current) return;

    const apply = () => {
      if (inspect3D) {
        if (!map.getSource('mapbox-dem')) {
          map.addSource('mapbox-dem', {
            type: 'raster-dem',
            url:  'mapbox://mapbox.mapbox-terrain-dem-v1',
            tileSize: 512, maxzoom: 14,
          });
        }
        map.setTerrain({ source: 'mapbox-dem', exaggeration: TERRAIN_EXAGGERATION });
        ['building-walls', 'building-roofs', 'building-water'].forEach(id => {
          if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'visible');
        });

        stopRotation();
        /* From altitude, drop into the book so there is something to look at;
           if the user is already in a neighbourhood, just tilt. */
        const target = map.getZoom() < INSPECT_MIN_ZOOM
          ? nearestPropertyCenter(allPropsRef.current, map.getCenter())
          : null;
        map.easeTo({
          ...(target ? { center: target, zoom: INSPECT_FLY_ZOOM } : {}),
          pitch: Math.max(map.getPitch(), INSPECT_PITCH),
          duration: 1400,
        });
        scheduleBuildingRefresh();
      } else {
        ['building-walls', 'building-roofs', 'building-water'].forEach(id => {
          if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'none');
        });
        if (map.getSource('buildings-3d')) map.getSource('buildings-3d').setData(emptyFC());
        map.setTerrain(null);
        map.easeTo({ pitch: 0, duration: 700 });
        setBuildingStatus(null);
      }
    };

    if (map.isStyleLoaded()) apply();
    else map.once('idle', apply);
  }, [inspect3D, scheduleBuildingRefresh, stopRotation]);

  /* Depth changes (a fresh analysis lands) must move the water planes. */
  useEffect(() => {
    if (inspectRef.current) scheduleBuildingRefresh();
  }, [properties.map(p => p.max_depth_ft).join(','), scheduleBuildingRefresh]);

  useEffect(() => () => {
    if (buildingTimer.current) clearTimeout(buildingTimer.current);
  }, []);

  /* ── Fly-to ──────────────────────────────────────────────────── */
  useEffect(() => {
    if (!flyTarget || !mapRef.current) return;
    stopRotation();

    if (flyTarget.bounds) {
      mapRef.current.fitBounds(flyTarget.bounds, {
        padding: {
          top: 80, bottom: 80, right: 80,
          left: 80 + leftInset,
        },
        maxZoom:  flyTarget.maxZoom || 15,
        duration: 2800,
      });
    } else {
      mapRef.current.flyTo({
        center:   flyTarget.center,
        zoom:     flyTarget.zoom || 10,
        duration: 2500,
        easing:   t => t * (2 - t),
      });
    }
  }, [flyTarget]);

  /* ── Flood overlay tile URL ──────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !pinsReady.current) return;
    // Remove old flood layer/source, add new one
    if (map.getLayer('flood-layer'))   map.removeLayer('flood-layer');
    if (map.getSource('flood-overlay')) map.removeSource('flood-overlay');
    addFloodLayer(map, tileUrl);
  }, [tileUrl]);

  /* ── Pre/post time toggle ────────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    // Flood raster visibility (only when we have real GEE tile)
    if (map.getLayer('flood-layer')) {
      map.setLayoutProperty(
        'flood-layer', 'visibility',
        timeMode === 'post' && tileUrl ? 'visible' : 'none'
      );
    }

    // Pin opacity shifts: post = full color, pre = ghosted
    if (map.getLayer('pins')) {
      map.setPaintProperty('pins', 'circle-opacity',
        timeMode === 'pre' ? 0.3 : 0.9);
      map.setPaintProperty('pins', 'circle-color',
        timeMode === 'pre'
          ? '#3A5060'
          : ['get', 'color']);
    }
  }, [timeMode, tileUrl]);

  /* ── Highlight selected pin ──────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer('pins')) return;
    const selId = selectedProperty?.property_id || '__none__';
    map.setPaintProperty('pins', 'circle-radius', pinRadius(selId));
    map.setPaintProperty('pins', 'circle-stroke-width', pinStroke(selId));
  }, [selectedProperty]);

  /* ── Globe brightness ────────────────────────────────────────── */
  const containerStyle = {
    position:   'fixed',
    inset:      0,
    zIndex:     0,
    filter:     dimmed ? 'brightness(0.62)' : 'brightness(1)',
    transition: 'filter 0.4s ease',
  };

  const toggleStyle = (active) => ({
    padding: '6px 12px', borderRadius: 999, cursor: 'pointer',
    fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.08em',
    textTransform: 'uppercase', fontFamily: 'var(--font)',
    background: active ? 'var(--teal-dim)' : 'var(--panel)',
    border: `1px solid ${active ? 'rgba(168,212,230,0.45)' : 'rgba(255,255,255,0.12)'}`,
    color: active ? 'var(--teal)' : 'var(--text-muted)',
    backdropFilter: 'blur(10px)',
    transition: 'all 0.15s ease',
  });

  const hasTriagePins = properties.length > 0 ||
    portfolioProperties.some(p => p.impact_class);
  const hasAnyPins = properties.length > 0 || portfolioProperties.length > 0;

  return (
    <>
      <div ref={containerRef} style={containerStyle} />

      {/* Pin legend — plain claims-operations language, shown whenever
          triaged pins are on the map so a first-time viewer never has to
          ask what the colors mean. */}
      {hasTriagePins && !dimmed && !isMobile && (
        <div className="anim-fade-in" style={{
          /* Sits above the chat bar (~72px tall) so they never overlap. */
          position: 'fixed', bottom: 96, left: 16 + leftInset, zIndex: 5,
          padding: '10px 14px', borderRadius: 10,
          background: 'var(--panel)', border: '1px solid rgba(255,255,255,0.08)',
          backdropFilter: 'blur(12px)', transition: 'left 0.25s ease',
        }}>
          <div style={{ fontSize: '0.56rem', fontWeight: 700, letterSpacing: '0.12em', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 7 }}>
            Triage decision
          </div>
          {[['#FF4444', 'Dispatch: send an adjuster'],
            ['#FFB347', 'Review: needs a human call'],
            ['#4CAF82', 'Approve remotely'],
            ['#6B8FA3', 'No flood detected: resolve remotely'],
          ].map(([c, label]) => (
            <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '2px 0' }}>
              <span style={{ width: 9, height: 9, borderRadius: '50%', background: c, flexShrink: 0 }} />
              <span style={{ fontSize: '0.66rem', color: 'var(--text-secondary)' }}>{label}</span>
            </div>
          ))}
        </div>
      )}

      {/* First-run guide — fades in, lingers a few seconds, fades away on its
          own (and disappears the moment anything is loaded). */}
      {!hasAnyPins && !guideGone && (
        <div style={{
          position: 'fixed', bottom: 90, left: '50%',
          transform: `translateX(-50%) translateY(${guideVisible ? 0 : 8}px)`,
          zIndex: 5, display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 12, padding: '12px 18px', maxWidth: '94vw',
          background: 'var(--panel)', border: '1px solid rgba(168,212,230,0.14)',
          borderRadius: 12, backdropFilter: 'blur(14px)', pointerEvents: 'none',
          opacity: guideVisible ? 1 : 0, transition: 'opacity 0.7s ease, transform 0.7s ease',
        }}>
          {[['1', 'Upload your policy portfolio'],
            ['2', 'Set the flood date & run analysis'],
            ['3', 'Review exposure & dispatch queue'],
          ].map(([n, label]) => (
            <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <span style={{
                width: 20, height: 20, borderRadius: '50%', flexShrink: 0,
                background: 'linear-gradient(135deg, #DDF1FB, #8FC4E8)', color: '#000',
                fontSize: '0.68rem', fontWeight: 800, display: 'flex',
                alignItems: 'center', justifyContent: 'center',
              }}>{n}</span>
              <span style={{ fontSize: '0.74rem', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{label}</span>
            </div>
          ))}
        </div>
      )}

      {/* Map layer toggles */}
      <div style={{
        position: 'fixed', bottom: isMobile ? 150 : 22, right: isMobile ? 8 : 16, zIndex: 5,
        display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end',
        maxWidth: isMobile ? 'calc(100vw - 72px)' : 'none', // stay clear of the left rail
      }}>
        {/* 3D inspect status + the provenance caveat. Dollar reserves are
            attached to these properties, so the panel states plainly which
            shapes are real survey-grade data (none of them) and which parts
            are estimated — it is not a footnote in a tooltip. */}
        {inspect3D && !isMobile && (
          <div style={{
            maxWidth: 268, padding: '9px 12px', borderRadius: 9,
            background: 'var(--panel)', border: '1px solid rgba(168,212,230,0.28)',
            fontSize: '0.64rem', color: 'var(--text-secondary)', lineHeight: 1.5,
            backdropFilter: 'blur(10px)',
          }}>
            {buildingStatus?.tooFar ? (
              <span style={{ color: '#FFB347' }}>
                Zoom into a neighbourhood (z{INSPECT_MIN_ZOOM}+) to draw buildings.
              </span>
            ) : buildingStatus?.empty ? (
              <span style={{ color: '#FFB347' }}>No properties in view — pan to your portfolio.</span>
            ) : buildingStatus?.loading ? (
              <span>Loading building footprints…</span>
            ) : buildingStatus ? (
              <>
                <div style={{ color: 'var(--teal)', fontWeight: 700, marginBottom: 3 }}>
                  {buildingStatus.count} building{buildingStatus.count === 1 ? '' : 's'}
                  {buildingStatus.flooded > 0 && ` · ${buildingStatus.flooded} with modeled water`}
                </div>
                <div>{footprintSummary(buildingStatus)}</div>
                {buildingStatus.solarHeights > 0 && (
                  <div style={{ marginTop: 4, color: '#7FD1A8' }}>
                    {buildingStatus.solarHeights} measured height
                    {buildingStatus.solarHeights === 1 ? '' : 's'} from Solar API
                  </div>
                )}
                <div style={{ marginTop: 5, color: 'var(--text-muted)', fontSize: '0.6rem' }}>
                  Footprints are OpenStreetMap outlines. Heights are estimated
                  from building type unless marked as measured. Water height is
                  the modeled depth for that property. Illustrative — not a
                  structural survey.
                </div>
                {buildingStatus.available === false && buildingStatus.reason && (
                  <div style={{ marginTop: 5, color: '#FFB347', fontSize: '0.6rem' }}>
                    {buildingStatus.reason}
                  </div>
                )}
              </>
            ) : null}
          </div>
        )}

        {showFema && zoomLevel < 9 && (
          <div style={{
            maxWidth: 240, padding: '7px 11px', borderRadius: 8,
            background: 'var(--panel)', border: '1px solid rgba(255,179,71,0.3)',
            fontSize: '0.64rem', color: '#FFB347', lineHeight: 1.45,
            backdropFilter: 'blur(10px)',
          }}>
            FEMA zones are parcel-scale. Zoom into a US neighborhood to see them.
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {hasAnyPins && (
            <button onClick={() => setInspect3D(v => !v)} style={toggleStyle(inspect3D)}
                    title="Drape real terrain and draw each property as a building, with the modeled flood depth as water up its walls. Neighbourhood zoom only — the pin view stays the default across a portfolio.">
              ⌂ 3D inspect
            </button>
          )}
          {hasAnyPins && (
            <button onClick={() => setShowHeat(v => !v)} style={toggleStyle(showHeat)}
                    title="Dollar-weighted exposure concentration: estimated loss where analyzed, coverage otherwise. Pins stay on.">
              ◉ Exposure heat
            </button>
          )}
          {stormTrack && (
            <button
              onClick={() => {
                const next = !showTrack;
                setShowTrack(next);
                if (next) flyToTrack();   // the track is often off-screen
              }}
              style={toggleStyle(showTrack)}
              title="NHC best track (simplified) for this event. Click to fly to it">
              🌀 Storm track
            </button>
          )}
          <button onClick={() => setShowFema(v => !v)} style={toggleStyle(showFema)}
                  title="FEMA National Flood Hazard Layer. US coverage only, renders when zoomed in">
            FEMA zones
          </button>
          <button
            onClick={() => setShowTutorial(true)}
            style={{
              ...toggleStyle(false),
              display: 'flex', alignItems: 'center', gap: 5,
              background: 'var(--teal-dim)', border: '1px solid var(--teal-border)',
              color: 'var(--teal)',
            }}
            title="A quick guided walkthrough of everything Altis can do">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10"/>
              <path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4"/>
              <line x1="12" y1="17" x2="12" y2="17"/>
            </svg>
            Tutorial
          </button>
        </div>
      </div>

      <Tutorial open={showTutorial} onClose={() => setShowTutorial(false)} />
    </>
  );
}

/* ── Helpers ─────────────────────────────────────────────────────── */
function emptyFC() {
  return { type: 'FeatureCollection', features: [] };
}

/* The property nearest the current camera centre, as [lon, lat] — where the
   3D toggle flies to when it is pressed from altitude, so the mode always
   opens on a house rather than an empty field. */
function nearestPropertyCenter(props, center) {
  let best = null, bestD = Infinity;
  for (const p of props || []) {
    const d = Math.hypot(+p.longitude - center.lng, +p.latitude - center.lat);
    if (d < bestD) { bestD = d; best = p; }
  }
  return best ? [+best.longitude, +best.latitude] : null;
}

/* "12 real footprints · 3 estimated" — the plain-language provenance line
   under the 3D legend. */
function footprintSummary(status) {
  if (!status || status.count == null) return null;
  const estimated = Math.max(0, status.count - (status.matched || 0));
  const parts = [];
  if (status.matched) parts.push(`${status.matched} mapped footprint${status.matched === 1 ? '' : 's'}`);
  if (estimated)      parts.push(`${estimated} placeholder box${estimated === 1 ? '' : 'es'}`);
  return parts.join(' · ');
}

/* Undo Mapbox GL's JSON round-trip on feature properties: "null" → null,
   "true"/"false" → booleans. Leaves real strings/numbers untouched. */
function cleanFeatureProps(p) {
  const out = {};
  for (const [k, v] of Object.entries(p || {})) {
    if (v === 'null' || v === 'undefined') out[k] = null;
    else if (v === 'true')  out[k] = true;
    else if (v === 'false') out[k] = false;
    else out[k] = v;
  }
  return out;
}

/* Pin radius: selected pin is largest; Dispatch pins are emphasised and all
   pins grow with zoom so a dense neighbourhood stays readable up close. */
function pinRadius(selId) {
  return [
    'case',
    ['==', ['get', 'property_id'], selId], 12,
    ['==', ['get', 'impact_class'], 'Dispatch'],
      ['interpolate', ['linear'], ['zoom'], 4, 6, 10, 8.5, 14, 12],
    ['interpolate', ['linear'], ['zoom'], 4, 4, 10, 6, 14, 8.5],
  ];
}

function pinStroke(selId) {
  return [
    'case',
    ['==', ['get', 'property_id'], selId], 3,
    ['==', ['get', 'impact_class'], 'Dispatch'], 2.4,
    1.5,
  ];
}

function addFloodLayer(map, tileUrl) {
  map.addSource('flood-overlay', {
    type:     'raster',
    tiles:    tileUrl ? [tileUrl] : [],
    tileSize: 256,
  });
  map.addLayer({
    id:     'flood-layer',
    type:   'raster',
    source: 'flood-overlay',
    paint:  { 'raster-opacity': 0.65 },
    layout: { visibility: tileUrl ? 'visible' : 'none' },
  }, 'clusters');
}
