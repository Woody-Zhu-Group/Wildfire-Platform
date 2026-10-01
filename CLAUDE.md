# WildfireIntel agent service

@AGENTS.md

AGENTS.md (imported above) holds learned preferences and workspace facts shared with other coding tools. Where it conflicts with this file, this file wins. In particular: the team repo is Woody-Zhu-Group/Wildfire-Platform (push to `platform`), the eval set is no longer 27 cases, and production runs GPT-6 Luna on OpenRouter (the local qwen path was removed on 2026-09-24).

Research platform for California wildfire and utility data (CPUC, CAL FIRE, PG&E EPSS, PSPS, US ignitions sample, HFTD, IOU territories, circuits, cNHPP risk model). Users are CPUC analysts. A wrong answer is worse than a slow answer or a clarifying question.

## Hard rules

- Never use em dashes in code comments, docs, commit messages, or reports.
- Push only to the `platform` remote (Woody-Zhu-Group/Wildfire-Platform). `origin` is Michael's old fork (ByteMasterMike/Wildfire-Services); nothing should live only there.
- Never push to or change `main` directly. Work on branches, open PRs, do not merge.
- Never print, log, or commit `TYPESAFE_API_KEY`, `.env`, or any credential.
- Never change a gold label in any eval file unless Michael gives a written label rule.
- Evals default to one pass. Use `--repeats 5` only when a change touches Jev question wording or adds new Jev facts.
- Do not run `services.agent.eval.runner` (it spends OpenRouter credits on every model-path case) unless explicitly asked with a budget. Offline Jev evals are fine.
- Stop and report if TypeSafe API spend in a session passes $5, unless told a different cap.
- Every change to routing reports how many existing routes changed across `cases.json`, `jev_paraphrases.json`, and all holdouts.
- Any claim about accuracy states which eval set it came from and whether that set is clean or already used for tuning.
- Fix the class of failure, not the reported question. A production question that exposes a bug is a test case, not the specification: the fix must be stated as a general rule or invariant, must not match on that question's specific words, and must be tested with the reported question plus at least three paraphrases written without looking at the fix. Prefer invariants enforced in the harness or registry over new word patterns in the router.
- Meaning-level judgments (whether a question asks for a change, names a real measure, or is really about an unsupported topic) belong to structure or to Jev's facts, never to new regular expressions or word lists in the router. Word lists are acceptable only for closed vocabularies owned by the naming registry, such as utility, county, and dataset names.
- Every PR that changes behavior, setup, architecture, endpoints, or env vars must update the affected docs (READMEs, `docs/*.md`, service READMEs, `.env.example`, CLAUDE.md, AGENTS.md) in the same PR, verified against the code on that branch. The PR description must list the docs touched, or say that none were affected. Never describe unmerged work as done.
- Any PR that changes website source (anything under `website/` that the build reads: `src/`, `index.html`, `package*.json`, `vite.config.ts`, `.env.production`) must run `npm run build` in `website/` and commit the rebuilt `docs/index.html` and `docs/assets/workspace/` in the same PR. The website test `tests/build-freshness.test.ts` (also `npm run check-build`) fails when `docs/` is not the build of the current source.

## Architecture and runtime modes

Production `decide` remains V3. The separate `v4` mode is experimental work in
draft PR #113, not a production mode switch. Its current flow is:

1. Three parallel Jev calls identify intent, dataset, measure and scope facts.
2. Code binds explicit slots to fixed tool plans and validates arguments,
   measured coverage, dataset definitions and named entities.
3. When a candidate exists, one additional Jev call chooses router or agent
   by plan completeness. Choice argmax and binary facts at 0.5 are used without
   confidence rejection gates. Accepted plans execute through the fixed harness.
4. Missing capabilities or an incomplete ordinary plan lead to agent planning;
   missing inputs and unavailable harness-only outputs can clarify or refuse.
5. Timeout, backend failure, missing/invalid answers or daily cap restores the
   pre-Jev keyword Router decision: fixed calls execute, a model route goes to
   the Agent, and clarification/refusal returns directly. Partial Jev readings
   are discarded. V4 does not yet run V3 backstops before Jev.

Pre-Jev backstop and injection policy and independent acceptance remain review
items. Shared decide/V4 request stop signals prevent queued or fourth calls after
timeout/disconnect; unsent reservations are released, sent calls still count.
Runtime logs default to external OS storage, metadata only, with seven-day
segment expiry and size rotation. Raw logging requires explicit opt-in. V4 skips
the separate slot planner.
Fault fallback now explicitly uses the legacy Router and inherits its semantic
limitations. It does not imply independent validation of every fallback plan.
The Agent has within-request tool/retry context, but receives no prior chat turns.
See `docs/JEV_V4_ROUTER.md`, `docs/JEV_V4_PROMPTS.md`, and
`docs/JEV_V4_ROUTER_RESULTS_20260929.md`; reported results are development-set
results, not independent production acceptance.

`router_gate` is retired and rejected by runtime configuration. Its frozen
payload and pure policy live in `services/agent/eval/legacy_router_gate.py` for
offline replay only. The evaluator accepts that mode only with `--replay`.

### V3 (`decide`) flow

On main today: steps 1, 3, and 5, and in step 4 the router's deterministic calls, Jev tool pick, and template answers. Jev deciding answer, clarify, or refuse (step 2) is `AGENT_JEV_MODE=decide` (off by default, `docs/JEV_DECIDE.md`); Jev owns the disposition and the router owns the wording, except that the generic `ranking_missing_slots` question yields to Jev's more specific clarification; the slot planner is `AGENT_SLOT_PLAN` (off by default, `docs/JEV_MULTI_TOOL.md`). With both on, decide runs first and the slot planner acts only on questions decide leaves as answer.

1. Router hard backstops fire first (`services/agent/routing.py`): live and current, future dates, city_needs_place, hftd_constraint_unavailable. Unsupported-topic keywords (cost, leadership, optimization, damage, and the rest of `routing.TOPIC_JUDGMENT_RULES`) are refusals in off mode; in decide mode Jev's off_topic refuses them at the decline gate (0.8), lifts them only at the answer gate (0.9), and the keyword rule is the fallback (issue #97, `docs/JEV_DECIDE.md`). The advice rule stays with the router.
2. Jev decides answer, clarify, or refuse (`services/agent/decisions/`, policy in `jev_policy.py`, schema in `v3.py`).
3. Router regex extracts slots (years, utilities, counties, dataset, dates).
4. Tools run: the router's deterministic call when it has one; otherwise Jev tool pick, template answers, or the slot planner for multi-part questions. The tool executor refuses any read for a utility or period outside its dataset's measured coverage (the loaders write `shared/dataset_coverage.json`, measured on exactly the rows each count reads: once per registry query definition, such as CAL FIRE's `incident_type_mode`; coverage is never declared by hand) with a `not_covered` result, never a zero, and the answer becomes a clarification on every path. It offers other data only where the coverage file's per-year counts show rows for that utility in that period, and says when an offer drops a filter.
5. Caveats attach per tool (`caveats.py`). Every rendered number must trace to tool evidence (`evidence_ids`). Changes, differences, percent changes, and ratios come from harness-derived evidence (`derived.py`), never from model arithmetic.

Jev (TypeSafe) is non-generative: it returns typed Choice, Score, and Noul answers with probabilities. Lessons that hold:
- Ask Jev small, unambiguous facts with mutually exclusive options; let code apply policy. Overlapping yes/no facts land in the 0.2 to 0.8 band.
- Jev confidence is relative to the options offered. If the right option is missing, it can be confidently wrong.
- For a Noul, confidence is max(p, 1 - p), never raw p.
- Live and offline payloads must hash the same (`test_live_tool_pick_payload_matches_the_offline_hybrid_call`).
- Report label accuracy AND mean confidence for any context change. V3 gates on confidence; V4 records it but does not gate ordinary routing on it.

## Env flags (all default off)

- `AGENT_JEV_MODE`: off, shadow, tool_pick, tool_pick_template, decide, v4; router_gate is offline replay only and is rejected at startup. Plan mode was archived on the `jev-plan-archive` branch and is not accepted (`docs/JEV_MULTI_TOOL.md`).
- `AGENT_JEV_DECIDE_MIN_CONFIDENCE` (0.8) and `AGENT_JEV_DECIDE_ANSWER_CONFIDENCE` (0.9): decide mode's decline and answer gates. The answer gate is a stated default, not chosen from any eval set; v3 was not used.
- `AGENT_JEV_TOOL_PICK_MIN_CONFIDENCE`: default 0.8
- `AGENT_JEV_BACKEND`: typesafe (default) or openrouter
- `AGENT_JEV_DAILY_CALL_CAP`: default 5000 API calls per process per UTC day, not questions. V3 reserves three calls; V4 reserves three, then one if plan-fit is needed. V4 falls back to the original keyword Router if either reservation fails. Actual production capacity depends on configured cap and worker count.
- `AGENT_JEV_LOG_PATH`: optional absolute log path; empty defaults to the OS runtime directory, outside Git. `AGENT_JEV_LOG_RAW=false` keeps metadata only; `AGENT_JEV_LOG_RETENTION_DAYS=7` expires log segments on activity. Idle host cleanup is documented in `docs/JEV_SHADOW.md`.
- `AGENT_LLM_PROVIDER`: openrouter is the only value; `OPENROUTER_API_KEY` is required and `AGENT_ALLOW_REMOTE_PROVIDER=true` must be set or startup fails loudly (`docs/OPENROUTER.md`). `AGENT_LLM_MODEL` (openai/gpt-6-luna) and `AGENT_LLM_FALLBACK_MODEL` (openai/gpt-6-sol) override the models
- `AGENT_SLOT_PLAN`: deterministic multi-entity planner, default off (`docs/JEV_MULTI_TOOL.md`); with decide on, decide runs first

## Key paths

- Agent: `services/agent/` (routing.py, orchestrator.py, views.py, caveats.py, derived.py, schemas.py)
- Jev: `services/agent/decisions/`
- Naming conventions (utilities, counties, tiers, causes, incident types, question wording): `services/shared/naming.py`, re-exported by `services/shared/dataset_registry.py`. Import from the registry; `tests/test_naming_single_source.py` fails on a copied list. See `services/shared/README.md`.
- Evals: `services/agent/eval/` (cases and frozen labels, compact `reports/`). Generated run data is external (`WILDFIRE_EVAL_RUNS_DIR`); see `services/agent/eval/README.md` for archive checksums and replay.
- Docs: `docs/JEV_SHADOW.md`, `docs/JEV_DECIDE.md`, `docs/JEV_MULTI_TOOL.md`, `docs/JEV_DETERMINISM.md`, `docs/JEV_BACKLOG.md`, `docs/OPENROUTER.md`. The root README has a documentation index.
- Website: `website/src/` (panelViews.ts, answerPanels.ts, agentContracts.ts, state.tsx)

## Tests

- `pytest tests/agent`
- Website tests in `website/tests/` (`npm test`), including the check that `docs/` matches a fresh build

## Branches and merge order

For which PRs are merged or open, check GitHub (gh pr list --state all), not this file.

After each merge, rebase the next branch onto `platform/main`, rerun `pytest tests/agent`, and report route changes across all eval sets.

## Eval sets and their status

- Dev (cases.json 107 + paraphrases 41 = 148; the original 105 predate the newer cases): used for tuning.
- Holdout v1 (63 of 97 rows without `needs_human_review`) and v2 (40): seen, now development data.
- Holdout v3 (88, 65 certain after independent ChatGPT labels): partly tuned. Router fixes were written from its disagreements.
- Smoke (6): the `scripts/smoke_test.sh` questions, replayed offline in the decide replay and `tests/agent/test_jev_decide_scope.py`; all six have stored Jev calls (five cross-backend via OpenRouter).
- Production shadow logs will be the next clean test.
- The 80-question V3/V4 comparison and 88-question router-first V4 benchmark have been used for development and prompt changes. The 96.36% strict score and 95.77% router retention are dev results. Independent executor/disposition labels must be frozen before a new holdout run.

## Deployment (EC2, reached by Michael through SSM, not SSH)

- Backend host `ip-172-31-2-9`, repo `/home/ubuntu/Wildfire-Services` (origin there is the platform repo), service `wildfire-agent` on port 8004. Production runs `main` at `6b691a8` (the PR #92 merge), deployed 2026-09-24.
- Model tier is OpenRouter (GPT-6 Luna, Sol on retries) since 2026-09-24. The CPU model instance 172.31.6.133 and the old GPU instance are retired.
- Eval worktree `/home/ubuntu/jev-eval`.
- Open items: lock port 8004 to CloudFront, revoke the old TypeSafe key.
