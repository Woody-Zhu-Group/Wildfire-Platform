import type { AgentAnswer } from './api.ts';
import type { PanelId } from './PanelPicker';
import type { PanelSettings } from './state';
import { CHART_DATASETS, DATASETS, utilityLabel } from './data.ts';

interface AnswerPanel { type: PanelId; name: string; settings: Partial<PanelSettings> }

const SERIES_PANEL_NAMES = {
  yearly: 'Year comparison',
  seasonal: 'Seasonal profile',
  cumulative_acres: 'Cumulative acres burned within a season',
  customer_events: 'Customers affected over time',
  regional: 'Regional trends',
} as const;

const SERIES_DATASET: Record<keyof typeof SERIES_PANEL_NAMES, string | null> = {
  yearly: null,
  seasonal: null,
  cumulative_acres: 'calfire',
  customer_events: 'psps',
  regional: 'epss',
};

const RANK_PANEL_NAMES = {
  county: 'County ranking',
  utility: 'Utility comparison',
  cause: 'Cause breakdown',
} as const;

function dateWindow(params: Record<string, unknown>): {start: string; end: string} | null {
  const start = typeof params.start_date === 'string' ? params.start_date : typeof params.year === 'number' ? `${params.year}-01-01` : null;
  const end = typeof params.end_date === 'string' ? params.end_date : typeof params.year === 'number' ? `${params.year}-12-31` : null;
  return start && end ? {start, end} : null;
}

function rankingComparison(view: NonNullable<AgentAnswer['views']>[number]): AnswerPanel | null {
  if (view.type !== 'comparison') return null;
  const evidence = view.evidence_ids;
  if (!Array.isArray(evidence) || evidence.length === 0 || evidence.some(id => typeof id !== 'string' || id.length === 0)) return null;
  const params = view.params;
  if (params.kind !== 'ranking') return null;
  if (Array.isArray(params.utilities) && params.utilities.length >= 2) return null;
  if (params.period_a_start || params.period_b_start) return null;
  if (params.group_by !== 'county' && params.group_by !== 'utility' && params.group_by !== 'cause') return null;
  if (typeof params.dataset !== 'string' || params.dataset.length === 0) return null;
  const dataset = DATASETS.find(item => item.api === params.dataset || item.id === params.dataset || item.query === params.dataset);
  if (!dataset) return null;
  const dates = dateWindow(params);
  if (!dates) return null;
  const utility = Array.isArray(params.utilities) && params.utilities.length === 1 ? utilityLabel(params.utilities[0]) ?? '' : '';
  return {
    type: 'comparison',
    name: RANK_PANEL_NAMES[params.group_by],
    settings: {
      dataset: dataset.id,
      groupBy: params.group_by,
      measure: 'count',
      filterMode: 'override',
      filters: {
        start: dates.start,
        end: dates.end,
        utility,
        county: typeof params.county === 'string' ? params.county : '',
      },
    },
  };
}

// Adapt only contracts supported by the existing workspace components.
export function panelsFromAnswer(answer: AgentAnswer): AnswerPanel[] {
  const panels: AnswerPanel[] = [];
  for (const view of answer.views ?? []) {
    const p = view.params;
    if (view.type === 'stat_card' && p.stat_mode === 'summary') {
      const evidence = view.evidence_ids;
      if (!Array.isArray(evidence) || evidence.length === 0 || evidence.some(id => typeof id !== 'string' || id.length === 0)) continue;
      const start = typeof p.start_date === 'string' ? p.start_date : p.year ? `${p.year}-01-01` : null;
      const end = typeof p.end_date === 'string' ? p.end_date : p.year ? `${p.year}-12-31` : null;
      if (!start || !end || typeof p.source_dataset !== 'string') continue;
      const dataset = DATASETS.find(item => item.api === p.source_dataset || item.id === p.source_dataset || item.query === p.source_dataset);
      if (!dataset) continue;
      panels.push({
        type: 'stat_card',
        name: 'Summary metrics',
        settings: {
          dataset: dataset.id,
          datasets: [dataset.id],
          statMode: 'summary',
          filterMode: 'override',
          filters: {
            start,
            end,
            utility: utilityLabel(p.utility) ?? '',
            county: typeof p.county === 'string' ? p.county : '',
          },
        },
      });
      continue;
    }
    if (view.type === 'stat_card' && p.stat_mode === 'medical_exposure') {
      const start = typeof p.start_date === 'string' ? p.start_date : p.year ? `${p.year}-01-01` : null;
      const end = typeof p.end_date === 'string' ? p.end_date : p.year ? `${p.year}-12-31` : null;
      if (!start || !end) continue;
      panels.push({
        type: 'stat_card',
        name: 'Medical baseline and life support customers affected by EPSS outages',
        settings: {
          dataset: 'epss',
          datasets: ['epss'],
          statMode: 'medical_exposure',
          filterMode: 'override',
          filters: {
            start,
            end,
            utility: utilityLabel(p.utility) ?? '',
            county: typeof p.county === 'string' ? p.county : '',
          },
        },
      });
      continue;
    }
    if (view.type === 'stat_card' && typeof p.value === 'number' && Number.isFinite(p.value)
      && ['label', 'scope', 'period'].every(key => typeof p[key] === 'string')) {
      panels.push({type: 'stat_card', name: String(p.label), settings: {answerStat: {
        value: p.value, label: String(p.label), scope: String(p.scope), period: String(p.period),
        unit: typeof p.unit === 'string' ? p.unit : '',
        sourceDataset: typeof p.source_dataset === 'string' ? p.source_dataset : '',
      }}});
      continue;
    }
    const ranking = rankingComparison(view);
    if (view.type === 'comparison') {
      if (ranking) panels.push(ranking);
      continue;
    }
    if (!['map', 'time_series', 'record_table'].includes(view.type)
      || (p.incident_type_mode && p.incident_type_mode !== 'wildfire_default')) continue;
    const ids = view.type === 'map' ? p.datasets : [p.dataset];
    if (!Array.isArray(ids) || ids.length !== 1) continue;
    const dataset = DATASETS.find(d => d.api === ids[0] || d.id === ids[0] || d.query === ids[0]);
    const seriesMode = view.type === 'time_series' && typeof view.params.series_mode === 'string' && view.params.series_mode in SERIES_PANEL_NAMES
      ? view.params.series_mode
      : null;
    if (seriesMode) {
      const evidence = view.evidence_ids;
      if (!Array.isArray(evidence) || evidence.length === 0 || evidence.some(id => typeof id !== 'string' || id.length === 0)) continue;
    }
    if (!dataset || (view.type === 'time_series' && !seriesMode && !CHART_DATASETS.some(d => d.id === dataset.id))) continue;
    if (seriesMode) {
      const required = SERIES_DATASET[seriesMode];
      const chartable = seriesMode === 'yearly' || seriesMode === 'seasonal';
      if (required && dataset.id !== required) continue;
      if (chartable && !CHART_DATASETS.some(item => item.id === dataset.id)) continue;
    }
    const start = typeof p.start_date === 'string' ? p.start_date : p.year ? `${p.year}-01-01` : null;
    const end = typeof p.end_date === 'string' ? p.end_date : p.year ? `${p.year}-12-31` : null;
    if (!start || !end) continue;
    const settings: Partial<PanelSettings> = {
      dataset: dataset.id, datasets: [dataset.id], filterMode: 'override',
      filters: {start, end, utility: utilityLabel(p.utility) ?? '', county: typeof p.county === 'string' ? p.county : ''},
      overlays: [...(p.show_hftd ? ['hftd'] : []), ...(p.show_territory ? ['territories'] : [])],
    };
    if (['daily', 'weekly', 'monthly'].includes(String(p.interval))) settings.interval = p.interval as 'daily' | 'weekly' | 'monthly';
    if (seriesMode) {
      settings.seriesMode = seriesMode;
      if (seriesMode === 'seasonal' && typeof p.year === 'number') settings.seasonYears = [p.year];
      if (seriesMode === 'yearly' && typeof p.year === 'number') settings.comparisonYears = [p.year];
    }
    const name = seriesMode ? SERIES_PANEL_NAMES[seriesMode] : `${dataset.name} · ${start.slice(0, 4)}`;
    panels.push({type: view.type as PanelId, name, settings});
  }
  return panels;
}

export function unsupportedViewNotice(answer: AgentAnswer): string | null {
  const views = answer.views ?? [];
  const names = [
    views.some(view => view.type === 'comparison' && rankingComparison(view) === null) ? 'comparison' : '',
    views.some(view => view.type === 'spatial_context') ? 'spatial context' : '',
  ].filter(Boolean);
  if (!names.length) return null;
  const label = names.join(' and ');
  return `${label[0].toUpperCase()}${label.slice(1)} ${names.length === 1 ? 'view is' : 'views are'} not supported here yet.`;
}
