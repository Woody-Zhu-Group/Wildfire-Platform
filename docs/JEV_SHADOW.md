# Jev shadow mode

Jev is a TypeSafe System One model. `off` and `shadow` do not change answers, tool calls, caveats, view specs, SSE events, `/health`, or eval scores. `tool_pick` and `tool_pick_template` are the modes that can change the model path.

`AGENT_JEV_MODE=off` is the default. That path never constructs the shadow runner and never imports `typesafe_sdk`.

`AGENT_JEV_MODE=shadow` logs Jev's decisions next to the regex router. A timeout, exception, missing key, missing package, or bad response is a warning plus a log line. The user request does not wait.

`AGENT_JEV_MODE=tool_pick` lets Jev choose the tool on the model path. It is off unless you set it. Qwen still writes the prose. The tool_pick request is the same call the offline `v3_hybrid` ablation sends, including the policy glossary. A context change is scored on that live payload, and the report includes the label and its confidence. If Jev's tool_pick confidence is below `AGENT_JEV_TOOL_PICK_MIN_CONFIDENCE` (default 0.8), or the call times out or errors, the normal qwen tool loop runs. Every decision is a `tool_pick_decision` line in the shadow log with the confidence and `path` `jev` or `qwen`.

`AGENT_JEV_MODE=tool_pick_template` uses that same gate, slot fill, and multi-tool refusal. After the tool succeeds, a template writes the answer for count, records list, map, trend, rank, spatial context, and a single comparison that does not ask why, explain, difference, or reason. Anything else, including an overview, still goes to qwen synthesis.

`verify`, `fallback`, and `route` are reserved names. Setting them aborts startup. They are not implemented.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_JEV_MODE` | `off` | `off`, `shadow`, `tool_pick`, or `tool_pick_template` |
| `AGENT_JEV_TOOL_PICK_MIN_CONFIDENCE` | `0.8` | Use Jev's tool only at or above this confidence |
| `AGENT_JEV_BACKEND` | `typesafe` | Only `typesafe` exists |
| `AGENT_JEV_MODEL` | `jev-latest` | Model route. Logs record the concrete version the API returns |
| `AGENT_JEV_TIMEOUT_SECONDS` | `3` | Per call. SDK retries are disabled so this is the whole budget |
| `AGENT_JEV_SAMPLE_RATE` | `1.0` | Fraction of questions sent to Jev |
| `AGENT_JEV_MAX_CONCURRENCY` | `4` | Extra calls are dropped and counted |
| `AGENT_JEV_DAILY_CALL_CAP` | `5000` | Per process, UTC day |
| `AGENT_JEV_LOG_PATH` | `services/agent/logs/jev_shadow.jsonl` | Append-only JSONL |
| `AGENT_JEV_LOG_MAX_MB` | `50` | Rotate, keep 5 files |
| `TYPESAFE_API_KEY` | unset | Read by the SDK. Never commit it |

## Where the key goes

On the EC2 host, put `TYPESAFE_API_KEY` in the systemd environment file for `wildfire-agent` (the same file as the other `AGENT_*` variables), or in AWS Secrets Manager and inject it into that unit. Do not put it in the repo, `.env` that gets committed, or the shadow log. The log writer redacts the key if it ever appears in a line.

Turning it on: set `AGENT_JEV_MODE=shadow`, provide the key, restart the agent. Turning it off: set `AGENT_JEV_MODE=off` and restart. In-flight shadow calls are abandoned after about 2 seconds on shutdown.

## Logs and reports

Logs live at `AGENT_JEV_LOG_PATH` (gitignored). Each line is a `routing`, `tool_pick`, `outcome`, `dropped`, or `wiring_error` record. `routing` and `tool_pick` store the exact request payload and the unmodified raw response.

```bash
python -m services.agent.eval.jev_offline_eval --dry-run --limit 5
python -m services.agent.eval.jev_offline_eval
python -m services.agent.eval.jev_shadow_report --check-parse
python -m services.agent.eval.jev_shadow_report --since 2026-09-21 --min-confidence 0.8 --out report.md
```

`--replay N` resends stored payloads. `--export-payloads N` writes curl commands that use `$TYPESAFE_API_KEY` rather than embedding the key. `--reword` takes a JSON file of alternate criteria; `services/agent/eval/jev_reword.example.json` is the example. `--categorized` reads a review CSV whose `category` column is one of `jev_wrong`, `label_wrong`, `wiring_bug`, `wording_issue`.

## Privacy

User questions are sent to TypeSafe's API (`https://api.typesafe.ai/v1/systemone`). The warehouse data behind those questions is public CPUC, CAL FIRE, and related records, but the question text itself leaves this infrastructure. Do not enable shadow mode on a host where questions must stay on-box.

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

The production model there is `qwen2.5:7b`. `--run-tag` suffixes the artifact directory. Run this from the repo root with the services and Ollama already up:

```bash
cd /home/ubuntu/Wildfire-Services
AGENT_JEV_MODE=off    .venv/bin/python -m services.agent.eval.runner --models qwen2.5:7b --thinking off --modes constrained --run-tag jev-off
AGENT_JEV_MODE=off    .venv/bin/python -m services.agent.eval.runner --models qwen2.5:7b --thinking off --modes constrained --run-tag jev-off-2
AGENT_JEV_MODE=shadow .venv/bin/python -m services.agent.eval.runner --models qwen2.5:7b --thinking off --modes constrained --run-tag jev-shadow
.venv/bin/python -m services.agent.eval.jev_noop_diff jev-off jev-shadow --baseline-tag jev-off-2
.venv/bin/python -m services.agent.eval.jev_vs_qwen jev-shadow --log services/agent/logs/jev_shadow.jsonl
```

`jev_vs_qwen` reads qwen tools from each trajectory event, not from `response.tool_calls`. It joins Jev on the question text. The shadow log is `AGENT_JEV_LOG_PATH`. It is not a file inside the run folder. On this host that file is `services/agent/logs/jev_shadow.jsonl` in `/home/ubuntu/Wildfire-Services`. Pass `--log` when the eval checkout is a different directory.

A field that differs between off and shadow, and also between the two off runs, is `llm_variance`. Only a difference that appears in the shadow run alone is a shadow effect. `AGENT_JEV_DAILY_CALL_CAP` counts user questions. One question may send several Jev calls. `AGENT_JEV_ABLATION` selects the shadow question layout.


## Turning on tool_pick

Set `AGENT_JEV_MODE=tool_pick` and restart. Leave `AGENT_JEV_TOOL_PICK_MIN_CONFIDENCE` at `0.8` unless you want a different gate. Jev returns a tool name. This harness fills arguments from slots the router already resolved:

- `data_query_records` needs a dataset and a year or date range.
- `data_query_spatial` needs one utility plus that time span, or a coordinate pair.
- `visualization_create` needs a dataset, a year or date range, and one explicit kind: a map word, or a trend/time-series word with daily, weekly, or monthly. Both kinds at once, or neither, falls back.
- `comparison_run` needs a named metric plus either two utilities and one year, one utility and two years, or both HFTD tiers and one year.

A missing required slot falls back to the qwen routing loop. So do a confidence below the threshold, a timeout, an error, a tool outside the candidate list, a failed tool call, and any question that needs more than one primary tool. Eval can still force `multi_intent_count_and_trend` onto the model path. A normal Ask with a dataset and a year runs that rule deterministically: the records count and the time series both, then the template, with no model call. One Jev pick does not answer a two-part question. Qwen still writes the prose when the template does not apply. Shadow mode stays identical to off for anything a user or the eval suite observes, aside from timings and request ids.

## Not implemented

Later phases, not built here:

- `verify`: check the model-path tool pick before it runs
- `fallback`: use Jev when the local model fails
- `route`: reserved name. Use `tool_pick` instead.
