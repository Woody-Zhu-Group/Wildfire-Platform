import { useEffect, useId, useRef, useState } from 'react';
import { COUNTIES, DATASETS, UTILITIES, configFor, filterSupport, type DatasetId, type Filters } from './data.ts';
import { getCoverage } from './api.ts';
import { useRemote } from './useRemote';

export function DatasetSelect({ value, onChange, label = 'Dataset', hideLabel = false, all = true }: { value: DatasetId; onChange: (value: DatasetId) => void; label?: string; hideLabel?: boolean; all?: boolean }) {
  return <label>{!hideLabel && label}<select aria-label={label} value={value} onChange={e => onChange(e.target.value as DatasetId)}>{(all ? DATASETS : DATASETS.slice(0, 3)).map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>;
}
interface YearSelection { available: number[]; onChange: (years: number[]) => void }
export function YearOptions({available, selected, onChange}: {available: number[]; selected: number[]; onChange: (years: number[]) => void}) {
  return <div className="year-options">{available.map(year => <label key={year}><input type="checkbox" checked={selected.includes(year)} disabled={!selected.includes(year) && selected.length >= 5}
    onChange={() => onChange(selected.includes(year) ? selected.filter(value => value !== year) : [...selected, year])}/>{year}</label>)}</div>;
}
export function ChartFilters({ filters, onChange, dataset, datasets, years, yearSelection }: { filters: Filters; onChange: (filters: Filters) => void; dataset?: DatasetId; datasets?: readonly DatasetId[]; years?: number[]; yearSelection?: YearSelection }) {
  const [open, setOpen] = useState(false);
  const summary = years ? yearSelection ? `${years.length} ${years.length === 1 ? 'year' : 'years'}` : years.join(' / ') : `${filters.start} – ${filters.end}`;
  return <div className="chart-filter-drawer">
    <button className="filter-trigger" aria-label="Filters" aria-haspopup="dialog" onClick={() => setOpen(true)}><span>Filters</span><span className="filter-summary">{summary}{filters.county && ` · ${filters.county}`}{filters.utility && ` · ${filters.utility}`}</span></button>
    {open && <FilterDialog filters={filters} onChange={onChange} dataset={dataset} datasets={datasets} years={years} yearSelection={yearSelection} onClose={() => setOpen(false)} />}
  </div>;
}
function FilterDialog({ filters, onChange, dataset, datasets, years, yearSelection, onClose }: { filters: Filters; onChange: (filters: Filters) => void; dataset?: DatasetId; datasets?: readonly DatasetId[]; years?: number[]; yearSelection?: YearSelection; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();
  const support = filterSupport(dataset ? [dataset] : datasets ?? []);
  const countyNote = `${title}-county-note`, utilityNote = `${title}-utility-note`;
  useEffect(() => {
    const element = dialog.current!;
    const root = document.documentElement; const previousOverflow = root.style.overflow;
    root.style.overflow = 'hidden'; element.showModal();
    return () => { element.close(); root.style.overflow = previousOverflow; };
  }, []);
  return <dialog ref={dialog} className="filter-dialog analysis-chart" aria-labelledby={title} onCancel={event => { event.stopPropagation(); onClose(); }}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}><div onClick={event => event.stopPropagation()}>
    <header><h2 id={title}>{dataset ? `${configFor(dataset).name} filters` : 'Time series filters'}</h2><button aria-label="Close filters" onClick={onClose}>×</button></header>
    {yearSelection && <fieldset className="filter-years"><legend>Years</legend><YearOptions available={yearSelection.available} selected={years??[]} onChange={yearSelection.onChange}/></fieldset>}
    <div className="analysis-filters">
      {!years && <><label>From<input aria-label="Start date" type="date" value={filters.start} onChange={e => onChange({ ...filters, start: e.target.value })} /></label>
      <label>To<input aria-label="End date" type="date" value={filters.end} onChange={e => onChange({ ...filters, end: e.target.value })} /></label></>}
      <label>County<select aria-label="County" aria-describedby={!support.county ? countyNote : undefined} disabled={!support.county && !filters.county} value={filters.county} onChange={e => onChange({ ...filters, county: e.target.value })}><option value="">All counties</option>{COUNTIES.map(c => <option key={c} disabled={!support.county}>{c}</option>)}</select>
        {!support.county && <small id={countyNote} className="filter-reason">Not available for this dataset</small>}</label>
      <label>Utility<select aria-label="Utility" aria-describedby={support.utility !== 'all' ? utilityNote : undefined} disabled={support.utility === 'none' && !filters.utility} value={filters.utility} onChange={e => onChange({ ...filters, utility: e.target.value })}><option value="">All utilities</option>{UTILITIES.map(u => <option key={u} disabled={support.utility === 'none' || (support.utility === 'single' && u !== support.only)}>{u}</option>)}</select>
        {support.utility !== 'all' && <small id={utilityNote} className="filter-reason">{support.utility === 'single' ? `${support.only} only` : 'Not available for this dataset'}</small>}</label>
    </div>
    {dataset && <Coverage dataset={dataset} />}
    <button className="quiet-button" onClick={onClose}>Done</button>
  </div></dialog>;
}
function Coverage({ dataset }: { dataset: DatasetId }) {
  const result = useRemote(`coverage:${dataset}`, () => getCoverage(dataset));
  return <p className="panel-note">{result.data ? `Recorded dates: ${result.data.start ?? 'unavailable'} – ${result.data.end ?? 'unavailable'}` : result.error ? 'Date coverage unavailable.' : 'Checking recorded date range…'}</p>;
}
export function LoadState({ loading, error, retry }: { loading?: boolean; error?: string | null; retry?: () => void }) {
  return <div className={`chart-empty ${error ? 'load-error' : ''}`} role="status">{error ? <div><p>{error}</p>{retry && <button className="quiet-button" onClick={retry}>Retry</button>}</div> : loading ? <span className="loading-text">Loading records…</span> : 'No matching records. Try another date range or filter.'}</div>;
}
function SourceRow({ dataset }: { dataset: DatasetId }) {
  const result = useRemote(`coverage:${dataset}`, () => getCoverage(dataset));
  return <div className="source-row"><strong>{configFor(dataset).name}</strong><span>{result.data ? `${result.data.total.toLocaleString()} dated records · ${result.data.start} – ${result.data.end}` : result.error ?? 'Checking coverage…'}</span></div>;
}
export function DataSources() {
  const [open, setOpen] = useState(false);
  return <footer className="data-sources"><details onToggle={e => setOpen(e.currentTarget.open)}><summary>Data sources & coverage</summary>
    {open && <>{DATASETS.map(d => <SourceRow key={d.id} dataset={d.id} />)}<p>Loaded from the remote wildfire warehouse. These are recorded event dates; last scrape times are not provided by the service.</p><p>CPUC is utility-attributed. CAL FIRE contains posted incidents. National ignitions are an all-cause sample. Their counts should not be added together.</p></>}
  </details></footer>;
}
