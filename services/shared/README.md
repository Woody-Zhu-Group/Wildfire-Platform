# services/shared

Backend-only helpers shared by the FastAPI services. Not to be confused with the repo-root `shared/` package, which holds database settings (`shared/db.py`), filesystem roots (`shared/paths.py`) and the generated frontend catalogs.

## `dataset_registry.py`

One catalog of the warehouse datasets. Each `DatasetSpec` in `DATASETS` records the warehouse table, aliases, visualization key, agent key, map style, allowed group-by fields, summary metrics, filters, rank pairs, routes and caveat IDs for one dataset.

Imported by:

- `services/data_query` (grouped counts, rank pairs, summary metric IDs, US ignitions metadata)
- `services/visualization` (dataset styles, dataset-name parsing, US ignitions metadata)
- `services/comparison` (null-reason strings)
- `services/agent` (dataset values, caveat IDs, view labels, `HDW_YEARS`, data query paths)

Caveat text is not stored here. The registry holds caveat IDs; the strings live in `services/agent/caveats.py`.

Where two services disagree (for example the US ignitions `notes` string differs between data_query and visualization), the registry keeps both values and says so in a comment rather than merging them.

## Generated frontend catalogs

`shared/datasets.json` and `shared/dataset_caveats.json` are generated from this registry and read by the website (`website/src/data.ts`, `website/src/caveats.ts`). Do not edit them by hand. After changing the registry, regenerate from the repo root:

```bash
python scripts/generate_frontend_registry.py
```

`tests/test_frontend_registry_generated.py` fails if the committed JSON files are out of date. `tests/test_dataset_registry.py` covers the registry itself.
