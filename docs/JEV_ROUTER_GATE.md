# Router-gate experiment

**Archived experiment, offline replay only.** `AGENT_JEV_MODE=router_gate`
is rejected at startup. The current experiment uses `AGENT_JEV_MODE=v4`,
documented in [JEV_V4_ROUTER.md](JEV_V4_ROUTER.md). Production `decide` remains V3.

The historical payload and pure policy are retained in
`services/agent/eval/legacy_router_gate.py`. The description below records the
former behavior; there is no live-call entry or deployment option for it.
Nothing changes the default mode or deployment configuration.

1. The rule router proposes a plan without querying data. Existing hard
   backstops and code-verified missing inputs stop first, before any Jev call.
2. One Jev request judges whether that concrete proposal correctly handles the
   question. Its input includes the query, proposed calls or clarification,
   planned view and tool descriptions. A second typed fact identifies intent;
   it supports the existing endpoint and derived-change checks.
3. A confident acceptance preserves the exact router plan, including simple
   counts, maps and rankings. There is no argument-generation LLM on that path.
4. Rejection or uncertain fit hands planning to the agent with the full supported
   tool catalog. A trustworthy comparison/trend reading cannot execute one
   scalar span count even if the separate fit reading accidentally accepts it.
5. Jev error, timeout, invalid response or daily-cap exhaustion preserves the
   original router decision. Provenance reports router fallback with the Jev
   failure reason, not a successful Jev decision. Logging remains best effort.

The existing 0.9 answer gate applies to accepting a proposal; 0.8 applies to
the intent fact and confident rejection. Unlike the previous v4 prototype,
answering does not require seven independent confidence checks. One eligible
question consumes one Jev call, not three. Hard backstops consume none.
The separate semantic slot planner does not override router_gate's selection.

## Evaluation

`router_gate_compare.py` compares the rule router, production v3 decide,
the previous unmerged v4 policy and router_gate on the same 36 development
questions. All three Jev payloads run five times, up to 1,260 API calls.
There are no generative-agent calls or data-service requests in this evaluation.
New router-fit labels were recorded before the full live capture; the set is
development data, not a clean holdout. Frozen existing gold sets are unchanged.

The report separates:

- Shared-vocabulary intent accuracy and mean confidence. V3 has no model-metrics
  or risk-surface labels, so those labels are not included in this common score.
- Proposal-fit accuracy, mistaken router acceptances and unnecessary rejections.
- Pre-execution disposition and actual router/agent handoff counts. A handoff is
  not evidence that a final answer is correct; this does not score Luna answers.
- Variation across five repeats. Repeats are not independent new questions.

Replay the original captured requests and decisions without network:

```powershell
python -m services.agent.eval.router_gate_compare --replay services/agent/eval/runs/router_gate_review_20260928/captures.jsonl.gz --output <new-directory>
```

Preview the current V4 benchmark without network:

```powershell
python -m services.agent.eval.router_gate_compare --cases services/agent/eval/v4_router_cases_v1.json
```

New live `router_gate` runs are rejected before loading provider keys. The original
captures preserve request bodies, typed answers, raw responses, timing and
payload hashes. No gold labels or historical results were changed by retirement.

The first live comparison is in
[`JEV_ROUTER_GATE_RESULTS_20260928.md`](JEV_ROUTER_GATE_RESULTS_20260928.md).
It does **not** support deploying the experiment yet. Its complete capture can
be replayed without network using `--replay <captures.jsonl.gz> --output <new-directory>`.
