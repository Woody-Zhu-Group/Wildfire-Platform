# services/shared

Backend-only helpers shared by the FastAPI services. Not to be confused with the repo-root `shared/` package, which holds database settings (`shared/db.py`), filesystem roots (`shared/paths.py`) and the generated frontend catalogs.

## `dataset_registry.py`

One catalog of the warehouse datasets. Each `DatasetSpec` in `DATASETS` records the warehouse table, aliases, visualization key, agent key, map style, allowed group-by fields, summary metrics, filters, rank pairs, routes and caveat IDs for one dataset.

Imported by:

- `services/data_query` (grouped counts, rank pairs, summary metric IDs, US ignitions metadata)
- `services/visualization` (dataset styles, dataset-name parsing, US ignitions metadata)
- `services/comparison` (null-reason strings)
- `services/agent` (dataset values, caveat IDs, view labels, `HDW_YEARS`, data query paths)
- `db/loaders` and `scripts/generate_frontend_registry.py` (naming conventions, below)

The registry also re-exports every name in `naming.py`, so a caller imports a naming convention from `services.shared.dataset_registry` like any other catalog value.

## Where every naming convention lives

Each convention is defined once. Datasets are defined on the registry's `DatasetSpec` entries in `dataset_registry.py`; everything else is in `naming.py`, which imports only the standard library so services, db loaders, and scripts can all use it. Callers import from `services.shared.dataset_registry`. `tests/test_naming_single_source.py` fails if another module defines its own list of utility, county, tier, or dataset names or aliases, or a regex naming several of them, and if the website hardcodes the county list or a utility list.

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
| Utility wording in questions | `naming.py` | `UTILITY_PATTERNS`, `UTILITY_ADVICE_SUBJECT_WORDS`, `UNTAGGED_UTILITY_PATTERN` | router, grounding, Jev |
| County names and aliases | `naming.py` | `CALIFORNIA_COUNTIES`, `COUNTY_ALIASES`, `COUNTY_SUFFIX_PATTERN`, `COUNTIES_NEEDING_QUALIFIER` | `counties.py`, router, grounding, clarifications, Jev, website |
| HFTD tiers | `naming.py` | `HFTD_TIER_BY_NUMBER`, `HFTD_TIER_NAMES`, `HFTD_TIERS`, `TIER_MENTION_PATTERN`, `TIER_LIST_PATTERN`, `TIER_WORD_PATTERN`, `TIER_DIGIT_PATTERN` | filters, comparison, agent schemas, router, grounding, HFTD loader |
| EPSS causes | `naming.py` | `EPSS_CAUSE_CODE_WORDS`, `EPSS_CAUSE_CODES_LEFT_ALONE`, `EPSS_UNKNOWN_CAUSE_SOURCE_SPELLINGS` | `epss_causes.py`, EPSS loader |
| EPSS outage types | none | No fixed list: filters resolve against stored values (`stored_values.py`) | filters |
| CAL FIRE incident types | `naming.py` | `CALFIRE_DEFAULT_INCIDENT_TYPES`, `CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM`, `calfire_default_type_sql`, `CALFIRE_INCIDENT_TYPE_KEYWORDS`, `INCIDENT_TYPE_MODES`, `ALL_INCIDENT_TYPES_PATTERN`, `UNTYPED_INCIDENT_PATTERN` | data_query, visualization, comparison, agent schemas, grounding |
| Missing-value labels | `dataset_registry.py` | `NOT_RECORDED`, `MISSING_LABEL_RANK` | data_query |

The website reads the names it needs from the generated `shared/naming.json` (counties, utility codes and display labels, workspace utilities, tiers) through `website/src/data.ts`.

What stays separate, and why:

- **City and place names** (`services/agent/places.py`, `_CA_CITIES` in `routing.py`): data, not convention. They are 1,619 Census Gazetteer rows and the list of incorporated cities that are not county names. They change when the gazetteer vintage changes, not when a naming rule changes, and no service outside the router matches on them.
- **Dataset capability sets** (which datasets take a county filter, which can share a timeline, which viz keys a view accepts): these say what a dataset can do, not what it is called. They are subsets of registry keys and are not duplicated names.
- **Prose** that mentions names (caveat text, Jev policy sentences, tool descriptions, error messages): wording, pinned in Jev payload hashes and user-facing text.
- **`frontend/assets/js`**: the legacy static map page keeps its own layer labels and utility seed list. It loads no module, so it cannot import the registry; it is out of scope until it is retired or reads `shared/naming.json`.
- **`analysis/`** and **`services/risk_forecasting/legacy/`**: one-off research scripts and superseded reference code, left as written.

Open inconsistencies this PR recorded and did not change (each would change behavior):

- `db/loaders/validate.py` counts `non_wildfire_typed` as `incident_type IS DISTINCT FROM 'Wildfire'`, so its 38 `Fire` rows count as non-wildfire, unlike the Wildfire+Fire default everywhere else.
- `/rank` with `group_by=utility` returns codes (`PGE`); `/grouped-counts` returns display labels (`PG&E`).
- The data_query aggregate and rank endpoints accept only canonical dataset keys, not registry aliases, and `parse_viz_dataset` does not accept the `cal fire` or `wildfire_incidents` aliases that `to_canonical` accepts.
- `normalize_psps_utility` passes an unknown source spelling through unchanged instead of failing.

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
