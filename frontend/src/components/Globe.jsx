import { useEffect, useRef, useCallback, useState } from 'react';
import { useIsMobile } from '../hooks/useIsMobile.js';
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
  const isMobile = useIsMobile();

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
      /* Deep space atmosphere with starfield */
      map.setFog({
        color:           'rgba(2, 4, 10, 0.9)',
        'high-color':    'rgba(2, 4, 10, 0.9)',
        'horizon-blend': 0.35,
        'space-color':   'rgba(0, 0, 4, 1)',
        'star-intensity': 0.88,
      });

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

    /* Cursors */
    ['clusters', 'pins', 'portfolio-pins'].forEach(layer => {
      map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
    });

    /* Stop rotation on user interaction */
    map.on('mousedown',  stopRotation);
    map.on('touchstart', stopRotation);

    /* Track zoom so layer hints (FEMA renders only at neighborhood scale)
       can tell the user why nothing appeared yet. */
    map.on('zoomend', () => setZoomLevel(map.getZoom()));

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

      {/* First-run guide — three steps, gone as soon as anything is loaded */}
      {!hasAnyPins && (
        <div className="anim-fade-in" style={{
          position: 'fixed', bottom: 90, left: '50%', transform: 'translateX(-50%)',
          zIndex: 5, display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 12, padding: '12px 18px', maxWidth: '94vw',
          background: 'var(--panel)', border: '1px solid rgba(168,212,230,0.14)',
          borderRadius: 12, backdropFilter: 'blur(14px)', pointerEvents: 'none',
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
        position: 'fixed', bottom: isMobile ? 148 : 22, right: isMobile ? 8 : 16, zIndex: 5,
        display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end',
      }}>
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
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
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
        </div>
      </div>
    </>
  );
}

/* ── Helpers ─────────────────────────────────────────────────────── */
function emptyFC() {
  return { type: 'FeatureCollection', features: [] };
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
