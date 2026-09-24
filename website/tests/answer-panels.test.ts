import test from 'node:test';
import assert from 'node:assert/strict';
import { panelsFromAnswer, unsupportedViewNotice } from '../src/answerPanels.ts';
import type { AgentAnswer } from '../src/api.ts';

const answer = (views: NonNullable<AgentAnswer['views']>): AgentAnswer => ({status: 'ok', answer_text: 'Recorded results.', views});

test('record-table specs use the dataset names emitted by the agent planner', () => {
  const datasets = {cpuc_ignitions: 'cpuc', epss_outages: 'epss', calfire_incidents: 'calfire', psps_events: 'psps', us_ignitions: 'us_ignitions'};
  for (const [source, expected] of Object.entries(datasets)) {
    const result = panelsFromAnswer(answer([{type: 'record_table', params: {dataset: source, year: 2024, row_limit: 25}}]));
    assert.equal(result.length, 1, source);
    assert.equal(result[0].type, 'record_table');
    assert.equal(result[0].settings.dataset, expected);
    assert.deepEqual(result[0].settings.filters, {start: '2024-01-01', end: '2024-12-31', utility: '', county: ''});
  }
});

test('map and series specs preserve exact dates, filters, overlays and interval', () => {
  const result = panelsFromAnswer(answer([
    {type: 'map', params: {datasets: ['ignitions'], start_date: '2023-03-15', end_date: '2024-07-31', utility: 'PGE', county: 'Marin', show_hftd: true, show_territory: true}},
    {type: 'time_series', params: {dataset: 'calfire', year: 2024, interval: 'weekly', incident_type_mode: 'wildfire_default'}},
  ]));
  assert.equal(result.length, 2);
  assert.deepEqual(result[0].settings.filters, {start: '2023-03-15', end: '2024-07-31', utility: 'PG&E', county: 'Marin'});
  assert.equal(result[0].settings.filterMode, 'override');
  assert.deepEqual(result[0].settings.overlays, ['hftd', 'territories']);
  assert.deepEqual(result[0].settings.datasets, ['cpuc']);
  assert.equal(result[1].settings.interval, 'weekly');
  assert.deepEqual(result[1].settings.datasets, ['calfire']);
});

test('summary stat mode opens the live summary panel and plain counts stay frozen', () => {
  const summary = panelsFromAnswer(answer([{
    type: 'stat_card',
    evidence_ids: ['ev_summary'],
    artifact_refs: [],
    params: {
      kind: 'count', source_dataset: 'epss_outages', value: 40, label: 'Summary',
      scope: 'statewide', period: '2024', unit: 'events', stat_mode: 'summary',
      view_id: 'summary-stats', year: 2024,
    },
  }]));
  assert.equal(summary.length, 1);
  assert.equal(summary[0].settings.statMode, 'summary');
  assert.equal(summary[0].settings.dataset, 'epss');
  assert.equal(summary[0].settings.answerStat, undefined);
  assert.equal(summary[0].name, 'Summary metrics');
  const frozen = panelsFromAnswer(answer([{
    type: 'stat_card',
    params: {kind: 'count', source_dataset: 'cpuc_ignitions', value: 532, label: 'Events', scope: 'PGE', period: '2024', unit: 'events'},
  }]));
  assert.equal(frozen[0].settings.statMode, undefined);
  assert.equal(frozen[0].settings.answerStat?.value, 532);
  const medical = panelsFromAnswer(answer([{
    type: 'stat_card',
    evidence_ids: ['ev_epss'],
    params: {
      kind: 'count', source_dataset: 'epss_outages', value: 88, label: 'EPSS outages',
      scope: 'statewide', period: '2024', unit: 'events', stat_mode: 'medical_exposure', year: 2024,
    },
  }]));
  assert.equal(medical[0].settings.statMode, 'medical_exposure');
  const bare = {type: 'stat_card' as const, params: {kind: 'count' as const, source_dataset: 'epss_outages', value: 1, label: 'Summary', scope: 'statewide', period: '2024', unit: 'events' as const, stat_mode: 'summary' as const, year: 2024}};
  assert.deepEqual(panelsFromAnswer(answer([bare])), []);
});

test('medical exposure stat cards open the EPSS panel instead of a frozen number', () => {
  const result = panelsFromAnswer(answer([{
    type: 'stat_card',
    params: {
      kind: 'count', source_dataset: 'epss_outages', value: 88, label: 'EPSS outages',
      scope: 'statewide', period: '2024', unit: 'events', stat_mode: 'medical_exposure',
      view_id: 'medical-exposure', year: 2024,
    },
    evidence_ids: ['ev_epss'],
    artifact_refs: [],
  }]));
  assert.equal(result.length, 1);
  assert.equal(result[0].type, 'stat_card');
  assert.equal(result[0].settings.statMode, 'medical_exposure');
  assert.equal(result[0].settings.dataset, 'epss');
  assert.equal(result[0].settings.filterMode, 'override');
  assert.deepEqual(result[0].settings.filters, {start: '2024-01-01', end: '2024-12-31', utility: '', county: ''});
  assert.equal(result[0].settings.answerStat, undefined);
});

test('stat specs retain source values, including zero counts and risk probabilities', () => {
  const result = panelsFromAnswer(answer([
    {type: 'stat_card', params: {kind: 'count', source_dataset: 'cpuc_ignitions', value: 0, label: 'Events', scope: 'Marin', period: '2024', unit: 'events'}},
    {type: 'stat_card', params: {kind: 'risk', source_dataset: 'cnhpp', value: 0.12, label: 'P(≥1 ignition)', scope: 'cell 20', period: '2024-06-01', unit: 'risk'}},
  ]));
  assert.equal(result[0].settings.answerStat?.value, 0);
  assert.equal(result[0].settings.answerStat?.sourceDataset, 'cpuc_ignitions');
  assert.deepEqual(result[1].settings.answerStat, {value: 0.12, label: 'P(≥1 ignition)', scope: 'cell 20', period: '2024-06-01', unit: 'risk', sourceDataset: 'cnhpp'});
});

test('a count the dataset does not cover opens as not covered, never as zero', () => {
  const reason = 'EPSS is PG&E-only in this warehouse, so there are no SCE rows in EPSS outages: that count would be absent, not zero.';
  const result = panelsFromAnswer(answer([
    {type: 'stat_card', params: {kind: 'spatial_metric', source_dataset: 'epss_outages', value: null, not_covered_reason: reason, label: 'EPSS outages', scope: 'SCE territory', period: '2024', unit: 'events'}},
    // No value and no reason is malformed; it is dropped rather than drawn as 0.
    {type: 'stat_card', params: {kind: 'spatial_metric', source_dataset: 'epss_outages', value: null, label: 'EPSS outages', scope: 'SCE territory', period: '2024', unit: 'events'}},
  ]));
  assert.equal(result.length, 1);
  assert.equal(result[0].settings.answerStat?.value, null);
  assert.equal(result[0].settings.answerStat?.notCoveredReason, reason);
});

test('unrepresentable incident filters and missing dates never become a different query', () => {
  const result = panelsFromAnswer(answer([
    {type: 'time_series', params: {dataset: 'calfire', year: 2024, incident_type_mode: 'all'}},
    {type: 'record_table', params: {dataset: 'cpuc_ignitions'}},
  ]));
  assert.deepEqual(result, []);
});

test('answer adaptation does not mutate the service payload or share panel settings', () => {
  const response = answer([{type: 'map', params: {datasets: ['ignitions'], year: 2024, show_hftd: true}}]);
  const before = structuredClone(response);
  const first = panelsFromAnswer(response);
  first[0].settings.overlays!.push('territories');
  first[0].settings.filters!.county = 'Marin';
  assert.deepEqual(response, before);
  assert.deepEqual(panelsFromAnswer(response)[0].settings.overlays, ['hftd']);
  assert.equal(panelsFromAnswer(response)[0].settings.filters?.county, '');
});

function ranking(groupBy: 'county' | 'utility' | 'cause', dataset: string) {
  return {
    type: 'comparison' as const,
    evidence_ids: ['ev_rank'],
    artifact_refs: [] as string[],
    params: {
      kind: 'ranking' as const,
      metric: 'ignition_count',
      dataset,
      group_by: groupBy,
      year: 2024,
      start_date: '2024-01-01',
      end_date: '2024-12-31',
    },
  };
}

function series(mode: 'yearly' | 'seasonal' | 'cumulative_acres' | 'customer_events' | 'regional', dataset: string) {
  return {
    type: 'time_series' as const,
    evidence_ids: ['ev_series'],
    artifact_refs: [] as string[],
    params: {dataset, year: 2024, interval: 'monthly' as const, series_mode: mode},
  };
}

test('series modes open the matching panel and a plain trend stays a timeline', () => {
  const cases = [
    {mode: 'yearly' as const, dataset: 'ignitions', id: 'cpuc', name: 'Year comparison'},
    {mode: 'seasonal' as const, dataset: 'calfire', id: 'calfire', name: 'Seasonal profile'},
    {mode: 'cumulative_acres' as const, dataset: 'calfire', id: 'calfire', name: 'Cumulative acres burned within a season'},
    {mode: 'customer_events' as const, dataset: 'psps', id: 'psps', name: 'Customers affected over time'},
    {mode: 'regional' as const, dataset: 'epss', id: 'epss', name: 'Regional trends'},
  ];
  for (const item of cases) {
    const result = panelsFromAnswer(answer([series(item.mode, item.dataset)]));
    assert.equal(result.length, 1, item.mode);
    assert.equal(result[0].type, 'time_series');
    assert.equal(result[0].name, item.name);
    assert.equal(result[0].settings.seriesMode, item.mode);
    assert.equal(result[0].settings.dataset, item.id);
    assert.deepEqual(result[0].settings.filters, {start: '2024-01-01', end: '2024-12-31', utility: '', county: ''});
  }
  const timeline = panelsFromAnswer(answer([{type: 'time_series', params: {dataset: 'calfire', year: 2024, interval: 'monthly'}}]));
  assert.equal(timeline.length, 1);
  assert.equal(timeline[0].settings.seriesMode, undefined);
  assert.equal(timeline[0].name, 'CAL FIRE · 2024');
  const wrong = panelsFromAnswer(answer([series('cumulative_acres', 'epss')]));
  assert.deepEqual(wrong, []);
  const ungrounded = series('regional', 'epss');
  delete (ungrounded as {evidence_ids?: string[]}).evidence_ids;
  assert.deepEqual(panelsFromAnswer(answer([ungrounded])), []);
});

test('county, utility, and cause ranks render as comparison panels', () => {
  const cases = [
    {groupBy: 'county' as const, dataset: 'cpuc_ignitions', id: 'cpuc', name: 'County ranking'},
    {groupBy: 'utility' as const, dataset: 'cpuc_ignitions', id: 'cpuc', name: 'Utility comparison'},
    {groupBy: 'cause' as const, dataset: 'epss_outages', id: 'epss', name: 'Cause breakdown'},
  ];
  for (const item of cases) {
    const response = answer([ranking(item.groupBy, item.dataset)]);
    const result = panelsFromAnswer(response);
    assert.equal(result.length, 1, item.groupBy);
    assert.equal(result[0].type, 'comparison');
    assert.equal(result[0].name, item.name);
    assert.equal(result[0].settings.dataset, item.id);
    assert.equal(result[0].settings.groupBy, item.groupBy);
    assert.equal(result[0].settings.measure, 'count');
    assert.equal(result[0].settings.filterMode, 'override');
    assert.deepEqual(result[0].settings.filters, {start: '2024-01-01', end: '2024-12-31', utility: '', county: ''});
    assert.equal(unsupportedViewNotice(response), null);
  }
});

test('a ranking without evidence, dataset, group, or dates does not invent a panel', () => {
  const bare = {type: 'comparison' as const, params: {kind: 'ranking' as const, metric: 'ignition_count', dataset: 'cpuc_ignitions', group_by: 'county', year: 2024}};
  assert.deepEqual(panelsFromAnswer(answer([bare])), []);
  assert.equal(unsupportedViewNotice(answer([bare])), 'Comparison view is not supported here yet.');
  const noGroup = ranking('county', 'cpuc_ignitions');
  delete (noGroup.params as {group_by?: string}).group_by;
  assert.deepEqual(panelsFromAnswer(answer([noGroup])), []);
});

test('period comparisons and two-utility compares stay on the notice', () => {
  const period = answer([{
    type: 'comparison',
    evidence_ids: ['ev_period'],
    params: {
      kind: 'periods', metric: 'ignition_count', scope: 'PGE',
      period_a_start: '2023-01-01', period_a_end: '2023-12-31',
      period_b_start: '2024-01-01', period_b_end: '2024-12-31',
    },
  }]);
  const utilities = answer([{
    type: 'comparison',
    evidence_ids: ['ev_utilities'],
    params: {
      kind: 'utilities', metric: 'ignition_count', utilities: ['PGE', 'SCE'],
      start_date: '2024-01-01', end_date: '2024-12-31',
    },
  }]);
  assert.deepEqual(panelsFromAnswer(period), []);
  assert.deepEqual(panelsFromAnswer(utilities), []);
  assert.equal(unsupportedViewNotice(period), 'Comparison view is not supported here yet.');
  assert.equal(unsupportedViewNotice(utilities), 'Comparison view is not supported here yet.');
});

test('unsupported Ask views receive a visible, deduplicated notice while supported views remain usable', () => {
  const response = answer([
    {type: 'comparison', params: {kind: 'utilities', metric: 'count'}},
    {type: 'comparison', params: {kind: 'ranking', metric: 'count'}},
    {type: 'spatial_context', params: {lat: 38, lon: -122}},
    {type: 'record_table', params: {dataset: 'cpuc_ignitions', year: 2024}},
  ]);
  const original = structuredClone(response);
  assert.equal(unsupportedViewNotice(response), 'Comparison and spatial context views are not supported here yet.');
  assert.equal(panelsFromAnswer(response).length, 1);
  assert.deepEqual(response, original);
  assert.equal(unsupportedViewNotice(answer([{type: 'spatial_context', params: {lat: 38, lon: -122}}])), 'Spatial context view is not supported here yet.');
  assert.equal(unsupportedViewNotice(answer([])), null);
  assert.equal(unsupportedViewNotice({status: 'error', answer_text: 'Unavailable'}), null);
});
