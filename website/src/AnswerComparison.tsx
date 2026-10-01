import { usePanel } from './state';
import { utilityLabel } from './data.ts';

const COLORS = ['#dc2626', '#2563eb', '#0f766e', '#a16207', '#9333ea', '#ea580c'];

export function AnswerComparison() {
  const { settings } = usePanel();
  const grid = settings.answerComparison!;
  const maximum = Math.max(...grid.cells.map(cell => cell.value ?? 0)) || 1;
  const label = (row: string) => utilityLabel(row) ?? row;
  const getCell = (row: string, column: string) => grid.cells.find(cell => cell.row === row && cell.column === column);
  const number = (value: number) => value.toLocaleString(undefined, {maximumSignificantDigits: 21});
  return <div className="analysis-chart answer-comparison">
    <div className="comparison-legend" aria-label="Compared periods">
      {grid.columns.map((column, index) => <span key={column}><i style={{background: COLORS[index % COLORS.length]}} />{column}</span>)}
    </div>
    <div className="comparison-groups" role="img" aria-label={`${grid.label} by entity and period. Exact values follow in the table.`}>
      {grid.rows.map(row => <div className="comparison-group" key={row}>
        <strong>{label(row)}</strong><div>{grid.columns.map((column, index) => {
          const cell = getCell(row, column);
          const value = cell?.value ?? null;
          const reason = cell?.reason || 'No count was returned for this entity and period.';
          return <div key={column} className="comparison-series" title={`${label(row)}, ${column}: ${value === null ? reason : number(value)}`}>
            <div className="bar-track">{value === null ? <span className="missing-bar">No data</span>
              : <div className="value-bar" style={{width: `${value / maximum * 100}%`, background: COLORS[index % COLORS.length]}} />}</div>
            <span>{value === null ? 'No data' : number(value)}</span>
          </div>;
        })}</div>
      </div>)}
    </div>
    <div className="comparison-table-scroll">
      <table className="comparison-values" aria-label={`${grid.label}: exact values`}>
        <thead><tr><th scope="col">Entity</th>{grid.columns.map(column => <th scope="col" key={column}>{column}</th>)}</tr></thead>
        <tbody>{grid.rows.map(row => <tr key={row}><th scope="row">{label(row)}</th>{grid.columns.map(column => {
          const cell = getCell(row, column);
          const reason = cell?.reason || 'No count was returned for this entity and period.';
          return <td key={column} title={cell?.value == null ? reason : undefined}>
            {cell?.value != null ? number(cell.value) : <span aria-label={`No data. ${reason}`}>No data</span>}
          </td>;
        })}</tr>)}</tbody>
      </table>
    </div>
  </div>;
}
