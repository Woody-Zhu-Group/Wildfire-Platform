# Jev review follow-up, 2026-10-01

This branch addresses Michael's review items #5, #8 and #9. PR #113 remains a
draft. Production V3 configuration and Jev request wording are unchanged. No
paid model API calls, deployment or merge were performed.

## Request lifecycle (#5)

The previous async timeout returned to the caller while its synchronous worker
could finish plan compilation and send the fourth Jev request. The caller and
worker now share a stop signal/deadline. Queue dispatch checks permission before
starting each backend call. Timeout, SSE disconnect and task cancellation stop
further calls; explicit cancellation does not run Router fallback or the Agent.
Already-started SDK calls may finish at their own timeout, but their results do
not resume this request's planning. No provider credit refund is claimed.

Batch reservations release only unsent slots. Started calls count even when the
backend fails. Closing a reservation twice does not refund twice, and closing a
previous-day reservation cannot subtract from the new day's counter. Concurrent
reservations remain bounded by the cap. V3 shares this lifecycle fix while its
semantic policy and confidence gates stay unchanged.

Regression tests reproduce the blocked compiler with exactly three initial
calls. After each outer timeout, disconnect and task cancellation, releasing
the worker leaves the backend/local counter at three, with no fourth call.
Queued timeouts release all unsent slots; started failures retain their count.

## Static current-location lookup (#7)

The query “Identify the utility territory containing my current location.”
cannot produce a factual answer without a location. Neither executor has device
location or prior-chat context. Static boundary lookup is nevertheless supported
by `data_query_spatial(kind=point, lat=..., lon=...)`. A query that explicitly
includes coordinates compiles to that fixed plan; no live-data service is needed.
Without cross-query context, a follow-up containing only coordinates is not
automatically a continuation. This change does not implement chat memory.

The override reconciles a static point intent with the conflicting live-topic
reading by asking for the missing location. It executes no tools and makes no
Agent handoff. Disabling only that override in memory gives the following replay
of the unchanged 88-question, five-repeat development benchmark:

| Metric | Override on | Override off |
|---|---:|---:|
| Strict executor/plan matches | 424/440 | 421/440 |
| Correct fixed-plan retention | 249/260 | 249/260 |
| Agent handoffs | 54 | 54 |
| Incorrect fixed plans accepted | 0 | 0 |

Only repetitions 1, 3 and 4 of `cross_05b` change, from missing-location
clarification to `unsupported_live_web`. In the other two repetitions Jev already
leads to a clarification. This is a development-set diagnostic, not independent
acceptance. The override, prompts and frozen labels were not edited. The
independent holdout remains deferred.

## Storage and logging (#8/#9)

706 tracked run files (19,749,144 bytes) moved to external runtime storage.
Original captures, provider responses, trajectories and billing receipts are
preserved byte-for-byte in a ZIP with per-file SHA-256 manifest, and in the
external replay directory. Compact numeric reports stay under
`services/agent/eval/reports/`. The old run path is now ignored and has no tracked
files. Eight typed readings for synthetic regression questions stay as a small
test fixture; they contain no prompts, provider bodies or runtime trajectories.
The [eval guide](../services/agent/eval/README.md) records the archive checksum,
source revision and recovery commands. Shared Git history was not rewritten.

The development host archive is
`C:/Users/steph/AppData/Local/Wildfire-Platform/archives/eval-runs-52f87c4.zip`;
replay files are under that runtime directory's `eval/runs/`. Existing local Jev
logs were moved without changing bytes to `archives/legacy-jev-logs-20261001/`
(SHA-256 `70ee553d34bd49ee22b3c8a43c136ed88b501eb7423ca8d89a9fadc429cb5bba`).
They are retained for the requested record inspection, separately from active
production log expiry.

Logs default outside the checkout with hashes, request IDs, route/decision,
confidence, timing and error presence. They omit query text, slots, prompt bodies,
tool arguments and arbitrary error text, including on stdout. Raw file capture
requires `AGENT_JEV_LOG_RAW=true`, still redacts both keys, and follows size/age
limits: 50 MB segments, five backups, seven-day segment expiry on activity.
Oldest-record timestamps prevent recent appends from extending retention.
Independent idle cleanup and journald policy require host configuration, described
in [the log guide](JEV_SHADOW.md); this PR does not modify the running host.
There is no persisted cross-query chat thread. Request context and TTL artifacts
remain in memory. Evaluation archives are retained separately for reproducibility.

## Verification

- Full Agent suite: **1,982 passed, 4 skipped, the same 18 existing CAL FIRE
  fixture/qualification failures** in `test_measured_coverage.py`. Their IDs match
  the previous report. The warehouse test skips with the unavailable DB.
- Clean-checkout contract checks with the external archive disabled: **544 passed,
  2 explicit historical-archive skips**. Smoke and clarification regressions use
  the compact fixture and still run.
- The 880 captured V3/V4 records pass complete case/mode/repeat and payload-hash
  verification and reproduce every saved report field after JSON normalization.
- All **398** original Router outputs are unchanged: cases 107, paraphrases 41,
  holdout v1 97, v2 65, v3 88. This includes arguments and slots, not just routes.
- Ruff finds no new diagnostics across 34 touched/new Python files; 13 existing
  findings in the older executor, evaluator and tests remain. Diff checks pass.
- Archive and replay-directory files were checked against all 706 original
  hashes. No API keys or task temporary files enter the commit.
