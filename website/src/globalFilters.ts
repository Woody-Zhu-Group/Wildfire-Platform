import type { PanelId } from './PanelPicker';
import { workspaceYears } from './coverage.ts';
import type { Filters } from './data.ts';
import type { PanelSettings } from './state';

export type FilterMode = 'inherit' | 'override';

export interface GlobalFilters {
  year: number;
}

export const DEFAULT_GLOBAL_YEAR = 2024;
export const DEFAULT_GLOBAL_FILTERS: GlobalFilters = { year: DEFAULT_GLOBAL_YEAR };
// Years in which some dataset has measured rows (shared/dataset_coverage.json);
// a panel whose own dataset has no rows in the chosen year says so.
export const WORKSPACE_YEARS: readonly number[] = workspaceYears();
export const GLOBAL_FILTERS_STORAGE_KEY = 'wildfire-workspace-global-v1';

export function yearWindow(year: number): Pick<Filters, 'start' | 'end'> {
  return { start: `${year}-01-01`, end: `${year}-12-31` };
}

export function yearFromFilters(filters: Filters): number {
  return Number(filters.start.slice(0, 4));
}

export function panelUsesGlobalYear(type: PanelId, settings: PanelSettings): boolean {
  if (settings.answerStat) return false;
  // The model evaluation is one fixed held-out year; the workspace year does not apply.
  if (type === 'stat_card' && settings.statMode === 'model_metrics') return false;
  if (type === 'time_series' && (settings.seriesMode === 'yearly' || settings.seriesMode === 'seasonal')) return false;
  return true;
}

export function inheritsGlobalYear(type: PanelId, settings: PanelSettings): boolean {
  return panelUsesGlobalYear(type, settings) && settings.filterMode !== 'override';
}

export function inheritedFilters(filters: Filters, global: GlobalFilters): Filters {
  return { ...filters, ...yearWindow(global.year) };
}

export function effectiveFilters(type: PanelId, settings: PanelSettings, global: GlobalFilters): Filters {
  return inheritsGlobalYear(type, settings) ? inheritedFilters(settings.filters, global) : settings.filters;
}

export function effectiveSettings(type: PanelId, settings: PanelSettings, global: GlobalFilters): PanelSettings {
  if (!inheritsGlobalYear(type, settings)) return settings;
  return { ...settings, filters: inheritedFilters(settings.filters, global) };
}

export function datesDivergeFromGlobal(filters: Filters, year: number): boolean {
  const window = yearWindow(year);
  return filters.start !== window.start || filters.end !== window.end;
}

export function applySettingsPatch(
  type: PanelId,
  settings: PanelSettings,
  patch: Partial<PanelSettings>,
  global: GlobalFilters,
): Partial<PanelSettings> {
  if (!inheritsGlobalYear(type, settings) || !patch.filters || !datesDivergeFromGlobal(patch.filters, global.year)) {
    return patch;
  }
  return { ...patch, filterMode: 'override' };
}

export function pinToYear(settings: PanelSettings, global: GlobalFilters): Partial<PanelSettings> {
  return { filterMode: 'override', filters: inheritedFilters(settings.filters, global) };
}

export function followWorkspaceYear(): Partial<PanelSettings> {
  return { filterMode: 'inherit' };
}

export function yearOverrideMarker(type: PanelId, settings: PanelSettings): string | null {
  if (!panelUsesGlobalYear(type, settings) || settings.filterMode !== 'override') return null;
  const startYear = yearFromFilters(settings.filters);
  const endYear = Number(settings.filters.end.slice(0, 4));
  return startYear === endYear ? `Pinned: ${startYear}` : `Pinned: ${startYear}–${endYear}`;
}

export function parseStoredGlobalFilters(raw: string | null): GlobalFilters {
  if (!raw) return { ...DEFAULT_GLOBAL_FILTERS };
  try {
    const stored = JSON.parse(raw) as { year?: unknown };
    if (Number.isInteger(stored.year) && (WORKSPACE_YEARS as readonly number[]).includes(stored.year as number)) {
      return { year: stored.year as number };
    }
  } catch { /* Storage is optional; unavailable or old state uses the default year. */ }
  return { ...DEFAULT_GLOBAL_FILTERS };
}
