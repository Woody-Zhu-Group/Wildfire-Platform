# Round 5: model-assisted, human-verified audit results

Method: for every row, Claude Opus 5.5 proposed an answer and page from the rules and the report PDF only, and Michael verified it against the PDF (`AUDIT_PLAN.md`, "Amendment"). This is not a blind or independent human audit.

Sheet: `audit_sheet_rechecked.xlsx`. Plan: `AUDIT_PLAN.md`. Scored by `audit.py score`; row-level results in `audit_scored_rechecked.csv`.

Version: after rechecking only the five rows flagged in the first scoring (A05, A08, A10, A22, A31), in a second model-assisted, human-verified pass. The other 55 rows are as submitted. The first scoring had already shown the verifier the compared value for A05, A08, A10, and A31, and rechecking only rows that disagreed can only raise agreement.

- Rows answered: 60 of 60; blank: none; unreadable answers (left out): none.
- Marked UNSURE: 3; marked SEEN: 0.

| Compared with the auditor | All answered rows | Without UNSURE | Without SEEN |
|---|---|---|---|
| Queue: values the two round 4 models agreed on | 30/30 (100.0%, 95% CI 88.6 to 100.0) | 30/30 (100.0%, 95% CI 88.6 to 100.0) | 30/30 (100.0%, 95% CI 88.6 to 100.0) |
| Unflagged: the pipeline's values | 14/15 (93.3%, 95% CI 70.2 to 98.8) | 13/13 (100.0%, 95% CI 77.2 to 100.0) | 14/15 (93.3%, 95% CI 70.2 to 98.8) |
| Old test labels (model-written gold) | 15/15 (100.0%, 95% CI 79.6 to 100.0) | 14/14 (100.0%, 95% CI 78.5 to 100.0) | 15/15 (100.0%, 95% CI 79.6 to 100.0) |
| Old test label rows: round 4 agreed answers, where both reviewers agreed | 12/12 (100.0%, 95% CI 75.8 to 100.0) | 12/12 (100.0%, 95% CI 75.8 to 100.0) | 12/12 (100.0%, 95% CI 75.8 to 100.0) |

## Disagreements

| ID | Group | Report | Field | Auditor | Compared value | Note |
|---|---|---|---|---|---|---|
| A54 | unflagged | sdge__sdge_de_energization_report_oct_19_20_2018 | mbl_advance_notice | all_notified | not_stated | UNSURE: NOT REALLY SPECIFIC |
