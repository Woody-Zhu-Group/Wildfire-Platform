# Round 5: model-assisted, human-verified audit

A person verified 60 values in the PSPS dataset against the report PDFs. **Method, for all 60 rows: model-assisted, human-verified.** Claude Opus 5.5 proposed an answer and a page for each row, and Michael verified each proposal against the report PDF. The proposing model saw only the labeling rules and the report PDFs, not `audit_key.csv`, `sample.csv`, `round4/dataset_reviewed.csv`, `FINDINGS.md`, or the PR text. This is not a blind or independent human audit (`AUDIT_PLAN.md`, "Amendment"). The old test labels were written by a model, not a person (`AUDIT_PLAN.md`, "Why").

## Results

**Headline, as submitted:** scored by `audit.py score` on `audit_sheet_filled.xlsx`. The full output is `audit_results.md`, and row-level detail is `audit_scored.csv`. The rates are agreement with model-assisted, human-verified labels, not independent accuracy: the proposing model also wrote the old labels and was round 4's Reviewer 1.

| Group (rows) | Compared with the verified label | Agreement (Wilson 95% interval) | Without `UNSURE:` rows |
|---|---|---|---|
| Queue values the round 4 models agreed on (30) | dataset value | 29/30 (96.7%, 83.3 to 99.4) | 29/30 |
| Unflagged pipeline values (15) | dataset value | 12/14 (85.7%, 60.1 to 96.0) | 11/12 |
| Old model-written test labels (15) | old gold label | 13/14 (92.9%, 68.5 to 98.7) | 13/13 |
| Same rows, round 4 agreed answers | round 4 answer | 12/12 (100%, 75.8 to 100) | 12/12 |

- **Rows left out (as submitted):** A22 (blank) and A10 (unreadable: `2020/09/25 02:46` for a 2019 event; it differs from the dataset's 2019-09-23 17:06 either way). None was marked `SEEN:`, and 4 were marked `UNSURE:`.
- **Differences (as submitted):**
  - **A05** (queue, PG&E Sept 30 2023 complaints): `zero` against `not_stated`. The verifier's note says "not applicable", which the rules count as `not_stated`.
  - **A08** (unflagged, PG&E Sept 7-10 2020 first de-energization): 14:31 against 04:25. The table has PUEBLO 2103 at 9/7 4:25 (p56). The verifier treats it as an error: it is about 10 hours before every other 9/7 circuit, and its Napa neighbor Pueblo 2102 is 9/8 4:07, so the date is likely 9/8. This is in `round3/contradictions.csv` as human-found. The note in `audit_sheet_rechecked.xlsx` was rewritten after the rescore. The scores did not change, because A08's `UNSURE:` status stayed the same, so `audit_results_rechecked.md` still shows the earlier note text.
  - **A54** (unflagged, SDG&E Oct 19-20 2018 MBL): `all_notified` (UNSURE) against `not_stated`.
  - **A31** (old label, SCE Oct 16 2020 cancellation): `not_stated` (UNSURE) against the old label `no`. The verifier's note says nobody was de-energized, but p5 lists 37 and 49 customers.
- **After rechecking the rows flagged in the first scoring:** only A05, A08, A10, A22, and A31 were rechecked, and the other 55 rows are as submitted.
  - Files: `audit_sheet_rechecked.xlsx`, scored into `audit_results_rechecked.md` and `audit_scored_rechecked.csv` with `audit.py score --sheet audit_sheet_rechecked.xlsx --tag _rechecked`.
  - A22 is now `not_stated` and matches the old label, so the old model-written labels agree with 14/15 (93.3%, 70.2 to 98.8).
  - The queue (29/30), unflagged (12/14), and round 4 (12/12) results are unchanged.
  - A05, A08, and A31 keep their answers.
  - A10 (`2020/09-25 02:46`) still cannot be read and is left out.

The steps below are the instructions the verifier followed.

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
| `AUDIT_PLAN.md` | The plan: why, what the sheet shows, sample, scoring, and the method amendment (committed after labeling, before scoring). |
| `audit.py` | `sample` draws `sample.csv`; `build` writes the sheet and the key; `score` scores the returned sheet. |
| `sample.csv` | The 60 sampled rows with their group. Committed before the sheet. The auditor does not open it. |
| `audit_sheet.xlsx` | The blank sheet for the auditor. It shows no model answer, dataset value, gold label, flag, or group; `build` checks that no row's number or time value appears in it. |
| `audit_key.csv` | The answer key: group, dataset value, round 4 answers, and old gold label per row. Gitignored, local only. `audit.py build` rewrites it identically from the committed data. |
| `key_sha256.txt` | SHA-256 of `audit_key.csv`, committed with the sheet. |
| `audit_sheet_filled.xlsx` | The verified labels, committed before scoring. |
| `audit_results.md`, `audit_scored.csv` | The scored results and row-level detail, as submitted (the headline). |
| `audit_sheet_rechecked.xlsx`, `audit_results_rechecked.md`, `audit_scored_rechecked.csv` | The sheet after rechecking only the rows flagged in the first scoring, and its scores. |
