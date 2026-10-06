import { useId, type CSSProperties, type MouseEvent } from "react"
import { GRID, cellOpacity, type CellCounts } from "./trySnapshot.ts"
import "./access.css"

const { rows: ROWS, cols: COLS, lat0: LAT0, lon0: LON0, spacing: SPACING } = GRID.meta
// Degrees of longitude are shorter than degrees of latitude at California's
// mean latitude, so cells are drawn narrower than tall.
const H = 10
const W = H * Math.cos(37.3 * Math.PI / 180)
const GAP = 1.1

const CITIES = [
  { name: "Sacramento", lat: 38.58, lon: -121.49 },
  { name: "San Francisco", lat: 37.77, lon: -122.42, anchor: "end" },
  { name: "Fresno", lat: 36.74, lon: -119.79 },
  { name: "Los Angeles", lat: 34.05, lon: -118.24, anchor: "end" },
  { name: "San Diego", lat: 32.72, lon: -117.16, anchor: "end" },
] as const

const x = (lon: number) => (lon - LON0) / SPACING * W
const y = (lat: number) => (ROWS - (lat - LAT0) / SPACING) * H

/**
 * California as the risk model's 824-cell grid. Cells with records take the
 * dataset's color, brighter for more records; the rest trace the state's shape.
 */
export function CaliforniaGrid({ counts, color, label, glow = false, active = null, onActive }: {
  counts: CellCounts
  color: string
  label: string
  glow?: boolean
  active?: number | null
  onActive?: (cell: number | null) => void
}) {
  const id = useId()
  const values = new Map(counts)
  const max = Math.max(1, ...values.values())
  const lit = GRID.cells.filter(cell => values.has(cell.id))
  const rect = (cell: typeof GRID.cells[number]) => ({ x: cell.col * W + GAP / 2, y: (ROWS - 1 - cell.row) * H + GAP / 2, width: W - GAP, height: H - GAP })
  const inspect = (event: MouseEvent<SVGSVGElement>) => {
    const cell = (event.target as Element).getAttribute("data-cell")
    onActive?.(cell === null ? null : Number(cell))
  }
  return (
    <svg className={`access-grid${glow ? " is-hero" : ""}${onActive ? " is-interactive" : ""}`} viewBox={`-4 -4 ${COLS * W + 8} ${ROWS * H + 8}`}
      role="img" aria-label={label} onMouseMove={onActive ? inspect : undefined} onMouseLeave={onActive ? () => onActive(null) : undefined}>
      {glow && <>
        <defs><filter id={`${id}-glow`} x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="6" /></filter></defs>
        <g className="access-grid-glow" filter={`url(#${id}-glow)`} aria-hidden="true">
          {lit.map(cell => <rect key={cell.id} {...rect(cell)} fill={color} fillOpacity={cellOpacity(values.get(cell.id)!, max)} />)}
        </g>
      </>}
      <g className="access-grid-empty" aria-hidden="true">
        {GRID.cells.filter(cell => !values.has(cell.id)).map(cell => <rect key={cell.id} data-cell={onActive ? cell.id : undefined} {...rect(cell)} rx={1.2} />)}
      </g>
      <g className="access-grid-lit" aria-hidden="true">
        {lit.map(cell => <rect key={cell.id} data-cell={cell.id} {...rect(cell)} rx={1.2} fill={color} fillOpacity={cellOpacity(values.get(cell.id)!, max)}
          style={{ "--grid-delay": `${Math.round((ROWS - cell.row) * 22 + (cell.id % 7) * 30)}ms` } as CSSProperties} />)}
      </g>
      {active !== null && GRID.cells[active] && <rect {...rect(GRID.cells[active])} rx={1.2} className="access-grid-active" aria-hidden="true" />}
      <g className="access-grid-cities" aria-hidden="true">
        {CITIES.map(city => {
          const anchor = "anchor" in city ? city.anchor : "start"
          return <g key={city.name}>
            <circle cx={x(city.lon)} cy={y(city.lat)} r={1.6} />
            <text x={x(city.lon) + (anchor === "end" ? -4 : 4)} y={y(city.lat) + 2.6} textAnchor={anchor}>{city.name}</text>
          </g>
        })}
      </g>
    </svg>
  )
}
