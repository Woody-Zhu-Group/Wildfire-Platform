# Round 4 calibration: test results

One test run on the 24 clean test reports after the freeze (`freeze.json`). Gold: `test_gold.csv`.
Accuracy is on agreed answers: both reviewers valid and identical.

## Pass bars

| Bar | Needed | Result | Pass |
|---|---|---|---|
| Queue-matched items | at least 90% | 27/29 (93.1%, 95% CI 78.0 to 98.1) | yes |
| All test values | at least 95% | 177/179 (98.9%, 95% CI 96.0 to 99.7) | yes |

**Overall: PASS.**

## Also reported (no bar)

- Agreement rate: 179/211 (84.8%, 95% CI 79.4 to 89.0) of test values agreed.
- Agreement rate, queue-matched: 29/32 (90.6%, 95% CI 75.8 to 96.8).
- Valid answers, Reviewer 1 (Opus): 185/211 (87.7%, 95% CI 82.6 to 91.5).
- Valid answers, Reviewer 2 (Sol): 208/211 (98.6%, 95% CI 95.9 to 99.5).
- Accuracy of agreed answers, certain gold only: 152/154 (98.7%, 95% CI 95.4 to 99.6).

### By field

| Field | Agreed | Agreed correct |
|---|---|---|
| mbl_advance_notice | 20/22 | 18/20 (90.0%, 95% CI 69.9 to 97.2) |
| wind_threshold_cited | 23/24 | 23/23 (100.0%, 95% CI 85.7 to 100.0) |
| complaints_reported | 24/24 | 24/24 (100.0%, 95% CI 86.2 to 100.0) |
| claims_reported | 24/24 | 24/24 (100.0%, 95% CI 86.2 to 100.0) |
| canceled_after_notice | 21/23 | 21/21 (100.0%, 95% CI 84.5 to 100.0) |
| customers_deenergized | 22/24 | 22/22 (100.0%, 95% CI 85.1 to 100.0) |
| first_deenergization | 11/23 | 11/11 (100.0%, 95% CI 74.1 to 100.0) |
| last_restoration | 14/23 | 14/14 (100.0%, 95% CI 78.5 to 100.0) |
| counties_deenergized | 20/24 | 20/20 (100.0%, 95% CI 83.9 to 100.0) |

### By utility

| Utility | Agreed | Agreed correct |
|---|---|---|
| PG&E | 48/53 | 48/48 (100.0%, 95% CI 92.6 to 100.0) |
| SCE | 79/97 | 79/79 (100.0%, 95% CI 95.4 to 100.0) |
| SDG&E | 52/61 | 50/52 (96.2%, 95% CI 87.0 to 98.9) |

### Why answers were invalid

| Check | Reviewer 1 | Reviewer 2 |
|---|---|---|
| not_stated_with_search | 9 | 30 |
| quote_found | 176 | 178 |
| quote_not_found | 2 | 2 |
| unsure | 6 | 1 |
| value_not_allowed | 18 | 0 |

### Agreed but wrong

- sdge__r1812005_sdge_psps_postevent_report_dec_911_2024_1_10_2025 `mbl_advance_notice`: agreed some_not_notified, gold all_notified (certain yes)
- sdge__sdge_psps_post_event_report_december_23_24_final `mbl_advance_notice`: agreed some_not_notified, gold all_notified (certain yes)

## Notes written after scoring (the numbers above are unchanged)

- **Opus's 18 `value_not_allowed` answers** are all first or last times the report does not state. Opus wrote a JSON `null` instead of the string `"null"` the instructions ask for, and the frozen check treats a JSON null as a missing value. Sol wrote `"null"` for all 18, and all 18 are kept gold rows whose gold is `null` (corrected on 2026-09-29; this note said 17). If a JSON null had counted as `null`, all 18 would have agreed and been right (checked on 2026-09-29 by rerunning the frozen check with the value read as `null`; this note said "most"). As scored, they are disagreements. This lowers the agreement rate, not the accuracy of agreed answers. The check is frozen and was not changed; the same rule applies to the queue run.
- **Both agreed-but-wrong answers are SDG&E MBL items on round 2 reports, and both are queue-matched.** They are the 2 misses in the queue-matched bar (27 of 29). In each, both reviewers read the report as saying some de-energized MBL customers were not notified, and the gold (certain) says all were. MBL is the only field with an agreed error.
