# Evaluation inputs, reports and runtime records

Case files and frozen labels are repository inputs. Files such as
`jev_holdout_v3_raw.json` contain synthetic question candidates, not API traces.
Do not relabel them during storage cleanup. Small synthetic fixtures under
`tests/agent/fixtures/` exercise parsing and replay contracts.
`decide_readings.json` preserves eight typed readings for synthetic regression
questions, including the original probabilities and source checksum. It omits
request payloads, provider bodies, trajectories and billing data, so smoke and
clarification regressions run in a clean checkout without the large archive.

Generated captures, provider responses, per-query trajectories and billing
receipts belong outside Git. Evaluators default to `eval/runs` under the Agent
runtime directory: `%LOCALAPPDATA%/Wildfire-Platform` on Windows or
`${XDG_STATE_HOME:-~/.local/state}/Wildfire-Platform` on Unix.
`WILDFIRE_EVAL_RUNS_DIR` selects another runtime directory. Evaluators with an
explicit `--output` use that path; choose a directory outside the checkout.
The old `services/agent/eval/runs/` path is ignored as a second safeguard.

`reports/` retains compact numeric reports. The 2026-10-01 migration removed
706 tracked run files (19,749,144 bytes) without changing their archived bytes.
The archive receipt is `reports/archive-20261001.json`. The archive
`eval-runs-52f87c4.zip` has SHA-256
`0e17d384cfd401579b8314cfb3481faaf78c647389375ed9a33c15c2ecc929b0`.
It contains a per-file checksum manifest and is retained in the runtime
directory's `archives/` folder on the development host. Extract the run files
to an external directory and set `WILDFIRE_EVAL_RUNS_DIR` to it for replay.
No credentials are needed for replay.

The original bytes also remain recoverable from Git revision
`52f87c410ac432e53aa7497d906ccb76a6051ec0`. This change removes traces from
the current tree; it does not rewrite shared Git history. For another machine:

```bash
git archive --format=tar --output=/tmp/wildfire-runs.tar 52f87c4 services/agent/eval/runs
mkdir -p /tmp/wildfire-replay
tar -xf /tmp/wildfire-runs.tar -C /tmp/wildfire-replay
export WILDFIRE_EVAL_RUNS_DIR=/tmp/wildfire-replay/services/agent/eval/runs
pytest tests/agent/test_legacy_router_gate.py tests/agent/test_v4_argmax.py
```

Use one disposable task directory and clean it after verification. The two
tests that assert historical 540/180-record scores require this archive; they
skip explicitly when absent. Synthetic policy and payload-integrity tests do
not depend on it. Full archival replay is separate from clean-checkout tests.

Raw evaluation records are deliberately retained for the requested query and
output inspection and reproducibility. They are not production chat history
and do not expire with the production log policy. Remove an old evaluation
directory explicitly when its records are no longer needed.
