import test from 'node:test';
import assert from 'node:assert/strict';
import { clearDataCache } from '../src/api.ts';
import { DEFAULT_FILTERS, type LayerResponse } from '../src/data.ts';
import { createWorkspaceAggregates, getGroupedCounts, getRegionalSeries, getSummary } from '../src/workspaceAggregates.ts';

function layer(properties: Record<string, unknown>[], total = properties.length): LayerResponse {
  return {
    geojson: {type: 'FeatureCollection', features: properties.map((properties, index) => ({type: 'Feature', id: String(properties.id ?? index), geometry: null, properties}))},
    meta: {total, returned: properties.length, truncated: properties.length < total},
  };
}

test('the default workspace still works when only the deployed visualization API exists', async t => {
  clearDataCache(); t.after(clearDataCache);
  const requests: URL[] = [];
  t.mock.method(globalThis, 'fetch', async (input: string) => {
    const url = new URL(input); requests.push(url);
    if (!url.pathname.endsWith('/map-layer')) return new Response('Not deployed', {status: 404});
    const offset = Number(url.searchParams.get('offset'));
    const count = offset === 0 ? 1000 : 1;
    return Response.json(layer(Array.from({length: count}, (_, i) => ({id: offset + i + 1, event_date: '2024-01-01', county: `County ${(offset + i) % 30}`, utility: 'PGE'})), 1001));
  });
  const result = await getGroupedCounts('cpuc', {...DEFAULT_FILTERS, utility: 'PG&E'}, 'county');
  assert.equal(result.total, 1001);
  assert.equal(result.rows.length, 30);
  assert.equal(result.rows.reduce((sum, row) => sum + (row.value ?? 0), 0), 1001);
  assert.deepEqual(requests.map(url => url.searchParams.get('offset')), ['0', '1000']);
  assert.ok(requests.every(url => url.searchParams.get('utility') === 'PGE'));
});

test('EPSS uses nested outages and keeps unknown, missing and unavailable values distinct', async t => {
  clearDataCache(); t.after(clearDataCache);
  t.mock.method(globalThis, 'fetch', async (input: string) => {
    const url = new URL(input);
    assert.equal(url.pathname.endsWith('/map-layer'), true);
    assert.equal(url.searchParams.get('include_outages'), 'true');
    return Response.json(layer([{id: 'circuit', division: 'Not the outage division', outages: [
      {id: 1, start_date: '2024-01-01', cause: 'Unknown', county: 'Marin', circuit_id: '043371102'},
      {id: 2, start_date: '2024-01-02', cause: null, county: 'Napa', circuit_id: '043371102'},
      {id: 3, start_date: '2024-01-03', cause: 'Weather', county: 'Napa', circuit_id: '043371103'},
    ]}]));
  });
  const causes = await getGroupedCounts('epss', DEFAULT_FILTERS, 'cause');
  assert.equal(causes.total, 3);
  assert.deepEqual(causes.rows.map(row => row.key).sort(), ['Not recorded', 'Unknown', 'Weather']);
  assert.deepEqual((await getGroupedCounts('epss', DEFAULT_FILTERS, 'utility')).rows, [
    {key: 'PG&E', value: 3}, {key: 'SCE', value: null}, {key: 'SDG&E', value: null},
  ]);
  const metrics = await getSummary('epss', DEFAULT_FILTERS);
  assert.equal(metrics.find(metric => metric.id === 'circuits')?.value, 2);
  assert.equal(metrics.find(metric => metric.id === 'counties')?.value, 2);
});

test('summary keeps the original CAL FIRE and PSPS metric definitions', async t => {
  clearDataCache(); t.after(clearDataCache);
  t.mock.method(globalThis, 'fetch', async (input: string) => Response.json(layer(new URL(input).searchParams.get('dataset') === 'calfire' ? [
    {incident_id: 'A', date_only_created: '2024-01-01', acres_burned: 75, county: 'Marin, Napa'},
    {incident_id: 'B', date_only_created: '2024-01-02', acres_burned: null, county: 'Napa'},
  ] : [
    {event_name: 'A', deenergization_start_date: '2024-01-01', customers_deenergized: 0, utility: 'PGE'},
    {event_name: 'B', deenergization_start_date: '2024-01-02', customers_deenergized: null, utility: 'PGE'},
  ])));
  const fire = await getSummary('calfire', DEFAULT_FILTERS);
  assert.deepEqual(fire.map(({id, value, missing}) => ({id, value, missing})), [
    {id: 'events', value: 2, missing: 0}, {id: 'acres', value: 75, missing: 1}, {id: 'counties', value: 2, missing: 0},
  ]);
  const psps = await getSummary('psps', DEFAULT_FILTERS);
  assert.deepEqual(psps.find(metric => metric.id === 'customers'), {id: 'customers', label: 'Customer-event total', unit: 'customer-events', value: 0, missing: 1});
});

test('empty populations are zero while entirely absent summary fields remain missing', async t => {
  clearDataCache(); t.after(clearDataCache);
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json(layer([])));
  assert.deepEqual((await getSummary('cpuc', DEFAULT_FILTERS)).map(metric => metric.value), [0, 0, 0]);
  clearDataCache();
  fetch.mock.mockImplementation(async () => Response.json(layer([{id: 1, event_date: '2024-01-01'}])));
  assert.deepEqual((await getSummary('cpuc', DEFAULT_FILTERS)).map(metric => [metric.value, metric.missing]), [[1, 0], [null, 1], [null, 1]]);
  assert.equal((await getSummary('us_ignitions', DEFAULT_FILTERS))[0].label, 'Sample records');
});

test('regional series use recorded outage divisions with clipped and zero-filled calendar bins', async t => {
  clearDataCache(); t.after(clearDataCache);
  t.mock.method(globalThis, 'fetch', async () => Response.json(layer([{id: 'circuit', division: 'Wrong division', outages: [
    {id: 1, start_date: '2023-12-31', division: 'North Bay'},
    {id: 2, start_date: '2024-01-01', division: 'North Bay'},
    {id: 3, start_date: '2024-01-08', division: ''},
  ]}])));
  const filters = {...DEFAULT_FILTERS, start: '2023-12-30', end: '2024-01-08'};
  for (const interval of ['daily', 'weekly', 'monthly', 'quarterly'] as const) {
    const result = await getRegionalSeries(filters, interval);
    assert.equal(result.total, 3);
    assert.deepEqual(result.series.map(region => [region.name, region.total]), [['North Bay', 2], ['Not recorded', 1]]);
    assert.ok(result.series.every(region => region.buckets[0].start === filters.start && region.buckets.at(-1)?.end === filters.end));
    if (interval === 'weekly') assert.deepEqual(result.series[0].buckets, [
      {start: '2023-12-30', end: '2023-12-30', count: 0},
      {start: '2023-12-31', end: '2023-12-31', count: 1},
      {start: '2024-01-01', end: '2024-01-07', count: 1},
      {start: '2024-01-08', end: '2024-01-08', count: 0},
    ]);
  }
});

test('default aggregation refuses incomplete records and invalid filters', async t => {
  clearDataCache(); t.after(clearDataCache);
  const fetch = t.mock.method(globalThis, 'fetch', async (input: string) => Response.json(new URL(input).searchParams.get('offset') === '0'
    ? layer([{id: 1, event_date: '2024-01-01'}], 2) : layer([], 2)));
  await assert.rejects(getGroupedCounts('cpuc', DEFAULT_FILTERS, 'county'), /pagination stopped/);
  assert.equal(fetch.mock.callCount(), 2);
  await assert.rejects(getSummary('psps', {...DEFAULT_FILTERS, county: 'Marin'}), /not available/);
  await assert.rejects(getRegionalSeries({...DEFAULT_FILTERS, utility: 'SCE'}, 'monthly'), /PG&E only/);
  assert.equal(fetch.mock.callCount(), 2);
});

test('explicit service mode uses aggregate responses and never hides a deployment failure', async t => {
  clearDataCache(); t.after(clearDataCache);
  const configured = createWorkspaceAggregates(true);
  const requests: string[] = [];
  const fetch = t.mock.method(globalThis, 'fetch', async (input: string) => {
    const path = new URL(input).pathname; requests.push(path);
    if (path.endsWith('/grouped-counts')) return Response.json({total: 1, rows: [{key: 'Marin', value: 1}]});
    if (path.endsWith('/summary')) return Response.json({total: 1, metrics: [{id: 'events', value: 1, missing: 0}]});
    if (path.endsWith('/regional-series')) return Response.json({total: 0, series: []});
    throw new Error('Unexpected record download');
  });
  assert.equal((await configured.getGroupedCounts('cpuc', DEFAULT_FILTERS, 'county')).total, 1);
  assert.equal((await configured.getSummary('us_ignitions', DEFAULT_FILTERS))[0].value, 1);
  assert.equal((await configured.getRegionalSeries(DEFAULT_FILTERS, 'monthly')).total, 0);
  assert.equal(requests.length, 3);
  clearDataCache();
  fetch.mock.mockImplementation(async () => new Response('Not deployed', {status: 404}));
  await assert.rejects(configured.getSummary('cpuc', DEFAULT_FILTERS), /HTTP 404/);
  assert.equal(fetch.mock.callCount(), 4);
});

test('both client modes block unsupported EPSS summaries before upstream can return an empty population', async t => {
  clearDataCache(); t.after(clearDataCache);
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('Unsupported scope must not reach the backend'); });
  for (const useDataQuery of [false, true]) {
    const client = createWorkspaceAggregates(useDataQuery);
    await assert.rejects(client.getSummary('epss', {...DEFAULT_FILTERS, utility: 'SCE'}), /PG&E only/);
  }
  assert.equal(fetch.mock.callCount(), 0);
});

test('browser aggregation counts a multi-county CAL FIRE incident in each county it lists', async t => {
  clearDataCache(); t.after(clearDataCache);
  t.mock.method(globalThis, 'fetch', async () => Response.json(layer([
    {incident_id: 'a', date_only_created: '2024-07-01', county: 'Shasta, Tehama', incident_type: 'Wildfire'},
    {incident_id: 'b', date_only_created: '2024-07-02', county: 'Shasta', incident_type: 'Wildfire'},
  ])));
  const result = await createWorkspaceAggregates(false).getGroupedCounts('calfire', DEFAULT_FILTERS, 'county');
  assert.deepEqual(result.rows, [{key: 'Shasta', value: 2}, {key: 'Tehama', value: 1}]);
  assert.equal(result.total, 2);
  assert.equal(result.multi_county_incidents, 1);
  assert.match(result.note ?? '', /more than the statewide total/);
});
