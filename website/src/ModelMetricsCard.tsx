import { getModelMetrics } from './api.ts';
import { ExportActions } from './ExportActions';
import { GRID_CAVEAT, METRIC_ROWS, metricCell, TIE_CAVEAT, trainingWindow } from './modelMetrics.ts';
import { usePanel } from './state';
import { useRemote } from './useRemote';

// HPP, NHPP and cNHPP side by side from GET /metrics, with the fit and
// evaluation window. The card does not use the workspace filters or year.
export function ModelMetricsCard() {
  const { settings } = usePanel();
  const remote = useRemote('model-metrics', getModelMetrics);
  const metrics = remote.data;
  const cited = settings.metricsCitation;
  const differs = Boolean(metrics && cited && (cited.evalYear !== metrics.eval_year || cited.paramsSha256 !== metrics.params_sha256));
  const unavailable = remote.error?.includes('(HTTP 503)');
  return <div className="analysis-chart stat-panel model-metrics-panel">
    <ExportActions datasets={[]} disabled={!metrics} rows={() => metrics!.models.flatMap(model => METRIC_ROWS.map(metric => ({
      model: model.model, metric: metric.label, value: model[metric.key], not_applicable_reason: model[metric.key] === null ? model.not_applicable_reason : '',
      eval_year: metrics!.eval_year, train_years: metrics!.train_years.join(';'), xi: metrics!.xi, params_sha256: metrics!.params_sha256,
    })))} />
    {remote.loading ? <div className="chart-empty" role="status"><span className="loading-text">Loading model metrics…</span></div>
      : remote.error ? <div className="chart-empty load-error model-metrics-error" role="alert"><div>
        <p><strong>{unavailable ? 'Model metrics unavailable' : 'Could not load model metrics'}</strong></p>
        <p>{unavailable ? 'The risk service would not serve its stored evaluation because it could not be verified against the committed model fit, so no numbers are shown.' : remote.error}</p>
        {unavailable && <p className="panel-note">Service detail: {remote.error!.replace(/^Risk model metrics are unavailable \(HTTP 503\): /, '')}</p>}
        <button className="quiet-button" onClick={remote.retry}>Retry</button>
      </div></div>
      : metrics && <>
        <p className="panel-note model-metrics-window">
          Evaluated on {metrics.eval_year} ({metrics.eval_start} to {metrics.eval_end}), trained on {trainingWindow(metrics)}; cNHPP xi = {metrics.xi}.
          Statewide, not a forecast.
        </p>
        {differs && <p className="panel-note model-metrics-stale" role="status">The risk service now reports a different evaluation than the one the answer cited.</p>}
        <div className="model-metrics-scroll"><table className="model-metrics-table" aria-label="Risk model performance by model">
          <thead><tr><th scope="col">Metric</th>{metrics.models.map(model => <th key={model.model} scope="col">{model.model}</th>)}</tr></thead>
          <tbody>{METRIC_ROWS.map(metric => <tr key={metric.key}>
            <th scope="row" title={metric.note}>{metric.label}</th>
            {metrics.models.map(model => {
              const cell = metricCell(model, metric.key);
              return <td key={model.model} className={cell.notApplicable ? 'not-applicable' : undefined} title={cell.reason}>
                {cell.text}{cell.reason && <span className="visually-hidden">: {cell.reason}</span>}
              </td>;
            })}
          </tr>)}</tbody>
        </table></div>
        {metrics.models.filter(model => model.not_applicable_reason).map(model => <p key={model.model} className="panel-note">
          {model.model} top-k precision and lift are not applicable: {model.not_applicable_reason}.
        </p>)}
        <p className="panel-note model-metrics-caveat" role="note">{TIE_CAVEAT}</p>
        <p className="panel-note">{GRID_CAVEAT}</p>
        <p className="panel-note model-metrics-hash">Parameter hash (sha256 of the committed fit): <code>{metrics.params_sha256}</code></p>
      </>}
  </div>;
}
