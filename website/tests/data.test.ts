import test from 'node:test';
import assert from 'node:assert/strict';
import { aggregateDaily, DEFAULT_FILTERS, filterError, recordsFromFeatures, type LayerResponse } from '../src/data.ts';
import { askAgent, clearDataCache, collectPages, getLayer, parseSSE } from '../src/api.ts';

function page(ids: number[], total: number): LayerResponse {
  return { geojson: { type: 'FeatureCollection', features: ids.map(id => ({ type: 'Feature', id, geometry: null, properties: { id, event_date: '2024-01-01' } })) }, meta: { total, returned: ids.length, truncated: ids.length < total } };
}
test('pagination includes the final page and refuses a changing or overlapping snapshot', async () => {
  const offsets: number[] = [];
  const result = await collectPages(async offset => { offsets.push(offset); return offset ? page([3], 3) : page([1,2], 3); });
  assert.deepEqual(offsets, [0,2]); assert.equal(result.geojson.features.length, 3); assert.equal(result.meta.truncated, false);
  await assert.rejects(collectPages(async offset => offset ? page([2], 3) : page([1,2], 3)), /Overlapping/);
  await assert.rejects(collectPages(async offset => offset ? page([], 3) : page([1,2], 3)), /pagination stopped/);
  await assert.rejects(collectPages(async offset => offset ? page([3], 4) : page([1,2], 3)), /Data changed/);
});

test('map-layer requests offsets 0 and 1000 for 1001 records without overlap', async t => {
  clearDataCache();
  t.after(clearDataCache);
  const offsets: number[] = [];
  t.mock.method(globalThis, 'fetch', async (input: string) => {
    const url = new URL(input);
    assert.equal(url.pathname.endsWith('/map-layer'), true);
    assert.equal(url.searchParams.get('limit'), '1000');
    const offset = Number(url.searchParams.get('offset'));
    offsets.push(offset);
    const ids = offset === 0 ? Array.from({length: 1000}, (_, i) => i + 1) : [1001];
    return Response.json(page(ids, 1001));
  });
  const result = await getLayer('cpuc', DEFAULT_FILTERS);
  assert.deepEqual(offsets, [0, 1000]);
  assert.equal(result.geojson.features.length, 1001);
  assert.equal(new Set(result.geojson.features.map(feature => feature.id)).size, 1001);
  assert.equal(result.meta.truncated, false);
});

test('unsupported geographic filters are rejected before any fetch', async t => {
  clearDataCache();
  t.after(clearDataCache);
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('Unexpected fetch'); });
  await assert.rejects(getLayer('epss', {...DEFAULT_FILTERS, utility: 'SCE'}), /rows only for PG&E/);
  await assert.rejects(getLayer('us_ignitions', {...DEFAULT_FILTERS, county: 'Marin'}), /do not support/);
  await assert.rejects(getLayer('us_ignitions', {...DEFAULT_FILTERS, utility: 'PG&E'}), /do not support/);
  await assert.rejects(getLayer('psps', {...DEFAULT_FILTERS, county: 'Marin'}), /not available/);
  assert.equal(fetch.mock.callCount(), 0);
});
test('EPSS counts outage records instead of circuit features and preserves leading-zero IDs', () => {
  const layer = page([1,2], 2);
  layer.geojson.features[0].properties = { circuit_id: '043371102', event_count: 2, outages: [{id: 1, circuit_id:'043371102', start_date:'2024-01-02'}, {id:2, start_date:'2024-01-03'}] };
  layer.geojson.features[1].properties = { outages: [{id:3, start_date:'2024-02-02'}] };
  const records = recordsFromFeatures('epss', layer.geojson.features);
  assert.equal(records.length, 3); assert.equal(records[0].properties.circuit_id, '043371102');
  assert.equal(records[0].utility, 'PG&E');
  assert.throws(() => recordsFromFeatures('epss', page([1],1).geojson.features), /missing event records/);
});
test('calendar buckets conserve counts across a year boundary, leap day and clipped weeks', () => {
  const daily = [{start:'2023-12-31',end:'2023-12-31',count:2},{start:'2024-01-01',end:'2024-01-01',count:3},{start:'2024-01-02',end:'2024-01-02',count:0},{start:'2024-02-29',end:'2024-02-29',count:4}];
  for (const interval of ['daily','weekly','monthly','quarterly'] as const) assert.equal(aggregateDaily(daily,interval).reduce((sum,b) => sum+b.count,0),9);
  assert.deepEqual(aggregateDaily(daily,'weekly').slice(0,2),[{start:'2023-12-31',end:'2023-12-31',count:2},{start:'2024-01-01',end:'2024-01-02',count:3}]);
  assert.equal(filterError({...DEFAULT_FILTERS,start:'2024-02-30'}),'Choose a valid start and end date.');
  assert.equal(filterError({...DEFAULT_FILTERS,start:'2025-01-01'}),'Start date must be on or before end date.');
});
test('SSE parsing handles data lines and fragmented answers, and rejects premature closure', async t => {
  assert.deepEqual(parseSSE('event: answer\ndata: {"answer_text":"Ready",\ndata: "status":"ok"}'), { event:'answer',data:{answer_text:'Ready',status:'ok'} });
  const encoder = new TextEncoder();
  t.mock.method(globalThis,'fetch', async () => new Response(new ReadableStream({ start(controller) {
    for (const part of ['event: tool_done\ndata: {}\n\nevent: ans','wer\ndata: {"answer_text":"532 records",','"status":"ok"}\n\n']) controller.enqueue(encoder.encode(part));
    controller.close();
  } }),{status:200}));
  const answer = await askAgent('How many?', new AbortController().signal, () => {});
  assert.equal(answer.answer_text,'532 records');
  t.mock.restoreAll();
  t.mock.method(globalThis,'fetch', async () => new Response('event: tool_done\ndata: {}\n\n'));
  await assert.rejects(askAgent('How many?',new AbortController().signal,()=>{}),/before an answer/);
});
