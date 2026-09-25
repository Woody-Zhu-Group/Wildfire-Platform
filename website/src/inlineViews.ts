import type { PanelInstance } from './PanelPicker';
import { effectiveSettings, type GlobalFilters } from './globalFilters.ts';

export interface InlineAnswerView { panel: PanelInstance; updated: boolean }

export function isInlineVisual(panel: PanelInstance): boolean {
  return panel.type === 'map' || panel.type === 'time_series' || panel.type === 'comparison';
}

export function snapshotInlinePanel(panel: PanelInstance, global: GlobalFilters): PanelInstance {
  return { ...panel, settings: { ...effectiveSettings(panel.type, panel.settings, global), filterMode: 'override' } };
}

export function initialInlineViews(panels: PanelInstance[], global: GlobalFilters): PanelInstance[] {
  const visuals = panels.filter(isInlineVisual);
  const first = visuals.find(panel => panel.type === 'map') ?? visuals[0];
  if (!first) return [];
  const second = visuals.find(panel => panel !== first && panel.type !== first.type) ?? visuals.find(panel => panel !== first);
  return [first, second].filter((panel): panel is PanelInstance => Boolean(panel)).map(panel => snapshotInlinePanel(panel, global));
}

export function selectInlineViews(previous: PanelInstance[], added: PanelInstance[]): InlineAnswerView[] {
  const updates = added.filter(isInlineVisual).slice(0, 2);
  if (updates.length === 2) return updates.map(panel => ({ panel, updated: true }));
  const focused = previous.slice(0, 2);
  if (updates.length === 1) {
    const panel = updates[0];
    const matching = focused.findIndex(current => current.type === panel.type);
    if (matching >= 0) focused[matching] = panel;
    else if (focused.length < 2) focused.push(panel);
    else focused[panel.type === 'map' ? 0 : 1] = panel;
  }
  return focused.map(panel => ({ panel, updated: updates.some(update => update.id === panel.id) }));
}
