import test from 'node:test';
import assert from 'node:assert/strict';
import { getModelMetrics } from '../src/api.ts';
import { panelUsesGlobalYear } from '../src/globalFilters.ts';
import { METRIC_ROWS, metricCell, MetricsUnavailableError, TIE_CAVEAT, trainingWindow, validateModelMetrics } from '../src/modelMetrics.ts';
import { currentView, panelDatasets, PANEL_VIEWS, viewSettings } from '../src/panelViews.ts';
import { DEFAULT_FILTERS } from '../src/data.ts';
import type { PanelSettings } from '../src/state';

const SHA = 'ee6fc19c9388c2ab23f693a0a71ef993844c1ad3793d242692657aafe08d9a57';
const HPP_REASON = 'every cell has the same intensity on every day, so top-k precision and lift would rank an arbitrary slice of cells';

// The shape GET /metrics returns, with the committed metrics_table.csv values.
function payload() {
  return {
    model: 'cNHPP', log_likelihood: -4873.631516436059, top5_precision: 0.004858614323195024,
    top1_precision: 0.0034860557768924302, lift_top5: 1.9774363590370423, auc: 0.7602070943760917,
    not_applicable_reason: null, xi: 0.2, train_years: [2020, 2021, 2022, 2023], eval_year: 2024,
    eval_start: '2024-01-01', eval_end: '2024-12-31', params_sha256: SHA,
    baselines: [
      {model: 'HPP', log_likelihood: -5204.190183871407, top5_precision: null, top1_precision: null, lift_top5: null, auc: 0.5, not_applicable_reason: HPP_REASON},
      {model: 'NHPP', log_likelihood: -4877.6157062377415, top5_precision: 0.005344475755514527, top1_precision: 0.00298804780876494, lift_top5: 2.1751799949407467, auc: 0.7587998731448378, not_applicable_reason: null},
    ],
  };
}

const settings = (patch: Partial<PanelSettings> = {}): PanelSettings => ({
  dataset: 'cpuc', filters: {...DEFAULT_FILTERS}, interval: 'monthly', groupBy: 'cause', measure: 'count', metric: 'events',
  datasets: ['cpuc'], overlays: [], ...patch,
});

test('metrics put HPP, NHPP and cNHPP side by side with the evaluation window', () => {
  const metrics = validateModelMetrics(payload());
  assert.deepEqual(metrics.models.map(model => model.model), ['HPP', 'NHPP', 'cNHPP']);
  assert.equal(trainingWindow(metrics), '2020 to 2023');
  assert.equal(metrics.eval_year, 2024);
  assert.equal(metrics.xi, 0.2);
  assert.equal(metrics.params_sha256, SHA);
  assert.deepEqual(METRIC_ROWS.map(row => row.key), ['log_likelihood', 'auc', 'top5_precision', 'top1_precision', 'lift_top5']);
  const cnhpp = metrics.models[2];
  assert.deepEqual(METRIC_ROWS.map(row => metricCell(cnhpp, row.key).text), ['-4,873.6', '0.760', '0.49%', '0.35%', '1.98']);
});

test('HPP ranking values show as not applicable with the reason; its log-likelihood and AUC stand', () => {
  const hpp = validateModelMetrics(payload()).models[0];
  for (const key of ['top5_precision', 'top1_precision', 'lift_top5'] as const) {
    assert.deepEqual(metricCell(hpp, key), {text: 'Not applicable', notApplicable: true, reason: HPP_REASON});
  }
  assert.equal(metricCell(hpp, 'auc').text, '0.500');
  assert.equal(metricCell(hpp, 'log_likelihood').text, '-5,204.2');
});

test('an incomplete or inconsistent evaluation is rejected', () => {
  const cases: [string, (raw: ReturnType<typeof payload>) => void][] = [
    ['missing HPP', raw => { raw.baselines.shift(); }],
    ['NHPP without ranking', raw => { (raw.baselines[1] as Record<string, unknown>).top5_precision = null; }],
    ['null without a reason', raw => { (raw.baselines[0] as Record<string, unknown>).not_applicable_reason = null; }],
    ['partly missing', raw => { (raw.baselines[0] as Record<string, unknown>).top5_precision = 0.01; }],
    ['bad hash', raw => { raw.params_sha256 = 'abc'; }],
    ['no training years', raw => { raw.train_years = []; }],
    ['wrong primary', raw => { raw.model = 'NHPP'; }],
  ];
  for (const [name, change] of cases) {
    const raw = payload(); change(raw);
    assert.throws(() => validateModelMetrics(raw), Error, name);
  }
});

test('the tie caveat is the risk README wording', () => {
  assert.match(TIE_CAVEAT, /^cNHPP vs NHPP is a tie\./);
  assert.match(TIE_CAVEAT, /every 95% bootstrap interval covers 0/);
});

test('a 503 from /metrics is a MetricsUnavailableError carrying the service reason', async t => {
  const detail = 'metrics_table.csv was not scored from artifacts/cnhpp_params.npz';
  const fetch = t.mock.method(globalThis, 'fetch', async (input: string) => {
    assert.equal(new URL(input).pathname.endsWith('/metrics'), true);
    return Response.json({detail}, {status: 503});
  });
  await assert.rejects(getModelMetrics(), (error: unknown) => error instanceof MetricsUnavailableError && error.detail === detail && /\(HTTP 503\)/.test(error.message));
  fetch.mock.mockImplementation(async () => new Response('down', {status: 503}));
  await assert.rejects(getModelMetrics(), (error: unknown) => error instanceof MetricsUnavailableError);
  fetch.mock.mockImplementation(async () => new Response('oops', {status: 500}));
  await assert.rejects(getModelMetrics(), /HTTP 500/);
  fetch.mock.mockImplementation(async () => Response.json(payload()));
  assert.equal((await getModelMetrics()).models.length, 3);
});

test('the model metrics view is a stat card with no datasets and no workspace year', () => {
  const view = PANEL_VIEWS.find(item => item.id === 'model-metrics')!;
  assert.equal(view.type, 'stat_card');
  const applied = viewSettings(settings({answerStat: {value: 1, label: 'x', scope: 'y', period: 'z', unit: ''}}), view);
  assert.equal(applied.statMode, 'model_metrics');
  assert.equal(applied.answerStat, undefined);
  assert.equal(currentView('stat_card', applied), 'model-metrics');
  assert.deepEqual(panelDatasets('stat_card', applied), []);
  assert.equal(panelUsesGlobalYear('stat_card', applied), false);
  const back = viewSettings(settings({statMode: 'model_metrics', metricsCitation: {evalYear: 2024, paramsSha256: SHA}}), PANEL_VIEWS.find(item => item.id === 'summary-stats')!);
  assert.equal(back.metricsCitation, undefined);
  assert.equal(currentView('stat_card', back), 'summary-stats');
});
