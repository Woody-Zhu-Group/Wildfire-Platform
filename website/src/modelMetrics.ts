// Risk model performance from the risk service's GET /metrics: the persisted
// HPP, NHPP and cNHPP evaluation on one held-out year. Nothing is recomputed here.

export type ModelName = 'HPP' | 'NHPP' | 'cNHPP';
export const MODEL_ORDER: readonly ModelName[] = ['HPP', 'NHPP', 'cNHPP'];

export interface ModelRow {
  model: ModelName;
  log_likelihood: number;
  auc: number;
  top5_precision: number | null;
  top1_precision: number | null;
  lift_top5: number | null;
  not_applicable_reason: string | null;
}

export interface ModelMetrics {
  models: ModelRow[];
  xi: number;
  train_years: number[];
  eval_year: number;
  eval_start: string;
  eval_end: string;
  params_sha256: string;
}

export type MetricKey = 'log_likelihood' | 'auc' | 'top5_precision' | 'top1_precision' | 'lift_top5';
const RANKING: readonly MetricKey[] = ['top5_precision', 'top1_precision', 'lift_top5'];

export const METRIC_ROWS: readonly {key: MetricKey; label: string; note: string}[] = [
  {key: 'log_likelihood', label: 'Log-likelihood', note: 'Held-out year; higher is better.'},
  {key: 'auc', label: 'AUC', note: 'Daily ranking diagnostic; 0.5 is no ranking.'},
  {key: 'top5_precision', label: 'Top 5% precision', note: 'Share of top 5% cell-days with an ignition.'},
  {key: 'top1_precision', label: 'Top 1% precision', note: 'Share of top 1% cell-days with an ignition.'},
  {key: 'lift_top5', label: 'Top 5% lift', note: 'Top 5% precision over the base rate.'},
];

// services/risk_forecasting/README.md, "Scientific caveats".
export const TIE_CAVEAT = 'cNHPP vs NHPP is a tie. On the corrected 824-cell weather grid, the out-of-sample log-likelihood gap flips sign across holdout years and every 95% bootstrap interval covers 0. The spatial memory term adds little here.';
export const GRID_CAVEAT = 'Scored statewide on 0.24 degree grid cells (about 25 km), coarser than circuits.';

/** A 503 from /metrics: the stored evaluation could not be verified against the committed fit. */
export class MetricsUnavailableError extends Error {
  readonly detail: string;
  constructor(detail: string) {
    super(`Risk model metrics are unavailable (HTTP 503): ${detail}`);
    this.name = 'MetricsUnavailableError';
    this.detail = detail;
  }
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function row(value: unknown): ModelRow {
  const item = (value ?? {}) as Partial<ModelRow>;
  if (!MODEL_ORDER.includes(item.model as ModelName) || !finite(item.log_likelihood) || !finite(item.auc)) {
    throw new Error('Risk service returned an invalid model row.');
  }
  const missing = RANKING.filter(key => item[key] === null || item[key] === undefined);
  if (RANKING.some(key => !missing.includes(key) && !finite(item[key]))) throw new Error(`Risk service returned an invalid ${item.model} value.`);
  const reason = typeof item.not_applicable_reason === 'string' && item.not_applicable_reason ? item.not_applicable_reason : null;
  // Top-k values are all present, or all absent with a reason (a model with no ranking).
  if (missing.length && (missing.length !== RANKING.length || !reason)) throw new Error(`Risk service returned partial ${item.model} ranking values.`);
  if (missing.length && item.model !== 'HPP') throw new Error(`Risk service returned no ranking values for ${item.model}.`);
  return {
    model: item.model as ModelName, log_likelihood: item.log_likelihood!, auc: item.auc!,
    top5_precision: item.top5_precision ?? null, top1_precision: item.top1_precision ?? null, lift_top5: item.lift_top5 ?? null,
    not_applicable_reason: missing.length ? reason : null,
  };
}

export function validateModelMetrics(value: unknown): ModelMetrics {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Risk service returned invalid metrics.');
  const raw = value as Record<string, unknown>;
  if (raw.model !== 'cNHPP' || !Array.isArray(raw.baselines)) throw new Error('Risk service returned invalid metrics.');
  const rows = [row(raw), ...raw.baselines.map(row)];
  if (rows.length !== 3 || new Set(rows.map(item => item.model)).size !== 3) throw new Error('Risk service must return HPP, NHPP and cNHPP.');
  const years = raw.train_years;
  if (!Array.isArray(years) || !years.length || !years.every(year => Number.isInteger(year))) throw new Error('Risk service returned invalid training years.');
  if (!Number.isInteger(raw.eval_year) || !finite(raw.xi) || typeof raw.eval_start !== 'string' || typeof raw.eval_end !== 'string'
    || typeof raw.params_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(raw.params_sha256)) {
    throw new Error('Risk service returned an invalid evaluation window.');
  }
  return {
    models: MODEL_ORDER.map(name => rows.find(item => item.model === name)!),
    xi: raw.xi, train_years: years as number[], eval_year: raw.eval_year as number,
    eval_start: raw.eval_start, eval_end: raw.eval_end, params_sha256: raw.params_sha256,
  };
}

/** Display text for one cell; a null value is not applicable, with the model's reason. */
export function metricCell(model: ModelRow, key: MetricKey): {text: string; notApplicable: boolean; reason?: string} {
  const value = model[key];
  if (value === null) return {text: 'Not applicable', notApplicable: true, reason: model.not_applicable_reason ?? undefined};
  if (key === 'log_likelihood') return {text: value.toLocaleString('en-US', {minimumFractionDigits: 1, maximumFractionDigits: 1}), notApplicable: false};
  if (key === 'auc') return {text: value.toFixed(3), notApplicable: false};
  if (key === 'lift_top5') return {text: value.toFixed(2), notApplicable: false};
  return {text: `${(value * 100).toFixed(2)}%`, notApplicable: false};
}

export function trainingWindow(metrics: ModelMetrics): string {
  const years = [...metrics.train_years].sort((a, b) => a - b);
  return years.length === 1 ? String(years[0]) : `${years[0]} to ${years.at(-1)}`;
}
