# Structured data from CPUC PSPS post-event reports: findings

Research memo, September 2026. Details and code are under `research/psps_reports/`: round 1 (pilot), `round2/` (clean test), `round3/` (full run), and `round4/` (model-assisted review of the round 3 queue).

## What was built

After every Public Safety Power Shutoff (PSPS), California's utilities must file a post-event report with the CPUC. The reports are long PDFs (10 to 550 pages) with no machine-readable companion for most events. We built a pipeline that turns every PG&E, SCE, and SDG&E post-event report the CPUC lists into one table: 155 events, 2017 to 2026 (`round3/dataset.csv`).

For each event the table records nine fields:

- **Five categorical facts:**
  - were Medical Baseline (MBL) customers notified before their power was cut,
  - does the report say wind met the shutoff threshold,
  - were any complaints reported,
  - were any claims reported,
  - were some notified customers never shut off.
- **Four numbers:**
  - customers de-energized,
  - first shutoff time,
  - last restoration time,
  - counties affected.

  Event duration is computed from the two times.

Every value carries its source (PDF page or Excel sheet and row). Uncertain values go to a review queue rather than into the table unmarked.

## Method

- **Categorical facts: Jev.** Jev is a typed decision model that picks one of a fixed set of options and reports a probability. It is asked one small question per fact with mutually exclusive options, always including "not stated in the report". It sees only the pages relevant to that question: the summary table, the template section for the topic, and the best keyword matches, up to 8 pages. If no page matches, the question is flagged and Jev is not asked.
- **Numbers: GPT-6 Luna.** Luna reads the full text and must cite a page and a quote for each number. Code, not the model, computes duration. Luna may not use times from notification logs, status updates, or "the event ended" statements.
- **Preprocessing:**
  - Table pages that exist only as images or vector drawings are rendered and transcribed.
  - Where a utility posts an Excel event workbook, circuit-level times come from the workbook.
  - Amended reports replace the original when the amendment is a full report; partial corrections are flagged.
- **Review rule:** an answer is flagged when Jev's confidence is below 0.9, when no page matched, when a relevant page could not be read, or when sources conflict.
- **Evaluation discipline:**
  - Each round was designed on reports already read, then tested on reports never read.
  - The test sample and pipeline version were committed before each run.
  - Gold labels for the final test were written before looking at the pipeline's answers.

## Accuracy

All labels are from one reviewer (the author), and no second reader has checked them. The two-reviewer split planned a 40-item overlap to measure that, but round 4 used models in place of the two human reviewers, so the overlap was never scored.

| Check | Reports | Clean? | Result |
|---|---|---|---|
| Pilot (round 1) | 10 | No, used for design | Jev 42/50, Luna numbers 40/40 |
| Round 2 test | 15, never read, stratified by utility and era | Yes | Jev 67/75 (50 of 51 right at confidence 0.9 or above); Luna 55/60 |
| Round 3 test | 10, never read, random (seed 20260924) | Yes | 80/90 values right; numbers 40/40; the review rule flags 9 of the 10 errors |

- **Trust in unflagged answers.** In the round 3 test, 69 of 70 unflagged values were right. With a sample this small, the true error rate for unflagged values could plausibly be anywhere from well under 1 percent to about 8 percent. The sample also leaned toward events with no shutoff (5 of 10, against 37 of 155 overall).
- **The weak fields** are MBL notification and wind. Both depend on how a utility words its report. PG&E, for example, describes a composite risk score rather than a wind threshold, so "does the report say wind met a threshold" often has no clear answer.
- **The numbers were reliable** whenever the report actually stated them. Luna's misses in round 2 were all cases where the PDF's circuit table was incomplete. The round 3 rules make it return null there instead.

## Model-assisted review of the queue (round 4)

Round 4 replaced the two human reviewers with two independent models: Claude Opus 5.5, run as fresh headless Claude Code sessions confined to a packet folder, and GPT-6 Sol on OpenRouter. Each model answered every flagged item from the report itself, without seeing the gold labels or the other model's output. On the blind items, neither model saw Jev's answers or confidences. Two exceptions apply:

- **Special items.** Sessions for special items (partial corrections, unreadable pages, workbook times) also received the event's current values from `round3/dataset.csv`, as `round4/DESIGN.md` allows. Those values include Jev's answers for the five categorical fields, on 23 events.
- **Item reason.** Every item showed its `reason`. For `low_confidence`, which the guide explains as "Jev answered, but not confidently", that reveals Jev's confidence was below 0.9, though not the answer or the number. An item's `page_refs` are the pages Jev was shown.

Code checked that each answer was an allowed value, that its quote appears on a cited page, and that the model was not unsure. A `not_stated` or `null` answer needs no quote, only the pages read and the search terms used. Items where both answers passed and matched were accepted. Details are in `round4/README.md` and `round4/DESIGN.md`.

**These values are model-reviewed; accuracy was measured on 211 hand-labeled values from 24 reports; no human audit of the reviewed items.** The design planned 25 reports. One round 2 report was excluded because round 3 used an amended PDF.

- **Test, run once after a committed freeze:**
  - On the 29 agreed queue-matched items, 27 were right (93.1%, Wilson 95% interval 78.0 to 98.1). The bar was 90%.
  - On all 179 agreed test values, 177 were right (98.9%, 96.0 to 99.7). The bar was 95%.
  - The models agreed on 179 of 211 test values.
  - Both agreed errors are SDG&E MBL items.
- **SDG&E MBL caveat:** Both agreed errors in the test were SDG&E Medical Baseline items (queue items 117 and 141). 8 SDG&E mbl_advance_notice items were accepted in the queue. Six of them now carry their hand label: 117 and 141, where the hand label replaced the agreed answer, and 204, 230, 253, and 260, where the agreed answer already matched it. The caveat applies to the two accepted SDG&E Medical Baseline items without a hand label, 184 and 203. The review rules were not changed after the test.
- **Queue:**
  - 206 of the 286 items were accepted as `model_review_agreed`.
  - 80 remain unresolved, mostly because Opus marked them unsure (60).
  - In the dataset, 145 field values are `model_review_agreed`, 112 are `model_review_no_change`, 270 are `model_labeled` (they carry an old gold label, which a model wrote; see `round5/AUDIT_PLAN.md`), 107 are unresolved, and 761 were never flagged, out of 1,395 (155 events × 9 fields).
  - `model_review_no_change` marks the 112 fields, on 17 events, that no reviewer answered on its own. An agreed whole-event item (a correction letter, a table page, or a report cut for length) left them unchanged, and the reviewers had the current values in view. `apply.py` had counted them as `model_review_agreed`. They were relabeled after the review of this work (`round4/no_change.py`), and no value changed.
  - **Agreed `not_stated` answers need no quote.** 88 of the 206 accepted queue items are `not_stated`. In 44 of them neither reviewer quoted a page, so the only evidence check was that both listed the pages they read and their search terms. In the other 44, at least one reviewer's quote was found on a cited page. The test backs these less than other values: only 18 of the 179 agreed test values were `not_stated` (all 18 right, 6 with no quote from either reviewer).
  - Model review changed 82 values and hand labels changed 8. In all, 87 values differ from `round3/dataset.csv`, since some fields changed in both steps.
  - Hand labels were applied after the test (`round4/hand_labels.py`), so known errors do not stay in the dataset. They come from the test gold and from the round 1 gold. The round 1 gold was converted to round 3 rules the same way as round 2: 63 of 90 rows kept, and 27 excluded (every time row, and 7 wind rows).
  - A gold `null` does not replace a time taken from a utility workbook, because the gold was written from the PDF alone. That case applies to 4 fields.
  - The PG&E Oct 21 2020 first de-energization keeps 14:42 and is unresolved. Its round 1 label (17:33) did not survive the conversion, and the report states both times: 14:42 for a transmission line and 17:33 for the first distribution circuit. It is listed in `round3/contradictions.csv`.
  - `round4/dataset_reviewed.csv` carries a per-field `review_method`.
- **What changed most:**
  - Wind answers moved from `met` or `not_met` to `not_stated` (13).
  - Claims moved from `zero` to `not_stated` (11).
  - 32 no-matching-page placeholders were filled in.
- **Limits:**
  - The queue-matched bar passed on its point estimate (27/29, 93.1%), but its 95% interval, 78.0 to 98.1, reaches well below the 90% bar.
  - The 28 special items (partial corrections, unreadable pages, workbook times) have no direct gold.
  - The 3 redlines were not asked.
  - The two human reviewer files in `round3/` were not used and are unchanged.
  - **A misapplied correction letter.** SCE's consolidated correction letter of April 1, 2024 amends only the Oct 29, Nov 9, Nov 20, and Dec 9, 2023 reports. Round 3 linked it to 8 SCE 2023 events. For the other four (Jul 11, Jul 18, Oct 11, and Nov 26, 2023), both reviewers found that it amends nothing, and those items (18, 19, 17, and 10) were agreed as no change. `round3/versions.csv` still lists the letter for all 8.

## Cost

All OpenRouter and TypeSafe calls for rounds 1 to 3 cost **$2.66**, and round 4 added **$10.39**, for **$13.05** in total:

| Round | Cost | What it covered |
|---|---|---|
| Round 1 | $0.17 | 10 reports |
| Round 2 | $0.21 | 15 reports |
| Round 3 | $2.28 | All 155 reports, including image transcription |
| Round 4 | $10.39 | GPT-6 Sol for model-assisted review (development, test, queue); Opus ran on the Claude subscription (199 sessions, no dollar cost) |

The dominant cost is human review, not computation.

- **Review queue:** 286 flagged items across 145 of the 155 events.
- **Reviewer split:** two reviewers with 163 items each (40 shared), about 5.4 to 8.2 hours each at 2 to 3 minutes per item.
- **Hand-checking time:** hand-checking a report in full took 15 to 20 minutes.

## Internal contradictions (22, hand-checked)

We found 22 places where a report disagrees with itself. They come from 17 of the 35 reports read closely, spread across all three utilities and all eras. Page numbers are PDF pages.

**1. Start and end times that disagree (10).** The narrative gives one time and the circuit table another, or "the event ended" is presented as the restoration time.

- PG&E, Oct 11, 2021: de-energization "began at 06:00" (p4), but the first circuit went out at 01:34 (Appendix B, p102).
- SDG&E, Nov 24 to 26, 2021: the start is given as 11:47 (p20), as 18:00 (p6), and as the first circuit at 21:53 (p124).
- SCE, Sept 2, 2025: "ended at 4:00 pm by which time service was restored" (p19), but the last circuit was restored at 12:49 (p62).
- SCE, Oct 23 to 28, 2020: the Atento circuit is listed as re-energized 10/28 07:30 (table, p7) but restored 10/29 11:53 (p12).
- SDG&E, Oct 19 to 20, 2018: final restoration is 10/20 09:38 (p3), but the attachment shows "PSPS Restore" at 10/19 14:52 (p10).
- Also:
  - PG&E, Oct 23 to 25, 2019: 18:20 vs about 18:01.
  - PG&E, Jan 13 to 15, 2025: 22:45 vs 23:13.
  - SCE, Oct 22, 2021: ended 3 pm vs restored 4:29 pm, with one customer not restored.
  - SCE, Oct 29, 2023: 06:31 vs 07:42.
  - PG&E, Jan 20 to 21, 2025: "all restored" on 1/21, but shared SCE customers were restored 1/24.

**2. Customer totals that disagree (5).**

- SCE, Oct 23 to 28, 2020: 36,307 (p3) vs 36,037 (p22). This looks like transposed digits.
- PG&E, Oct 23 to 25, 2019: "approximately 177,000" (p2, p9) vs 176,620 (Table 2, p9).
- SCE, Sept 4 to 8, 2019 (amended report): "approximately 650" (p3) vs 240 + 392 = 632 (p5).
- SDG&E, Dec 23 to 24, 2020: 6,797 is given as the number of accounts impacted (p11, p128) and also as the number spared by sectionalizing (p10).
- SDG&E, Jan 20 to 24, 2025: 29,980 total vs 27,015 unique customers (both on p6). This is stated, not an error, but a dataset has to choose one.

**3. Counties in scope counted as de-energized (3).**

- SCE, Nov 24, 2021: 6 counties including Kern (p8) vs 5, with Kern "in scope but not de-energized" (p22).
- SCE, Oct 29, 2023: 6 in Table 1 and the introduction (p4, p6) vs 5 in the summary and Section 3 (p5, p18).
- SDG&E, Jan 20 to 24, 2025: 3 counties in Table 1 (p8) vs 4 named in the introduction (p6).

**4. Breakdowns that do not add up (3).**

- PG&E, July 20 to 21, 2024: the text gives 112 residential, 92 commercial, and 24 other customers (p23); the circuit table totals 110, 82, and 19 (p76).
- PG&E, Oct 25 to 28, 2020: 30 MBL customers without notification (p35) vs 51 without an attempted notification (p36).
- PG&E, Jan 13 to 15, 2025: one customer not notified (p9) vs three (p41).

**5. Incomplete tables presented as the record (1).** SCE, Jan 20, 2025: the circuit table in the PDF lists 5 of 225 de-energized circuits (p19). The full table is only in the Excel workbook. SCE's Oct 29, 2023 report has the same problem: 5 of 41 circuits.

Most of these are small. They matter because a dataset built from these reports silently picks one value unless it has a stated precedence rule. Ours prefers the circuit table when complete, then the section that answers the CPUC template question. The contradictions also bear on reporting quality in their own right.

Round 4's reviewers added 16 `CONTRADICTION:` notes, marked "model-found, unchecked". A further 28 automatic candidates (workbook versus PDF time conflicts, and reports stating several customer totals) are listed in `round3/contradictions.csv` and have not been hand-checked.

## Gaps in what utilities publish

- **Dead links.** Two SCE 2019 originals (Sept 4 to 8 and Oct 21 to 26) now return "resource removed" on the CPUC site. Only their amendments survive.
- **Missing workbooks.** The CPUC template requires an event data workbook with circuit-level times, but only 8 are public:
  - 6 from SDG&E, on sdge.com,
  - 2 from SCE, on the CPUC site.

  SCE's own PSPS reports page returns 404. Recent SCE PDFs list only a handful of circuits, so exact times for most SCE events are not publicly recoverable from the PDF. One SDG&E workbook (Oct 2023) has no circuit table at all. We did not collect PG&E attachments.
- **Tables with no text.** Some tables exist only as images or vector outlines. For example, SCE's Table 1 (event summary) and Table 9 (MBL notification) in Oct 2021 have no text layer. We transcribed 414 such pages; 11 were unreadable.
- **A cover filing with no report.** For PG&E's Dec 15, 2023 event, the CPUC file is only the 3-page cover filing. The report itself (Attachment A, Table A-1.2) was filed on archival DVD, so nothing can be read from the PDF. The dataset has no numbers for this event. Its categorical fields are `not_stated`, except MBL, which is unresolved.
- **Broken text encoding.** One PG&E report (Sept 30, 2023) has a font encoding that shifts every letter and drops every digit, so it needs OCR.
- **Redlines and corrections.**
  - Three PG&E 2024 amendments were filed only as redlines, where extracted text mixes deleted and inserted values.
  - 17 events have partial correction letters instead of restated reports.
- **Format drift.** Reports from 2017 to 2020 are letters answering numbered ESRB-8 questions. The standard template starts in late 2021, so older events are less likely to state MBL, complaint, or cancellation facts explicitly.

## Cross-check against the warehouse PSPS table

We compared the round 3 dataset with the warehouse table `wildfire.psps_events`, which is loaded from `psps_events.geojson` in the `dataset_demo` project. That table holds 56 PG&E, SCE, and SDG&E events from October 2021 to November 2025, with dates but no times. We matched events by utility and overlapping dates, using read-only queries. The script is `round3/warehouse_crosscheck.py` and the row-level output is `round3/warehouse_crosscheck.csv`.

- **Coverage.** All 56 warehouse events match a report, one to one.
  - Inside the warehouse's date range, the dataset has 22 more events, all of them events where customers were notified but nobody was shut off. The warehouse records only shutoffs, so this is expected.
  - Outside that range, the dataset adds 58 shutoff events (2017 to early 2021, and 2026) and 19 more no-shutoff events.
  - No shutoff event appears in the warehouse without a report, or the other way round, within the overlap.
- **Customers de-energized: 37 of 56 match exactly.** 15 differ by under 2 percent, usually by 1 to 40 customers, and the dataset is higher in 11 of them. The largest of these is SCE, Nov 24, 2021: 78,514 in the report (p8, p22) against 79,697 in the warehouse. Four differ a lot, and in each the report explains the gap:
  - **SDG&E, Jan 7 to 16, 2025:** 21,508 (dataset) vs 15,103 (warehouse). The report gives both: "21,508 total customers (15,103 unique customers)" (p7). It also shows the amended total replacing an earlier 21,605.
  - **SDG&E, Jan 20 to 24, 2025:** 29,980 vs 27,015. Same pattern: total vs unique, both stated on p6. The warehouse uses unique customers for SDG&E and the dataset uses totals.
  - **SCE, Oct 15, 2021:** 67 vs 104. The report says 67 three times (p8, p9, p38), and its footnote says this is the unique count although one circuit was shut off twice. The number 104 appears nowhere in the report text.
  - **SCE, Oct 1, 2024:** 15 vs 1. The report says "1 SCE customer and 14 PG&E customers were de-energized" (p8). The warehouse counts SCE's own customer only, and the dataset adds the PG&E customers. This page's text layer is also scrambled, another broken-font case; we read it from the rendered page.
- **First shutoff date: 51 of 53 match.** For three events the report states no first time and the dataset has none; those are not compared. The two misses are one day apart, and in both the report supports the dataset:
  - PG&E, Oct 14 to 16, 2021: "On October 15, 2021 at 01:00 PDT, PG&E began de-energizing" (p7), and the circuit table starts 10/15 01:00. The warehouse has 10/14.
  - PG&E, June 2025: "On June 19 at 04:47 PDT, PG&E began de-energization" (p4). The warehouse has 6/18.
- **Last restoration date: 50 of 55 match.** Three of the five misses trace to a small number of late customers that the warehouse leaves out:
  - PG&E, Oct 11, 2021: the Calpine 1144 line, which PG&E does not own, was restored 10/14 (Appendix B, p102), two days after everyone else (warehouse 10/12).
  - SCE, Nov 24, 2022: one commercial customer was left off until 11/27 because an isolation device was left open (p40), while "service was restored to all" by 11/25 (p21). The warehouse has 11/25.
  - SDG&E, Nov 6 to 8, 2024: the report's restoration table and text both end 11/8 at 08:18 (p41, p139), against 11/7 in the warehouse.

  The other two, SCE Sept 7, 2024 and SCE Oct 28, 2025, differ by one day in the other direction and were not checked against the reports.
- **Counties: 42 of 56 match, but this comparison is weak.** The warehouse has no county field. We counted counties covering at least 1 percent of each event polygon, and the polygons clip slivers of neighboring counties. For example, Orange, Riverside, and Imperial together make up under 0.4 percent of the SDG&E polygons, while the reports' own tables list 3 or 4 counties. Treat county disagreements as a definitional difference, not an error in either source.

**The pattern.** The two sources agree on which events happened and on almost all dates. They disagree mainly on definitions:

- total vs unique customers,
- whether one utility's report counts another utility's customers,
- whether a handful of late or third-party customers set the restoration date.

In every large disagreement we checked, the report pages support the dataset's value as the report's own number. The warehouse value is usually also a defensible reading. A combined panel should carry both a total and a unique customer count, and should state whose customers are counted.

## What the dataset could support after review

Round 4 settled 206 of the 286 flagged items by model review. Once the remaining 80 are resolved by a person, the table would be a consistent, sourced event record of PSPS use by the three utilities from 2017 to 2026. It could support:

- **An event panel:** frequency, size (customers), duration, and geographic spread (counties) of shutoffs by utility and year. Events where notices went out but no one was shut off are included, which matters for studying notification burden.
- **Compliance-style indicators:** how often de-energized MBL customers were not notified in advance, how often events generated complaints or claims, and how often notified customers were never shut off. These are presence or absence indicators, not counts. The pipeline does not yet extract complaint and claim counts, though the reports usually give them.
- **Cross-checks against our warehouse PSPS data** (`wildfire.psps`) and CAL FIRE incidents, using event dates and counties. For the 7 events with workbooks, the comparison can go down to the circuit.
- **A report-quality analysis.** With contradictions in about half the reports read closely, the consistency of utility reporting is itself a measurable outcome.

**Caveats for use:**

- The wind field is weak for PG&E and should not be used without review.
- Circuit-level times are reliable only for the 7 workbook events and for reports whose PDF table lists every circuit.
- All gold labels come from one labeler, and no second labeler has checked them. The planned two-reviewer overlap was never scored, because round 4 used models in place of the two human reviewers.
- Values marked `model_review_agreed` were settled by two models, not a person. Values marked `model_review_no_change` were left unchanged by an agreed whole-event item and never answered on their own. Values marked `model_labeled` carry an old gold label, written by a Claude Opus 5.5 session, not a person. Use `review_method` to separate them.
