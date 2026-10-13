import { useEffect, useId, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import studyUrl from '../data/optimization_grid/study.json?url';
import circuitsUrl from '../data/optimization_grid/circuits.json?url';
import { getJSON } from './api.ts';
import { LoadState } from './Controls';
import { ExportActions } from './ExportActions';
import { lineSvg } from './exports.ts';
import { usePanel } from './state';
import { useRemote } from './useRemote';
import {
  allocation, decisionClass, DEFAULT_VISIBLE_METHODS, FILLED_METRICS, formatMetric, formatParam, matchedLevels, metricDef,
  methodIndex, METRICS, PROPOSED_METHOD, RANKING_LABEL, RANKING_METRICS, RANKING_NOTE, RANKING_SERIES, rankingSpread,
  rankingWinShare, sectionalizedCounts, sensitivity, setupByIdOrDefault, setupLabel, setupValues, sliderIndex, snapSetup, spread,
  validateCircuits, validateDecisions, validateStudy, winShare,
  type DecisionClass, type MetricId, type ParamKey, type PlanVariant, type Study, type StudyCircuits,
} from './studyData.ts';

// One URL per setup; a decision file is fetched only when its map is shown.
const decisionFiles = import.meta.glob('../data/optimization_grid/decisions/*.json', { eager: true, query: '?url', import: 'default' }) as Record<string, string>;
const decisionUrl = (setupId: string): string | undefined => decisionFiles[`../data/optimization_grid/decisions/${setupId}.json`];

function useStudy() {
  return useRemote('optimization-study', async () => validateStudy(await getJSON<unknown>(studyUrl)));
}

export function OptimizationStudy() {
  const { settings } = usePanel();
  const remote = useStudy();
  if (!remote.data) return <div className="analysis-chart study-panel"><LoadState loading={remote.loading} error={remote.error} retry={remote.error ? remote.retry : undefined} /></div>;
  const study = remote.data;
  switch (settings.studyView) {
    case 'sensitivity': return <SensitivityView study={study} />;
    case 'allocation': return <AllocationView study={study} />;
    case 'map': return <DecisionMapView study={study} />;
    case 'summary': return <SummaryView study={study} />;
    default: return <MethodsView study={study} />;
  }
}

function MethodKey({ study, index }: { study: Study; index: number }) {
  const method = study.data.methods[index];
  return <span className="method-key" title={`${method.label}. ${method.description}`}><i aria-hidden="true" style={{ background: `var(--method-${index})` }} />{method.short}</span>;
}

function MetricSelect({ value, onChange, variant }: { value: MetricId; onChange: (id: MetricId) => void; variant: PlanVariant }) {
  return <label>Metric<select aria-label="Metric" value={value} onChange={event => onChange(event.target.value as MetricId)}>
    {METRICS.filter(metric => variant === 'solved' || FILLED_METRICS.includes(metric.id)).map(metric => <option key={metric.id} value={metric.id} title={metric.description}>{metric.label}</option>)}
  </select></label>;
}

/** The plan variant and a metric that exists for it. */
function usePlanVariant(): { variant: PlanVariant; metric: MetricId } {
  const { settings } = usePanel();
  const variant: PlanVariant = settings.studyFill ? 'filled' : 'solved';
  const chosen = settings.studyMetric ?? 'risk_reduction';
  return { variant, metric: variant === 'filled' && !FILLED_METRICS.includes(chosen) ? 'risk_reduction' : chosen };
}

function PlanToggle() {
  const { settings, update } = usePanel();
  return <div className="measure-switch" role="group" aria-label="Plans">
    <button type="button" aria-pressed={!settings.studyFill} title="Plans as the optimization solved them." onClick={() => update({ studyFill: false })}>As solved</button>
    <button type="button" aria-pressed={Boolean(settings.studyFill)} title="Unused sectionalization budget filled by predicted exposure, then the test-year PSPS response re-solved. Filling is free in the model, so it separates the value of the uncertainty set from how many circuits a plan sectionalized." onClick={() => update({ studyFill: true })}>Budget filled</button>
  </div>;
}

/**
 * One slider per swept parameter. The grid is not a full factorial, so a move
 * can carry other parameters to the nearest run setup (``snapSetup``); the
 * note names them. Sliders only reach the setups the grid actually ran.
 */
function SetupSliders({ study, setup }: { study: Study; setup: number }) {
  const { update } = usePanel();
  const [moved, setMoved] = useState<ParamKey[]>([]);
  const base = useId();
  const values = setupValues(study, setup);
  const label = (key: ParamKey) => study.data.parameters.find(parameter => parameter.key === key)!.label;
  return <div className="study-sliders">
    {study.data.parameters.map(parameter => <div key={parameter.key} className="study-slider" title={`${parameter.description} Unit: ${parameter.unit}.`}>
      <label htmlFor={`${base}-${parameter.key}`}><span>{parameter.label}</span><strong>{formatParam(parameter.key, values[parameter.key])}</strong></label>
      <input id={`${base}-${parameter.key}`} type="range" min={0} max={parameter.values.length - 1} step={1}
        value={sliderIndex(study, setup, parameter.key)} aria-valuetext={formatParam(parameter.key, values[parameter.key])}
        onChange={event => {
          const next = snapSetup(study, setup, parameter.key, parameter.values[Number(event.target.value)]);
          setMoved(next.changed);
          update({ studySetup: study.data.setups.id[next.index] });
        }} />
      <span className="study-slider-ticks" aria-hidden="true">{parameter.values.map(value => <span key={value}>{formatParam(parameter.key, value)}</span>)}</span>
    </div>)}
    {moved.length > 0 && <p className="filter-reason study-slider-note" role="status">Also moved {moved.map(label).join(', ')}: the grid did not run that combination.</p>}
  </div>;
}

function SetupPicker({ study, setup }: { study: Study; setup: number }) {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();
  useEffect(() => {
    if (!open) return;
    const element = dialog.current!;
    const root = document.documentElement; const previous = root.style.overflow;
    root.style.overflow = 'hidden'; element.showModal();
    return () => { element.close(); root.style.overflow = previous; };
  }, [open]);
  return <div className="chart-filter-drawer study-setup">
    <button className="filter-trigger" aria-haspopup="dialog" aria-label={`Planning setup: ${setupLabel(study, setup)}`} onClick={() => setOpen(true)}>
      <span>Setup</span><span className="filter-summary">{setupLabel(study, setup)}</span>
    </button>
    {open && <dialog ref={dialog} className="filter-dialog analysis-chart" aria-labelledby={title}
      onCancel={event => { event.stopPropagation(); setOpen(false); }} onClick={event => { if (event.target === event.currentTarget) setOpen(false); }}>
      <div onClick={event => event.stopPropagation()}>
        <header><h2 id={title}>Planning setup</h2><button aria-label="Close setup" onClick={() => setOpen(false)}>×</button></header>
        <SetupSliders study={study} setup={setup} />
        <p className="panel-note">PSPS SAIFI factor {study.data.fixed.delta} and miscoverage level {study.data.fixed.alpha} are fixed across the grid.</p>
        <button className="quiet-button" onClick={() => setOpen(false)}>Done</button>
      </div>
    </dialog>}
  </div>;
}

function setupRow(study: Study, setup: number) {
  const values = setupValues(study, setup);
  return { setup: study.data.setups.id[setup], sectionalization_budget: values.C, fast_trip_budget: values.B, saifi_cap: values.W, fast_trip_effectiveness: values.E, fast_trip_saifi_factor: values.G };
}

function MethodsView({ study }: { study: Study }) {
  const { settings, update, expanded } = usePanel();
  const setup = setupByIdOrDefault(study, settings.studySetup);
  const plan = usePlanVariant();
  const metric = metricDef(plan.metric);
  const rows = study.data.methods.map((method, index) => ({ method, index, ...spread(study, setup, index, metric.id, plan.variant) }));
  const reference = RANKING_METRICS.includes(metric.id) ? rankingSpread(study, setup, metric.id, plan.variant) : null;
  const numbers = [...rows, ...(reference ? [reference] : [])].flatMap(row => row.values.filter((value): value is number => value !== null));
  const lo = Math.min(0, ...numbers), hi = Math.max(0, ...numbers);
  const span = hi - lo || 1;
  const pos = (value: number) => (value - lo) / span * 100;
  return <div className="analysis-chart study-panel">
    <ExportActions datasets={[]} rows={() => [...rows.map(row => ({ label: row.method.label, values: row.values })), ...(reference ? [{ label: `${RANKING_LABEL} (not in the grid)`, values: reference.values }] : [])]
      .flatMap(row => row.values.map((value, exp) => ({ ...setupRow(study, setup), plans: plan.variant, method: row.label, replication: exp + 1, metric: metric.label, value })))} />
    <div className="study-toolbar"><MetricSelect value={metric.id} variant={plan.variant} onChange={studyMetric => update({ studyMetric })} /><PlanToggle /><SetupPicker study={study} setup={setup} /></div>
    <div className="bar-summary"><span title={metric.description}>{metric.label}</span><span>Bar: mean of {study.replications} replications · ticks: each replication</span></div>
    <div className="study-bars" role="list" aria-label={`${metric.label} by planning method`}>
      {rows.map(row => <div key={row.method.slug} className="study-bar-row" role="listitem"
        title={`${row.method.label}: ${row.values.map((value, exp) => `replication ${exp + 1} ${formatMetric(metric.unit, value)}`).join(', ')}`}>
        <MethodKey study={study} index={row.index} />
        <div className="study-track">
          {row.mean === null ? <span className="missing-bar" title="This metric is not defined for this method.">No data</span> : <>
            <div className={`value-bar${row.mean < 0 ? ' is-negative' : ''}`} style={{ left: `${pos(Math.min(0, row.mean))}%`, width: `${Math.abs(pos(row.mean) - pos(0))}%` }} />
            {lo < 0 && <span className="study-zero" style={{ left: `${pos(0)}%` }} />}
            {row.min !== null && row.max !== null && <span className="study-range" style={{ left: `${pos(row.min)}%`, width: `${pos(row.max) - pos(row.min)}%` }} />}
            {row.values.map((value, exp) => value === null ? null : <span key={exp} className="study-tick" style={{ left: `${pos(value)}%` }} />)}
          </>}
        </div>
        <span className="bar-value"><strong>{formatMetric(metric.unit, row.mean)}</strong>{row.min !== null && row.max !== null && row.min !== row.max && <small>{formatMetric(metric.unit, row.min)} to {formatMetric(metric.unit, row.max)}</small>}</span>
      </div>)}
      {reference && reference.mean !== null && <div className="study-bar-row is-reference" role="listitem" title={`${RANKING_LABEL}. ${RANKING_NOTE}`}>
        <span className="method-key"><i aria-hidden="true" className="is-reference" />Ranking ref.</span>
        <div className="study-track">
          <div className={`value-bar${reference.mean < 0 ? ' is-negative' : ''}`} style={{ left: `${pos(Math.min(0, reference.mean))}%`, width: `${Math.abs(pos(reference.mean) - pos(0))}%` }} />
          {lo < 0 && <span className="study-zero" style={{ left: `${pos(0)}%` }} />}
          {reference.values.map((value, exp) => value === null ? null : <span key={exp} className="study-tick" style={{ left: `${pos(value)}%` }} />)}
        </div>
        <span className="bar-value"><strong>{formatMetric(metric.unit, reference.mean)}</strong><small>not in the grid</small></span>
      </div>}
    </div>
    {expanded && <ReplicationTable study={study} setup={setup} metric={metric.id} variant={plan.variant} />}
  </div>;
}

function ReplicationTable({ study, setup, metric, variant }: { study: Study; setup: number; metric: MetricId; variant: PlanVariant }) {
  const def = metricDef(metric);
  return <div className="comparison-table-scroll study-table"><table className="comparison-values" aria-label={`${def.label} by method and replication`}>
    <thead><tr><th scope="col">Method</th>{Array.from({ length: study.replications }, (_, exp) => <th key={exp} scope="col">Replication {exp + 1}</th>)}<th scope="col">Mean</th></tr></thead>
    <tbody>{study.data.methods.map((method, index) => {
      const s = spread(study, setup, index, metric, variant);
      return <tr key={method.slug}><th scope="row">{method.label}</th>{s.values.map((value, exp) => <td key={exp}>{formatMetric(def.unit, value)}</td>)}<td>{formatMetric(def.unit, s.mean)}</td></tr>;
    })}
    {RANKING_METRICS.includes(metric) && (() => { const r = rankingSpread(study, setup, metric, variant); return <tr><th scope="row" title={RANKING_NOTE}>{RANKING_LABEL} (not in the grid)</th>{r.values.map((value, exp) => <td key={exp}>{formatMetric(def.unit, value)}</td>)}<td>{formatMetric(def.unit, r.mean)}</td></tr>; })()}
    </tbody>
  </table></div>;
}

function visibleMethods(study: Study, chosen: string[] | undefined): number[] {
  const slugs = chosen ?? [...DEFAULT_VISIBLE_METHODS];
  return study.data.methods.map((method, index) => slugs.includes(method.slug) ? index : -1).filter(index => index >= 0);
}

function niceTicks(lo: number, hi: number, count = 4): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(f => f * magnitude).find(f => f >= raw)!;
  const start = Math.floor(lo / step) * step;
  return Array.from({ length: Math.ceil((hi - start) / step - 1e-9) + 1 }, (_, i) => start + i * step);
}

function SensitivityView({ study }: { study: Study }) {
  const { settings, update, expanded, title } = usePanel();
  const setup = setupByIdOrDefault(study, settings.studySetup);
  const plan = usePlanVariant();
  const metric = metricDef(plan.metric);
  const param = settings.studyParam ?? 'W';
  const mode = settings.studyMode ?? 'slice';
  const parameter = study.data.parameters.find(item => item.key === param)!;
  const shown = visibleMethods(study, settings.studyMethods);
  const series = shown.map(index => ({ index, points: sensitivity(study, setup, param, metric.id, index, mode, plan.variant) }));
  const reference = RANKING_METRICS.includes(metric.id) ? sensitivity(study, setup, param, metric.id, RANKING_SERIES, mode, plan.variant) : null;
  const matched = mode === 'matched' ? matchedLevels(study, param) : null;
  const [hovered, setHovered] = useState<number | null>(null);
  const plot = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 540, height: 246 });
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setSize({ width: Math.max(260, entry.contentRect.width), height: Math.max(140, entry.contentRect.height) }));
    observer.observe(plot.current!);
    return () => observer.disconnect();
  }, []);
  const levels = parameter.values;
  const values = [...series.map(item => item.points), ...(reference ? [reference] : [])].flatMap(points => points.map(point => point.value)).filter((value): value is number => value !== null);
  const zeroBased = metric.unit !== 'ratio';
  const ticks = niceTicks(zeroBased ? Math.min(0, ...values) : Math.min(...values), Math.max(...values, zeroBased ? 0 : -Infinity));
  const yLo = ticks[0], yHi = ticks[ticks.length - 1];
  const { width, height } = size;
  const left = 48, right = width - 20, top = 18, bottom = height - 30;
  const x = (i: number) => levels.length <= 1 ? (left + right) / 2 : left + i / (levels.length - 1) * (right - left);
  const y = (value: number) => bottom - (value - yLo) / (yHi - yLo || 1) * (bottom - top);
  const active = hovered === null ? null : Math.min(hovered, levels.length - 1);
  // Without a hover, the readout shows the selected setup's level (slice) or the last level (matched).
  const selectedLevel = levels.findIndex(level => Math.abs(level - setupValues(study, setup)[param]) < 1e-9);
  const readoutIndex = active ?? (mode === 'slice' && selectedLevel >= 0 ? selectedLevel : levels.length - 1);
  const tickLabel = (tick: number) => metric.unit === 'percent' && (yHi - yLo) * 100 >= 4 ? `${Math.round(tick * 100)}%` : formatMetric(metric.unit, tick);
  const toggle = (slug: string) => {
    const current = study.data.methods.filter((_, index) => shown.includes(index)).map(method => method.slug);
    update({ studyMethods: current.includes(slug) ? current.filter(item => item !== slug) : [...current, slug] });
  };
  const empty = !shown.length ? 'Select at least one method.' : !values.length ? 'No run setups vary only this parameter here. Try the matched average.' : null;
  const scope = mode === 'slice'
    ? `Other parameters held at ${setupLabel(study, setup).split(' · ').filter(part => !part.startsWith(`${param} `)).join(' · ')}`
    : `Each level averages the same ${matched!.combos.length} combinations of the other parameters`;
  const pathFor = (points: { value: number | null }[]) => points.reduce((d, point, i) => point.value === null ? d
    : `${d}${i > 0 && points[i - 1].value !== null ? 'L' : 'M'}${x(i)} ${y(point.value)}`, '');
  return <div className="analysis-chart series-panel study-panel">
    <ExportActions datasets={[]} disabled={Boolean(empty)}
      rows={() => [...series.map(item => ({ label: study.data.methods[item.index].label, points: item.points })), ...(reference ? [{ label: `${RANKING_LABEL} (not in the grid)`, points: reference }] : [])]
        .flatMap(item => item.points.map(point => ({ method: item.label, plans: plan.variant, parameter: parameter.label, level: point.level, metric: metric.label, value: point.value, mode, setups_averaged: point.setups, ...(mode === 'slice' ? setupRow(study, setup) : {}) })))}
      svg={() => { const svg = plot.current?.querySelector('svg'); if (!svg) throw new Error('Chart is not ready.'); return lineSvg(svg, title, `${metric.label} by ${parameter.label.toLowerCase()}${plan.variant === 'filled' ? ', unused sectionalization budget filled' : ''}. ${scope}. Mean of ${study.replications} replications.`, series.map(item => ({ label: study.data.methods[item.index].label, color: getComputedStyle(document.documentElement).getPropertyValue(`--method-${item.index}`).trim() }))); }} />
    <div className="study-toolbar">
      <label>Vary<select aria-label="Parameter to vary" value={param} onChange={event => { update({ studyParam: event.target.value as ParamKey }); setHovered(null); }}>
        {study.data.parameters.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
      <MetricSelect value={metric.id} variant={plan.variant} onChange={studyMetric => update({ studyMetric })} />
      <PlanToggle />
      <div className="measure-switch" role="group" aria-label="Other parameters">
        <button type="button" aria-pressed={mode === 'slice'} title="Hold the other parameters at the selected setup" onClick={() => update({ studyMode: 'slice' })}>Slice</button>
        <button type="button" aria-pressed={mode === 'matched'} title="Average over the setups shared by every level" onClick={() => update({ studyMode: 'matched' })}>Matched average</button>
      </div>
      {mode === 'slice' && <SetupPicker study={study} setup={setup} />}
    </div>
    <div className="dataset-switches" role="group" aria-label="Methods shown">
      {study.data.methods.map((method, index) => <button key={method.slug} type="button" role="checkbox" aria-checked={shown.includes(index)} aria-label={method.label} title={method.description} onClick={() => toggle(method.slug)}>
        <span className="dataset-swatch" style={{ background: `var(--method-${index})` }} />{method.short}<span aria-hidden="true" className="dataset-check">{shown.includes(index) ? '✓' : '−'}</span>
      </button>)}
      {reference && <span className="study-reference-key" title={RANKING_NOTE}><i aria-hidden="true" />Ranking ref. (dashed)</span>}
    </div>
    <div ref={plot} className="series-plot">
      {empty ? <div className="chart-empty" role="status">{empty}</div> : <svg viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0}
        aria-label={`${metric.label} by ${parameter.label}. Use left and right arrows to inspect levels.`}
        onMouseLeave={() => setHovered(null)} onMouseMove={event => {
          const bounds = event.currentTarget.getBoundingClientRect();
          const position = (event.clientX - bounds.left) / bounds.width * width;
          setHovered(Math.max(0, Math.min(levels.length - 1, Math.round((position - left) / (right - left || 1) * (levels.length - 1)))));
        }} onKeyDown={event => {
          if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); setHovered(Math.max(0, Math.min(levels.length - 1, (active ?? 0) + (event.key === 'ArrowRight' ? 1 : -1)))); }
        }}>
        {ticks.map(tick => <g key={tick}><path d={`M${left} ${y(tick)}H${right}`} stroke="var(--chart-grid)" /><text x={left - 8} y={y(tick) + 4} textAnchor="end">{tickLabel(tick)}</text></g>)}
        {levels.map((level, i) => <text key={level} x={x(i)} y={height - 9} textAnchor={i === 0 ? 'start' : i === levels.length - 1 ? 'end' : 'middle'}>
          {formatParam(param, level)}{series.every(item => item.points[i].value === null) ? ' (not run)' : ''}</text>)}
        {active !== null && <path d={`M${x(active)} ${top}V${bottom}`} stroke="var(--chart-cursor)" />}
        {reference && <path className="study-reference-line" d={pathFor(reference)} fill="none"><title>{RANKING_LABEL} (not in the grid)</title></path>}
        {series.map(item => <g key={item.index}>
          <path d={pathFor(item.points)} fill="none" stroke={`var(--method-${item.index})`} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
          {item.points.map((point, i) => point.value === null ? null : <circle key={i} cx={x(i)} cy={y(point.value)} r={active === i ? 5 : 4} fill={`var(--method-${item.index})`} stroke="var(--surface)" strokeWidth="2">
            <title>{study.data.methods[item.index].label}, {parameter.label} {formatParam(param, point.level)}: {formatMetric(metric.unit, point.value)}</title></circle>)}
        </g>)}
      </svg>}
    </div>
    {!empty && <div className="series-readout" aria-live="polite">
      <span title={scope}>{parameter.label} {formatParam(param, levels[readoutIndex])}{active === null ? ` · ${scope.charAt(0).toLowerCase()}${scope.slice(1)}` : ''}</span>
      <div>{series.map(item => <span key={item.index} className="method-key"><i aria-hidden="true" style={{ background: `var(--method-${item.index})` }} />{study.data.methods[item.index].short}
        <strong>{formatMetric(metric.unit, item.points[readoutIndex].value)}</strong></span>)}
        {reference && <span className="method-key" title={RANKING_NOTE}><i aria-hidden="true" className="is-reference" />Ranking ref.<strong>{formatMetric(metric.unit, reference[readoutIndex].value)}</strong></span>}</div>
    </div>}
    {expanded && !empty && <div className="comparison-table-scroll study-table"><table className="comparison-values" aria-label={`${metric.label} by ${parameter.label} and method`}>
      <thead><tr><th scope="col">Method</th>{levels.map(level => <th key={level} scope="col">{formatParam(param, level)}</th>)}</tr></thead>
      <tbody>{series.map(item => <tr key={item.index}><th scope="row">{study.data.methods[item.index].label}</th>{item.points.map(point => <td key={point.level}>{formatMetric(metric.unit, point.value)}</td>)}</tr>)}
        {reference && <tr><th scope="row" title={RANKING_NOTE}>{RANKING_LABEL} (not in the grid)</th>{reference.map(point => <td key={point.level}>{formatMetric(metric.unit, point.value)}</td>)}</tr>}</tbody>
    </table></div>}
  </div>;
}

function AllocationView({ study }: { study: Study }) {
  const { settings, expanded } = usePanel();
  const setup = setupByIdOrDefault(study, settings.studySetup);
  const cap = study.data.setups.W[setup];
  const rows = study.data.methods.map((method, index) => ({ method, index, share: allocation(study, setup, index) }));
  const percent = (value: number) => `${(value * 100).toFixed(0)}%`;
  return <div className="analysis-chart study-panel">
    <ExportActions datasets={[]} rows={() => rows.map(row => ({ ...setupRow(study, setup), method: row.method.label, fast_trip_share_of_cap: row.share?.fastTrip ?? null, psps_realized_share_of_cap: row.share?.psps ?? null, psps_planned_share_of_cap: row.share?.pspsPlanned ?? null, unused_share_of_cap: row.share?.unused ?? null }))} />
    <div className="study-toolbar"><SetupPicker study={study} setup={setup} /></div>
    <div className="bar-summary"><span>Share of the SAIFI cap ({cap.toFixed(2)} interruptions per customer-year)</span><span>Mean of {study.replications} replications</span></div>
    <div className="study-bars" role="list" aria-label="SAIFI cap use by planning method">
      {rows.map(row => <div key={row.method.slug} className="study-bar-row" role="listitem"
        title={row.share ? `${row.method.label}: fast-trip ${percent(row.share.fastTrip)}, PSPS in the test year ${percent(row.share.psps)} (planned ${percent(row.share.pspsPlanned)}), unused ${percent(row.share.unused)}` : undefined}>
        <MethodKey study={study} index={row.index} />
        <div className="study-track study-stack">{row.share ? <>
          {row.share.fastTrip > 0.0005 && <span className="study-segment" style={{ width: `${row.share.fastTrip * 100}%`, background: 'var(--decision-fast-trip)' }} />}
          {row.share.psps > 0.0005 && <span className="study-segment" style={{ width: `${row.share.psps * 100}%`, background: 'var(--decision-psps)' }} />}
        </> : <span className="missing-bar">No data</span>}</div>
        <span className="bar-value"><strong>{row.share ? `${percent(row.share.fastTrip + row.share.psps)} used` : 'No data'}</strong>{row.share && <small>PSPS planned {percent(row.share.pspsPlanned)}</small>}</span>
      </div>)}
    </div>
    <div className="map-legend study-legend" aria-label="Legend"><div className="legend-keys">
      <span className="legend-key"><i className="legend-swatch" style={{ background: 'var(--decision-fast-trip)' }} />Fast-trip settings</span>
      <span className="legend-key"><i className="legend-swatch" style={{ background: 'var(--decision-psps)' }} />PSPS in the test year</span>
      <span className="legend-key"><i className="legend-swatch is-track" />Unused</span>
    </div></div>
    {expanded && <div className="comparison-table-scroll study-table"><table className="comparison-values" aria-label="SAIFI cap use by planning method">
      <thead><tr><th scope="col">Method</th><th scope="col">Fast-trip</th><th scope="col">PSPS, test year</th><th scope="col">PSPS, planned</th><th scope="col">Unused</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.method.slug}><th scope="row">{row.method.label}</th>{row.share ? [row.share.fastTrip, row.share.psps, row.share.pspsPlanned, row.share.unused].map((value, i) => <td key={i}>{percent(value)}</td>) : <td colSpan={4}>No data</td>}</tr>)}</tbody>
    </table></div>}
  </div>;
}

/** Re-render when the workspace theme changes, so canvas colors follow the CSS tokens. */
function useThemeVersion() {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const observer = new MutationObserver(() => setVersion(value => value + 1));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  return version;
}

const CLASS_ORDER: Record<DecisionClass, number> = { none: 0, sectionalized: 1, fast_trip: 2, psps: 3 };
const CLASS_LABEL: Record<DecisionClass, string> = { sectionalized: 'Sectionalized', fast_trip: 'Sectionalized with fast-trip', psps: 'Shut off in the test year', none: 'Not sectionalized' };
const CLASS_TOKEN: Record<DecisionClass, string> = { sectionalized: '--decision-sectionalized', fast_trip: '--decision-fast-trip', psps: '--decision-psps', none: '--decision-none' };

function DecisionMapView({ study }: { study: Study }) {
  const { settings, update, expanded } = usePanel();
  const setup = setupByIdOrDefault(study, settings.studySetup);
  const setupId = study.data.setups.id[setup];
  const slugs = study.data.methods.map(method => method.slug);
  const method = settings.studyMethod && slugs.includes(settings.studyMethod) ? settings.studyMethod : PROPOSED_METHOD;
  const replication = settings.studyReplication !== undefined && settings.studyReplication >= -1 && settings.studyReplication < study.replications ? settings.studyReplication : 0;
  const circuits = useRemote('optimization-circuits', async () => validateCircuits(await getJSON<unknown>(circuitsUrl)));
  const url = decisionUrl(setupId);
  const decisions = useRemote(url ? `optimization-decisions:${setupId}` : null, async () => validateDecisions(await getJSON<unknown>(url!), setupId, study.data.source.circuits, slugs, study.replications));
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const [tileError, setTileError] = useState(false);
  const theme = useThemeVersion();
  // The setup sliders float over the map: folded in the overview, open when expanded.
  const [setupOpen, setSetupOpen] = useState(expanded);
  useEffect(() => setSetupOpen(expanded), [expanded]);
  const bounds = useMemo(() => circuits.data
    ? L.latLngBounds(circuits.data.lines.flatMap(parts => parts.map(part => [part[1], part[0]] as [number, number])))
    : null, [circuits.data]);
  // Fit the circuits until the user pans or zooms; a panel resize (adding panels, expanding) refits.
  const view = useRef({ userMoved: false, fitting: false, fit: () => {} });
  view.current.fit = () => {
    const instance = map.current;
    if (!instance || !bounds?.isValid() || view.current.userMoved) return;
    view.current.fitting = true;
    instance.invalidateSize({ pan: false, animate: false });
    instance.fitBounds(bounds, { padding: [12, 12], animate: false });
    view.current.fitting = false;
  };
  useEffect(() => {
    // Quarter zoom steps let a statewide fit fill the panel instead of snapping a whole level out.
    const instance = L.map(host.current!, { preferCanvas: true, scrollWheelZoom: false, zoomSnap: 0.25, zoomDelta: 0.5 }).setView([37.5, -120.5], 6);
    map.current = instance;
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { className: 'base-map-tiles', attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 19 })
      .on('tileerror', () => setTileError(true)).addTo(instance);
    instance.on('dragstart zoomstart', () => { if (!view.current.fitting) view.current.userMoved = true; });
    const observer = new ResizeObserver(() => { instance.invalidateSize({ pan: true, animate: false }); view.current.fit(); });
    observer.observe(host.current!);
    return () => { observer.disconnect(); instance.remove(); map.current = null; };
  }, []);
  useEffect(() => { const instance = map.current; if (!instance) return; if (expanded) instance.scrollWheelZoom.enable(); else instance.scrollWheelZoom.disable(); }, [expanded]);
  const codes = decisions.data?.methods[method];
  const shares = useMemo(() => codes && replication === -1 ? sectionalizedCounts(codes) : null, [codes, replication]);
  const classes = useMemo(() => codes && replication >= 0 ? Array.from(codes[replication], decisionClass) : null, [codes, replication]);
  const counts = useMemo(() => {
    const result: Record<DecisionClass, number> = { sectionalized: 0, fast_trip: 0, psps: 0, none: 0 };
    classes?.forEach(item => { result[item]++; });
    return result;
  }, [classes]);
  useEffect(() => {
    const instance = map.current, data = circuits.data;
    if (!instance || !data || (!classes && !shares)) return;
    const css = getComputedStyle(host.current!);
    const color = (token: string) => css.getPropertyValue(token).trim() || '#888';
    const renderer = L.canvas();
    const group = L.layerGroup().addTo(instance);
    const order = data.unit.map((_, i) => i).sort((a, b) => (classes ? CLASS_ORDER[classes[a]] - CLASS_ORDER[classes[b]] : shares![a] - shares![b]));
    for (const i of order) {
      const latlngs = data.lines[i].map(part => { const points: [number, number][] = []; for (let k = 0; k < part.length; k += 2) points.push([part[k + 1], part[k]]); return points; });
      let stroke: string, weight: number, opacity: number, status: string;
      if (classes) {
        const kind = classes[i];
        stroke = color(CLASS_TOKEN[kind]); weight = kind === 'none' ? 1.2 : kind === 'psps' ? 3.5 : 2.5; opacity = kind === 'none' ? 0.55 : 0.95;
        status = CLASS_LABEL[kind];
      } else {
        const count = shares![i];
        stroke = count ? color(`--study-share-${count}`) : color('--decision-none'); weight = count ? 2.5 : 1.2; opacity = count ? 0.95 : 0.55;
        status = `Sectionalized in ${count} of ${study.replications} replications`;
      }
      L.polyline(latlngs, { renderer, color: stroke, weight, opacity, lineCap: 'round', lineJoin: 'round' })
        .bindTooltip(circuitTooltip(data, i, status), { sticky: true }).addTo(group);
    }
    return () => { instance.removeLayer(group); };
  }, [circuits.data, classes, shares, theme, study.replications]);
  useEffect(() => { view.current.fit(); }, [bounds, expanded]);
  const loading = circuits.loading || decisions.loading;
  const error = circuits.error || decisions.error || (!url ? `No decision file for setup ${setupId}.` : null);
  const shownCount = classes ? classes.length - counts.none : shares ? shares.filter(Boolean).length : 0;
  return <div className="live-map study-map">
    <ExportActions datasets={[]} disabled={!circuits.data || !codes} rows={() => circuits.data!.unit.map((unit, i) => ({
      ...setupRow(study, setup), method: study.data.methods[methodIndex(study, method)].label, replication: replication >= 0 ? replication + 1 : 'all',
      unit, circuit_id: circuits.data!.circuitId[i], division: circuits.data!.division[i], hftd_tier: circuits.data!.tier[i], population: circuits.data!.population[i],
      decision: classes ? CLASS_LABEL[classes[i]] : null, replications_sectionalized: shares ? shares[i] : null,
    }))} />
    <div className="analysis-chart map-toolbar"><div className="study-toolbar">
      <label>Method<select aria-label="Planning method" value={method} onChange={event => update({ studyMethod: event.target.value })}>
        {study.data.methods.map(item => <option key={item.slug} value={item.slug}>{item.label}</option>)}</select></label>
      <label>Replication<select aria-label="Replication" value={replication} onChange={event => update({ studyReplication: Number(event.target.value) })}>
        {Array.from({ length: study.replications }, (_, exp) => <option key={exp} value={exp}>Replication {exp + 1}</option>)}
        <option value={-1}>All {study.replications}, by frequency</option></select></label>
      <span className="map-count">{codes ? `${shownCount.toLocaleString()} of ${study.data.source.circuits} circuits sectionalized` : ''}</span>
    </div></div>
    <div className="map-stage"><div ref={host} className="leaflet-host" aria-label="Planning decisions by circuit" />
      <div className={`study-map-setup${setupOpen ? ' is-open' : ''}`}>
        <button type="button" className="study-map-setup-toggle" aria-expanded={setupOpen} aria-label={`Planning setup: ${setupLabel(study, setup)}`}
          onClick={() => setSetupOpen(open => !open)}>
          <span>Setup</span><span className="filter-summary">{setupLabel(study, setup)}</span><span aria-hidden="true">{setupOpen ? '▴' : '▾'}</span>
        </button>
        {setupOpen && <SetupSliders study={study} setup={setup} />}
      </div>
      {(loading || error) && <div className="map-status"><LoadState loading={loading} error={error} retry={circuits.error ? circuits.retry : decisions.error ? decisions.retry : undefined} /></div>}
      {tileError && <div className="map-warning" role="status">Basemap unavailable; circuits are still shown.</div>}
    </div>
    <div className="map-legend" aria-label="Map legend"><div className="legend-keys">
      {classes ? (['sectionalized', 'fast_trip', 'psps', 'none'] as DecisionClass[]).map(kind => <span key={kind} className="legend-key"
        title={kind === 'psps' ? 'One optimal shutoff set for the test-year ignitions; tied sets can differ circuit by circuit.' : undefined}>
        <i className="legend-symbol is-line" style={{ color: `var(${CLASS_TOKEN[kind]})` }} />{CLASS_LABEL[kind]} ({counts[kind].toLocaleString()})</span>)
        : <>{Array.from({ length: study.replications }, (_, k) => <span key={k} className="legend-key"><i className="legend-symbol is-line" style={{ color: `var(--study-share-${k + 1})` }} />{k + 1}</span>)}
          <span className="legend-key">of {study.replications} replications sectionalized</span></>}
    </div></div>
  </div>;
}

function circuitTooltip(data: StudyCircuits, i: number, status: string) {
  const element = document.createElement('div');
  const lines = [data.unit[i], `Circuit ${data.circuitId[i]} · ${data.division[i]} · HFTD Tier ${data.tier[i] ?? 'none'}`, `Population ${data.population[i].toLocaleString()}`, status];
  lines.forEach((line, index) => { const row = document.createElement(index === 0 ? 'strong' : 'div'); row.textContent = line; element.appendChild(row); });
  return element;
}

function SummaryView({ study }: { study: Study }) {
  const { settings } = usePanel();
  const setup = setupByIdOrDefault(study, settings.studySetup);
  const { variant } = usePlanVariant();
  const proposed = methodIndex(study, PROPOSED_METHOD);
  const metric = (id: MetricId) => spread(study, setup, proposed, id, variant);
  const share = allocation(study, setup, proposed);
  const covered = study.data.coverage[PROPOSED_METHOD];
  const wins = useMemo(() => ({ co: winShare(study, proposed, methodIndex(study, 'co_optimized'), variant), plan: winShare(study, proposed, methodIndex(study, 'planning_only'), variant), rank: rankingWinShare(study, proposed, variant) }), [study, proposed, variant]);
  const items: { id: string; label: string; value: string; note?: string }[] = [
    { id: 'rr', label: `Risk reduction, ${study.data.methods[proposed].label.toLowerCase()}`, value: formatMetric('percent', metric('risk_reduction').mean), note: metricDef('risk_reduction').description },
    { id: 'co', label: 'Gain over co-optimized', value: formatMetric('percent', metric('gain_co_optimized').mean), note: metricDef('gain_co_optimized').description },
    { id: 'plan', label: 'Gain over planning only', value: formatMetric('percent', metric('gain_planning').mean), note: metricDef('gain_planning').description },
    { id: 'sect', label: 'Circuits sectionalized', value: formatMetric('count', metric('sectionalized').mean) },
    { id: 'cap', label: variant === 'filled' ? 'SAIFI cap used by PSPS, as solved' : 'SAIFI cap used by PSPS', value: share ? `${(share.psps * 100).toFixed(0)}%` : 'No data', note: 'Mean share of the SAIFI cap spent on test-year PSPS by the solved plan.' },
    { id: 'cov', label: 'Ignitions inside the uncertainty set', value: covered ? `${covered.filter(Boolean).length} of ${covered.length}` : 'No data', note: 'Replications in which every circuit and group count fell inside the set.' },
    { id: 'rank', label: 'Risk reduction, ranking reference', value: formatMetric('percent', rankingSpread(study, setup, 'risk_reduction').mean), note: RANKING_NOTE },
  ];
  return <div className="analysis-chart stat-panel study-summary">
    <ExportActions datasets={[]} rows={() => items.map(item => ({ ...setupRow(study, setup), plans: variant, metric: item.label, value: item.value }))} />
    <div className="stat-toolbar"><PlanToggle /><SetupPicker study={study} setup={setup} /></div>
    <dl className="stat-metrics" aria-label="Planning study summary">
      {items.map(item => <div key={item.id} className="stat-metric" title={item.note}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}
    </dl>
    <p className="panel-note">Across all {study.data.source.complete_setups} setups and {study.replications} replications{variant === 'filled' ? ', with every plan\'s unused sectionalization budget filled' : ''}, the proposed plan left less exposure than co-optimized in {(wins.co.share * 100).toFixed(1)}% of {wins.co.pairs} paired draws, less than planning only in {(wins.plan.share * 100).toFixed(1)}%, and less than the ranking reference in {(wins.rank.share * 100).toFixed(1)}%.</p>
  </div>;
}
