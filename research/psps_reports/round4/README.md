# Round 4: model-assisted review of the round 3 queue

Work in progress. `DESIGN.md` is the plan, including the changes decided before the freeze. This README is completed when the round ends.

## Code

| File | What it does |
|---|---|
| `packets.py` | Builds the item sets (dev, test, queue) and writes each session's packet folder outside the repository. |
| `run_claude.py` | Reviewer 1: the packet isolation check, then one fresh headless Claude Code session per packet. |
| `run_sol.py` | Reviewer 2: GPT-6 Sol on OpenRouter with `search`, `read_page`, and `view_page` over the same packet, plus the spend ledger. |
| `verify.py` | The code checks on each answer and the agreement rule. |

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
