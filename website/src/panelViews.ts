import type { PanelId, PanelInstance } from './PanelPicker';
import type { PanelSettings } from './state';
import { CHART_DATASETS, DATASETS, supportedFilters, type DatasetId } from './data.ts';

export interface PanelView {
  id: string;
  type: PanelId;
  title: string;
  description: string;
  settings: Partial<PanelSettings>;
}

export const PANEL_VIEWS: PanelView[] = [
  { id: 'events-map', type: 'map', title: 'Wildfire events', description: 'Explore ignition and fire locations.', settings: { dataset: 'cpuc', overlays: [], mapMode: 'events' } },
  { id: 'outages-map', type: 'map', title: 'Outage circuits', description: 'Locate PG&E EPSS outages by circuit.', settings: { dataset: 'epss', overlays: [], mapMode: 'events' } },
  { id: 'psps-map', type: 'map', title: 'PSPS areas', description: 'Explore shutoff areas by utility.', settings: { dataset: 'psps', overlays: [], mapMode: 'events' } },
  { id: 'weather-map', type: 'map', title: 'Fire weather', description: 'Play daily HDW alongside event starts.', settings: { dataset: 'cpuc', overlays: ['hdw'], mapMode: 'events' } },
  { id: 'risk-surface-map', type: 'map', title: 'Modeled ignition risk surface', description: 'Map cNHPP risk for one historical date as a statistical hindcast.', settings: { dataset: 'cpuc', overlays: [], mapMode: 'risk' } },
  { id: 'residual-map', type: 'map', title: 'Model residual map', description: 'Compare observed CPUC ignitions to the cNHPP hindcast using the training cell assignment.', settings: { dataset: 'cpuc', overlays: [], mapMode: 'residual' } },
  { id: 'events-time', type: 'time_series', title: 'Event trends', description: 'Follow CPUC, EPSS and CAL FIRE over time.', settings: { seriesMode: 'timeline', datasets: ['cpuc', 'epss', 'calfire'] } },
  { id: 'annual-time', type: 'time_series', title: 'Year comparison', description: 'Compare years on the same calendar axis.', settings: { dataset: 'cpuc', seriesMode: 'yearly' } },
  { id: 'regional-time', type: 'time_series', title: 'Regional trends', description: 'Compare EPSS trends across PG&E divisions.', settings: { dataset: 'epss', seriesMode: 'regional' } },
  { id: 'seasonal-time', type: 'time_series', title: 'Seasonal profile', description: 'Average weekly events across selected years.', settings: { dataset: 'cpuc', seriesMode: 'seasonal' } },
  { id: 'cumulative-acres', type: 'time_series', title: 'Cumulative acres burned within a season', description: 'Accumulate reported CAL FIRE acreage through the selected period.', settings: { dataset: 'calfire', seriesMode: 'cumulative_acres' } },
  { id: 'customer-events', type: 'time_series', title: 'Customers affected over time', description: 'Track PSPS customer-event totals without implying unique customers.', settings: { dataset: 'psps', seriesMode: 'customer_events' } },
  { id: 'county-comparison', type: 'comparison', title: 'County ranking', description: 'Rank counties by recorded events.', settings: { dataset: 'cpuc', groupBy: 'county' } },
  { id: 'utility-comparison', type: 'comparison', title: 'Utility comparison', description: 'Compare recorded counts across utilities.', settings: { dataset: 'cpuc', groupBy: 'utility' } },
  { id: 'cause-comparison', type: 'comparison', title: 'Cause breakdown', description: 'Compare the recorded causes of EPSS outages.', settings: { dataset: 'epss', groupBy: 'cause' } },
  { id: 'event-records', type: 'record_table', title: 'Event records', description: 'Search individual events and open their details.', settings: { dataset: 'cpuc' } },
  { id: 'summary-stats', type: 'stat_card', title: 'Summary metrics', description: 'See related totals under one set of filters.', settings: { dataset: 'cpuc', statMode: 'summary' } },
  { id: 'model-metrics', type: 'stat_card', title: 'Risk model performance', description: 'Compare HPP, NHPP and cNHPP on the held-out evaluation year.', settings: { statMode: 'model_metrics' } },
  { id: 'medical-exposure', type: 'stat_card', title: 'Medical baseline and life support customers affected by EPSS outages', description: 'Sum medical-baseline and life-support customer-events during PG&E outages.', settings: { dataset: 'epss', statMode: 'medical_exposure' } },
];

export function viewSettings(current: PanelSettings, view: PanelView): PanelSettings {
  return structuredClone({ ...current, ...view.settings, answerStat: undefined, metricsCitation: undefined, weatherDate: undefined, weatherYear: undefined, riskDate: undefined, mapView: undefined, playbackDate: undefined });
}

export function currentView(type: PanelId, settings: PanelSettings): string {
  if (type === 'map' && settings.mapMode === 'risk') return 'risk-surface-map';
  if (type === 'map' && settings.mapMode === 'residual') return 'residual-map';
  if (type === 'map') return settings.overlays.includes('hdw') ? 'weather-map' : settings.dataset === 'epss' ? 'outages-map' : settings.dataset === 'psps' ? 'psps-map' : 'events-map';
  if (type === 'time_series') return settings.seriesMode === 'regional' ? 'regional-time' : settings.seriesMode === 'seasonal' ? 'seasonal-time' : settings.seriesMode === 'cumulative_acres' ? 'cumulative-acres' : settings.seriesMode === 'customer_events' ? 'customer-events' : settings.seriesMode === 'yearly' ? 'annual-time' : 'events-time';
  if (type === 'comparison') return `${settings.groupBy}-comparison`;
  if (type === 'record_table') return 'event-records';
  if (settings.statMode === 'model_metrics') return 'model-metrics';
  return settings.statMode === 'medical_exposure' ? 'medical-exposure' : 'summary-stats';
}

export function updatePanelSettings(panel: PanelInstance, patch: Partial<PanelSettings>): PanelInstance {
  const settings = { ...panel.settings, ...patch };
  if (patch.dataset !== undefined || patch.datasets !== undefined || patch.seriesMode !== undefined) {
    settings.filters = supportedFilters(settings.filters, panelDatasets(panel.type, settings));
  }
  const automaticTitle = !panel.name || (!panel.nameIsCustom && PANEL_VIEWS.some(view => view.type === panel.type && view.title === panel.name));
  const name = automaticTitle ? PANEL_VIEWS.find(view => view.id === currentView(panel.type, settings))!.title : panel.name;
  return { ...panel, name, settings };
}

export function panelDatasets(type: PanelId, settings: PanelSettings): DatasetId[] {
  if (type === 'map' && settings.mapMode === 'risk') return [];
  if (type === 'stat_card' && settings.statMode === 'model_metrics') return [];
  if (type === 'map' && settings.mapMode === 'residual') return ['cpuc'];
  if (type === 'stat_card' && settings.answerStat) {
    const source = settings.answerStat.sourceDataset;
    const dataset = DATASETS.find(item => item.id === source || item.api === source || item.query === source);
    return dataset ? [dataset.id] : [];
  }
  if (type === 'time_series') {
    if (settings.seriesMode === 'regional') return ['epss'];
    if (settings.seriesMode === 'customer_events') return ['psps'];
    if (!settings.seriesMode || settings.seriesMode === 'timeline') return [...settings.datasets];
    return [CHART_DATASETS.some(dataset => dataset.id === settings.dataset) ? settings.dataset : 'cpuc'];
  }
  return [settings.dataset];
}
