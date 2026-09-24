import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { clearDataCache, getGroupedCounts, getRegionalSeries, getSummary } from '../src/api.ts';
import { DEFAULT_FILTERS } from '../src/data.ts';

test('production build profile pins Data Query to the live CloudFront URL', () => {
  const text = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), '../.env.production'), 'utf8');
  const dataQuery = text.split(/\r?\n/).map(line => line.trim()).find(line => line.startsWith('VITE_DATA_QUERY_URL='))?.slice('VITE_DATA_QUERY_URL='.length);
  const risk = text.split(/\r?\n/).map(line => line.trim()).find(line => line.startsWith('VITE_RISK_URL='))?.slice('VITE_RISK_URL='.length);
  assert.equal(dataQuery, 'https://d3t70p3if3twy3.cloudfront.net/api/data-query');
  assert.equal(risk, 'https://d3t70p3if3twy3.cloudfront.net/api/risk-forecasting');
});

test('grouped counts fetch one geometry-free response and retain all categories', async t => {
  clearDataCache(); t.after(clearDataCache);
  const urls: URL[] = [];
  t.mock.method(globalThis, 'fetch', async (input: string) => {
    const url = new URL(input); urls.push(url);
    return Response.json({total: 60, rows: Array.from({length: 60}, (_, index) => ({key: `County ${index}`, value: 1}))});
  });
  const result = await getGroupedCounts('cpuc', {...DEFAULT_FILTERS, utility: 'PG&E'}, 'county');
  assert.equal(result.rows.length, 60);
  assert.equal(result.total, 60);
  assert.equal(urls.length, 1);
  assert.equal(urls[0].pathname.endsWith('/grouped-counts'), true);
  assert.equal(urls[0].searchParams.get('dataset'), 'cpuc_ignitions');
  assert.equal(urls[0].searchParams.get('utility'), 'PGE');
  assert.equal(urls[0].searchParams.has('limit'), false);
});

test('grouped counts retain unknown causes, missing causes and unavailable utilities', async t => {
  clearDataCache(); t.after(clearDataCache);
  t.mock.method(globalThis, 'fetch', async (input: string) => Response.json(new URL(input).searchParams.get('group_by') === 'cause'
    ? {total: 4, rows: [{key: 'Unknown', value: 2}, {key: 'Not recorded', value: 1}, {key: 'Weather', value: 1}]}
    : {total: 4, rows: [{key: 'PG&E', value: 4}, {key: 'SCE', value: null}, {key: 'SDG&E', value: null}]}));
  const causes = await getGroupedCounts('epss', DEFAULT_FILTERS, 'cause');
  assert.equal(causes.rows.find(row => row.key === 'Unknown')?.value, 2);
  assert.equal(causes.rows.find(row => row.key === 'Not recorded')?.value, 1);
  assert.equal((await getGroupedCounts('epss', DEFAULT_FILTERS, 'utility')).rows.find(row => row.key === 'SCE')?.value, null);
});

test('grouped counts keep the service key and carry the registry code and label (issue #89)', async t => {
  clearDataCache(); t.after(clearDataCache);
  t.mock.method(globalThis, 'fetch', async () => Response.json({total: 6, rows: [
    {key: 'PG&E', code: 'PGE', label: 'PG&E', value: 4}, {key: 'SDG&E', code: 'SDGE', label: 'SDG&E', value: 2}, {key: 'SCE', code: 'SCE', label: 'SCE', value: 0},
  ]}));
  const result = await getGroupedCounts('cpuc', DEFAULT_FILTERS, 'utility');
  assert.deepEqual(result.rows.map(row => [row.key, row.code, row.label]), [['PG&E', 'PGE', 'PG&E'], ['SDG&E', 'SDGE', 'SDG&E'], ['SCE', 'SCE', 'SCE']]);
});

test('grouped counts from a service without code and label get them from the naming registry', async t => {
  clearDataCache(); t.after(clearDataCache);
  t.mock.method(globalThis, 'fetch', async (input: string) => Response.json(new URL(input).searchParams.get('group_by') === 'utility'
    ? {total: 3, rows: [{key: 'PG&E', value: 2}, {key: 'Not recorded', value: 1}, {key: 'SDG&E', value: 0}, {key: 'SCE', value: 0}]}
    : {total: 3, rows: [{key: 'Butte', value: 3}]}));
  const utilities = await getGroupedCounts('cpuc', DEFAULT_FILTERS, 'utility');
  assert.deepEqual(utilities.rows.map(row => [row.key, row.code, row.label]), [
    ['PG&E', 'PGE', 'PG&E'], ['Not recorded', 'Not recorded', 'Not recorded'], ['SCE', 'SCE', 'SCE'], ['SDG&E', 'SDGE', 'SDG&E'],
  ]);
  const counties = await getGroupedCounts('cpuc', DEFAULT_FILTERS, 'county');
  assert.deepEqual(counties.rows.map(row => [row.key, row.code, row.label]), [['Butte', 'Butte', 'Butte']]);
});

test('grouped counts reject a code or label that is not text', async t => {
  clearDataCache(); t.after(clearDataCache);
  t.mock.method(globalThis, 'fetch', async () => Response.json({total: 1, rows: [{key: 'Butte', code: 'Butte', label: null, value: 1}]}));
  await assert.rejects(getGroupedCounts('cpuc', DEFAULT_FILTERS, 'county'), /complete dataset/);
});

test('summary downloads totals without any map-layer request', async t => {
  clearDataCache(); t.after(clearDataCache);
  const urls: URL[] = [];
  t.mock.method(globalThis, 'fetch', async (input: string) => {
    urls.push(new URL(input));
    return Response.json({total: 33457, metrics: [{id: 'events', value: 33457, missing: 0}]});
  });
  assert.equal((await getSummary('us_ignitions', DEFAULT_FILTERS))[0].value, 33457);
  assert.equal(urls.length, 1);
  assert.equal(urls[0].pathname.endsWith('/summary'), true);
  assert.equal(urls[0].searchParams.get('dataset'), 'us_ignitions');
});

test('regional series use the selected interval and return every division for rendering and exports', async t => {
  clearDataCache(); t.after(clearDataCache);
  t.mock.method(globalThis, 'fetch', async (input: string) => {
    const url = new URL(input);
    assert.equal(url.pathname.endsWith('/regional-series'), true);
    assert.equal(url.searchParams.get('interval'), 'quarterly');
    assert.equal(url.searchParams.get('county'), 'Marin');
    return Response.json({total: 18, series: Array.from({length: 18}, (_, index) => ({name: `Division ${index}`, total: 1, buckets: [
      {start: '2024-01-01', end: '2024-03-31', count: 1}, {start: '2024-04-01', end: '2024-06-30', count: 0},
      {start: '2024-07-01', end: '2024-09-30', count: 0}, {start: '2024-10-01', end: '2024-12-31', count: 0},
    ]}))});
  });
  const result = await getRegionalSeries({...DEFAULT_FILTERS, county: 'Marin'}, 'quarterly');
  assert.equal(result.series.length, 18);
  assert.equal(result.total, 18);
  assert.deepEqual(result.series[0].buckets.map(bucket => bucket.count), [1, 0, 0, 0]);
});

test('aggregate totals cannot silently truncate and backend failures never fall back to GeoJSON', async t => {
  clearDataCache(); t.after(clearDataCache);
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({total: 60, rows: [{key: 'Marin', value: 1}]}));
  await assert.rejects(getGroupedCounts('cpuc', DEFAULT_FILTERS, 'county'), /complete dataset/);
  clearDataCache();
  fetch.mock.mockImplementation(async () => new Response('Not deployed', {status: 404}));
  await assert.rejects(getSummary('cpuc', DEFAULT_FILTERS), /HTTP 404/);
  assert.equal(fetch.mock.callCount(), 2);
});

test('unsupported aggregate filters are blocked before network access', async t => {
  clearDataCache(); t.after(clearDataCache);
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('Unexpected fetch'); });
  await assert.rejects(getSummary('psps', {...DEFAULT_FILTERS, county: 'Marin'}), /not available/);
  await assert.rejects(getGroupedCounts('epss', {...DEFAULT_FILTERS, utility: 'SCE'}, 'utility'), /PG&E only/);
  await assert.rejects(getRegionalSeries({...DEFAULT_FILTERS, utility: 'SCE'}, 'daily'), /PG&E only/);
  assert.equal(fetch.mock.callCount(), 0);
});

test('CAL FIRE county rows may exceed the incident total only when the service reports multi-county incidents', async t => {
  clearDataCache(); t.after(clearDataCache);
  const note = 'A CAL FIRE incident that lists several counties is counted in every county it lists.';
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({
    total: 2, multi_county_incidents: 1, note,
    rows: [{key: 'Shasta', value: 2}, {key: 'Tehama', value: 1}],
  }));
  const result = await getGroupedCounts('calfire', DEFAULT_FILTERS, 'county');
  assert.equal(result.total, 2);
  assert.equal(result.multi_county_incidents, 1);
  assert.equal(result.note, note);
  clearDataCache();
  fetch.mock.mockImplementation(async () => Response.json({total: 2, rows: [{key: 'Shasta', value: 2}, {key: 'Tehama', value: 1}]}));
  await assert.rejects(getGroupedCounts('calfire', DEFAULT_FILTERS, 'county'), /complete dataset/);
  clearDataCache();
  fetch.mock.mockImplementation(async () => Response.json({total: 2, multi_county_incidents: 1, rows: [{key: 'Marin', value: 3}]}));
  await assert.rejects(getGroupedCounts('cpuc', DEFAULT_FILTERS, 'county'), /complete dataset/);
});
