import { useContext, useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet.markercluster';
import 'leaflet/dist/leaflet.css';
import 'leaflet.markercluster/dist/MarkerCluster.css';
import { getBoundaries, getLayer, getRecords } from './api.ts';
import { configFor, filterError, unavailableReason, recordsFromFeatures, asText, type EventRecord } from './data.ts';
import { soleUtilityLabel } from './coverage.ts';
import { ChartFilters, DatasetSelect, LoadState } from './Controls';
import { useRemote } from './useRemote';
import { SelectionContext, usePanel } from './state';
import { HdwPlayer, HDW_GRID } from './HdwPlayer';
import { MapLegend, acresRadius } from './MapLegend';
import { featuresOnDate } from './weather.ts';
import { ExportActions } from './ExportActions';
import { MapEventPreview, type EventPreview } from './MapEventPreview';
import { ResidualMap } from './ResidualMap.tsx';
import { RiskSurfaceMap } from './RiskSurfaceMap.tsx';
import { PlaybackControls } from './PlaybackControls.tsx';
import { calendarDays, emptyPlaybackDay, eventMapViewMode, usePlayback, visibleEventFeatures } from './playback.ts';

export function EventMap() {
  const {settings} = usePanel();
  return settings.mapMode === 'risk' ? <RiskSurfaceMap/> : settings.mapMode === 'residual' ? <ResidualMap/> : <EventDataMap/>;
}
function EventDataMap() {
  const { settings, update, expanded } = usePanel();
  const { dataset, filters, overlays } = settings;
  const weather = overlays.includes('hdw');
  const weatherDate = weather && settings.weatherDate && settings.weatherDate >= filters.start && settings.weatherDate <= filters.end ? settings.weatherDate : null;
  const daily = !weather && eventMapViewMode(settings.mapView) === 'daily';
  const days = useMemo(() => calendarDays(filters.start, filters.end), [filters.start, filters.end]);
  const playback = usePlayback(daily ? days : [], daily ? settings.playbackDate : undefined, date => update({ playbackDate: date }));
  const scopeDate = weather ? weatherDate : daily ? playback.current ?? null : null;
  const selection = useContext(SelectionContext);
  const selectionRef = useRef(selection); selectionRef.current = selection;
  const [preview, setPreview] = useState<EventPreview | null>(null);
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const frame = useRef<HTMLDivElement>(null);
  const keepPreview = () => { if (previewTimer.current) clearTimeout(previewTimer.current); };
  const leavePreview = () => { keepPreview(); previewTimer.current = setTimeout(() => setPreview(current => current?.pinned ? current : null), 180); };
  useEffect(() => () => { if (previewTimer.current) clearTimeout(previewTimer.current); }, []);
  const [tileError, setTileError] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const [mapInstance, setMapInstance] = useState<L.Map | null>(null);
  const validation = filterError(filters) || unavailableReason(dataset, filters);
  const needOutages = dataset === 'epss' && (weather || daily);
  const result = useRemote(validation ? null : JSON.stringify(['map', dataset, filters, needOutages]), () => getLayer(dataset, filters, needOutages));
  const boundaryKinds = overlays.filter(o => o === 'hftd' || o === 'territories');
  const boundaryKey = boundaryKinds.join(',');
  const boundaries = useRemote(boundaryKey ? `boundaries:${boundaryKey}` : null, async () => (await Promise.all(boundaryKinds.map(o => getBoundaries(o as 'hftd' | 'territories')))).flat());
  const shown = useMemo(() => {
    const features = result.data?.geojson.features ?? [];
    if (weather) return weatherDate ? featuresOnDate(features, dataset, weatherDate) : [];
    return visibleEventFeatures(features, dataset, daily ? 'daily' : 'range', playback.current);
  }, [result.data, weather, weatherDate, dataset, daily, playback.current]);
  const emptyDay = emptyPlaybackDay({ view: daily ? 'daily' : 'range', loading: result.loading, error: validation || result.error, total: result.data?.meta.total ?? 0, shown: shown.length });
  useEffect(() => {
    const instance = L.map(host.current!, { preferCanvas: true, scrollWheelZoom: false }).setView([37.6, -120.8], 5);
    map.current = instance;
    setMapInstance(instance);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { className: 'base-map-tiles', attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 19 }).on('tileerror', () => setTileError(true)).addTo(instance);
    const observer = new ResizeObserver(() => instance.invalidateSize({ pan: true, animate: false })); observer.observe(host.current!);
    return () => { observer.disconnect(); instance.remove(); map.current = null; };
  }, []);
  useEffect(() => {
    const instance = map.current!;
    const coarsePointer = matchMedia('(pointer: coarse)');
    const updateGestures = () => {
      if (expanded) instance.scrollWheelZoom.enable(); else instance.scrollWheelZoom.disable();
      if (expanded || !coarsePointer.matches) instance.dragging.enable(); else instance.dragging.disable();
      if (expanded) instance.touchZoom.enable(); else instance.touchZoom.disable();
    };
    updateGestures(); coarsePointer.addEventListener('change', updateGestures);
    return () => coarsePointer.removeEventListener('change', updateGestures);
  }, [expanded]);
  useEffect(() => {
    if (weather && mapInstance) mapInstance.fitBounds(L.latLngBounds(HDW_GRID.cells.map(c => [c.lat, c.lon] as [number, number])), { padding: [16,16], animate: false });
  }, [weather, mapInstance]);
  useEffect(() => {
    const instance = map.current;
    if (!instance || weather || !daily || !result.data) return;
    instance.invalidateSize({ pan: false, animate: false });
    const bounds = L.geoJSON(result.data.geojson).getBounds();
    if (bounds.isValid()) instance.fitBounds(bounds, { padding: [24, 24], maxZoom: 10, animate: false });
  }, [daily, weather, result.data, dataset, expanded]);
  useEffect(() => {
    const instance = map.current;
    if (!instance || !boundaries.data || !boundaryKey) return;
    const layer = L.geoJSON(boundaries.data, { interactive: false, style: feature => ({ color: feature?.properties.tier ? '#d97706' : '#9ba9bb', weight: 1, opacity: .65, fillOpacity: feature?.properties.tier === 'Tier 3' ? .2 : feature?.properties.tier ? .1 : .025 }) }).addTo(instance);
    layer.bringToBack();
    return () => { instance.removeLayer(layer); };
  }, [boundaries.data, boundaryKey]);
  useEffect(() => {
    const instance = map.current; keepPreview(); setPreview(null);
    if (!instance || !result.data || validation) return;
    const config = configFor(dataset);
    const featureGroup = L.featureGroup().addTo(instance);
    const pointRenderer = dataset === 'calfire' ? L.svg() : undefined;
    const clusters = L.markerClusterGroup({ maxClusterRadius: 35, showCoverageOnHover: false, chunkedLoading: true, iconCreateFunction: cluster => L.divIcon({ className: 'event-cluster', html: `<span style="border-color:${config.color}">${cluster.getChildCount()}</span>`, iconSize: [34,34] }) });
    const points: L.Marker[] = [];
    for (const feature of shown) {
      if (!feature.geometry) continue;
      const props = feature.properties;
      const record: EventRecord = dataset === 'epss' ? {
        id: String(props.circuit_id), dataset, name: asText(props.circuit_name) ?? 'Circuit', date: asText(props.first_event) ?? '', county: null, utility: soleUtilityLabel('circuits'), cause: null, acres: null,
        geometry: feature.geometry, properties: { ...props, circuit_detail: true, scope_start: scopeDate ?? filters.start, scope_end: scopeDate ?? filters.end },
      } : recordsFromFeatures(dataset, [feature])[0];
      const show = (location: L.LatLng, pinned = false, hover = false) => {
        keepPreview();
        const reveal = () => setPreview(current => current?.pinned && !pinned ? current : { record, location: [location.lat, location.lng], pinned });
        if (hover) previewTimer.current = setTimeout(reveal, 140); else reveal();
      };
      const bindPreview = (layer: L.Layer, point?: L.LatLng) => {
        layer.on('mouseover', (event: L.LeafletMouseEvent) => show(point ?? event.latlng, false, true));
        layer.on('mouseout', leavePreview);
        layer.on('click', (event: L.LeafletMouseEvent) => show(point ?? event.latlng, true));
        if (point && (layer instanceof L.Marker || layer instanceof L.CircleMarker)) layer.on('add', () => {
          const element = layer.getElement();
          if (!element) return;
          element.setAttribute('aria-label', record.name);
          element.setAttribute('role', 'button');
          element.setAttribute('tabindex', '0');
          element.addEventListener('focus', () => show(point));
          element.addEventListener('blur', leavePreview);
          element.addEventListener('keydown', event => {
            if ((event as KeyboardEvent).key === 'Enter' || (event as KeyboardEvent).key === ' ') { event.preventDefault(); show(point, true); }
          });
        });
      };
      if (feature.geometry.type === 'Point' && dataset !== 'calfire') {
        const [lon, lat] = feature.geometry.coordinates;
        const marker = L.marker([lat, lon], { icon: L.divIcon({ className: 'event-marker', html: `<span style="background:${config.color}"></span>`, iconSize: [12,12] }) });
        bindPreview(marker, marker.getLatLng());
        points.push(marker);
      } else {
        L.geoJSON(feature, {
          style: { color: dataset === 'calfire' ? '#b91c1c' : config.color, weight: dataset === 'calfire' ? 1 : 2, fillOpacity: .35 },
          pointToLayer: (f, latlng) => L.circleMarker(latlng, { renderer: pointRenderer, radius: acresRadius(f.properties.acres_burned), color: '#b91c1c', fillColor: '#b91c1c', fillOpacity: .35, weight: 1 }),
          onEachFeature: (feature, layer) => bindPreview(layer, feature.geometry.type === 'Point' ? L.latLng(feature.geometry.coordinates[1], feature.geometry.coordinates[0]) : undefined),
        }).addTo(featureGroup);
      }
    }
    if (points.length) { clusters.addLayers(points); featureGroup.addLayer(clusters); }
    const bounds = featureGroup.getBounds();
    if (!weather && !daily && bounds.isValid()) instance.fitBounds(bounds, { padding: [24, 24], maxZoom: 10, animate: false });
    return () => { instance.removeLayer(featureGroup); if (pointRenderer && instance.hasLayer(pointRenderer)) instance.removeLayer(pointRenderer); };
  }, [result.data, shown, dataset, validation, filters.start, filters.end, weather, weatherDate, daily, scopeDate]);
  const missing = shown.filter(f => !f.geometry).length;
  const error = validation || result.error;
  return <div ref={frame} className="live-map">
    <ExportActions datasets={[dataset]} disabled={Boolean(error||result.loading||!shown.length||((weather||daily)&&!scopeDate))} rows={async()=>{
      const records=await getRecords(dataset,{...filters,start:scopeDate??filters.start,end:scopeDate??filters.end});
      return records.map(record=>({dataset:configFor(dataset).name,...record.properties}));
    }} />
    <div className="analysis-chart map-toolbar"><div className="map-filter-line"><DatasetSelect value={dataset} onChange={dataset => update({ dataset })} />
      {!weather && <div className="measure-switch" role="group" aria-label="Map view">
        <button type="button" aria-pressed={!daily} onClick={() => update({ mapView: 'range' })}>Full range</button>
        <button type="button" aria-pressed={daily} onClick={() => update({ mapView: 'daily' })}>Day by day</button>
      </div>}
      <span className="map-count">{result.data && !error ? `${shown.length.toLocaleString()} ${dataset === 'epss' ? 'circuits' : weather ? 'starts' : 'events'}` : ''}</span></div>
      <ChartFilters filters={filters} onChange={filters => update({ filters })} dataset={dataset} />
    </div>
    {weather && <HdwPlayer map={mapInstance} />}
    {daily && <PlaybackControls
      label="Event playback"
      dates={days}
      current={playback.current}
      index={playback.index}
      playing={playback.playing}
      speed={playback.speed}
      dateLabel="Event date"
      playLabel="Play events"
      pauseLabel="Pause events"
      speedLabel="Playback speed"
      scrubberLabel="Event day"
      dateOutput={playback.current ?? 'Unavailable'}
      onTogglePlay={() => {
        if (playback.index === days.length - 1 && days[0]) update({ playbackDate: days[0] });
        playback.setPlaying(playing => !playing);
      }}
      onSpeed={playback.setSpeed}
      onScrub={date => { playback.setPlaying(false); update({ playbackDate: date }); }}
      note={dataset === 'psps' ? <p className="panel-note">Day-by-day shows PSPS areas that started on the selected date, not remaining active areas.</p> : undefined}
    />}
    <div className="map-stage"><div ref={host} className="leaflet-host" aria-label="Wildfire event map" />
      {(error || result.loading || (!weather && !daily && result.data && !result.data.meta.total)) && <div className="map-status"><LoadState loading={result.loading} error={error} retry={result.error ? result.retry : undefined} /></div>}
      {emptyDay && <div className="map-empty-day" role="status">No events on this date.</div>}
      {(boundaries.error || boundaries.loading || tileError || (!emptyDay && missing > 0)) && <div className="map-warning" role="status">{boundaries.error ? <>Boundary layer unavailable. <button onClick={boundaries.retry}>Retry</button></> : boundaries.loading ? 'Loading boundaries…' : tileError ? 'Basemap unavailable; event geometry is still shown.' : `${missing} records have no geometry.`}</div>}
    </div>
    <MapLegend dataset={dataset} hftd={overlays.includes('hftd')} territories={overlays.includes('territories')} weather={weather} />
    <div className="map-layers" role="group" aria-label="Map layers">{[['hftd','HFTD','HFTD Tier 2 / 3'],['territories','IOU','IOU territories'],['hdw','HDW','HDW playback']].map(([id,label,name]) => <label key={id} title={name}><input type="checkbox" aria-label={name} checked={overlays.includes(id)} onChange={() => update({ overlays: overlays.includes(id) ? overlays.filter(o => o !== id) : [...overlays,id] })} />{label}</label>)}</div>
    {preview && mapInstance && frame.current && <MapEventPreview map={mapInstance} container={frame.current} preview={preview} onClose={() => setPreview(null)} onEnter={keepPreview} onLeave={leavePreview} onInspect={() => { setPreview(null); selectionRef.current.inspect(preview.record); }} />}
  </div>;
}
