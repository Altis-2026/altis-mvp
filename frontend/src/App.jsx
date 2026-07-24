import { useState, useCallback, useEffect } from 'react';
import Globe            from './components/Globe.jsx';
import Header           from './components/Header.jsx';
import KPIBar            from './components/KPIBar.jsx';
import TimeSlider        from './components/TimeSlider.jsx';
import PropertyDrawer    from './components/PropertyDrawer.jsx';
import UploadModal       from './components/UploadModal.jsx';
import Sidebar, { RAIL_WIDTH, PANEL_WIDTH } from './components/Sidebar.jsx';
import EventsPanel        from './components/EventsPanel.jsx';
import PortfoliosPanel    from './components/PortfoliosPanel.jsx';
import AnalysisPanel      from './components/AnalysisPanel.jsx';
import SarComparePanel    from './components/SarComparePanel.jsx';
import ReportsPanel       from './components/ReportsPanel.jsx';
import DispatchQueuePanel from './components/DispatchQueuePanel.jsx';
import OperationsPanel    from './components/OperationsPanel.jsx';
import DataGrid           from './components/DataGrid.jsx';
import ChatBar            from './components/ChatBar.jsx';
import { api }            from './services/api.js';

function computeBounds(properties) {
  const valid = properties.filter(p => p.latitude && p.longitude);
  if (valid.length === 0) return null;
  let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
  valid.forEach(p => {
    const lat = +p.latitude, lon = +p.longitude;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
  });
  return [[minLon, minLat], [maxLon, maxLat]];
}

export default function App() {
  /* ── Events (fetched dynamically — no more hardcoded list) ──── */
  const [events, setEvents] = useState([]);

  useEffect(() => {
    api.getEvents().then(setEvents).catch(() => setEvents([]));
  }, []);

  /* ── Event selection / map state ─────────────────────────────── */
  const [selectedEvent,    setSelectedEvent]    = useState(null);
  const [properties,       setProperties]       = useState([]);
  const [stats,            setStats]            = useState(null);
  const [selectedProperty, setSelectedProperty] = useState(null);
  const [timeMode,         setTimeMode]         = useState('post');
  const [tileUrl,          setTileUrl]          = useState(null);
  const [flyTarget,        setFlyTarget]        = useState(null);
  const [showUpload,       setShowUpload]       = useState(false);
  const [loading,          setLoading]          = useState(false);

  /* ── Sidebar ──────────────────────────────────────────────────── */
  const [activePanel, setActivePanel] = useState(null);
  const leftInset = RAIL_WIDTH + (activePanel ? PANEL_WIDTH : 0);

  /* ── Claims data grid (full-screen) ──────────────────────────── */
  const [grid, setGrid] = useState(null); // { kind, rows, title } | null

  /* ── Portfolios ───────────────────────────────────────────────── */
  const [portfolios,       setPortfolios]       = useState([]);
  const [portfolioId,      setPortfolioId]      = useState(null);
  const [portfolioProps,   setPortfolioProps]   = useState([]);
  const [portfolioAnalyzed, setPortfolioAnalyzed] = useState(false);
  const [analyzing,        setAnalyzing]        = useState(false);

  /* ── Live, global satellite analysis ─────────────────────────── */
  const [geeLive,       setGeeLive]       = useState(null);
  const [liveAnalyzing, setLiveAnalyzing] = useState(false);
  const [liveEventDate, setLiveEventDate] = useState(null);
  const [liveMeta,      setLiveMeta]      = useState(null);
  const [stormTrack,    setStormTrack]    = useState(null);
  const [savedSettings, setSavedSettings] = useState(null);  // {eventDate, preDays, postDays}
  const [zoneSummary,   setZoneSummary]   = useState(null);  // fast PIF check
  const [liveError,     setLiveError]     = useState('');    // inline, never alert()

  useEffect(() => {
    api.geeStatus().then(d => setGeeLive(!!d.live_analysis)).catch(() => setGeeLive(false));
  }, []);

  const refreshPortfolios = useCallback(() => {
    api.listPortfolios().then(d => setPortfolios(d.portfolios || [])).catch(() => {});
  }, []);

  useEffect(() => { refreshPortfolios(); }, [refreshPortfolios]);

  /* ── SAR compare tray (max 4) ─────────────────────────────────── */
  const [compareList, setCompareList] = useState([]);
  const compareIds  = new Set(compareList.map(p => p.property_id));
  const compareFull = compareList.length >= 4;

  const addToCompare = useCallback((property) => {
    setCompareList(prev => {
      if (prev.length >= 4 || prev.some(p => p.property_id === property.property_id)) return prev;
      return [...prev, property];
    });
  }, []);
  const removeFromCompare = useCallback((id) => {
    setCompareList(prev => prev.filter(p => p.property_id !== id));
  }, []);

  /* ── Select an event → fetch data, fly globe ─────────────────── */
  const handleEventSelect = useCallback(async (eventId) => {
    if (eventId === selectedEvent) return;

    setLoading(true);
    setSelectedProperty(null);
    setProperties([]);
    setStats(null);
    setTileUrl(null);
    setSelectedEvent(eventId);
    setPortfolioAnalyzed(false);
    setStormTrack(null);

    const evt = events.find(e => e.id === eventId);
    if (evt) setFlyTarget({ center: [evt.lon, evt.lat], zoom: evt.zoom || 10 });

    // NHC best-track overlay (only exists for hurricane events; 404 = none).
    api.getStormTrack(eventId).then(setStormTrack).catch(() => setStormTrack(null));

    try {
      const [propsData, tilesData] = await Promise.all([
        api.getProperties(eventId),
        api.getTiles(eventId),
      ]);
      setProperties(propsData.properties || []);
      setStats(propsData.stats || null);
      setTileUrl(tilesData.tile_url || null);
    } catch (err) {
      console.error('Failed to load event data:', err);
    } finally {
      setLoading(false);
    }
  }, [selectedEvent, events]);

  /* ── Portfolio uploaded successfully ─────────────────────────── */
  const handlePortfolioSuccess = useCallback((data, settings = null) => {
    setPortfolioId(data.portfolio_id);
    setPortfolioProps(data.properties || []);
    setPortfolioAnalyzed(false);
    setLiveEventDate(null);
    setLiveMeta(null);
    setSavedSettings(settings || null);
    setShowUpload(false);
    setActivePanel('analysis');   // land the user on the Run-Analysis step
    refreshPortfolios();

    const bounds = computeBounds(data.properties || []);
    if (bounds) {
      setFlyTarget({ bounds });
    } else if (data.center) {
      setFlyTarget({ center: [data.center.lon, data.center.lat], zoom: 11 });
    }

    if (settings) {
      api.savePortfolioSettings(data.portfolio_id, {
        event_date: settings.eventDate,
        pre_days:   settings.preDays,
        post_days:  settings.postDays,
      }).catch(() => {});
      if (settings.runNow && settings.eventDate) {
        runLiveAnalysis(data.portfolio_id, settings.eventDate,
                        settings.preDays, settings.postDays);
      }
    }
  }, [refreshPortfolios]);  // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Select a saved portfolio from the sidebar ───────────────── */
  const handlePortfolioSelect = useCallback(async (id) => {
    if (id === portfolioId) return;
    setLoading(true);
    try {
      const data = await api.getPortfolio(id);
      const props = data.properties || [];
      setPortfolioId(id);
      setPortfolioProps(props);
      setPortfolioAnalyzed(false);
      setLiveEventDate(null);
      setLiveMeta(null);
      setSavedSettings(null);

      const bounds = computeBounds(props);
      if (bounds) setFlyTarget({ bounds });

      // Saved analysis settings prefill the run controls.
      api.getPortfolioSettings(id)
        .then(s => { if (s?.event_date) setSavedSettings({
          eventDate: s.event_date, preDays: s.pre_days, postDays: s.post_days }); })
        .catch(() => {});

      // Reload stored live results so a saved analysis isn't shown as
      // "not analyzed" after a restart (metrics were silently lost before).
      try {
        const saved = await api.getResults(id, 'live');
        if (saved?.results?.some(r => r.impact_class)) {
          setPortfolioProps(saved.results);
          setPortfolioAnalyzed(true);
          setLiveMeta(saved.meta || null);
          if (saved.meta?.windows?.post_start) {
            setLiveEventDate(saved.meta.windows.post_start);
          }
        }
      } catch { /* no saved analysis — fine */ }
    } catch (err) {
      console.error('Failed to load portfolio:', err);
    } finally {
      setLoading(false);
    }
  }, [portfolioId]);

  /* ── Analyze active portfolio against the active event ───────── */
  const handleAnalyzePortfolio = useCallback(async () => {
    if (!portfolioId || !selectedEvent) return;
    setAnalyzing(true);
    try {
      const data = await api.analyzePortfolio(portfolioId, selectedEvent);
      setPortfolioProps(data.results || []);
      setPortfolioAnalyzed(true);
    } catch (err) {
      console.error('Portfolio analysis failed:', err);
    } finally {
      setAnalyzing(false);
    }
  }, [portfolioId, selectedEvent]);

  /* ── Run live, global satellite analysis on the active portfolio ── */
  const runLiveAnalysis = useCallback(async (pid, eventDate, preDays, postDays, bboxFilter) => {
    if (!pid || !eventDate) return;
    setLiveAnalyzing(true);
    try {
      const payload = {
        event_date: eventDate,
        label: `${pid} · ${eventDate}`,
      };
      if (preDays)  payload.pre_days  = preDays;
      if (postDays) payload.post_days = postDays;
      if (bboxFilter) payload.bbox_filter = bboxFilter;

      const data = await api.analyzeLive(pid, payload);
      setPortfolioProps(data.results || []);
      setPortfolioAnalyzed(true);
      setLiveMeta(data.meta || null);
      setLiveEventDate(eventDate);
      setSavedSettings({ eventDate, preDays, postDays });
      setLiveError('');
      const bounds = computeBounds(data.results || []);
      if (bounds) setFlyTarget({ bounds });
    } catch (err) {
      console.error('Live analysis failed:', err);
      setLiveError(err?.detail || String(err?.message || err) ||
                   'Live analysis failed. Check the backend terminal.');
    } finally {
      setLiveAnalyzing(false);
    }
  }, []);

  const handleAnalyzeLive = useCallback((eventDate, opts = {}) => {
    return runLiveAnalysis(portfolioId, eventDate,
                           opts.preDays, opts.postDays, opts.bboxFilter);
  }, [portfolioId, runLiveAnalysis]);

  const drawerOpen = selectedProperty !== null;
  const activePortfolio = portfolios.find(p => p.id === portfolioId);
  const selectedEventMeta = events.find(e => e.id === selectedEvent);

  /* ── Fast PIF zone summary: pure bbox math, refreshes the moment a
     portfolio is loaded or the event (and thus the zone bbox) changes. ── */
  useEffect(() => {
    if (!portfolioId || portfolioProps.length === 0) {
      setZoneSummary(null);
      return;
    }
    api.zoneSummary(portfolioId, selectedEventMeta?.bbox || null)
      .then(setZoneSummary)
      .catch(() => setZoneSummary(null));
  }, [portfolioId, selectedEventMeta?.bbox, portfolioProps.length]);

  /* ── Open the claims data grid for a given dataset ───────────── */
  const openGrid = useCallback((kind) => {
    const which = kind || (properties.length ? 'event' : 'portfolio');
    if (which === 'portfolio' && portfolioProps.length) {
      setGrid({ kind: 'portfolio', rows: portfolioProps,
                title: `${activePortfolio?.id || 'Portfolio'} · ${portfolioProps.length} properties` });
    } else if (properties.length) {
      setGrid({ kind: 'event', rows: properties,
                title: `${selectedEventMeta?.label || 'Event'} · ${properties.length} properties` });
    }
  }, [properties, portfolioProps, activePortfolio, selectedEventMeta]);

  const canOpenGrid = properties.length > 0 || portfolioProps.length > 0;

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>

      <Globe
        properties={properties}
        portfolioProperties={portfolioProps}
        selectedProperty={selectedProperty}
        onPropertySelect={setSelectedProperty}
        tileUrl={tileUrl}
        timeMode={timeMode}
        flyTarget={flyTarget}
        dimmed={drawerOpen}
        leftInset={leftInset}
        stormTrack={stormTrack}
        zoneBbox={zoneSummary?.zone_source === 'event' ? zoneSummary.bbox : null}
      />

      <Header
        selectedEvent={selectedEventMeta}
        onUploadClick={() => setShowUpload(true)}
        onGridClick={canOpenGrid ? () => openGrid() : null}
        loading={loading}
      />

      {selectedEvent && (
        <TimeSlider timeMode={timeMode} onTimeChange={setTimeMode} hasTile={!!tileUrl} />
      )}

      {(stats || liveMeta?.exposure) && (
        <KPIBar stats={stats} exposure={liveMeta?.exposure} leftInset={leftInset} />
      )}

      <Sidebar activePanel={activePanel} onSetPanel={setActivePanel} compareCount={compareList.length}>
        {activePanel === 'events' && (
          <EventsPanel
            events={events}
            selectedEvent={selectedEvent}
            onSelect={handleEventSelect}
            loading={loading}
          />
        )}
        {activePanel === 'portfolios' && (
          <PortfoliosPanel
            portfolios={portfolios}
            activePortfolioId={portfolioId}
            onSelect={handlePortfolioSelect}
            onUploadClick={() => setShowUpload(true)}
            loading={loading}
          />
        )}
        {activePanel === 'dispatch' && (
          <DispatchQueuePanel
            eventId={selectedEvent}
            eventLabel={selectedEventMeta?.label}
            eventProperties={properties}
            portfolioProps={portfolioProps}
            portfolioAnalyzed={portfolioAnalyzed}
            portfolioLabel={activePortfolio?.id}
            onSelectProperty={setSelectedProperty}
            onOpenGrid={openGrid}
          />
        )}
        {activePanel === 'analysis' && (
          <AnalysisPanel
            eventProperties={properties}
            eventLabel={selectedEventMeta?.label}
            portfolioProperties={portfolioProps}
            portfolioLabel={portfolioId}
            portfolioAnalyzed={portfolioAnalyzed}
            onSelectProperty={setSelectedProperty}
            onAddToCompare={addToCompare}
            compareIds={compareIds}
            compareFull={compareFull}
            onAnalyzePortfolio={handleAnalyzePortfolio}
            analyzing={analyzing}
            onAnalyzeLive={handleAnalyzeLive}
            liveAnalyzing={liveAnalyzing}
            geeLive={geeLive}
            liveMeta={liveMeta}
            portfolioId={portfolioId}
            savedSettings={savedSettings}
            zoneSummary={zoneSummary}
            liveError={liveError}
            onDismissError={() => setLiveError('')}
          />
        )}
        {activePanel === 'compare' && (
          <SarComparePanel
            compareList={compareList}
            onRemove={removeFromCompare}
            onClear={() => setCompareList([])}
          />
        )}
        {activePanel === 'reports' && (
          <ReportsPanel
            events={events}
            eventLabel={selectedEventMeta?.label}
            eventProperties={properties}
            portfolioId={portfolioId}
            portfolioLabel={activePortfolio?.id}
            portfolioProperties={portfolioProps}
          />
        )}
        {activePanel === 'operations' && (
          <OperationsPanel selectedEventMeta={selectedEventMeta} />
        )}
      </Sidebar>

      <PropertyDrawer
        property={selectedProperty}
        eventId={selectedEvent}
        liveEventDate={liveEventDate}
        eventLabel={liveMeta?.label || selectedEventMeta?.label}
        durationSlices={liveMeta?.duration_slices}
        onClose={() => setSelectedProperty(null)}
        onAddToCompare={addToCompare}
        isInCompare={selectedProperty ? compareIds.has(selectedProperty.property_id) : false}
        compareFull={compareFull}
      />

      {showUpload && (
        <UploadModal
          events={events}
          onClose={() => setShowUpload(false)}
          onSuccess={handlePortfolioSuccess}
        />
      )}

      {grid && (
        <DataGrid
          title={grid.title}
          rows={grid.rows}
          kind={grid.kind}
          eventLabel={liveMeta?.label || selectedEventMeta?.label}
          eventDate={liveEventDate || selectedEventMeta?.event_date}
          onClose={() => setGrid(null)}
          onRowClick={(p) => { setSelectedProperty(p); setGrid(null); }}
        />
      )}

      {!grid && (
        <ChatBar
          eventMeta={selectedEventMeta}
          eventStats={stats}
          selectedProperty={selectedProperty}
          portfolioId={portfolioAnalyzed ? portfolioId : null}
          panelOpen={!!activePanel}
        />
      )}

    </div>
  );
}
