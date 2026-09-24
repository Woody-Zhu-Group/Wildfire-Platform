import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import catalog from '../../shared/dataset_coverage.json' with {type: 'json'};
import naming from '../../shared/naming.json' with {type: 'json'};
import { coverageReason, coverageWindow, coveredUtilities, datasetYears, soleUtilityLabel, workspaceYears } from '../src/coverage.ts';
import { DEFAULT_FILTERS, unavailableReason } from '../src/data.ts';
import { WORKSPACE_YEARS } from '../src/globalFilters.ts';

// Coverage is measured by the loaders (shared/dataset_coverage.json), never
// declared in website code. These are the PR #93 re-review cases.

const year = (y: number) => ({...DEFAULT_FILTERS, start: `${y}-01-01`, end: `${y}-12-31`});

test('the website reads the measured utilities, not a hand-written list', () => {
  assert.deepEqual(coveredUtilities('cpuc_ignitions'), ['PACIFICORP', 'PGE', 'SCE', 'SDGE']);
  assert.deepEqual(coveredUtilities('psps_events'), ['Liberty', 'PGE', 'SCE', 'SDGE']);
  assert.equal(soleUtilityLabel('epss_outages'), 'PG&E');
  assert.equal(soleUtilityLabel('cpuc_ignitions'), null);
});

test('Liberty CPUC ignitions in 2023 and Bear Valley PSPS or CPUC are unavailable, not zero', () => {
  assert.match(unavailableReason('cpuc', {...year(2023), utility: 'Liberty'})!, /^CPUC has rows only for PacifiCorp, PG&E, SCE, and SDG&E\./);
  assert.match(unavailableReason('psps', {...year(2023), utility: 'BVES'})!, /PSPS has rows only for/);
  assert.match(unavailableReason('cpuc', {...year(2023), utility: 'BVES'})!, /CPUC has rows only for/);
  // Covered reads stay available.
  assert.equal(unavailableReason('cpuc', {...year(2023), utility: 'SCE'}), null);
});

test('PacifiCorp and PG&E PSPS in 2019 are both unavailable', () => {
  assert.match(coverageReason('psps_events', 'PSPS', 'PACIFICORP', '2019-01-01', '2019-12-31')!, /rows only for/);
  assert.equal(
    coverageReason('psps_events', 'PSPS', 'PGE', '2019-01-01', '2019-12-31'),
    'PSPS for PG&E starts on 2021-10-11. There is no PSPS data for this period.',
  );
});

test('PG&E EPSS in 2020 is unavailable while 2021 is covered from 2021-11-01', () => {
  assert.equal(unavailableReason('epss', year(2020)), 'EPSS starts on 2021-11-01. There is no EPSS data for this period.');
  assert.equal(unavailableReason('epss', {...year(2020), utility: 'PG&E'}), 'EPSS for PG&E starts on 2021-11-01. There is no EPSS data for this period.');
  assert.equal(unavailableReason('epss', year(2021)), null);
});

test('no website source hardcodes a utility for coverage', () => {
  const dir = fileURLToPath(new URL('../src/', import.meta.url));
  const offenders = readdirSync(dir).filter(name => /\.(ts|tsx)$/.test(name))
    .filter(name => /PG&E|PG&amp;E|['"]PGE['"]|'pge'/.test(readFileSync(join(dir, name), 'utf8')));
  assert.deepEqual(offenders, []);
});

test('the untagged filter reads the untagged span, as the Python registry does', () => {
  const untagged = naming.untagged_utility;
  const calfire = catalog.datasets.calfire_incidents;
  assert.deepEqual(coverageWindow('calfire_incidents', untagged), [calfire.untagged!.first, calfire.last]);
  assert.equal(coverageReason('calfire_incidents', 'CAL FIRE', untagged, '2013-01-01', '2013-12-31'), null);
  // CPUC has no rows without a utility: not covered, never "rows only for" a list.
  assert.equal(
    coverageReason('cpuc_ignitions', 'CPUC', untagged, '2023-01-01', '2023-12-31'),
    'CPUC has no rows without a utility. There is no untagged CPUC data.',
  );
});

test('the workspace years are the measured years with rows, not a hand-written list', () => {
  const measured = new Set(Object.keys(catalog.datasets).flatMap(dataset => datasetYears(dataset)));
  assert.deepEqual([...WORKSPACE_YEARS], [...measured].sort((a, b) => a - b));
  assert.deepEqual(workspaceYears(), [...WORKSPACE_YEARS]);
  // CAL FIRE has 141 rows in 2013, so 2013 is offered; no dataset has rows in 2011.
  assert.ok(WORKSPACE_YEARS.includes(2013));
  assert.ok(!WORKSPACE_YEARS.includes(2011));
  const source = readFileSync(fileURLToPath(new URL('../src/globalFilters.ts', import.meta.url)), 'utf8');
  assert.doesNotMatch(source, /WORKSPACE_YEARS\s*=\s*\[/);
});

test('a year with no rows in the dataset is unavailable, and a year with rows is not', () => {
  assert.equal(unavailableReason('calfire', year(2013)), null);
  assert.equal(
    unavailableReason('calfire', year(2011)),
    'CAL FIRE has no rows between 2009 and 2013. There is no CAL FIRE data for this period.',
  );
  assert.equal(unavailableReason('cpuc', year(2013)), 'CPUC starts on 2020-01-01. There is no CPUC data for this period.');
});
