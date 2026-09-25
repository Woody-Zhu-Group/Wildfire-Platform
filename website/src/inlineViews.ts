import type { PanelInstance } from './PanelPicker';
import { effectiveSettings, type GlobalFilters } from './globalFilters.ts';

export function isInlineVisual(panel: PanelInstance): boolean {
  return panel.type === 'map' || panel.type === 'time_series' || panel.type === 'comparison';
}

export function snapshotInlinePanel(panel: PanelInstance, global: GlobalFilters): PanelInstance {
  return { ...panel, settings: { ...effectiveSettings(panel.type, panel.settings, global), filterMode: 'override' } };
}

export function selectInlineViews(added: PanelInstance[]): PanelInstance[] {
  return added.filter(isInlineVisual).slice(0, 2);
}
