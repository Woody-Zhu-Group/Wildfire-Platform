import { useEffect, useMemo, useRef, useState } from 'react';
import { getRegionalSeries } from './workspaceAggregates.ts';
import { filterError, unavailableReason, type Interval } from './data.ts';
import { soleUtilityLabel } from './coverage.ts';
import { ChartFilters, LoadState } from './Controls';
import { ExportActions } from './ExportActions';
import { regionalSvg } from './exports.ts';
import { TemporalPlot } from './TemporalPlot';
import { usePanel } from './state';
import { useRemote } from './useRemote';

export function RegionalSeries() {
  const {settings,update,expanded,expand,title}=usePanel();
  const {filters,interval}=settings;
  const validation=filterError(filters)||unavailableReason('epss',filters);
  const remote=useRemote(validation?null:JSON.stringify(['regional',filters,interval]),()=>getRegionalSeries(filters,interval));
  const series=useMemo(()=>remote.data?.series??[],[remote.data]);
  const grid=useRef<HTMLDivElement>(null);
  const [size,setSize]=useState({width:600,height:340});
  const [inspected,setInspected]=useState<{name:string;index:number}|null>(null);
  useEffect(()=>{const observer=new ResizeObserver(([entry])=>setSize({width:entry.contentRect.width,height:entry.contentRect.height}));observer.observe(grid.current!);return()=>observer.disconnect();},[]);
  useEffect(()=>setInspected(null),[series]);
  const columns=size.width>=1000?3:size.width>=460?2:1;
  const capacity=Math.max(1,Math.floor((size.height+12)/132))*columns;
  const visible=expanded?series:series.slice(0,capacity);
  const ceiling=Math.ceil(Math.max(4,...series.flatMap(region=>region.buckets.map(bucket=>bucket.count)))/4)*4;
  const error=validation||remote.error;
  const ready=!error&&!remote.loading&&series.length>0;
  const active=inspected?series.find(region=>region.name===inspected.name):null;
  const bucket=active&&inspected?active.buckets[inspected.index]:null;
  const whose=soleUtilityLabel('epss_outages')??'';
  const caption=`${whose} EPSS; ${filters.start} – ${filters.end}; ${filters.county||'All counties'}; ${interval} outage counts by division. Shared vertical scale.`;
  return <div className="analysis-chart regional-panel">
    <ExportActions datasets={['epss']} disabled={!ready} rows={()=>series.flatMap(region=>region.buckets.map(bucket=>({dataset:'EPSS',division:region.name,period_start:bucket.start,period_end:bucket.end,outages:bucket.count,utility:whose,county:filters.county})))} svg={()=>regionalSvg(title,caption,series)}/>
    <div className="regional-toolbar"><span>{whose} · EPSS</span><label>Interval<select aria-label="Regional time interval" value={interval} onChange={event=>update({interval:event.target.value as Interval})}><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option></select></label></div>
    <ChartFilters filters={filters} dataset="epss" onChange={filters=>update({filters})}/>
    <div className="regional-meta"><span>{ready?`${series.length} divisions`:''}</span><span>Same scale · Outages</span></div>
    <div ref={grid} className="regional-grid" style={{gridTemplateColumns:`repeat(${columns},minmax(0,1fr))`}}>
      {!ready?<LoadState loading={remote.loading} error={error} retry={remote.error?remote.retry:undefined}/>:visible.map(region=><section key={region.name} className="region-small-multiple" aria-label={`${region.name} outages`}>
        <header><h3>{region.name}</h3><span>{region.total.toLocaleString()}</span></header>
        <TemporalPlot compact unit="Outages" values={region.buckets.map(bucket=>bucket.count)} labels={region.buckets.map(bucket=>bucket.start.slice(0,4)===filters.start.slice(0,4)&&filters.start.slice(0,4)===filters.end.slice(0,4)?bucket.start.slice(5):bucket.start)} ceiling={ceiling} onInspect={index=>setInspected(index===null?null:{name:region.name,index})}/>
      </section>)}
    </div>
    <div className="regional-readout" aria-live="polite">{bucket?<span title={`${active!.name} · ${bucket.start} – ${bucket.end}`}><strong>{bucket.count} outages</strong> · {active!.name} · {bucket.start} – {bucket.end}</span>:<span>{ready?`${remote.data!.total.toLocaleString()} outages`:''}</span>}
      {ready&&!expanded&&visible.length<series.length&&<button className="text-button" onClick={expand}>Showing {visible.length} of {series.length} · View all →</button>}
    </div>
  </div>;
}
