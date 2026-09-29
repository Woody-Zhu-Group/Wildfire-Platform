# Round 5: model-assisted, human-verified audit results

Method: for every row, Claude Opus 5.5 proposed an answer and page from the rules and the report PDF only, and Michael verified it against the PDF (`AUDIT_PLAN.md`, "Amendment"). This is not a blind or independent human audit.

Sheet: `audit_sheet_rechecked.xlsx`. Plan: `AUDIT_PLAN.md`. Scored by `audit.py score`; row-level results in `audit_scored_rechecked.csv`.

Version: after rechecking the rows flagged in the first scoring (A05, A08, A10, A22, A31). Only those rows were rechecked; the other 55 rows are as submitted.

- Rows answered: 59 of 60; blank: none; unreadable answers (left out): A10.
- Marked UNSURE: 4; marked SEEN: 0.

| Compared with the auditor | All answered rows | Without UNSURE | Without SEEN |
|---|---|---|---|
| Queue: values the two round 4 models agreed on | 29/30 (96.7%, 95% CI 83.3 to 99.4) | 29/30 (96.7%, 95% CI 83.3 to 99.4) | 29/30 (96.7%, 95% CI 83.3 to 99.4) |
| Unflagged: the pipeline's values | 12/14 (85.7%, 95% CI 60.1 to 96.0) | 11/12 (91.7%, 95% CI 64.6 to 98.5) | 12/14 (85.7%, 95% CI 60.1 to 96.0) |
| Old test labels (model-written gold) | 14/15 (93.3%, 95% CI 70.2 to 98.8) | 13/13 (100.0%, 95% CI 77.2 to 100.0) | 14/15 (93.3%, 95% CI 70.2 to 98.8) |
| Old test label rows: round 4 agreed answers, where both reviewers agreed | 12/12 (100.0%, 95% CI 75.8 to 100.0) | 12/12 (100.0%, 95% CI 75.8 to 100.0) | 12/12 (100.0%, 95% CI 75.8 to 100.0) |

## Disagreements

| ID | Group | Report | Field | Auditor | Compared value | Note |
|---|---|---|---|---|---|---|
| A05 | queue | pge__pge_post_event_report_9302023_event | complaints_reported | zero | not_stated | PSPS protacol not initiated so not applicable |
| A08 | unflagged | pge__pge_sept_7_10_2020_psps_post_event_report | first_deenergization | 2020-09-07 14:31:00 | 2020-09-07 04:25 | There is one earlier Pueblo 2103 but that is too early it is a mistake |
| A31 | old_test_label | sce__sce_oct_16_2020_psps_post_event_report | canceled_after_notice | not_stated | no | UNSURE: No customers de energized |
| A54 | unflagged | sdge__sdge_de_energization_report_oct_19_20_2018 | mbl_advance_notice | all_notified | not_stated | UNSURE: NOT REALLY SPECIFIC |
