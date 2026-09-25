# PSPS post-event reports: round 2 (clean test)

Round 2 changes the page selection and the Jev questions, then tests the pipeline once on 15 reports I had never read. The round 1 files one level up are unchanged.

## What changed from round 1

1. **Page selection** (`pipeline.py`, `select_pages`):
   - Every question gets the summary and event-table pages (Executive Summary, Table 1 "PSPS Event Summary", "Customers Notified and De-energized", "At A Glance").
   - Each question also gets the template section for its topic (for example "positive or affirmative notification" for MBL, "Complaints and Claims", "Decision criteria and detailed thresholds"), plus the page after it, since tables spill over. Table-of-contents pages are skipped.
   - Remaining slots, up to 8 pages, are filled by keyword score.
   - A question with no page matching its topic is flagged `no_matching_page`, and Jev is not asked.
2. **A not_stated option on every Jev question.** Wind was the only question without one. It is now a Choice (met, not_met, not_stated) instead of a Noul. The instructions tell Jev the pages are excerpts and to choose not_stated when they don't say. The MBL question now spells out the label rule: a delivered notification or a door hanger counts, and MBL customers who were in scope but never lost power do not count.
3. **Luna** (numbers) has the same prompt and model as round 1. The quote check is looser: it looks for the value's digits on the cited page.

On the original 10, the new selection shows the gold page for 45 of 50 questions. That was the only tuning check, run before any round 2 API call.

## Clean test sample

`sample.py` builds a pool of 142 original PG&E, SCE, and SDG&E post-event reports from saved snapshots of the two CPUC pages (`cpuc_pages/`). It drops amendments, corrections, SED reviews, other utilities, the 10 pilot reports, and the three other PG&E 2023 files I opened in round 1. It then draws one report per utility and era (2017 to 2019, 2020, 2021 to 2022, 2023 to 2024, 2025 to 2026) with **seed 20260923**. `sample.csv` lists the 15 picks and the size of each cell.

- The sample was committed and pushed (commit 93061c9) before any extraction ran.
- One bug was fixed before freezing: my first draft of the pool tagged some Liberty and PacifiCorp reports as SDG&E. Nothing had been run on that draft.
- The pipeline ran once. I read the reports only afterward, to hand-check them.

The 15 include two events where no customer was de-energized (SCE August 2019, SDG&E January 2021), two pre-template letters from 2018 and 2019, and several recent reports whose circuit tables are only partly in the PDF.

## Results

The full tables are in `summary.md`, and the row-level results are in `validation_clean15.csv` and `validation_orig10_v2.csv`. A gold value may list alternatives ("a|b") when the report contradicts itself. "null" means the report does not state the value. `certain = no` marks a contradiction or a judgment call.

### Clean 15 (never read before the run)

| Field | Extractor | Right | Right, certain gold only |
|---|---|---|---|
| MBL notified in advance | Jev | 12/15 | 9/10 |
| Wind met threshold | Jev | 14/15 | 7/8 |
| Complaints | Jev | 15/15 | 13/13 |
| Claims | Jev | 15/15 | 15/15 |
| Canceled after notice | Jev | 11/15 (4 flagged, no matching page) | 11/15 |
| Customers de-energized | Luna | 15/15 | 11/11 |
| First de-energization | Luna | 11/15 | 10/10 |
| Last restoration | Luna | 14/15 | 9/9 |
| Counties | Luna | 15/15 | 10/10 |
| Duration | code | 11/15 | 6/6 |

**Jev calibration (clean 15, 71 answered questions):**

| Confidence | Answers | Right |
|---|---|---|
| 0.9 to 1.0 | 51 | 50/51 |
| 0.7 to 0.9 | 9 | 8/9 |
| 0.5 to 0.7 | 5 | 5/5 |
| below 0.5 | 6 | 4/6 |

**Review rule, "Jev below 0.9, or no matching page":** it flags 24 of 75 questions and catches 7 of 8 misses.

The miss it lets through is SDG&E January 2021 MBL: Jev answered not_stated at 0.97, and the gold is all_notified. That event de-energized no one, and the question has no "no one lost power" option, so the gold label there is itself a judgment call. It is a question-design gap, not a reading error.

**Luna:** all 5 misses are on timestamps whose gold is uncertain. In each case the report never states the true first or last time:
- In 2 cases (SCE 2023, SCE 2025) the in-PDF circuit table covers only 5 of 41 or 5 of 225 circuits.
- In 2 cases (SDG&E 2024, SDG&E 2025) Luna used a status-update time ("As of 3:02 pm, SDG&E has implemented PSPS for 4 circuits").
- In 1 case Luna gave SCE's event close ("ended at 4:00 pm by which time service was restored").

On every certain label, Luna was right. The automatic check found the value on the cited page for 52 of 55 non-null values. The 3 failures were two correct zeros (the cited page says "no circuits were de-energized") and one +1 from adding an SDG&E customer to SCE's count.

### Original 10, round 2 Jev (used for design, not clean)

- Jev: 47/50, up from 42/50 in round 1.
- Wind: 10/10, up from 7/10, now that "the report never says wind met a threshold" is an option instead of a forced yes or no.
- MBL: 8/10.
- The review rule catches 3 of 3 misses, compared with 6 of 8 for round 1.
- Luna was not rerun: 40/40.

Because these reports shaped both rounds, the clean 15 are the numbers to quote.

### What the clean test says

- **Page selection mostly fixed the confident retrieval misses.** In round 1, two confident misses (0.96 and 1.00) came from never seeing the deciding page. In the clean 15, 50 of 51 answers at 0.9 or above were right.
- **Flagging works as intended.** All 4 no-match flags were cancellation questions where the report uses other words: "weren't turned off", "no longer at risk", "did not de-energize". Each would have been a guess in round 1; now each goes to review. Adding those phrases to the topic pattern is the obvious next fix. I did not make it, so the clean result stands as run.
- **MBL is still the weakest Jev field.** Two of its three misses were at low confidence (0.29 and 0.76), so review catches them.
- **Two scaling problems are new in round 2.** Some SCE tables exist only as images, with no text layer: in SCE October 2021, Table 1 and Table 9 had to be read by rendering the page. And recent SCE and SDG&E reports put the full circuit table only in an Excel workbook filed alongside the PDF. First and last times therefore need the workbooks, not the PDF.

## Internal contradictions (`contradictions.csv`)

I found 21 contradictions: 5 in the 10 pilot reports and 16 in the clean 15. At least one appeared in 16 of the 25 reports.

| Report | Field | Conflicting values |
|---|---|---|
| PG&E Oct 2021 | first de-energization | 06:00 (narrative p4) vs 01:34 (circuit table p102) |
| PG&E Jan 2025 (Jan 20 event) | last restoration | 1/21 14:17, "100% within 24 h" (p8, p75) vs shared SCE customers 1/24 (p37) |
| SCE Nov 2021 | counties | 6 incl. Kern (p8) vs 5, Kern in scope only (p22) |
| SCE Sep 2025 | last restoration | 9/10 16:00 "all restored" (p19) vs 12:49 last circuit (p62) |
| SDG&E Nov 2021 | first de-energization | 11:47 (p20) vs 18:00 (p6) vs 21:53 first circuit (p124) |
| PG&E Oct 2019 | customers | ~177,000 (text) vs 176,620 (Table 2) |
| PG&E Oct 2019 | last restoration | 18:20 (p8, Table 2) vs ~18:01 (p21) |
| PG&E Oct 2020 | MBL without notification | 30 (p35) vs 51 not attempted (p36) |
| PG&E Jul 2024 | customer breakdown | 112/92/18/24 (p23) vs 110/82/17/19 (Appendix B) |
| PG&E Jan 2025 (Jan 13 event) | first de-energization | 22:45 (p7) vs 23:13 (Appendix B) |
| PG&E Jan 2025 (Jan 13 event) | customers not notified | 1 (p9) vs 3 (p41) |
| SCE Oct 2020 | customers | 36,307 (p3) vs 36,037 (p22) |
| SCE Oct 2020 | last restoration | Atento 10/28 07:30 (table) vs 10/29 11:53 (p12) |
| SCE Oct 2021 | last restoration | event ended 3 pm (p8) vs 4:29 pm (p21); p41 says one customer not restored |
| SCE Oct 2023 | counties | 6 incl. Kern (p4, Table 1) vs 5 (p5, p18) |
| SCE Oct 2023 | first de-energization | 06:31 (p4) vs 07:42 in a table listing 5 of 41 circuits |
| SCE Jan 2025 | circuit times | Table 5 lists 5 of 225 de-energized circuits |
| SDG&E Oct 2018 | last restoration | 10/20 09:38 (table) vs "PSPS Restore" 10/19 14:52 (attachment) |
| SDG&E Dec 2020 | customers | 6,797 impacted (p11, p128) vs "averted impacts for ~6,797" (p10) |
| SDG&E Jan 2025 | counties | 3 (Table 1) vs 4 named (p6) |
| SDG&E Jan 2025 | customers | 29,980 total vs 27,015 unique (both stated) |

The patterns that recur:
- Narrative start times disagree with circuit tables.
- "Event ended" is used as if it were the restoration time.
- Counties that were in scope get counted as de-energized.
- Customer-type breakdowns don't add up to the table totals.

A dataset built from these reports needs a source-precedence rule. Mine was: the circuit table when it is complete, then the section that answers the template question. It also needs a per-field conflict flag instead of one silent value.

## Cost

Round 2 spent $0.21: $0.023 on Jev (120 calls across both sets) and $0.185 on Luna (15 full reports). Rounds 1 and 2 together cost $0.38. The pipeline tracks spend in `runs/spend.json` and stops at $1.

## Reproduce

```
python research/psps_reports/round2/sample.py        # rebuilds pool.csv and sample.csv (same seed)
python research/psps_reports/round2/pipeline.py extract --sources research/psps_reports/round2/sample.csv
python research/psps_reports/round2/pipeline.py select  --sources research/psps_reports/round2/sample.csv   # no API
python research/psps_reports/round2/pipeline.py jev  --sources research/psps_reports/round2/sample.csv --out jev_v2_new15.jsonl
python research/psps_reports/round2/pipeline.py luna --sources research/psps_reports/round2/sample.csv --out luna_new15.jsonl
python research/psps_reports/round2/score.py
```
