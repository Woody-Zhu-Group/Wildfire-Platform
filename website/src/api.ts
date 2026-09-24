import { configFor, filterError, groupNames, unavailableReason, utilityCode, UTILITIES, recordsFromFeatures, type Bucket, type DatasetId, type Filters, type GroupBy, type Interval, type LayerResponse, type Boundary } from './data.ts';
import type { AgentAnswer, AgentStreamEvent } from './agentContracts.ts';
import { readSummary, type SummaryResponse } from './stats.ts';
import type { RegionSeries } from './temporal.ts';
import { validateObservedTraining, type ObservedTraining } from './residual.ts';
import { validateRiskSurface, type RiskSurface } from './riskSurface.ts';
import { MetricsUnavailableError, validateModelMetrics, type ModelMetrics } from './modelMetrics.ts';
export type { AgentAnswer, AgentStreamEvent } from './agentContracts.ts';

export const VISUALIZATION_URL = (import.meta.env?.VITE_VISUALIZATION_URL || 'https://d3t70p3if3twy3.cloudfront.net/api/visualization').replace(/\/+$/, '');
export const AGENT_URL = (import.meta.env?.VITE_AGENT_URL || 'https://d3t70p3if3twy3.cloudfront.net/api/agent').replace(/\/+$/, '');
export const DATA_QUERY_URL = (import.meta.env?.VITE_DATA_QUERY_URL || 'https://d3t70p3if3twy3.cloudfront.net/api/data-query').replace(/\/+$/, '');
export const RISK_URL = (import.meta.env?.VITE_RISK_URL || 'https://d3t70p3if3twy3.cloudfront.net/api/risk-forecasting').replace(/\/+$/, '');
const cache = new Map<string, { at: number; promise: Promise<unknown> }>();
export function clearDataCache() { cache.clear(); }
export async function getJSON<T>(url: string, timeoutMs = 25_000): Promise<T> {
  const found = cache.get(url);
  if (found && Date.now() - found.at < 300_000) return found.promise as Promise<T>;
  const promise = (async () => {
    const response = await fetch(url, { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(timeoutMs) });
    if (!response.ok) throw new Error(`Data service returned HTTP ${response.status}. Please retry.`);
    return await response.json() as T;
  })();
  const entry = { at: Date.now(), promise };
  cache.set(url, entry);
  if (cache.size > 32) cache.delete(cache.keys().next().value!);
  try { return await promise; }
  catch (error) { if (cache.get(url) === entry) cache.delete(url); throw error; }
}
function queryParams(dataset: DatasetId, filters: Filters) {
  const error = filterError(filters) || unavailableReason(dataset, filters);
  if (error) throw new Error(error);
  const params = new URLSearchParams({ dataset: configFor(dataset).api, start_date: filters.start, end_date: filters.end });
  if (filters.utility) params.set('utility', utilityCode(filters.utility));
  if (filters.county) params.set('county', filters.county);
  return params;
}
export async function collectPages(load: (offset: number) => Promise<LayerResponse>): Promise<LayerResponse> {
  const first = await load(0);
  const features = [...first.geojson.features];
  const ids = new Set(features.map(f => String(f.id)));
  while (features.length < first.meta.total) {
    const next = await load(features.length);
    if (next.meta.total !== first.meta.total || !next.geojson.features.length) throw new Error('Data changed or pagination stopped before completion. Please retry.');
    for (const feature of next.geojson.features) {
      const id = String(feature.id);
      if (ids.has(id)) throw new Error('Overlapping data pages. Please retry.');
      ids.add(id); features.push(feature);
    }
  }
  if (features.length !== first.meta.total) throw new Error('The response count does not match the complete dataset.');
  return { ...first, geojson: { type: 'FeatureCollection', features }, meta: { ...first.meta, returned: features.length, truncated: false } };
}
export async function getLayer(dataset: DatasetId, filters: Filters, outages = false) {
  const params = queryParams(dataset, filters);
  params.set('limit', '1000');
  if (outages && dataset === 'epss') params.set('include_outages', 'true');
  return collectPages(offset => {
    const page = new URLSearchParams(params); page.set('offset', String(offset));
    return getJSON<LayerResponse>(`${VISUALIZATION_URL}/map-layer?${page}`);
  });
}
export async function getRecords(dataset: DatasetId, filters: Filters) {
  return recordsFromFeatures(dataset, (await getLayer(dataset, filters, true)).geojson.features);
}
function aggregateParams(dataset: DatasetId, filters: Filters) {
  const params = queryParams(dataset, filters);
  params.set('dataset', configFor(dataset).query);
  return params;
}
// key is the service's group value; code and label come from the naming registry
// (issue #89). Display the label.
export interface GroupedRow {key: string; code: string; label: string; value: number | null}
export interface GroupedCounts {rows: GroupedRow[]; total: number; multi_county_incidents?: number; note?: string}
// A CAL FIRE incident that lists several counties ("Shasta, Tehama") counts in each
// county it lists, so CAL FIRE county rows may sum above the incident total, and only
// when the service reports such incidents.
export function groupedRowsMatchTotal(result: GroupedCounts, dataset: DatasetId, groupBy: GroupBy) {
  const sum = result.rows.reduce((total, row) => total + (row.value ?? 0), 0);
  if (sum === result.total) return true;
  const multi = result.multi_county_incidents;
  return dataset === 'calfire' && groupBy === 'county' && Number.isSafeInteger(multi) && multi! > 0 && sum > result.total;
}
export async function getGroupedCounts(dataset: DatasetId, filters: Filters, groupBy: GroupBy): Promise<GroupedCounts> {
  const params = aggregateParams(dataset, filters); params.set('group_by', groupBy);
  const result = await getJSON<GroupedCounts>(`${DATA_QUERY_URL}/grouped-counts?${params}`);
  if (!Number.isSafeInteger(result.total) || result.total < 0 || !Array.isArray(result.rows)
    || result.rows.some(row => !row || typeof row.key !== 'string' || (row.value !== null && (!Number.isSafeInteger(row.value) || row.value < 0))
      || (row.code !== undefined && typeof row.code !== 'string') || (row.label !== undefined && typeof row.label !== 'string'))
    || new Set(result.rows.map(row => row.key)).size !== result.rows.length
    || !groupedRowsMatchTotal(result, dataset, groupBy)) throw new Error('Grouped counts do not match the complete dataset.');
  // Deploy-order safeguard: if docs/ ships before the data query service is updated, rows
  // arrive without code or label, so fill them from the registry with the same rule.
  const rows = result.rows.map(row => ({...groupNames(groupBy, row.key), ...row}));
  return {...result, rows: rows.sort((a, b) => (b.value ?? -1) - (a.value ?? -1) || a.key.localeCompare(b.key))};
}
export async function getSummary(dataset: DatasetId, filters: Filters) {
  const params = aggregateParams(dataset, filters);
  return readSummary(await getJSON<SummaryResponse>(`${DATA_QUERY_URL}/summary?${params}`), dataset);
}
export async function getRegionalSeries(filters: Filters, interval: Interval) {
  const params = aggregateParams('epss', filters); params.delete('dataset'); params.set('interval', interval);
  const result = await getJSON<{series: RegionSeries[]; total: number}>(`${DATA_QUERY_URL}/regional-series?${params}`);
  if (!Number.isSafeInteger(result.total) || result.total < 0 || !Array.isArray(result.series)
    || result.series.some(region => !region || typeof region.name !== 'string' || !Number.isSafeInteger(region.total) || region.total < 0
      || !Array.isArray(region.buckets) || !region.buckets.length
      || region.buckets.some(bucket => !bucket || typeof bucket.start !== 'string' || typeof bucket.end !== 'string' || !Number.isSafeInteger(bucket.count) || bucket.count < 0)
      || region.buckets[0].start !== filters.start || region.buckets.at(-1)!.end !== filters.end
      || region.buckets.reduce((sum, bucket) => sum + bucket.count, 0) !== region.total)
    || new Set(result.series.map(region => region.name)).size !== result.series.length
    || result.series.reduce((sum, region) => sum + region.total, 0) !== result.total) throw new Error('Regional series do not match the complete dataset.');
  return {...result, series: [...result.series].sort((a, b) => b.total - a.total || a.name.localeCompare(b.name))};
}
export async function getDailySeries(dataset: DatasetId, filters: Filters) {
  const params = queryParams(dataset, filters); params.set('interval', 'daily');
  const result = await getJSON<{ buckets: Bucket[]; meta: { total_events: number } }>(`${VISUALIZATION_URL}/time-series?${params}`);
  if (result.buckets.reduce((sum, bucket) => sum + bucket.count, 0) !== result.meta.total_events) throw new Error('Time series total does not match its buckets.');
  return result.buckets;
}
export async function getRiskSurface(date: string): Promise<RiskSurface> {
  return validateRiskSurface(
    await getJSON<unknown>(`${RISK_URL}/surface?date=${encodeURIComponent(date)}`, 60_000),
    date,
  );
}
// Not cached through getJSON: a 503 carries the risk service's reason, which the card shows.
export async function getModelMetrics(): Promise<ModelMetrics> {
  const response = await fetch(`${RISK_URL}/metrics`, { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(25_000) });
  if (response.status === 503) {
    let detail = '';
    try { const body = await response.json() as {detail?: unknown}; detail = typeof body?.detail === 'string' ? body.detail : ''; } catch { /* no JSON body */ }
    throw new MetricsUnavailableError(detail || 'the risk service did not give a reason.');
  }
  if (!response.ok) throw new Error(`Risk service returned HTTP ${response.status}. Please retry.`);
  return validateModelMetrics(await response.json());
}
export async function getObservedTraining(date: string): Promise<ObservedTraining> {
  return validateObservedTraining(
    await getJSON<unknown>(`${RISK_URL}/observed-training?date=${encodeURIComponent(date)}`),
    date,
  );
}
export async function getCoverage(dataset: DatasetId) {
  const result = await getJSON<{ buckets: Bucket[] }>(`${VISUALIZATION_URL}/time-series?dataset=${configFor(dataset).api}&interval=daily`);
  const nonzero = result.buckets.filter(b => b.count > 0);
  return { start: nonzero[0]?.start ?? null, end: nonzero.at(-1)?.end ?? null, total: result.buckets.reduce((sum, b) => sum + b.count, 0) };
}
export async function getBoundaries(kind: 'hftd' | 'territories'): Promise<Boundary[]> {
  if (kind === 'hftd') {
    const data = await getJSON<LayerResponse>(`${VISUALIZATION_URL}/map-layer?dataset=hftd`);
    return data.geojson.features as Boundary[];
  }
  const results = await Promise.all(UTILITIES.map(utilityCode).map(utility => getJSON<{ geojson: Boundary }>(`${VISUALIZATION_URL}/utility-territory?utility=${utility}`)));
  return results.map(result => result.geojson);
}
export interface DetailResponse {
  attributes: Record<string, unknown>;
  detail_fields: { label: string; value: unknown }[];
  geometry: GeoJSON.Geometry | null;
  outages?: Record<string, unknown>[] | null;
  affected_circuits?: Record<string, unknown>[] | null;
}
export async function getDetail(dataset: DatasetId, id: string, circuitScope?: { start: string; end: string }) {
  const params = new URLSearchParams({ dataset: circuitScope ? 'circuits' : configFor(dataset).api, id });
  if (circuitScope) { params.set('start_date', circuitScope.start); params.set('end_date', circuitScope.end); }
  const result = await getJSON<DetailResponse>(`${VISUALIZATION_URL}/event-detail?${params}`);
  for (const rows of [result.outages, result.affected_circuits]) {
    if (rows !== undefined && rows !== null && (!Array.isArray(rows) || rows.some(row => !row || typeof row !== 'object' || Array.isArray(row)))) {
      throw new Error('The detail service returned invalid related records. Please retry.');
    }
  }
  return result;
}
export function parseSSE(frame: string): { event: string; data: unknown } | null {
  const lines = frame.split('\n');
  const payload = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!payload) return null;
  return { event: lines.find(line => line.startsWith('event:'))?.slice(6).trim() ?? 'message', data: JSON.parse(payload) };
}
export async function askAgent(question: string, signal: AbortSignal, onProgress: (text: string) => void, onEvent?: (event: AgentStreamEvent) => void): Promise<AgentAnswer> {
  const response = await fetch(`${AGENT_URL}/ask/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify({ question }), signal });
  if (!response.ok || !response.body) throw new Error(`Agent unavailable (HTTP ${response.status}). You can still use the data panels.`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, '\n');
      let boundary: number;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const parsed = parseSSE(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2);
        if (!parsed) continue;
        if (!parsed.data || typeof parsed.data !== 'object' || Array.isArray(parsed.data)) throw new Error('The agent returned an invalid stream event.');
        if (parsed.event === 'answer' || parsed.event === 'error') {
          const answer = parsed.data as AgentAnswer;
          if (typeof answer.answer_text !== 'string') throw new Error('The agent returned an incomplete answer.');
          return answer;
        }
        onEvent?.({event: parsed.event, data: parsed.data as Record<string, unknown>});
        onProgress(parsed.event.includes('tool') ? 'Reading data…' : 'Working on your question…');
      }
      if (done) throw new Error('The connection ended before an answer arrived. Please retry.');
    }
  } finally { await reader.cancel(); }
}
