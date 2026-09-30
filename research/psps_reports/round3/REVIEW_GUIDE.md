# Review guide: PSPS post-event report dataset

## What this is

California's electric utilities file a report with the CPUC after every Public Safety Power Shutoff (PSPS) event. In a PSPS, the utility turns power off to prevent wildfires during high winds. We extracted a small set of facts from every PG&E, SCE, and SDG&E post-event report (155 events) into `dataset.csv`.

- A typed decision model called Jev answered five yes/no-style questions.
- A language model (GPT-6 Luna) read the numbers.
- Where a utility posted an Excel workbook, circuit times came from the workbook.

Answers the pipeline was unsure about were put in a review queue. Your job is to settle each queued item from the report itself.

Your file is `review_reviewer_A.csv` or `review_reviewer_B.csv`. Each file has 163 items. Budget about 2 to 3 minutes per item, which is 5.5 to 8 hours in total.

## How to work

1. **Work one report at a time.** Your file is sorted by `report_id`, so all items for one PDF are together. Open `document_url` once and settle every item for that report before moving on.
2. **Page numbers are PDF page numbers**, counted from the first page of the file (page 1 is the cover letter), as your PDF viewer shows them. They are not the page numbers printed in the report's footer, which are often off by 2 to 10.
3. **`page_refs`** lists the pages the pipeline looked at. They are a starting point, not a limit. The answer is often elsewhere, for example in a summary table (usually "Table 1"), a notification section, or a complaints section.
4. **Decide before you look at Jev's answer.** Your file does not include Jev's answer or confidence.
   - Record your decision first.
   - Then, if you want, open `review_answer_key.csv`, find the same `item`, and fill in `matches_jev` (yes or no).
   - Never change a decision after seeing the key. If you think you made a mistake, add a note instead.
5. **Overlap items.** Rows marked `overlap = yes` are also in the other reviewer's file. Review them on your own and don't discuss them with the other reviewer; they measure how often two careful readers agree.
6. **Record your time** per item in `minutes_spent`, to the nearest minute.

## Where to record decisions

Fill in these columns in your own CSV. Leave the other columns unchanged.

| Column | What to put |
|---|---|
| `decision_value` | Your answer, using exactly one of the allowed values below for that field. |
| `decision_pages` | The PDF page or pages that support your answer, separated by semicolons (for example `9;41`). Use `none` if the report is silent. |
| `matches_jev` | yes or no, filled only after you have decided (see step 4). |
| `notes` | Anything a second reader needs: a quote, the reason for a judgment call, or a contradiction (see below). |
| `minutes_spent` | A whole number. |

## The fields

Answer only from what the report says. Don't use outside knowledge, other reports, or news coverage.

### `mbl_advance_notice`: were Medical Baseline customers notified in advance?

Medical Baseline (MBL) customers are enrolled in a program for people who rely on powered medical equipment. The question is whether every MBL customer who actually lost power had a notification delivered before the shutoff.

| Value | Use when |
|---|---|
| `all_notified` | The report says every de-energized MBL customer had a notification delivered (or a successful positive notification) before de-energization, or it reports zero MBL notification failures. A door visit or door hanger counts, even if the customer never confirmed receipt. |
| `some_not_notified` | The report says at least one de-energized MBL customer had no notification attempted or delivered before de-energization. This includes events where the utility could send no advance notice at all to the customers who were shut off. |
| `not_applicable` | The report says no customers were de-energized in the event. |
| `not_stated` | Customers were de-energized, but the report doesn't say whether the MBL customers among them were notified in advance. |

MBL customers who were in scope but never lost power don't count. For example, "one MBL customer could not be contacted, but was not de-energized" is still `all_notified`. Some utilities (SCE in 2019 and 2020) track only "critical care" customers, a subset of MBL. Use what they report, and note the substitution.

### `wind_threshold_cited`: does the report say wind met the shutoff threshold?

| Value | Use when |
|---|---|
| `met` | The report says observed or forecast wind speeds or gusts met, reached, or exceeded a de-energization threshold, alert speed, or wind criterion for at least one area that was de-energized. Forecast statements count, for example "circuits forecast to exceed alert speed criteria". PG&E's 2019 "outage producing wind" levels count as a wind criterion. |
| `not_met` | The report says winds did not reach the threshold in any de-energized area (power was shut off for other reasons). This is rare. |
| `not_stated` | The report gives wind readings, forecasts, or a description of the threshold process, but never says winds met a threshold in a de-energized area. Also use it when nothing was de-energized. |

PG&E from 2020 on usually describes a composite model score (FPI, CFP, or "PSPS guidance") rather than a wind threshold. A statement that an area "exceeded PSPS guidance" is **not** a wind statement. Statements such as "the remaining areas were delayed because winds failed to reach mFPC" only imply that the de-energized areas met it. Record `not_stated` and quote the sentence in notes.

### `complaints_reported` and `claims_reported`

| Value | Use when |
|---|---|
| `one_or_more` | The report gives a count of at least one complaint (or claim) for this event. |
| `zero` | The report says none were received. "No formal complaints were lodged" counts as zero; note if the report limits it to one channel, such as "from the CPUC". |
| `not_stated` | The report gives no count for this event. This includes "Not applicable" and counts deferred to another report ("complaints will be reported with the October 26 event"). If the section talks only about complaints, claims are `not_stated`. |

### `canceled_after_notice`: were some notified customers not shut off?

| Value | Use when |
|---|---|
| `yes` | Some notified customers, circuits, or areas were not de-energized. Examples: a cancellation, a removal from scope, "no longer at risk" notices, a "Cancelled" count above zero in the summary table, or simply more customers notified than de-energized. It also covers events where customers were notified and nobody was shut off. |
| `no` | The report says every notified customer was de-energized, or its summary table shows Cancelled = 0. |
| `not_stated` | The report doesn't say. For example, no advance notices were sent at all and the report is silent on scope. |

Compare "customers notified" or "in scope" with "customers de-energized" when the report gives both. A gap means `yes`, even if the word "cancel" never appears.

### Numbers

| Field | Format | Rule |
|---|---|---|
| `customers_deenergized` | whole number | The event total as the report states it. Use `0` if nobody was shut off. If the report gives two totals (for example total and unique, or approximate and exact), put the one in the summary table and note the other. |
| `first_deenergization` | `YYYY-MM-DD HH:MM`, 24-hour local time | When the first customers lost power: the earliest time in a circuit table that lists every de-energized circuit, or a sentence stating the first de-energization. Don't use EOC activation, notification or status-update times, or "the event began at". |
| `last_restoration` | same | When the last de-energized customers got power back. Don't use "the event ended at" or EOC deactivation. |
| `counties_deenergized` | whole number | Counties where customers actually lost power, not counties only in scope. |

If the report doesn't state the value (for example the circuit table lists only some circuits and no sentence covers the whole event), write `null` and explain in notes.

## Item types (`reason` column)

- **low_confidence:** Jev answered, but not confidently. Answer the question yourself.
- **no_matching_page:** the pipeline found no page on the topic. Search the whole PDF (Ctrl+F for "cancel", "complaint", "claim", "Medical Baseline", "threshold"); the answer is often under unusual wording. If the report is truly silent, answer `not_stated`.
- **unreadable_page:** a table page is an image that couldn't be read automatically, or a very long report was cut short. Read the listed pages, then note which dataset fields they change. Write `decision_value = no change`, or give the corrections as `field=value; field=value`.
- **conflicting_sources**, which comes in three kinds:
  - *Workbook vs PDF time:* `detail` shows both values. Decide which is right for the whole event, using the rules above, and put it in `decision_value`.
  - *Partial correction filed* (`field = all fields`): `detail` links a correction letter. Read it, then compare with this event's row in `dataset.csv`. This item is not blind, since you need the current values. Write `no change`, or give the corrections as `field=value; field=value`.
  - *Redline* (`field = numeric fields`): the report is a marked-up amendment, and the pipeline may have read a struck-out (deleted) number. Check the four numbers in `dataset.csv` against the clean (non-struck) text. Write `no change` or give the corrections.

## Amendments and contradictions

- **Which document:** `document_url` is the version the pipeline used. That is the latest full amendment where one exists, otherwise the original. Review that document. If an amendment, and not the original, says something different, the amendment wins.
- **Contradictions inside a report are common.** Earlier rounds found them in 16 of 25 reports. Examples:
  - narrative start times that disagree with the circuit table,
  - "event ended" used as a restoration time,
  - counties in scope counted as de-energized,
  - typos (36,307 against 36,037).

  To resolve one:
  1. Prefer the circuit table when it lists every circuit.
  2. Otherwise prefer the section that answers the template question: Table 1 for totals, Section 3 for time and place, Section 5 for notifications, Section 7 for complaints and claims.
  3. Record your choice in `decision_value`.
  4. Start the note with `CONTRADICTION:` and give both values and both pages, for example `CONTRADICTION: 36307 (p3) vs 36037 (p22)`.

  These notes will be merged into `contradictions.csv`.
- **Don't guess.** If the report can support two answers and the rules above don't settle it, pick the closest one, add `UNSURE:` at the start of the note, and say why. Unsure items get a second look.

## When you finish

Save your CSV under the same name and send it back. Once both files are in, `python research/psps_reports/round3/agreement.py` reports the agreement rate on the overlap items and lists the disagreements to settle.
