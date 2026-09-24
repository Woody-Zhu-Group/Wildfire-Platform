import * as service from './api.ts';
import { aggregateDaily, asNumber, asText, configFor, groupNames, utilityCode, UTILITIES, type Bucket, type DatasetId, type Filters, type GroupBy, type Interval } from './data.ts';
import { coverageReason } from './coverage.ts';
import { readSummary, type SummaryResponse } from './stats.ts';
import type { RegionSeries } from './temporal.ts';

async function groupedFromRecords(dataset: DatasetId, filters: Filters, groupBy: GroupBy): Promise<service.GroupedCounts> {
  const events = await service.getRecords(dataset, filters);
  const counts = new Map<string, number>();
  // CAL FIRE by county counts a multi-county incident in every county it lists, as the data service does.
  const splitCounties = dataset === 'calfire' && groupBy === 'county';
  let multi = 0;
  for (const event of events) {
    const raw = event[groupBy] ?? 'Not recorded';
    const keys = splitCounties ? [...new Set(raw.split(',').map(part => part.trim()).filter(Boolean))] : [raw];
    if (keys.length > 1) multi += 1;
    for (const key of keys) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const keys = groupBy === 'utility' ? [...new Set([...(filters.utility ? [filters.utility] : UTILITIES), ...counts.keys()])] : [...counts.keys()];
  // A utility outside measured coverage for the period is null with its reason, never 0.
  const {query, name} = configFor(dataset);
  const uncovered = (key: string) => groupBy === 'utility' && !counts.has(key) ? coverageReason(query, name, utilityCode(key), filters.start, filters.end) : null;
  const rows = keys.map(key => { const reason = uncovered(key); return {key, ...groupNames(groupBy, key), value: reason ? null : counts.get(key) ?? 0, ...(reason ? {reason} : {})}; })
    .sort((a, b) => (b.value ?? -1) - (a.value ?? -1) || a.key.localeCompare(b.key));
  return multi ? {rows, total: events.length, multi_county_incidents: multi, note: MULTI_COUNTY_NOTE} : {rows, total: events.length};
}

export const MULTI_COUNTY_NOTE = 'A CAL FIRE incident that lists several counties (for example "Shasta, Tehama") is counted, with its full acreage, in every county it lists, so county totals can add up to more than the statewide total.';

async function summaryFromRecords(dataset: DatasetId, filters: Filters) {
  const events = await service.getRecords(dataset, filters);
  const metrics: SummaryResponse['metrics'] = [{id: 'events', value: events.length, missing: 0}];
  const distinct = (id: string, values: (string | null)[]) => {
    const known = values.filter(value => value !== null);
    metrics.push({id, value: known.length || !values.length ? new Set(known).size : null, missing: values.length - known.length});
  };
  if (dataset === 'calfire' || dataset === 'psps') {
    const id = dataset === 'calfire' ? 'acres' : 'customers';
    const values = events.map(event => id === 'acres' ? event.acres : asNumber(event.properties.customers_deenergized));
    metrics.push({id, value: !values.length || values.some(value => value !== null) ? values.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null, missing: values.filter(value => value === null).length});
  }
  if (dataset === 'epss') distinct('circuits', events.map(event => asText(event.properties.circuit_id)));
  if (['cpuc', 'calfire', 'epss'].includes(dataset)) distinct('counties', events.flatMap(event => event.county?.split(',').map(county => county.trim()) ?? [null]));
  if (dataset === 'cpuc' || dataset === 'psps') distinct('utilities', events.map(event => event.utility));
  return readSummary({total: events.length, metrics}, dataset);
}

async function regionalFromRecords(filters: Filters, interval: Interval): Promise<{series: RegionSeries[]; total: number}> {
  const events = await service.getRecords('epss', filters);
  const days: Bucket[] = [];
  for (let time = Date.parse(filters.start); time <= Date.parse(filters.end); time += 86400000) {
    const date = new Date(time).toISOString().slice(0, 10);
    days.push({start: date, end: date, count: 0});
  }
  const groups = new Map<string, Map<string, number>>();
  for (const event of events) {
    if (event.date < filters.start || event.date > filters.end) continue;
    const name = asText(event.properties.division)?.trim() || 'Not recorded';
    if (!groups.has(name)) groups.set(name, new Map());
    const counts = groups.get(name)!;
    counts.set(event.date, (counts.get(event.date) ?? 0) + 1);
  }
  const series = [...groups].map(([name, counts]) => {
    const buckets = aggregateDaily(days.map(day => ({...day, count: counts.get(day.start) ?? 0})), interval);
    return {name, buckets, total: buckets.reduce((sum, bucket) => sum + bucket.count, 0)};
  }).sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
  return {series, total: series.reduce((sum, region) => sum + region.total, 0)};
}

// Enable SQL aggregates only after their public URL has been configured for this build.
// Failures in that mode stay visible rather than switching data sources at runtime.
export function createWorkspaceAggregates(useDataQuery: boolean) {
  return useDataQuery
    ? {getGroupedCounts: service.getGroupedCounts, getSummary: service.getSummary, getRegionalSeries: service.getRegionalSeries}
    : {getGroupedCounts: groupedFromRecords, getSummary: summaryFromRecords, getRegionalSeries: regionalFromRecords};
}

export const {getGroupedCounts, getSummary, getRegionalSeries} = createWorkspaceAggregates(Boolean(import.meta.env?.VITE_DATA_QUERY_URL));
