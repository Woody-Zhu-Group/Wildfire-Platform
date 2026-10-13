import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import {
  allocation, decisionClass, DEFAULT_SETUP_ID, formatMetric, matchedLevels, metricValue, RANKING_SERIES, rankingSpread,
  rankingValue, rankingWinShare, sectionalizedCounts,
  sensitivity, setupByIdOrDefault, setupLabel, snapSetup, spread, validateCircuits, validateDecisions, validateStudy,
  sliderIndex, validStudySettings, winShare, type StudyData,
} from '../src/studyData.ts';
import { STUDY_CITATION } from '../src/citation.ts';
import { currentView, panelDatasets, PANEL_VIEWS, viewSettings } from '../src/panelViews.ts';
import { panelUsesGlobalYear } from '../src/globalFilters.ts';
import { DEFAULT_FILTERS } from '../src/data.ts';
import type { PanelSettings } from '../src/state';

const DATA_DIR = new URL('../data/optimization_grid/', import.meta.url);

// Three methods, five setups that are deliberately not a full factorial, two replications.
function fixture(): StudyData {
  const setups = {
    id: ['C20-B10-W10-E70-G90', 'C30-B20-W10-E70-G90', 'C30-B20-W20-E80-G90', 'C40-B20-W20-E80-G90', 'C30-B20-W20-E70-G90'],
    C: [0.2, 0.3, 0.3, 0.4, 0.3], B: [0.1, 0.2, 0.2, 0.2, 0.2], W: [0.1, 0.1, 0.2, 0.2, 0.2],
    E: [0.7, 0.7, 0.8, 0.8, 0.7], G: [0.9, 0.9, 0.9, 0.9, 0.9], complete: [true, true, true, true, true],
  };
  const runs: StudyData['runs'] = {
    setup: [], method: [], exp: [], exposure: [], exposureFilled: [], plannedExposure: [], sectionalized: [], sectionalizedFilled: [], fastTrip: [], pspsPlanned: [], pspsRealized: [],
    saifiFastTrip: [], saifiPspsPlanned: [], saifiPspsRealized: [], populationSectionalized: [], circuitWidth: [], elapsedSec: [], timedOut: [],
  };
  for (let setup = 0; setup < 5; setup++) for (let method = 0; method < 3; method++) for (let exp = 0; exp < 2; exp++) {
    const exposure = 100 - 10 * setup - (method === 0 ? 30 : method === 1 ? 10 : 0) - exp * 5;
    runs.setup.push(setup); runs.method.push(method); runs.exp.push(exp); runs.exposure.push(exposure);
    // Filling helps the baselines more than the proposed plan, as in the grid.
    runs.exposureFilled.push(method === 0 ? exposure : exposure - 15); runs.sectionalizedFilled.push(20);
    runs.plannedExposure.push(method === 2 ? null : exposure * 2);
    runs.sectionalized.push(10 + method); runs.fastTrip.push(method === 2 ? 5 : 0); runs.pspsPlanned.push(3); runs.pspsRealized.push(2 + exp);
    runs.saifiFastTrip.push(method === 2 ? 0.05 : 0); runs.saifiPspsPlanned.push(0.04); runs.saifiPspsRealized.push(0.02 + exp * 0.02);
    runs.populationSectionalized.push(0.3); runs.circuitWidth.push(method === 0 ? 7.5 : null); runs.elapsedSec.push(1 + method); runs.timedOut.push(false);
  }
  return {
    version: 1,
    source: { paper: 'https://arxiv.org/abs/2604.01232', complete_setups: 5, setups: 5, runs: runs.setup.length, circuits: 3 },
    methods: [
      { slug: 'group_conformal_random', label: 'Group conformal, random groups', short: 'GC random', family: 'robust', description: '' },
      { slug: 'co_optimized', label: 'Co-optimized', short: 'Co-optimized', family: 'deterministic', description: '' },
      { slug: 'planning_only', label: 'Planning only', short: 'Planning only', family: 'planning', description: '' },
    ],
    parameters: [
      { key: 'C', label: 'Sectionalization budget', unit: '', description: '', values: [0.2, 0.3, 0.4] },
      { key: 'B', label: 'Fast-trip budget', unit: '', description: '', values: [0.1, 0.2] },
      { key: 'W', label: 'SAIFI cap', unit: '', description: '', values: [0.1, 0.2] },
      { key: 'E', label: 'Fast-trip effectiveness', unit: '', description: '', values: [0.7, 0.8] },
      { key: 'G', label: 'Fast-trip SAIFI factor', unit: '', description: '', values: [0.9] },
    ],
    fixed: { delta: 1, alpha: 0.4 },
    baselineExposure: [200, 100],
    // Ranking reference: 40 below the baseline in every setup and replication.
    rankingExposure: Array.from({length: 10}, (_, i) => (i % 2 === 0 ? 200 : 100) - 40),
    coverage: { group_conformal_random: [1, 0] },
    setups, runs,
  };
}

test('the study validates and indexes every run', () => {
  const study = validateStudy(fixture());
  assert.equal(study.replications, 2);
  assert.equal(study.lookup.length, 5 * 3 * 2);
  assert.ok([...study.lookup].every(row => row >= 0));
});

test('inconsistent study data is rejected', () => {
  const cases: [string, (data: StudyData) => void][] = [
    ['short column', data => { data.runs.exposure.pop(); }],
    ['repeated run', data => { data.runs.exp[1] = 0; }],
    ['missing method', data => { data.methods[2].slug = 'other'; }],
    ['bad parameter order', data => { data.parameters.reverse(); }],
    ['complete setup with a missing run', data => { for (const column of Object.values(data.runs)) (column as unknown[]).shift(); }],
    ['no baseline', data => { data.baselineExposure = []; }],
  ];
  for (const [name, mutate] of cases) {
    const data = fixture(); mutate(data);
    assert.throws(() => validateStudy(data), /invalid/, name);
  }
});

test('metrics are computed per setup and replication, with gains against the same draw', () => {
  const study = validateStudy(fixture());
  // Setup 2, proposed (method 0), replication 0: exposure 100 - 20 - 30 = 50; baseline 200.
  assert.equal(metricValue(study, 2, 0, 0, 'risk_reduction'), 0.75);
  assert.equal(metricValue(study, 2, 0, 0, 'gain_planning'), 1 - 50 / 80);
  assert.equal(metricValue(study, 2, 0, 0, 'gain_co_optimized'), 1 - 50 / 70);
  assert.equal(metricValue(study, 2, 2, 0, 'gain_planning'), 0);
  assert.equal(metricValue(study, 2, 2, 0, 'plan_ratio'), null);
  assert.equal(metricValue(study, 2, 0, 1, 'plan_ratio'), 2);
  const s = spread(study, 2, 0, 'risk_reduction');
  assert.deepEqual(s.values, [0.75, 1 - 45 / 100]);
  assert.equal(s.min, 0.55); assert.equal(s.max, 0.75);
});

test('filled plans use the filled exposure; metrics of the solved plan are not reported for them', () => {
  const study = validateStudy(fixture());
  // Setup 2, replication 0: proposed 50 (unchanged), co-optimized 70 - 15 = 55, planning only 80 - 15 = 65.
  assert.equal(metricValue(study, 2, 0, 0, 'gain_co_optimized', 'filled'), 1 - 50 / 55);
  assert.equal(metricValue(study, 2, 1, 0, 'risk_reduction', 'filled'), 1 - 55 / 200);
  assert.equal(metricValue(study, 2, 1, 0, 'sectionalized', 'filled'), 20);
  assert.equal(metricValue(study, 2, 1, 0, 'psps_realized', 'filled'), null);
  assert.equal(metricValue(study, 2, 1, 0, 'plan_ratio', 'filled'), null);
  assert.equal(metricValue(study, 2, 1, 0, 'fast_trip', 'filled'), 0);
  assert.deepEqual(winShare(study, 0, 1, 'filled'), { share: 1, pairs: 10 });
  const data = fixture();
  (data.runs as Record<string, unknown>).exposureFilled = undefined;
  assert.throws(() => validateStudy(data), /exposureFilled missing/);
});

test('the ranking reference pairs with the same draw and only reports exposure metrics', () => {
  const study = validateStudy(fixture());
  // Setup 2, replication 0: reference exposure 160; planning only 80; co-optimized 70.
  assert.equal(rankingValue(study, 2, 0, 'exposure'), 160);
  assert.equal(rankingValue(study, 2, 0, 'risk_reduction'), 1 - 160 / 200);
  assert.equal(rankingValue(study, 2, 0, 'gain_planning'), 1 - 160 / 80);
  assert.equal(rankingValue(study, 2, 0, 'gain_co_optimized', 'filled'), 1 - 160 / 55);
  assert.equal(rankingValue(study, 2, 0, 'sectionalized'), null);
  const reference = rankingSpread(study, 2, 'risk_reduction').values as number[];
  assert.ok(Math.abs(reference[0] - 0.2) < 1e-12 && Math.abs(reference[1] - 0.4) < 1e-12);
  // Setup 0, replication 2 leaves 65 against the reference's 60; the other nine draws win.
  assert.deepEqual(rankingWinShare(study, 0), {share: 0.9, pairs: 10});
  const line = sensitivity(study, 2, 'C', 'risk_reduction', RANKING_SERIES, 'slice');
  assert.equal(line[0].value, null);
  assert.ok(Math.abs(line[1].value! - 0.3) < 1e-12);
  const data = fixture(); data.rankingExposure.pop();
  assert.throws(() => validateStudy(data), /ranking reference/);
});

test('values format with their units and missing values say No data', () => {
  assert.equal(formatMetric('percent', 0.4661), '46.6%');
  assert.equal(formatMetric('exposure', 1916572.69), '1.92M');
  assert.equal(formatMetric('ratio', 3.739), '3.74×');
  assert.equal(formatMetric('saifi', 0.0812), '0.081');
  assert.equal(formatMetric('seconds', 240.03), '240 s');
  assert.equal(formatMetric('percent', null), 'No data');
});

test('setups fall back to the default and snap to the nearest run setup', () => {
  const study = validateStudy(fixture());
  assert.equal(setupByIdOrDefault(study, 'missing'), study.data.setups.id.indexOf(DEFAULT_SETUP_ID));
  assert.equal(setupLabel(study, 2), 'C 30% · B 20% · W 0.20 · E 80% · G 0.90');
  // From C30-B20-W20-E80-G90, a 20% sectionalization budget exists only with B 10%, W 0.10, E 70%.
  const snapped = snapSetup(study, 2, 'C', 0.2);
  assert.equal(study.data.setups.id[snapped.index], 'C20-B10-W10-E70-G90');
  assert.deepEqual(snapped.changed, ['B', 'W', 'E']);
  // Changing W keeps C, B, and E when such a setup exists.
  const w = snapSetup(study, 4, 'W', 0.1);
  assert.equal(study.data.setups.id[w.index], 'C30-B20-W10-E70-G90');
  assert.deepEqual(w.changed, []);
});

test('sensitivity slices hold the other parameters; matched averages never mix backgrounds', () => {
  const study = validateStudy(fixture());
  const slice = sensitivity(study, 2, 'C', 'risk_reduction', 0, 'slice');
  assert.deepEqual(slice.map(point => point.level), [0.2, 0.3, 0.4]);
  assert.equal(slice[0].value, null); // C20 was never run with B20, W20, E80
  assert.ok(slice[1].value !== null && slice[2].value !== null);
  const matched = matchedLevels(study, 'C');
  assert.deepEqual(matched.levels, [0.3, 0.4]);
  assert.deepEqual(matched.combos, ['0.2|0.2|0.8|0.9']);
  const average = sensitivity(study, 2, 'C', 'risk_reduction', 0, 'matched');
  assert.equal(average[0].value, null);
  assert.equal(average[1].setups, 1);
  assert.equal(average[1].value, spread(study, 2, 0, 'risk_reduction').mean);
});

test('allocation reports shares of the SAIFI cap and win shares count paired draws', () => {
  const study = validateStudy(fixture());
  const share = allocation(study, 2, 2)!; // W 0.2, fast-trip SAIFI 0.05, PSPS 0.02 and 0.04
  assert.ok(Math.abs(share.fastTrip - 0.25) < 1e-12);
  assert.ok(Math.abs(share.psps - 0.15) < 1e-12);
  assert.ok(Math.abs(share.unused - 0.6) < 1e-12);
  assert.deepEqual(winShare(study, 0, 1), { share: 1, pairs: 10 });
});

test('decision flags decode into classes and replication counts', () => {
  assert.equal(decisionClass('0'), 'none');
  assert.equal(decisionClass('1'), 'sectionalized');
  assert.equal(decisionClass('3'), 'fast_trip');
  assert.equal(decisionClass('9'), 'psps');
  assert.equal(decisionClass('b'), 'psps');
  assert.deepEqual(sectionalizedCounts(['10b', '005', '100']), [2, 0, 2]);
  assert.throws(() => validateDecisions({ setup: 'S', circuits: 3, methods: { a: ['000'] } }, 'S', 3, ['a'], 2), /invalid/);
  assert.throws(() => validateDecisions({ setup: 'S', circuits: 3, methods: { a: ['00', '000'] } }, 'S', 3, ['a'], 2), /invalid/);
});

test('the shipped study data validates and every complete setup has its decisions', () => {
  const study = validateStudy(JSON.parse(readFileSync(new URL('study.json', DATA_DIR), 'utf8')));
  const circuits = validateCircuits(JSON.parse(readFileSync(new URL('circuits.json', DATA_DIR), 'utf8')));
  assert.equal(study.data.methods.length, 8);
  assert.equal(study.data.source.circuits, circuits.unit.length);
  assert.ok(study.data.setups.id.includes(DEFAULT_SETUP_ID));
  assert.ok(circuits.circuitId.every(id => /^\d{9}$/.test(id)));
  const files = new Set(readdirSync(new URL('decisions/', DATA_DIR)));
  const complete = study.data.setups.id.filter((_, index) => study.data.setups.complete[index]);
  assert.equal(complete.length, study.data.source.complete_setups);
  for (const id of complete) assert.ok(files.has(`${id}.json`), id);
  const slugs = study.data.methods.map(method => method.slug);
  const sample = JSON.parse(readFileSync(new URL(`decisions/${DEFAULT_SETUP_ID}.json`, DATA_DIR), 'utf8'));
  validateDecisions(sample, DEFAULT_SETUP_ID, circuits.unit.length, slugs, study.replications);
});

const base = (patch: Partial<PanelSettings> = {}): PanelSettings => ({
  dataset: 'cpuc', filters: {...DEFAULT_FILTERS}, interval: 'monthly', groupBy: 'cause', measure: 'count', metric: 'events',
  datasets: ['cpuc', 'epss', 'calfire'], overlays: [], ...patch,
});

test('study views have their own ids, no dataset notes, no workspace year, and keep the setup across views', () => {
  for (const view of PANEL_VIEWS.filter(item => item.type === 'optimization')) {
    const settings = viewSettings(base({studySetup: 'C40-B30-W30-E90-G80', studyMethod: 'bonferroni'}), view);
    assert.equal(currentView('optimization', settings), view.id);
    assert.deepEqual(panelDatasets('optimization', settings), []);
    assert.equal(panelUsesGlobalYear('optimization', settings), false);
    assert.equal(settings.studySetup, 'C40-B30-W30-E90-G80');
    assert.equal(settings.studyMethod, 'bonferroni');
  }
  // A stored view name that no longer exists falls back to the method comparison.
  assert.equal(currentView('optimization', base({studyView: 'retired' as never})), 'study-methods');
});

test('saved study settings are validated before a stored workspace is restored', () => {
  assert.ok(validStudySettings({}));
  assert.ok(validStudySettings({studyView: 'map', studySetup: 'C30-B20-W20-E80-G90', studyMetric: 'gain_planning', studyParam: 'W', studyMode: 'matched', studyMethods: ['ci'], studyMethod: 'ci', studyReplication: -1, studyFill: true}));
  for (const bad of [{studyView: 'chart'}, {studyMetric: 'cost'}, {studyParam: 'D'}, {studyMode: 'all'}, {studyMethods: 'ci'}, {studyReplication: 1.5}, {studyReplication: -2}, {studySetup: 3}, {studyFill: 'yes'}]) {
    assert.equal(validStudySettings(bad), false, JSON.stringify(bad));
  }
});

test('sliders sit at the setup value and the shipped setup sits where the grid put it', () => {
  const study = validateStudy(fixture());
  assert.equal(sliderIndex(study, 2, 'C'), 1); // C 30% of 20, 30, 40
  assert.equal(sliderIndex(study, 0, 'B'), 0); // B 10% of 10, 20
  assert.equal(sliderIndex(study, 3, 'E'), 1); // E 80% of 70, 80
});

test('the footer citation is the study the data cites', () => {
  const shipped = JSON.parse(readFileSync(new URL('study.json', DATA_DIR), 'utf8'));
  assert.equal(STUDY_CITATION.url, shipped.source.paper);
  assert.match(STUDY_CITATION.bibtex, /eprint=\{2604\.01232\}/);
  assert.ok(STUDY_CITATION.bibtex.split('\n').every(line => line === line.trimEnd()), 'no trailing spaces in the BibTeX');
});
