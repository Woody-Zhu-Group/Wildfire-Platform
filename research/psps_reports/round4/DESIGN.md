# Round 4: model-assisted review of the round 3 queue

> **Note (2026-09-29):** the "hand-checked" labels this design relies on were written by a Claude Opus 5.5 session, not by a person (`../round5/AUDIT_PLAN.md`). The design text below is unchanged, except that "two independent models" now reads "two different models" (2026-09-29).

Status: design, committed before any round 4 code or model call.

## Why

Round 3 flagged 286 items across 145 events for human review (`round3/review_queue.csv`). This round replaces the two human reviewers with two different models (one of them, Claude Opus 5.5, also wrote the old gold labels). Each model answers every flagged item from the report itself, code checks their evidence, and items where they agree are accepted.

The method is measured once, on reports that already have hand-checked labels, before it touches the queue. No new human labeling is part of this round.

## What this changes in our claims

- Values settled this way are labeled `model_review_agreed`, not human-reviewed.
- The memo states: "model-reviewed; accuracy measured on N hand-labeled values from 25 reports; no human audit of the reviewed items."
- `round3/review_reviewer_A.csv` and `review_reviewer_B.csv` are not used and stay unchanged.

## The two reviewers

| | Model | Access |
|---|---|---|
| Reviewer 1 | Claude Opus 5.5 | Claude Code in headless mode (`claude -p --model opus`) on the Claude subscription, not an API. The Claude Code version and model are recorded in `freeze.json`. |
| Reviewer 2 | GPT-6 Sol (`openai/gpt-6-sol`) | OpenRouter |

They come from different model families, so their errors are less likely to line up. GPT-6 Luna is not used, because it extracted the round 3 numbers.

Each reviewer runs on its own and never sees:

- the other reviewer's output,
- Jev's answer or confidence (the queue's `answer` and `confidence` columns are dropped before anything is shown to a reviewer),
- `round3/review_answer_key.csv`,
- any gold label.

For Reviewer 2 this is enforced by building its prompts in code. For Reviewer 1 it is enforced by the packet setup below, because a Claude Code session can read files.

**The session that builds this round never answers items.** It reads gold labels while building `test_gold.csv`, so every Reviewer 1 answer comes from a fresh headless session started in a packet folder.

## How a reviewer works

- **One session per report**, answering all of that report's queue items, as the human guide does.
- **Instructions:** the "The fields", "Item types", and "Amendments and contradictions" sections of `round3/REVIEW_GUIDE.md`, verbatim, plus each item's `question` and `options`. These are the same label rules a human reviewer would use.
- **Reviewer 1 (Claude Code packets):**
  - For each report, code builds a packet folder outside the repository (for example under the system temp directory). It holds only:
    - `items.json`: the report's queue items, without `answer` or `confidence`,
    - `instructions.md`: the review guide sections above,
    - `pages/NNN.txt`: the text layer of every page,
    - `pages/NNN_transcription.txt`: the stored transcription, for pages in `round3/runs/images.jsonl`,
    - `pages/NNN.png`: rendered images of image-only pages, pages with little or scrambled text, and every page in the items' `page_refs`,
    - `current_values.json`: only for the special items below.
  - A fresh session runs in the packet folder: `claude -p --model opus --allowedTools "Read Grep Glob Write" --max-turns N`, with Write limited to `answers.json` in that folder. There is no Bash, no web access, and no access to the repository.
  - Before the development runs, a check confirms that a session in a packet folder cannot read a file outside it. If it can, stop and fix the setup before any run.
- **Reviewer 2 (Sol, tools in code):**
  - `search(query)`: pages containing the text, with a short snippet.
  - `read_page(n)`: the page's text layer, plus the stored transcription for pages in `round3/runs/images.jsonl`.
  - `view_page(n)`: the rendered page as an image, for image-only tables and scrambled text.
- **Limits:** a turn or tool-call limit per session for each reviewer, set during development and frozen.
- **Output per item** (strict JSON schema): `item`, `value`, `pages`, `quote` (verbatim from the report), `note`, `unsure` (true or false).
  - `value` is one of the item's `options`, or `no change` / `field=value; field=value` for the special items below.
  - For a `not_stated` answer, `quote` is empty; the reviewer instead records the pages it read and the search terms it used.
- **Special items** (28: partial corrections, redlines, unreadable table pages, and workbook-versus-PDF times): the reviewer also receives the event's current values from `round3/dataset.csv`, as the human guide allows. These items are not blind by nature.

## Code checks

An answer is valid only if all three hold:

1. `value` is an allowed value, or well-formed correction syntax.
2. `quote` appears on a cited page after normalizing whitespace, case, and ligatures, in either the text layer or the stored transcription. An answer read only from a rendered image, with no text layer or transcription to check against, is marked `unverified_visual` and is not valid.
3. `unsure` is false.

An invalid answer counts as a disagreement.

## Decision rule

- **Both answers valid and identical:** accepted, with `review_method = model_review_agreed`.
- **Anything else:** `unresolved`. The dataset keeps the pipeline value, adds the flag `model_review_disagreement`, and records both answers. There is no tie-break model.
- **Contradictions:** `CONTRADICTION:` notes from either reviewer are appended to `round3/contradictions.csv`, marked `model-found, unchecked`.

## Development and test sets

**Development (tune freely):** the 10 round 1 pilot reports (`gold_labels.csv`). 13 of their items are in the queue. Round 1 labels use older wind values, so map `true` to `met` and `false` to `not_stated`.

**Test (run once, after the freeze):** the 25 clean reports from round 2 (`round2/gold_new15.csv`, 15 reports) and round 3 (`round3/gold_sample10.csv`, 10 reports), 225 hand-checked values.

- 35 of the queue's categorical items fall on these reports and already have a gold label (19 from round 2, 16 from round 3). This is the closest match to the queue, so it gets its own bar. The queue run reuses these test answers instead of asking again.
- On the test reports, reviewers answer all nine fields as queue-style items: the five categorical questions with the queue's `question` and `options` text, and the four numbers with the rules in the guide's "Numbers" table.

**Gold conversion:** round 2 labels were written under round 2 rules. Before the freeze:

1. List every label-rule difference between round 2 and round 3 (`round2/README.md` against `round3/README.md` and `round3/REVIEW_GUIDE.md`).
2. Apply only mechanical mappings. For example, MBL `not_stated` becomes `not_applicable` where the gold `customers_deenergized` is 0.
3. Exclude and list any gold row touched by a difference that is not mechanical.
4. Exclude and list any round 2 report whose document differs from the file round 3 used (an amendment replaced the original).
5. Write the result to `test_gold.csv`, committed with the freeze.

**Scoring** uses the comparison rules in `round3/score_sample.py`: exact category, exact number, time to the minute.

## Pass bar (fixed before the test run)

Accuracy of agreed answers:

1. On the queue-matched test items (35 before exclusions): at least 90 percent correct.
2. On all test values (225 minus exclusions): at least 95 percent correct.

Both must pass. Gold rows marked uncertain count toward the bar.

Also reported, with no bar: the agreement rate, the share of valid answers, accuracy on gold rows marked certain, Wilson 95 percent intervals, and a breakdown by field and by utility.

**If either bar fails:** stop. Nothing is applied to the dataset, `calibration_results.md` records the failure, and the queue stays as it is. There is no second test run on the same gold.

## Cost

- **Reviewer 1** runs on the Claude subscription, so there is no dollar cost. Record the number of sessions and turns per report. If a usage limit pauses the run, resume it later with nothing changed, and note the pause in `runs/spend.json`.
- **Reviewer 2** runs on OpenRouter. Measure its cost on 2 development reports, project the development, test, and queue runs, set a hard cap at 1.5 times the projection, and record it in `freeze.json` before the test run. The run stops at the cap and records where it stopped.

## Order of work

1. Commit this design on its own.
2. Build the packet builder, the Sol tools, and the checks. Confirm the packet isolation check passes. Iterate on the development set only.
3. **Freeze:** write `freeze.json` (Claude Code version and model, Sol model id, prompts, allowed tools, turn and tool limits, Sol cost cap, code commit), `test_gold.csv`, and the exclusion list. Commit and push before any test-set call.
4. Run the test once. Score it. Commit `calibration_results.md` and the runs.
5. If it passes, run the queue, apply the decisions, and commit.
6. Update `FINDINGS.md`, add a pointer in `round3/README.md`, and update the PR description.

## Outputs (`research/psps_reports/round4/`)

| File | Contents |
|---|---|
| `DESIGN.md` | This document |
| `freeze.json` | Everything fixed before the test run |
| `test_gold.csv` | Converted test labels, with exclusions listed |
| `packets.py`, `run_claude.py`, `run_sol.py`, `verify.py`, `score.py`, `apply.py` | Code |
| `runs/*.jsonl`, `runs/spend.json` | Raw reviewer outputs and spend |
| `calibration_results.md` | Test results against the pass bar |
| `reviewed_queue.csv` | Every queue item with both answers, validity, and status |
| `dataset_reviewed.csv` | `round3/dataset.csv` with decisions applied and a per-field `review_method`: `unflagged`, `model_review_agreed`, or `unresolved` |
| `README.md` | How to rerun and what the results mean |

## Known limits

- All gold labels come from one labeler.
- The 35 queue-matched items are a small sample, so the 90 percent bar has a wide interval.
- The special items have no direct gold labels; the test's numeric fields cover the reading they depend on.
- Items that can be read only from an image may end up unresolved.

## Changes before the freeze

Decided on 2026-09-28, after the design was committed and before any round 4 code or model call. Each one fixes a part of the design that could not work as written.

1. **Partial corrections (17 items).** The packet gets the correction letter's pages in their own folder (`correction/NNN.txt`, plus `correction/NNN.png` for image-only or scrambled pages). Sol's `search`, `read_page`, and `view_page` take a `document` argument (`report` or `correction`). The quote check looks for the quote on the cited pages of the document each page is cited from. For these items, an answer is valid only if at least one cited page is from the correction letter.
2. **Redlines (3 items).** These are sent straight to `unresolved` without asking either reviewer. The text layer cannot separate struck from inserted text, so no answer could pass the code checks honestly.
3. **Defaults accepted:**
   - `not_stated` and `null` answers skip the quote check. They are valid only if they record the pages read and the search terms used.
   - Round 2 MBL gold becomes `not_applicable` wherever the gold `customers_deenergized` is 0.
   - `r2_pge_2017_2019` is excluded from the test. Round 3 used the amended PDF, which differs from the file round 2 labeled.
   - The two workbook-versus-PDF time items are asked as designed. Workbooks are not in the packet, so an answer that picks the workbook time cannot pass the quote check and the item ends `unresolved`.
4. **Packet isolation setup.** Reviewer 1 sessions run with only the Read, Grep, Glob, and Write tools available, file access limited to the packet folder, no MCP servers, no user or project settings, and permission mode `dontAsk`. The exact command is recorded in `freeze.json`.

**Updated test counts before rule-difference exclusions:** 216 values (225 minus the 9 on `r2_pge_2017_2019`) and 34 queue-matched items (35 minus item 262).

**Spend controls for Sol:**
- One minimal Sol call runs before the first development run. A 402 or any credit error stops the round.
- The OpenRouter allowance is $6.92. If the projection (development plus test plus queue, times 1.5) is above it, the round stops before any more Sol calls. The allowance is recorded in `freeze.json`.

**Added after the first list (same day, still before any round 4 code or model call):**

5. **Special items get their own session where they would expose Jev's answers.** The categorical values in `round3/dataset.csv` are Jev's answers, so `current_values.json` must never share a packet with blind items. On the 20 reports that have both (18 in the queue run, 2 in development), each reviewer runs two sessions: one packet with the blind items and no current values, and a second packet with only the special items and `current_values.json`. Every other report keeps one session.
6. **Smaller additions:**
   - The answer format gets a `search_terms` field, where `not_stated` and `null` answers record the searches they ran.
   - When an event has two correction letters (items 4 and 7), they go in `correction/` and `correction2/`, and Sol's `document` argument takes the same names.
   - The quote check removes all whitespace, rather than collapsing it, so a quote still matches across a line-break hyphen ("de-\nenergizing").
   - Categorical test items that are not in the queue get the pages Jev was shown as `page_refs`, read only from the page lists in `round3/runs/jev.jsonl`. Numeric test items get no `page_refs`.
   - Development also runs the 3 special items on the round 1 reports (items 9, 21, and 25).
