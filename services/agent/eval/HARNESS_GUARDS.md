# Harness guard fixes (2026-08-10 / 2026-08-11)

> Historical record, 2026-09-24: dated record of the August 2026 fixes and runs on the local Ollama `qwen3:4b` path; the results below are unchanged. That path, `AGENT_STRUCTURED_MODE`, and the thinking cells were removed when production switched to OpenRouter (GPT-6 Luna); see `docs/OPENROUTER.md`. Since these runs, `eval/cases.json` has 107 cases, not 57, and a risk map or surface question with a date and no place routes to the router-only `risk_surface` tool instead of clarifying (`services/agent/routing.py`).

## Bugs fixed

1. **Relative dates**: `services/agent/time_resolve.py` owns all relative/explicit year resolution. Unambiguous forms (`N years ago`, `last/this year`, `in the last N years`) resolve against `date.today()`; vague forms (`recent`, `lately`, `currently`) clarify. A named calendar day (`2024-08-15`, `August 15th 2024`) is that day, not the month or year. When the harness has resolved year(s), mismatched model years are **overridden**; invented years are rejected only when the harness has none (`year_not_derived`). Out-of-coverage years still fail loudly. Modeled-risk questions (`risky` / `ignition risk`) route to `risk_forecast` or refuse; they must not be answered with a CPUC/CAL FIRE count.
2. **Grounding**: root cause: `_unsupported_numbers` always allowed `{"0","1","2","3","100"}` and scanned full summary JSON (including `limit`/`returned`). Now curated evidence numbers + `_quantity_mismatches` against tool totals.
3. **Vague spatial scope**: `near`/`around`/`close to` without radius/coords → `undefined_spatial_scope` clarification.
4. **Retry bound**: any identical non-transient tool-failure fingerprint twice blocks that tool (not only `invalid_arguments`). `max_tool_steps` only caps model turns.
5. **Silent filter drop**: deterministic rules refuse to answer when the question names county/month (or other) constraints the tool call cannot express. CPUC ignitions now have a load-time spatially inferred `county` column (Census TIGER PIP). US ignitions still have no county column (coverage gap → unsupported). CAL FIRE/EPSS/PSPS/CPUC can take `county=`. Month names become `start_date`/`end_date` windows.
6. **Utility grounding**: utility filters must appear in the question/slots; place names (Sacramento) are never coerced to an IOU.
7. **Network errors**: frontend humanizes bare `Failed to fetch` / network disconnects into an actionable agent-port/CORS message.
8. **Synthesis prose**: synthesis must return a full sentence (dataset/year/scope); bare-number answers are rewritten via the deterministic renderer. Count prose includes scope/year.
9. **Path-independent caveats**: `collect_qualifications` runs once in `ask()` after any successful primary tools (det, model, synthesis fallback, slot-grounded rescue). Model synthesis consumes those quals; it does not own a second attachment path. `cpuc_utility_caused` attaches on every successful CPUC ignition read. CPUC↔US compare questions companion-fetch the missing dataset so `us_ignitions_sample` does not depend on a second primary tool call. `calfire_map_feed_counts` attaches when a CAL FIRE answer spans 2023–2024 or compares CAL FIRE **counts** across years (map feed ≠ Redbook; 133→611 is posting, not occurrence; acre comparisons remain valid). Single-year CAL FIRE counts do not get it. Qualification-call metadata fetches are ignored when deciding the span. `cnhpp_grid_resolution` and `cnhpp_contagion_tie` attach on every successful `risk_forecast`; `cnhpp_cell_461` only when cell 461 is in the scored set. Do not add these to 2023/2024-only CAL FIRE eval `required_caveats`.
10. **Audit trail args**: SSE `tool_call` / logs show post-normalize executed arguments (harness year fill), with raw model args retained as `requested_arguments`.
11. **Comparison trail labels**: frontend reads `row.key`.
12. **Spatial prose**: region dict rendered as `SCE territory`, not Python repr.
13. **Qualifications**: UI places them below the answer; answer text no longer embeds the qualification tail.
14. **Audit slots**: `route.slot_resolution` exposes harness-resolved years/ranges.
15. **Context window**: agent alias / requests use `num_ctx=32768`; startup logs `effective_num_ctx`. Ollama `/api/ps` `context_length` reflects the *loaded* request options (can read 4096 if a process loaded without `options.num_ctx`); it is not an independent Modelfile truth.
16. **Synthesis hang (Sacramento County 2024)**: root cause was **unbounded wait on prompt-mode synthesis**, not a 4096 context window. Repro still reaches tools successfully (`calfire_incidents` count=11) then, under default `structured_mode=prompt`, synthesis does not return within `AGENT_SYNTHESIS_TIMEOUT_SECONDS` (180). Before the timeout existed, the HTTP client could wait up to `AGENT_TIMEOUT_SECONDS` (900). What ends the user-visible hang is **timeout + `synthesis_fallback_to_tool_summary`**, not the prose prompt and not num_ctx. Under `structured_mode=constrained`, synthesis tokens return (~15–25s/turn) but grounding retries can still exhaust and fall back.
17. **Synthesis quality (2026-08-11)**: grounding allows numbers from evidence JSON **and caveat text** (prompt says both; allow-list must match). Comma forms like `1,234` normalize to `1234`. Synthesis prompt slims exploratory record samples (names/labels only; no `event_date` day tokens). When numbers already ground but claims/evidence_ids are missing or wrong, harness emits `citation_repaired` instead of exhausting retries into `synthesis_fallback`. Eval cases `model_cpuc_tell_me_about_2023` and `model_sacramento_tell_me_about_2024` assert `expected_answer_origin=model` (not just status=answer). Confirmed run `synth-origin-confirm`: both origin=model, fallback_rate=0.
18. **Derived arithmetic (2026-09-24)**: a question that asked how much counts changed could not get the change, because grounding allows only evidence numbers. `services/agent/derived.py` now adds a `harness_arithmetic` evidence item (difference, percent change, ratio as asked, each with `source_evidence_ids`) before synthesis; the model cites it and never computes. A model-computed change that is not derived is still a `grounding_error`. Same PR: every count gets a stat card (the global cap of three dropped the fourth), and the `ignition_definition_<utility>` caveat lists every period checked instead of only the first.

## County / month support (CPUC vs CAL FIRE)

| Dataset | County filter | Month filter |
|---|---|---|
| CPUC ignitions | **Yes** (`county=` from lat/lon PIP) | Yes via `start_date`/`end_date` |
| US ignitions | **No** | Yes via dates |
| CAL FIRE | **Yes** (`county=`) | Yes via dates |
| EPSS / PSPS | Yes | Yes via dates |

Silent statewide answers for county/month questions are a harness bug; the router must unsupported/clarify instead.

## Eval (2026-08-11)

Run: `qwen3-4b__thinking-off__constrained__filter-drop-synth-merged` (57 cases; full run + repair of failed/new cases).

| | Result |
|---|---|
| End-to-end | **55/57 (96.5%)** |
| Status | **100%** |
| Caveats | **98.2%** |
| Recovery | **100%** |
| `effective_num_ctx` | **32768** (Ollama `/api/ps` context_length=32768; not 4096) |

Critical new cases **passed**: silent Sacramento/August CPUC filter-drop, CAL FIRE Sacramento August, Sacramento≠IOU unsupported, model “Tell me about CPUC 2023” (prose + `cpuc_utility_caused` + year in executed args).

Remaining failures (model-tier flakes, not harness regressions):
- `cpuc_vs_us`: model emitted one CPUC count, skipped the US sample companion call.
- `count_plus_trend`: model emitted an extra duplicate records call after a bad first args attempt (tools sequence mismatch).

### Flake quantification (2026-08-11, 10 trials each, `thinking=off` / `constrained`, seed=42)

| Case | Failures | Failure rate | Mode |
|---|---|---|---|
| `cpuc_vs_us` | **10/10** | **100%** | Always one `data_query_records` (CPUC only); skips US companion → missing `us_ignitions_sample` |
| `count_plus_trend` | **0/10** | **0%** | All passed in this probe (prior suite still saw a duplicate-call miss) |

Artifact: `services/agent/eval/flake_probe_results.json`.

### `cpuc_vs_us` diagnosis (2026-08-11)

| Condition | Result (5 trials) |
|---|---|
| Baseline question | **5/5** emit exactly **one** tool call (`cpuc_ignitions`), then harness synthesizes (`single_then_synth=100%`). Does not attempt US and fail; never plans both. |
| Explicit “emit BOTH datasets” hint | **5/5** emit **two** calls in the first turn (intent fixed by prompting), but **0/5** succeed: both fail schema (`county="null"`, invented `tier`). |

Conclusion for PI: multi-tool **intent** is promptable; reliable dual **success** is not on 4B+Ollama (no `tool_choice`). Artifact: `services/agent/eval/cpuc_vs_us_diagnosis.json`.

### Full suite after synthesis fix (`synthesis-quality-20260811`)

| Metric | Value |
|---|---|
| End-to-end | **55/57 (96.5%)** |
| Model completed / harness rescued | **8/11 (72.7%)** / **3/11 (27.3%)** |
| Failures | `cpuc_vs_us`, `collision_wrong_kind_model_repair` |

Config flag: `AGENT_DISABLE_DETERMINISTIC_ROUTING` / eval `--disable-deterministic` (default **off**).

Architecture writeup: [`ROUTING_EXPERIMENT.md`](ROUTING_EXPERIMENT.md).
