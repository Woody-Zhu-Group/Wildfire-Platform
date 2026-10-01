import type { ComparisonGrid } from './agentContracts.ts';

export function validComparisonGrid(value: unknown, evidence?: readonly string[]): value is ComparisonGrid {
  if (!value || typeof value !== 'object') return false;
  const grid = value as ComparisonGrid;
  if (typeof grid.label !== 'string' || !grid.label || !Array.isArray(grid.cells) || !grid.cells.length) return false;
  if (![grid.rows, grid.columns].every(axis => Array.isArray(axis) && axis.length > 0
    && axis.every(label => typeof label === 'string' && label.length > 0) && new Set(axis).size === axis.length)) return false;
  const coordinates = new Set<string>();
  return grid.cells.every(cell => {
    if (!cell || !grid.rows.includes(cell.row) || !grid.columns.includes(cell.column)
      || typeof cell.evidence_id !== 'string' || !cell.evidence_id || (evidence && !evidence.includes(cell.evidence_id))) return false;
    if (cell.value === null ? typeof cell.reason !== 'string' || !cell.reason
      : typeof cell.value !== 'number' || !Number.isFinite(cell.value) || Boolean(cell.reason)) return false;
    const key = JSON.stringify([cell.row, cell.column]);
    if (coordinates.has(key)) return false;
    coordinates.add(key);
    return true;
  });
}
