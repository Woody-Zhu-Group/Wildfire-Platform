import catalog from '../../shared/dataset_coverage.json' with {type: 'json'};
import naming from '../../shared/naming.json' with {type: 'json'};

// Dataset coverage as the loaders measured it (db/loaders/coverage.py writes
// shared/dataset_coverage.json; services/shared/dataset_registry.py reads the
// same file). Nothing here declares which utilities or dates a dataset covers.
type Span = {first: string | null; last: string | null; rows: number};
type Entry = Span & {date_column: string | null; utility_dimension: boolean; utilities: Record<string, Span>};

const MEASURED = catalog.datasets as Record<string, Entry>;
// Names as clarification text writes them (PacifiCorp, Bear Valley), as the
// registry's not-covered text does.
const LABELS: Record<string, string> = {...naming.utility_display_labels, ...naming.utility_clarify_labels};
const label = (code: string) => LABELS[code] ?? code;

function names(codes: string[]) {
  const labels = codes.map(label);
  return labels.length <= 2 ? labels.join(' and ') : `${labels.slice(0, -1).join(', ')}, and ${labels.at(-1)}`;
}

/** Utility codes the dataset has rows for. */
export function coveredUtilities(dataset: string): string[] {
  return Object.keys(MEASURED[dataset]?.utilities ?? {});
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
  const first = utility === null ? entry.first : entry.utilities[utility]?.first ?? null;
  return first && entry.last ? [first, entry.last] : null;
}

/** Only the utilities the dataset has rows for, in one clause. */
export function coverageSummary(dataset: string, name: string) {
  return `${name} has rows only for ${names(coveredUtilities(dataset))}`;
}

/**
 * Why the dataset has no rows for this utility (a code) and period, or null
 * when it does. A count outside measured coverage is absent, never zero.
 */
export function coverageReason(dataset: string, name: string, utility: string | null, start: string, end: string): string | null {
  const entry = MEASURED[dataset];
  if (!entry) return null;
  if (utility !== null && !entry.utility_dimension) return `${name} has no utility column. This utility has no ${name} data.`;
  if (utility !== null && !(utility in entry.utilities)) return `${coverageSummary(dataset, name)}. This utility has no ${name} data.`;
  const window = coverageWindow(dataset, utility);
  if (!window || (end >= window[0] && start <= window[1])) return null;
  const who = utility === null ? '' : ` for ${label(utility)}`;
  return end < window[0]
    ? `${name}${who} starts on ${window[0]}. There is no ${name} data for this period.`
    : `${name} ends on ${window[1]}. There is no ${name} data for this period.`;
}
