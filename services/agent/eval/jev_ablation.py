"""Live schema v3 calls and the five-config ablation."""

from __future__ import annotations

import json
import statistics
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from services.agent.decisions.backend import Answer, DecisionResult
from services.agent.decisions.expected_facts import expected_facts
from services.agent.decisions.jev_policy import JevFacts, derive_outcome, facts_from_answers
from services.agent.decisions.schemas import DOMAIN_CONTEXT, routing_questions, tool_pick_questions
from services.agent.decisions.v3 import calls_for
from services.agent.eval.jev_metrics import field_applies, labels_match
from services.agent.eval.jev_offline_eval import RUNS, _today, expected_for

CONFIGS = (
    "v2_full",
    "v3_split",
    "v3_single",
    "v3_no_glossary",
    "v3_policy_context",
)

_IN_FLIGHT = threading.Semaphore(3)

_V3_MODE = {
    "v3_split": "per_call",
    "v3_single": "concatenated",
    "v3_no_glossary": "none",
    "v3_policy_context": "policy",
    "v3_hybrid": "policy",
}


def units_from_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for job in jobs:
        if job.get("diagnostic"):
            continue
        case = job["case"]
        key = (job["source"], str(case.get("id")))
        slot = grouped.setdefault(
            key,
            {"case": case, "source": job["source"], "question": job["question"], "tools": None},
        )
        if job["kind"] == "tool_pick":
            slot["tools"] = list(job.get("candidate_tools") or [])
    return list(grouped.values())


def _calls(unit: dict[str, Any], config: str) -> list[dict[str, Any]]:
    question = unit["question"]
    today = _today()
    tools = unit["tools"]
    if config == "v2_full":
        questions = dict(routing_questions())
        if tools:
            questions.update(tool_pick_questions(tools))
        return [
            {
                "name": "v2",
                "state": {"question": question, "today": today, "context": DOMAIN_CONTEXT},
                "questions": questions,
            }
        ]
    return calls_for(
        question,
        today,
        include_tools=tools,
        glossary_mode=_V3_MODE[config],
        policy_context=DOMAIN_CONTEXT if config in {"v3_policy_context", "v3_hybrid"} else None,
        include_direct_clarify=config == "v3_hybrid",
    )


def _run_calls(backend: Any, unit: dict[str, Any], config: str) -> dict[str, Any]:
    calls = _calls(unit, config)
    started = time.perf_counter()

    def one(call: dict[str, Any]) -> DecisionResult | None:
        result = None
        for attempt in range(3):
            with _IN_FLIGHT:
                result = backend.evaluate(
                    call["state"],
                    call["questions"],
                    request_id=str(unit["case"].get("id")),
                    question_hash=call["name"],
                )
            if result is None or not result.error or "Connection" not in result.error:
                return result
            time.sleep(1.5 * (attempt + 1))
        return result

    if len(calls) == 1:
        results = [one(calls[0])]
    else:
        # Calls 1-3 (and call 4, when present) do not depend on each other.
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            results = list(pool.map(one, calls))
    wall_ms = (time.perf_counter() - started) * 1000
    answers: dict[str, Answer] = {}
    tokens = 0
    errors = []
    for result in results:
        if result is None or result.error:
            errors.append(None if result is None else result.error)
            continue
        answers.update(result.answers)
        tokens += int(result.input_tokens or 0)
    return {"answers": answers, "tokens": tokens, "wall_ms": wall_ms, "errors": errors, "calls": len(calls)}


def _values(unit: dict[str, Any], config: str, answers: dict[str, Answer]) -> dict[str, Any]:
    expected = expected_for(unit["case"], unit["source"])
    if config == "v2_full":
        def choice(name: str) -> Any:
            answer = answers.get(name)
            return None if answer is None else answer.value

        return {
            "disposition": choice("disposition"),
            "intent": choice("intent"),
            "dataset": choice("dataset"),
            "tool_pick": choice("tool_pick"),
            "clarify_reason": choice("clarify_reason"),
            "unsupported_topic": choice("unsupported_topic"),
            "facts": None,
            "outcome": None,
            "expected": expected,
        }
    facts = facts_from_answers(answers)
    outcome = derive_outcome(facts, question=unit["question"])
    intent = answers.get("intent")
    dataset = answers.get("dataset")
    tool = answers.get("tool_pick")
    direct_clarify = answers.get("clarify_reason")
    # Score the direct Choice on its own. Gold gating in field_applies still
    # drops the metric unless the gold disposition is clarify.
    if config == "v3_hybrid" and direct_clarify is not None:
        clarify_reason = direct_clarify.value
    else:
        clarify_reason = outcome.clarify_reason if outcome.disposition == "clarify" else None
    return {
        "disposition": outcome.disposition,
        "intent": None if intent is None else intent.value,
        "dataset": None if dataset is None else dataset.value,
        "tool_pick": None if tool is None else tool.value,
        "tool_confidence": None if tool is None else tool.confidence,
        "clarify_reason": clarify_reason,
        "unsupported_topic": outcome.unsupported_topic,
        "facts": facts,
        "outcome": outcome,
        "expected": expected,
    }


def _summarize(
    field_votes: dict[str, list[list[Any]]],
    expected_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """field_votes[field][repeat] is aligned with expected_rows[field]."""
    summary = {}
    for field, columns in field_votes.items():
        rows = expected_rows.get(field) or []
        if not columns or not columns[0]:
            summary[field] = {"n": 0, "majority": 0.0, "flip_rate": 0.0}
            continue
        width = len(columns[0])
        majority_hits = 0
        flips = 0
        for index in range(width):
            votes = [column[index] for column in columns]
            modal = Counter(votes).most_common(1)[0][0]
            expected = rows[index].get(field) if index < len(rows) else None
            if labels_match(expected, modal):
                majority_hits += 1
            if len(set(map(_freeze, votes))) > 1:
                flips += 1
        summary[field] = {
            "n": width,
            "majority": majority_hits / width,
            "flip_rate": flips / width,
        }
    return summary


def _freeze(value: Any) -> str:
    return str(value)


def run_ablation(
    jobs: list[dict[str, Any]],
    backend: Any,
    *,
    repeats: int,
    configs: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    selected = configs or CONFIGS
    units = units_from_jobs(jobs)
    report: dict[str, Any] = {"configs": {}, "fact_errors": []}
    print(f"Ablation: {len(units)} questions x {repeats} repeats x {len(selected)} configs")
    for config in selected:
        field_columns: dict[str, list[list[Any]]] = defaultdict(list)
        para_columns: dict[str, list[list[Any]]] = defaultdict(list)
        expected_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        para_expected: dict[str, list[dict[str, Any]]] = defaultdict(list)
        tokens: list[int] = []
        walls: list[float] = []
        tool_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for repeat_index in range(repeats):
            actuals: dict[str, list[Any]] = defaultdict(list)
            para_actuals: dict[str, list[Any]] = defaultdict(list)
            for unit in units:
                shot = _run_calls(backend, unit, config)
                judged = _values(unit, config, shot["answers"])
                tokens.append(shot["tokens"])
                walls.append(shot["wall_ms"])
                if unit["tools"] and judged.get("tool_pick") is not None:
                    tool_trace[str(unit["case"].get("id"))].append(
                        {
                            "value": judged.get("tool_pick"),
                            "confidence": judged.get("tool_confidence"),
                        }
                    )
                if repeat_index == 0 and config != "v2_full" and judged["outcome"] is not None:
                    _note_fact_error(unit, judged, report)
                for field in (
                    "disposition",
                    "intent",
                    "dataset",
                    "tool_pick",
                    "clarify_reason",
                ):
                    if field == "tool_pick" and not unit["tools"]:
                        continue
                    if not field_applies(field, judged["expected"]):
                        continue
                    actuals[field].append(judged[field])
                    if repeat_index == 0:
                        expected_rows[field].append(judged["expected"])
                    if unit["source"] == "paraphrases":
                        para_actuals[field].append(judged[field])
                        if repeat_index == 0:
                            para_expected[field].append(judged["expected"])
            for field, values in actuals.items():
                field_columns[field].append(values)
            for field, values in para_actuals.items():
                para_columns[field].append(values)
            print(f"  {config} repeat {repeat_index + 1}/{repeats}")
        walls_sorted = sorted(walls)
        def pct(q: float) -> float:
            if not walls_sorted:
                return 0.0
            index = min(len(walls_sorted) - 1, int(round(q * (len(walls_sorted) - 1))))
            return walls_sorted[index]

        report["configs"][config] = {
            "all": _summarize(field_columns, expected_rows),
            "paraphrases": _summarize(para_columns, para_expected),
            "mean_input_tokens": statistics.fmean(tokens) if tokens else 0,
            "p50_ms": pct(0.50),
            "p95_ms": pct(0.95),
            "calls": sum(1 for _ in tokens),
        }
        flips = []
        for case_id, votes in tool_trace.items():
            values = [item["value"] for item in votes]
            if len(set(values)) <= 1:
                continue
            flips.append({"id": case_id, "votes": votes})
            original = votes[0]
            print(
                f"  tool_pick flip {case_id}: "
                + ", ".join(f"{item['value']}@{item['confidence']}" for item in votes)
                + f" original_confidence={original['confidence']}"
            )
        report["configs"][config]["tool_flips"] = flips
        _print_config(config, report["configs"][config])
        partial = RUNS / "jev_ablation_partial.json"
        partial.write_text(json.dumps(report, default=str), encoding="utf-8")
        print(f"Checkpoint {partial}")
    winner = pick_winner(report["configs"])
    report["winner"] = winner
    report["conclusion"] = conclude(report["configs"], winner)
    print()
    print(report["conclusion"])
    print(f"Winning config: {winner}")
    return report


def _note_fact_error(unit: dict[str, Any], judged: dict[str, Any], report: dict[str, Any]) -> None:
    expected = judged["expected"]
    if labels_match(expected.get("disposition"), judged["disposition"]):
        return
    labeled = expected_facts(unit["question"])
    facts: JevFacts = judged["facts"]
    wrong = []
    for name, spec in labeled.items():
        if spec.get("needs_human_review"):
            continue
        observed = getattr(facts, name, None)
        if not isinstance(observed, float):
            continue
        if (observed >= facts.threshold) != bool(spec["value"]):
            wrong.append(name)
    if len(report["fact_errors"]) < 40:
        report["fact_errors"].append(
            {
                "id": unit["case"].get("id"),
                "expected": expected.get("disposition"),
                "derived": judged["disposition"],
                "trace": judged["outcome"].trace,
                "wrong_facts": wrong,
            }
        )
        if len(report["fact_errors"]) <= 12:
            print(
                f"    disposition {unit['case'].get('id')}: expected {expected.get('disposition')} "
                f"derived {judged['disposition']} via {judged['outcome'].trace} "
                f"wrong facts {wrong or 'none labeled'}"
            )


def _print_config(config: str, body: dict[str, Any]) -> None:
    print(
        f"{config}: mean input tokens {body['mean_input_tokens']:.0f} "
        f"wall p50 {body['p50_ms']:.0f}ms p95 {body['p95_ms']:.0f}ms"
    )
    for scope in ("all", "paraphrases"):
        for field, stats in body[scope].items():
            print(
                f"  {scope} {field}: majority {stats['majority']:.1%} "
                f"flip {stats['flip_rate']:.1%} n={stats['n']}"
            )


def pick_winner(configs: dict[str, Any]) -> str:
    def key(name: str) -> tuple:
        stats = configs[name]["all"].get("disposition", {"majority": 0, "flip_rate": 1})
        return (stats["majority"], -stats["flip_rate"], -configs[name]["mean_input_tokens"])

    return max(configs, key=key)


def conclude(configs: dict[str, Any], winner: str) -> str:
    def disp(name: str) -> str:
        stats = configs.get(name, {}).get("all", {}).get("disposition")
        if not stats:
            return f"{name} was not in this run"
        return f"{name} disposition majority is {stats['majority']:.1%} with flip {stats['flip_rate']:.1%}"

    return (
        f"{winner} wins on majority-vote disposition accuracy, then lower flip rate, then fewer input tokens. "
        f"{disp('v2_full')}. {disp('v3_split')}. {disp('v3_single')}. "
        f"{disp('v3_no_glossary')}. {disp('v3_policy_context')}. "
        f"Use {winner} as the shadow default."
    )


def score_fact_nouls(answers: dict[str, Answer], question: str) -> list[tuple[str, bool | None]]:
    labeled = expected_facts(question)
    rows = []
    for name, spec in labeled.items():
        if spec.get("needs_human_review"):
            rows.append((name, None))
            continue
        answer = answers.get(name)
        if answer is None or not isinstance(answer.value, (int, float)):
            rows.append((name, None))
            continue
        rows.append((name, (float(answer.value) >= 0.5) == bool(spec["value"])))
    return rows
