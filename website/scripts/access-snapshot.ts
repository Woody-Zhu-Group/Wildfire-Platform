// Writes src/access/trySnapshot.json: the dated, aggregated data behind the
// landing page's map and its Try it panels, which visitors see without an
// account. It reads the public services through the workspace's own loaders
// (src/api.ts), so pagination, totals and measured coverage are checked the same
// way, and stores only counts: per grid cell, per month and per group.
//
//   cd website && node scripts/access-snapshot.ts
//
// Fails without writing when the services disagree with themselves.
import { writeFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import grid from "../../docs/assets/data/weather_anim/grid_cells.json" with { type: "json" }
import { DATA_QUERY_URL, VISUALIZATION_URL, getDailySeries, getGroupedCounts, getLayer } from "../src/api.ts"
import { aggregateDaily, type DatasetId, type Filters, type GroupBy } from "../src/data.ts"

const TRY_YEARS = [2022, 2023, 2024]
const HERO_YEARS = [2020, 2021, 2022, 2023, 2024]
const MAP_DATASETS: DatasetId[] = ["cpuc", "calfire"]
const SERIES_DATASETS: DatasetId[] = ["cpuc", "epss", "calfire"]
const GROUPS: { dataset: DatasetId; by: GroupBy }[] = [
  { dataset: "cpuc", by: "utility" },
  { dataset: "epss", by: "cause" },
  { dataset: "calfire", by: "county" },
]

const yearFilters = (year: number): Filters => ({ start: `${year}-01-01`, end: `${year}-12-31`, county: "", utility: "" })

// Cells span [lat, lat + spacing) x [lon, lon + spacing), as GridSurfaceMap draws them.
const { lat0, lon0, spacing } = grid.meta
const cellAt = new Map(grid.cells.map(cell => [`${cell.row}:${cell.col}`, cell.id]))

async function cellCounts(dataset: DatasetId, year: number) {
  const layer = await getLayer(dataset, yearFilters(year))
  const counts = new Map<number, number>()
  let outside = 0
  for (const feature of layer.geojson.features) {
    if (feature.geometry?.type !== "Point") { outside++; continue }
    const [lon, lat] = feature.geometry.coordinates
    const id = cellAt.get(`${Math.floor((lat - lat0) / spacing)}:${Math.floor((lon - lon0) / spacing)}`)
    if (id === undefined) outside++
    else counts.set(id, (counts.get(id) ?? 0) + 1)
  }
  return { total: layer.meta.total, outside, counts }
}

const sparse = (counts: Map<number, number>) => [...counts].sort((a, b) => a[0] - b[0])

function check(condition: boolean, message: string) {
  if (!condition) throw new Error(message)
}

const map: Record<string, Record<string, { total: number; outside: number; cells: [number, number][] }>> = {}
const hero = new Map<number, number>()
let heroTotal = 0, heroOutside = 0
for (const dataset of MAP_DATASETS) {
  map[dataset] = {}
  const years = dataset === "cpuc" ? HERO_YEARS : TRY_YEARS
  for (const year of years) {
    const { total, outside, counts } = await cellCounts(dataset, year)
    if (TRY_YEARS.includes(year)) map[dataset][year] = { total, outside, cells: sparse(counts) }
    if (dataset === "cpuc") {
      heroTotal += total; heroOutside += outside
      for (const [id, count] of counts) hero.set(id, (hero.get(id) ?? 0) + count)
    }
    console.log(`map ${dataset} ${year}: ${total} records, ${outside} outside the grid`)
  }
}

const series: Record<string, Record<string, number[]>> = {}
for (const dataset of SERIES_DATASETS) {
  series[dataset] = {}
  for (const year of TRY_YEARS) {
    const months = aggregateDaily(await getDailySeries(dataset, yearFilters(year)), "monthly")
    check(months.length === 12 && months[0].start.startsWith(`${year}-01`), `${dataset} ${year}: expected 12 months, got ${months.length}`)
    series[dataset][year] = months.map(month => month.count)
    const mapped = map[dataset]?.[year]
    const sum = series[dataset][year].reduce((a, b) => a + b, 0)
    if (mapped) check(sum === mapped.total, `${dataset} ${year}: series total ${sum} differs from map total ${mapped.total}`)
  }
}

const groups: Record<string, { by: GroupBy; years: Record<string, unknown> }> = {}
for (const { dataset, by } of GROUPS) {
  groups[dataset] = { by, years: {} }
  for (const year of TRY_YEARS) {
    const result = await getGroupedCounts(dataset, yearFilters(year), by)
    const months = series[dataset][year].reduce((a, b) => a + b, 0)
    check(result.total === months, `${dataset} ${year}: grouped total ${result.total} differs from series total ${months}`)
    groups[dataset].years[year] = {
      total: result.total,
      rows: result.rows.map(row => ({ label: row.label, value: row.value, ...(row.reason ? { reason: row.reason } : {}) })),
      ...(result.note ? { note: result.note, multi_county_incidents: result.multi_county_incidents } : {}),
    }
  }
}

const snapshot = {
  generated: new Date().toISOString().slice(0, 10),
  sources: { visualization: VISUALIZATION_URL, data_query: DATA_QUERY_URL },
  years: TRY_YEARS,
  hero: { dataset: "cpuc", start: `${HERO_YEARS[0]}-01-01`, end: `${HERO_YEARS.at(-1)}-12-31`, total: heroTotal, outside: heroOutside, cells: sparse(hero) },
  map,
  series,
  groups,
}
const target = fileURLToPath(new URL("../src/access/trySnapshot.json", import.meta.url))
writeFileSync(target, JSON.stringify(snapshot) + "\n")
console.log(`wrote ${target}`)
