import type { EventRecord } from './data.ts';

export interface CumulativePoint {
  date: string;
  acres: number;
}

export function cumulativeAcres(
  events: readonly EventRecord[],
  start: string,
  end: string,
): {points: CumulativePoint[]; total: number; missing: number} {
  const daily = new Map<string, number>();
  let missing = 0;
  for (const event of events) {
    if (event.date < start || event.date > end) continue;
    if (event.acres === null) {
      missing += 1;
      continue;
    }
    daily.set(event.date, (daily.get(event.date) ?? 0) + event.acres);
  }
  const points: CumulativePoint[] = [];
  let total = 0;
  for (let time = Date.parse(start); time <= Date.parse(end); time += 86_400_000) {
    const date = new Date(time).toISOString().slice(0, 10);
    total += daily.get(date) ?? 0;
    points.push({date, acres: total});
  }
  return {points, total, missing};
}

// The panel caption. The count of incidents without acreage is known only once
// the records load; before that it is not available, never 0.
export function cumulativeCaption(start: string, end: string, missing: number | null | undefined): string {
  const acreage = typeof missing === 'number'
    ? `${missing.toLocaleString()} incidents have no acreage.`
    : 'The number of incidents without acreage is not available until the records load.';
  return `CAL FIRE incident-map feed; ${start} – ${end}; cumulative reported acres. ${acreage}`;
}
