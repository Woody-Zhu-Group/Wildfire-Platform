import catalog from '../../shared/dataset_coverage.json' with {type: 'json'};
import naming from '../../shared/naming.json' with {type: 'json'};

// Dataset coverage as the loaders measured it (db/loaders/coverage.py writes
// shared/dataset_coverage.json; services/shared/dataset_registry.py reads the
// same file). Nothing here declares which utilities or dates a dataset covers.
// "years" maps a calendar year to its row count; a year with no rows is absent.
type Span = {first: string | null; last: string | null; rows: number; years?: Record<string, number>};
type Entry = Span & {
  date_column: string | null;
  utility_dimension: boolean;
  utilities: Record<string, Span>;
  untagged?: Span | null;
};

const MEASURED = catalog.datasets as Record<string, Entry>;
// The utility filter value for rows with no utility, as the Python registry names it.
const UNTAGGED = naming.untagged_utility;
// Names as clarification text writes them (PacifiCorp, Bear Valley), as the
// registry's not-covered text does.
const LABELS: Record<string, string> = {...naming.utility_display_labels, ...naming.utility_clarify_labels};
const label = (code: string) => code === UNTAGGED ? 'untagged' : LABELS[code] ?? code;

function names(codes: string[]) {
  const labels = codes.map(label);
  return labels.length <= 2 ? labels.join(' and ') : `${labels.slice(0, -1).join(', ')}, and ${labels.at(-1)}`;
}

/** The measured span for one utility (a code, or the untagged value), or the dataset's own. */
function spanFor(entry: Entry, utility: string | null): Span | null {
  if (utility === null) return entry;
  if (utility === UNTAGGED) return entry.untagged ?? null;
  return entry.utilities[utility] ?? null;
}

function yearsWithRows(span: Span | null | undefined): number[] {
  return Object.entries(span?.years ?? {}).filter(([, rows]) => rows > 0).map(([year]) => Number(year)).sort((a, b) => a - b);
}

/** Utility codes the dataset has rows for. */
export function coveredUtilities(dataset: string): string[] {
  return Object.keys(MEASURED[dataset]?.utilities ?? {});
}

/** Calendar years in which the dataset (or one utility in it) has rows, as measured. */
export function datasetYears(dataset: string, utility: string | null = null): number[] {
  const entry = MEASURED[dataset];
  return entry ? yearsWithRows(spanFor(entry, utility)) : [];
}

/** Every year in which some dataset has rows: the years the workspace offers. */
export function workspaceYears(): number[] {
  return [...new Set(Object.keys(MEASURED).flatMap(dataset => datasetYears(dataset)))].sort((a, b) => a - b);
}

/** The one utility's display label when the dataset has rows for exactly one, else null. */
export function soleUtilityLabel(dataset: string): string | null {
  const entry = MEASURED[dataset];
  const codes = coveredUtilities(dataset);
  return entry?.utility_dimension && codes.length === 1 ? label(codes[0]) : null;
}

/** From the utility's first row (or the dataset's) to the dataset's last row. */
export function coverageWindow(dataset: string, utility: string | null): [string, string] | null {
  const entry = MEASURED[dataset];
  if (!entry) return null;
  const first = spanFor(entry, utility)?.first ?? null;
  return first && entry.last ? [first, entry.last] : null;
}

/** Only the utilities the dataset has rows for, in one clause. */
export function coverageSummary(dataset: string, name: string) {
  return `${name} has rows only for ${names(coveredUtilities(dataset))}`;
}

/**
 * A period inside the window that the dataset still has no rows for in any
 * year it touches (CAL FIRE between its one 2009 row and 2013): a gap in the
 * source, not a zero.
 */
function emptyYearsReason(dataset: string, name: string, window: [string, string], start: string, end: string): string | null {
  const years = datasetYears(dataset);
  if (!years.length) return null;
  const first = Number((start > window[0] ? start : window[0]).slice(0, 4));
  const last = Number((end < window[1] ? end : window[1]).slice(0, 4));
  if (years.some(year => year >= first && year <= last)) return null;
  const before = years.filter(year => year < first).at(-1);
  const after = years.find(year => year > last);
  return before !== undefined && after !== undefined
    ? `${name} has no rows between ${before} and ${after}. There is no ${name} data for this period.`
    : `${name} has no rows from ${first} to ${last}. There is no ${name} data for this period.`;
}

/**
 * Why the dataset has no rows for this utility (a code, or the untagged
 * value) and period, or null when it does. A count outside measured coverage
 * is absent, never zero.
 */
export function coverageReason(dataset: string, name: string, utility: string | null, start: string, end: string): string | null {
  const entry = MEASURED[dataset];
  if (!entry) return null;
  if (utility !== null && !entry.utility_dimension) return `${name} has no utility column. This utility has no ${name} data.`;
  if (utility === UNTAGGED && !entry.untagged) return `${name} has no rows without a utility. There is no untagged ${name} data.`;
  if (utility !== null && utility !== UNTAGGED && !(utility in entry.utilities)) return `${coverageSummary(dataset, name)}. This utility has no ${name} data.`;
  const window = coverageWindow(dataset, utility);
  if (!window) return null;
  if (end >= window[0] && start <= window[1]) return emptyYearsReason(dataset, name, window, start, end);
  const who = utility === null ? '' : ` for ${label(utility)}`;
  return end < window[0]
    ? `${name}${who} starts on ${window[0]}. There is no ${name} data for this period.`
    : `${name} ends on ${window[1]}. There is no ${name} data for this period.`;
}
