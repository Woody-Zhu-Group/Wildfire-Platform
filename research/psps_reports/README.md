# PSPS post-event reports pilot

A pilot that tests whether Jev plus a small LLM can turn the CPUC's unstructured utility PSPS post-event reports into a structured dataset. The pilot covers 10 reports: 4 PG&E, 3 SCE, and 3 SDG&E, with events from 2020 to 2025.

Outputs:

- `pilot.csv`: one row per event, with every extracted field, Jev confidence, the pages shown to Jev, and the page cited for each number.
- `validation.csv`: one row per report and field (100 rows), with the extracted value, the hand-checked gold value, the gold source pages, whether they match, and notes.
- `validation_summary.md`: per-field accuracy, Jev confidence on right and wrong answers, and cost.
- `gold_labels.csv`: my hand-checked labels, with source pages, a certainty flag, and notes.

## Sources

The CPUC publishes utility PSPS reports on two pages:

- Current (2024 to 2026): https://www.cpuc.ca.gov/consumer-support/psps/utility-company-psps-reports-post-event-and-post-season
- Archive (2017 to 2023): https://www.cpuc.ca.gov/consumer-support/psps/utility-company-psps-reports-post-event-and-post-season/archived-psps-post-event-reports-2017-2023

`sources.csv` lists the PDF URL for each of the 10 reports. All 10 downloaded with plain HTTP (no login, no blocking). `extract_pages.py` downloads them to `raw/` (gitignored, 81 MB) and writes per-page text to `pages/`.

I skipped one report I had first picked: PG&E's September 30, 2023 report (`pge-post-event-report-9302023-event.pdf`). Its text layer is unusable. The letters are shifted by 29 code points and every digit is missing, so no text extraction can recover the numbers without OCR. I replaced it with PG&E's September 20, 2023 report. That was 1 file out of the 11 I opened.

## Method

Each tool gets the kind of question it is good at.

**Jev (categorical facts).** There are five fields, and each is asked as one small question with mutually exclusive options:

| Field | Type | Options |
|---|---|---|
| `mbl_advance_notice` | Choice | all_notified, some_not_notified, not_stated |
| `wind_threshold_cited` | Noul | true or false (confidence = max(p, 1 - p)) |
| `complaints_reported` | Choice | one_or_more, zero, not_stated |
| `claims_reported` | Choice | one_or_more, zero, not_stated |
| `canceled_after_notice` | Choice | yes, no, not_stated |

Jev does not see the whole report. For each field, keyword scoring picks the top 5 pages. Pages in the back half of a report count half, because that part is mostly appendices. Jev gets those pages with `[Page N]` markers. If no page matches, the field is recorded as not_stated without a Jev call; this happened once. The exact wording is in `jev_extract.py`.

**GPT-6 Luna via OpenRouter (numbers).** There is one call per report. Luna reads the full report text with page markers and returns JSON with the following for each number: the value, the page it came from, and a verbatim quote. The numbers are customers de-energized, first de-energization time, last restoration time, and number of counties de-energized (plus the county names). Code then checks that the quote is on the cited page. Event duration is computed in code from the two timestamps and is never produced by the model.

**Hand check.** For every report I checked all 9 extracted fields against the source pages myself, plus the derived duration. I read the relevant sections and searched the full text for contradicting statements. For timestamps I scanned every date and time in the circuit tables. One sentence was split across a page break, so I read that page as a rendered image. `gold_labels.csv` records the gold value, its pages, and a note for each field.

Label rules I applied:

- MBL notified in advance: a notification was delivered, or a door-hanger visit was made, before de-energization. Unconfirmed receipt still counts as notified. Customers who were in scope but not de-energized do not count.
- Wind threshold cited: the report says wind speeds or gusts met or exceeded a de-energization threshold or a wind criterion. General descriptions of the protocol do not count, and neither do gust readings with no threshold.
- Canceled after notice: some notified customers were not de-energized. A summary-table "Cancelled" count above zero qualifies.
- First de-energization and last restoration: the earliest and latest circuit times in the report's circuit table. Where the narrative disagrees, I noted it and marked the label uncertain.

I marked a label uncertain (`certain = no`) when the report contradicts itself or the rule is a judgment call. That applies to 10 of the 90 hand labels, or 13 of the 100 validation rows once the derived durations are counted. The biggest group is the four PG&E wind rows. PG&E's threshold is a composite model score, and the reports never say directly that wind met it.

## Results

These accuracy numbers come from the 10 pilot reports. The set is not a clean holdout. I wrote the questions and retrieval patterns after reading parts of 3 of these reports, and the labels are mine alone with no second labeler. I did not change any question or retrieval setting after seeing Jev's answers.

| Field | Extractor | Right (all 10) | Right (certain gold only) | Mean Jev confidence when right | Mean Jev confidence when wrong |
|---|---|---|---|---|---|
| mbl_advance_notice | Jev | 7/10 | 7/9 | 0.80 | 0.78 (n=3) |
| wind_threshold_cited | Jev | 7/10 | 5/6 | 0.82 | 0.70 (n=3) |
| complaints_reported | Jev | 10/10 | 10/10 | 0.99 | n/a |
| claims_reported | Jev | 10/10 | 10/10 | 1.00 | n/a |
| canceled_after_notice | Jev | 8/10 | 8/9 | 1.00 | 0.43 (n=1, plus 1 with no call) |
| customers_deenergized | Luna | 10/10 | 10/10 | | |
| first_deenergization | Luna | 10/10 | 8/8 | | |
| last_restoration | Luna | 10/10 | 9/9 | | |
| counties_deenergized | Luna | 10/10 | 9/9 | | |
| event_duration_hours | code | 10/10 | 7/7 | | |

Jev: 42 of 50 right. Luna: 40 of 40 right, and the cited page supports the value in all 40.

Jev accuracy by confidence band:

| Confidence | Answers | Right |
|---|---|---|
| 0.9 to 1.0 | 37 | 35/37 |
| 0.7 to 0.9 | 5 | 3/5 |
| below 0.7 | 7 | 4/7 |

What the 8 Jev misses show:

- **Retrieval, not Jev, caused 4 of them.** The deciding page was never shown for PG&E 2025 cancellations (Table 1 on page 9 says Cancelled = 0), SDG&E 2021 MBL (Table 4 on page 23), SDG&E 2020 cancellations (no page matched), or PG&E 2023 MBL (Table 7 on page 48). Two of these were confident misses: SDG&E 2021 MBL came back not_stated at 1.00, and PG&E 2023 MBL came back some_not_notified at 0.96. Jev was right about the pages it saw; the right page was missing. This matches the lesson in CLAUDE.md that Jev is confident relative to what it is given.
- **2 misses are reading errors with the evidence shown.** For SDG&E 2020 wind (true at 0.89), Jev took "circuit 157 met the PSPS threshold for notifications" as a wind threshold. For SDG&E 2020 MBL (some_not_notified, but at only 0.39), the wrong answer came with low confidence.
- **2 misses are PG&E wind rows with uncertain gold labels.** All four PG&E wind answers sat between 0.51 and 0.70 confidence (noul p 0.43 to 0.70). That band reflects a real ambiguity in how PG&E describes its thresholds, so these are better sent for review than scored.
- Complaints and claims were 20 of 20 at confidence 0.92 to 1.00. These sections follow a fixed template and are easy to retrieve.

For Luna, every value was right, but the quote check flagged 6 of 40 quotes as "not on page" even though the values were right. The causes were line-break hyphens ("de-\nenergizing"), footnote markers glued to numbers ("customers1"), and table cells joined across lines. A real pipeline needs a looser check, such as matching the digits of the value on the cited page.

Some reports disagree with themselves. PG&E 2021 says de-energization began at 06:00, but its circuit table starts at 01:34. SDG&E 2021 gives three different start times (11:47, 18:00, and a first circuit at 21:53). SCE 2021's introduction lists Kern among the de-energized counties, while its Section 3 says Kern was only in scope. PG&E 2025 says all customers were restored on January 21, yet a notification table shows shared SCE customers were restored on January 24. Any dataset built from these reports needs a stated rule for which source wins, or a per-field flag.

## Cost

| Item | Calls | Tokens | Cost |
|---|---|---|---|
| Jev (jev-latest, $0.042 per M input) | 50 | 134,401 input | $0.0056 |
| GPT-6 Luna (openai/gpt-6-luna, $0.10 in and $0.50 out per M) | 10 | 967,225 in, 12,417 out | $0.1666 |
| Total | | | $0.17 |

That is well under the $2 cap. Luna costs 30 times more than Jev because it reads whole reports; the largest (SDG&E 2021, 232 pages) cost $0.08.

## Scaling to every filed report

The two CPUC pages link about 197 unique post-event PDFs from 2017 to 2026. About 26 of them are amendments or corrections, and some are from PacifiCorp, Liberty, and BVES. Scaling would take the following:

1. **Inventory and dedup.** Scrape both pages and map each PDF to a utility and event. Link amendments to their originals and decide which one wins. Pre-2021 reports use an older format with numbered questions instead of the SED template, so they need their own keyword patterns.
2. **OCR fallback.** Add a check for garbled pages (`common.is_garbled`) and send broken-font files through OCR. 1 of the 11 files I opened needed it, and no OCR tool is installed here yet.
3. **Better retrieval for Jev.** This is the main accuracy lever. Half of Jev's misses were retrieval misses. Three changes would help: always include the executive summary table page (it holds the notified, de-energized, and cancelled counts and the MBL count), find the template section headers (Section 5 notifications, Section 7 complaints) instead of just scoring keywords, and show more pages. Jev accepted a 24k-token state without trouble, and at $0.042 per million tokens, showing 20 pages per question would cost about $0.02 per report.
4. **Tighter questions.** Split the MBL question into "any de-energized MBL customer had no notification attempted or delivered" and "any de-energized MBL customer did not confirm receipt". For wind, either accept that PG&E is inherently ambiguous or ask whether the report states a wind criterion was met.
5. **Use the data workbooks where they exist.** Since 2021, SCE and PG&E file Excel event workbooks and geodatabases with per-circuit times and customer counts. For numbers, those beat PDF extraction, and Luna can then serve as a cross-check.
6. **Review queue instead of full hand-checking.** Send any Jev answer below 0.9 confidence to human review. At this pilot's rates, that is 12 of 49 answers, and the queue would catch 5 of the 8 misses. Flagging calls where retrieval found no page catches a 6th. The other 2 are confident retrieval misses (0.96 and 1.00). Spot-check a random 10% of the rest, plus every number whose quote check fails. Hand-checking these 10 reports took roughly 15 to 20 minutes each. A full check of about 170 reports would be around 50 hours, which a review queue would cut to a fraction.
7. **Cost at full scale.** About 170 reports cost about $3 of Luna for full-text reads (about $0.017 average per report) and under $0.10 of Jev, even with the larger retrieval windows above.

## Reproduce

```
python research/psps_reports/extract_pages.py      # download PDFs, write pages/
python research/psps_reports/jev_extract.py        # Jev, writes runs/jev_raw.jsonl
python research/psps_reports/llm_extract.py        # Luna, writes runs/llm_raw.jsonl
python research/psps_reports/build_pilot.py        # pilot.csv, validation.csv, validation_summary.md
```

Keys (`TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`) are read from the repo `.env` and are never printed or written. Both extractors stop if spend reaches $2. `jev_extract.py --dry-run` prints the retrieved pages without calling Jev.
