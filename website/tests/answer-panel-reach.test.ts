import test from 'node:test';
import assert from 'node:assert/strict';
import { panelsFromAnswer, unsupportedViewNotice } from '../src/answerPanels.ts';
import { validComparisonGrid } from '../src/comparisonGrid.ts';
import { panelUsesGlobalYear } from '../src/globalFilters.ts';
import { viewSettings } from '../src/panelViews.ts';
import type { AgentAnswer } from '../src/api.ts';
import { DEFAULT_FILTERS } from '../src/data.ts';
import { currentView, PANEL_VIEWS } from '../src/panelViews.ts';
import type { PanelId } from '../src/PanelPicker';
import type { PanelSettings } from '../src/state';

type Spec = NonNullable<AgentAnswer['views']>[number];
type ComparisonSpec = Extract<Spec, {type: 'comparison'}>;
const answer = (views: Spec[]): AgentAnswer => ({status: 'ok', answer_text: 'Recorded results.', views});

// Same defaults as newPanel in state.tsx, which App.tsx merges answer settings onto.
function applied(type: PanelId, patch: Partial<PanelSettings>): PanelSettings {
  return {
    dataset: type === 'comparison' ? 'epss' : 'cpuc', filters: {...DEFAULT_FILTERS},
    interval: 'monthly', groupBy: 'cause', measure: 'count', metric: 'events',
    datasets: ['cpuc', 'epss', 'calfire'], overlays: [], ...patch,
  };
}

function viewIds(spec: Spec): string[] {
  return panelsFromAnswer(answer([spec])).map(panel => currentView(panel.type, applied(panel.type, panel.settings)));
}

const ev = (...ids: string[]) => ({evidence_ids: ids, artifact_refs: []});

const countGrid = (): ComparisonSpec => ({type: 'comparison', ...ev('ev_pge', 'ev_sce'), params: {
  kind: 'grid', metric: 'ignition_count', dataset: 'cpuc_ignitions', grid: {
    label: 'CPUC ignitions', rows: ['PGE', 'SCE'], columns: ['2020', '2023'], cells: [
      {row: 'PGE', column: '2020', value: 510, evidence_id: 'ev_pge'},
      {row: 'PGE', column: '2023', value: 374, evidence_id: 'ev_pge'},
      {row: 'SCE', column: '2020', value: 145, evidence_id: 'ev_sce'},
      {row: 'SCE', column: '2023', value: 90, evidence_id: 'ev_sce'},
    ],
  },
}});

test('multi-entity counts reach a frozen comparison with every value and citation', () => {
  const spec = countGrid();
  const response = answer([spec]);
  const [panel] = panelsFromAnswer(response);
  assert.equal(panel.type, 'comparison');
  assert.deepEqual(panel.settings.answerComparison, spec.params.grid);
  assert.equal(unsupportedViewNotice(response), null);
  assert.equal(panelUsesGlobalYear(panel.type, applied(panel.type, panel.settings)), false);
  panel.settings.answerComparison!.cells[0].value = 1;
  assert.equal((spec.params.grid as {cells: {value: number}[]}).cells[0].value, 510);
  assert.equal(viewSettings(applied(panel.type, panel.settings), PANEL_VIEWS.find(v => v.id === 'utility-comparison')!).answerComparison, undefined);
});

test('comparison cells require cited evidence, real values and unique coordinates', () => {
  for (const mutate of [
    (s: ComparisonSpec) => { s.evidence_ids = []; },
    (s: ComparisonSpec) => { s.params.grid!.cells[0].evidence_id = 'uncited'; },
    (s: ComparisonSpec) => { s.params.grid!.cells[0].value = NaN; },
    (s: ComparisonSpec) => { s.params.grid!.cells[0].value = null; },
    (s: ComparisonSpec) => { s.params.grid!.cells.push(s.params.grid!.cells[0]); },
  ]) {
    const spec = countGrid();
    // Mutations exercise malformed incoming wire data.
    mutate(spec);
    assert.deepEqual(panelsFromAnswer(answer([spec])), []);
    assert.match(unsupportedViewNotice(answer([spec]))!, /Comparison/);
  }
});

test('county, period and null comparisons use cited cells without becoming zero', () => {
  for (const kind of ['regions', 'periods', 'utilities'] as const) {
    const spec = countGrid();
    spec.params.kind = kind;
    spec.params.grid!.cells[0] = {...spec.params.grid!.cells[0], value: null, reason: 'No coverage'};
    const [panel] = panelsFromAnswer(answer([spec]));
    assert.equal(panel.settings.answerComparison!.cells[0].value, null);
    assert.equal(panel.settings.answerComparison!.cells[0].reason, 'No coverage');
    assert.equal(validComparisonGrid(panel.settings.answerComparison), true);
  }
});
const eventMap = (dataset: string, extra: Record<string, unknown> = {}): Spec => ({type: 'map', ...ev('ev_map'), params: {datasets: [dataset], year: 2024, extent: 'statewide', ...extra}});
const series = (mode: string, dataset: string): Spec => ({type: 'time_series', ...ev('ev_series'), params: {dataset, year: 2024, interval: 'monthly', series_mode: mode as 'yearly'}});
const ranking = (groupBy: string, dataset: string): Spec => ({type: 'comparison', ...ev('ev_rank'), params: {kind: 'ranking', metric: 'ignition_count', dataset, group_by: groupBy, year: 2024, start_date: '2024-01-01', end_date: '2024-12-31'}});
const stat = (mode: 'summary' | 'medical_exposure', dataset: string): Spec => ({type: 'stat_card', ...ev('ev_stat'), params: {kind: 'count', value: 3, label: 'x', scope: 'statewide', period: '2024', source_dataset: dataset, unit: 'events', stat_mode: mode, year: 2024}});
const grid = (mode: 'risk' | 'residual', day = '2024-08-15'): Spec => ({type: 'map', ...ev('ev_risk'), params: {datasets: [], map_mode: mode, risk_date: day, extent: 'statewide'}});
const SHA = 'ee6fc19c9388c2ab23f693a0a71ef993844c1ad3793d242692657aafe08d9a57';
// The spec services/agent/views.py _model_metrics_views emits for a risk_metrics read.
const modelMetrics = (extra: Record<string, unknown> = {}): Spec => ({type: 'stat_card', ...ev('ev_metrics'), params: {kind: 'model_metrics', value: 0.7602, label: 'cNHPP AUC', scope: 'Statewide, 824 grid cells', period: 'Evaluated on 2024', source_dataset: 'cnhpp', unit: null, stat_mode: 'model_metrics', view_id: 'model-metrics', eval_year: 2024, params_sha256: SHA, ...extra}});
const timeline = (datasets: string[], evidence = datasets.map(d => `ev_${d}`)): Spec => ({type: 'time_series', ...ev(...evidence), params: {dataset: datasets[0], datasets, year: 2024, interval: 'monthly', series_mode: 'timeline'}});

test('planner-shaped specs reach all 19 workspace views', () => {
  const specs: [string, Spec][] = [
    ['events-map', eventMap('ignitions')],
    ['outages-map', eventMap('epss')],
    ['psps-map', eventMap('psps')],
    ['weather-map', eventMap('ignitions', {show_hdw: true})],
    ['risk-surface-map', grid('risk')],
    ['residual-map', grid('residual')],
    ['events-time', timeline(['ignitions', 'epss', 'calfire'])],
    ['annual-time', series('yearly', 'ignitions')],
    ['regional-time', series('regional', 'epss')],
    ['seasonal-time', series('seasonal', 'calfire')],
    ['cumulative-acres', series('cumulative_acres', 'calfire')],
    ['customer-events', series('customer_events', 'psps')],
    ['county-comparison', ranking('county', 'cpuc_ignitions')],
    ['utility-comparison', ranking('utility', 'cpuc_ignitions')],
    ['cause-comparison', ranking('cause', 'epss_outages')],
    ['event-records', {type: 'record_table', ...ev('ev_rows'), params: {dataset: 'cpuc_ignitions', year: 2024, row_limit: 25}}],
    ['summary-stats', stat('summary', 'epss_outages')],
    ['medical-exposure', stat('medical_exposure', 'epss_outages')],
    ['model-metrics', modelMetrics()],
  ];
  const reached = new Set<string>();
  for (const [expected, spec] of specs) {
    assert.deepEqual(viewIds(spec), [expected], expected);
    reached.add(expected);
  }
  assert.deepEqual([...reached].sort(), PANEL_VIEWS.map(view => view.id).sort());
});

test('risk and residual grids carry the scored day and no invented dataset or window', () => {
  for (const mode of ['risk', 'residual'] as const) {
    const [panel] = panelsFromAnswer(answer([grid(mode)]));
    assert.equal(panel.type, 'map');
    assert.equal(panel.settings.mapMode, mode);
    assert.equal(panel.settings.riskDate, '2024-08-15');
    assert.deepEqual(panel.settings.filters, {start: '2024-08-15', end: '2024-08-15', utility: '', county: ''});
    assert.equal(panel.settings.dataset, undefined);
    assert.equal(panel.name, PANEL_VIEWS.find(view => view.id === (mode === 'risk' ? 'risk-surface-map' : 'residual-map'))!.title);
  }
});

test('a grid map without evidence or a real scored day opens nothing', () => {
  const bare = grid('risk');
  delete (bare as {evidence_ids?: string[]}).evidence_ids;
  assert.deepEqual(panelsFromAnswer(answer([bare])), []);
  assert.deepEqual(panelsFromAnswer(answer([grid('risk', '2024-02-30')])), []);
  assert.deepEqual(panelsFromAnswer(answer([grid('residual', '')])), []);
  const noDay = grid('risk');
  delete (noDay.params as {risk_date?: string}).risk_date;
  assert.deepEqual(panelsFromAnswer(answer([noDay])), []);
  const withLayer = grid('risk');
  (withLayer.params as {datasets: string[]}).datasets = ['ignitions'];
  assert.deepEqual(panelsFromAnswer(answer([withLayer])), []);
});

test('HDW turns on the overlay for the grounded year only', () => {
  const [panel] = panelsFromAnswer(answer([eventMap('epss', {show_hdw: true, year: 2023})]));
  assert.deepEqual(panel.settings.overlays, ['hdw']);
  assert.equal(panel.settings.weatherYear, 2023);
  assert.equal(panel.settings.dataset, 'epss');
  assert.equal(panel.name, 'Fire weather');
  const ungrounded = eventMap('ignitions', {show_hdw: true});
  delete (ungrounded as {evidence_ids?: string[]}).evidence_ids;
  assert.deepEqual(panelsFromAnswer(answer([ungrounded])), []);
  const spansYears = eventMap('ignitions', {show_hdw: true, start_date: '2023-06-01', end_date: '2024-06-01'});
  assert.deepEqual(panelsFromAnswer(answer([spansYears])), []);
});

test('the PG&E CPUC event map stays an event map', () => {
  const [panel] = panelsFromAnswer(answer([eventMap('ignitions', {utility: 'PGE', show_territory: true, extent: 'territory'})]));
  assert.deepEqual(panel.settings.overlays, ['territories']);
  assert.equal(panel.settings.mapMode, undefined);
  assert.equal(panel.settings.weatherYear, undefined);
  assert.equal(currentView('map', applied('map', panel.settings)), 'events-map');
});

test('a timeline needs one evidence id per named chartable dataset', () => {
  const [panel] = panelsFromAnswer(answer([timeline(['epss', 'ignitions'])]));
  assert.equal(panel.settings.seriesMode, 'timeline');
  assert.deepEqual(panel.settings.datasets, ['epss', 'cpuc']);
  assert.equal(panel.name, 'Event trends');
  assert.deepEqual(panel.settings.filters, {start: '2024-01-01', end: '2024-12-31', utility: '', county: ''});
  assert.deepEqual(panelsFromAnswer(answer([timeline(['ignitions', 'epss', 'calfire'], ['ev_a', 'ev_b'])])), []);
  assert.deepEqual(panelsFromAnswer(answer([timeline(['ignitions', 'psps'])])), []);
  assert.deepEqual(panelsFromAnswer(answer([timeline(['ignitions', 'ignitions'])])), []);
  assert.deepEqual(panelsFromAnswer(answer([timeline(['ignitions'])])), []);
});

test('a single-dataset trend never grows extra datasets', () => {
  const [panel] = panelsFromAnswer(answer([{type: 'time_series', ...ev('ev_calfire'), params: {dataset: 'calfire', year: 2024, interval: 'monthly'}}]));
  assert.deepEqual(panel.settings.datasets, ['calfire']);
  assert.deepEqual(applied('time_series', panel.settings).datasets, ['calfire']);
});

test('the model metrics card carries the cited evaluation and needs evidence and a real hash', () => {
  const [panel] = panelsFromAnswer(answer([modelMetrics()]));
  assert.equal(panel.type, 'stat_card');
  assert.equal(panel.name, PANEL_VIEWS.find(view => view.id === 'model-metrics')!.title);
  assert.deepEqual(panel.settings, {statMode: 'model_metrics', metricsCitation: {evalYear: 2024, paramsSha256: SHA}});
  const bare = modelMetrics();
  delete (bare as {evidence_ids?: string[]}).evidence_ids;
  assert.deepEqual(panelsFromAnswer(answer([bare])), []);
  assert.deepEqual(panelsFromAnswer(answer([modelMetrics({params_sha256: 'abc'})])), []);
  assert.deepEqual(panelsFromAnswer(answer([modelMetrics({eval_year: '2024'})])), []);
  // Never falls through to a generic answer stat.
  assert.equal(panelsFromAnswer(answer([modelMetrics({eval_year: null})])).length, 0);
});
