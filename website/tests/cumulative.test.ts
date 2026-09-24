import test from 'node:test';
import assert from 'node:assert/strict';
import { cumulativeAcres, cumulativeCaption } from '../src/cumulative.ts';
import type { EventRecord } from '../src/data.ts';

function incident(id: string, date: string, acres: number | null): EventRecord {
  return {id, date, acres, dataset:'calfire', name:id, county:null, utility:null, cause:null, geometry:null, properties:{}};
}

test('cumulative acres preserves zero days and missing acreage', () => {
  const result = cumulativeAcres([
    incident('a','2024-01-01',10),
    incident('b','2024-01-01',2.5),
    incident('c','2024-01-03',7.5),
    incident('d','2024-01-03',null),
  ], '2024-01-01', '2024-01-03');
  assert.deepEqual(result, {
    points: [
      {date:'2024-01-01', acres:12.5},
      {date:'2024-01-02', acres:12.5},
      {date:'2024-01-03', acres:20},
    ],
    total:20,
    missing:1,
  });
});

test('the caption never reports 0 missing-acreage incidents before the records load', () => {
  assert.match(cumulativeCaption('2024-01-01', '2024-12-31', undefined), /not available until the records load/);
  assert.doesNotMatch(cumulativeCaption('2024-01-01', '2024-12-31', undefined), /0 incidents/);
  assert.match(cumulativeCaption('2024-01-01', '2024-12-31', 0), /0 incidents have no acreage/);
});
