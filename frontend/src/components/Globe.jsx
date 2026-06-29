import { useEffect, useRef, useCallback } from 'react';
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';

const TRIAGE_COLORS = {
  'Dispatch':       '#FF4444',
  'Remote-Approve': '#4CAF82',
  'Remote-Deny':    '#6B8FA3',
  'Review':         '#FFB347',
  'Portfolio':      '#E8D5A3',
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
}) {
  const containerRef = useRef(null);
  const mapRef       = useRef(null);
  const rafRef       = useRef(null);
  const isRotating   = useRef(true);
  const pinsReady    = useRef(false);

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

    /* Pin click → open drawer */
    map.on('click', 'pins', (e) => {
      const p = e.features[0].properties;
      onPropertySelect?.({ ...p });
    });

    map.on('click', 'portfolio-pins', (e) => {
      const p = e.features[0].properties;
      onPropertySelect?.({ ...p, isPortfolio: true });
    });

    /* Cursors */
    ['clusters', 'pins', 'portfolio-pins'].forEach(layer => {
      map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
    });

    /* Stop rotation on user interaction */
    map.on('mousedown',  stopRotation);
    map.on('touchstart', stopRotation);

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
        properties: { label: `PORTFOLIO — ${valid.length} PROPERTIES` },
      }],
    });
  }, [portfolioProperties]);

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

  return <div ref={containerRef} style={containerStyle} />;
}

/* ── Helpers ─────────────────────────────────────────────────────── */
function emptyFC() {
  return { type: 'FeatureCollection', features: [] };
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
