# PSPS post-event reports: round 3 (full run)

Round 3 fixes the problems found in round 2 and runs the pipeline over every PG&E, SCE, and SDG&E post-event report the CPUC lists. It then measures accuracy on 10 reports that had never been read.

## Outputs

| File | What it is |
|---|---|
| `dataset.csv` | One row per event (155). Every field, its value, its source (PDF pages shown to Jev, the PDF page Luna cited, or the Excel sheet and row), Jev confidence, the PDF value next to any workbook value, and the event's flags. |
| `review_queue.csv` | Only flagged items (286). Each has the question, Jev's answer, its confidence, page references, and the document URL. Sorted in this order: conflicting sources, unreadable pages, no matching page, then low confidence (lowest first). |
| `contradictions.csv` | 22 hand-checked contradictions (21 from rounds 1 and 2, plus 1 found in the accuracy sample), plus 28 automatic candidates. The candidates are either workbook versus PDF time conflicts or reports that state several different "customers de-energized" totals. |
| `accuracy_sample_summary.md`, `validation_sample10.csv`, `gold_sample10.csv` | The accuracy check (step 4). |
| `manifest.json`, `versions.csv`, `accuracy_sample.csv` | The frozen run: pipeline version, event list, and sample. Committed before the run (commit 78d6e63). |
| `amendments.csv`, `amendment_links.csv` | Every amendment and correction, linked to its original, with the rule applied. |
| `runs/` | Raw outputs: `jev.jsonl`, `luna.jsonl`, `images.jsonl` (image transcriptions), `workbook_times.jsonl`, and `spend.json`. |

## Sources

- **Reports:** the CPUC current and archive PSPS report pages, using the round 2 snapshots in `../round2/cpuc_pages/`.
  - There are 155 original post-event reports for the three utilities. That is the 142 in the round 2 pool, plus the 10 pilot reports and the 3 PG&E 2023 files the round 2 pool left out.
  - Two SCE 2019 originals now return "resource removed" on the CPUC site, so their amendments are the only available version.
- **Amendments:** 27 amendment or correction files, linked 34 times, because the consolidated 2023 SCE correction covers 8 events.
- **Workbooks:** 8 event data workbooks are publicly posted.
  - SDG&E has 6 on sdge.com (psps-more-info page): Nov 2021, Oct 2023, Nov 2024, Dec 2024 (amended), Jan 7 2025, and Jan 20 2025.
  - SCE has 2 on the CPUC page: the Jan 4 and Jan 20, 2025 events.
  - SCE's own PSPS reports page returns 404, and I found no other SCE workbooks. The Oct 2023 SDG&E workbook has no circuit table, so circuit times come from workbooks for 7 events.

## Method

Everything below was designed only on the 25 reports read in rounds 1 and 2. It was frozen in `manifest.json` (pipeline round3-v1) before the full run.

1. **Amendment rule.** Each amendment is linked to the original listed just before it on the CPUC page. The consolidated SCE 2023 corrections and the SDG&E 2021 corrections are linked by event.
   - An amendment counts as **full** when it has at least half the original's pages and is not a correction letter. Otherwise it is **partial**.
   - The latest full version of each event is extracted. 15 events use an amendment.
   - A partial correction does not replace the report. Its event is flagged for review with the correction linked; 17 events have one.
   - Three PG&E 2024 amendments are redlines, whose text layer can include struck values, so their numeric fields are flagged.
2. **Page selection** (from round 2): summary and event-table pages, then template section anchors, then keyword score, up to 8 pages. Added in round 3: the cancellation phrasings seen in round 2 ("weren't turned off", "no longer at risk", "did not de-energize", "event avoided", and similar). A question with no matching page is flagged, and Jev is not asked.
3. **Jev questions:** the round 2 questions, each with a not_stated option. The MBL question also gets **not_applicable** for events with no customers de-energized.
4. **Image and vector tables.** A table page counts as unreadable-by-text when it has a "Table N" caption, fewer than 12 numbers before the next numbered item, and either an image covering at least 4 percent of the page or at least 20 vector drawings (some SCE tables are drawn as outlines). Near-empty scanned pages also count.
   - Tables that bear on the fields (summary, notification, circuit, complaint, and claim tables) are rendered at 150 dpi and transcribed by GPT-6 Luna, which must return the table or exactly UNREADABLE.
   - Up to 8 pages per report were transcribed. In all, 414 pages were transcribed, 11 were unreadable, and 11 were over the cap; those events are flagged.
   - 351 image tables that don't bear on the fields (thresholds, risk tools, lessons learned) were skipped and logged, not flagged.
5. **Numbers (Luna).** Luna reads the full text plus transcriptions. Its prompt now forbids times from notification logs, status updates, EOC open or close, and "event ended" statements, and requires null when the circuit table is partial.
6. **Workbooks.** The parser finds the circuit table in any sheet (SDG&E "Table 3", SCE "T05") and takes the earliest de-energization and latest restoration across all circuit rows. A workbook time replaces the PDF time. The PDF value is kept in `*_pdf_value`, and a gap of more than 30 minutes is flagged as conflicting sources.
7. **Review rule:** flag any Jev answer below 0.9 confidence, any question with no matching page, any relevant table page that could not be read (or text cut for length), and any conflicting sources (workbook versus PDF, a partial correction on file, or a redlined version).

**Changes made during the run** (recorded here because they alter the frozen file hash; none changes a question, prompt, or rule for reports already processed):
- The spend ledger had a thread race that crashed the first image-stage attempt; 123 pages were kept, not redone. Reads now take the lock and writes are atomic.
- OpenRouter error payloads now raise a readable error.
- One report, SDG&E Nov 26 to Dec 9, 2020 (395 pages, 3.6 MB of text), exceeded Luna's context window. For reports that big, pages are now kept in order up to 1.5 million characters, and the event is flagged. It was the only report affected.

## Accuracy on unseen reports (step 4)

Ten events were drawn with seed 20260924 from the 127 never read in rounds 1 and 2. I wrote the gold labels from the report text before looking at any pipeline output for them. Every value was checked, flagged or not. This set is clean: nothing about it was used to design the pipeline.

| Field | Right | Right, certain gold only | Flagged | Errors not flagged |
|---|---|---|---|---|
| MBL notified in advance | 8/10 | 7/8 | 4 | 0 |
| Wind met threshold | 9/10 | 7/8 | 6 | 0 |
| Complaints | 10/10 | 8/8 | 0 | 0 |
| Claims | 7/10 | 7/8 | 3 | 0 |
| Canceled after notice | 6/10 | 6/9 | 3 | 1 |
| Customers de-energized | 10/10 | 9/9 | 1 | 0 |
| First de-energization | 10/10 | 10/10 | 1 | 0 |
| Last restoration | 10/10 | 9/9 | 1 | 0 |
| Counties | 10/10 | 9/9 | 1 | 0 |
| Duration (computed) | 10/10 | 9/9 | 1 | 0 |

- **Totals:** 90 values checked, 10 errors. The review rule flags 9 of the 10 errors, and **69 of the 70 unflagged values are right (98.6%)**.
- **The error it missed:** SCE September 2019 (the amended report). Cancellation came back not_stated at 1.00, but the report says up to 16,000 customers were in scope and about 650 were de-energized; it never uses a cancellation word for the rest.
- **Jev calibration on the sample:**
  - 0.9 or above: 33 of 34 right.
  - 0.7 to 0.9: 5 of 8 right.
  - Below 0.7: 2 of 5 right.
- **Luna and workbook numbers were 40 of 40 right.** Five of the 10 sample events had no de-energization, and Luna returned 0 customers and null times for all five.
- **The sample is small and skews toward pre-template, no-shutoff reports.** Five of the ten had no de-energization, versus about 37 of 155 overall. It supports "unflagged answers are usually right". It does not support a precise error rate: at 69 of 70, the true unflagged error rate could plausibly be anywhere from under 1 to about 8 percent.

## Review queue

**286 items across 145 of the 155 events. At 2 to 3 minutes per item, that is 9.5 to 14.3 hours.**

| Reason | Items |
|---|---|
| Low Jev confidence | 212 (wind 87, MBL 83, cancellation 16, claims 16, complaints 10) |
| No matching page | 46 (cancellation 16, claims 9, complaints 9, MBL 8, wind 4) |
| Conflicting sources | 22 (17 partial corrections, 3 redlines, 2 workbook vs PDF restoration times) |
| Unreadable pages | 6 (5 table pages, 1 text cut for length) |

Wind and MBL account for 170 of the 212 low-confidence items. Most are PG&E wind answers (PG&E uses a composite model score, not a wind threshold) and MBL answers on older reports with no MBL accounting. Accepting "not_stated" for PG&E wind without review would remove most of the wind items. That is a policy call for the team, and I did not make it.

## Known limitations

- **Workbooks.** Public workbooks exist for only 8 events. Most SCE circuit times, and all PG&E times, still come from PDFs, and recent SCE PDFs list only 5 circuits in their tables. For those events Luna now returns null rather than a partial-table time.
- **Redlines.** The PG&E 2024 amendments are redlines, and text extraction cannot separate struck from inserted text. They are flagged, not resolved.
- **Partial corrections** are flagged, not applied.
- **Image tables.** Detection is a heuristic. It found the known SCE image and vector tables in the 25 read reports, but it can miss others, and it transcribes some tables whose text was already present.
- **Question wording.** The cancellation question still misses reports that describe notified customers without any cancellation word, which is the error the review rule missed.
- **Automatic contradiction candidates** in `contradictions.csv` are not hand-checked. Some are partial counts, such as per-phase totals.
- **Gold labels** are one reviewer's (mine). Uncertain labels are marked `certain = no`.
- **Luna prompt changed.** Its rules for times changed between rounds, so round 1 and 2 Luna accuracy do not carry over directly. The 40 of 40 above is the round 3 number.

## Cost

| Item | USD |
|---|---|
| Jev (775 calls) | 0.147 |
| GPT-6 Luna, full text (155 reports, including one failed oversize attempt) | 1.627 |
| GPT-6 Luna, image transcription (about 430 pages, including calls in flight when the first attempt crashed) | 0.464 |
| Smoke test on 5 read reports before the freeze | 0.045 |
| **Round 3 total** | **2.28** |
| Rounds 1 to 3 total | 2.66 |

That is under the $6 cap.

## Reproduce

```
python research/psps_reports/round3/inventory.py --download
python research/psps_reports/round3/pipeline3.py versions
python research/psps_reports/round3/pipeline3.py extract
python research/psps_reports/round3/pipeline3.py images
python research/psps_reports/round3/pipeline3.py workbooks
python research/psps_reports/round3/pipeline3.py jev
python research/psps_reports/round3/pipeline3.py luna
python research/psps_reports/round3/assemble.py
python research/psps_reports/round3/score_sample.py
```
