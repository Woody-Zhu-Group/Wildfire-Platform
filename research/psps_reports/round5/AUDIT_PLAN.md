# Round 5: human audit of the PSPS dataset (plan)

Status: plan. It is committed together with the sample list (`sample.csv`) before the audit sheet is built. Nothing below changes after the auditor starts labeling.

## Why

- **No person has checked any value in this dataset.**
  - The gold labels used in rounds 1 to 4 (`gold_labels.csv`, `round2/gold_new15.csv`, `round3/gold_sample10.csv`) were written by a Claude Opus 5.5 Claude Code session on 2026-09-23, not by a person. That session's transcript shows it creating each file, with a Write call or shell heredocs, after reading the report pages. No labels came from the user, and each committed file matches what the session wrote. The earlier docs called them "hand-checked" because the round prompts asked the session to hand-check the values itself.
  - Round 4's Reviewer 1 was the same model, so the round 4 test measured two models against labels written by one of them.
- **This audit is the first human check.** It measures three things: the values the two round 4 models agreed on, the values the pipeline never flagged, and the old model-written test labels.

## Who, when, and what rules

- **Labeler:** one person, Michael, working from the report PDFs.
- **Timing:** after round 4. The dataset, code, and old labels are not changed while the audit runs.
- **Rules:** the "The fields" section of `round3/REVIEW_GUIDE.md`, the same rules the models and the old labels followed. The sheet includes them on its Rules tab.

## Blindness

The auditor sees, for each row:

- the report name and PDF link;
- a workbook link, for the time fields of the events with a utility workbook;
- the question and the allowed answers;
- pages to start from, and search words;
- empty columns for the answer, page, and note.

The sheet does not show:

- any model answer (Jev, Luna, Opus, or Sol);
- any dataset value, old gold label, confidence, review flag, or queue reason;
- which sample group a row belongs to.

Two details keep the page hints from leaking answers:

- **Pages to start from** are the report's summary and event-table pages, as the round 3 page selector found them. Every row of a report gets the same pages, so they say nothing about any one field. When a report has none, the row says to search the PDF.
- **Search words** are fixed per field and come from the review guide.

The answer key and the sample list stay out of view:

- **Answer key:** `audit_key.csv` holds each row's group, source ids, dataset value, round 4 answers, and old gold label. It is written next to the sheet, is gitignored, and is not read until scoring. Its SHA-256 is committed with the sheet (`key_sha256.txt`).
- **Sample list:** `sample.csv` names each row's group. The auditor should not open it or `audit_key.csv`.
- **Prior exposure:** the auditor has read `FINDINGS.md` and the PR description, which quote some report values. If you remember a row's value from there, start the note with `SEEN:`.

## Sample

- **Seed and draw:** seed 20260929, using Python's `random.Random`. `python research/psps_reports/round5/audit.py sample` draws with one generator, in this order.
- **Order and sorting:** each population is sorted by (report_id, field) before drawing, and a row drawn for one group is not drawn again.

| Group | Size | Population |
|---|---|---|
| `queue` | 30 | Fields whose `review_method` in `round4/dataset_reviewed.csv` is `model_review_agreed` (145). Each is backed by exactly one agreed queue item. `model_review_no_change` fields are excluded. |
| `unflagged` | 15 | Fields whose `review_method` is `unflagged` (761). |
| `old_test_label` | 15 | Kept rows of `round4/test_gold.csv` (211), the model-written labels the round 4 test used. |

The drawn rows are sorted by report and field and numbered A01 to A60. They cover 48 reports, 60 rows in all:

- **By utility:** 30 SCE, 17 SDG&E, and 13 PG&E.
- **By field:** 11 wind, 10 MBL, 9 claims, 8 complaints, 7 first de-energization, 5 customers, 5 cancellation, and 5 last restoration.

## Scoring (fixed before labeling)

`audit.py score` reads the returned sheet and the answer key, and writes `audit_results.md`.

**Comparison rules**, the same as rounds 2 to 4:

- Categories must match exactly.
- Numbers must match exactly, after commas are dropped.
- Times must match to the minute.
- `not_stated` matches an empty value, `null`, or `not_stated`.
- The auditor may write two values as `a|b` when the report supports both. A value that matches either counts as right.
- Rows left blank are left out, and their number is reported.

**What is compared, per group** (each with a Wilson 95% interval):

| Group | Compared with the auditor |
|---|---|
| `queue` | The dataset value, which is the value the two round 4 models agreed on. |
| `unflagged` | The dataset value, which is the pipeline's value (Jev or Luna). |
| `old_test_label` | The old model-written gold label: how often the old labels agree with a person. Secondary: for rows where both round 4 reviewers agreed in the test, their agreed answer. |

**Sensitivity:** each result is also reported without rows whose note starts with `UNSURE:`, and without rows whose note starts with `SEEN:`.

**How much the sample can tell us:** there are no pass bars. With 30 rows the interval is wide. For example, 27 of 30 right gives 90.0% (74.4 to 96.5), and 14 of 15 gives 70.2 to 98.8.

**After scoring:** the results go in `audit_results.md`. `FINDINGS.md` is then updated to describe the old test labels as model-written and this audit as the human check.

## Effort

48 PDFs and 60 rows. The rows are sorted by report, so each PDF is opened once. At 3 to 5 minutes a row, this takes about 3 to 5 hours.

## Files

| File | When |
|---|---|
| `AUDIT_PLAN.md`, `audit.py` (sample), `sample.csv` | Committed and pushed before the sheet is built. |
| `audit_sheet.xlsx`, `key_sha256.txt`, `README.md`, `audit.py` (build and score) | Committed before labeling. `audit_key.csv` stays local and gitignored. |
| The filled sheet and `audit_results.md` | After labeling. |
