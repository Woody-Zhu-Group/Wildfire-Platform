# Round 4: model-assisted review of the round 3 queue

Two models, Claude Opus 5.5 and GPT-6 Sol, each answered the round 3 review queue on their own, from the report itself. Code checked their evidence, and items where they agreed were accepted. The method was tested once, on 211 hand-labeled values from 24 clean reports, before it touched the queue. It passed both bars. `DESIGN.md` is the plan, including the changes decided before the freeze.

**Values settled here are model-reviewed, not human-reviewed.** Accuracy was measured on 211 hand-labeled values from 24 reports; no human audit of the reviewed items. All gold labels come from one labeler.

## Results

**Test** (`calibration_results.md`; clean set, run once after the freeze):

| Bar | Needed | Result (Wilson 95% interval) | Pass |
|---|---|---|---|
| Queue-matched items | at least 90% | 27/29 agreed answers right (93.1%, 78.0 to 98.1) | yes |
| All test values | at least 95% | 177/179 agreed answers right (98.9%, 96.0 to 99.7) | yes |

- **Agreement:** the reviewers agreed on 179 of 211 test values (84.8%), and on 29 of 32 queue-matched ones.
- **The two agreed errors** are both SDG&E MBL items that the gold says were `all_notified`.
- **Opus's null times:** Opus wrote 18 unstated times as a JSON `null` rather than the string `"null"`. The frozen check counts those as invalid, which lowers agreement but not accuracy.

**Queue** (`reviewed_queue.csv`, `dataset_reviewed.csv`):

- **206 of 286 items accepted** (`model_review_agreed`):
  - 175 from the queue run,
  - 31 reused from the test run, since they ask the same question on the same report.
- **80 unresolved:**
  - 62 where a reviewer marked the item unsure (60 of them Opus),
  - 9 read only from an image,
  - 5 where both answers were valid but different,
  - 3 redlines, which were not asked,
  - 1 correction that listed fields outside the correction syntax (item 16).

  In 59 of the 80, the two reviewers gave the same value.
- **Dataset fields after model review (155 events × 9 fields):** 962 unflagged, 304 `model_review_agreed`, 129 `unresolved`. 82 values changed:
  - 32 `NO_MATCHING_PAGE` placeholders now have an answer.
  - Wind moved from `met` or `not_met` to `not_stated` 13 times.
  - Claims moved from `zero` to `not_stated` 11 times.
  - The SDG&E Jan 20 to 24, 2025 last restoration is now the PDF's 15:48 (item 22), and its duration was recomputed.
- **Hand labels** (`hand_labels.py`, added after the test on 2026-09-28): every field with a hand label now carries it, so known errors do not stay in the dataset.
  - The labels come from two sources:
    - the kept rows of `test_gold.csv`;
    - the round 1 gold, converted to round 3 rules the same way as round 2 (`round1_gold_converted.csv`, rule list in `round1_rule_differences.json`).
  - Excluded gold rows and `r2_pge_2017_2019` are skipped.
  - **Round 1 conversion:** 63 of 90 rows are kept and 27 excluded.
    - All 20 time rows are excluded (R3). Round 1 took the circuit table's minimum and maximum, while round 3 needs a table that lists every circuit.
    - The 5 wind `false` rows are excluded (R1), since they could be `not_met` or `not_stated`.
    - 2 wind `true` rows are excluded (R2) because they rest on an implication, or don't tie the threshold to de-energized areas.
    - The only mechanical mapping is wind `true` to `met` (3 rows).
  - The previous value is kept in `<field>_value_before_hand_label`, and the field's round 4 disagreement flag is removed.
  - A gold `null` does not replace a time taken from a utility workbook, since the gold was written from the PDF alone. This keeps 4 workbook times (SCE Jan 20 2025, first and last; SDG&E Dec 9 2024, first; SDG&E Jan 20 2025, first).
  - A gold `a|b` keeps the current value when it is one of the alternatives.
  - **PG&E Oct 21 2020 first de-energization.** Its round 1 label (17:33) was dropped by the conversion. The report states both 14:42 (a transmission line, p75) and 17:33 (the earliest distribution circuit in Appendix A, p73). The field keeps 14:42, is `unresolved` with the flag `first_deenergization:contradiction_in_report`, and is listed in `round3/contradictions.csv` as "round 4 hand-label check".
  - Hand labels changed 8 values, all from `test_gold.csv`, including the two agreed test errors (items 117 and 141, SDG&E MBL, now `all_notified`).
  - **Final dataset fields:** 761 unflagged, 270 `hand_labeled`, 257 `model_review_agreed`, 107 `unresolved`. 87 values differ from `round3/dataset.csv`.
  - The test scoring and `calibration_results.md` are unchanged.
- **Contradictions:** 16 `CONTRADICTION:` notes were appended to `round3/contradictions.csv`, marked "round 4 model-found, unchecked".

**Effort:**
- 199 Opus sessions (5 isolation checks, 24 development, 24 test, 146 queue) on the Claude subscription, with no usage-limit pause.
- Sol cost $10.39 on OpenRouter: $1.91 development (including the ping), $1.82 test, $6.66 queue. The cap was $20.08. Details are in `runs/spend.json`.

## What the results mean

- An agreed answer on a queue-style item was right about 93 percent of the time on the closest test match. The interval runs from 78 to 98 percent, because only 29 items back it.
- Across all test values, agreed answers were right about 99 percent of the time.
- The weak spot is the same as in round 3: MBL notification. Both agreed errors are MBL items, and MBL has the most unresolved items (35 of 91).
- Unresolved items keep the pipeline value, carry the flag `<field>:model_review_disagreement`, and record both answers in `model_review_unresolved`. They still need a human.

## Rerun

```
python research/psps_reports/round4/run_claude.py isolation
python research/psps_reports/round4/run_claude.py run --set dev --tag _vN   # development only
python research/psps_reports/round4/run_sol.py ping
python research/psps_reports/round4/run_sol.py run --set dev --tag _vN
python research/psps_reports/round4/score.py dev --tag _vN
python research/psps_reports/round4/score.py gold
python research/psps_reports/round4/score.py freeze        # after committing the code
python research/psps_reports/round4/run_claude.py run --set test
python research/psps_reports/round4/run_sol.py run --set test
python research/psps_reports/round4/score.py test
python research/psps_reports/round4/run_claude.py run --set queue
python research/psps_reports/round4/run_sol.py run --set queue
python research/psps_reports/round4/apply.py
python research/psps_reports/round4/hand_labels.py   # always right after apply.py
```

The test and queue commands use the frozen limits and refuse to run if a code file or the Claude Code version changed. A rerun resumes: sessions already in `runs/*.jsonl` are skipped. The test was run once and must not be rerun on the same gold.

## Code

| File | What it does |
|---|---|
| `packets.py` | Builds the item sets (dev, test, queue) and writes each session's packet folder outside the repository. |
| `run_claude.py` | Reviewer 1: the packet isolation check, then one fresh headless Claude Code session per packet. |
| `run_sol.py` | Reviewer 2: GPT-6 Sol on OpenRouter with `search`, `read_page`, and `view_page` over the same packet, plus the spend ledger. |
| `verify.py` | The code checks on each answer and the agreement rule. |
| `score.py` | Gold conversion (`test_gold.csv`, `test_exclusions.csv`), development scoring, the freeze, and the test scoring. |
| `apply.py` | Applies the decisions: `reviewed_queue.csv`, `dataset_reviewed.csv`, and the contradiction notes. |
| `hand_labels.py` | Converts the round 1 gold to round 3 rules (`round1_gold_converted.csv`, `round1_rule_differences.json`), then puts the hand labels into `dataset_reviewed.csv` after `apply.py` and records the previous values. |

## Packet isolation check

`python research/psps_reports/round4/run_claude.py isolation` starts a fresh session in a test packet and asks it to read, search, list, and write outside the folder, then checks the transcript and the disk. The result is in `runs/isolation_check.json`. It passed only after three fixes:

1. **Prompt on stdin.** On Windows, `claude` is an npm `claude.CMD` shim, and a multi-line prompt passed as an argument reached the session cut to its first line. Prompts now go in on stdin.
2. **An Edit rule on the absolute path.** `Write(./answers.json)` never matched, so every write was denied. File-writing permissions are `Edit(...)` rules, and Claude Code matches them against the POSIX form of the Windows path, so the rule is `Edit(//c/.../<packet>/answers.json)`.
3. **Neutral wording.** A first version framed as an access test made Opus decline every step, which tests the model's manners, not the permission layer. The check now asks for ordinary file steps, and it fails unless the transcript shows each outside read and write was actually attempted.

The command that passed: `claude -p --model opus --tools Read,Grep,Glob,Write --allowedTools "Read(./**)" "Grep(./**)" "Glob(./**)" "Edit(//c/.../answers.json)" --permission-mode dontAsk --restricted --safe-mode --strict-mcp-config --setting-sources local --no-session-persistence --max-turns N --output-format stream-json --verbose`. `--restricted` confines the file tools to the packet folder.

## Freeze

`freeze.json` fixes everything before the test run: Claude Code 2.1.281 with `claude-opus-5-5` (alias `opus`), the session command, prompt, tools, and limits; Sol (`openai/gpt-6-sol`) with its tools, answer schema, and limits; both reviewers' instructions (text and hash); the packet and check settings; the gold hashes; the pass bars; and the code commit with a hash of every code file. The test and queue runs refuse to start when a code file no longer matches its frozen hash or the installed Claude Code version differs.

**Sol spend limits:** the cap is $20.08, 1.5 times the projection of $13.39, which is $1.91 spent in development plus $1.97 for the test and $9.51 for the queue. The OpenRouter allowance is $25. The run stops before any call once total Sol spend, development included, reaches the cap.

## Test gold (`test_gold.csv`, `test_exclusions.csv`)

225 rows: 135 from round 2 and 90 from round 3. 211 are kept and 14 are excluded, which leaves 32 queue-matched items. The round 2 labels were converted as follows:

| Difference (round 2 vs round 3) | Handling |
|---|---|
| D1: MBL gains `not_applicable` when nobody was de-energized | Mechanical: 2 rows (SCE Aug 2019, SDG&E Jan 2021) change from `all_notified` to `not_applicable` |
| D2: SCE 2019-2020 critical-care substitution | Excluded: SCE Oct 2020 MBL |
| D3: no advance notice to shut-off customers means `some_not_notified` | Excluded: SDG&E Oct 2018 MBL |
| D4: a notified-versus-de-energized gap means cancellation `yes` | Excluded: PG&E Jan 13 2025 cancellation (gold `no`) |
| D5: time rules (no "event began", notification, or status times) | Excluded: SCE Oct 2023 first de-energization, SDG&E Jan 2025 last restoration |
| D6: PG&E composite scores are not wind statements | No row changes |
| D7: "Not applicable" and deferred counts are `not_stated` | No row changes |
| D8: two totals, use the summary-table one | No row changes: the gold convention `a|b` is the same as in round 3's own gold |
| D9: round 3 uses the latest full amendment | Excluded: all 9 rows of `r2_pge_2017_2019` |

## Implementation choices

These are decisions I made during the build that DESIGN.md leaves open.

- **Packets:** written to `<system temp>/psps_r4/<set>/<reviewer>/<session>/`. The build manifest sits next to the folder, never inside it. PNGs are rendered at 150 dpi for:
  - every page listed in `round3/runs/images.jsonl` (any status),
  - pages with fewer than 200 non-whitespace characters or scrambled text (`common.is_garbled`),
  - every page in an item's `page_refs`.
- **Items:** a reviewer sees only `item`, `reason`, `field`, `question`, `options`, `page_refs`, and `detail`, and the builder fails if any other key appears.
  - On development and test reports, the item ids are the field names.
  - Categorical items not in the queue have an empty `reason`.
  - Number items get a short question and a format line.
- **current_values.json:** the nine fields, the county names, the duration, and the version. It has no confidences and no sources.
- **Special items on test reports** (items 4, 5, 22, 26) run in queue special sessions after the test. Item 262 is on the excluded report, so it runs in the queue.
- **Opus:**
  - 80-turn limit, 1-hour timeout, 3 sessions at a time.
  - API keys and parent-session variables are removed from the child environment, and `DISABLE_AUTOUPDATER=1` is set.
  - A usage-limit stop pauses the run and records the pause in `runs/spend.json`.
- **Sol:**
  - Reasoning effort `medium`, a 50-tool-call limit, and at most 30 rounds.
  - `search` returns at most 30 pages, each with a snippet of about 200 characters. `view_page` serves only the pages that have a PNG in the packet, the same images Opus gets.
  - The final answer is forced into a strict JSON schema.
- **Pages** are cited as `"9"` for a report page and `"correction:3"` or `"correction2:1"` for a correction letter.
- **Canonical values:** commas are dropped from numbers, and `null`, `none`, and empty all read as null. Corrections are sorted by field before two answers are compared.
- **Development tuning** (on round 1 reports only):
  - The quote instruction now asks for character-for-character copying, including stray footnote numbers.
  - Sol's tool limit went from 40 to 50.
  - Both passes are in `runs/*_dev_v1.jsonl` and `runs/*_dev_v2.jsonl`.
- **Apply rules** (`apply.py` docstring):
  - A field is `unresolved` if any item covering it is unresolved. It is also `unresolved` if two agreed items give it different values.
  - Corrections are applied only to fields that are not unresolved.
  - The event duration is recomputed from the new times.
  - `CONTRADICTION:` notes are taken only from the answers applied to queue items.
