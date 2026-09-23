# Accuracy sample (10 never-read reports, seed 20260924)

| Field | Right | Right (certain gold) | Flagged | Errors not flagged |
|---|---|---|---|---|
| mbl_advance_notice | 8/10 | 7/8 | 4 | 0 |
| wind_threshold_cited | 9/10 | 7/8 | 6 | 0 |
| complaints_reported | 10/10 | 8/8 | 0 | 0 |
| claims_reported | 7/10 | 7/8 | 3 | 0 |
| canceled_after_notice | 6/10 | 6/9 | 3 | 1 |
| customers_deenergized | 10/10 | 9/9 | 1 | 0 |
| first_deenergization | 10/10 | 10/10 | 1 | 0 |
| last_restoration | 10/10 | 9/9 | 1 | 0 |
| counties_deenergized | 10/10 | 9/9 | 1 | 0 |
| event_duration_hours | 10/10 | 9/9 | 1 | 0 |

Field values checked (excluding derived duration): 90. Errors: 10. Flagged by the review rule: 9. Errors the rule missed: 1.
Unflagged values: 70, of which 69 right (98.6%).

Jev calibration on the sample:

| Confidence | Answers | Right | Mean confidence |
|---|---|---|---|
| 0.9 to 1.0 | 34 | 33/34 | 0.98 |
| 0.7 to 0.9 | 8 | 5/8 | 0.80 |
| below 0.7 | 5 | 2/5 | 0.54 |

Errors:

- sce__sce_psps_postevent_reportjuly_29aug_2_2019_8162019 claims_reported: extracted zero, gold not_stated (certain no; flagged: low_confidence; confidence 0.78)
- sdge__sdge_oct_26_27_2020_psps_post_event_report wind_threshold_cited: extracted met, gold not_stated (certain yes; flagged: low_confidence; confidence 0.68)
- sdge__sdge_oct_26_27_2020_psps_post_event_report canceled_after_notice: extracted not_stated, gold yes (certain yes; flagged: low_confidence; confidence 0.69)
- sce__sce_oct_16_2020_psps_post_event_report mbl_advance_notice: extracted not_stated, gold some_not_notified (certain yes; flagged: low_confidence; confidence 0.22)
- sce__sce_oct_16_2020_psps_post_event_report canceled_after_notice: extracted NO_MATCHING_PAGE, gold no (certain no; flagged: no_matching_page; confidence )
- sdge__sdge_de_energization_report_nov_89_2018 canceled_after_notice: extracted NO_MATCHING_PAGE, gold yes (certain yes; flagged: no_matching_page; confidence )
- sce__sce_jun_25_28_2020_psps_post_event_report claims_reported: extracted zero, gold not_stated (certain no; flagged: low_confidence; confidence 0.78)
- sce__sce_post_event_reporting_september_4_through_september_8_2019 mbl_advance_notice: extracted NO_MATCHING_PAGE, gold all_notified (certain no; flagged: no_matching_page; confidence )
- sce__sce_post_event_reporting_september_4_through_september_8_2019 canceled_after_notice: extracted not_stated, gold yes (certain yes; flagged: no; confidence 1.0)
- pge__psps_event_12_07_20_de_energization_report claims_reported: extracted zero, gold not_stated (certain yes; flagged: low_confidence; confidence 0.85)
