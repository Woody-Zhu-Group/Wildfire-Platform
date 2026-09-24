import { useEffect, useId, useRef, useState } from "react";
import { CHART_DATASETS as DATASETS, filterError, aggregateDaily, unavailableReason, datasetNote,
  type DatasetId, type Interval } from './data.ts';
import { getDailySeries } from './api.ts';
import { getGroupedCounts } from './workspaceAggregates.ts';
import { ChartFilters, LoadState } from './Controls';
import { usePanel } from './state';
import { useRemote } from './useRemote';
import { useRowCapacity } from './useRowCapacity';
import { YearComparison } from './YearComparison';
import { RegionalSeries } from './RegionalSeries';
import { SeasonalSeries } from './SeasonalSeries';
import { ExportActions } from './ExportActions';
import { lineSvg, barSvg, type ExportRow } from './exports.ts';
import { CumulativeAcres } from './CumulativeAcres.tsx';
import { CustomerEventsSeries } from './CustomerEventsSeries.tsx';

export function TimeSeries() {
  const { settings } = usePanel();
  if (settings.seriesMode === 'regional') return <RegionalSeries />;
  if (settings.seriesMode === 'seasonal') return <SeasonalSeries />;
  if (settings.seriesMode === 'cumulative_acres') return <CumulativeAcres />;
  if (settings.seriesMode === 'customer_events') return <CustomerEventsSeries />;
  return settings.seriesMode === 'yearly' ? <YearComparison /> : <TimelineSeries />;
}
function TimelineSeries() {
  const { settings, update, title } = usePanel();
  const { filters, interval, datasets: selected } = settings;
  const setFilters = (filters: typeof settings.filters) => update({ filters });
  const setInterval = (interval: Interval) => update({ interval });
  const [hovered, setHovered] = useState<number | null>(null);
  const epssNote = useId();
  const epssBlocked = !selected.includes('epss') && Boolean(unavailableReason('epss', filters));
  const plot = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(540);
  const [height, setHeight] = useState(246);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => { setWidth(Math.max(240, entry.contentRect.width)); setHeight(Math.max(120, entry.contentRect.height)); });
    observer.observe(plot.current!);
    return () => observer.disconnect();
  }, []);

  const error = filterError(filters);
  const remote = useRemote(error || !selected.length ? null : JSON.stringify(['series', filters, interval, selected]), () => Promise.all(
    DATASETS.filter(d => selected.includes(d.id)).map(async dataset => {
      const reason = unavailableReason(dataset.id, filters);
      const buckets = reason ? [] : aggregateDaily(await getDailySeries(dataset.id, filters), interval);
      return { ...dataset, buckets, values: buckets.map(b => b.count), total: buckets.reduce((sum, b) => sum + b.count, 0), reason };
    })
  ));
  const series = remote.data ?? [];
  const buckets = series.find(d => !d.reason)?.buckets ?? [];
  const visible = series.filter(dataset => !dataset.reason);
  const max = Math.max(4, ...visible.flatMap(dataset => dataset.values));
  const ceiling = Math.ceil(max / 4) * 4;
  const left = 36, right = width - 16, top = 28, bottom = height - 36;
  const x = (index: number) => buckets.length <= 1 ? (left + right) / 2 : left + index / (buckets.length - 1) * (right - left);
  const y = (value: number) => bottom - value / ceiling * (bottom - top);
  const tickIndices = [...new Set(Array.from({ length: Math.min(5, buckets.length) }, (_, i) => Math.round(i * (buckets.length - 1) / Math.max(1, Math.min(5, buckets.length) - 1))))];
  const activeIndex = hovered === null || !buckets.length ? null : Math.min(hovered, buckets.length - 1);
  const empty = error || remote.error || (remote.loading ? "Loading records…" : null) || (!selected.length ? "Select at least one dataset to show a line." : !visible.length ? "No data is available for the selected datasets and utility." : null);

  return <div className="analysis-chart series-panel">
    <ExportActions datasets={selected} disabled={Boolean(empty)} rows={()=>series.flatMap<ExportRow>(d=>d.reason ? [{dataset:d.name,period_start:filters.start,period_end:filters.end,count:null,utility:filters.utility,county:filters.county,unavailable_reason:d.reason}] : d.buckets.map(b=>({dataset:d.name,period_start:b.start,period_end:b.end,count:b.count,utility:filters.utility,county:filters.county,unavailable_reason:''})))}
      svg={()=>{const svg=plot.current?.querySelector('svg');if(!svg)throw new Error('Chart is not ready.');return lineSvg(svg,title,`${filters.start} – ${filters.end}; ${filters.utility||'All utilities'}; ${filters.county||'All counties'}; ${interval} event counts. CAL FIRE posting coverage varies by year. ${series.filter(d=>d.reason).map(d=>d.reason).join(' ')}`,visible.map(d=>({label:`${d.name}: ${d.total}`,color:d.color})));}} />
    <div className="analysis-heading series-toolbar">
    <div className="dataset-switches" role="group" aria-label="Visible datasets">
      {DATASETS.map(dataset => <button key={dataset.id} type="button" role="checkbox" aria-checked={selected.includes(dataset.id)} aria-label={dataset.name}
        disabled={dataset.id === 'epss' && epssBlocked} aria-describedby={dataset.id === 'epss' && epssBlocked ? epssNote : undefined}
        title={dataset.name} onClick={() => update({ datasets: selected.includes(dataset.id) ? selected.filter(id => id !== dataset.id) : [...selected, dataset.id] })}>
        <span className="dataset-swatch" style={{ background: dataset.color }} />{dataset.name}
        <span aria-hidden="true" className="dataset-check">{selected.includes(dataset.id) ? "✓" : "−"}</span>
      </button>)}
      {epssBlocked && <small id={epssNote} className="filter-reason">EPSS: PG&E only</small>}
    </div>
    <label>Interval<select aria-label="Time interval" value={interval} onChange={event => { setInterval(event.target.value as Interval); setHovered(null); }}><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option></select></label>
    </div>
    <ChartFilters filters={filters} datasets={selected} onChange={next => { setFilters(next); setHovered(null); }} />
    <div ref={plot} className="series-plot">
      {empty ? <LoadState loading={remote.loading} error={remote.loading ? null : empty} retry={remote.error ? remote.retry : undefined} /> : <svg viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0}
        aria-label={`${interval} event counts. Use left and right arrows to inspect periods.`}
        onMouseLeave={() => setHovered(null)} onMouseMove={event => {
          const bounds = event.currentTarget.getBoundingClientRect();
          const position = (event.clientX - bounds.left) / bounds.width * width;
          setHovered(Math.max(0, Math.min(buckets.length - 1, Math.round((position - left) / (right - left) * (buckets.length - 1)))));
        }} onKeyDown={event => {
          if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
            event.preventDefault();
            setHovered(Math.max(0, Math.min(buckets.length - 1, (activeIndex ?? 0) + (event.key === "ArrowRight" ? 1 : -1))));
          }
        }}>
        <text x={left} y="13">Events</text>
        {[0, 1, 2, 3, 4].map(i => <g key={i}><path d={`M${left} ${y(ceiling * i / 4)}H${right}`} stroke="var(--chart-grid)" /><text x={left - 9} y={y(ceiling * i / 4) + 4} textAnchor="end">{ceiling * i / 4}</text></g>)}
        {tickIndices.map(index => <text key={index} x={x(index)} y={height - 11} textAnchor={index === 0 ? "start" : index === buckets.length - 1 ? "end" : "middle"}>{filters.start.slice(0, 4) === filters.end.slice(0, 4) ? buckets[index].start.slice(5) : buckets[index].start.slice(0, 7)}</text>)}
        {visible.map(dataset => <g key={dataset.id}>
          <polyline points={dataset.values.map((value, index) => `${x(index)},${y(value)}`).join(" ")} fill="none" stroke={dataset.color} strokeWidth="2" strokeLinejoin="round" />
          {dataset.values.length <= 24 && dataset.values.map((value, index) => <circle key={index} cx={x(index)} cy={y(value)} r="3" fill={dataset.color}><title>{dataset.name}: {buckets[index].start} – {buckets[index].end}: {value} events</title></circle>)}
        </g>)}
        {activeIndex !== null && <g><path d={`M${x(activeIndex)} ${top}V${bottom}`} stroke="var(--chart-cursor)" strokeDasharray="3 4" />{visible.map(dataset => <circle key={dataset.id} cx={x(activeIndex)} cy={y(dataset.values[activeIndex])} r="4" fill={dataset.color} stroke="var(--plot-marker-stroke)" strokeWidth="2" />)}</g>}
      </svg>}
    </div>
    {!empty && <div className="series-readout" aria-live="polite"><span>{activeIndex === null ? "Selected period" : `${buckets[activeIndex].start} – ${buckets[activeIndex].end}`}</span><div>{visible.map(dataset => <span key={dataset.id} style={{ color: dataset.color }}>{dataset.name} <strong>{activeIndex === null ? dataset.total : dataset.values[activeIndex]}</strong></span>)}</div></div>}
    {series.filter(dataset => dataset.reason).map(dataset => <p key={dataset.id} className="chart-notice" role="status">{dataset.reason}</p>)}
  </div>;
}

export function Comparison() {
  const { settings, update, expanded, expand, title } = usePanel();
  const { dataset, groupBy, measure, filters } = settings;
  const setFilters = (filters: typeof settings.filters) => update({ filters });
  const setDataset = (dataset: DatasetId) => update({ dataset });
  const setMeasure = (measure: 'count' | 'share') => update({ measure });
  const config = DATASETS.find(item => item.id === dataset)!;
  const validation = filterError(filters) || unavailableReason(dataset, filters)
    || (groupBy === 'cause' && !config.hasCause ? `Cause data is not available for ${config.name}. Choose Utility or County.` : null);
  const remote = useRemote(validation ? null : JSON.stringify(['grouped-counts', dataset, groupBy, filters]), () => getGroupedCounts(dataset, filters, groupBy));
  const total = remote.data?.total ?? 0;
  const error = validation || remote.error;
  const rows = error ? [] : remote.data?.rows ?? [];
  const viewport = useRef<HTMLDivElement>(null);
  const capacity = useRowCapacity(viewport, '.analysis-bar-row', rows.length > 0);
  const visibleRows = expanded ? rows : rows.slice(0, capacity);
  const max = Math.max(1, ...rows.map(row => row.value ?? 0));
  return <div className="analysis-chart comparison-panel">
    <ExportActions datasets={[dataset]} disabled={Boolean(error||remote.loading||!total)} rows={()=>rows.map(row=>({dataset:config.name,group_by:groupBy,category:row.key,count:row.value,share_percent:row.value===null?null:row.value/total*100,period_start:filters.start,period_end:filters.end,utility:filters.utility,county:filters.county,unavailable_reason:row.value===null?'EPSS is PG&E-only':''}))}
      svg={()=>barSvg(title,`${config.name}; ${filters.start} – ${filters.end}; ${filters.utility||'All utilities'}; ${filters.county||'All counties'}; ${groupBy}; ${measure==='share'?'percent of selected records':'event count'}; ${total} records. ${datasetNote(dataset)}${remote.data?.note ? ` ${remote.data.note}` : ''}`,rows,total,config.color,measure==='share')} />
    <div className="comparison-toolbar">
    <div className="comparison-context">
      <label>Dataset<select aria-label="Comparison dataset" value={dataset} onChange={event => setDataset(event.target.value as DatasetId)}>{DATASETS.map(item => <option key={item.id} value={item.id} disabled={groupBy === 'cause' && !item.hasCause}>{item.name}</option>)}</select>
        {groupBy === 'cause' && <small className="filter-reason">Cause data: EPSS only</small>}</label>
      <span>By {groupBy}</span>
    </div>
    <div className="measure-switch" role="group" aria-label="Bar values"><button aria-pressed={measure === "count"} onClick={() => setMeasure("count")}>Count</button><button aria-pressed={measure === "share"} onClick={() => setMeasure("share")}>Share %</button></div>
    </div>
    <ChartFilters filters={filters} onChange={setFilters} dataset={dataset} />
    {error || remote.loading || !total ? <LoadState loading={remote.loading} error={error} retry={remote.error ? remote.retry : undefined} /> : <>
      <div className="bar-summary"><span>{config.name} · {total} events</span><span>{measure === "count" ? "Event count" : "% of selected records"}</span></div>
      {remote.data?.note && <small className="filter-reason">{remote.data.multi_county_incidents?.toLocaleString()} of these incidents list more than one county. {remote.data.note}</small>}
      <div ref={viewport} className="analysis-bars" role="list" aria-label="Grouped event counts">
        {visibleRows.map(row => {
          const share = (row.value ?? 0) / total * 100;
          return <div key={row.key} className="analysis-bar-row" role="listitem">
            <span className="bar-label" title={row.key}>{row.key}</span>
            <div className="bar-track">{row.value === null ? <span className="missing-bar" title="EPSS is PG&E-only">No data</span> : <div className="value-bar" style={{ background: row.key === "Not recorded" ? "#777" : config.color, width: `${measure === "count" ? row.value / max * 100 : share}%` }} />}</div>
            <span className="bar-value">{row.value === null ? "—" : <><strong>{measure === "count" ? row.value : `${share.toFixed(1)}%`}</strong><small>{measure === "count" ? `${share.toFixed(1)}%` : `${row.value} events`}</small></>}</span>
          </div>;
        })}
      </div>
      {!expanded && <div className="overview-more">{rows.length > visibleRows.length && <button className="text-button" onClick={expand}>Showing {visibleRows.length} of {rows.length} categories · View all →</button>}</div>}
    </>}
  </div>;
}
