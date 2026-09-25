# PG&E wind "not stated" spot-check

Decision rule (from the project lead): check 10 randomly chosen PG&E wind items that Jev answered not_stated. If all 10 hold, auto-accept every PG&E wind not_stated item and remove them from the review queue. If any fail, keep them all in the queue.

- Population: 15 queue items (PG&E, wind_threshold_cited, answer not_stated; all flagged for low confidence). The other 8 PG&E wind items in the queue are 6 "met" answers and 2 with no matching page, and were not eligible.
- Sample: 10 items drawn with `random.Random(20260925).sample(items_sorted_by_report_id, 10)`.
- Method: I read each report's body (first 60 percent of pages) for any statement that observed or forecast winds met, exceeded, or reached a de-energization threshold, criterion, or PG&E guidance in an area that was de-energized. No API calls.

Result: **9 hold, 1 fails.** The failure is PG&E November 20 to 21, 2019 (queue item 147). Page 9 says "breezy to gusty offshore winds at or above outage producing levels would combine with dry and receptive fuels" in the areas that were then scoped and de-energized. PG&E's 2019 protocol used outage-producing wind as its wind criterion, so the answer should be met (forecast), not not_stated.

**Outcome: the rule was not applied. All 15 PG&E wind not_stated items stay in the queue.** The pattern the spot-check exposes is that 2019 PG&E reports use "outage producing wind" language that does meet the question, while 2020 and later reports mostly describe composite guidance. A narrower rule (auto-accept PG&E wind not_stated for events from 2020 on) would have held on all 7 sampled reports from 2020 on, but that is a different rule from the one approved and was not applied.

Details per item: `pge_wind_spotcheck.csv`.
