# Jev shadow mode

Jev is a TypeSafe System One model. `off` and `shadow` do not change answers, tool calls, caveats, view specs, SSE events, `/health`, or eval scores. `tool_pick` and `tool_pick_template` are the modes that can change the model path.

`AGENT_JEV_MODE=off` is the default. That path never constructs the shadow runner and never imports `typesafe_sdk`.

`AGENT_JEV_MODE=shadow` logs Jev's decisions next to the regex router. A timeout, exception, missing key, missing package, or bad response is a warning plus a log line. The user request does not wait.

`AGENT_JEV_MODE=tool_pick` lets Jev choose the tool on the model path. It is off unless you set it. The LLM still writes the prose. The tool_pick request is the same call the offline `v3_hybrid` ablation sends, including the policy glossary. A context change is scored on that live payload, and the report includes the label and its confidence. If Jev's tool_pick confidence is below `AGENT_JEV_TOOL_PICK_MIN_CONFIDENCE` (default 0.8), or the call times out or errors, the normal LLM tool loop runs. Every decision is a `tool_pick_decision` line in the shadow log with the confidence and `path` `jev` or `qwen` (the historical label for the LLM loop, kept so older logs parse).

`AGENT_JEV_MODE=tool_pick_template` uses that same gate, slot fill, and multi-tool refusal. After the tool succeeds, a template writes the answer for count, records list, map, trend, rank, spatial context, and a single comparison that does not ask why, explain, difference, or reason. Anything else, including an overview, still goes to LLM synthesis.

`verify`, `fallback`, and `route` are reserved names. Setting them aborts startup. They are not implemented.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_JEV_MODE` | `off` | `off`, `shadow`, `tool_pick`, `tool_pick_template`, or `decide` (see `docs/JEV_DECIDE.md`) |
| `AGENT_JEV_DECIDE_MIN_CONFIDENCE` | `0.8` | decide mode: a Jev clarify or refuse wins over the router only at or above this confidence |
| `AGENT_JEV_DECIDE_ANSWER_CONFIDENCE` | `0.9` | decide mode: a Jev answer wins over a router clarify or refuse only at or above this confidence |
| `AGENT_JEV_TOOL_PICK_MIN_CONFIDENCE` | `0.8` | Use Jev's tool only at or above this confidence |
| `AGENT_JEV_BACKEND` | `typesafe` | `typesafe` or `openrouter` (same request body, see `docs/OPENROUTER.md`) |
| `AGENT_JEV_MODEL` | `jev-latest` (`typesafe/jev-1.13-20260917` when the backend is `openrouter`) | Model route. Logs record the concrete version the API returns |
| `AGENT_JEV_TIMEOUT_SECONDS` | `3` | Per call. SDK retries are disabled so this is the whole budget |
| `AGENT_JEV_SAMPLE_RATE` | `1.0` | Fraction of questions sent to Jev |
| `AGENT_JEV_MAX_CONCURRENCY` | `4` | Extra calls are dropped and counted |
| `AGENT_JEV_DAILY_CALL_CAP` | `5000` | Jev API calls per process per UTC day, not questions. One question makes three calls in decide mode and up to four in shadow mode. A question whose calls do not all fit sends none: shadow logs it as `dropped` with reason `daily_cap`, decide falls back to the router with `decision_source.why` = `jev_daily_cap` |
| `AGENT_JEV_LOG_PATH` | `services/agent/logs/jev_shadow.jsonl` | Append-only JSONL. Each write holds a lock on `<path>.lock`, so several uvicorn workers may share one log |
| `AGENT_JEV_LOG_MAX_MB` | `50` | Rotate, keep 5 files |
| `AGENT_JEV_ABLATION` | `v3_hybrid` | Question layout: `v2_full`, `v3_split`, `v3_single`, `v3_no_glossary`, `v3_policy_context`, or `v3_hybrid` |
| `TYPESAFE_API_KEY` | unset | Read by the SDK for the `typesafe` backend. Required at startup when Jev is on with that backend. Never commit it |
| `OPENROUTER_API_KEY` | unset | Required at startup when `AGENT_JEV_BACKEND=openrouter`. Never commit it |
| `AGENT_ALLOW_REMOTE_PROVIDER` | `false` | Must be `true` for any `AGENT_JEV_MODE` other than `off`; startup fails otherwise |

## Startup checks

Any Jev mode other than `off` is checked when settings load, before the service accepts a request:

- `AGENT_ALLOW_REMOTE_PROVIDER=true` must be set, because every Jev backend is remote (`services/agent/SECURITY.md`).
- The active backend's key must be present and not blank: `TYPESAFE_API_KEY` for `typesafe`, `OPENROUTER_API_KEY` for `openrouter`. A missing key used to be found on the first question, which silently disabled Jev while the service looked healthy. Now the service refuses to start, and the error names the variable, never its value.

## Where the key goes

On the EC2 host, put `TYPESAFE_API_KEY` in the systemd environment file for `wildfire-agent` (the same file as the other `AGENT_*` variables), or in AWS Secrets Manager and inject it into that unit. Do not put it in the repo, `.env` that gets committed, or the shadow log. The log writer redacts both backend keys if either ever appears in a line.

## Workers and the log

The log writer is safe across processes. Every write (the rotation check and the append) runs while holding an exclusive lock on `<log path>.lock` (`fcntl.flock` on Linux, `msvcrt.locking` on Windows), so two uvicorn workers cannot rotate the same file at once or interleave partial lines, and `jev_shadow_report --check-parse` stays clean (`test_two_processes_append_and_rotate_without_corrupting_the_log`). The daily call cap and the admitted-request map are per process, so with `--workers N` the total daily spend is N times the cap. Production runs one worker.

Shadow mode keeps a small map of admitted request ids so the outcome record can be matched to its routing record. An entry leaves when the outcome is written; an entry whose request errored or whose streaming client disconnected is evicted after `shadow.ADMITTED_TTL_SECONDS` (15 minutes), and the map never holds more than `shadow.ADMITTED_MAX_SIZE` (10000) entries.

Turning it on: set `AGENT_JEV_MODE=shadow`, provide the key, restart the agent. Turning it off: set `AGENT_JEV_MODE=off` and restart. In-flight shadow calls are abandoned after about 2 seconds on shutdown.

## Logs and reports

Logs live at `AGENT_JEV_LOG_PATH` (gitignored). Each line is a `routing`, `tool_pick`, `outcome`, `dropped`, or `wiring_error` record from shadow mode, or a `tool_pick_decision` record from `tool_pick` and `tool_pick_template`. `routing` and `tool_pick` store the exact request payload and the unmodified raw response.

```bash
python -m services.agent.eval.jev_offline_eval --dry-run --limit 5
python -m services.agent.eval.jev_offline_eval
python -m services.agent.eval.jev_shadow_report --check-parse
python -m services.agent.eval.jev_shadow_report --since 2026-09-21 --min-confidence 0.8 --out report.md
```

`--replay N` resends stored payloads. `--export-payloads N` writes curl commands that use `$TYPESAFE_API_KEY` rather than embedding the key. `jev_offline_eval --reword` takes a JSON file of alternate criteria; `services/agent/eval/jev_reword.example.json` is the example. `--categorized` reads a review CSV whose `category` column is one of `jev_wrong`, `label_wrong`, `wiring_bug`, `wording_issue`.

## Privacy

User questions are sent to TypeSafe's API (`https://api.typesafe.ai/v1/systemone`), or to OpenRouter (`https://openrouter.ai/api/v1/systemone`) when `AGENT_JEV_BACKEND=openrouter`. The warehouse data behind those questions is public CPUC, CAL FIRE, and related records, but the question text itself leaves this infrastructure. Do not enable shadow mode on a host where questions must stay on-box.

The deployed EC2 host runs Python 3.12. `typesafe-sdk` requires Python >= 3.10, so 3.12 is supported. Shadow code stays on 3.10-compatible syntax (`str | None`, no 3.13-only stdlib).

## Follow-ups

- Consider routing "wildfire(s)" plus a county to CAL FIRE instead of refusing with `unexpressable_county_filter`. `routing.py` was not changed. `utility_not_invented_from_place` accepts either that refusal or an answer with dataset `calfire_incidents` and intent `count`.

## Production on EC2

Keep `AGENT_JEV_MODE=off` until a local scored eval with shadow off and shadow on has an empty diff of every scored field. Then:

1. In the agent virtualenv: `pip install "typesafe-sdk>=0.7.1"`.
2. Put `TYPESAFE_API_KEY` and the `AGENT_JEV_*` variables in the agent systemd `EnvironmentFile`, and `chmod 600` that file. Never put the key in the repo.
3. Set `AGENT_JEV_LOG_PATH` to an absolute path on the box, and make that directory writable by the service user.
4. Set `AGENT_JEV_MODE=shadow` and restart the agent unit.
5. Confirm it is working: `tail` the log, then `python -m services.agent.eval.jev_shadow_report --check-parse`.
6. Turn it off immediately by setting `AGENT_JEV_MODE=off` and restarting.
7. If the API key is ever printed, committed, or pasted into a ticket, rotate it in the TypeSafe account and replace the systemd file.

## No-op diff on the EC2 host

The production model is `openai/gpt-6-luna` on OpenRouter, so each runner pass spends API credits (about $0.10 per full pass of the 14 model-path dev cases at the prices in `services/agent/pricing.py`); set a budget before running. `--run-tag` suffixes the artifact directory. Run this from the repo root with the services up:

```bash
cd /home/ubuntu/Wildfire-Services
AGENT_JEV_MODE=off    .venv/bin/python -m services.agent.eval.runner --models openai/gpt-6-luna --run-tag jev-off
AGENT_JEV_MODE=off    .venv/bin/python -m services.agent.eval.runner --models openai/gpt-6-luna --run-tag jev-off-2
AGENT_JEV_MODE=shadow .venv/bin/python -m services.agent.eval.runner --models openai/gpt-6-luna --run-tag jev-shadow
.venv/bin/python -m services.agent.eval.jev_noop_diff jev-off jev-shadow --baseline-tag jev-off-2
.venv/bin/python -m services.agent.eval.jev_shadow_report --log services/agent/logs/jev_shadow.jsonl
```

The shadow report compares Jev's tool_pick with the tools the model emitted, read from each trajectory event, not from `response.tool_calls`. The shadow log is `AGENT_JEV_LOG_PATH`. It is not a file inside the run folder. On this host that file is `services/agent/logs/jev_shadow.jsonl` in `/home/ubuntu/Wildfire-Services`. Pass `--log` when the eval checkout is a different directory. The earlier `jev_vs_qwen` comparison script was removed with the local model path.

A field that differs between off and shadow, and also between the two off runs, is `llm_variance`. Only a difference that appears in the shadow run alone is a shadow effect. The diff drops the timing and id fields (`jev_noop_diff.IGNORE`) at every nesting level on both sides before comparing, and reports nested differences by dotted path (for example `route.rule`), so a timing field present on one side only is never reported. `AGENT_JEV_DAILY_CALL_CAP` counts Jev API calls, not questions (see the table above). `AGENT_JEV_ABLATION` selects the shadow question layout.


## Turning on tool_pick

Set `AGENT_JEV_MODE=tool_pick` and restart. Leave `AGENT_JEV_TOOL_PICK_MIN_CONFIDENCE` at `0.8` unless you want a different gate. Jev returns a tool name. This harness fills arguments from slots the router already resolved:

- `data_query_records` needs a dataset and a year or date range.
- `data_query_spatial` needs one utility plus that time span, or a coordinate pair.
- `visualization_create` needs a dataset, a year or date range, and one explicit kind: a map word, or a trend/time-series word with daily, weekly, or monthly. Both kinds at once, or neither, falls back.
- `comparison_run` needs a named metric plus either two or more utilities and one year, one utility and two years, or both HFTD tiers and one year.
- Label rules I and J: no slot fill is built for a count or chart whose dataset does not cover the named utility (EPSS for a utility other than PG&E; the US sample, which has no utility column, for any utility), or for a comparison whose metric's dataset covers none of the named utilities. Coverage is measured by the loaders and read through the registry (`call_coverage_gap` in `services/agent/coverage.py`, with the fill's period), not a utility name in the template; a fill for a period the dataset has no rows for falls back the same way. Those fall back (the router already clarifies them). A US-sample count with a utility used to drop the utility and count the national sample; it now falls back too. If a fill ever did build one, the executor returns `not_covered` and the answer is a clarification.
- A `comparison_run` fill that would drop a named utility, county, HFTD tier, or month falls back (`comparison_uncarried_constraints`).

A pick of any other tool (`data_query_rank`, `visualization_inspect`, `risk_forecast`) has no slot fill and falls back. An HFTD map needs no year.

A missing required slot falls back to the LLM routing loop. So do a confidence below the threshold, a timeout, an error, a tool outside the candidate list, a failed tool call, and any question that needs more than one primary tool. Eval can still force `multi_intent_count_and_trend` onto the model path. A normal Ask with a dataset and a year runs that rule deterministically: the records count and the time series both, then the template, with no model call. One Jev pick does not answer a two-part question. The LLM still writes the prose when the template does not apply. Shadow mode stays identical to off for anything a user or the eval suite observes, aside from timings and request ids.

## detect_partial_200 tool_pick tie

`detect_partial_200` is the model-path question "Map the 2024 US ignition sample." Gold tool is `visualization_create`. On `jev-1.13.0` the tool_pick call sits near a tie with `clarify`. A wording probe on 2026-09-22, five repeats, same candidate set (`visualization_create` only), mean `visualization_create` probability:

| Wording | Mean visualization_create |
|---|---:|
| Map the 2024 US ignition sample. | 0.54 |
| Map the US ignition sample for 2024. | 0.55 |
| Map the 2024 US ignitions. | 0.68 |
| Map US ignitions for 2024. | 0.76 |

The word "sample" costs 14 points when the year stays in front (0.54 to 0.68) and 21 points when the year stays at the end (0.55 to 0.76). Year position does not matter: the two sample wordings are 0.54 and 0.55.

In `services/agent/eval/runs/jev_hybrid_raw_20260922T204748Z.json` (a local run file, not committed), 12 of 14 model-path questions have a minimum top-two gap from 0.28 to 0.95. This case stays at 0.01 to 0.07. Confidence on the original wording was 0.25 to 0.37, so the default 0.8 gate falls back to the LLM tool loop (`below_threshold`).

The glossary line in the tool_pick context says "us_ignitions is an all-cause sample, not a census." That line may be priming the clarify option when the question also says "sample." Question text and policy were left unchanged.

## Not implemented

Later phases, not built here:

- `verify`: check the model-path tool pick before it runs
- `fallback`: use Jev when the local model fails
- `route`: reserved name. Use `tool_pick` instead.
