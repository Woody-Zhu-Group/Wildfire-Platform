import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_FILTERS, filterSupport, supportedFilters } from '../src/data.ts';
import { panelDatasets, updatePanelSettings } from '../src/panelViews.ts';
import { datasetCaveats } from '../src/caveats.ts';
import type { PanelSettings } from '../src/state';

const settings: PanelSettings = {dataset: 'cpuc', filters: {...DEFAULT_FILTERS, county: 'Marin', utility: 'SCE'}, interval: 'monthly', groupBy: 'county', measure: 'count', metric: 'events', datasets: ['cpuc', 'epss', 'calfire'], overlays: []};

test('filter availability follows actual dataset capabilities including a mixed time series', () => {
  assert.deepEqual(filterSupport(['cpuc', 'calfire']), {county: true, utility: 'all'});
  assert.deepEqual(filterSupport(['cpuc', 'epss', 'calfire']), {county: true, utility: 'pge'});
  assert.deepEqual(filterSupport(['psps']), {county: false, utility: 'all'});
  assert.deepEqual(filterSupport(['us_ignitions']), {county: false, utility: 'none'});
});

test('explicit dataset changes clear only filters the new source cannot support', () => {
  const original = structuredClone(settings);
  const panel = {id: 1, type: 'map' as const, settings};
  assert.deepEqual(updatePanelSettings(panel, {dataset: 'epss'}).settings.filters, {...settings.filters, utility: ''});
  assert.deepEqual(updatePanelSettings(panel, {dataset: 'psps'}).settings.filters, {...settings.filters, county: ''});
  assert.deepEqual(updatePanelSettings(panel, {dataset: 'us_ignitions'}).settings.filters, {...DEFAULT_FILTERS});
  assert.deepEqual(updatePanelSettings(panel, {dataset: 'calfire'}).settings.filters, settings.filters);
  assert.deepEqual(supportedFilters({...settings.filters, utility: 'PG&E'}, ['epss']), {...settings.filters, utility: 'PG&E'});
  assert.deepEqual(settings, original);
});

test('card notes follow visible time-series datasets and the cited source of agent stat cards', () => {
  assert.deepEqual(panelDatasets('time_series', settings), ['cpuc', 'epss', 'calfire']);
  assert.equal(datasetCaveats(panelDatasets('time_series', settings)).length, 3);
  assert.deepEqual(panelDatasets('time_series', {...settings, seriesMode: 'regional'}), ['epss']);
  assert.match(datasetCaveats(panelDatasets('time_series', {...settings, seriesMode: 'regional'}))[0], /PG&E-only/);
  assert.match(datasetCaveats(panelDatasets('map', {...settings, dataset: 'epss'}))[0], /PG&E-only/);
  assert.match(datasetCaveats(panelDatasets('map', {...settings, dataset: 'us_ignitions'}))[0], /FireCastRL classification sample/);
  assert.deepEqual(panelDatasets('time_series', {...settings, dataset: 'calfire', seriesMode: 'seasonal'}), ['calfire']);
  assert.deepEqual(panelDatasets('time_series', {...settings, dataset: 'psps', seriesMode: 'yearly'}), ['cpuc']);
  const stat = {value: 1, label: 'Result', scope: 'Marin', period: '2024', unit: 'events'};
  assert.deepEqual(panelDatasets('stat_card', {...settings, answerStat: {...stat, sourceDataset: 'calfire_incidents'}}), ['calfire']);
  assert.deepEqual(datasetCaveats(panelDatasets('stat_card', {...settings, answerStat: {...stat, sourceDataset: 'cnhpp'}})), []);
  assert.deepEqual(panelDatasets('stat_card', {...settings, answerStat: stat}), []);
});
