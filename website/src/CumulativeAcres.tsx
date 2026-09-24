import { useRef, useState } from 'react';
import { getRecords, getSummary } from './api.ts';
import { ChartFilters, LoadState } from './Controls';
import { cumulativeAcres, cumulativeCaption } from './cumulative.ts';
import { ExportActions } from './ExportActions';
import { lineSvg } from './exports.ts';
import { usePanel } from './state';
import { TemporalPlot } from './TemporalPlot';
import { useRemote } from './useRemote';

export function CumulativeAcres() {
  const {settings, update, title} = usePanel();
  const {filters} = settings;
  const plot = useRef<HTMLDivElement>(null);
  const [inspected, setInspected] = useState<number | null>(null);
  const remote = useRemote(JSON.stringify(['cumulative-acres', filters]), async () => {
    const [events, summary] = await Promise.all([
      getRecords('calfire', filters),
      getSummary('calfire', filters),
    ]);
    const result = cumulativeAcres(events, filters.start, filters.end);
    const eventTotal = summary.find(metric => metric.id === 'events')?.value;
    const acresTotal = summary.find(metric => metric.id === 'acres')?.value;
    if (eventTotal !== events.length || acresTotal !== result.total) {
      throw new Error('Cumulative acres do not match the server summary.');
    }
    return {...result, eventTotal};
  });
  const points = remote.data?.points ?? [];
  const values = points.map(point => point.acres);
  const ceiling = Math.max(1, remote.data?.total ?? 0);
  const active = inspected === null ? null : points[inspected];
  const caption = cumulativeCaption(filters.start, filters.end, remote.data?.missing);
  return <div className="analysis-chart seasonal-panel">
    <ExportActions datasets={['calfire']} disabled={Boolean(remote.error||remote.loading||!points.length)}
      rows={()=>points.map(point=>({dataset:'CAL FIRE',date:point.date,cumulative_acres:point.acres,period_start:filters.start,period_end:filters.end,utility:filters.utility,county:filters.county}))}
      svg={()=>{const svg=plot.current?.querySelector('svg');if(!svg)throw new Error('Chart is not ready.');return lineSvg(svg,title,caption,[{label:'Cumulative reported acres',color:'#ee8585'}]);}}/>
    <ChartFilters filters={filters} dataset="calfire" onChange={filters=>{update({filters});setInspected(null);}}/>
    <p className="panel-note">Observed CAL FIRE incident-map records; cumulative reported acres, not a model prediction.</p>
    <div ref={plot} className="seasonal-plot">{remote.data
      ? <TemporalPlot values={values} labels={points.map(point=>point.date)} ceiling={ceiling} unit="Cumulative acres" onInspect={setInspected}/>
      : <LoadState loading={remote.loading} error={remote.error} retry={remote.error?remote.retry:undefined}/>}</div>
    {remote.data && <div className="series-readout seasonal-readout" aria-live="polite"><span>{active?.date ?? `${filters.start} – ${filters.end}`}</span><div><strong>{(active?.acres ?? remote.data.total).toLocaleString()} cumulative acres</strong><span>{remote.data.eventTotal.toLocaleString()} incidents · {remote.data.missing.toLocaleString()} missing acreage</span></div></div>}
  </div>;
}
