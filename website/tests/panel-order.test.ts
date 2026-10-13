import test from 'node:test';
import assert from 'node:assert/strict';
import { movePanel, nearestPanelSlot, prependPanels, type PanelSlot } from '../src/panelOrder.ts';

const panels = Array.from({length: 5}, (_, index) => ({id: index + 1, settings: {filter: `scope-${index}`}}));
const slots: PanelSlot[] = panels.map((panel, index) => ({id: panel.id, left: index % 2 * 420, top: Math.floor(index / 2) * 540, width: 400, height: 520}));

test('dropping into a grid slot inserts the panel and shifts the remaining order', () => {
  const down = movePanel(panels, 1, 3);
  assert.deepEqual(down.map(panel => panel.id), [2, 3, 4, 1, 5]);
  assert.deepEqual(movePanel(panels, 5, 1).map(panel => panel.id), [1, 5, 2, 3, 4]);
  assert.deepEqual(panels.map(panel => panel.id), [1, 2, 3, 4, 5]);
  assert.ok(down.every(panel => panel === panels.find(original => original.id === panel.id)));
});

test('edge positions and a removed panel never lose or duplicate workspace state', () => {
  assert.deepEqual(movePanel(panels, 1, 99).map(panel => panel.id), [2, 3, 4, 5, 1]);
  assert.deepEqual(movePanel(panels, 5, -1).map(panel => panel.id), [5, 1, 2, 3, 4]);
  assert.equal(movePanel(panels, 3, 2), panels);
  assert.equal(movePanel(panels, 99, 2), panels);
  assert.deepEqual(movePanel([], 1, 0), []);
});

test('new panels from the picker or an answer lead the workspace in their given order', () => {
  const picked = {id: 6, settings: {filter: 'picked'}};
  assert.deepEqual(prependPanels(panels, [picked]).map(panel => panel.id), [6, 1, 2, 3, 4, 5]);
  const answer = [7, 8, 9].map(id => ({id, settings: {filter: `answer-${id}`}}));
  assert.deepEqual(prependPanels(panels, answer).map(panel => panel.id), [7, 8, 9, 1, 2, 3, 4, 5]);
  assert.deepEqual(prependPanels([], answer).map(panel => panel.id), [7, 8, 9]);
  assert.deepEqual(panels.map(panel => panel.id), [1, 2, 3, 4, 5]);
});

test('reopening a panel already in the workspace keeps the order and never duplicates it', () => {
  assert.equal(prependPanels(panels, [panels[2]]), panels);
  assert.equal(prependPanels(panels, []), panels);
  assert.deepEqual(prependPanels(panels, [panels[2], {id: 6, settings: {filter: 'new'}}]).map(panel => panel.id), [6, 1, 2, 3, 4, 5]);
});

test('snap targets follow the closest panel center across rows, gaps and outside the grid', () => {
  assert.equal(nearestPanelSlot({x: 200, y: 260}, slots), 0);
  assert.equal(nearestPanelSlot({x: 620, y: 260}, slots), 1);
  assert.equal(nearestPanelSlot({x: 610, y: 790}, slots), 3);
  assert.equal(nearestPanelSlot({x: 202, y: 1335}, slots), 4);
  assert.equal(nearestPanelSlot({x: -500, y: -500}, slots), 0);
  assert.equal(nearestPanelSlot({x: 410, y: 260}, slots), 0);
  assert.equal(nearestPanelSlot({x: 411, y: 260}, slots), 1);
  assert.equal(nearestPanelSlot({x: 0, y: 0}, []), null);
});

test('single-column and scrolled layouts use their current viewport positions', () => {
  const single = slots.map((slot, index) => ({...slot, left: 14, top: index * 534 - 800, width: 362}));
  assert.equal(nearestPanelSlot({x: 195, y: 528}, single), 2);
  assert.equal(nearestPanelSlot({x: 195, y: -540}, single), 0);
});
