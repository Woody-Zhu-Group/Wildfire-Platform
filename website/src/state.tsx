import { createContext, useContext } from 'react';
import { DEFAULT_FILTERS, type DatasetId, type EventRecord, type Filters, type GroupBy, type Interval } from './data.ts';
import type { PanelId, PanelInstance } from './PanelPicker';

export interface PanelSettings {
  dataset: DatasetId; filters: Filters; interval: Interval; groupBy: GroupBy;
  measure: 'count' | 'share'; metric: 'events' | 'acres' | 'counties' | 'customers';
  datasets: DatasetId[]; overlays: string[];
  filterMode?: 'inherit' | 'override';
  mapMode?: 'events' | 'risk' | 'residual';
  mapView?: 'range' | 'daily';
  playbackDate?: string;
  riskDate?: string;
  statMode?: 'summary' | 'medical_exposure' | 'model_metrics';
  /** The evaluation a chat answer cited when it opened the model metrics card. */
  metricsCitation?: { evalYear: number; paramsSha256: string };
  weatherYear?: number; weatherDate?: string;
  seriesMode?: 'timeline' | 'yearly' | 'regional' | 'seasonal' | 'cumulative_acres' | 'customer_events'; comparisonYears?: number[]; seasonYears?: number[];
  answerStat?: { value: number | null; label: string; scope: string; period: string; unit: string; sourceDataset?: string; unavailableReason?: string };
}
export function newPanel(id: number, type: PanelId): PanelInstance {
  return { id, type, settings: {
    dataset: type === 'comparison' ? 'epss' : 'cpuc', filters: { ...DEFAULT_FILTERS },
    interval: 'monthly', groupBy: 'cause', measure: 'count', metric: 'events',
    datasets: ['cpuc', 'epss', 'calfire'], overlays: [],
  } };
}
export const SelectionContext = createContext<{ inspect: (record: EventRecord) => void }>({ inspect: () => {} });
export const GlobalFiltersContext = createContext<{ filters: { year: number }; setYear: (year: number) => void } | null>(null);
export function useGlobalFilters() {
  const global = useContext(GlobalFiltersContext);
  if (!global) throw new Error('Global filters missing');
  return global;
}
export const PanelContext = createContext<{ settings: PanelSettings; update: (patch: Partial<PanelSettings>) => void; expanded: boolean; expand: () => void; actionsHost: HTMLElement | null; title: string } | null>(null);
export function usePanel() {
  const panel = useContext(PanelContext);
  if (!panel) throw new Error('Panel context missing');
  return panel;
}
