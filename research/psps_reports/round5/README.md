# Round 5: human audit

A person checks 60 values in the PSPS dataset against the report PDFs, without seeing any model answer. It is the first human check of this dataset. The old test labels were written by a model, not a person (`AUDIT_PLAN.md`, "Why"). `AUDIT_PLAN.md` fixes the sample, what the sheet shows, and the scoring. It was committed before the sheet was built.

## How to fill in the audit sheet

Open `audit_sheet.xlsx`. It has three tabs: **How to** (these steps in short), **Audit** (the 60 rows), and **Rules** (the labeling rules).

1. **Go one report at a time.** The rows are grouped by report, so you open each PDF once (48 PDFs). Click **Open PDF** in a row to open that report.
2. **Read the question and answer it from the report alone.** Don't use news coverage, other reports, or anything you remember from this project. The Rules tab explains every answer choice. For example, the wind question only counts wind that met a shutoff threshold in an area that actually lost power.
3. **Where to look.** "Pages to start from" are the report's summary pages, the same for every row of that report. They are only a starting point. Use your PDF viewer's search with the listed search words, and read the whole relevant section. Page numbers mean the page number your PDF viewer shows (page 1 is the first page of the file), not the number printed at the bottom of the page.
4. **My answer.**
   - For a yes/no-style question, pick from the dropdown.
   - For a number, type it without commas.
   - For a time, type it as `2024-10-17 14:05`: 24-hour clock, local time, as the report gives it. The cell is set to text, so Excel will not change what you type.
   - If the report does not give the value, pick `not_stated`.
   - If the report gives two different values and the Rules tab doesn't say which one wins, you may type both as `a|b` (for example `36307|36037`). Start the note with `UNSURE:`.
5. **My page:** the page or pages that support your answer, separated by semicolons (`9;41`). Write `none` if the report is silent.
6. **Note:** a short quote or reason, especially for anything that took judgment. Start the note with:
   - `UNSURE:` if you are not sure;
   - `CONTRADICTION:` if the report disagrees with itself (give both values and pages);
   - `SEEN:` if you remember this value from `FINDINGS.md`, the PR, or an earlier review.
7. **Workbook:** one row (A45) is a time for an event whose utility posted an Excel data workbook. Click **Open workbook**, and use its circuit table if it lists every circuit.
8. **Don't open `sample.csv` or `audit_key.csv`** until you have finished. They show which group each row is in and the values being checked.
9. **Save the sheet** under any name and send it back. Blank rows are fine. They are left out and counted.

Expect about 3 to 5 minutes a row, so 3 to 5 hours in all. You can stop and come back; nothing is timed.

## After the sheet comes back

```
python research/psps_reports/round5/audit.py score --sheet <path to the filled sheet>
```

This checks `audit_key.csv` against `key_sha256.txt`, compares each group with your answers, and writes `audit_results.md` (Wilson 95% intervals, with and without `UNSURE:` and `SEEN:` rows) and `audit_scored.csv`. `FINDINGS.md` is then updated to describe the old test labels as model-written and this audit as the human check.

## Files

| File | What it is |
|---|---|
| `AUDIT_PLAN.md` | The plan: why, blindness, sample, scoring. Committed before the sheet. |
| `audit.py` | `sample` draws `sample.csv`; `build` writes the sheet and the key; `score` scores the returned sheet. |
| `sample.csv` | The 60 sampled rows with their group. Committed before the sheet. The auditor does not open it. |
| `audit_sheet.xlsx` | The blank sheet for the auditor. It shows no model answer, dataset value, gold label, flag, or group; `build` checks that no row's number or time value appears in it. |
| `audit_key.csv` | The answer key: group, dataset value, round 4 answers, and old gold label per row. Gitignored, local only. `audit.py build` rewrites it identically from the committed data. |
| `key_sha256.txt` | SHA-256 of `audit_key.csv`, committed with the sheet. |
