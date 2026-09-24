# WildfireIntel agent service

@AGENTS.md

AGENTS.md (imported above) holds learned preferences and workspace facts shared with other coding tools. Where it conflicts with this file, this file wins. In particular: the team repo is Woody-Zhu-Group/Wildfire-Platform (push to `platform`), the eval set is no longer 27 cases, and production runs qwen2.5:7b.

Research platform for California wildfire and utility data (CPUC, CAL FIRE, PG&E EPSS, PSPS, US ignitions sample, HFTD, IOU territories, circuits, cNHPP risk model). Users are CPUC analysts. A wrong answer is worse than a slow answer or a clarifying question.

## Hard rules

- Never use em dashes in code comments, docs, commit messages, or reports.
- Push only to the `platform` remote (Woody-Zhu-Group/Wildfire-Platform). `origin` is Michael's old fork (ByteMasterMike/Wildfire-Services); nothing should live only there.
- Never push to or change `main` directly. Work on branches, open PRs, do not merge.
- Never print, log, or commit `TYPESAFE_API_KEY`, `.env`, or any credential.
- Never change a gold label in any eval file unless Michael gives a written label rule.
- Evals default to one pass. Use `--repeats 5` only when a change touches Jev question wording or adds new Jev facts.
- Do not run `services.agent.eval.runner` (it calls the qwen model host) unless explicitly asked. Offline Jev evals are fine.
- Stop and report if TypeSafe API spend in a session passes $5, unless told a different cap.
- Every change to routing reports how many existing routes changed across `cases.json`, `jev_paraphrases.json`, and all holdouts.
- Any claim about accuracy states which eval set it came from and whether that set is clean or already used for tuning.
- Every PR that changes behavior, setup, architecture, endpoints, or env vars must update the affected docs (READMEs, `docs/*.md`, service READMEs, `.env.example`, CLAUDE.md, AGENTS.md) in the same PR, verified against the code on that branch. The PR description must list the docs touched, or say that none were affected. Never describe unmerged work as done.
- Any PR that changes website source (anything under `website/` that the build reads: `src/`, `index.html`, `package*.json`, `vite.config.ts`, `.env.production`) must run `npm run build` in `website/` and commit the rebuilt `docs/index.html` and `docs/assets/workspace/` in the same PR. The website test `tests/build-freshness.test.ts` (also `npm run check-build`) fails when `docs/` is not the build of the current source.

## Architecture (target design: Jev first)

On main today: steps 1, 3, and 5, and in step 4 the router's deterministic calls, Jev tool pick, and template answers. Jev deciding answer, clarify, or refuse (step 2) is `AGENT_JEV_MODE=decide` (off by default, `docs/JEV_DECIDE.md`); Jev owns the disposition and the router owns the wording; the slot planner is `AGENT_SLOT_PLAN` (off by default, `docs/JEV_MULTI_TOOL.md`). With both on, decide runs first and the slot planner acts only on questions decide leaves as answer.

1. Router hard backstops fire first (`services/agent/routing.py`): live and current, future dates, city_needs_place, hftd_constraint_unavailable, explicit unsupported topics.
2. Jev decides answer, clarify, or refuse (`services/agent/decisions/`, policy in `jev_policy.py`, schema in `v3.py`).
3. Router regex extracts slots (years, utilities, counties, dataset, dates).
4. Tools run: the router's deterministic call when it has one; otherwise Jev tool pick, template answers, or the slot planner for multi-part questions.
5. Caveats attach per tool (`caveats.py`). Every rendered number must trace to tool evidence (`evidence_ids`).

Jev (TypeSafe) is non-generative: it returns typed Choice, Score, and Noul answers with probabilities. Lessons that hold:
- Ask Jev small, unambiguous facts with mutually exclusive options; let code apply policy. Overlapping yes/no facts land in the 0.2 to 0.8 band.
- Jev confidence is relative to the options offered. If the right option is missing, it can be confidently wrong.
- For a Noul, confidence is max(p, 1 - p), never raw p.
- Live and offline payloads must hash the same (`test_live_tool_pick_payload_matches_the_offline_hybrid_call`).
- Report label accuracy AND mean confidence for any context change; the gate runs on confidence.

## Env flags (all default off)

- `AGENT_JEV_MODE`: off, shadow, tool_pick, tool_pick_template, decide; plan mode was archived on the `jev-plan-archive` branch and is not accepted (`docs/JEV_MULTI_TOOL.md`).
- `AGENT_JEV_DECIDE_MIN_CONFIDENCE` (0.8) and `AGENT_JEV_DECIDE_ANSWER_CONFIDENCE` (0.9): decide mode's decline and answer gates. The answer gate is a stated default, not chosen from any eval set; v3 was not used.
- `AGENT_JEV_TOOL_PICK_MIN_CONFIDENCE`: default 0.8
- `AGENT_JEV_BACKEND`: typesafe (default) or openrouter
- `AGENT_JEV_LOG_PATH`: shadow log location
- `AGENT_LLM_PROVIDER`: ollama (default) or openrouter; openrouter also needs `AGENT_ALLOW_REMOTE_PROVIDER=true` and `OPENROUTER_API_KEY` (`docs/OPENROUTER.md`)
- `AGENT_SLOT_PLAN`: deterministic multi-entity planner, default off (`docs/JEV_MULTI_TOOL.md`); with decide on, decide runs first

## Key paths

- Agent: `services/agent/` (routing.py, orchestrator.py, views.py, caveats.py, schemas.py)
- Jev: `services/agent/decisions/`
- Evals: `services/agent/eval/` (cases.json, jev_paraphrases.json, jev_holdout.json, jev_holdout_v2.json, jev_holdout_v3_questions.json, jev_holdout_v3_labels_chatgpt.json, runs/).
- Docs: `docs/JEV_SHADOW.md`, `docs/JEV_DECIDE.md`, `docs/JEV_MULTI_TOOL.md`, `docs/JEV_DETERMINISM.md`, `docs/JEV_BACKLOG.md`, `docs/OPENROUTER.md`. The root README has a documentation index.
- Website: `website/src/` (panelViews.ts, answerPanels.ts, agentContracts.ts, state.tsx)

## Tests

- `pytest tests/agent`
- Website tests in `website/tests/` (`npm test`), including the check that `docs/` matches a fresh build

## Branches and merge order

Merged into main: `router-paraphrase-fixes` (#22), `ops-shadow-tooling` (#24), `openai-provider` (#25), `panel-summary-stats` (#26), `risk-health-check` (#27), `geocode-cities` (#28), `jev-shadow` (#43), `hftd-geometry-rebuild` (#45), `jev-multi-tool` (#46, slot planner; plan mode archived on `jev-plan-archive`), `time-resolve-fixes` (#48), `jev-decider` (#49), `model-date-range` (#50), `clarify-all-missing` (#51), `readme-refresh` (#57), `router-followups` (#58), `gitignore-hook-files` (#59), `month-range-fix` (#66), `geometry-followups` (#69).

Open:
- `research-psps-reports` (PR #29), `us-sample-routing` (PR #68), `risk-and-cleanup-fixes` (PR #70).

After each merge, rebase the next branch onto `platform/main`, rerun `pytest tests/agent`, and report route changes across all eval sets.

## Eval sets and their status

- Dev (cases.json 107 + paraphrases 41 = 148; the original 105 predate the newer cases): used for tuning.
- Holdout v1 (63 of 97 rows without `needs_human_review`) and v2 (40): seen, now development data.
- Holdout v3 (88, 65 certain after independent ChatGPT labels): partly tuned. Router fixes were written from its disagreements.
- Production shadow logs will be the next clean test.

## Deployment (EC2, reached by Michael through SSM, not SSH)

- Backend host `ip-172-31-2-9`, repo `/home/ubuntu/Wildfire-Services` (origin there is the platform repo), service `wildfire-agent` on port 8004. Production is still at PR #15.
- Model host 172.31.6.133, Ollama qwen2.5:7b at 4096 context, CPU only. Slow: hard questions take 3 to 12 minutes.
- Eval worktree `/home/ubuntu/jev-eval`.
- Open items: lock port 8004 to CloudFront, rotate the TypeSafe key, switch prose answers to OpenRouter (GPT-6 Luna, Sol for hard fallbacks) once `docs/OPENROUTER.md` clears the switch (the code is on main, off by default).
