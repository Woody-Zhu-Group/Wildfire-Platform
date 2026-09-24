import { useContext, useEffect, useRef, useState } from 'react';
import { getDetail, getRecords } from './api.ts';
import { getSummary } from './workspaceAggregates.ts';
import { configFor, filterError, unavailableReason, type EventRecord } from './data.ts';
import { soleUtilityLabel } from './coverage.ts';
import { ChartFilters, DatasetSelect, LoadState } from './Controls';
import { SelectionContext, usePanel } from './state';
import { useRemote } from './useRemote';
import { useRowCapacity } from './useRowCapacity';
import { ExportActions } from './ExportActions';
import { medicalExposureMetrics } from './exposure.ts';
import { ModelMetricsCard } from './ModelMetricsCard';

export function RecordTable() {
  const { settings, update, expanded } = usePanel(); const { dataset, filters } = settings;
  const { inspect } = useContext(SelectionContext);
  const [query, setQuery] = useState(''); const [offset, setOffset] = useState(0);
  const viewport = useRef<HTMLDivElement>(null);
  const validation = filterError(filters) || unavailableReason(dataset, filters);
  const remote = useRemote(validation ? null : JSON.stringify(['records', dataset, filters]), () => getRecords(dataset, filters));
  const events = remote.data ?? [];
  const rows = events.filter(e => [e.name,e.county,e.utility,e.cause,e.id].join(' ').toLowerCase().includes(query.toLowerCase()));
  const capacity = useRowCapacity(viewport, 'tbody tr', rows.length > 0, 'thead');
  const pageSize = expanded ? 25 : capacity;
  const current = Math.min(offset, Math.max(0, rows.length - 1));
  useEffect(() => setOffset(0), [dataset, filters, query]);
  useEffect(() => { if (viewport.current) viewport.current.scrollTop = 0; }, [current, pageSize]);
  return <div className="analysis-chart records-panel"><div className="record-toolbar"><DatasetSelect hideLabel value={dataset} onChange={dataset => update({ dataset })} /><ChartFilters filters={filters} onChange={filters => update({ filters })} dataset={dataset} /></div>
    <ExportActions datasets={[dataset]} disabled={Boolean(validation||remote.error||remote.loading||!rows.length)} rows={()=>rows.map(record=>({dataset:configFor(dataset).name,...record.properties}))} />
    <input className="record-search" aria-label="Filter records" placeholder="Filter by event, county, utility, or cause…" value={query} onChange={e => setQuery(e.target.value)} />
    {validation || remote.error || remote.loading ? <LoadState loading={remote.loading} error={validation || remote.error} retry={remote.error ? remote.retry : undefined} /> : <>
      <div ref={viewport} className="record-scroll"><table>
        <colgroup><col className="record-col-event"/><col className="record-col-county"/><col className="record-col-date"/><col className="record-col-value"/></colgroup>
        <thead><tr><th scope="col">Event</th><th scope="col">County</th><th scope="col">Date</th><th scope="col">{dataset === 'calfire' ? 'Acres' : 'Utility'}</th></tr></thead>
        <tbody key={`${dataset}-${current}-${pageSize}-${query}`}>{rows.slice(current, current + pageSize).map(e => <tr key={e.id}><td><button className="record-link" title={e.name} onClick={() => inspect(e)}>{e.name}</button></td><td title={e.county ?? undefined}>{e.county ?? '—'}</td><td>{e.date || '—'}</td><td>{dataset === 'calfire' ? e.acres?.toLocaleString() ?? '—' : e.utility ?? '—'}</td></tr>)}</tbody>
      </table></div>
      {!rows.length && <p className="panel-note">No matching records.</p>}
      <div className="record-pagination"><span>{rows.length ? `${current + 1}–${Math.min(current + pageSize, rows.length)}` : '0'} of {rows.length.toLocaleString()} records{query && ` (${events.length.toLocaleString()} before search)`}</span><div><button aria-label="Previous page" disabled={current === 0} onClick={() => setOffset(Math.max(0, current - pageSize))}>←</button><button aria-label="Next page" disabled={current + pageSize >= rows.length} onClick={() => setOffset(current + pageSize)}>→</button></div></div>
    </>}
  </div>;
}
export function StatCard() {
  const { settings, update } = usePanel(); const { dataset, filters, answerStat } = settings;
  if (settings.statMode === 'medical_exposure') return <MedicalExposureCard />;
  if (settings.statMode === 'model_metrics') return <ModelMetricsCard />;
  const validation = filterError(filters) || unavailableReason(dataset, filters);
  const remote = useRemote(validation || answerStat ? null : JSON.stringify(['summary', dataset, filters]), () => getSummary(dataset, filters));
  const metrics = remote.data ?? [];
  const statValue = answerStat?.value ?? null;
  // A missing or not-covered count shows "Not available" and why, never 0.
  if (answerStat && statValue === null) return <div className="stat-content"><p className="panel-note">{answerStat.scope} · {answerStat.period}</p><div className="stat-value stat-unavailable">Not available<span>{answerStat.label}</span></div><p className="panel-note">{answerStat.unavailableReason}</p></div>;
  if (answerStat && statValue !== null) return <div className="stat-content"><p className="panel-note">{answerStat.scope} · {answerStat.period}</p><div className="stat-value">{(answerStat.unit === 'risk' ? statValue * 100 : statValue).toLocaleString(undefined, { maximumFractionDigits: 2 })}{answerStat.unit === 'risk' ? '%' : ''}<span>{answerStat.label}{answerStat.unit === 'percentile' ? ' · percentile' : ''}</span></div><p className="panel-note">From the agent's cited result.</p></div>;
  return <div className="analysis-chart stat-panel"><div className="stat-toolbar"><DatasetSelect hideLabel value={dataset} onChange={dataset => update({ dataset })} /><ChartFilters filters={filters} onChange={filters => update({ filters })} dataset={dataset} /></div>
    <ExportActions datasets={[dataset]} disabled={Boolean(validation||remote.error||remote.loading)} rows={()=>metrics.map(metric=>({dataset:configFor(dataset).name,metric:metric.id,value:metric.value,missing_records:metric.missing,unit:metric.unit,...filters}))} />
    {validation || remote.error || remote.loading ? <LoadState loading={remote.loading} error={validation || remote.error} retry={remote.error ? remote.retry : undefined} /> : <dl className="stat-metrics" aria-label={`${configFor(dataset).name} summary`}>
      {metrics.map(metric => <div key={metric.id} className="stat-metric"><dt>{metric.label}{metric.missing > 0 && <span className="stat-missing" title={`${metric.missing} records have no value for this metric`}> · {metric.missing} missing</span>}</dt><dd>{metric.value === null ? '—' : metric.value.toLocaleString(undefined, { maximumFractionDigits: 1 })}</dd></div>)}
    </dl>}
  </div>;
}
function MedicalExposureCard() {
  const { settings, update } = usePanel();
  const { filters } = settings;
  const validation = filterError(filters) || unavailableReason('epss', filters);
  const remote = useRemote(validation ? null : JSON.stringify(['medical-exposure', filters]), () => getRecords('epss', filters));
  const metrics = medicalExposureMetrics(remote.data ?? []);
  return <div className="analysis-chart stat-panel">
    <div className="stat-toolbar"><ChartFilters filters={filters} onChange={filters => update({ filters })} dataset="epss" /></div>
    <ExportActions datasets={['epss']} disabled={Boolean(validation||remote.error||remote.loading)} rows={()=>metrics.map(metric=>({dataset:'EPSS',metric:metric.id,value:metric.value,missing_records:metric.missing,unit:metric.unit,...filters}))} />
    {validation || remote.error || remote.loading ? <LoadState loading={remote.loading} error={validation || remote.error} retry={remote.error ? remote.retry : undefined} /> : <>
      <p className="panel-note">Exposure during {soleUtilityLabel('epss_outages')} EPSS outages; totals are customer-events, not deduplicated customers.</p>
      <dl className="stat-metrics" aria-label="EPSS medical baseline and life support exposure">
        {metrics.map(metric => <div key={metric.id} className="stat-metric"><dt>{metric.label}{metric.missing > 0 && <span className="stat-missing" title={`${metric.missing} records have no value for this metric`}> · {metric.missing} missing</span>}</dt><dd>{metric.value === null ? <span title={`None of the ${metric.missing} outages has a value for this metric`}>Not available</span> : metric.value.toLocaleString()}</dd></div>)}
      </dl>
    </>}
  </div>;
}
function DetailFields({attributes}: {attributes: Record<string, unknown>}) {
  return <dl>{Object.entries(attributes).filter(([key]) => !['geom', 'geometry'].includes(key)).map(([key, value]) => <div key={key}>
    <dt>{key.replaceAll('_', ' ')}</dt><dd>{value === null || value === undefined ? 'No data' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd>
  </div>)}</dl>;
}
function RelatedRecords({kind, rows}: {kind: 'outages' | 'circuits'; rows?: Record<string, unknown>[] | null}) {
  const label = kind === 'outages' ? 'Outages' : 'Affected circuits';
  return <section className="detail-related" aria-label={label}>
    <h3>{label}{rows && <span> · {rows.length.toLocaleString()}</span>}</h3>
    {!rows ? <p className="panel-note">No data</p> : !rows.length ? <p className="panel-note">{kind === 'outages' ? 'No outages recorded for this period.' : 'No affected circuits recorded.'}</p> : rows.map((row, index) => {
      const title = kind === 'outages' ? `Outage ${row.id ?? index + 1}${row.start_date ? ` · ${row.start_date}` : ''}`
        : `${row.circuit_id ?? 'Circuit'}${row.circuit_name ? ` · ${row.circuit_name}` : ''}`;
      return <details key={String(row.id ?? row.circuit_id ?? index)} className="detail-related-record"><summary>{title}</summary><DetailFields attributes={row} /></details>;
    })}
  </section>;
}
export function EventDetail({ record, onClose }: { record: EventRecord; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const scope = record.properties.circuit_detail ? { start: String(record.properties.scope_start), end: String(record.properties.scope_end) } : undefined;
  const result = useRemote(JSON.stringify(['detail', record.dataset, record.id, scope]), () => getDetail(record.dataset, record.id, scope));
  useEffect(() => { const element = dialog.current!; element.showModal(); return () => element.close(); }, []);
  const attributes = result.data?.attributes ?? {};
  return <dialog ref={dialog} className="event-dialog" aria-labelledby="event-detail-title" onCancel={onClose} onClick={e => { if (e.target === e.currentTarget) onClose(); }}><div onClick={e => e.stopPropagation()}>
    <header><div><p>{configFor(record.dataset).name} · {scope ? 'Circuit' : 'Event'} detail</p><h2 id="event-detail-title">{record.name}</h2></div><button aria-label="Close event detail" onClick={onClose}>×</button></header>
    {result.loading || result.error ? <LoadState loading={result.loading} error={result.error} retry={result.retry} /> : <>
      <DetailFields attributes={attributes} />
      {scope && <RelatedRecords kind="outages" rows={result.data?.outages} />}
      {record.dataset === 'psps' && <RelatedRecords kind="circuits" rows={result.data?.affected_circuits} />}
    </>}
  </div></dialog>;
}
