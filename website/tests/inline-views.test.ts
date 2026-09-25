import test from 'node:test';
import assert from 'node:assert/strict';
import type { PanelId, PanelInstance } from '../src/PanelPicker.tsx';
import { initialInlineViews, selectInlineViews, snapshotInlinePanel } from '../src/inlineViews.ts';

function newPanel(id: number, type: PanelId): PanelInstance {
  return { id, type, settings: {
    dataset: 'cpuc', filters: { start: '2024-01-01', end: '2024-12-31', county: '', utility: '' },
    interval: 'monthly', groupBy: 'cause', measure: 'count', metric: 'events', datasets: ['cpuc'], overlays: [],
  } };
}

test('inline views freeze the workspace year used by the answer', () => {
  const map = newPanel(1, 'map');
  const [snapshot] = initialInlineViews([map], { year: 2023 });
  assert.deepEqual(snapshot.settings.filters, { ...map.settings.filters, start: '2023-01-01', end: '2023-12-31' });
  assert.equal(snapshot.settings.filterMode, 'override');
});

test('initial inline views use two available charts even when no map exists', () => {
  const panels = [newPanel(1, 'time_series'), newPanel(2, 'comparison')];
  assert.deepEqual(initialInlineViews(panels, { year: 2024 }).map(panel => panel.id), [1, 2]);
});

test('a new map replaces only the map and keeps the earlier chart marked as previous', () => {
  const map = newPanel(1, 'map');
  const series = newPanel(2, 'time_series');
  const nextMap = snapshotInlinePanel(newPanel(3, 'map'), { year: 2024 });
  const result = selectInlineViews([map, series], [nextMap]);
  assert.deepEqual(result.map(view => [view.panel.id, view.updated]), [[3, true], [2, false]]);
});

test('two new graphs replace the pair; non-visual answers retain both with prior labels', () => {
  const previous = [newPanel(1, 'map'), newPanel(2, 'time_series')];
  const additions = [newPanel(3, 'stat_card'), newPanel(4, 'map'), newPanel(5, 'comparison')];
  const updated = selectInlineViews(previous, additions);
  assert.deepEqual(updated.map(view => [view.panel.id, view.updated]), [[4, true], [5, true]]);
  const retained = selectInlineViews(updated.map(view => view.panel), [newPanel(6, 'record_table')]);
  assert.deepEqual(retained.map(view => [view.panel.id, view.updated]), [[4, false], [5, false]]);
});
