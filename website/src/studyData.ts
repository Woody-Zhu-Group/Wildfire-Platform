// Sectionalization and fast-trip planning study (arXiv:2604.01232): the
// optimization grid ingested by analysis/sectionalization/ingest.py into
// website/data/optimization_grid/. Pure helpers; the panels are in
// OptimizationStudy.tsx. Nothing here calls a service.

export type ParamKey = 'C' | 'B' | 'W' | 'E' | 'G';
export const PARAM_KEYS: readonly ParamKey[] = ['C', 'B', 'W', 'E', 'G'];
export type StudyView = 'methods' | 'sensitivity' | 'allocation' | 'map' | 'summary';
export const STUDY_VIEWS: readonly StudyView[] = ['methods', 'sensitivity', 'allocation', 'map', 'summary'];
export type MethodFamily = 'robust' | 'deterministic' | 'planning';

export interface StudyMethod { slug: string; label: string; short: string; family: MethodFamily; description: string }
export interface StudyParameter { key: ParamKey; label: string; unit: string; description: string; values: number[] }
interface Columns<T> { [column: string]: T[] }
export interface StudyData {
  version: 1;
  source: { paper: string; complete_setups: number; setups: number; runs: number; circuits: number; skipped_setups?: number };
  methods: StudyMethod[];
  parameters: StudyParameter[];
  fixed: { delta: number; alpha: number };
  baselineExposure: number[];
  /**
   * Not a grid method: the budget filled by predicted population-weighted
   * exposure, no fast-trip, the same test-year PSPS response
   * (analysis/sectionalization/ingest.py ranking_baselines). Setup-major,
   * one value per setup and replication; null for an incomplete setup.
   */
  rankingExposure: (number | null)[];
  coverage: Record<string, number[]>;
  setups: { id: string[]; complete: boolean[] } & Record<ParamKey, number[]>;
  runs: {
    setup: number[]; method: number[]; exp: number[];
    exposure: number[]; exposureFilled: number[]; plannedExposure: (number | null)[];
    sectionalized: number[]; sectionalizedFilled: number[]; fastTrip: number[]; pspsPlanned: number[]; pspsRealized: number[];
    saifiFastTrip: number[]; saifiPspsPlanned: number[]; saifiPspsRealized: number[];
    populationSectionalized: number[]; circuitWidth: (number | null)[]; elapsedSec: number[]; timedOut: boolean[];
  };
}

const RUN_COLUMNS = ['setup', 'method', 'exp', 'exposure', 'exposureFilled', 'plannedExposure', 'sectionalized', 'sectionalizedFilled',
  'fastTrip', 'pspsPlanned', 'pspsRealized', 'saifiFastTrip', 'saifiPspsPlanned', 'saifiPspsRealized', 'populationSectionalized',
  'circuitWidth', 'elapsedSec', 'timedOut'] as const;

/**
 * ``solved``: the plans as the grid solved them. ``filled``: each plan's unused
 * sectionalization budget completed by predicted exposure and the test-year PSPS
 * response re-solved (analysis/sectionalization/model.py fill_sectionalization).
 * Sectionalization is free in the model beyond its budget, so filling never
 * raises exposure; it separates the value of the uncertainty set from how many
 * circuits a plan happened to sectionalize.
 */
export type PlanVariant = 'solved' | 'filled';
// Metrics recomputed for filled plans; the others describe the solved plan only.
export const FILLED_METRICS: readonly MetricId[] = ['risk_reduction', 'gain_planning', 'gain_co_optimized', 'exposure', 'sectionalized', 'fast_trip', 'saifi_fast_trip'];

/** The validated study plus an index from (setup, method, replication) to a run row. */
export interface Study { data: StudyData; replications: number; lookup: Int32Array }

// Shown in the panel's data notes disclosure.
export const STUDY_NOTES: readonly string[] = [
  'Semi-synthetic planning study (Chen, Zhu, and Sioshansi, arXiv:2604.01232): 803 HFTD Tier 2 and 3 distribution circuits of the case-study utility in the paper, test-year ignitions simulated from a fitted weather and ignition model, five replications per setup. These are model outcomes, not recorded events or utility plans.',
  'Exposure is population-weighted ignitions left after fast-trip and PSPS. Each run\'s realized value was re-checked by re-solving its PSPS response (largest difference 0.01%).',
];

export const PROPOSED_METHOD = 'group_conformal_random';
export const DEFAULT_SETUP_ID = 'C30-B20-W20-E80-G90';
export const DEFAULT_VISIBLE_METHODS: readonly string[] = ['group_conformal_random', 'co_optimized', 'planning_only'];

// Method colors are CSS tokens (--method-N, N = method order) in index.css.

function fail(message: string): never {
  throw new Error(`Optimization study data is invalid: ${message}`);
}
const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

export function validateStudy(raw: unknown): Study {
  const data = raw as StudyData;
  if (!data || typeof data !== 'object' || data.version !== 1) fail('unsupported version');
  if (!Array.isArray(data.methods) || data.methods.length < 2) fail('methods missing');
  const slugs = data.methods.map(method => method.slug);
  if (new Set(slugs).size !== slugs.length) fail('duplicate method');
  for (const slug of [PROPOSED_METHOD, 'co_optimized', 'planning_only']) if (!slugs.includes(slug)) fail(`method ${slug} missing`);
  if (!Array.isArray(data.parameters) || data.parameters.map(p => p.key).join() !== PARAM_KEYS.join()) fail('parameters must be C, B, W, E, G');
  if (!Array.isArray(data.baselineExposure) || !data.baselineExposure.length || !data.baselineExposure.every(v => isNumber(v) && v > 0)) fail('baseline exposure missing');
  const setups = data.setups;
  const nSetups = setups?.id?.length ?? 0;
  if (!nSetups || new Set(setups.id).size !== nSetups) fail('setup ids missing or repeated');
  for (const key of PARAM_KEYS) if (setups[key]?.length !== nSetups || !setups[key].every(isNumber)) fail(`setup column ${key}`);
  if (setups.complete?.length !== nSetups) fail('setup completeness');
  const runs = data.runs as unknown as Columns<unknown>;
  const n = data.runs?.setup?.length ?? 0;
  if (!n) fail('no runs');
  for (const column of RUN_COLUMNS) if (!Array.isArray(runs[column])) fail(`run column ${column} missing`);
  for (const [column, values] of Object.entries(runs)) if (!Array.isArray(values) || values.length !== n) fail(`run column ${column} has the wrong length`);
  const replications = data.baselineExposure.length;
  if (!Array.isArray(data.rankingExposure) || data.rankingExposure.length !== nSetups * replications
    || !data.rankingExposure.every(value => value === null || (isNumber(value) && value >= 0))) fail('ranking reference has the wrong shape');
  const lookup = new Int32Array(nSetups * data.methods.length * replications).fill(-1);
  for (let row = 0; row < n; row++) {
    const setup = data.runs.setup[row], method = data.runs.method[row], exp = data.runs.exp[row];
    if (!(setup >= 0 && setup < nSetups && method >= 0 && method < data.methods.length && exp >= 0 && exp < replications)) fail(`run ${row} is out of range`);
    if (!isNumber(data.runs.exposure[row]) || data.runs.exposure[row] < 0) fail(`run ${row} has no exposure`);
    const slot = (setup * data.methods.length + method) * replications + exp;
    if (lookup[slot] !== -1) fail(`run ${row} repeats a setup, method and replication`);
    lookup[slot] = row;
  }
  setups.complete.forEach((complete, setup) => {
    if (!complete) return;
    for (let slot = setup * data.methods.length * replications; slot < (setup + 1) * data.methods.length * replications; slot++) {
      if (lookup[slot] === -1) fail(`setup ${setups.id[setup]} is marked complete but has missing runs`);
    }
  });
  return { data, replications, lookup };
}

export function runRow(study: Study, setup: number, method: number, exp: number): number {
  if (setup < 0 || method < 0) return -1;
  return study.lookup[(setup * study.data.methods.length + method) * study.replications + exp] ?? -1;
}
export const methodIndex = (study: Study, slug: string) => study.data.methods.findIndex(method => method.slug === slug);

export type MetricId = 'risk_reduction' | 'gain_planning' | 'gain_co_optimized' | 'exposure' | 'sectionalized'
  | 'fast_trip' | 'psps_realized' | 'saifi_fast_trip' | 'saifi_psps' | 'plan_ratio' | 'solve_seconds';
export type MetricUnit = 'percent' | 'count' | 'exposure' | 'saifi' | 'ratio' | 'seconds';
export interface MetricDef { id: MetricId; label: string; short: string; unit: MetricUnit; description: string }
export const METRICS: readonly MetricDef[] = [
  { id: 'risk_reduction', label: 'Risk reduction', short: 'Risk reduction', unit: 'percent', description: 'Share of unmitigated, population-weighted ignition exposure removed in the test year.' },
  { id: 'gain_planning', label: 'Gain over planning only', short: 'Gain vs planning only', unit: 'percent', description: 'Share of the planning-only plan\'s remaining exposure this plan removes, same setup and replication.' },
  { id: 'gain_co_optimized', label: 'Gain over co-optimized', short: 'Gain vs co-optimized', unit: 'percent', description: 'Share of the co-optimized plan\'s remaining exposure this plan removes, same setup and replication.' },
  { id: 'exposure', label: 'Remaining exposure', short: 'Remaining exposure', unit: 'exposure', description: 'Population-weighted ignitions left after fast-trip and PSPS in the test year.' },
  { id: 'sectionalized', label: 'Sectionalized circuits', short: 'Sectionalized', unit: 'count', description: 'Circuits prepared for sectional shutoff.' },
  { id: 'fast_trip', label: 'Fast-trip circuits', short: 'Fast-trip', unit: 'count', description: 'Circuits given fast-trip settings for the year.' },
  { id: 'psps_realized', label: 'Circuits shut off', short: 'Shut off', unit: 'count', description: 'Circuits de-energized by the optimal PSPS response to the test-year ignitions. Optimal sets can tie, so compare exposure, not this count.' },
  { id: 'saifi_fast_trip', label: 'SAIFI from fast-trip', short: 'Fast-trip SAIFI', unit: 'saifi', description: 'Expected interruptions per customer from fast-trip settings.' },
  { id: 'saifi_psps', label: 'SAIFI from PSPS', short: 'PSPS SAIFI', unit: 'saifi', description: 'Interruptions per customer from the test-year PSPS response.' },
  { id: 'plan_ratio', label: 'Planned / realized exposure', short: 'Plan / realized', unit: 'ratio', description: 'Exposure the plan expected (worst case for robust plans, predicted mean for co-optimized) over the exposure realized.' },
  { id: 'solve_seconds', label: 'Solve time', short: 'Solve time', unit: 'seconds', description: 'Planning solve time; robust solves stop at 240 seconds.' },
];
export const metricDef = (id: MetricId) => METRICS.find(metric => metric.id === id) ?? METRICS[0];

export function metricValue(study: Study, setup: number, method: number, exp: number, metric: MetricId, variant: PlanVariant = 'solved'): number | null {
  const row = runRow(study, setup, method, exp);
  if (row < 0) return null;
  if (variant === 'filled' && !FILLED_METRICS.includes(metric)) return null;
  const runs = study.data.runs;
  const exposures = variant === 'filled' ? runs.exposureFilled : runs.exposure;
  const exposure = exposures[row];
  const relative = (baselineSlug: string) => {
    const other = runRow(study, setup, methodIndex(study, baselineSlug), exp);
    if (other < 0) return null;
    const base = exposures[other];
    return base > 0 ? 1 - exposure / base : null;
  };
  switch (metric) {
    case 'risk_reduction': return 1 - exposure / study.data.baselineExposure[exp];
    case 'gain_planning': return relative('planning_only');
    case 'gain_co_optimized': return relative('co_optimized');
    case 'exposure': return exposure;
    case 'sectionalized': return (variant === 'filled' ? runs.sectionalizedFilled : runs.sectionalized)[row];
    case 'fast_trip': return runs.fastTrip[row];
    case 'psps_realized': return runs.pspsRealized[row];
    case 'saifi_fast_trip': return runs.saifiFastTrip[row];
    case 'saifi_psps': return runs.saifiPspsRealized[row];
    case 'plan_ratio': { const planned = runs.plannedExposure[row]; return planned === null || exposure <= 0 ? null : planned / exposure; }
    case 'solve_seconds': return runs.elapsedSec[row];
  }
}

export interface Spread { mean: number | null; min: number | null; max: number | null; values: (number | null)[] }

// Metrics the ranking reference can show: it has an exposure but no solve, set, or plan of its own.
export const RANKING_METRICS: readonly MetricId[] = ['risk_reduction', 'gain_planning', 'gain_co_optimized', 'exposure'];
export const RANKING_LABEL = 'Rank by predicted exposure';
export const RANKING_NOTE = 'Not in the grid: sectionalize the circuits with the highest predicted population-weighted exposure up to the budget, no fast-trip, and the same test-year PSPS response. It uses only the predicted means every plan is built from.';

/** The ranking reference's metric for one setup and replication, paired with the same draw. */
export function rankingValue(study: Study, setup: number, exp: number, metric: MetricId, variant: PlanVariant = 'solved'): number | null {
  if (!RANKING_METRICS.includes(metric)) return null;
  const exposure = study.data.rankingExposure[setup * study.replications + exp];
  if (exposure === null || exposure === undefined) return null;
  const relative = (slug: string) => {
    const row = runRow(study, setup, methodIndex(study, slug), exp);
    if (row < 0) return null;
    const base = (variant === 'filled' ? study.data.runs.exposureFilled : study.data.runs.exposure)[row];
    return base > 0 ? 1 - exposure / base : null;
  };
  switch (metric) {
    case 'risk_reduction': return 1 - exposure / study.data.baselineExposure[exp];
    case 'gain_planning': return relative('planning_only');
    case 'gain_co_optimized': return relative('co_optimized');
    default: return exposure;
  }
}
function spreadOf(values: (number | null)[]): Spread {
  const present = values.filter((value): value is number => value !== null);
  if (!present.length) return { mean: null, min: null, max: null, values };
  return { mean: present.reduce((sum, value) => sum + value, 0) / present.length, min: Math.min(...present), max: Math.max(...present), values };
}
/** One method's metric across the replications of one setup. */
export function spread(study: Study, setup: number, method: number, metric: MetricId, variant: PlanVariant = 'solved'): Spread {
  return spreadOf(Array.from({ length: study.replications }, (_, exp) => metricValue(study, setup, method, exp, metric, variant)));
}
/** Share of paired draws (complete setups) in which a method leaves less exposure than the ranking reference. */
export function rankingWinShare(study: Study, method: number, variant: PlanVariant = 'solved'): { share: number; pairs: number } {
  let wins = 0, pairs = 0;
  study.data.setups.complete.forEach((complete, setup) => {
    if (!complete) return;
    for (let exp = 0; exp < study.replications; exp++) {
      const value = metricValue(study, setup, method, exp, 'exposure', variant), reference = rankingValue(study, setup, exp, 'exposure');
      if (value === null || reference === null) continue;
      pairs++;
      if (value < reference) wins++;
    }
  });
  return { share: pairs ? wins / pairs : 0, pairs };
}
export function rankingSpread(study: Study, setup: number, metric: MetricId, variant: PlanVariant = 'solved'): Spread {
  return spreadOf(Array.from({ length: study.replications }, (_, exp) => rankingValue(study, setup, exp, metric, variant)));
}

export function formatMetric(unit: MetricUnit, value: number | null): string {
  if (value === null || !Number.isFinite(value)) return 'No data';
  switch (unit) {
    case 'percent': return `${(value * 100).toFixed(1)}%`;
    case 'count': return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
    case 'exposure': return value >= 1e6 ? `${(value / 1e6).toFixed(2)}M` : value >= 1e3 ? `${(value / 1e3).toFixed(1)}K` : value.toFixed(0);
    case 'saifi': return value.toFixed(3);
    case 'ratio': return `${value.toFixed(2)}×`;
    case 'seconds': return value < 10 ? `${value.toFixed(1)} s` : `${Math.round(value)} s`;
  }
}

export function formatParam(key: ParamKey, value: number): string {
  return key === 'W' || key === 'G' ? value.toFixed(2) : `${Math.round(value * 100)}%`;
}
export function setupValues(study: Study, setup: number): Record<ParamKey, number> {
  return Object.fromEntries(PARAM_KEYS.map(key => [key, study.data.setups[key][setup]])) as Record<ParamKey, number>;
}
export function setupLabel(study: Study, setup: number): string {
  const values = setupValues(study, setup);
  return PARAM_KEYS.map(key => `${key} ${formatParam(key, values[key])}`).join(' · ');
}
/** A setup's position on one parameter's slider (index into that parameter's grid levels). */
export function sliderIndex(study: Study, setup: number, key: ParamKey): number {
  return levelIndex(study, key, study.data.setups[key][setup]);
}
export function setupByIdOrDefault(study: Study, id: string | undefined): number {
  const index = study.data.setups.id.indexOf(id ?? DEFAULT_SETUP_ID);
  if (index >= 0 && study.data.setups.complete[index]) return index;
  const fallback = study.data.setups.id.indexOf(DEFAULT_SETUP_ID);
  return fallback >= 0 ? fallback : study.data.setups.complete.indexOf(true);
}

const sameValue = (a: number, b: number) => Math.abs(a - b) < 1e-9;
const levelIndex = (study: Study, key: ParamKey, value: number) => study.data.parameters.find(p => p.key === key)!.values.findIndex(v => sameValue(v, value));

/**
 * The run setup with ``key = value`` that changes the other parameters least.
 * The grid is not a full factorial (for example, a 20% sectionalization budget
 * was only run with a 10% fast-trip budget), so a choice can move other knobs;
 * ``changed`` names them so the panel can say so.
 */
export function snapSetup(study: Study, current: number, key: ParamKey, value: number): { index: number; changed: ParamKey[] } {
  const now = setupValues(study, current);
  let best = -1, bestCost = Infinity;
  study.data.setups.id.forEach((_, index) => {
    if (!study.data.setups.complete[index] || !sameValue(study.data.setups[key][index], value)) return;
    let cost = 0;
    PARAM_KEYS.forEach((other, position) => {
      if (other === key) return;
      const distance = Math.abs(levelIndex(study, other, study.data.setups[other][index]) - levelIndex(study, other, now[other]));
      cost += distance * 10 ** (PARAM_KEYS.length - position); // earlier parameters are kept first
    });
    if (cost < bestCost) { best = index; bestCost = cost; }
  });
  if (best < 0) return { index: current, changed: [] };
  const next = setupValues(study, best);
  return { index: best, changed: PARAM_KEYS.filter(other => other !== key && !sameValue(next[other], now[other])) };
}

/** Levels of ``param`` that share the most setups with identical other parameters. */
export function matchedLevels(study: Study, param: ParamKey): { levels: number[]; combos: string[] } {
  const others = PARAM_KEYS.filter(key => key !== param);
  const byLevel = new Map<number, Set<string>>();
  study.data.setups.id.forEach((_, index) => {
    if (!study.data.setups.complete[index]) return;
    const level = study.data.setups[param][index];
    const combo = others.map(key => study.data.setups[key][index]).join('|');
    const found = [...byLevel.keys()].find(existing => sameValue(existing, level));
    const set = byLevel.get(found ?? level) ?? new Set<string>();
    set.add(combo); byLevel.set(found ?? level, set);
  });
  const levels = [...byLevel.keys()].sort((a, b) => a - b);
  let best: { levels: number[]; combos: string[] } = { levels: [], combos: [] };
  for (let mask = 1; mask < 1 << levels.length; mask++) {
    const chosen = levels.filter((_, i) => mask & (1 << i));
    if (chosen.length < 2) continue;
    const combos = [...byLevel.get(chosen[0])!].filter(combo => chosen.every(level => byLevel.get(level)!.has(combo)));
    const size = combos.length * chosen.length, bestSize = best.combos.length * best.levels.length;
    if (size > bestSize || (size === bestSize && chosen.length > best.levels.length)) best = { levels: chosen, combos: combos.sort() };
  }
  return best;
}

export interface SensitivityPoint { level: number; value: number | null; setups: number }
/** Use ``RANKING_SERIES`` as the method index to follow the ranking reference. */
export const RANKING_SERIES = -1;
const seriesMean = (study: Study, setup: number, method: number, metric: MetricId, variant: PlanVariant) =>
  (method === RANKING_SERIES ? rankingSpread(study, setup, metric, variant) : spread(study, setup, method, metric, variant)).mean;
/**
 * A method's metric at each level of ``param``. ``slice`` holds the other four
 * parameters at the selected setup. ``matched`` averages each level over the
 * same set of other-parameter combinations, so levels are never compared
 * across different backgrounds; levels outside that matched set show no data.
 */
export function sensitivity(study: Study, base: number, param: ParamKey, metric: MetricId, method: number, mode: 'slice' | 'matched', variant: PlanVariant = 'solved'): SensitivityPoint[] {
  const parameter = study.data.parameters.find(p => p.key === param)!;
  const others = PARAM_KEYS.filter(key => key !== param);
  if (mode === 'slice') {
    const now = setupValues(study, base);
    return parameter.values.map(level => {
      const index = study.data.setups.id.findIndex((_, i) => study.data.setups.complete[i] && sameValue(study.data.setups[param][i], level)
        && others.every(key => sameValue(study.data.setups[key][i], now[key])));
      return { level, value: index < 0 ? null : seriesMean(study, index, method, metric, variant), setups: index < 0 ? 0 : 1 };
    });
  }
  const matched = matchedLevels(study, param);
  const combos = new Set(matched.combos);
  return parameter.values.map(level => {
    if (!matched.levels.some(value => sameValue(value, level))) return { level, value: null, setups: 0 };
    const means: number[] = [];
    study.data.setups.id.forEach((_, index) => {
      if (!study.data.setups.complete[index] || !sameValue(study.data.setups[param][index], level)) return;
      if (!combos.has(others.map(key => study.data.setups[key][index]).join('|'))) return;
      const mean = seriesMean(study, index, method, metric, variant);
      if (mean !== null) means.push(mean);
    });
    return { level, value: means.length ? means.reduce((sum, value) => sum + value, 0) / means.length : null, setups: means.length };
  });
}

export interface Allocation { fastTrip: number; psps: number; pspsPlanned: number; unused: number }
/** Mean share of the SAIFI cap spent on fast-trip and on PSPS across replications. */
export function allocation(study: Study, setup: number, method: number): Allocation | null {
  const cap = study.data.setups.W[setup];
  const rows = Array.from({ length: study.replications }, (_, exp) => runRow(study, setup, method, exp)).filter(row => row >= 0);
  if (!rows.length || !(cap > 0)) return null;
  const mean = (column: number[]) => rows.reduce((sum, row) => sum + column[row], 0) / rows.length / cap;
  const fastTrip = mean(study.data.runs.saifiFastTrip), psps = mean(study.data.runs.saifiPspsRealized);
  return { fastTrip, psps, pspsPlanned: mean(study.data.runs.saifiPspsPlanned), unused: Math.max(0, 1 - fastTrip - psps) };
}

/** Share of (setup, replication) pairs over complete setups where method a has lower exposure than b. */
export function winShare(study: Study, a: number, b: number, variant: PlanVariant = 'solved'): { share: number; pairs: number } {
  const exposures = variant === 'filled' ? study.data.runs.exposureFilled : study.data.runs.exposure;
  let wins = 0, pairs = 0;
  study.data.setups.complete.forEach((complete, setup) => {
    if (!complete) return;
    for (let exp = 0; exp < study.replications; exp++) {
      const ra = runRow(study, setup, a, exp), rb = runRow(study, setup, b, exp);
      if (ra < 0 || rb < 0) continue;
      pairs++;
      if (exposures[ra] < exposures[rb]) wins++;
    }
  });
  return { share: pairs ? wins / pairs : 0, pairs };
}

/** Saved planning study settings are well formed (unknown values would break a stored workspace). */
export function validStudySettings(settings: Record<string, unknown>): boolean {
  const optional = (key: string, check: (value: unknown) => boolean) => settings[key] === undefined || check(settings[key]);
  return optional('studyView', value => STUDY_VIEWS.includes(value as StudyView))
    && optional('studySetup', value => typeof value === 'string')
    && optional('studyMetric', value => METRICS.some(metric => metric.id === value))
    && optional('studyParam', value => PARAM_KEYS.includes(value as ParamKey))
    && optional('studyMode', value => value === 'slice' || value === 'matched')
    && optional('studyMethods', value => Array.isArray(value) && value.every(item => typeof item === 'string'))
    && optional('studyMethod', value => typeof value === 'string')
    && optional('studyReplication', value => Number.isInteger(value) && (value as number) >= -1)
    && optional('studyFill', value => typeof value === 'boolean');
}

export interface StudyCircuits { unit: string[]; circuitId: string[]; division: string[]; tier: (2 | 3 | null)[]; population: number[]; lines: number[][][] }
export function validateCircuits(raw: unknown): StudyCircuits {
  const data = raw as StudyCircuits;
  const n = data?.unit?.length ?? 0;
  if (!n) fail('no circuits');
  for (const column of ['circuitId', 'division', 'tier', 'population', 'lines'] as const) if (data[column]?.length !== n) fail(`circuit column ${column}`);
  if (!data.lines.every(parts => parts.length > 0 && parts.every(part => part.length >= 4 && part.length % 2 === 0))) fail('circuit geometry');
  return data;
}

export interface SetupDecisions { setup: string; circuits: number; methods: Record<string, string[]> }
export function validateDecisions(raw: unknown, setupId: string, circuits: number, methods: readonly string[], replications: number): SetupDecisions {
  const data = raw as SetupDecisions;
  if (data?.setup !== setupId || data.circuits !== circuits) fail(`decisions for ${setupId}`);
  for (const slug of methods) {
    const codes = data.methods?.[slug];
    if (!Array.isArray(codes) || codes.length !== replications || !codes.every(code => typeof code === 'string' && code.length === circuits && /^[0-9a-f]*$/.test(code))) fail(`decisions for ${setupId} ${slug}`);
  }
  return data;
}

export type DecisionClass = 'psps' | 'fast_trip' | 'sectionalized' | 'none';
export const DECISION_CLASSES: readonly DecisionClass[] = ['sectionalized', 'fast_trip', 'psps', 'none'];
// Flags packed by analysis/sectionalization/model.py encode_decisions.
const SECTIONALIZED = 1, FAST_TRIP = 2, PSPS_REALIZED = 8;
export function decisionClass(flag: string): DecisionClass {
  const value = parseInt(flag, 16);
  return value & PSPS_REALIZED ? 'psps' : value & FAST_TRIP ? 'fast_trip' : value & SECTIONALIZED ? 'sectionalized' : 'none';
}
/** For each circuit, the number of replications in which the plan sectionalized it. */
export function sectionalizedCounts(codes: readonly string[]): number[] {
  const counts = new Array<number>(codes[0]?.length ?? 0).fill(0);
  for (const code of codes) for (let i = 0; i < code.length; i++) if (parseInt(code[i], 16) & SECTIONALIZED) counts[i]++;
  return counts;
}
