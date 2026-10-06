import test from "node:test"
import assert from "node:assert/strict"
import coverage from "../../shared/dataset_coverage.json" with { type: "json" }
import { GRID, SNAPSHOT, cellCenter, cellOpacity, type GridCounts } from "../src/access/trySnapshot.ts"

const STALE = "Regenerate with `node scripts/access-snapshot.ts` in website/."
const measured = (key: "cpuc_ignitions" | "epss_outages" | "calfire_incidents", year: number) =>
  (coverage.datasets[key].years as Record<string, number>)[String(year)]
const sum = (values: readonly number[]) => values.reduce((a, b) => a + b, 0)

function assertCells(counts: GridCounts, label: string) {
  const ids = counts.cells.map(([id]) => id)
  assert.equal(new Set(ids).size, ids.length, `${label}: duplicate cells`)
  assert.ok(counts.cells.every(([id, count]) => GRID.cells[id]?.id === id && Number.isInteger(count) && count > 0), `${label}: unknown cell or empty count`)
  assert.equal(sum(counts.cells.map(([, count]) => count)) + counts.outside, counts.total, `${label}: drawn plus outside must equal the total`)
}

test("snapshot totals equal the measured coverage of the warehouse", () => {
  for (const year of SNAPSHOT.years) {
    assert.equal(sum(SNAPSHOT.series.cpuc[year]), measured("cpuc_ignitions", year), `CPUC ${year}. ${STALE}`)
    assert.equal(sum(SNAPSHOT.series.epss[year]), measured("epss_outages", year), `EPSS ${year}. ${STALE}`)
    assert.equal(sum(SNAPSHOT.series.calfire[year]), measured("calfire_incidents", year), `CAL FIRE ${year}. ${STALE}`)
  }
  const heroYears = Array.from({ length: Number(SNAPSHOT.hero.end.slice(0, 4)) - Number(SNAPSHOT.hero.start.slice(0, 4)) + 1 }, (_, i) => Number(SNAPSHOT.hero.start.slice(0, 4)) + i)
  assert.equal(SNAPSHOT.hero.total, sum(heroYears.map(year => measured("cpuc_ignitions", year))), `Hero map. ${STALE}`)
})

test("every Try it view of a year counts the same records", () => {
  assert.equal(coverage.datasets.calfire_incidents.definition, "wildfire_default", "the snapshot uses the default CAL FIRE incident types")
  for (const year of SNAPSHOT.years) {
    for (const dataset of ["cpuc", "epss", "calfire"] as const) {
      const months = SNAPSHOT.series[dataset][year]
      assert.equal(months.length, 12, `${dataset} ${year}: months`)
      assert.equal(SNAPSHOT.groups[dataset].years[year].total, sum(months), `${dataset} ${year}: grouped total`)
    }
    for (const dataset of ["cpuc", "calfire"] as const) {
      const counts = SNAPSHOT.map[dataset][year]
      assert.equal(counts.total, sum(SNAPSHOT.series[dataset][year]), `${dataset} ${year}: map total`)
      assertCells(counts, `${dataset} ${year}`)
    }
  }
  assertCells(SNAPSHOT.hero, "hero")
})

test("snapshot carries only aggregates and a calendar date", () => {
  assert.match(SNAPSHOT.generated, /^\d{4}-\d{2}-\d{2}$/)
  const text = JSON.stringify(SNAPSHOT)
  assert.doesNotMatch(text, /"(id|event_date|coordinates|properties)"/, "no record-level fields")
})

test("cells are located by their center and shaded by count", () => {
  const first = GRID.cells[0]
  assert.deepEqual(cellCenter(0), { lat: first.lat + GRID.meta.spacing / 2, lon: first.lon + GRID.meta.spacing / 2 })
  assert.equal(cellCenter(GRID.cells.length), null)
  assert.equal(cellOpacity(0, 10), 0)
  assert.equal(cellOpacity(10, 10), 1)
  assert.ok(cellOpacity(1, 74) >= 0.2 && cellOpacity(1, 74) < cellOpacity(2, 74) && cellOpacity(2, 74) < cellOpacity(74, 74))
})
