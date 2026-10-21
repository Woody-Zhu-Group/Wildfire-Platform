// The landing page's data: counts written by scripts/access-snapshot.ts from the
// public services, plus the risk model's 0.24-degree California grid. Visitors
// without an account see only these aggregates, never a live service call.
import data from "./trySnapshot.json" with { type: "json" }
import grid from "../../../docs/assets/data/weather_anim/grid_cells.json" with { type: "json" }

export type MapDataset = "cpuc" | "calfire"
export type SeriesDataset = "cpuc" | "epss" | "calfire"
/** [grid cell id, count] for every cell with at least one record. */
export type CellCounts = readonly (readonly [number, number])[]
export interface GridCounts { total: number; outside: number; cells: CellCounts }
export interface GroupRow { label: string; value: number | null; reason?: string }
export interface GroupYear { total: number; rows: GroupRow[]; note?: string; multi_county_incidents?: number }
export interface TrySnapshot {
  generated: string
  sources: { visualization: string; data_query: string }
  years: number[]
  hero: GridCounts & { dataset: MapDataset; start: string; end: string }
  map: Record<MapDataset, Record<string, GridCounts>>
  series: Record<SeriesDataset, Record<string, number[]>>
  groups: Record<SeriesDataset, { by: "utility" | "cause" | "county"; years: Record<string, GroupYear> }>
}

export const SNAPSHOT = data as unknown as TrySnapshot
export const GRID = grid

/** Cells span [lat, lat + spacing) x [lon, lon + spacing) from their stored corner. */
export function cellCenter(id: number): { lat: number; lon: number } | null {
  const cell = GRID.cells[id]
  if (!cell || cell.id !== id) return null
  const half = GRID.meta.spacing / 2
  return { lat: cell.lat + half, lon: cell.lon + half }
}

export function formatCoordinate({ lat, lon }: { lat: number; lon: number }): string {
  return `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`
}

/**
 * Opacity for a count: log-scaled so a single record stays visible beside hot
 * spots, then curved so the hot spots still stand out.
 */
export function cellOpacity(count: number, max: number): number {
  if (!(count > 0) || !(max > 0)) return 0
  return 0.2 + 0.8 * Math.pow(Math.log1p(Math.min(count, max)) / Math.log1p(max), 1.5)
}
