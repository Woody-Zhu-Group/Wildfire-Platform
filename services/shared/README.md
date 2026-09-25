# services/shared

Backend-only helpers shared by the FastAPI services. Not to be confused with the repo-root `shared/` package, which holds database settings (`shared/db.py`), filesystem roots (`shared/paths.py`) and the generated frontend catalogs.

## `dataset_registry.py`

One catalog of the warehouse datasets. Each `DatasetSpec` in `DATASETS` records the warehouse table, aliases, visualization key, agent key, map style, allowed group-by fields, summary metrics, filters, rank pairs, routes and caveat IDs for one dataset. Which utilities and dates a dataset covers is measured, never declared: the loaders write `shared/dataset_coverage.json` (`db/loaders/coverage.py`), and the registry loads it as `DATASET_COVERAGE`. `dataset_coverage_gap(dataset, utilities, start, end, periods=...)` returns the gap when no named utility has rows in any asked period (a utility is covered from its first row to the dataset's last row; with no utility, the dataset's own dates), with the measured reason, the other utilities covered in the period, the named utilities' own windows, and the alternatives with measured rows for every named utility in every period (`rows_in_period`: the period holds a whole year with rows, or the utility's first or last row date; a window that only overlaps is not enough). A period whose every year has no rows in the dataset at all is not covered. It raises on an unknown dataset, so a coverage check never passes silently. `coverage_window`, `covered_utilities`, `coverage_summary`, `dataset_years` (years with rows, for a dataset, a utility, or the untagged rows), `warehouse_year_range` (the first and last year any dataset has rows; the time resolver's `DATA_YEAR_MIN`), `rows_in_period`, and `partial_coverage_note` (a covered period that runs past the window) read the same file. Coverage is measured on the rows each count reads: `query_definitions` on a spec (CAL FIRE: the default incident types, all, untyped, keyed by `incident_type_mode`; predicates from `CALFIRE_INCIDENT_TYPE_MODE_SQL` in `naming.py`, the default first) is what the loader measures, `definition_words` names each definition's rows in a reason, and `definition_argument` is the tool argument that picks one. Every lookup above takes `definition=` (None: the default query); `call_definition(dataset, arguments)` reads it from a call, `definition_for_filter(dataset, value)` from a data_query `incident_type` value (a single stored type has no measured definition and raises), and `default_definition(dataset)` names the default. A coverage file measured for other definitions than the registry's is refused on first read (`_stale_definitions`), as is a missing one; importing the registry never reads coverage, so the loader can regenerate it. Otherwise a spec carries wording only: `no_utility_reason` (the US sample has no utility column) and `not_covered_alternatives` (the datasets to offer, in order, each still filtered by measured coverage). `COMPARISON_METRIC_DATASETS` maps each comparison metric to its dataset; it is the comparison service's own map (`services.comparison.metrics.METRIC_DATASETS`, from which `METRICS` is derived), read on first use because that module imports this one, and `tests/agent/test_coverage_one_source.py` ties it to `METRICS`, the `MetricName` and agent `Metric` literals, and the service queries. `single_utility_dataset(dataset)` is true when measured coverage has exactly one utility, so the dataset has no utility dimension to rank. `not_covered_message(gap)` and `not_covered_question(gap)` build the not-covered sentence and clarification from the gap, stating each offer as the records it holds (`alternatives_records`: "CPUC ignitions have records for SDG&E in 2022") and saying when an offer drops a filter coverage does not measure (`gap["unmeasured_filters"]`, set by `services/agent/coverage.py`), so no caller writes that text itself. The agent router, Jev templates, slot planner, executor, the comparison, data query, and visualization services, and the website (`website/src/coverage.ts`, reading the same JSON file) all read coverage from here; none names a utility, and `tests/agent/test_measured_coverage.py` and `website/tests/measured-coverage.test.ts` fail if one does. `REASON_CIRCUITS_SCOPE` (the per-circuit denominator reason) is also built from the measured circuits inventory, on first use.

Imported by:

- `services/data_query` (grouped counts, rank pairs, summary metric IDs, US ignitions metadata)
- `services/visualization` (dataset styles, dataset-name parsing, US ignitions metadata)
- `services/comparison` (null-reason strings)
- `services/agent` (dataset values, caveat IDs, view labels, `HDW_YEARS`, data query paths)
- `db/loaders` and `scripts/generate_frontend_registry.py` (naming conventions, below)

The registry also re-exports every name in `naming.py`, so a caller imports a naming convention from `services.shared.dataset_registry` like any other catalog value.

## Where every naming convention lives

Each convention is defined once. Datasets are defined on the registry's `DatasetSpec` entries in `dataset_registry.py`; everything else is in `naming.py`, which imports only the standard library so services, db loaders, and scripts can all use it. Callers import from `services.shared.dataset_registry`. `tests/test_naming_single_source.py` fails if another module defines its own copy of a convention:

- a list, tuple, set, dict, or Enum class body (member names and values together) holding two or more utility spellings, two or more county names, both HFTD tier names, or two or more EPSS cause codes;
- two or more of the CAL FIRE non-wildfire types (`"Earthquake"`, `"Flood"`, `"Hazmat"`), which define the default, or the old default pair (`"Wildfire"`, `"Fire"`), as a collection, as SQL (`'Flood', 'Hazmat'`), or as a parameter (`Earthquake,Flood`); the website scan catches the non-wildfire list too;
- a dict mapping dataset aliases, dataset labels, or EPSS cause codes to their word forms;
- any string with `|` alternation (f-strings checked with their literal parts joined) naming two or more utilities (each utility counted once however it is spelled), two or more counties, or three or more datasets;
- a website source file that hardcodes the county list, a utility list, or both tier names anywhere in the file, not just on one line.

Prose is skipped: a string with a sentence break, and the `description`, `detail`, `help`, or `summary` argument of a call (API parameter descriptions, error messages). Tests and `services/data_query/_smoke_test.py` are not scanned because they assert the values.

Where two callers used different spellings of the same thing, both were kept under separate names with a comment (for example the harness model-argument aliases are narrower than the data query filter aliases). Merging them would change what a caller accepts or prints.

| Convention | Defined in | Names | Used by |
|---|---|---|---|
| Dataset keys, aliases, viz and agent keys, labels, styles | `dataset_registry.py` | `DATASETS`, `ALIASES`, `STAT_LABELS`, `DQ_TO_VIZ`, `COUNT_MAP_DATASETS`, `LAYER_VIZ_KEYS` | all services, router, views, eval scripts, frontend generator |
| Harness dataset argument aliases | `dataset_registry.py` | `ARGUMENT_VIZ_DATASET_ALIASES`, `ARGUMENT_RECORDS_DATASET_ALIASES` | `agent/argument_normalize.py` |
| Clarification dataset nouns | `dataset_registry.py` | `CLARIFY_DATASET_LABELS` | `agent/clarify_missing.py` |
| Dataset wording in questions | `naming.py` | `DATASET_QUESTION_PATTERNS`, `BARE_IGNITIONS_PATTERN`, `BARE_OUTAGES_PATTERN`, `EVENT_DATASET_WORDS`, `EXPECTED_FACT_DATASET_PATTERNS` | router, orchestrator, label rules, Jev expected facts |
| US sample wording | `naming.py` | `US_SAMPLE_NAMED_PATTERN`, `SAMPLE_BEFORE_IGNITIONS_PATTERN`, `ALL_CAUSES_*_PATTERN`, `CPUC_OR_UTILITY_BEFORE_IGNITIONS_PATTERN`, `IGNITION_QUALIFIER_PATTERN` | router |
| Utility codes | `naming.py` | `UTILITY_CODES`, `KNOWN_UTILITIES`, `UNTAGGED_UTILITY`, `RISK_MODEL_UTILITIES` | filters, agent schemas, risk `place.py`, PSPS loader, generator |
| Utility spellings accepted | `naming.py` | `UTILITY_FILTER_KEYS`, `UTILITY_FILTER_SUFFIXES` (service filters); `UTILITY_ARGUMENT_ALIASES` (harness); `IOU_PUBLISHER_UTILITY_CODES` (IOU loader) | `data_query/filters.py`, `agent/argument_normalize.py`, `db/loaders` |
| Utility labels and names | `naming.py` | `UTILITY_DISPLAY_LABELS`, `WORKSPACE_UTILITIES`, `UTILITY_CLARIFY_LABELS`, `UTILITY_FULL_NAMES`, `UTILITY_POSSESSIVE_NAMES` | grouped counts, website, clarifications, Jev questions, point answers |
| Row code and label | `naming.py` | `group_code_and_label` | `/rank`, `/grouped-counts`, and `/compare-utilities` rows; agent ranking and comparison summaries and answers; website `groupNames` mirrors it |
| Utility wording in questions | `naming.py` | `UTILITY_PATTERNS`, `UTILITY_ADVICE_SUBJECT_WORDS`, `UNTAGGED_UTILITY_PATTERN` | router, grounding, Jev |
| County names and aliases | `naming.py` | `CALIFORNIA_COUNTIES`, `COUNTY_ALIASES`, `COUNTY_SUFFIX_PATTERN`, `COUNTIES_NEEDING_QUALIFIER` | `counties.py`, router, grounding, clarifications, Jev, website |
| HFTD tiers | `naming.py` | `HFTD_TIER_BY_NUMBER`, `HFTD_TIER_NAMES`, `HFTD_TIERS`, `TIER_MENTION_PATTERN`, `TIER_LIST_PATTERN`, `TIER_WORD_PATTERN`, `TIER_DIGIT_PATTERN` | filters, comparison, agent schemas, router, grounding, HFTD loader |
| EPSS causes | `naming.py` | `EPSS_CAUSE_CODE_WORDS`, `EPSS_CAUSE_CODES_LEFT_ALONE`, `EPSS_UNKNOWN_CAUSE_SOURCE_SPELLINGS` | `epss_causes.py`, EPSS loader |
| EPSS outage types | none | No fixed list: filters resolve against stored values (`stored_values.py`) | filters |
| CAL FIRE incident types | `naming.py` | `CALFIRE_NON_WILDFIRE_INCIDENT_TYPES` (the default: every incident except these), `CALFIRE_REVIEWED_WILDFIRE_INCIDENT_TYPES`, `CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM`, `CALFIRE_DEFAULT_DESCRIPTION`, `calfire_default_type_sql`, `calfire_incident_type_filter`, `calfire_counted_by_default`, `calfire_untyped_count_sql`, `calfire_missing_counts_sql`, `calfire_missing_counts_meta`, `CALFIRE_UNTYPED_COUNTED_KEY`, `CALFIRE_UNTAGGED_COUNTED_KEY`, `CALFIRE_UNTAGGED_EXCLUDED_KEY`, `CALFIRE_INCIDENT_TYPE_KEYWORDS`, `INCIDENT_TYPE_MODES`, `DEFAULT_INCIDENT_TYPE_MODE`, `ALL_INCIDENT_TYPES_PATTERN`, `UNTYPED_INCIDENT_PATTERN` | data_query, visualization, comparison, agent router, tools, caveats, schemas, grounding, load validation, website (`shared/naming.json`) |
| Missing-value labels | `dataset_registry.py` | `NOT_RECORDED`, `MISSING_LABEL_RANK` | data_query |
| Measures a ranking or comparison can order by | `dataset_registry.py` | `RANK_MEASURES` (derived from `ALLOWED_RANK_PAIRS`), `COMPARE_MEASURES` (per comparison scope), `MEASURE_DATASETS`, `measure_utilities()` (measured: a measure whose dataset has rows for one utility only, EPSS) | decide-mode measure clarification (`services/agent/measure_clarify.py`); the dataset and grouping options in every clarification and the `ranking_missing_slots` text (`services/agent/clarify_missing.py`) |
| Datasets a yearly or seasonal chart reads | `dataset_registry.py` | `SERIES_DATASETS` | router series charts and the `series_mode_missing_dataset` question (`services/agent/routing.py`, `services/agent/clarify_missing.py`) |
| Measure names | `naming.py` | `MEASURE_LABELS`, `MEASURES_NOT_IN_DATA` | decide-mode measure clarification |

The website reads the names it needs from the generated `shared/naming.json` (counties, utility codes and display labels, workspace utilities, tiers) through `website/src/data.ts`.

What stays separate, and why:

- **City and place names** (`services/agent/places.py`, `_CA_CITIES` in `routing.py`): data, not convention. They are 1,619 Census Gazetteer rows and the list of incorporated cities that are not county names. They change when the gazetteer vintage changes, not when a naming rule changes, and no service outside the router matches on them.
- **Dataset capability sets** (which datasets take a county filter, which can share a timeline, which viz keys a view accepts): these say what a dataset can do, not what it is called. They are subsets of registry keys and are not duplicated names.
- **Prose** that mentions names (caveat text, Jev policy sentences, tool descriptions, error messages): wording, pinned in Jev payload hashes and user-facing text.
- **`frontend/assets/js`**: the legacy static map page keeps its own layer labels and utility seed list. It loads no module, so it cannot import the registry; it is out of scope until it is retired or reads `shared/naming.json`.
- **`analysis/`** and **`services/risk_forecasting/legacy/`**: one-off research scripts and superseded reference code, left as written.

Resolved in `rank-code-labels` (issue #89):

- `/rank` with `group_by=utility` keys rows by code (`PGE`) and `/grouped-counts` by display label (`PG&E`). The keys are unchanged; every row of both responses also carries `code` and `label` from `group_code_and_label` (`naming.py`). Utility rows get the registry code and display label whichever form the key is in; a utility the registry does not list, a missing-value row, and every county, circuit, or cause row use the key as both. `/compare-utilities` result rows carry the same two fields (region and period comparisons do not). The agent's ranking and utility comparison answers and the website Comparison panel show `label`; derived-evidence entities and caveat matching still use `key`. The website mirrors the rule as `groupNames` in `website/src/data.ts`, built from the generated `shared/naming.json`, and uses it only as a deploy-order safeguard: if the website or agent is updated before the data query service, rows arrive without `code` or `label` and are filled with the same rule.

Resolved in `naming-followups` (2026-09-24):

- `db/loaders/validate.py` counts typed CAL FIRE rows outside the registry default (`calfire_default_type_sql`), so the 38 `Fire` rows no longer count as non-wildfire. On the local warehouse the count went from 42 to 4 (2 Flood, 1 Earthquake, 1 Hazmat); the line now reads "incident_type outside the Wildfire,Fire default". Since the default change of 2026-09-24 (`docs/DATA_CHANGE_CALFIRE_DEFAULT.md`) it reads "incident_type excluded by the default (Earthquake, Flood, Hazmat): 4", followed by the untyped rows the default counts and any stored type not yet reviewed.
- Every endpoint that takes a dataset accepts the registry aliases. `/rank`, `/grouped-counts`, and `/summary` resolve the name with `parse_dataset` (`services/data_query/filters.py`), and `parse_viz_dataset` falls back to `to_viz_key`, which covers `/map-layer`, `/time-series`, and `/event-detail`. A name the registry does not know still gets each endpoint's own 400.
- `normalize_psps_utility` raises `UnknownPspsUtilityError` on a spelling that matches no utility code. The current `psps_events.geojson` has 61 events in four spellings (SCE 37, PGE 14, SDGE 5, Liberty 5), all known.

### EPSS cause codes (written rule, 2026-09-24)

`EPSS_CAUSE_CODE_WORDS` (defined in `naming.py`, exported by the registry) records that an EPSS cause code and its word form are the same cause. Filters and groupings match both spellings and results display the word form (`services/shared/epss_causes.py` applies it in SQL; `parse_cause` in `services/data_query/filters.py` folds the filter value). The codes occur only in the 2021 rows; from 2022 the source writes words. The rule covers only unambiguous pairs:

| Code | Rows | Word form | Rows | Combined |
|---|---|---|---|---|
| `VEG` | 1 | Vegetation | 1,042 | 1,043 |
| `UNK` | 6 | Unknown | 3,724 | 3,730 |
| `3RD` | 1 | 3rd Party | 914 | 915 |

Left alone (`EPSS_CAUSE_CODES_LEFT_ALONE`): `EF` (1 row, 2021). It reads as "Equipment Failure", but both "Equipment" (290 rows, 2022 only) and "Equipment Failure/Involved" (986 rows, 2023 on) could claim it. Those two are both words, so this rule does not merge them either; that needs its own written rule. The other causes (Animal, Company Initiated, Environmental/External) have no code form. See `docs/DATA_CHANGE_CALFIRE_COUNTIES.md`.

Caveat text is not stored here. The registry holds caveat IDs; the strings live in `services/agent/caveats.py`.

Where two services disagree (for example the US ignitions `notes` string differs between data_query and visualization), the registry keeps both values and says so in a comment rather than merging them.

## Generated frontend catalogs

`shared/datasets.json` and `shared/dataset_caveats.json` are generated from this registry and read by the website (`website/src/data.ts`, `website/src/caveats.ts`). Do not edit them by hand. After changing the registry, regenerate from the repo root:

```bash
python scripts/generate_frontend_registry.py
```

`tests/test_frontend_registry_generated.py` fails if the committed JSON files are out of date. `tests/test_dataset_registry.py` covers the registry itself.
