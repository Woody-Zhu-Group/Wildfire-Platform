# Round 5: model-assisted, human-verified audit results

Method: in the first pass, for every row, Claude Opus 5.5 proposed an answer and page from the rules and the report PDF only, and Michael verified it against the PDF. The five rechecked rows (A05, A08, A10, A22, A31) were not labeled that way; see the three steps below and `AUDIT_PLAN.md`, "Amendment". This is not a blind or independent human audit.

Sheet: `audit_sheet_rechecked.xlsx`. Plan: `AUDIT_PLAN.md`. Scored by `audit.py score`; row-level results in `audit_scored_rechecked.csv`.

Version: after three recheck steps, on the five rows that the Claude Code session flagged after scoring the first pass against the answer key; A54 also differed and was not rechecked, and the other 55 rows are as submitted. (1) 39505be: Michael's own recheck filled A22, fixed a typo in A08's note, and changed A10 to 2020/09-25 02:46, which is still unreadable (intermediate result: queue 29/30, unflagged 12/14, old labels 14/15). (2) 6ccc416: the key-aware session found the 10-hour gap and the neighbor-circuit evidence for A08 and wrote A08's note from it, in place of Michael's stated timeline reason, after he chose that wording. (3) d4aff3b: a separate Opus chat, given a prompt the key-aware session wrote, proposed answers and Michael verified them. The chat saw the PDF links and questions, his earlier answers and notes, rule excerpts, and row hints (A08: the 4:25 entry and neighbor reasoning, and a request for the time the rule gives; A31: the p5 customer counts; A10, A22, A05: what needed clearing up). The first scoring had shown him the compared values for A05, A08, A10, and A31, and rechecking only rows that disagreed can only raise agreement, so these figures are an upper bound.

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
