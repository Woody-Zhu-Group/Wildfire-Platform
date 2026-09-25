import test from 'node:test';
import assert from 'node:assert/strict';
import type { PanelId, PanelInstance } from '../src/PanelPicker.tsx';
import { selectInlineViews, snapshotInlinePanel } from '../src/inlineViews.ts';

function newPanel(id: number, type: PanelId): PanelInstance {
  return { id, type, settings: {
    dataset: 'cpuc', filters: { start: '2024-01-01', end: '2024-12-31', county: '', utility: '' },
    interval: 'monthly', groupBy: 'cause', measure: 'count', metric: 'events', datasets: ['cpuc'], overlays: [],
  } };
}

test('inline views freeze the workspace year used by the answer', () => {
  const map = newPanel(1, 'map');
  const snapshot = snapshotInlinePanel(map, { year: 2023 });
  assert.deepEqual(snapshot.settings.filters, { ...map.settings.filters, start: '2023-01-01', end: '2023-12-31' });
  assert.equal(snapshot.settings.filterMode, 'override');
});

test('an answer with no new graphs shows no inline views', () => {
  assert.deepEqual(selectInlineViews([]), []);
  assert.deepEqual(selectInlineViews([newPanel(1, 'stat_card'), newPanel(2, 'record_table')]), []);
});

test('an answer with one new graph shows only that graph', () => {
  const result = selectInlineViews([newPanel(1, 'stat_card'), newPanel(2, 'map')]);
  assert.deepEqual(result.map(panel => panel.id), [2]);
});

test('an answer shows at most two of its new graphs', () => {
  const result = selectInlineViews([newPanel(1, 'map'), newPanel(2, 'time_series'), newPanel(3, 'comparison')]);
  assert.deepEqual(result.map(panel => panel.id), [1, 2]);
});
