# analysis/

One-off, read-only investigations of the PostGIS warehouse. Each script queries
live tables through `shared.db`, writes a JSON result file beside itself, and
does not modify warehouse data. Findings are dated snapshots; rerun a script
before quoting its numbers as current.

Run from the repo root (PowerShell):

```powershell
$env:PYTHONPATH = "."
$env:PYTHONIOENCODING = "utf-8"
python analysis/<script>.py
```

| Script | Results | Write-up |
|---|---|---|
| `calfire_2024_jump.py` | `calfire_2024_jump_results.json` | [`calfire-2024-jump.md`](calfire-2024-jump.md): the 2023 to 2024 CAL FIRE count jump (133 to 611) is an incident-map posting change, not a fire-occurrence trend |
| `compare_cpuc_calfire_us.py` | `compare_cpuc_calfire_us_results.json` | [`docs/dataset-comparison-cpuc-calfire-us.md`](../docs/dataset-comparison-cpuc-calfire-us.md): CPUC, CAL FIRE, and US ignitions record different events and cannot be compared or combined as one census |
